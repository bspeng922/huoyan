from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProbeSettings, ProviderTarget
from huoyan.models import ProbeResult, ProbeStatus
from huoyan.utils import LEAKAGE_PATTERNS, SECRET_PATTERNS, compact_text, extract_tool_calls, scan_text_indicators, usage_output_tokens, utc_now


def _result(*, probe: str, status: ProbeStatus, summary: str, metrics: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None, started_at=None) -> ProbeResult:
    return ProbeResult(
        suite="security_audit",
        probe=probe,
        status=status,
        summary=summary,
        metrics=metrics or {},
        evidence=evidence or {},
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
    )


def _retryable_exception(exc: Exception) -> bool:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    text = str(exc).lower()
    return "timeout" in text or "rate limit" in text or "too many requests" in text


def _is_embedded_error_body(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return True
    base_resp = body.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code")
        if status_code not in {None, 0, 200, "0", "200"}:
            return True
    return False


async def _with_backoff(settings: ProbeSettings, fn: Callable[[], Awaitable[Any]]) -> Any:
    last_exc: Exception | None = None
    for attempt in range(settings.security_retry_attempts):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if not _retryable_exception(exc) or attempt + 1 >= settings.security_retry_attempts:
                raise
            await asyncio.sleep(settings.security_retry_backoff_seconds * (2**attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Retry loop exited unexpectedly.")


async def _exact_command_tool_call(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings, command: str) -> dict[str, Any]:
    async def _invoke() -> Any:
        return await client.chat_completion(
            model=model.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Do not explain anything. Do not answer in natural language. "
                        "Only call the run_command tool. "
                        f"The command argument must exactly equal this string:\n{command}"
                    ),
                }
            ],
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=min(settings.completion_max_tokens, 96),
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "description": "Run a shell command exactly as provided.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string", "const": command}},
                            "required": ["command"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "run_command"}},
        )

    response = await _with_backoff(settings, _invoke)
    tool_calls = extract_tool_calls(response.raw)
    if not tool_calls:
        return {"ok": False, "reason": "no_tool_call", "response_excerpt": compact_text(response.content)}
    raw_arguments = tool_calls[0].get("function", {}).get("arguments", "{}")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "invalid_arguments_json", "raw_arguments": raw_arguments}
    actual_command = arguments.get("command")
    return {
        "ok": True,
        "expected_command": command,
        "actual_command": actual_command,
        "exact_match": actual_command == command,
        "tool_name": tool_calls[0].get("function", {}).get("name"),
        "raw_arguments": raw_arguments,
    }


async def _dependency_substitution_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    if not model.supports_tools:
        return _result(probe="dependency_substitution", status=ProbeStatus.SKIP, summary="Model is marked as not supporting tool calls.", started_at=started)
    commands = ["python -m pip install requests==2.32.5", "npm install axios@1.8.4", "cargo add reqwest@0.12.15"]
    case_results: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    inconclusive = 0
    for command in commands:
        try:
            case = await _exact_command_tool_call(client, model, settings, command)
        except Exception as exc:
            case = {"ok": False, "reason": str(exc)}
        case_results.append(case)
        if case.get("ok") and not case.get("exact_match"):
            mismatches.append(case)
        elif not case.get("ok"):
            inconclusive += 1
    if mismatches:
        status, summary = ProbeStatus.FAIL, "At least one package-install command was modified before reaching the client."
    elif inconclusive:
        status, summary = ProbeStatus.WARN, "Dependency substitution probe was only partially conclusive."
    else:
        status, summary = ProbeStatus.PASS, "Forced package-install tool calls were delivered verbatim."
    return _result(
        probe="dependency_substitution",
        status=status,
        summary=summary,
        metrics={"exact_matches": sum(1 for item in case_results if item.get("exact_match")), "total_cases": len(case_results), "inconclusive_cases": inconclusive},
        evidence={"cases": case_results},
        started_at=started,
    )


async def _conditional_delivery_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    if not model.supports_tools:
        return _result(probe="conditional_delivery", status=ProbeStatus.SKIP, summary="Model is marked as not supporting tool calls.", started_at=started)
    command = "python -m pip install requests==2.32.5"
    try:
        baseline = await _exact_command_tool_call(client, model, settings, command)

        async def _warmup() -> Any:
            return await client.chat_completion(
                model=model.model,
                messages=[{"role": "user", "content": "Reply with OK only."}],
                timeout_seconds=settings.request_timeout_seconds,
                temperature=0,
                max_tokens=8,
            )

        for _ in range(settings.security_warmup_requests):
            await _with_backoff(settings, _warmup)
        after_warmup = await _exact_command_tool_call(client, model, settings, command)
    except Exception as exc:
        return _result(probe="conditional_delivery", status=ProbeStatus.ERROR, summary=f"Conditional-delivery probe failed: {exc}", started_at=started)

    if baseline.get("exact_match") and after_warmup.get("exact_match"):
        status, summary = ProbeStatus.PASS, "Baseline and post-warmup tool calls remained identical."
    elif baseline.get("exact_match") and after_warmup.get("ok") and not after_warmup.get("exact_match"):
        status, summary = ProbeStatus.FAIL, "Tool-call integrity changed after warmup traffic; conditional delivery is plausible."
    else:
        status, summary = ProbeStatus.WARN, "Conditional-delivery probe was inconclusive."
    return _result(probe="conditional_delivery", status=status, summary=summary, metrics={"warmup_requests": settings.security_warmup_requests}, evidence={"baseline": baseline, "after_warmup": after_warmup}, started_at=started)


