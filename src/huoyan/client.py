from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, Field

from huoyan.config import ProviderTarget
from huoyan.utils import (
    extract_message_text,
    redact_sensitive_data,
    redact_sensitive_text,
    sha256_text,
    stable_json_dumps,
)


class ChatResponse(BaseModel):
    content: str
    raw: dict[str, Any]
    usage: dict[str, Any] = Field(default_factory=dict)
    status_code: int
    elapsed_seconds: float
    headers: dict[str, str] = Field(default_factory=dict)


class StreamResponse(BaseModel):
    content: str
    raw_chunks: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    status_code: int
    elapsed_seconds: float
    ttft_seconds: float | None = None
    first_content_seconds: float | None = None
    generation_seconds: float | None = None
    content_event_offsets_seconds: list[float] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)


class RawHTTPResponse(BaseModel):
    status_code: int
    elapsed_seconds: float
    headers: dict[str, str] = Field(default_factory=dict)
    text: str = ""
    json_body: dict[str, Any] | list[Any] | None = None
    response_hash: str | None = None


class OpenAICompatClient:
    def __init__(self, provider: ProviderTarget):
        self.provider = provider
        self._client: httpx.AsyncClient | None = None
        self.audit_log_entries: list[dict[str, Any]] = []

    async def __aenter__(self) -> "OpenAICompatClient":
        self._client = httpx.AsyncClient(timeout=None)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def endpoint(self) -> str:
        base = self.provider.base_url.rstrip("/")
        if self.provider.api_style == "openai-chat":
            if base.endswith("/chat/completions"):
                return base
            if base.endswith("/v1"):
                return base + "/chat/completions"
            return base + "/v1/chat/completions"
        if self.provider.api_style == "openai-responses":
            if base.endswith("/responses"):
                return base
            if base.endswith("/v1"):
                return base + "/responses"
            return base + "/v1/responses"
        if base.endswith("/v1/messages"):
            return base
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.provider.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        if self.provider.api_style == "anthropic-messages":
            headers["anthropic-version"] = self.provider.anthropic_version
        headers.update(self.provider.default_headers)
        return headers

    def _record_audit_log(
        self,
        *,
        request_body: dict[str, Any],
        response_body: Any,
        response_hash: str,
        status_code: int,
        elapsed_seconds: float,
        mode: str,
        model: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.audit_log_entries.append(
            {
                "provider": self.provider.name,
                "endpoint": self.endpoint,
                "api_style": self.provider.api_style,
                "mode": mode,
                "model": model,
                "status_code": status_code,
                "elapsed_seconds": round(elapsed_seconds, 6),
                "request_body": redact_sensitive_data(request_body),
                "response_body": redact_sensitive_data(response_body),
                "response_hash": response_hash,
                "response_headers": redact_sensitive_data(headers or {}),
            }
        )

    def _raise_for_embedded_error(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return

        error = raw.get("error")
        if isinstance(error, dict) and error.get("message"):
            message = str(error.get("message"))
            code = error.get("code")
            raise RuntimeError(f"Embedded API error: {code} {message}".strip())

        base_resp = raw.get("base_resp")
        if isinstance(base_resp, dict):
            status_code = base_resp.get("status_code")
            if status_code not in {None, 0, 200, "0", "200"}:
                status_msg = str(base_resp.get("status_msg", "")).strip()
                raise RuntimeError(f"Embedded upstream error: {status_code} {status_msg}".strip())

    def _split_system_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        system_parts: list[str] = []
        remaining: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system":
                content = message.get("content")
                if isinstance(content, str):
                    system_parts.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            system_parts.append(str(item.get("text", "")))
            else:
                remaining.append(message)
        return system_parts, remaining

    def _convert_messages_for_responses(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                converted.append({"role": message.get("role", "user"), "content": content})
                continue

            new_content: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    new_content.append({"type": "input_text", "text": item.get("text", "")})
                elif item.get("type") == "image_url":
                    image_url = item.get("image_url")
                    if isinstance(image_url, dict):
                        image_url = image_url.get("url")
                    new_content.append({"type": "input_image", "image_url": image_url})
            converted.append({"role": message.get("role", "user"), "content": new_content})
        return converted

    def _convert_messages_for_anthropic(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts, remaining = self._split_system_messages(messages)
        converted: list[dict[str, Any]] = []
        for message in remaining:
            role = str(message.get("role", "user"))
            content = message.get("content")
            if isinstance(content, str):
                converted.append({"role": role, "content": content})
                continue

            if isinstance(content, list):
                blocks: list[dict[str, Any]] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type == "text":
                        blocks.append({"type": "text", "text": item.get("text", "")})
                converted.append({"role": role, "content": blocks})
                continue

            converted.append({"role": role, "content": str(content or "")})

        system = "\n\n".join(part for part in system_parts if part).strip() or None
        return system, converted

    def _convert_tools_for_responses(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                converted.append(tool)
                continue
            function = tool.get("function", {})
            converted.append(
                {
                    "type": "function",
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters", {}),
                    "strict": True,
                }
            )
        return converted

    def _convert_tools_for_anthropic(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            function = tool.get("function", {})
            converted.append(
                {
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters", {}),
                }
            )
        return converted

    def _convert_tool_choice_for_responses(self, tool_choice: Any) -> Any:
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            function = tool_choice.get("function", {})
            if "name" in function:
                return {"type": "function", "name": function["name"]}
        return tool_choice

    def _convert_tool_choice_for_anthropic(self, tool_choice: Any) -> Any:
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            function = tool_choice.get("function", {})
            if "name" in function:
                return {"type": "tool", "name": function["name"]}
        return tool_choice

    def _build_payload(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        payload_extra = dict(extra)
        max_tokens = payload_extra.pop("max_tokens", None)
        tools = payload_extra.pop("tools", None)
        tool_choice = payload_extra.pop("tool_choice", None)
        stream_options = payload_extra.pop("stream_options", None)

        if self.provider.api_style == "openai-chat":
            payload = {
                "model": model,
                "messages": messages,
                "stream": stream,
                **payload_extra,
            }
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if tools is not None:
                payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            if stream_options is not None:
                payload["stream_options"] = stream_options
            return payload

        if self.provider.api_style == "openai-responses":
            payload = {
                "model": model,
                "input": self._convert_messages_for_responses(messages),
                "stream": stream,
                **payload_extra,
            }
            if max_tokens is not None:
                payload["max_output_tokens"] = max_tokens
            if tools is not None:
                payload["tools"] = self._convert_tools_for_responses(tools)
            if tool_choice is not None:
                payload["tool_choice"] = self._convert_tool_choice_for_responses(tool_choice)
            if self.provider.reasoning_effort:
                payload["reasoning"] = {"effort": self.provider.reasoning_effort}
            if self.provider.disable_response_storage:
                payload["store"] = False
            return payload

        system, anthropic_messages = self._convert_messages_for_anthropic(messages)
        payload = {
            "model": model,
            "messages": anthropic_messages,
            "stream": stream,
            **payload_extra,
        }
        if system:
            payload["system"] = system
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools is not None:
            payload["tools"] = self._convert_tools_for_anthropic(tools)
        if tool_choice is not None:
            payload["tool_choice"] = self._convert_tool_choice_for_anthropic(tool_choice)
        return payload

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        timeout_seconds: float,
        **extra: Any,
    ) -> ChatResponse:
        if self._client is None:
            raise RuntimeError("Client is not initialized.")

        payload = self._build_payload(model=model, messages=messages, stream=False, extra=extra)
        started = perf_counter()
        response = await self._client.post(
            self.endpoint,
            headers=self.headers,
            json=payload,
            timeout=timeout_seconds,
        )
        elapsed = perf_counter() - started
        response.raise_for_status()
        raw = response.json()
        self._raise_for_embedded_error(raw)
        usage = raw.get("usage", {}) or {}
        self._record_audit_log(
            request_body=payload,
            response_body=raw,
            response_hash=sha256_text(response.content),
            status_code=response.status_code,
            elapsed_seconds=elapsed,
            mode="chat_completion",
            model=model,
            headers=dict(response.headers),
        )
        return ChatResponse(
            content=extract_message_text(raw),
            raw=raw,
            usage=usage,
            status_code=response.status_code,
            elapsed_seconds=elapsed,
            headers=dict(response.headers),
        )

    async def stream_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        timeout_seconds: float,
        **extra: Any,
    ) -> StreamResponse:
        if self._client is None:
            raise RuntimeError("Client is not initialized.")

        payload = self._build_payload(model=model, messages=messages, stream=True, extra=extra)
        started = perf_counter()
        output_parts: list[str] = []
        raw_chunks: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        ttft_seconds: float | None = None
        first_content_seconds: float | None = None
        content_event_offsets_seconds: list[float] = []
        status_code = 200
        headers: dict[str, str] = {}
        pending_event: str | None = None

        async with self._client.stream(
            "POST",
            self.endpoint,
            headers=self.headers,
            json=payload,
            timeout=timeout_seconds,
        ) as response:
            status_code = response.status_code
            headers = dict(response.headers)
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    pending_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    break

                chunk = json.loads(payload_text)
                self._raise_for_embedded_error(chunk)
                if pending_event and "type" not in chunk:
                    chunk["type"] = pending_event
                raw_chunks.append(chunk)

                if self.provider.api_style == "openai-chat":
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta", {})
                    delta_content = delta.get("content")
                    delta_reasoning = delta.get("reasoning_content")

                    if isinstance(delta_reasoning, str) and delta_reasoning and ttft_seconds is None:
                        ttft_seconds = perf_counter() - started

                    if isinstance(delta_content, str):
                        if delta_content and ttft_seconds is None:
                            ttft_seconds = perf_counter() - started
                        if delta_content and first_content_seconds is None:
                            first_content_seconds = perf_counter() - started
                        if delta_content:
                            content_event_offsets_seconds.append(perf_counter() - started)
                        output_parts.append(delta_content)
                    elif isinstance(delta_content, list):
                        joined = "".join(
                            str(item.get("text", ""))
                            for item in delta_content
                            if isinstance(item, dict) and item.get("type") == "text"
                        )
                        if joined and ttft_seconds is None:
                            ttft_seconds = perf_counter() - started
                        if joined and first_content_seconds is None:
                            first_content_seconds = perf_counter() - started
                        if joined:
                            content_event_offsets_seconds.append(perf_counter() - started)
                        output_parts.append(joined)
                    if chunk.get("usage"):
                        usage = chunk["usage"]

                elif self.provider.api_style == "openai-responses":
                    event_type = chunk.get("type") or pending_event
                    delta_text = None
                    if event_type == "response.output_text.delta":
                        delta_text = chunk.get("delta", "")
                    elif event_type == "response.completed":
                        response_obj = chunk.get("response", {}) or {}
                        usage = response_obj.get("usage", {}) or chunk.get("usage", {}) or {}
                    if isinstance(delta_text, str) and delta_text:
                        if ttft_seconds is None:
                            ttft_seconds = perf_counter() - started
                        if first_content_seconds is None:
                            first_content_seconds = perf_counter() - started
                        content_event_offsets_seconds.append(perf_counter() - started)
                        output_parts.append(delta_text)

                else:
                    event_type = chunk.get("type") or pending_event
                    if event_type == "content_block_delta":
                        delta = chunk.get("delta", {}) or {}
                        if delta.get("type") == "text_delta":
                            text = str(delta.get("text", ""))
                            if text and ttft_seconds is None:
                                ttft_seconds = perf_counter() - started
                            if text and first_content_seconds is None:
                                first_content_seconds = perf_counter() - started
                            if text:
                                content_event_offsets_seconds.append(perf_counter() - started)
                            output_parts.append(text)
                    elif event_type == "message_delta":
                        usage = chunk.get("usage", {}) or usage
                    elif event_type == "message_start":
                        message = chunk.get("message", {}) or {}
                        usage = message.get("usage", {}) or usage

                pending_event = None

        elapsed = perf_counter() - started
        generation_seconds = None
        if first_content_seconds is not None:
            generation_seconds = max(elapsed - first_content_seconds, 0.0)
        elif ttft_seconds is not None:
            generation_seconds = max(elapsed - ttft_seconds, 0.0)

        self._record_audit_log(
            request_body=payload,
            response_body={"chunks": raw_chunks, "content": "".join(output_parts), "usage": usage},
            response_hash=sha256_text(stable_json_dumps(raw_chunks)),
            status_code=status_code,
            elapsed_seconds=elapsed,
            mode="stream_chat_completion",
            model=model,
            headers=headers,
        )
        return StreamResponse(
            content="".join(output_parts),
            raw_chunks=raw_chunks,
            usage=usage,
            status_code=status_code,
            elapsed_seconds=elapsed,
            ttft_seconds=ttft_seconds,
            first_content_seconds=first_content_seconds,
            generation_seconds=generation_seconds,
            content_event_offsets_seconds=content_event_offsets_seconds,
            headers=headers,
        )

    async def raw_json_request(
        self,
        *,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> RawHTTPResponse:
        if self._client is None:
            raise RuntimeError("Client is not initialized.")

        started = perf_counter()
        response = await self._client.post(
            self.endpoint,
            headers=self.headers,
            json=payload,
            timeout=timeout_seconds,
        )
        elapsed = perf_counter() - started
        response_text = response.text
        json_body: dict[str, Any] | list[Any] | None = None
        try:
            json_body = response.json()
        except Exception:
            json_body = None

        response_hash = sha256_text(response.content)
        self._record_audit_log(
            request_body=payload,
            response_body=json_body if json_body is not None else redact_sensitive_text(response_text),
            response_hash=response_hash,
            status_code=response.status_code,
            elapsed_seconds=elapsed,
            mode="raw_json_request",
            model=str(payload.get("model", "")),
            headers=dict(response.headers),
        )
        return RawHTTPResponse(
            status_code=response.status_code,
            elapsed_seconds=elapsed,
            headers=dict(response.headers),
            text=redact_sensitive_text(response_text),
            json_body=json_body,
            response_hash=response_hash,
        )
