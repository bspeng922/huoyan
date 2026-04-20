from __future__ import annotations

import json
from typing import Any

from jsonschema import ValidationError, validate

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProbeSettings, ProviderTarget
from huoyan.models import ProbeResult, ProbeStatus
from huoyan.progress import ProgressCallback, run_probe_sequence
from huoyan.utils import compact_text, extract_json_block, extract_message_text, extract_tool_calls, local_now


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
        started_at=started_at or local_now(),
        finished_at=local_now(),
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


def _build_long_context_targets(max_target_chars: int) -> list[int]:
    checkpoints = [8000, 16000, 32000, max_target_chars]
    targets = sorted({min(max_target_chars, item) for item in checkpoints if item > 0})
    return targets or [max_target_chars]


async def _tool_calling_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = local_now()
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
    started = local_now()
    checkpoints = _build_long_context_targets(settings.long_context_target_chars)
    sweep_results: list[dict[str, Any]] = []
    first_failed_target_chars: int | None = None
    max_preserved_target_chars: int | None = None

    for target_chars in checkpoints:
        document, expected = _build_long_context(target_chars)
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
            if first_failed_target_chars is None:
                first_failed_target_chars = target_chars
            sweep_results.append(
                {
                    "target_chars": target_chars,
                    "status": "error",
                    "error": str(exc),
                }
            )
            break

        parsed = extract_json_block(response.content)
        if not parsed:
            if first_failed_target_chars is None:
                first_failed_target_chars = target_chars
            sweep_results.append(
                {
                    "target_chars": target_chars,
                    "status": "fail",
                    "canary_hits": 0,
                    "expected": expected,
                    "response_excerpt": compact_text(response.content),
                }
            )
            break

        hit = sum(1 for key, value in expected.items() if parsed.get(key) == value)
        result_status = "pass" if hit == 3 else "warn" if hit >= 1 else "fail"
        sweep_results.append(
            {
                "target_chars": target_chars,
                "status": result_status,
                "canary_hits": hit,
                "expected": expected,
                "actual": parsed,
            }
        )
        if hit == 3:
            max_preserved_target_chars = target_chars
            continue
        if first_failed_target_chars is None:
            first_failed_target_chars = target_chars
        break

    pass_count = sum(1 for item in sweep_results if item.get("status") == "pass")
    if not sweep_results:
        return _result(
            probe="long_context_integrity",
            status=ProbeStatus.ERROR,
            summary="Long-context sweep did not produce any result.",
            started_at=started,
        )

    first_status = sweep_results[0]["status"]
    if first_status == "error":
        status = ProbeStatus.ERROR
        summary = "Long-context sweep failed on the first checkpoint."
    elif pass_count == len(checkpoints):
        status = ProbeStatus.PASS
        summary = f"All long-context checkpoints passed up to {max_preserved_target_chars} characters."
    elif pass_count > 0:
        status = ProbeStatus.WARN
        summary = f"Long-context canaries held up to {max_preserved_target_chars} characters, then degraded at {first_failed_target_chars} characters."
    else:
        status = ProbeStatus.FAIL
        summary = f"Long-context canaries failed at the first checkpoint ({first_failed_target_chars} characters)."

    return _result(
        probe="long_context_integrity",
        status=status,
        summary=summary,
        metrics={
            "tested_target_chars": checkpoints,
            "fully_preserved_targets": pass_count,
            "max_preserved_target_chars": max_preserved_target_chars,
            "first_failed_target_chars": first_failed_target_chars,
            "canary_hits": max((item.get("canary_hits") or 0) for item in sweep_results),
        },
        evidence={"sweep_results": sweep_results},
        started_at=started,
    )


async def _multimodal_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = local_now()
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

TOOL_RESULT_JSON = '{"city": "杭州", "temperature": 28, "condition": "晴", "humidity": 65}'
MULTI_TURN_OUTPUT_INSTRUCTION = (
    "根据上面的工具结果，只输出 JSON，不要解释。"
    'JSON 必须包含 city、temperature、condition、clothing_advice 四个字段。'
)


def _build_multi_turn_payload_openai(
    model_name: str, tool_call_id: str, phase1_assistant: dict[str, Any], tool_result_content: str
) -> dict[str, Any]:
    return {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "帮我查一下杭州今天的天气"},
            phase1_assistant,
            {"role": "tool", "tool_call_id": tool_call_id, "content": tool_result_content},
            {"role": "user", "content": MULTI_TURN_OUTPUT_INSTRUCTION},
        ],
    }


def _build_multi_turn_payload_anthropic(
    model_name: str, tool_use_id: str, tool_name: str, phase1_assistant_content: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "model": model_name,
        "max_tokens": 200,
        "messages": [
            {"role": "user", "content": "帮我查一下杭州今天的天气"},
            {"role": "assistant", "content": phase1_assistant_content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": TOOL_RESULT_JSON,
                    },
                    {"type": "text", "text": MULTI_TURN_OUTPUT_INSTRUCTION},
                ],
            },
        ],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather information for a city",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ],
    }


