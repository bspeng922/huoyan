from __future__ import annotations

import asyncio
import socket
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProbeSettings, ProviderTarget
from huoyan.models import ProbeResult, ProbeStatus
from huoyan.progress import ProgressCallback, run_probe_sequence
from huoyan.utils import (
    estimate_prompt_tokens,
    estimate_text_tokens,
    infer_family,
    local_now,
    usage_input_tokens,
    usage_output_tokens,
)


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
        suite="cost_security",
        probe=probe,
        status=status,
        summary=summary,
        metrics=metrics or {},
        evidence=evidence or {},
        started_at=started_at or local_now(),
        finished_at=local_now(),
    )


async def _token_alignment_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = local_now()
    expected_output = "HX_TOKEN_OUTPUT_BASELINE_20260416_ALPHA_BETA_31415926"
    messages = [
        {
            "role": "user",
            "content": (
                "请阅读下面固定文本，然后只原样输出指定字符串，不要解释，不要引号，不要代码块：\n"
                "弱智吧问题、文言文、Rust 生命周期、SQL JSON Path、藏头诗与并发压测会混在一起，"
                "目的是观测 token 统计是否与本地估算对齐。\n"
                f"请只输出：{expected_output}"
            ),
        }
    ]
    family = infer_family(model.model, model.claimed_family)
    local_prompt = estimate_prompt_tokens(messages, model.model, family)
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=messages,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=32,
        )
    except Exception as exc:
        return _result(
            probe="token_alignment",
            status=ProbeStatus.ERROR,
            summary=f"Token-alignment probe failed: {exc}",
            started_at=started,
        )

    api_prompt_tokens = usage_input_tokens(response.usage)
    api_output_tokens = usage_output_tokens(response.usage)
    if api_prompt_tokens is None:
        return _result(
            probe="token_alignment",
            status=ProbeStatus.WARN,
            summary="API did not return input/prompt token usage, so usage-token alignment could not be checked on the prompt side.",
            metrics={"local_prompt_estimate": local_prompt},
            started_at=started,
        )
    if not local_prompt["supported"] or local_prompt["count"] is None:
        return _result(
            probe="token_alignment",
            status=ProbeStatus.SKIP,
            summary="No reliable local tokenizer mapping is available for this model family, so usage-token alignment is recorded but not scored.",
            metrics={"api_prompt_tokens": api_prompt_tokens, "local_prompt_estimate": local_prompt},
            started_at=started,
        )

    returned_output = response.content.strip()
    output_exact_match = returned_output == expected_output
    local_output = estimate_text_tokens(
        expected_output if output_exact_match else returned_output,
        model.model,
        family,
    )

    prompt_delta = api_prompt_tokens - local_prompt["count"]
    prompt_ratio = api_prompt_tokens / local_prompt["count"] if local_prompt["count"] else None
    output_delta = None
    output_ratio = None
    if api_output_tokens is not None and local_output["count"]:
        output_delta = api_output_tokens - local_output["count"]
        output_ratio = api_output_tokens / local_output["count"]

    approximate = bool(local_prompt["approximate"] or local_output["approximate"])
    if approximate:
        wide_band_ok = True
        for ratio in [prompt_ratio, output_ratio]:
            if ratio is not None and not (0.5 <= ratio <= 1.5):
                wide_band_ok = False
                break
        if wide_band_ok:
            status = ProbeStatus.SKIP
            summary = "Local tokenizer mapping for this model family is approximate only; prompt/output usage-token ratios are recorded for reference and excluded from scoring."
        else:
            status = ProbeStatus.WARN
            summary = "API usage-token counts diverge strongly even under an approximate local tokenizer mapping."
    else:
        prompt_ok = prompt_ratio is not None and 0.9 <= prompt_ratio <= 1.1
        prompt_warn = prompt_ratio is not None and 0.75 <= prompt_ratio <= 1.25
        output_ok = output_ratio is not None and 0.9 <= output_ratio <= 1.1
        output_warn = output_ratio is not None and 0.75 <= output_ratio <= 1.25

        if api_output_tokens is None:
            status = ProbeStatus.WARN
            summary = "API returned prompt token usage, but completion/output token usage was missing."
        elif not output_exact_match:
            status = ProbeStatus.WARN
            summary = "Model did not echo the expected output exactly, so the output-side usage-token comparison is only partially reliable."
        elif prompt_ok and output_ok:
            status = ProbeStatus.PASS
            summary = "Prompt and echoed-output usage-token counts align with the local tokenizer estimates."
        elif prompt_warn and output_warn:
            status = ProbeStatus.WARN
            summary = "Prompt or echoed-output usage-token counts are somewhat offset from the local tokenizer estimates."
        else:
            status = ProbeStatus.FAIL
            summary = "Prompt or echoed-output usage-token counts are materially offset from the local tokenizer estimates."

    return _result(
        probe="token_alignment",
        status=status,
        summary=summary,
        metrics={
            "api_prompt_tokens": api_prompt_tokens,
            "local_prompt_tokens": local_prompt["count"],
            "prompt_delta_tokens": prompt_delta,
            "prompt_ratio": round(prompt_ratio, 4) if prompt_ratio is not None else None,
            "api_output_tokens": api_output_tokens,
            "local_output_tokens": local_output["count"],
            "output_delta_tokens": output_delta,
            "output_ratio": round(output_ratio, 4) if output_ratio is not None else None,
            "output_exact_match": output_exact_match,
            "delta_tokens": prompt_delta,
            "ratio": round(prompt_ratio, 4) if prompt_ratio is not None else None,
            "tokenizer": local_prompt["tokenizer"],
            "approximate": approximate,
            "alignment_scope": "api_usage_vs_local_estimate",
            "billing_audit": False,
            "billing_caveat": "This probe compares API usage-token counts with local estimates. It is not a billing multiplier audit.",
        },
        evidence={"expected_output": expected_output, "actual_output": returned_output},
        started_at=started,
    )


