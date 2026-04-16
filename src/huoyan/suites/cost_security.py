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
from huoyan.utils import estimate_prompt_tokens, infer_family, usage_input_tokens, utc_now


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
        started_at=started_at or utc_now(),
        finished_at=utc_now(),
    )


async def _token_alignment_probe(
    client: OpenAICompatClient,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = utc_now()
    messages = [
        {
            "role": "user",
            "content": (
                "请阅读下面固定文本，然后只回答 OK：\n"
                "弱智吧问题、文言文、Rust 生命周期、SQL JSON Path、藏头诗与并发压测会混在一起，"
                "目的是观测 token 统计是否与本地估算对齐。"
            ),
        }
    ]
    family = infer_family(model.model, model.claimed_family)
    local = estimate_prompt_tokens(messages, model.model, family)
    try:
        response = await client.chat_completion(
            model=model.model,
            messages=messages,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=0,
            max_tokens=8,
        )
    except Exception as exc:
        return _result(
            probe="token_alignment",
            status=ProbeStatus.ERROR,
            summary=f"Token-alignment probe failed: {exc}",
            started_at=started,
        )

    api_tokens = usage_input_tokens(response.usage)
    if api_tokens is None:
        return _result(
            probe="token_alignment",
            status=ProbeStatus.WARN,
            summary="API did not return input/prompt token usage.",
            metrics={"local_estimate": local},
            started_at=started,
        )
    if not local["supported"] or local["count"] is None:
        return _result(
            probe="token_alignment",
            status=ProbeStatus.SKIP,
            summary="No reliable local tokenizer mapping is available for this model family.",
            metrics={"api_prompt_tokens": api_tokens, "local_estimate": local},
            started_at=started,
        )

    delta = api_tokens - local["count"]
    ratio = api_tokens / local["count"] if local["count"] else None
    if local["approximate"]:
        if ratio is not None and 0.5 <= ratio <= 1.5:
            status = ProbeStatus.SKIP
            summary = "Local tokenizer mapping for this model family is approximate only; recorded the ratio for reference but excluded it from scoring."
        else:
            status = ProbeStatus.WARN
            summary = "API token usage diverges strongly even under an approximate local tokenizer mapping."
    else:
        if ratio is not None and 0.9 <= ratio <= 1.1:
            status = ProbeStatus.PASS
            summary = "API token usage aligns with the local tokenizer estimate."
        elif ratio is not None and 0.75 <= ratio <= 1.25:
            status = ProbeStatus.WARN
            summary = "API token usage is somewhat offset from the local tokenizer estimate."
        else:
            status = ProbeStatus.FAIL
            summary = "API token usage is materially offset from the local tokenizer estimate."

    return _result(
        probe="token_alignment",
        status=status,
        summary=summary,
        metrics={
            "api_prompt_tokens": api_tokens,
            "local_prompt_tokens": local["count"],
            "delta_tokens": delta,
            "ratio": round(ratio, 4) if ratio is not None else None,
            "tokenizer": local["tokenizer"],
            "approximate": local["approximate"],
        },
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
    started = utc_now()
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
    started = utc_now()
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


async def _rate_limit_transparency_probe(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    settings: ProbeSettings,
) -> ProbeResult:
    started = utc_now()
    observations: list[dict[str, Any]] = []
    saw_429 = False
    saw_headers = False
    saw_headers_on_429 = False
    saw_retry_after_on_429 = False
    for attempt in range(3):
        payload = _build_valid_minimal_payload(provider, model.model)
        raw = await client.raw_json_request(payload=payload, timeout_seconds=settings.request_timeout_seconds)
        rate_headers = _extract_rate_limit_headers(raw.headers)
        if rate_headers:
            saw_headers = True
        if raw.status_code == 429:
            saw_429 = True
            if rate_headers:
                saw_headers_on_429 = True
            if "retry-after" in rate_headers:
                saw_retry_after_on_429 = True
        observations.append(
            {
                "attempt": attempt + 1,
                "status_code": raw.status_code,
                "rate_limit_headers": rate_headers,
                "response_excerpt": raw.text[:240],
            }
        )
        await asyncio.sleep(0.2)

    if saw_429 and not saw_headers_on_429:
        status = ProbeStatus.FAIL
        summary = "Rate limiting occurred but the 429 responses did not expose Retry-After or x-ratelimit-* metadata."
    elif saw_429 and saw_headers_on_429:
        status = ProbeStatus.PASS
        summary = "Rate limiting was triggered and the 429 responses exposed rate-limit metadata."
    elif saw_headers:
        status = ProbeStatus.PASS
        summary = "Observed proactive rate-limit metadata in non-429 responses."
    else:
        status = ProbeStatus.SKIP
        summary = "Rate limiting was not triggered and no proactive metadata was observed, so transparency could not be evaluated."

    return _result(
        probe="rate_limit_transparency",
        status=status,
        summary=summary,
        metrics={
            "sampled_requests": len(observations),
            "saw_429": saw_429,
            "saw_rate_limit_headers": saw_headers,
            "saw_rate_limit_headers_on_429": saw_headers_on_429,
            "saw_retry_after_on_429": saw_retry_after_on_429,
        },
        evidence={"observations": observations},
        started_at=started,
    )


async def _security_headers_probe(provider: ProviderTarget) -> ProbeResult:
    started = utc_now()
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
) -> list[ProbeResult]:
    return [
        await _token_alignment_probe(client, model, settings),
        await _tls_probe(provider),
        await _security_headers_probe(provider),
        await _rate_limit_transparency_probe(client, provider, model, settings),
        await _privacy_policy_probe(provider),
    ]