def _build_multi_turn_payload_responses(
    model_name: str, call_id: str, function_name: str, function_args: str
) -> dict[str, Any]:
    return {
        "model": model_name,
        "input": [
            {"role": "user", "content": "帮我查一下杭州今天的天气"},
            {
                "type": "function_call",
                "id": call_id,
                "name": function_name,
                "arguments": function_args,
            },
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": TOOL_RESULT_JSON,
            },
            {"role": "user", "content": MULTI_TURN_OUTPUT_INSTRUCTION},
        ],
    }


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _coerce_temperature(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _multi_turn_tool_probe(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = local_now()
    if not model.supports_tools:
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.SKIP,
            summary="Model is marked as not supporting tool calls.",
            started_at=started,
        )

    weather_tool = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather information for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }

    # Phase 1: Ask model to call get_weather for Hangzhou
    try:
        phase1_response = await client.chat_completion(
            model=model.model,
            messages=[{"role": "user", "content": "帮我查一下杭州今天的天气"}],
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 80),
            tools=[weather_tool],
            tool_choice={"type": "function", "function": {"name": "get_weather"}},
        )
    except Exception as exc:
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.ERROR,
            summary=f"Multi-turn tool Phase 1 failed: {exc}",
            started_at=started,
        )

    tool_calls = extract_tool_calls(phase1_response.raw)
    if not tool_calls:
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.FAIL,
            summary="Phase 1: No tool call was returned for get_weather request.",
            evidence={"response_excerpt": compact_text(phase1_response.content)},
            started_at=started,
        )

    first_call = tool_calls[0]
    call_id = first_call.get("id") or "call_default"
    function_name = first_call.get("function", {}).get("name", "get_weather")
    function_args = first_call.get("function", {}).get("arguments", '{"city": "杭州"}')

    # Phase 2: Build multi-turn payload with tool result and send via raw_json_request
    api_style = provider.api_style
    if api_style == "openai-chat":
        assistant_msg = phase1_response.raw.get("choices", [{}])[0].get("message", {})
        payload = _build_multi_turn_payload_openai(model.model, call_id, assistant_msg, TOOL_RESULT_JSON)
    elif api_style == "anthropic-messages":
        assistant_content = phase1_response.raw.get("content", [])
        payload = _build_multi_turn_payload_anthropic(model.model, call_id, function_name, assistant_content)
    elif api_style == "openai-responses":
        payload = _build_multi_turn_payload_responses(model.model, call_id, function_name, function_args)
    else:
        assistant_msg = phase1_response.raw.get("choices", [{}])[0].get("message", {})
        payload = _build_multi_turn_payload_openai(model.model, call_id, assistant_msg, TOOL_RESULT_JSON)

    try:
        raw_response = await client.raw_json_request(payload=payload, timeout_seconds=settings.request_timeout_seconds)
    except Exception as exc:
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.ERROR,
            summary=f"Multi-turn tool Phase 2 failed: {exc}",
            started_at=started,
        )

    if raw_response.json_body is None:
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.ERROR,
            summary="Phase 2 returned non-JSON response.",
            evidence={"raw_text": compact_text(raw_response.text)},
            started_at=started,
        )

    response_text = extract_message_text(raw_response.json_body)
    if not response_text:
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.FAIL,
            summary="Phase 2 returned empty response text.",
            evidence={"raw_json_excerpt": compact_text(str(raw_response.json_body)[:500])},
            started_at=started,
        )

    parsed = extract_json_block(response_text)
    if not isinstance(parsed, dict):
        return _result(
            probe="multi_turn_tool",
            status=ProbeStatus.FAIL,
            summary="Phase 2 did not return parseable JSON.",
            evidence={"response_excerpt": compact_text(response_text)},
            started_at=started,
        )

    actual_city = _normalized_text(parsed.get("city"))
    actual_condition = _normalized_text(parsed.get("condition"))
    actual_temperature = _coerce_temperature(parsed.get("temperature"))
    clothing_advice = str(parsed.get("clothing_advice", "")).strip()

    city_ok = actual_city in {"杭州", "hangzhou"}
    condition_ok = actual_condition in {"晴", "sunny"}
    temperature_ok = actual_temperature is not None and abs(actual_temperature - 28.0) < 0.1
    clothing_ok = bool(clothing_advice)
    matched_fields = sum([city_ok, condition_ok, temperature_ok, clothing_ok])

    if matched_fields == 4:
        status = ProbeStatus.PASS
        summary = "Multi-turn tool call integrity verified: the model carried structured tool-result fields into the final answer."
    elif matched_fields >= 3:
        status = ProbeStatus.WARN
        summary = "Multi-turn tool call partially verified: most structured tool-result fields were preserved, but at least one field drifted."
    else:
        status = ProbeStatus.FAIL
        summary = "Multi-turn tool call failed: the final structured answer did not preserve enough tool-result fields."

    return _result(
        probe="multi_turn_tool",
        status=status,
        summary=summary,
        metrics={
            "matched_fields": matched_fields,
            "required_fields": 4,
            "city_ok": city_ok,
            "temperature_ok": temperature_ok,
            "condition_ok": condition_ok,
            "clothing_advice_ok": clothing_ok,
        },
        evidence={"response_excerpt": compact_text(response_text), "actual": parsed},
        started_at=started,
    )


async def run_agentic_suite(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
    progress_callback: ProgressCallback | None = None,
) -> list[ProbeResult]:
    return await run_probe_sequence(
        suite="agentic",
        progress_callback=progress_callback,
        steps=[
            ("tool_calling", lambda: _tool_calling_probe(client, model, settings)),
            (
                "multi_turn_tool",
                lambda: _multi_turn_tool_probe(client, provider, model, settings),
            ),
            (
                "long_context_integrity",
                lambda: _long_context_probe(client, model, settings),
            ),
            ("multimodal_support", lambda: _multimodal_probe(client, model, settings)),
        ],
    )
