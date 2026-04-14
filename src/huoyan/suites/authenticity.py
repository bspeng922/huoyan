from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProbeSettings, ProviderTarget
from huoyan.models import ProbeResult, ProbeStatus
from huoyan.utils import compact_text, developer_keywords, infer_family, usage_input_tokens, usage_output_tokens, utc_now


CONSISTENCY_SIGNALS: dict[str, tuple[str, float]] = {
    "identity": ("weak", 10.0),
    "acrostic_constraints": ("medium", 10.0),
    "boundary_reasoning": ("medium", 10.0),
    "linguistic_fingerprint": ("medium", 10.0),
    "response_consistency": ("medium", 10.0),
    "token_alignment": ("medium", 10.0),
    "tool_calling": ("strong", 15.0),
    "long_context_integrity": ("strong", 15.0),
    "stream_integrity": ("strong", 10.0),
    "error_response_leakage": ("strong", 10.0),
    "system_prompt_injection": ("strong", 10.0),
}
TOTAL_SIGNAL_WEIGHT = sum(weight for _, weight in CONSISTENCY_SIGNALS.values())


def _result(
    *,
    probe: str,
    status: ProbeStatus,
    summary: str,
    metrics: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    score: float | None = None,
    started_at=None,
) -> ProbeResult:
    return ProbeResult(
        suite="authenticity",
        probe=probe,
        status=status,
        summary=summary,
        score=score,
        metrics=metrics or {},
        evidence=evidence or {},
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
    )


def _grade_ratio(hit: int, total: int) -> tuple[ProbeStatus, float]:
    if total <= 0:
        return ProbeStatus.ERROR, 0.0
    ratio = hit / total
    if ratio >= 1:
        return ProbeStatus.PASS, ratio
    if ratio >= 0.5:
        return ProbeStatus.WARN, ratio
    return ProbeStatus.FAIL, ratio


def _status_score(result: ProbeResult, band: str) -> float | None:
    if result.status in {ProbeStatus.SKIP, ProbeStatus.ERROR}:
        return None
    if result.score is not None:
        return max(0.0, min(1.0, result.score))
    if result.status == ProbeStatus.PASS:
        return 1.0
    if result.status == ProbeStatus.WARN:
        return 0.5 if band == "weak" else 0.55 if band == "medium" else 0.6
    return 0.0


