from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from huoyan.logging_utils import get_logger
from pydantic import BaseModel, ConfigDict, Field

from huoyan.utils import local_now

logger = get_logger(__name__)


JobStatus = Literal["queued", "running", "completed", "failed"]


class WebRunJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress_completed: int = 0
    progress_total: int = 0
    progress_percent: float = 0.0
    current_suite: str | None = None
    current_probe: str | None = None
    current_probe_label: str | None = None
    last_completed_probe: str | None = None
    last_completed_probe_label: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class WebRunJobStore:
    def __init__(self, output_root: str | Path):
        self.root = Path(output_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "jobs.json"
        self._lock = threading.Lock()
        self._jobs: dict[str, WebRunJob] = {}
        logger.info("Web job store initialized path=%s", self._index_path.resolve())

    def create_job(self, *, progress_total: int) -> WebRunJob:
        job = WebRunJob(
            job_id=uuid4().hex[:12],
            status="queued",
            created_at=local_now().isoformat(),
            progress_total=progress_total,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._write_jobs()
        logger.info("Created web job job_id=%s progress_total=%s", job.job_id, progress_total)
        return job

    def get_job(self, job_id: str) -> WebRunJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else job.model_copy(deep=True)

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.started_at is None:
                job.started_at = local_now().isoformat()
            job.status = "running"
            self._write_jobs()
        logger.info("Job marked running job_id=%s", job_id)

    def probe_started(self, *, job_id: str, suite: str, probe: str, probe_label: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            if job.started_at is None:
                job.started_at = local_now().isoformat()
            job.current_suite = suite
            job.current_probe = probe
            job.current_probe_label = probe_label
            self._write_jobs()
        logger.info(
            "Job probe started job_id=%s suite=%s probe=%s",
            job_id,
            suite,
            probe,
        )

    def probe_finished(self, *, job_id: str, suite: str, probe: str, probe_label: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.current_suite = suite
            job.current_probe = probe
            job.current_probe_label = probe_label
            job.last_completed_probe = probe
            job.last_completed_probe_label = probe_label
            job.progress_completed = min(job.progress_completed + 1, job.progress_total)
            job.progress_percent = self._progress_percent(job.progress_completed, job.progress_total)
            self._write_jobs()
        logger.info(
            "Job probe finished job_id=%s suite=%s probe=%s progress=%s/%s",
            job_id,
            suite,
            probe,
            job.progress_completed,
            job.progress_total,
        )

    def complete(self, *, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.finished_at = local_now().isoformat()
            job.progress_completed = job.progress_total
            job.progress_percent = 100.0 if job.progress_total else 0.0
            job.current_suite = None
            job.current_probe = None
            job.current_probe_label = None
            job.result = result
            self._write_jobs()
        logger.info("Job completed job_id=%s", job_id)

    def fail(self, *, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.finished_at = local_now().isoformat()
            job.error = error
            job.current_suite = None
            job.current_probe = None
            job.current_probe_label = None
            self._write_jobs()
        logger.error("Job failed job_id=%s error=%s", job_id, error)

    def _write_jobs(self) -> None:
        payload = [job.model_dump(mode="json") for job in self._jobs.values()]
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _progress_percent(completed: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return round((completed / total) * 100.0, 2)
