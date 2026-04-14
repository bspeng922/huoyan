from __future__ import annotations

import json
from typing import Any

from jsonschema import ValidationError, validate

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProbeSettings
from huoyan.models import ProbeResult, ProbeStatus
from huoyan.utils import compact_text, extract_json_block, extract_tool_calls, utc_now


def _result(
    *,
    probe: str,
    status: ProbeStatus,
    summary: str,
    metrics: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    started_at=None,
) -> ProbeResult:
    return ProbeResult(
        suite="agentic",
        probe=probe,
        status=status,
        summary=summary,
        metrics=metrics or {},
        evidence=evidence or {},
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
    )


def _build_long_context(target_chars: int) -> tuple[str, dict[str, str]]:
    head = "amber-17"
    middle = "lotus-29"
    tail = "onyx-41"
    filler_paragraphs = [
        "这是一段用于长上下文完整性检测的填充文本，重点不是语义本身，而是确认网关没有在中间层偷偷截断提示词。",
        "如果上下文被吞掉，模型通常只能记住结尾附近的信息，而无法稳定回忆头部与中部的细节。",
        "真实业务里，长上下文往往由代码、研究笔记、日志、表格说明和结论草稿混合组成，而不是单一重复文本。",
        "因此这里故意使用多段不同内容的填充文字，尽量模拟更接近实际项目资料的上下文结构。",
        "如果中转层做了压缩、裁剪、替换或缓存复用，头部、中部、尾部的 canary 命中情况往往会出现明显分化。",
    ]
    chunk_parts: list[str] = []
    for index in range(40):
        chunk_parts.append(filler_paragraphs[index % len(filler_paragraphs)] + "\n")
    chunk = "".join(chunk_parts)
    segments = [f"HEAD_CANARY={head}\n"]
    while sum(len(item) for item in segments) < target_chars // 2:
        segments.append(chunk)
    segments.append(f"MIDDLE_CANARY={middle}\n")
    while sum(len(item) for item in segments) < target_chars - len(chunk):
        segments.append(chunk)
    segments.append(f"TAIL_CANARY={tail}\n")
    document = "".join(segments)
    return document, {"head": head, "middle": middle, "tail": tail}


async def _tool_calling_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = utc_now()
    if not model.supports_tools:
        return _result(
            probe="tool_calling",
            status=ProbeStatus.SKIP,
            summary="Model is marked as not supporting tool calls.",
            started_at=started,
        )

    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "const": "Hangzhou"},
            "weight_kg": {"type": "number", "const": 2.5},
            "fragile": {"type": "boolean", "const": True},
        },
        "required": ["city", "weight_kg", "fragile"],
        "additionalProperties": False,
    }
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "请调用 `route_package` 工具，参数必须表示："
                        "city=Hangzhou, weight_kg=2.5, fragile=true。不要直接回答自然语言。"
                    ),
                }
            ],
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 120),
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "route_package",
                        "description": "Route a package to the correct logistics service.",
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "route_package"}},
        )
    except Exception as exc:
        return _result(
            probe="tool_calling",
            status=ProbeStatus.ERROR,
            summary=f"Tool-calling probe failed: {exc}",
            started_at=started,
        )

    tool_calls = extract_tool_calls(response.raw)
    if not tool_calls:
        return _result(
            probe="tool_calling",
            status=ProbeStatus.FAIL,
            summary="No tool calls were returned.",
            evidence={"response_excerpt": compact_text(response.content)},
            started_at=started,
        )

    call = tool_calls[0]
    raw_arguments = call.get("function", {}).get("arguments", "{}")
    try:
        parsed_arguments = json.loads(raw_arguments)
        validate(parsed_arguments, schema)
    except (json.JSONDecodeError, ValidationError) as exc:
        return _result(
            probe="tool_calling",
            status=ProbeStatus.WARN,
            summary=f"Tool call was present but arguments were invalid: {exc}",
            evidence={"raw_arguments": raw_arguments},
            started_at=started,
        )

    return _result(
        probe="tool_calling",
        status=ProbeStatus.PASS,
        summary="Tool call returned and matched the expected JSON schema.",
        metrics={"tool_call_count": len(tool_calls)},
        evidence={"arguments": parsed_arguments},
        started_at=started,
    )


async def _long_context_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = utc_now()
    document, expected = _build_long_context(settings.long_context_target_chars)
    prompt = (
        "下面是一份长文档，请只输出 JSON，包含 head、middle、tail 三个字段，"
        "值分别对应文档中的三个 CANARY。不要输出其他内容。\n\n"
        f"{document}"
    )
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=[{"role": "user", "content": prompt}],
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 160),
        )
    except Exception as exc:
        return _result(
            probe="long_context_integrity",
            status=ProbeStatus.ERROR,
            summary=f"Long-context probe failed: {exc}",
            started_at=started,
        )

    parsed = extract_json_block(response.content)
    if not parsed:
        return _result(
            probe="long_context_integrity",
            status=ProbeStatus.FAIL,
            summary="Model did not return parseable JSON for the long-context canaries.",
            evidence={"response_excerpt": compact_text(response.content)},
            started_at=started,
        )

    hit = sum(1 for key, value in expected.items() if parsed.get(key) == value)
    if hit == 3:
        status = ProbeStatus.PASS
        summary = "Head, middle, and tail canaries were preserved."
    elif hit >= 1:
        status = ProbeStatus.WARN
        summary = f"Only {hit}/3 context canaries were preserved."
    else:
        status = ProbeStatus.FAIL
        summary = "No context canary was preserved; truncation is likely."

    return _result(
        probe="long_context_integrity",
        status=status,
        summary=summary,
        metrics={
            "target_chars": settings.long_context_target_chars,
            "canary_hits": hit,
        },
        evidence={"expected": expected, "actual": parsed},
        started_at=started,
    )


async def _multimodal_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = utc_now()
    if not model.supports_vision:
        return _result(
            probe="multimodal_support",
            status=ProbeStatus.SKIP,
            summary="Model is marked as not supporting vision input.",
            started_at=started,
        )
    if not settings.multimodal_image_url:
        return _result(
            probe="multimodal_support",
            status=ProbeStatus.SKIP,
            summary="No multimodal image URL/data URI was configured.",
            started_at=started,
        )

    try:
        response = await client.chat_completion(
            model=model.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请简洁描述这张图片。如果你能判断主色，也请一并说明。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": settings.multimodal_image_url},
                        },
                    ],
                }
            ],
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 120),
        )
    except Exception as exc:
        return _result(
            probe="multimodal_support",
            status=ProbeStatus.ERROR,
            summary=f"Multimodal probe failed: {exc}",
            started_at=started,
        )

    if settings.multimodal_expected_answer:
        expected = settings.multimodal_expected_answer.lower()
        actual = response.content.lower()
        if expected in actual:
            status = ProbeStatus.PASS
            summary = "Multimodal request succeeded and matched the expected signal."
        else:
            status = ProbeStatus.WARN
            summary = "Multimodal request succeeded but did not match the expected answer hint."
    else:
        status = ProbeStatus.PASS if response.content.strip() else ProbeStatus.WARN
        summary = (
            "Multimodal request succeeded."
            if response.content.strip()
            else "Multimodal request returned an empty answer."
        )

    return _result(
        probe="multimodal_support",
        status=status,
        summary=summary,
        evidence={"response_excerpt": compact_text(response.content)},
        started_at=started,
    )


async def run_agentic_suite(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> list[ProbeResult]:
    return [
        await _tool_calling_probe(client, model, settings),
        await _long_context_probe(client, model, settings),
        await _multimodal_probe(client, model, settings),
    ]
