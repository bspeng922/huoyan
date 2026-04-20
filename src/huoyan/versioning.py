from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from huoyan import __version__


REPORT_SCHEMA_VERSION = "2"
SCORE_VERSION = "2026-04-16.scorecards-v1"
PROBE_VERSIONS: dict[str, str] = {
    "capability_fingerprint": "2",
    "acrostic_constraints": "1",
    "boundary_reasoning": "1",
    "identity": "1",
    "linguistic_fingerprint": "1",
    "response_consistency": "2",
    "ttft_tps": "2",
    "concurrency": "1",
    "availability": "2",
    "tool_calling": "1",
    "multi_turn_tool": "2",
    "long_context_integrity": "2",
    "multimodal_support": "2",
    "token_alignment": "1",
    "tls_baseline": "1",
    "security_headers": "1",
    "rate_limit_transparency": "2",
    "privacy_policy": "1",
    "dependency_substitution": "1",
    "conditional_delivery": "1",
    "error_response_leakage": "1",
    "stream_integrity": "1",
    "system_prompt_injection": "2",
    "capability_score": "1",
    "protocol_score": "1",
    "security_score": "1",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    output = completed.stdout.strip()
    return output or None


def build_runtime_metadata() -> dict[str, Any]:
    git_commit = _run_git("rev-parse", "--short=12", "HEAD")
    git_branch = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    git_status = _run_git("status", "--porcelain")
    return {
        "app_version": __version__,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "score_version": SCORE_VERSION,
        "probe_versions": PROBE_VERSIONS,
        "git_commit": git_commit,
        "git_branch": git_branch,
        "git_dirty": bool(git_status),
    }