def _tls_inspect(base_url: str) -> dict[str, Any]:
    parsed = urlparse(base_url)
    if parsed.scheme.lower() != "https":
        return {"ok": False, "reason": "Base URL is not HTTPS."}

    hostname = parsed.hostname
    port = parsed.port or 443
    if not hostname:
        return {"ok": False, "reason": "Base URL does not include a hostname."}

    context = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=10) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as secure_socket:
            cert = secure_socket.getpeercert()
            expires = cert.get("notAfter")
            expires_at = None
            expires_in_days = None
            if expires:
                expires_at = datetime.strptime(expires, "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=timezone.utc
                )
                expires_in_days = (expires_at - datetime.now(timezone.utc)).days
            return {
                "ok": True,
                "tls_version": secure_socket.version(),
                "cipher": secure_socket.cipher()[0] if secure_socket.cipher() else None,
                "issuer": cert.get("issuer"),
                "subject": cert.get("subject"),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "expires_in_days": expires_in_days,
            }


async def _tls_probe(provider: ProviderTarget) -> ProbeResult:
    started = local_now()
    try:
        inspection = await asyncio.to_thread(_tls_inspect, provider.base_url)
    except Exception as exc:
        return _result(
            probe="tls_baseline",
            status=ProbeStatus.ERROR,
            summary=f"TLS inspection failed: {exc}",
            started_at=started,
        )

    if not inspection.get("ok"):
        return _result(
            probe="tls_baseline",
            status=ProbeStatus.FAIL,
            summary=str(inspection.get("reason")),
            evidence=inspection,
            started_at=started,
        )

    expires_in_days = inspection.get("expires_in_days")
    tls_version = inspection.get("tls_version") or ""
    if tls_version in {"TLSv1", "TLSv1.1"}:
        status = ProbeStatus.FAIL
        summary = f"Insecure TLS version detected: {tls_version}."
    elif expires_in_days is not None and expires_in_days < 14:
        status = ProbeStatus.WARN
        summary = f"TLS certificate expires soon: {expires_in_days} days left."
    else:
        status = ProbeStatus.PASS
        summary = f"TLS baseline looks healthy with {tls_version}."

    return _result(
        probe="tls_baseline",
        status=status,
        summary=summary,
        metrics={
            "tls_version": tls_version,
            "cipher": inspection.get("cipher"),
            "expires_in_days": expires_in_days,
        },
        evidence={
            "issuer": inspection.get("issuer"),
            "subject": inspection.get("subject"),
            "expires_at": inspection.get("expires_at"),
        },
        started_at=started,
    )


