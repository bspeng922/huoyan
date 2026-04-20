from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from huoyan.utils import local_now


class ProbeStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class ProbeResult(BaseModel):
    suite: str
    probe: str
    status: ProbeStatus
    summary: str
    score: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=local_now)
    finished_at: datetime = Field(default_factory=local_now)


class ModelReport(BaseModel):
    provider_name: str
    provider_base_url: str
    model: str
    claimed_family: str
    overall_status: ProbeStatus
    summary: dict[str, int]
    settings: dict[str, Any]
    results: list[ProbeResult]


class ProviderReport(BaseModel):
    name: str
    base_url: str
    overall_status: ProbeStatus
    summary: dict[str, int]
    models: list[ModelReport]
    audit_log_entries: list[dict[str, Any]] = Field(default_factory=list)


class RunReport(BaseModel):
    generated_at: datetime = Field(default_factory=local_now)
    overall_status: ProbeStatus
    summary: dict[str, int]
    providers: list[ProviderReport]
    metadata: dict[str, Any] = Field(default_factory=dict)
    audit_log_entries: list[dict[str, Any]] = Field(default_factory=list)
