from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProbeSettings
from huoyan.models import ProbeResult, ProbeStatus
from huoyan.utils import estimate_prompt_tokens, estimate_text_tokens, infer_family, percentile, usage_input_tokens, usage_output_tokens, usage_reasoning_tokens, utc_now


TTFT_THRESHOLDS: dict[str, tuple[float, float]] = {
    "openai": (2.5, 6.0),
    "claude": (2.5, 6.0),
    "gemini": (2.5, 6.0),
    "glm": (1.2, 3.0),
    "qwen": (1.2, 3.0),
    "kimi": (1.5, 4.0),
    "deepseek": (1.5, 4.0),
    "minimax": (1.5, 4.0),
}


def _ttft_thresholds(family: str, reasoning_observed: bool) -> tuple[float, float, str]:
    warn_at, fail_at = TTFT_THRESHOLDS.get(family, (2.5, 6.0))
    if not reasoning_observed:
        return warn_at, fail_at, "default"
    return max(warn_at * 2.5, warn_at + 1.5), max(fail_at * 2.5, fail_at + 4.0), "reasoning_adjusted"


def _result(*, probe: str, status: ProbeStatus, summary: str, metrics: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None, started_at=None) -> ProbeResult:
    return ProbeResult(
        suite="performance",
        probe=probe,
        status=status,
        summary=summary,
        metrics=metrics or {},
        evidence=evidence or {},
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
    )


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "min": None, "max": None, "p99": None, "p90": None, "p75": None}
    return {
        "avg": round(sum(values) / len(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "p99": round(percentile(values, 0.99), 4) if percentile(values, 0.99) is not None else None,
        "p90": round(percentile(values, 0.90), 4) if percentile(values, 0.90) is not None else None,
        "p75": round(percentile(values, 0.75), 4) if percentile(values, 0.75) is not None else None,
    }


async def _stream_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    if not model.supports_stream:
        return _result(probe="ttft_tps", status=ProbeStatus.SKIP, summary="Model is marked as not supporting stream output.", started_at=started)

    prompt = "请连续输出 450 到 550 个汉字，主题是“大模型网关压测观察”，内容要自然连贯，不要分点，不要标题。"
    messages = [{"role": "user", "content": prompt}]
    family = infer_family(model.model, model.claimed_family)
    input_token_estimate = estimate_prompt_tokens(messages, model.model, family)

    samples: list[dict[str, Any]] = []
    for index in range(settings.performance_stream_samples):
        try:
            response = await client.stream_chat_completion(
                model=model.model,
                messages=messages,
                timeout_seconds=settings.request_timeout_seconds,
                temperature=0.3,
                max_tokens=settings.stream_max_tokens,
                stream_options={"include_usage": True},
            )
        except Exception as exc:
            return _result(probe="ttft_tps", status=ProbeStatus.ERROR, summary=f"Streaming probe failed: {exc}", started_at=started)

        output_token_estimate = estimate_text_tokens(response.content, model.model, family)
        input_tokens = usage_input_tokens(response.usage) or input_token_estimate.get("count")
        api_output_tokens_total = usage_output_tokens(response.usage)
        reasoning_tokens = usage_reasoning_tokens(response.usage)
        if api_output_tokens_total is not None and reasoning_tokens is not None:
            output_tokens = max(api_output_tokens_total - reasoning_tokens, 0)
        else:
            output_tokens = api_output_tokens_total or output_token_estimate.get("count")
        output_token_throughput = None
        inter_token_latency_ms = None
        if response.generation_seconds and output_tokens:
            output_token_throughput = output_tokens / response.generation_seconds
            if output_tokens > 1:
                inter_token_latency_ms = (response.generation_seconds / (output_tokens - 1)) * 1000
        request_latency_ms = response.elapsed_seconds * 1000
        request_throughput = 1 / response.elapsed_seconds if response.elapsed_seconds > 0 else None
        reasoning_observed = bool((reasoning_tokens or 0) > 0)
        if not reasoning_observed and response.ttft_seconds is not None and response.first_content_seconds is not None:
            reasoning_observed = (response.first_content_seconds - response.ttft_seconds) > 1.0
        samples.append(
            {
                "ttft_seconds": response.ttft_seconds,
                "first_content_seconds": response.first_content_seconds,
                "reasoning_observed": reasoning_observed,
                "inter_token_latency_ms": inter_token_latency_ms,
                "request_latency_ms": request_latency_ms,
                "generation_seconds": response.generation_seconds,
                "elapsed_seconds": response.elapsed_seconds,
                "input_sequence_length": input_tokens,
                "output_sequence_length": output_tokens,
                "api_output_tokens_total": api_output_tokens_total,
                "api_reasoning_tokens": reasoning_tokens,
                "api_input_tokens": usage_input_tokens(response.usage),
                "api_output_tokens": output_tokens,
                "estimated_input_tokens": input_token_estimate.get("count"),
                "estimated_output_tokens": output_token_estimate.get("count"),
                "output_token_throughput_per_second": output_token_throughput,
                "request_throughput_per_second": request_throughput,
                "output_excerpt": response.content[:240],
            }
        )
        if index + 1 < settings.performance_stream_samples and settings.performance_stream_sample_interval_seconds > 0:
            await asyncio.sleep(settings.performance_stream_sample_interval_seconds)

    ttft_values = [item["ttft_seconds"] for item in samples if item["ttft_seconds"] is not None]
    ttfc_values = [item["first_content_seconds"] for item in samples if item["first_content_seconds"] is not None]
    itl_values = [item["inter_token_latency_ms"] for item in samples if item["inter_token_latency_ms"] is not None]
    request_latency_values = [item["request_latency_ms"] for item in samples if item["request_latency_ms"] is not None]
    input_length_values = [float(item["input_sequence_length"]) for item in samples if item["input_sequence_length"] is not None]
    output_length_values = [float(item["output_sequence_length"]) for item in samples if item["output_sequence_length"] is not None]
    output_tput_values = [item["output_token_throughput_per_second"] for item in samples if item["output_token_throughput_per_second"] is not None]
    request_tput_values = [item["request_throughput_per_second"] for item in samples if item["request_throughput_per_second"] is not None]

    ttft_stats = _stats(ttft_values)
    ttfc_stats = _stats(ttfc_values)
    itl_stats = _stats(itl_values)
    request_latency_stats = _stats(request_latency_values)
    input_length_stats = _stats(input_length_values)
    output_length_stats = _stats(output_length_values)
    output_tput_stats = _stats(output_tput_values)
    request_tput_stats = _stats(request_tput_values)

    reasoning_observed = any(bool(item.get("reasoning_observed")) for item in samples)
    warn_at, fail_at, threshold_mode = _ttft_thresholds(family, reasoning_observed)
    observed_ttft = ttft_stats["p90"] if ttft_stats["p90"] is not None else ttft_stats["avg"]
    if observed_ttft is None:
        status = ProbeStatus.WARN
        summary = "No first-reply timestamp was captured from the sampled streams."
    elif observed_ttft >= fail_at:
        status = ProbeStatus.FAIL
        summary = f"TTFT p90 {observed_ttft:.2f}s exceeded the fail threshold {fail_at:.2f}s for {family}."
    elif observed_ttft >= warn_at:
        status = ProbeStatus.WARN
        summary = f"TTFT p90 {observed_ttft:.2f}s exceeded the warning threshold {warn_at:.2f}s for {family}."
    else:
        status = ProbeStatus.PASS
        summary = f"TTFT p90 {observed_ttft:.2f}s is within the expected range for {family}."

    return _result(
        probe="ttft_tps",
        status=status,
        summary=summary,
        metrics={
            "sample_count": len(samples),
            "ttft_seconds": ttft_stats["avg"],
            "ttft_stats_seconds": ttft_stats,
            "first_content_seconds": ttfc_stats["avg"],
            "first_content_stats_seconds": ttfc_stats,
            "inter_token_latency_ms": itl_stats["avg"],
            "inter_token_latency_stats_ms": itl_stats,
            "request_latency_ms": request_latency_stats["avg"],
            "request_latency_stats_ms": request_latency_stats,
            "input_sequence_length": int(round(input_length_stats["avg"])) if input_length_stats["avg"] is not None else None,
            "input_sequence_length_stats": input_length_stats,
            "output_sequence_length": int(round(output_length_stats["avg"])) if output_length_stats["avg"] is not None else None,
            "output_sequence_length_stats": output_length_stats,
            "estimated_input_tokens": samples[-1]["estimated_input_tokens"] if samples else input_token_estimate.get("count"),
            "estimated_output_tokens": samples[-1]["estimated_output_tokens"] if samples else None,
            "api_input_tokens": samples[-1]["api_input_tokens"] if samples else None,
            "api_output_tokens": samples[-1]["api_output_tokens"] if samples else None,
            "output_token_throughput_per_second": output_tput_stats["avg"],
            "output_token_throughput_stats_per_second": output_tput_stats,
            "request_throughput_per_second": request_tput_stats["avg"],
            "request_throughput_stats_per_second": request_tput_stats,
            "tokens_per_second": output_tput_stats["avg"],
            "reasoning_observed": reasoning_observed,
            "ttft_threshold_warn_seconds": round(warn_at, 4),
            "ttft_threshold_fail_seconds": round(fail_at, 4),
            "ttft_threshold_mode": threshold_mode,
        },
        evidence={"sample_outputs": [item["output_excerpt"] for item in samples[:2]]},
        started_at=started,
    )


async def _single_concurrency_call(client: OpenAICompatClient, model_name: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        response = await client.chat_completion(
            model=model_name,
            messages=[{"role": "user", "content": "请只回答 OK。"}],
            timeout_seconds=timeout_seconds,
            temperature=0,
            max_tokens=8,
        )
        return {"ok": True, "status_code": response.status_code, "latency_seconds": response.elapsed_seconds}
    except Exception as exc:
        resp = getattr(exc, "response", None)
        status_code = getattr(resp, "status_code", None)
        rate_limit_headers = {}
        if resp is not None and getattr(resp, "headers", None):
            headers = dict(resp.headers)
            interesting = [
                "retry-after",
                "x-ratelimit-limit",
                "x-ratelimit-remaining",
                "x-ratelimit-reset",
                "x-ratelimit-reset-requests",
                "x-ratelimit-reset-tokens",
            ]
            for key in interesting:
                value = headers.get(key)
                if value is not None:
                    rate_limit_headers[key] = value
        return {
            "ok": False,
            "status_code": status_code,
            "error": str(exc),
            "rate_limit_headers": rate_limit_headers,
        }


async def _concurrency_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    all_metrics: list[dict[str, Any]] = []
    worst_status = ProbeStatus.PASS
    status_rank = {ProbeStatus.PASS: 0, ProbeStatus.SKIP: 1, ProbeStatus.WARN: 2, ProbeStatus.FAIL: 3, ProbeStatus.ERROR: 4}
    for level in settings.concurrency_levels:
        batch_started = perf_counter()
        tasks = [asyncio.create_task(_single_concurrency_call(client, model.model, settings.request_timeout_seconds)) for _ in range(level)]
        results = await asyncio.gather(*tasks)
        batch_elapsed = perf_counter() - batch_started
        latencies = [item["latency_seconds"] for item in results if item.get("ok")]
        success_count = sum(1 for item in results if item.get("ok"))
        success_rate = success_count / level
        status_codes: dict[str, int] = {}
        rate_limit_samples: list[dict[str, Any]] = []
        for item in results:
            code = item.get("status_code")
            if code is not None:
                status_codes[str(code)] = status_codes.get(str(code), 0) + 1
            if item.get("rate_limit_headers"):
                rate_limit_samples.append(
                    {
                        "status_code": code,
                        "headers": item["rate_limit_headers"],
                    }
                )
        level_status = ProbeStatus.PASS if success_rate == 1.0 else ProbeStatus.WARN if success_rate >= 0.95 else ProbeStatus.FAIL
        if status_rank[level_status] > status_rank[worst_status]:
            worst_status = level_status
        request_throughput = success_count / batch_elapsed if batch_elapsed > 0 else None
        all_metrics.append(
            {
                "concurrency": level,
                "success_rate": round(success_rate, 4),
                "batch_elapsed_seconds": round(batch_elapsed, 4),
                "request_throughput_per_second": round(request_throughput, 4) if request_throughput is not None else None,
                "p50_latency_seconds": round(percentile(latencies, 0.50), 4) if latencies else None,
                "p95_latency_seconds": round(percentile(latencies, 0.95), 4) if latencies else None,
                "status_codes": status_codes,
                "rate_limit_samples": rate_limit_samples[:3],
            }
        )
    summary = "All configured concurrency levels completed without request loss." if worst_status == ProbeStatus.PASS else "Some concurrency levels showed request loss, throttling, or gateway instability."
    return _result(probe="concurrency", status=worst_status, summary=summary, metrics={"levels": all_metrics}, started_at=started)


async def _availability_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    samples: list[dict[str, Any]] = []
    for index in range(settings.uptime_samples):
        result = await _single_concurrency_call(client, model.model, settings.request_timeout_seconds)
        result["sample"] = index + 1
        samples.append(result)
        if index + 1 < settings.uptime_samples and settings.uptime_interval_seconds > 0:
            await asyncio.sleep(settings.uptime_interval_seconds)
    success_count = sum(1 for item in samples if item.get("ok"))
    availability = success_count / len(samples)
    if availability == 1.0:
        status, summary = ProbeStatus.PASS, "Short-window availability sampling completed with no failures."
    elif availability >= 0.95:
        status, summary = ProbeStatus.WARN, "Short-window availability sampling showed intermittent failures."
    else:
        status, summary = ProbeStatus.FAIL, "Short-window availability sampling showed frequent failures."
    return _result(probe="availability", status=status, summary=summary, metrics={"availability_ratio": round(availability, 4), "samples": samples}, started_at=started)


async def run_performance_suite(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> list[ProbeResult]:
    return [await _stream_probe(client, model, settings), await _concurrency_probe(client, model, settings), await _availability_probe(client, model, settings)]