async def _privacy_policy_probe(provider: ProviderTarget) -> ProbeResult:
    started = local_now()
    if provider.privacy_policy_url:
        return _result(
            probe="privacy_policy",
            status=ProbeStatus.PASS,
            summary="Privacy policy URL is configured. Manual review is still required.",
            evidence={"privacy_policy_url": provider.privacy_policy_url},
            started_at=started,
        )
    return _result(
        probe="privacy_policy",
        status=ProbeStatus.WARN,
        summary="No privacy policy URL was configured for this provider.",
        started_at=started,
    )


def _build_valid_minimal_payload(provider: ProviderTarget, model_name: str) -> dict[str, Any]:
    if provider.api_style == "openai-responses":
        payload: dict[str, Any] = {
            "model": model_name,
            "input": "ping",
            "max_output_tokens": 8,
            "store": False,
        }
        if provider.reasoning_effort:
            payload["reasoning"] = {"effort": provider.reasoning_effort}
        return payload
    if provider.api_style == "anthropic-messages":
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "stream": False,
        }
    return {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
    }


def _extract_rate_limit_headers(headers: dict[str, Any]) -> dict[str, str]:
    interesting = [
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    ]
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    return {key: normalized[key] for key in interesting if key in normalized}


async def _rate_limit_observation(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model_name: str,
    timeout_seconds: float,
    *,
    phase: str,
    attempt: int,
) -> dict[str, Any]:
    payload = _build_valid_minimal_payload(provider, model_name)
    raw = await client.raw_json_request(payload=payload, timeout_seconds=timeout_seconds)
    return {
        "phase": phase,
        "attempt": attempt,
        "status_code": raw.status_code,
        "rate_limit_headers": _extract_rate_limit_headers(raw.headers),
        "response_excerpt": raw.text[:240],
    }


