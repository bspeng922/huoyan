from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None


FAMILY_HINTS: dict[str, str] = {
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "claude": "claude",
    "gemini": "gemini",
    "glm": "glm",
    "kimi": "kimi",
    "moonshot": "kimi",
    "qwen": "qwen",
    "minimax": "minimax",
    "abab": "minimax",
    "deepseek": "deepseek",
}

DEVELOPER_HINTS: dict[str, list[str]] = {
    "openai": ["openai"],
    "claude": ["anthropic"],
    "gemini": ["google", "deepmind"],
    "glm": ["智谱", "zhipu", "bigmodel", "z.ai"],
    "kimi": ["moonshot", "月之暗面", "kimi"],
    "qwen": ["阿里", "alibaba", "通义"],
    "minimax": ["minimax"],
    "deepseek": ["deepseek", "深度求索"],
}

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "github_pat": re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    "slack_bot": re.compile(r"\bxoxb-[0-9A-Za-z-]{20,}\b"),
    "eth_private_key": re.compile(r"\b0x[a-fA-F0-9]{64}\b"),
    "pem_private_key": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
}

LEAKAGE_PATTERNS: dict[str, re.Pattern[str]] = {
    "upstream_url": re.compile(
        r"https?://[^\s\"']*(?:openai|anthropic|googleapis|bedrock|azure|vertexai)[^\s\"']*",
        re.IGNORECASE,
    ),
    "traceback": re.compile(r"(?:Traceback \(most recent call last\)|RuntimeError:|ValueError:)"),
    "filesystem_path": re.compile(r"(?:[A-Za-z]:\\[^\s\"']+|/(?:home|root|app|usr|var)/[^\s\"']+)"),
    "internal_impl": re.compile(r"\b(?:litellm|openai-python|httpx|uvicorn|fastapi|starlette)\b", re.I),
    "relay_business_internal": re.compile(
        r"(?:\bdistributor\b|分组|可用渠道|无可用渠道|渠道（?distributor）?|内部渠道|折扣分组|[一二三四五六七八九十\d\.]+折.*?模型|中转接口|代理\s*API|计费倍率|剩余额度|用尽)",
        re.IGNORECASE,
    ),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def infer_family(model_name: str, claimed_family: str | None = None) -> str:
    if claimed_family:
        return claimed_family.lower()
    lowered = model_name.lower()
    for needle, family in FAMILY_HINTS.items():
        if needle in lowered:
            return family
    return "unknown"


def developer_keywords(family: str) -> list[str]:
    return DEVELOPER_HINTS.get(family.lower(), [])


def compact_text(value: str, limit: int = 320) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def extract_json_block(text: str) -> dict[str, Any] | None:
    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE))
    for candidate in candidates:
        stripped = candidate.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS.values():
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_sensitive_data(item) for key, item in value.items()}
    return value


def scan_text_indicators(text: str) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    for label, pattern in {**SECRET_PATTERNS, **LEAKAGE_PATTERNS}.items():
        matches = pattern.findall(text)
        normalized: list[str] = []
        for match in matches[:5]:
            normalized.append("".join(str(part) for part in match) if isinstance(match, tuple) else str(match))
        if normalized:
            findings[label] = normalized
    return findings


def extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def extract_message_text(raw: dict[str, Any]) -> str:
    if "choices" in raw:
        try:
            return extract_text_content(raw["choices"][0]["message"].get("content"))
        except (KeyError, IndexError, TypeError):
            return ""
    if isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    if isinstance(raw.get("content"), list):
        return extract_text_content(raw["content"])
    parts: list[str] = []
    for item in raw.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            parts.append(extract_text_content(item.get("content")))
        elif item.get("type") in {"output_text", "text"}:
            parts.append(str(item.get("text", "")))
    return "".join(parts)


def extract_tool_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if "choices" in raw:
        try:
            tool_calls = raw["choices"][0]["message"].get("tool_calls")
        except (KeyError, IndexError, TypeError):
            return []
        return tool_calls if isinstance(tool_calls, list) else []
    if isinstance(raw.get("content"), list):
        tool_calls: list[dict[str, Any]] = []
        for item in raw["content"]:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": item.get("id"),
                        "function": {
                            "name": item.get("name"),
                            "arguments": stable_json_dumps(item.get("input", {})),
                        },
                    }
                )
        return tool_calls
    tool_calls = []
    for item in raw.get("output", []) or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            tool_calls.append(
                {
                    "id": item.get("id") or item.get("call_id"),
                    "function": {
                        "name": item.get("name"),
                        "arguments": item.get("arguments", "{}"),
                    },
                }
            )
    return tool_calls


def usage_input_tokens(usage: dict[str, Any]) -> int | None:
    return usage.get("prompt_tokens") if usage.get("prompt_tokens") is not None else usage.get("input_tokens")


def usage_output_tokens(usage: dict[str, Any]) -> int | None:
    return usage.get("completion_tokens") if usage.get("completion_tokens") is not None else usage.get("output_tokens")


def usage_reasoning_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        return details.get("reasoning_tokens")
    details = usage.get("outputTokensDetails")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        return details.get("reasoning_tokens")
    details = usage.get("output_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        return details.get("reasoning_tokens")
    return None


def _encoding_for_model(model_name: str, family: str) -> tuple[Any | None, str | None, bool]:
    if tiktoken is None:
        return None, None, False
    fallback = None
    approximate = False
    lowered = model_name.lower()
    if family == "openai":
        fallback = "o200k_base" if any(prefix in lowered for prefix in ["gpt-4o", "gpt-5", "o3", "o4"]) else "cl100k_base"
    elif family in {"deepseek", "qwen", "kimi", "glm"}:
        fallback = "cl100k_base"
        approximate = True
    try:
        return tiktoken.encoding_for_model(model_name), model_name, approximate
    except KeyError:
        if not fallback:
            return None, None, False
        return tiktoken.get_encoding(fallback), fallback, approximate


def estimate_prompt_tokens(messages: list[dict[str, Any]], model_name: str, family: str) -> dict[str, Any]:
    encoding, tokenizer_name, approximate = _encoding_for_model(model_name, family)
    if encoding is None:
        return {"supported": False, "count": None, "tokenizer": None, "approximate": False, "note": "No local tokenizer mapping for this family."}
    tokens_per_message = 3
    tokens_per_name = 1
    total = 0
    for message in messages:
        total += tokens_per_message
        total += len(encoding.encode(str(message.get("role", ""))))
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                    total += len(encoding.encode(str(item.get("text", ""))))
        else:
            total += len(encoding.encode(str(content or "")))
        if "name" in message:
            total += tokens_per_name + len(encoding.encode(str(message["name"])))
    total += 3
    return {
        "supported": True,
        "count": total,
        "tokenizer": tokenizer_name,
        "approximate": approximate,
        "note": "Approximate local chat-token estimate." if approximate else None,
    }


def estimate_text_tokens(text: str, model_name: str, family: str) -> dict[str, Any]:
    encoding, tokenizer_name, approximate = _encoding_for_model(model_name, family)
    if encoding is None:
        return {"supported": False, "count": None, "tokenizer": None, "approximate": False, "note": "No local tokenizer mapping for this family."}
    return {
        "supported": True,
        "count": len(encoding.encode(text)),
        "tokenizer": tokenizer_name,
        "approximate": approximate,
        "note": "Approximate local text-token estimate." if approximate else None,
    }