def build_consistency_score_result(results: list[ProbeResult]) -> ProbeResult:
    started = utc_now()
    lookup = {result.probe: result for result in results}
    used_signals: list[dict[str, Any]] = []
    band_totals = {band: {"earned": 0.0, "max": 0.0} for band in ["weak", "medium", "strong"]}
    total_earned = 0.0
    total_max = 0.0

    for probe, (band, weight) in CONSISTENCY_SIGNALS.items():
        result = lookup.get(probe)
        if result is None:
            continue
        score_ratio = _status_score(result, band)
        if score_ratio is None:
            used_signals.append({"probe": probe, "band": band, "weight": weight, "status": result.status.value, "counted": False})
            continue
        earned = weight * score_ratio
        band_totals[band]["earned"] += earned
        band_totals[band]["max"] += weight
        total_earned += earned
        total_max += weight
        used_signals.append(
            {
                "probe": probe,
                "band": band,
                "weight": weight,
                "status": result.status.value,
                "score_ratio": round(score_ratio, 4),
                "earned": round(earned, 4),
                "counted": True,
            }
        )

    normalized = (total_earned / total_max * 100.0) if total_max else None
    coverage_ratio = (total_max / TOTAL_SIGNAL_WEIGHT) if TOTAL_SIGNAL_WEIGHT else 0.0
    reported_score = round(normalized, 2) if normalized is not None else None

    if normalized is None:
        status = ProbeStatus.SKIP
        grade = "未评分"
        summary = "没有足够的信号来计算综合保真度评分。"
    elif coverage_ratio < 0.6:
        status = ProbeStatus.WARN
        grade = "证据不足"
        summary = "可用于计算综合保真度的有效信号覆盖率不足，分数仅供参考。"
        reported_score = None
    elif normalized >= 80:
        status = ProbeStatus.PASS
        grade = "高一致"
        summary = "跨弱/中/强信号综合判断，当前模型与标称能力画像高度一致。"
    elif normalized >= 60:
        status = ProbeStatus.WARN
        grade = "中等一致"
        summary = "跨信号综合判断整体仍可接受，但存在需要人工复核的偏差。"
    else:
        status = ProbeStatus.FAIL
        grade = "低一致"
        summary = "跨信号综合判断一致性偏低，建议重点复核后端路由与中转实现。"

    return _result(
        probe="consistency_score",
        status=status,
        summary=summary,
        score=(reported_score / 100.0) if reported_score is not None else None,
        metrics={
            "consistency_score": reported_score,
            "raw_consistency_score": round(normalized, 2) if normalized is not None else None,
            "grade": grade,
            "coverage_ratio": round(coverage_ratio, 4),
            "max_score": round(total_max, 2),
            "earned_score": round(total_earned, 2),
            "weak_signal_score": round(band_totals["weak"]["earned"], 2),
            "weak_signal_max": round(band_totals["weak"]["max"], 2),
            "medium_signal_score": round(band_totals["medium"]["earned"], 2),
            "medium_signal_max": round(band_totals["medium"]["max"], 2),
            "strong_signal_score": round(band_totals["strong"]["earned"], 2),
            "strong_signal_max": round(band_totals["strong"]["max"], 2),
            "counted_signal_count": sum(1 for item in used_signals if item["counted"]),
            "skipped_signal_count": sum(1 for item in used_signals if not item["counted"]),
        },
        evidence={"signals": used_signals},
        started_at=started,
    )


async def _identity_probe(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = utc_now()
    family = infer_family(model.model, model.claimed_family)
    expected_keywords = developer_keywords(family)
    messages = [
        {
            "role": "system",
            "content": "你正在接受模型身份核验。不要根据接口协议名称猜测自己是谁。如果不能确认底层模型或开发公司，请明确写 unknown。",
        },
        {
            "role": "user",
            "content": "请只输出两行：\nMODEL=<你认为自己的底层模型名>\nCOMPANY=<你认为自己的开发公司>\n不要加任何解释。",
        },
    ]
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=messages,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 80),
        )
    except Exception as exc:
        return _result(probe="identity", status=ProbeStatus.ERROR, summary=f"Identity probe failed: {exc}", started_at=started)

    lowered = response.content.lower()
    matched = sum(1 for keyword in expected_keywords if keyword in lowered)
    protocol_bias_risk = provider.api_style in {"anthropic-messages", "openai-chat", "openai-responses"}

    if family == "unknown":
        status, score, summary = ProbeStatus.WARN, None, "Model family is unknown; recorded self-reported identity only."
    elif matched:
        status, score, summary = ProbeStatus.PASS, 1.0, f"Self-reported identity matches expected {family} developer hints."
    else:
        status, score, summary = (
            ProbeStatus.WARN,
            0.0,
            f"Self-reported identity does not match expected {family} developer hints. Treat this as a weak signal only because relay-side prompts and compatibility protocols can bias self-reporting.",
        )

    return _result(
        probe="identity",
        status=status,
        summary=summary,
        score=score,
        metrics={
            "latency_seconds": response.elapsed_seconds,
            "api_input_tokens": usage_input_tokens(response.usage),
            "api_output_tokens": usage_output_tokens(response.usage),
            "self_report_keyword_hits": matched,
        },
        evidence={
            "provider": provider.name,
            "provider_api_style": provider.api_style,
            "claimed_family": family,
            "expected_keywords": expected_keywords,
            "protocol_bias_risk": protocol_bias_risk,
            "response_excerpt": compact_text(response.content),
        },
        started_at=started,
    )