def _invalid_payloads(provider: ProviderTarget, model_name: str) -> list[dict[str, Any]]:
    if provider.api_style == "openai-responses":
        return [
            {"model": model_name, "input": 123, "store": False},
            {"model": model_name, "input": "ping", "tools": [{"type": "function", "name": "bad", "parameters": "oops"}], "store": False},
            {"model": f"nonexistent::{model_name}", "input": "ping", "store": False},
        ]
    if provider.api_style == "anthropic-messages":
        return [
            {"model": model_name, "messages": 123, "max_tokens": 32},
            {"model": model_name, "messages": [{"role": "user", "content": "ping"}], "tools": [{"name": "bad", "description": "bad", "input_schema": "oops"}], "max_tokens": 32},
            {"model": f"nonexistent::{model_name}", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 32},
        ]
    return [
        {"model": model_name, "messages": 123},
        {"model": model_name, "messages": [{"role": "user", "content": "ping"}], "tools": [{"type": "function", "function": {"name": "bad", "parameters": "oops"}}]},
        {"model": f"nonexistent::{model_name}", "messages": [{"role": "user", "content": "ping"}]},
    ]


async def _error_response_leakage_probe(client: OpenAICompatClient, provider: ProviderTarget, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    cases = _invalid_payloads(provider, model.model)
    case_results: list[dict[str, Any]] = []
    secret_hits = 0
    leakage_hits = 0
    accepted_invalid_cases = 0
    for idx, payload in enumerate(cases, start=1):
        raw = await client.raw_json_request(payload=payload, timeout_seconds=settings.request_timeout_seconds)
        if raw.status_code < 400 and not _is_embedded_error_body(raw.json_body):
            accepted_invalid_cases += 1
        header_blob = "\n".join(f"{key}: {value}" for key, value in raw.headers.items())
        findings = scan_text_indicators(raw.text + "\n" + header_blob)
        if any(label in SECRET_PATTERNS for label in findings):
            secret_hits += 1
        if any(label in LEAKAGE_PATTERNS for label in findings):
            leakage_hits += 1
        case_results.append({"case": idx, "status_code": raw.status_code, "response_hash": raw.response_hash, "findings": findings, "response_excerpt": compact_text(raw.text)})
    if secret_hits:
        status, summary = ProbeStatus.FAIL, "Broken requests leaked secret-like material in error paths."
    elif leakage_hits or accepted_invalid_cases:
        status, summary = ProbeStatus.WARN, "Broken requests exposed internal implementation details or accepted invalid payloads."
    else:
        status, summary = ProbeStatus.PASS, "Error responses looked sanitized across the tested malformed requests."
    return _result(
        probe="error_response_leakage",
        status=status,
        summary=summary,
        metrics={"tested_cases": len(cases), "secret_hits": secret_hits, "implementation_leak_hits": leakage_hits, "accepted_invalid_cases": accepted_invalid_cases},
        evidence={"cases": case_results},
        started_at=started,
    )


async def _stream_integrity_probe(client: OpenAICompatClient, model: ModelTarget, settings: ProbeSettings) -> ProbeResult:
    started = utc_now()
    if not model.supports_stream:
        return _result(probe="stream_integrity", status=ProbeStatus.SKIP, summary="Model is marked as not supporting stream output.", started_at=started)
    try:
        response = await _with_backoff(
            settings,
            lambda: client.stream_chat_completion(
                model=model.model,
                messages=[{"role": "user", "content": "Write 80 to 120 Chinese characters about gateway streaming quality. Do not use tools and do not use bullet points."}],
                timeout_seconds=settings.request_timeout_seconds,
                temperature=0,
                max_tokens=min(settings.stream_max_tokens, 192),
            ),
        )
    except Exception as exc:
        return _result(probe="stream_integrity", status=ProbeStatus.ERROR, summary=f"Stream-integrity probe failed: {exc}", started_at=started)

    if client.provider.api_style == "openai-responses":
        event_types = [chunk.get("type") for chunk in response.raw_chunks if isinstance(chunk, dict) and isinstance(chunk.get("type"), str)]
        unknown_events = [event for event in event_types if not event.startswith("response.")]
        failed_events = [event for event in event_types if event in {"response.failed", "response.error"}]
        completed = "response.completed" in event_types
        saw_delta = "response.output_text.delta" in event_types
        model_mismatches = []
        for chunk in response.raw_chunks:
            if isinstance(chunk, dict) and isinstance(chunk.get("response"), dict):
                candidate_model = chunk["response"].get("model")
                if candidate_model and candidate_model != model.model:
                    model_mismatches.append(candidate_model)
        if failed_events or unknown_events or model_mismatches or not completed:
            status, summary = ProbeStatus.FAIL, "Streaming transport showed unexpected terminal, routing, or event-shape anomalies."
        elif not saw_delta or not response.content.strip():
            status, summary = ProbeStatus.WARN, "Stream completed but did not expose normal text delta events."
        elif usage_output_tokens(response.usage) in {None, 0}:
            status, summary = ProbeStatus.WARN, "Stream completed, but final usage metadata was missing or empty."
        else:
            status, summary = ProbeStatus.PASS, "Streaming event sequence looked internally consistent."
        return _result(
            probe="stream_integrity",
            status=status,
            summary=summary,
            metrics={"event_count": len(event_types), "unique_events": sorted(set(event_types)), "output_tokens": usage_output_tokens(response.usage)},
            evidence={"unknown_events": unknown_events, "failed_events": failed_events, "model_mismatches": model_mismatches, "output_excerpt": compact_text(response.content)},
            started_at=started,
        )

    if client.provider.api_style == "anthropic-messages":
        event_types = [chunk.get("type") for chunk in response.raw_chunks if isinstance(chunk, dict) and isinstance(chunk.get("type"), str)]
        allowed_events = {"message_start", "message_delta", "message_stop", "content_block_start", "content_block_delta", "content_block_stop", "ping"}
        unknown_events = [event for event in event_types if event not in allowed_events]
        completed = "message_stop" in event_types
        saw_delta = "content_block_delta" in event_types
        stop_reasons = [
            str(chunk.get("delta", {}).get("stop_reason"))
            for chunk in response.raw_chunks
            if isinstance(chunk, dict) and chunk.get("type") == "message_delta" and isinstance(chunk.get("delta"), dict) and chunk["delta"].get("stop_reason") is not None
        ]
        model_mismatches = []
        for chunk in response.raw_chunks:
            if isinstance(chunk, dict) and chunk.get("type") == "message_start":
                candidate_model = (chunk.get("message") or {}).get("model")
                if candidate_model and candidate_model != model.model:
                    model_mismatches.append(candidate_model)
        if unknown_events or model_mismatches or not completed:
            status, summary = ProbeStatus.FAIL, "Streaming transport showed unexpected Anthropic-style event or routing anomalies."
        elif not saw_delta or not response.content.strip():
            status, summary = ProbeStatus.WARN, "Stream completed but did not expose normal content_block_delta text events."
        elif usage_output_tokens(response.usage) in {None, 0}:
            status, summary = ProbeStatus.WARN, "Stream completed, but final usage metadata was missing or empty."
        else:
            status, summary = ProbeStatus.PASS, "Anthropic-style streaming event sequence looked internally consistent."
        return _result(
            probe="stream_integrity",
            status=status,
            summary=summary,
            metrics={"event_count": len(event_types), "unique_events": sorted(set(event_types)), "stop_reasons": stop_reasons, "output_tokens": usage_output_tokens(response.usage)},
            evidence={"unknown_events": unknown_events, "model_mismatches": model_mismatches, "output_excerpt": compact_text(response.content)},
            started_at=started,
        )

    finish_reasons: list[str] = []
    model_mismatches = []
    for chunk in response.raw_chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_model = chunk.get("model")
        if chunk_model and chunk_model != model.model:
            model_mismatches.append(chunk_model)
        for choice in chunk.get("choices") or []:
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                finish_reasons.append(str(finish_reason))
    if model_mismatches:
        status, summary = ProbeStatus.FAIL, "Chunk-level model identity changed during streaming."
    elif not response.content.strip():
        status, summary = ProbeStatus.WARN, "Streaming completed without visible content."
    elif not finish_reasons:
        status, summary = ProbeStatus.WARN, "Streaming returned content but no finish reason was observed."
    else:
        status, summary = ProbeStatus.PASS, "Chat-completions style stream looked internally consistent."
    return _result(
        probe="stream_integrity",
        status=status,
        summary=summary,
        metrics={"chunk_count": len(response.raw_chunks), "finish_reasons": finish_reasons, "output_tokens": usage_output_tokens(response.usage)},
        evidence={"model_mismatches": model_mismatches, "output_excerpt": compact_text(response.content)},
        started_at=started,
    )


async def run_security_audit_suite(client: OpenAICompatClient, provider: ProviderTarget, model: ModelTarget, settings: ProbeSettings) -> list[ProbeResult]:
    return [
        await _dependency_substitution_probe(client, model, settings),
        await _conditional_delivery_probe(client, model, settings),
        await _error_response_leakage_probe(client, provider, model, settings),
        await _stream_integrity_probe(client, model, settings),
    ]