async def _rate_limit_transparency_probe(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = local_now()
    passive_observations: list[dict[str, Any]] = []
    active_observations: list[dict[str, Any]] = []
    saw_429 = False
    saw_headers = False
    saw_headers_on_429 = False
    saw_retry_after_on_429 = False
    for attempt in range(2):
        observation = await _rate_limit_observation(
            client,
            provider,
            model.model,
            settings.request_timeout_seconds,
            phase="passive",
            attempt=attempt + 1,
        )
        rate_headers = observation["rate_limit_headers"]
        if rate_headers:
            saw_headers = True
        if observation["status_code"] == 429:
            saw_429 = True
            if rate_headers:
                saw_headers_on_429 = True
            if "retry-after" in rate_headers:
                saw_retry_after_on_429 = True
        passive_observations.append(observation)
        await asyncio.sleep(0.2)

    burst_size = max(6, max(settings.concurrency_levels or [6]))
    burst_tasks = [
        asyncio.create_task(
            _rate_limit_observation(
                client,
                provider,
                model.model,
                settings.request_timeout_seconds,
                phase="active_burst",
                attempt=index + 1,
            )
        )
        for index in range(burst_size)
    ]
    for observation in await asyncio.gather(*burst_tasks):
        rate_headers = observation["rate_limit_headers"]
        if rate_headers:
            saw_headers = True
        if observation["status_code"] == 429:
            saw_429 = True
            if rate_headers:
                saw_headers_on_429 = True
            if "retry-after" in rate_headers:
                saw_retry_after_on_429 = True
        active_observations.append(observation)

    if saw_429 and not saw_headers_on_429:
        status = ProbeStatus.FAIL
        summary = "Active or passive sampling hit rate limiting, but the 429 responses did not expose Retry-After or x-ratelimit-* metadata."
    elif saw_429 and saw_headers_on_429:
        status = ProbeStatus.PASS
        summary = "Passive or active sampling triggered rate limiting, and the 429 responses exposed rate-limit metadata."
    elif saw_headers:
        status = ProbeStatus.PASS
        summary = "Observed proactive rate-limit metadata during passive or active sampling."
    else:
        status = ProbeStatus.SKIP
        summary = "Neither passive sampling nor the active burst exposed rate-limit metadata, so transparency could not be evaluated."

    return _result(
        probe="rate_limit_transparency",
        status=status,
        summary=summary,
        metrics={
            "sampled_requests": len(passive_observations) + len(active_observations),
            "passive_sample_count": len(passive_observations),
            "active_burst_size": burst_size,
            "saw_429": saw_429,
            "saw_rate_limit_headers": saw_headers,
            "saw_rate_limit_headers_on_429": saw_headers_on_429,
            "saw_retry_after_on_429": saw_retry_after_on_429,
        },
        evidence={"passive_observations": passive_observations, "active_observations": active_observations},
        started_at=started,
    )


async def _security_headers_probe(provider: ProviderTarget) -> ProbeResult:
    started = local_now()
    parsed = urlparse(provider.base_url)
    if parsed.scheme.lower() != "https":
        return _result(
            probe="security_headers",
            status=ProbeStatus.FAIL,
            summary="Base URL is not HTTPS, so HTTP security headers are not meaningful.",
            started_at=started,
        )

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.get(provider.base_url, headers=provider.default_headers)
    except Exception as exc:
        return _result(
            probe="security_headers",
            status=ProbeStatus.ERROR,
            summary=f"Security-headers probe failed: {exc}",
            started_at=started,
        )

    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
    observed = {
        "strict-transport-security": headers.get("strict-transport-security"),
        "x-content-type-options": headers.get("x-content-type-options"),
    }
    hsts = observed["strict-transport-security"] is not None
    xcto = observed["x-content-type-options"] is not None
    if hsts and xcto:
        status = ProbeStatus.PASS
        summary = "Core API-relevant HTTP security headers are present."
    elif response.status_code in {404, 405}:
        status = ProbeStatus.SKIP
        summary = f"Sampled API endpoint returned HTTP {response.status_code}; header coverage is inconclusive for a pure API surface."
    elif hsts or xcto:
        status = ProbeStatus.WARN
        summary = "Some API-relevant HTTP security headers are present, but the core set is incomplete."
    else:
        status = ProbeStatus.WARN
        summary = "No API-relevant HTTP security headers were observed on the sampled endpoint."

    return _result(
        probe="security_headers",
        status=status,
        summary=summary,
        metrics={"http_status": response.status_code, "header_count_checked": len(observed)},
        evidence=observed,
        started_at=started,
    )


async def run_cost_security_suite(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
    progress_callback: ProgressCallback | None = None,
) -> list[ProbeResult]:
    return await run_probe_sequence(
        suite="cost_security",
        progress_callback=progress_callback,
        steps=[
            ("token_alignment", lambda: _token_alignment_probe(client, model, settings)),
            ("tls_baseline", lambda: _tls_probe(provider)),
            ("security_headers", lambda: _security_headers_probe(provider)),
            (
                "rate_limit_transparency",
                lambda: _rate_limit_transparency_probe(client, provider, model, settings),
            ),
            ("privacy_policy", lambda: _privacy_policy_probe(provider)),
        ],
    )