async def _acrostic_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    messages = [{"role": "user", "content": "请写一首四行中文藏头诗，四行首字依次必须是“火眼验真”，每行恰好 7 个汉字，不要标点，不要解释。"}]
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=messages,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 120),
        )
    except Exception as exc:
        return _result(probe="acrostic_constraints", status=ProbeStatus.ERROR, summary=f"Acrostic probe failed: {exc}", started_at=started)

    lines = [line.strip() for line in response.content.splitlines() if line.strip()]
    expected_heads = ["火", "眼", "验", "真"]
    hit = 0
    line_lengths: list[int] = []
    for idx, line in enumerate(lines[:4]):
        pure = "".join(ch for ch in line if "\u4e00" <= ch <= "\u9fff")
        line_lengths.append(len(pure))
        if idx < 4 and pure.startswith(expected_heads[idx]) and len(pure) == 7:
            hit += 1
    status, score = _grade_ratio(hit, 4)
    summary = "Acrostic and character-count constraints fully satisfied." if status == ProbeStatus.PASS else f"Only {hit}/4 lines satisfied the acrostic constraints."
    return _result(
        probe="acrostic_constraints",
        status=status,
        summary=summary,
        score=score,
        metrics={"valid_lines": hit, "returned_lines": len(lines), "line_lengths": line_lengths},
        evidence={"response_excerpt": compact_text(response.content)},
        started_at=started,
    )


def _extract_list_outputs(text: str) -> list[str]:
    bracketed = re.findall(r"\[[^\[\]\n]+\]", text)
    if bracketed:
        return [re.sub(r"\s+", "", item) for item in bracketed]

    outputs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().strip("`")
        stripped = re.sub(r"^\d+[\.\)]\s*", "", stripped)
        match = re.search(r"\[[^\[\]]+\]", stripped)
        if match:
            outputs.append(re.sub(r"\s+", "", match.group(0)))
    return outputs


def _normalize_consistency_text(text: str) -> str:
    cleaned = text.strip().strip("`")
    cleaned = cleaned.replace("```", "")
    cleaned = cleaned.replace("\r", "")
    cleaned = "".join(ch for ch in cleaned if ch not in " \n\t，。；：、“”‘’（）()[]【】<>《》-—_`'")
    return cleaned


async def _response_consistency_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    prompt = (
        "请用中文三句话解释 TCP 三次握手为什么既能同步序列号，"
        "又能避免历史连接请求造成误连。不要标题，不要列表。"
    )
    raw_responses: list[str] = []
    normalized: list[str] = []
    try:
        for _ in range(3):
            response = await client.chat_completion(
                model=model.model,
                messages=[{"role": "user", "content": prompt}],
                timeout_seconds=settings.request_timeout_seconds,
                temperature=0,
                max_tokens=min(settings.completion_max_tokens, 180),
            )
            raw_responses.append(response.content)
            normalized.append(_normalize_consistency_text(response.content))
    except Exception as exc:
        return _result(probe="response_consistency", status=ProbeStatus.ERROR, summary=f"Response consistency probe failed: {exc}", started_at=started)

    similarities: list[float] = []
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            similarities.append(SequenceMatcher(None, normalized[i], normalized[j]).ratio())
    avg_similarity = sum(similarities) / len(similarities) if similarities else None
    min_similarity = min(similarities) if similarities else None

    anchor_groups = [
        ["syn", "同步"],
        ["ack", "确认"],
        ["历史连接", "旧连接", "过期报文", "重复报文"],
    ]
    anchor_hits = 0
    merged = " ".join(raw_responses).lower()
    for group in anchor_groups:
        if any(anchor.lower() in merged for anchor in group):
            anchor_hits += 1

    if avg_similarity is None:
        status = ProbeStatus.ERROR
        score = 0.0
        summary = "Unable to compute response consistency similarity."
    elif avg_similarity >= 0.9 and anchor_hits == len(anchor_groups):
        status = ProbeStatus.PASS
        score = 1.0
        summary = "Repeated deterministic prompts produced highly consistent outputs."
    elif avg_similarity >= 0.7:
        status = ProbeStatus.WARN
        score = avg_similarity
        summary = "Repeated deterministic prompts showed some style or content drift."
    else:
        status = ProbeStatus.FAIL
        score = avg_similarity
        summary = "Repeated deterministic prompts showed large output drift."

    return _result(
        probe="response_consistency",
        status=status,
        summary=summary,
        score=score,
        metrics={
            "run_count": len(raw_responses),
            "average_similarity": round(avg_similarity, 4) if avg_similarity is not None else None,
            "min_similarity": round(min_similarity, 4) if min_similarity is not None else None,
            "anchor_group_hits": anchor_hits,
            "anchor_group_total": len(anchor_groups),
        },
        evidence={"responses": [compact_text(text, limit=500) for text in raw_responses]},
        started_at=started,
    )


