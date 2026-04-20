from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from huoyan.logging_utils import get_logger
from pydantic import BaseModel, ConfigDict, Field

from huoyan.models import RunReport
from huoyan.utils import local_now

logger = get_logger(__name__)


class WebRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    generated_at: str
    base_url: str
    model: str
    api_style: str
    key_hint: str
    overall_status: str
    summary: dict[str, int] = Field(default_factory=dict)
    focus_metrics: list[dict[str, Any]] = Field(default_factory=list)
    export_files: dict[str, str] = Field(default_factory=dict)


class WebHistoryStore:
    def __init__(self, output_root: str | Path):
        self.root = Path(output_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "history.json"
        self._runs_dir = self.root / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        logger.info("Web history store initialized root=%s", self.root.resolve())

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def create_run_dir(self, run_id: str) -> Path:
        run_dir = self._runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created web run directory run_id=%s path=%s", run_id, run_dir.resolve())
        return run_dir

    def list_records(self) -> list[WebRunRecord]:
        with self._lock:
            records = self._load_records()
        logger.debug("Loaded web history records count=%s", len(records))
        return [record for record in records if self._has_report(record)]

    def get_record(self, run_id: str) -> WebRunRecord | None:
        records = self.list_records()
        for record in records:
            if record.run_id == run_id:
                return record
        return None

    def save_record(
        self,
        *,
        report: RunReport,
        base_url: str,
        model: str,
        api_style: str,
        api_key: str,
        export_files: dict[str, Path],
        focus_metrics: list[dict[str, Any]],
    ) -> WebRunRecord:
        record = WebRunRecord(
            run_id=self._resolve_run_id(export_files),
            created_at=local_now().isoformat(),
            generated_at=report.generated_at.isoformat(),
            base_url=base_url,
            model=model,
            api_style=api_style,
            key_hint=self._mask_key(api_key),
            overall_status=report.overall_status.value,
            summary=report.summary,
            focus_metrics=focus_metrics,
            export_files={
                fmt: str(path.resolve().relative_to(self.root.resolve()))
                for fmt, path in export_files.items()
            },
        )

        with self._lock:
            records = self._load_records()
            records = [item for item in records if item.run_id != record.run_id]
            records.insert(0, record)
            self._write_records(records)
        logger.info(
            "Saved web history record run_id=%s model=%s base_url=%s",
            record.run_id,
            record.model,
            record.base_url,
        )
        return record

    def export_path(self, record: WebRunRecord, fmt: str) -> Path | None:
        relative = record.export_files.get(fmt)
        if not relative:
            return None
        path = self.root / relative
        return path if path.exists() else None

    def load_report(self, record: WebRunRecord) -> RunReport:
        json_path = self.export_path(record, "json")
        if json_path is None:
            raise FileNotFoundError(f"Missing JSON export for run {record.run_id}")
        logger.info("Loading web report run_id=%s path=%s", record.run_id, json_path.resolve())
        return RunReport.model_validate_json(json_path.read_text(encoding="utf-8"))

    def _load_records(self) -> list[WebRunRecord]:
        if not self._index_path.exists():
            return []
        raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        records: list[WebRunRecord] = []
        for item in raw:
            try:
                records.append(WebRunRecord.model_validate(item))
            except Exception:
                continue
        return records

    def _write_records(self, records: list[WebRunRecord]) -> None:
        payload = [record.model_dump(mode="json") for record in records]
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Web history index updated path=%s count=%s", self._index_path.resolve(), len(records))

    def _has_report(self, record: WebRunRecord) -> bool:
        return self.export_path(record, "json") is not None

    def _resolve_run_id(self, export_files: dict[str, Path]) -> str:
        first_path = next(iter(export_files.values()), None)
        if first_path is not None:
            run_dir = first_path.resolve().parent
            if run_dir.parent == self.runs_dir.resolve():
                return run_dir.name
        return uuid4().hex[:12]

    @staticmethod
    def new_run_id() -> str:
        return uuid4().hex[:12]

    @staticmethod
    def _mask_key(api_key: str) -> str:
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "*" * len(api_key)
        return f"{api_key[:4]}...{api_key[-4:]}"
