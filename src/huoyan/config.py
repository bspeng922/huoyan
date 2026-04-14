from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, SecretStr


SuiteName = Literal[
    "authenticity",
    "performance",
    "agentic",
    "cost_security",
    "security_audit",
]
APIStyle = Literal["openai-chat", "openai-responses", "anthropic-messages"]


class ProbeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_suites: list[SuiteName] = Field(
        default_factory=lambda: [
            "authenticity",
            "performance",
            "agentic",
            "cost_security",
            "security_audit",
        ]
    )
    request_timeout_seconds: PositiveFloat = 60
    completion_max_tokens: PositiveInt = 256
    stream_max_tokens: PositiveInt = 512
    performance_stream_samples: PositiveInt = 5
    performance_stream_sample_interval_seconds: float = 0.5
    concurrency_levels: list[PositiveInt] = Field(default_factory=lambda: [5])
    uptime_samples: PositiveInt = 5
    uptime_interval_seconds: float = 1.0
    long_context_target_chars: PositiveInt = 20000
    security_warmup_requests: PositiveInt = 3
    security_retry_attempts: PositiveInt = 3
    security_retry_backoff_seconds: PositiveFloat = 3.0
    multimodal_image_url: str | None = None
    multimodal_expected_answer: str | None = None


class ProbeSettingsOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_suites: list[SuiteName] | None = None
    request_timeout_seconds: PositiveFloat | None = None
    completion_max_tokens: PositiveInt | None = None
    stream_max_tokens: PositiveInt | None = None
    performance_stream_samples: PositiveInt | None = None
    performance_stream_sample_interval_seconds: float | None = None
    concurrency_levels: list[PositiveInt] | None = None
    uptime_samples: PositiveInt | None = None
    uptime_interval_seconds: float | None = None
    long_context_target_chars: PositiveInt | None = None
    security_warmup_requests: PositiveInt | None = None
    security_retry_attempts: PositiveInt | None = None
    security_retry_backoff_seconds: PositiveFloat | None = None
    multimodal_image_url: str | None = None
    multimodal_expected_answer: str | None = None


class ModelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    claimed_family: str | None = None
    supports_stream: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    settings: ProbeSettingsOverride = Field(default_factory=ProbeSettingsOverride)


class ProviderTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    base_url: str
    api_key: SecretStr
    api_style: APIStyle = "openai-chat"
    anthropic_version: str = "2023-06-01"
    default_headers: dict[str, str] = Field(default_factory=dict)
    privacy_policy_url: str | None = None
    reasoning_effort: str | None = None
    disable_response_storage: bool = True
    defaults: ProbeSettings = Field(default_factory=ProbeSettings)
    models: list[ModelTarget]


class ReportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_dir: str = "reports"
    formats: list[Literal["json", "md"]] = Field(default_factory=lambda: ["json", "md"])
    write_transparency_log: bool = True


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: list[ProviderTarget]
    report: ReportSettings = Field(default_factory=ReportSettings)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return AppConfig.model_validate(raw)


def merge_settings(base: ProbeSettings, override: ProbeSettingsOverride) -> ProbeSettings:
    merged = base.model_dump()
    merged.update(override.model_dump(exclude_none=True))
    return ProbeSettings.model_validate(merged)