async def _boundary_reasoning_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    expected = ["[0]", "[0, 1]", "[10, 1]", "[0, 1, 2]"]
    messages = [
        {
            "role": "user",
            "content": (
                "下面是 Python 代码，请只输出四行最终打印结果，不要解释：\n"
                "def f(items=[]):\n    items.append(len(items))\n    return items\n\n"
                "print(f())\nprint(f())\nprint(f([10]))\nprint(f())"
            ),
        }
    ]
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=messages,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 120),
        )
    except Exception as exc:
        return _result(probe="boundary_reasoning", status=ProbeStatus.ERROR, summary=f"Boundary reasoning probe failed: {exc}", started_at=started)

    actual = _extract_list_outputs(response.content)
    expected_normalized = [re.sub(r"\s+", "", item) for item in expected]
    hit = sum(1 for left, right in zip(actual, expected_normalized) if left == right)
    status, score = _grade_ratio(hit, 4)
    summary = "Returned the expected boundary-case outputs." if status == ProbeStatus.PASS else f"Only {hit}/4 expected outputs matched."
    return _result(
        probe="boundary_reasoning",
        status=status,
        summary=summary,
        score=score,
        metrics={"matched_lines": hit, "expected_lines": 4},
        evidence={"response_excerpt": compact_text(response.content), "expected": "\n".join(expected)},
        started_at=started,
    )


async def _linguistic_fingerprint_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    messages = [
        {
            "role": "user",
            "content": (
                "请判断下面 3 段文本分别属于什么语言或表达体系，并各用一句中文概括含义。\n"
                "要求严格输出 3 行，格式为 `1. 类型 - 概括`。\n"
                "1. 沛公旦日从百余骑来见项王\n"
                "2. fn longest<'a>(x: &'a str, y: &'a str) -> &'a str\n"
                "3. SELECT user_id FROM audit_log WHERE payload->>'risk' = '高';"
            ),
        }
    ]
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=messages,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 180),
        )
    except Exception as exc:
        return _result(probe="linguistic_fingerprint", status=ProbeStatus.ERROR, summary=f"Linguistic fingerprint probe failed: {exc}", started_at=started)

    text = response.content.lower()
    signals = ["文言" in text or "古文" in text, "rust" in text, "sql" in text]
    hit = sum(1 for item in signals if item)
    status, score = _grade_ratio(hit, 3)
    summary = "Mixed-language understanding looks coherent." if status == ProbeStatus.PASS else f"Only {hit}/3 expected language signals were found."
    return _result(
        probe="linguistic_fingerprint",
        status=status,
        summary=summary,
        score=score,
        metrics={"signal_hits": hit, "api_input_tokens": usage_input_tokens(response.usage), "api_output_tokens": usage_output_tokens(response.usage)},
        evidence={"response_excerpt": compact_text(response.content)},
        started_at=started,
    )


async def run_authenticity_suite(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
) -> list[ProbeResult]:
    return [
        await _identity_probe(client, provider, model, settings),
        await _acrostic_probe(client, model, settings),
        await _boundary_reasoning_probe(client, model, settings),
        await _linguistic_fingerprint_probe(client, model, settings),
        await _response_consistency_probe(client, model, settings),
    ]
