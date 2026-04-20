from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from huoyan.config import APIStyle, AppConfig, ModelTarget, ProbeSettings, ProviderTarget, SuiteName
from huoyan.logging_utils import configure_logging, get_logger
from huoyan.models import ModelReport, ProbeResult, RunReport
from huoyan.reporting import write_report
from huoyan.runner import expected_run_result_count, run_app
from huoyan.web_jobs import WebRunJob, WebRunJobStore
from huoyan.web_store import WebHistoryStore, WebRunRecord


DEFAULT_SUITES: list[SuiteName] = [
    "authenticity",
    "performance",
    "agentic",
    "cost_security",
    "security_audit",
]

STATUS_LABELS = {
    "pass": "通过",
    "warn": "警告",
    "fail": "失败",
    "skip": "跳过",
    "error": "错误",
}

SUITE_LABELS = {
    "scorecard": "评分卡",
    "authenticity": "真实性与路由",
    "performance": "性能与稳定性",
    "agentic": "工具链路与上下文",
    "cost_security": "计量与入口安全",
    "security_audit": "中转安全审计",
}

PROBE_LABELS = {
    "capability_score": "能力评分",
    "protocol_score": "协议评分",
    "security_score": "安全评分",
    "identity": "身份自报",
    "capability_fingerprint": "能力侧写",
    "acrostic_constraints": "约束跟随",
    "boundary_reasoning": "边界推理",
    "linguistic_fingerprint": "多语言理解",
    "response_consistency": "一致性抽样",
    "ttft_tps": "TTFT 与吞吐",
    "concurrency": "并发稳定性",
    "availability": "可用性抽样",
    "tool_calling": "单轮工具调用",
    "multi_turn_tool": "多轮工具链路",
    "long_context_integrity": "长上下文完整性",
    "multimodal_support": "多模态支持",
    "token_alignment": "Usage Token 对齐",
    "tls_baseline": "TLS 基线",
    "security_headers": "安全响应头",
    "rate_limit_transparency": "限流透明度",
    "privacy_policy": "隐私策略记录",
    "dependency_substitution": "安装命令完整性",
    "conditional_delivery": "条件投递抽样",
    "error_response_leakage": "错误响应泄漏",
    "stream_integrity": "流式协议完整性",
    "system_prompt_injection": "系统提示注入披露",
}

PROBE_ORDER = [
    "capability_score",
    "protocol_score",
    "security_score",
    "identity",
    "capability_fingerprint",
    "acrostic_constraints",
    "boundary_reasoning",
    "linguistic_fingerprint",
    "response_consistency",
    "ttft_tps",
    "concurrency",
    "availability",
    "tool_calling",
    "multi_turn_tool",
    "long_context_integrity",
    "multimodal_support",
    "token_alignment",
    "tls_baseline",
    "security_headers",
    "rate_limit_transparency",
    "privacy_policy",
    "dependency_substitution",
    "conditional_delivery",
    "error_response_leakage",
    "stream_integrity",
    "system_prompt_injection",
]

FOCUS_PROBES = [
    "capability_score",
    "protocol_score",
    "security_score",
    "ttft_tps",
    "concurrency",
    "availability",
    "token_alignment",
    "dependency_substitution",
    "stream_integrity",
]

logger = get_logger(__name__)


class WebRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    api_style: APIStyle | None = None
    claimed_family: str | None = None
    supports_stream: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    enabled_suites: list[SuiteName] = Field(default_factory=lambda: list(DEFAULT_SUITES))


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=2, max_length=8)


def create_app(output_dir: str | Path = "reports") -> FastAPI:
    store = WebHistoryStore(Path(output_dir) / "web")
    job_store = WebRunJobStore(Path(output_dir) / "web")
    app = FastAPI(title="Huoyan Web Console")
    logger.info("Web application created output_dir=%s", Path(output_dir).resolve())

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/history", response_class=HTMLResponse)
    async def history_page() -> HTMLResponse:
        return HTMLResponse(HISTORY_HTML)

    @app.get("/api/history")
    async def history() -> dict[str, Any]:
        logger.info("History list requested")
        return {"records": [_serialize_record(record) for record in store.list_records()]}

    @app.get("/api/history/{run_id}")
    async def history_detail(run_id: str) -> dict[str, Any]:
        logger.info("History detail requested run_id=%s", run_id)
        record = _require_record(store, run_id)
        report = _load_report(store, record)
        return {
            "record": _serialize_record(record),
            "report": _serialize_report(report),
        }

    @app.get("/api/history/{run_id}/export/{fmt}")
    async def export_file(run_id: str, fmt: str) -> FileResponse:
        logger.info("Export requested run_id=%s format=%s", run_id, fmt)
        record = _require_record(store, run_id)
        path = store.export_path(record, fmt)
        if path is None:
            raise HTTPException(status_code=404, detail=f"未找到 {fmt} 导出文件")
        media_type = {
            "json": "application/json",
            "md": "text/markdown; charset=utf-8",
            "ndjson": "application/x-ndjson",
        }.get(fmt, "application/octet-stream")
        return FileResponse(path, filename=path.name, media_type=media_type)

    @app.post("/api/run/start")
    async def start_run(request: WebRunRequest) -> dict[str, Any]:
        payload = _normalize_run_request(request)
        config = _build_config(payload, store.runs_dir / "pending")
        job = job_store.create_job(progress_total=expected_run_result_count(config))
        logger.info(
            "Web async run created job_id=%s model=%s base_url=%s expected_results=%s",
            job.job_id,
            payload.model,
            payload.base_url,
            job.progress_total,
        )
        asyncio.create_task(
            _run_web_job(
                job=job,
                request=payload,
                store=store,
                job_store=job_store,
            )
        )
        return {"job": _serialize_job(job)}

    @app.get("/api/run/jobs/{job_id}")
    async def job_status(job_id: str) -> dict[str, Any]:
        logger.debug("Job status requested job_id=%s", job_id)
        job = _require_job(job_store, job_id)
        return {"job": _serialize_job(job)}

    @app.post("/api/run")
    async def run_once(request: WebRunRequest) -> dict[str, Any]:
        payload = request.model_copy(
            update={
                "base_url": request.base_url.strip(),
                "model": request.model.strip(),
                "claimed_family": request.claimed_family.strip() if request.claimed_family else None,
            }
        )
        if not payload.enabled_suites:
            raise HTTPException(status_code=400, detail="至少选择一个测试套件")

        payload = payload.model_copy(update={"api_style": payload.api_style or _infer_api_style(payload.base_url)})

        payload = _normalize_run_request(request)
        run_id = store.new_run_id()
        run_dir = store.create_run_dir(run_id)
        config = _build_config(payload, run_dir)
        logger.info(
            "Web direct run started run_id=%s model=%s base_url=%s",
            run_id,
            payload.model,
            payload.base_url,
        )

        try:
            report = await run_app(config)
        except Exception as exc:  # pragma: no cover - depends on remote APIs
            logger.exception("Web direct run failed run_id=%s model=%s", run_id, payload.model)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        written = write_report(
            report,
            str(run_dir),
            ["json", "md"],
            write_transparency_log=True,
        )
        focus_metrics = _focus_metrics(_primary_model(report))
        record = store.save_record(
            report=report,
            base_url=payload.base_url,
            model=payload.model,
            api_style=payload.api_style,
            api_key=payload.api_key,
            export_files=written,
            focus_metrics=focus_metrics,
        )
        logger.info(
            "Web direct run completed run_id=%s model=%s overall_status=%s",
            run_id,
            payload.model,
            report.overall_status.value,
        )
        return {
            "record": _serialize_record(record),
            "report": _serialize_report(report),
        }

    @app.post("/api/compare")
    async def compare_runs(request: CompareRequest) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(request.ids))
        if len(unique_ids) < 2:
            raise HTTPException(status_code=400, detail="至少选择两条测试记录")

        logger.info("Compare requested run_ids=%s", ",".join(unique_ids))
        records: list[WebRunRecord] = []
        reports: list[RunReport] = []
        for run_id in unique_ids:
            record = _require_record(store, run_id)
            records.append(record)
            reports.append(_load_report(store, record))

        return _build_comparison(records, reports)

    return app


def _normalize_run_request(request: WebRunRequest) -> WebRunRequest:
    payload = request.model_copy(
        update={
            "base_url": request.base_url.strip(),
            "model": request.model.strip(),
            "claimed_family": request.claimed_family.strip() if request.claimed_family else None,
        }
    )
    if not payload.enabled_suites:
        raise HTTPException(status_code=400, detail="至少选择一个测试套件")
    normalized = payload.model_copy(update={"api_style": payload.api_style or _infer_api_style(payload.base_url)})
    logger.info(
        "Normalized web request model=%s base_url=%s api_style=%s suites=%s",
        normalized.model,
        normalized.base_url,
        normalized.api_style,
        ",".join(normalized.enabled_suites),
    )
    return normalized


def _require_job(job_store: WebRunJobStore, job_id: str) -> WebRunJob:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="未找到对应运行任务")
    return job


def _serialize_job(job: WebRunJob) -> dict[str, Any]:
    payload = job.model_dump(mode="json")
    if job.result is not None:
        payload["result"] = job.result
    return payload


async def _run_web_job(
    *,
    job: WebRunJob,
    request: WebRunRequest,
    store: WebHistoryStore,
    job_store: WebRunJobStore,
) -> None:
    job_store.mark_running(job.job_id)
    run_dir = store.create_run_dir(job.job_id)
    config = _build_config(request, run_dir)
    logger.info(
        "Web job execution started job_id=%s model=%s base_url=%s",
        job.job_id,
        request.model,
        request.base_url,
    )

    async def on_progress(event: dict[str, Any]) -> None:
        probe = str(event.get("probe", ""))
        suite = str(event.get("suite", ""))
        probe_label = _probe_label(probe)
        if event.get("type") == "probe_started":
            job_store.probe_started(
                job_id=job.job_id,
                suite=suite,
                probe=probe,
                probe_label=probe_label,
            )
        elif event.get("type") == "probe_finished":
            job_store.probe_finished(
                job_id=job.job_id,
                suite=suite,
                probe=probe,
                probe_label=probe_label,
            )

    try:
        report = await run_app(config, progress_callback=on_progress)
        written = write_report(
            report,
            str(run_dir),
            ["json", "md"],
            write_transparency_log=True,
        )
        focus_metrics = _focus_metrics(_primary_model(report))
        record = store.save_record(
            report=report,
            base_url=request.base_url,
            model=request.model,
            api_style=str(request.api_style),
            api_key=request.api_key,
            export_files=written,
            focus_metrics=focus_metrics,
        )
        job_store.complete(
            job_id=job.job_id,
            result={
                "record": _serialize_record(record),
                "report": _serialize_report(report),
            },
        )
        logger.info(
            "Web job execution completed job_id=%s model=%s overall_status=%s",
            job.job_id,
            request.model,
            report.overall_status.value,
        )
    except Exception as exc:  # pragma: no cover - depends on remote APIs
        logger.exception("Web job execution failed job_id=%s model=%s", job.job_id, request.model)
        job_store.fail(job_id=job.job_id, error=str(exc))


def run_server(host: str = "127.0.0.1", port: int = 8000, output_dir: str | Path = "reports") -> None:
    configure_logging()
    logger.info(
        "Launching uvicorn host=%s port=%s output_dir=%s",
        host,
        port,
        Path(output_dir).resolve(),
    )
    uvicorn.run(create_app(output_dir), host=host, port=port)


def _infer_api_style(base_url: str) -> APIStyle:
    normalized = base_url.strip().lower().rstrip("/")
    if normalized.endswith("/responses") or "/responses" in normalized:
        return "openai-responses"
    if normalized.endswith("/messages") or "/messages" in normalized:
        return "anthropic-messages"
    return "openai-chat"


def _build_config(request: WebRunRequest, run_dir: Path) -> AppConfig:
    provider = ProviderTarget(
        name="web-console",
        base_url=request.base_url,
        api_key=request.api_key,
        api_style=request.api_style,
        defaults=ProbeSettings(enabled_suites=list(request.enabled_suites)),
        models=[
            ModelTarget(
                model=request.model,
                claimed_family=request.claimed_family,
                supports_stream=request.supports_stream,
                supports_tools=request.supports_tools,
                supports_vision=request.supports_vision,
            )
        ],
    )
    return AppConfig(
        providers=[provider],
        report={
            "output_dir": str(run_dir),
            "formats": ["json", "md"],
            "write_transparency_log": True,
        },
    )


def _require_record(store: WebHistoryStore, run_id: str) -> WebRunRecord:
    record = store.get_record(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到对应测试记录")
    return record


def _load_report(store: WebHistoryStore, record: WebRunRecord) -> RunReport:
    try:
        return store.load_report(record)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="测试记录对应的 JSON 导出已不存在") from exc


def _serialize_record(record: WebRunRecord) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    payload["status_label"] = _status_label(record.overall_status)
    payload["download_urls"] = {
        fmt: f"/api/history/{record.run_id}/export/{fmt}"
        for fmt in record.export_files
    }
    return payload


def _serialize_report(report: RunReport) -> dict[str, Any]:
    model = _primary_model(report)
    provider = report.providers[0]
    return {
        "generated_at": report.generated_at.isoformat(),
        "overall_status": report.overall_status.value,
        "overall_status_label": _status_label(report.overall_status.value),
        "summary": report.summary,
        "provider": {
            "name": provider.name,
            "base_url": provider.base_url,
            "overall_status": provider.overall_status.value,
            "overall_status_label": _status_label(provider.overall_status.value),
            "summary": provider.summary,
        },
        "model": {
            "name": model.model,
            "claimed_family": model.claimed_family,
            "overall_status": model.overall_status.value,
            "overall_status_label": _status_label(model.overall_status.value),
            "summary": model.summary,
            "focus_cards": _focus_metrics(model),
            "suites": _serialize_suites(model),
        },
    }


def _serialize_suites(model: ModelReport) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in model.results:
        grouped.setdefault(result.suite, []).append(_serialize_probe(result))
    suites: list[dict[str, Any]] = []
    for suite_name, probes in grouped.items():
        probes.sort(key=lambda item: _probe_sort_key(item["probe"]))
        suites.append(
            {
                "suite": suite_name,
                "label": _suite_label(suite_name),
                "probes": probes,
            }
        )
    suites.sort(key=lambda item: _suite_sort_key(item["suite"]))
    return suites


def _serialize_probe(result: ProbeResult) -> dict[str, Any]:
    return {
        "suite": result.suite,
        "suite_label": _suite_label(result.suite),
        "probe": result.probe,
        "label": _probe_label(result.probe),
        "status": result.status.value,
        "status_label": _status_label(result.status.value),
        "summary": result.summary,
        "value": _probe_value(result),
        "metrics": result.metrics,
        "evidence": result.evidence,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
    }


def _focus_metrics(model: ModelReport) -> list[dict[str, Any]]:
    focus_map = {probe: index for index, probe in enumerate(FOCUS_PROBES)}
    cards = [
        _serialize_probe(result)
        for result in model.results
        if result.probe in focus_map
    ]
    cards.sort(key=lambda item: focus_map.get(item["probe"], len(focus_map)))
    return cards[:6]


def _build_comparison(records: list[WebRunRecord], reports: list[RunReport]) -> dict[str, Any]:
    models = [_primary_model(report) for report in reports]
    probe_maps = [{result.probe: result for result in model.results} for model in models]

    ordered_probes: list[str] = []
    for probe in PROBE_ORDER:
        if any(probe in probe_map for probe_map in probe_maps):
            ordered_probes.append(probe)
    extras = sorted(
        {
            probe
            for probe_map in probe_maps
            for probe in probe_map
            if probe not in ordered_probes
        }
    )
    ordered_probes.extend(extras)

    rows: list[dict[str, Any]] = []
    for probe in ordered_probes:
        present = [probe_map.get(probe) for probe_map in probe_maps]
        if not any(present):
            continue
        suite_name = next(result.suite for result in present if result is not None)
        row = {
            "probe": probe,
            "label": _probe_label(probe),
            "suite": suite_name,
            "suite_label": _suite_label(suite_name),
            "cells": [],
        }
        for result in present:
            if result is None:
                row["cells"].append(
                    {
                        "status": "skip",
                        "status_label": _status_label("skip"),
                        "value": "-",
                        "summary": "该条记录不包含此指标",
                    }
                )
                continue
            row["cells"].append(
                {
                    "status": result.status.value,
                    "status_label": _status_label(result.status.value),
                    "value": _probe_value(result),
                    "summary": result.summary,
                }
            )
        rows.append(row)

    return {
        "runs": [_serialize_record(record) for record in records],
        "rows": rows,
    }


def _primary_model(report: RunReport) -> ModelReport:
    if not report.providers or not report.providers[0].models:
        raise HTTPException(status_code=500, detail="报告中没有可展示的模型结果")
    return report.providers[0].models[0]


def _probe_value(result: ProbeResult) -> str:
    metrics = result.metrics

    if result.probe in {"capability_score", "protocol_score", "security_score"}:
        score = metrics.get("score")
        grade = metrics.get("grade")
        if score is None:
            return str(grade or "-")
        if grade:
            return f"{_fmt(score)} / 100 · {grade}"
        return f"{_fmt(score)} / 100"

    if result.probe == "ttft_tps":
        ttft = _fmt(metrics.get("ttft_seconds"))
        tps = _fmt(metrics.get("output_token_throughput_per_second"))
        return f"TTFT {ttft}s · {tps} tok/s"

    if result.probe == "concurrency":
        levels = metrics.get("levels", [])
        if isinstance(levels, list) and levels:
            worst = min(levels, key=lambda item: float(item.get("success_rate", 1.0)))
            success_rate = _fmt_pct(worst.get("success_rate"))
            concurrency = worst.get("concurrency", "-")
            return f"并发 {concurrency} · 成功率 {success_rate}"
        return "-"

    if result.probe == "availability":
        return _fmt_pct(metrics.get("availability_ratio"))

    if result.probe == "token_alignment":
        prompt_ratio = _fmt(metrics.get("prompt_ratio", metrics.get("ratio")), digits=4)
        output_ratio = _fmt(metrics.get("output_ratio"), digits=4)
        return f"输入 {prompt_ratio} · 输出 {output_ratio}"

    if result.probe == "dependency_substitution":
        return _fmt_ratio(metrics.get("exact_matches"), metrics.get("total_cases"))

    if result.probe == "stream_integrity":
        return str(metrics.get("event_count", metrics.get("chunk_count", "-")))

    if result.probe == "tool_calling":
        return f"{metrics.get('tool_call_count', 0)} 次"

    if result.probe == "multi_turn_tool":
        return _fmt_ratio(metrics.get("matched_fields"), metrics.get("required_fields"))

    if result.probe == "long_context_integrity":
        return str(metrics.get("max_preserved_target_chars", "-"))

    if result.probe == "tls_baseline":
        return str(metrics.get("tls_version", "-"))

    if result.probe == "rate_limit_transparency":
        return f"{metrics.get('sampled_requests', '-') } 次采样"

    if result.probe == "error_response_leakage":
        return (
            f"secret {metrics.get('secret_hits', 0)} · "
            f"accepted {metrics.get('accepted_invalid_cases', 0)}"
        )

    if result.probe == "identity":
        excerpt = result.evidence.get("response_excerpt")
        return _compact(excerpt) if excerpt else "-"

    if result.probe == "capability_fingerprint":
        return _fmt_ratio(metrics.get("correct_count"), metrics.get("total_challenges"))

    if result.probe == "acrostic_constraints":
        return _fmt_ratio(metrics.get("valid_lines"), 4)

    if result.probe == "boundary_reasoning":
        return _fmt_ratio(metrics.get("matched_lines"), metrics.get("expected_lines"))

    if result.probe == "linguistic_fingerprint":
        return _fmt_ratio(metrics.get("signal_hits"), 3)

    if result.probe == "response_consistency":
        return _fmt_pct(metrics.get("average_anchor_coverage"))

    return "-"


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _suite_label(name: str) -> str:
    return SUITE_LABELS.get(name, name)


def _probe_label(name: str) -> str:
    return PROBE_LABELS.get(name, name)


def _suite_sort_key(name: str) -> int:
    order = ["scorecard", "authenticity", "performance", "agentic", "cost_security", "security_audit"]
    try:
        return order.index(name)
    except ValueError:
        return len(order)


def _probe_sort_key(name: str) -> int:
    try:
        return PROBE_ORDER.index(name)
    except ValueError:
        return len(PROBE_ORDER)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.1f}%"


def _fmt_ratio(left: Any, right: Any) -> str:
    if left is None or right in {None, 0}:
        return "-"
    return f"{left}/{right}"


def _compact(value: Any, limit: int = 60) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Huoyan Web Console</title>
  <style>
    :root {
      --bg: #06070b;
      --bg-soft: rgba(255, 255, 255, 0.04);
      --bg-strong: rgba(255, 255, 255, 0.08);
      --line: rgba(255, 255, 255, 0.12);
      --line-strong: rgba(255, 122, 69, 0.45);
      --text: #f6f1e9;
      --muted: rgba(246, 241, 233, 0.68);
      --accent: #ff7a45;
      --accent-soft: rgba(255, 122, 69, 0.14);
      --pass: #62d6a6;
      --warn: #f3c86f;
      --fail: #ff6b63;
      --skip: #8f96a3;
      --error: #ff4d8b;
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
      --radius: 22px;
      --mono: "Cascadia Code", "JetBrains Mono", "Fira Code", monospace;
      --sans: "Aptos", "Microsoft YaHei UI", "PingFang SC", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      min-height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
    }

    body::before,
    body::after {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: -2;
    }

    body::before {
      background:
        radial-gradient(circle at 14% 16%, rgba(255, 122, 69, 0.28), transparent 28%),
        radial-gradient(circle at 84% 22%, rgba(255, 180, 124, 0.14), transparent 20%),
        radial-gradient(circle at 72% 82%, rgba(255, 122, 69, 0.12), transparent 22%),
        linear-gradient(135deg, #05070a 0%, #0b1018 46%, #13161c 100%);
    }

    body::after {
      z-index: -1;
      opacity: 0.24;
      background-image:
        linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
      background-size: 42px 42px;
      mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.94), transparent 96%);
    }

    .page {
      width: min(1320px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }

    .hero {
      position: relative;
      overflow: hidden;
      min-height: min(64vh, 620px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 34px;
      padding: clamp(32px, 5vw, 56px);
      display: grid;
      align-items: end;
      gap: 32px;
      background:
        linear-gradient(150deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.015)),
        linear-gradient(135deg, rgba(255, 122, 69, 0.12), transparent 44%);
      box-shadow: var(--shadow);
      isolation: isolate;
    }

    .hero::before {
      content: "";
      position: absolute;
      inset: 12% -14% auto auto;
      width: min(42vw, 520px);
      aspect-ratio: 1;
      border-radius: 50%;
      background:
        radial-gradient(circle, rgba(255, 122, 69, 0.92) 0%, rgba(255, 122, 69, 0.16) 28%, transparent 64%);
      filter: blur(14px);
      opacity: 0.9;
      transform: rotate(12deg);
      z-index: -1;
    }

    .hero::after {
      content: "";
      position: absolute;
      inset: auto -6% 14% auto;
      width: min(36vw, 480px);
      height: 2px;
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.88), transparent);
      box-shadow: 0 0 24px rgba(255, 255, 255, 0.35);
      transform: rotate(-13deg);
      opacity: 0.7;
      z-index: -1;
    }

    .eyebrow {
      margin: 0 0 12px;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.56);
    }

    .hero h1 {
      margin: 0;
      max-width: 8.5ch;
      font-size: clamp(48px, 8vw, 112px);
      line-height: 0.92;
      letter-spacing: -0.06em;
    }

    .hero p {
      margin: 18px 0 0;
      max-width: 660px;
      font-size: clamp(16px, 2vw, 20px);
      line-height: 1.72;
      color: var(--muted);
    }

    .hero-status {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      width: fit-content;
      padding: 12px 18px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.14);
      background: rgba(0, 0, 0, 0.24);
      backdrop-filter: blur(18px);
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    .hero-status::before {
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 0 0 rgba(255, 122, 69, 0.5);
      animation: pulse 1.6s infinite;
    }

    .workspace {
      margin-top: 26px;
      display: grid;
      gap: 20px;
    }

    .grid-top {
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.7fr);
      gap: 20px;
    }

    .surface {
      position: relative;
      overflow: hidden;
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.02)),
        rgba(0, 0, 0, 0.22);
      box-shadow: var(--shadow);
    }

    .surface::before {
      content: "";
      position: absolute;
      inset: 0 auto auto 0;
      width: 160px;
      height: 1px;
      background: linear-gradient(90deg, var(--accent), transparent);
      opacity: 0.75;
    }

    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 24px 24px 0;
    }

    .panel-head h2,
    .panel-head h3 {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.68);
    }

    .status-dot {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.84);
    }

    .status-dot::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--skip);
      box-shadow: 0 0 0 0 rgba(143, 150, 163, 0.4);
    }

    .status-dot.running::before {
      background: var(--accent);
      box-shadow: 0 0 0 0 rgba(255, 122, 69, 0.5);
      animation: pulse 1.4s infinite;
    }

    .status-dot.done::before {
      background: var(--pass);
      animation: none;
    }

    .status-dot.error::before {
      background: var(--fail);
      animation: none;
    }

    form {
      padding: 18px 24px 24px;
      display: grid;
      gap: 18px;
    }

    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }

    .field-grid .full {
      grid-column: 1 / -1;
    }

    label {
      display: grid;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
    }

    input,
    select {
      width: 100%;
      padding: 14px 16px;
      border-radius: 14px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
      font: inherit;
      outline: none;
      transition: border-color 180ms ease, transform 180ms ease, background 180ms ease;
    }

    input:focus,
    select:focus {
      border-color: var(--line-strong);
      background: rgba(255, 255, 255, 0.06);
      transform: translateY(-1px);
    }

    .chip-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .chip {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.04);
      cursor: pointer;
      user-select: none;
      transition: border-color 180ms ease, background 180ms ease, transform 180ms ease;
    }

    .chip:hover {
      border-color: rgba(255, 122, 69, 0.36);
      transform: translateY(-1px);
    }

    .chip input {
      width: 16px;
      height: 16px;
      margin: 0;
      accent-color: var(--accent);
    }

    .toggles {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .switch {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
    }

    .switch input {
      width: 18px;
      height: 18px;
      margin: 0;
      accent-color: var(--accent);
    }

    .form-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
      padding-top: 4px;
    }

    .hint {
      margin: 0;
      color: rgba(246, 241, 233, 0.52);
      font-size: 13px;
      line-height: 1.7;
    }

    button,
    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 48px;
      padding: 0 18px;
      border: none;
      border-radius: 999px;
      background: linear-gradient(135deg, #ff8f57, #ff6a2b);
      color: #160b05;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      transition: transform 180ms ease, box-shadow 180ms ease, opacity 180ms ease;
      box-shadow: 0 14px 32px rgba(255, 122, 69, 0.28);
    }

    button:hover,
    .button-link:hover {
      transform: translateY(-1px);
      box-shadow: 0 18px 34px rgba(255, 122, 69, 0.34);
    }

    button:disabled {
      opacity: 0.55;
      cursor: wait;
      transform: none;
      box-shadow: none;
    }

    .ghost-button {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text);
      box-shadow: none;
      border: 1px solid rgba(255, 255, 255, 0.14);
    }

    .ghost-button:hover {
      box-shadow: none;
      border-color: rgba(255, 122, 69, 0.4);
    }

    .protocol-note {
      padding: 0 24px 24px;
      display: grid;
      gap: 18px;
      align-content: start;
    }

    .protocol-note h3 {
      margin: 0;
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.02;
      letter-spacing: -0.04em;
    }

    .protocol-note p {
      margin: 0;
      font-size: 15px;
      line-height: 1.8;
      color: var(--muted);
    }

    .signal-list {
      display: grid;
      gap: 12px;
      margin-top: 8px;
    }

    .signal-item {
      display: grid;
      gap: 6px;
      padding: 16px 0;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
    }

    .signal-item:first-child {
      border-top: none;
    }

    .signal-item strong {
      font-family: var(--mono);
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .rail {
      display: grid;
      gap: 20px;
    }

    .result-shell,
    .history-shell,
    .compare-shell {
      padding: 0 24px 24px;
    }

    .empty-state {
      padding: 22px 0 0;
      color: rgba(246, 241, 233, 0.56);
      line-height: 1.8;
    }

    .result-head {
      padding-top: 22px;
      display: grid;
      gap: 16px;
    }

    .result-title {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }

    .result-title h3 {
      margin: 0;
      font-size: clamp(24px, 4vw, 38px);
      line-height: 1.05;
      letter-spacing: -0.04em;
    }

    .meta-line {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }

    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .status-pill,
    .mini-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(255, 255, 255, 0.05);
      font-size: 12px;
      line-height: 1;
      white-space: nowrap;
    }

    .status-pill.pass { color: var(--pass); border-color: rgba(98, 214, 166, 0.26); }
    .status-pill.warn { color: var(--warn); border-color: rgba(243, 200, 111, 0.26); }
    .status-pill.fail { color: var(--fail); border-color: rgba(255, 107, 99, 0.26); }
    .status-pill.skip { color: var(--skip); border-color: rgba(143, 150, 163, 0.26); }
    .status-pill.error { color: var(--error); border-color: rgba(255, 77, 139, 0.26); }

    .download-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .download-row a {
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      padding: 0 14px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: var(--text);
      text-decoration: none;
      background: rgba(255, 255, 255, 0.04);
      transition: border-color 180ms ease, transform 180ms ease;
    }

    .download-row a:hover {
      transform: translateY(-1px);
      border-color: rgba(255, 122, 69, 0.44);
    }

    .metric-strip {
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 18px;
      overflow: hidden;
    }

    .metric-cell {
      min-height: 112px;
      padding: 18px 18px 16px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.03), transparent);
      border-right: 1px solid rgba(255, 255, 255, 0.08);
      display: grid;
      align-content: start;
      gap: 8px;
    }

    .metric-cell:last-child {
      border-right: none;
    }

    .metric-cell strong {
      font-size: 13px;
      color: rgba(246, 241, 233, 0.72);
    }

    .metric-cell span {
      font-family: var(--mono);
      font-size: 18px;
      line-height: 1.4;
    }

    .metric-cell em {
      font-style: normal;
      color: rgba(246, 241, 233, 0.52);
      font-size: 12px;
      line-height: 1.6;
    }

    .suite-stack {
      margin-top: 22px;
      display: grid;
      gap: 18px;
    }

    .suite-block {
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 18px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.02);
    }

    .suite-head {
      padding: 16px 18px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }

    .suite-head h4 {
      margin: 0;
      font-size: 14px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.76);
    }

    .probe-list {
      display: grid;
    }

    .probe-row {
      padding: 16px 18px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      display: grid;
      gap: 12px;
    }

    .probe-row:first-child {
      border-top: none;
    }

    .probe-top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
    }

    .probe-top strong {
      font-size: 15px;
    }

    .probe-value {
      font-family: var(--mono);
      font-size: 13px;
      color: rgba(246, 241, 233, 0.72);
    }

    .probe-summary {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.8;
    }

    details {
      border-radius: 14px;
      background: rgba(0, 0, 0, 0.24);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }

    summary {
      cursor: pointer;
      padding: 12px 14px;
      font-size: 13px;
      color: rgba(246, 241, 233, 0.76);
      user-select: none;
    }

    pre {
      margin: 0;
      padding: 0 14px 14px;
      overflow: auto;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.68;
      color: #fbe7d8;
    }

    .history-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      padding: 18px 24px 0;
    }

    .history-actions p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }

    .history-table-wrap,
    .compare-table-wrap {
      padding-top: 18px;
      overflow: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }

    th,
    td {
      text-align: left;
      padding: 14px 12px;
      vertical-align: top;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      font-size: 13px;
      line-height: 1.7;
    }

    th {
      color: rgba(246, 241, 233, 0.58);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    tr.active-row td {
      background: rgba(255, 122, 69, 0.06);
    }

    .history-open {
      padding: 0;
      min-height: auto;
      border: none;
      background: none;
      box-shadow: none;
      color: var(--text);
      font-size: 15px;
      font-weight: 700;
    }

    .history-open:hover {
      transform: none;
      color: #ffb58f;
    }

    .record-meta {
      display: grid;
      gap: 4px;
      margin-top: 4px;
      color: rgba(246, 241, 233, 0.5);
      font-size: 12px;
      line-height: 1.6;
    }

    .summary-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .summary-pills span {
      padding: 6px 9px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.05);
      font-family: var(--mono);
      font-size: 11px;
    }

    .focus-list {
      display: grid;
      gap: 8px;
    }

    .focus-item {
      color: rgba(246, 241, 233, 0.72);
      font-size: 12px;
    }

    .compare-cell {
      min-width: 200px;
      display: grid;
      gap: 8px;
    }

    .compare-cell p {
      margin: 0;
      color: rgba(246, 241, 233, 0.58);
      font-size: 12px;
      line-height: 1.7;
    }

    .mono {
      font-family: var(--mono);
    }

    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: min(360px, calc(100vw - 36px));
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(0, 0, 0, 0.72);
      color: var(--text);
      box-shadow: var(--shadow);
      opacity: 0;
      transform: translateY(12px);
      pointer-events: none;
      transition: opacity 180ms ease, transform 180ms ease;
      z-index: 30;
    }

    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }

    .reveal {
      animation: rise 0.42s ease both;
    }

    @keyframes rise {
      from {
        opacity: 0;
        transform: translateY(14px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    @keyframes pulse {
      0% {
        box-shadow: 0 0 0 0 rgba(255, 122, 69, 0.45);
      }
      70% {
        box-shadow: 0 0 0 14px rgba(255, 122, 69, 0);
      }
      100% {
        box-shadow: 0 0 0 0 rgba(255, 122, 69, 0);
      }
    }

    @media (max-width: 980px) {
      .page {
        width: min(100vw - 20px, 1320px);
        padding-top: 10px;
      }

      .hero {
        min-height: auto;
        padding: 28px 22px;
      }

      .grid-top {
        grid-template-columns: 1fr;
      }

      .field-grid {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 640px) {
      .hero h1 {
        max-width: 10ch;
      }

      .panel-head,
      form,
      .protocol-note,
      .history-actions,
      .result-shell,
      .history-shell,
      .compare-shell {
        padding-left: 18px;
        padding-right: 18px;
      }

      .metric-strip {
        grid-template-columns: 1fr 1fr;
      }

      .meta-line,
      .pill-row,
      .download-row,
      .summary-pills {
        gap: 8px;
      }
    }
  </style>
</head>
<body>
  <div class="page">
    <header class="hero">
      <div>
        <p class="eyebrow">Huoyan Web Console</p>
        <h1>中转站检测台</h1>
        <p>输入 baseUrl、模型与 key，直接复用 Huoyan 现有测试链路完成检测；当前结果、历史记录与多结果对比都在同一页完成。</p>
      </div>
      <div class="hero-status" id="heroStatus">idle / waiting</div>
    </header>

    <main class="workspace">
      <section class="grid-top">
        <div class="surface">
          <div class="panel-head">
            <h2>运行设置</h2>
            <span id="runStatus" class="status-dot">待命</span>
          </div>
          <form id="runForm">
            <div class="field-grid">
              <label class="full">
                <span>Base URL</span>
                <input id="baseUrl" name="base_url" type="url" placeholder="https://your-relay.example.com/v1" required>
              </label>
              <label>
                <span>模型</span>
                <input id="modelName" name="model" type="text" placeholder="gpt-4o / glm-4.5 / qwen-max" required>
              </label>
              <label>
                <span>API 风格</span>
                <select id="apiStyle" name="api_style">
                  <option value="openai-chat">openai-chat</option>
                  <option value="openai-responses">openai-responses</option>
                  <option value="anthropic-messages">anthropic-messages</option>
                </select>
              </label>
              <label class="full">
                <span>API Key</span>
                <input id="apiKey" name="api_key" type="password" placeholder="sk-..." required>
              </label>
              <label>
                <span>声明家族（可选）</span>
                <input id="claimedFamily" name="claimed_family" type="text" placeholder="openai / claude / qwen">
              </label>
            </div>

            <div>
              <label>能力开关</label>
              <div class="toggles">
                <label class="switch"><input id="supportsStream" type="checkbox" checked> 支持流式</label>
                <label class="switch"><input id="supportsTools" type="checkbox" checked> 支持工具调用</label>
                <label class="switch"><input id="supportsVision" type="checkbox"> 支持视觉</label>
              </div>
            </div>

            <div>
              <label>测试套件</label>
              <div class="chip-grid">
                <label class="chip"><input class="suite-check" type="checkbox" value="authenticity" checked> authenticity</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="performance" checked> performance</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="agentic" checked> agentic</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="cost_security" checked> cost_security</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="security_audit" checked> security_audit</label>
              </div>
            </div>

            <div class="form-actions">
              <p class="hint">历史记录只保留脱敏后的报告与导出文件，不回存原始 key。</p>
              <button id="runButton" type="submit">开始测试</button>
            </div>
          </form>
        </div>

        <div class="surface">
          <div class="panel-head">
            <h2>工作区</h2>
          </div>
          <div class="protocol-note">
            <h3>同页完成运行、追溯与横向对比</h3>
            <p>页面默认针对单一中转站与单一模型运行测试。每次运行都会生成结构化报告、Markdown 导出和透明日志导出。</p>
            <div class="signal-list">
              <div class="signal-item">
                <strong>Result Rail</strong>
                <span>当前结果直接在下方展开，按 suite 归组查看 probe 结论、状态和细项。</span>
              </div>
              <div class="signal-item">
                <strong>History Ledger</strong>
                <span>历史区保留已测试记录，可回看、下载 JSON / Markdown / NDJSON。</span>
              </div>
              <div class="signal-item">
                <strong>Compare Matrix</strong>
                <span>勾选多条记录后生成指标对比矩阵，快速识别分数、状态和摘要差异。</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="surface">
        <div class="panel-head">
          <h2>当前结果</h2>
        </div>
        <div id="resultMount" class="result-shell">
          <div class="empty-state">尚未运行测试。提交表单后，结果会在这里按 suite 展开。</div>
        </div>
      </section>

      <section class="surface">
        <div class="panel-head">
          <h2>测试记录</h2>
        </div>
        <div class="history-actions">
          <p>选择一条记录回看详情，选择多条记录生成对比矩阵。</p>
          <button id="compareButton" type="button" class="ghost-button">对比所选记录</button>
        </div>
        <div id="historyMount" class="history-shell">
          <div class="empty-state">暂无历史记录。</div>
        </div>
      </section>

      <section class="surface">
        <div class="panel-head">
          <h2>结果对比</h2>
        </div>
        <div id="compareMount" class="compare-shell">
          <div class="empty-state">勾选至少两条历史记录后，在这里输出对比矩阵。</div>
        </div>
      </section>
    </main>
  </div>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>

  <script>
    const runForm = document.getElementById('runForm');
    const runButton = document.getElementById('runButton');
    const runStatus = document.getElementById('runStatus');
    const heroStatus = document.getElementById('heroStatus');
    const resultMount = document.getElementById('resultMount');
    const historyMount = document.getElementById('historyMount');
    const compareMount = document.getElementById('compareMount');
    const compareButton = document.getElementById('compareButton');
    const toast = document.getElementById('toast');

    const state = {
      activeRunId: null,
      history: [],
    };

    function escapeHtml(value) {
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function prettyJson(value) {
      return escapeHtml(JSON.stringify(value, null, 2));
    }

    function suiteLabel(name) {
      return {
        scorecard: '评分卡',
        authenticity: '真实性与路由',
        performance: '性能与稳定性',
        agentic: '工具链路与上下文',
        cost_security: '计量与入口安全',
        security_audit: '中转安全审计',
      }[name] || name;
    }

    function showToast(message, tone = 'default') {
      toast.textContent = message;
      toast.style.borderColor = tone === 'error'
        ? 'rgba(255, 107, 99, 0.38)'
        : 'rgba(255, 122, 69, 0.38)';
      toast.classList.add('show');
      window.clearTimeout(showToast.timer);
      showToast.timer = window.setTimeout(() => {
        toast.classList.remove('show');
      }, 2800);
    }

    async function fetchJson(url, options = {}) {
      const response = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
      });
      const contentType = response.headers.get('content-type') || '';
      const payload = contentType.includes('application/json') ? await response.json() : null;
      if (!response.ok) {
        const detail = payload && payload.detail ? payload.detail : '请求失败';
        throw new Error(detail);
      }
      return payload;
    }

    function setRunState(mode, text) {
      runStatus.className = 'status-dot';
      if (mode === 'running') {
        runStatus.classList.add('running');
      } else if (mode === 'done') {
        runStatus.classList.add('done');
      } else if (mode === 'error') {
        runStatus.classList.add('error');
      }
      runStatus.textContent = text;
      heroStatus.textContent = {
        running: 'running / probing',
        done: 'idle / report ready',
        error: 'halt / request failed',
      }[mode] || 'idle / waiting';
    }

    function buildSummaryPills(summary) {
      const entries = Object.entries(summary || {});
      if (!entries.length) {
        return '<span class="mini-pill">无汇总</span>';
      }
      return entries
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([key, value]) => `<span class="mini-pill mono">${escapeHtml(key)}: ${escapeHtml(value)}</span>`)
        .join('');
    }

    function renderDownloads(record) {
      const urls = record.download_urls || {};
      const formats = Object.keys(urls);
      if (!formats.length) {
        return '';
      }
      return `
        <div class="download-row">
          ${formats.map((format) => (
            `<a href="${escapeHtml(urls[format])}" target="_blank" rel="noopener">导出 ${escapeHtml(format.toUpperCase())}</a>`
          )).join('')}
        </div>
      `;
    }

    function renderResult(report, record) {
      const model = report.model;
      const focusCards = (model.focus_cards || []).map((item) => `
        <div class="metric-cell">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.value || '-')}</span>
          <em>${escapeHtml(item.status_label)} · ${escapeHtml(item.summary || '')}</em>
        </div>
      `).join('');

      const suites = (model.suites || []).map((suite) => `
        <section class="suite-block reveal">
          <div class="suite-head">
            <h4>${escapeHtml(suite.label)}</h4>
            <span class="mini-pill mono">${escapeHtml(suite.suite)}</span>
          </div>
          <div class="probe-list">
            ${(suite.probes || []).map((probe) => `
              <article class="probe-row">
                <div class="probe-top">
                  <div>
                    <strong>${escapeHtml(probe.label)}</strong>
                    <div class="probe-value">${escapeHtml(probe.value || '-')}</div>
                  </div>
                  <span class="status-pill ${escapeHtml(probe.status)}">${escapeHtml(probe.status_label)}</span>
                </div>
                <div class="probe-summary">${escapeHtml(probe.summary || '')}</div>
                ${(Object.keys(probe.metrics || {}).length || Object.keys(probe.evidence || {}).length) ? `
                  <details>
                    <summary>查看 metrics / evidence</summary>
                    ${Object.keys(probe.metrics || {}).length ? `<pre>${prettyJson(probe.metrics)}</pre>` : ''}
                    ${Object.keys(probe.evidence || {}).length ? `<pre>${prettyJson(probe.evidence)}</pre>` : ''}
                  </details>
                ` : ''}
              </article>
            `).join('')}
          </div>
        </section>
      `).join('');

      resultMount.innerHTML = `
        <div class="result-head reveal">
          <div class="result-title">
            <div>
              <h3>${escapeHtml(model.name)}</h3>
              <div class="meta-line">
                <span>${escapeHtml(report.provider.base_url)}</span>
                <span>家族 ${escapeHtml(model.claimed_family || '-')}</span>
                <span>生成于 ${escapeHtml(record.generated_at)}</span>
                <span>Key ${escapeHtml(record.key_hint || '')}</span>
              </div>
            </div>
            <div class="pill-row">
              <span class="status-pill ${escapeHtml(model.overall_status)}">${escapeHtml(model.overall_status_label)}</span>
              ${buildSummaryPills(model.summary)}
            </div>
          </div>
          ${renderDownloads(record)}
          ${focusCards ? `<div class="metric-strip">${focusCards}</div>` : ''}
          <div class="suite-stack">${suites || '<div class="empty-state">这条结果没有可展示的 suite。</div>'}</div>
        </div>
      `;
    }

    function renderHistory(records) {
      if (!records.length) {
        historyMount.innerHTML = '<div class="empty-state">暂无历史记录。</div>';
        return;
      }

      historyMount.innerHTML = `
        <div class="history-table-wrap reveal">
          <table>
            <thead>
              <tr>
                <th>选择</th>
                <th>记录</th>
                <th>状态</th>
                <th>汇总</th>
                <th>重点指标</th>
                <th>导出</th>
              </tr>
            </thead>
            <tbody>
              ${records.map((record) => `
                <tr class="${record.run_id === state.activeRunId ? 'active-row' : ''}">
                  <td>
                    <input class="compare-check" type="checkbox" value="${escapeHtml(record.run_id)}">
                  </td>
                  <td>
                    <button type="button" class="history-open" data-run-id="${escapeHtml(record.run_id)}">${escapeHtml(record.model)}</button>
                    <div class="record-meta">
                      <span>${escapeHtml(record.base_url)}</span>
                      <span>${escapeHtml(record.generated_at)}</span>
                    </div>
                  </td>
                  <td>
                    <span class="status-pill ${escapeHtml(record.overall_status)}">${escapeHtml(record.status_label)}</span>
                  </td>
                  <td>
                    <div class="summary-pills">${buildSummaryPills(record.summary)}</div>
                  </td>
                  <td>
                    <div class="focus-list">
                      ${(record.focus_metrics || []).slice(0, 3).map((item) => `
                        <div class="focus-item">${escapeHtml(item.label)} · ${escapeHtml(item.value || '-')}</div>
                      `).join('')}
                    </div>
                  </td>
                  <td>${renderDownloads(record)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;

      historyMount.querySelectorAll('.history-open').forEach((button) => {
        button.addEventListener('click', async () => {
          try {
            const runId = button.dataset.runId;
            const payload = await fetchJson(`/api/history/${runId}`);
            state.activeRunId = runId;
            renderResult(payload.report, payload.record);
            renderHistory(state.history);
          } catch (error) {
            showToast(error.message, 'error');
          }
        });
      });
    }

    function renderComparison(payload) {
      const rows = payload.rows || [];
      if (!rows.length) {
        compareMount.innerHTML = '<div class="empty-state">这些记录没有可对比的共同指标。</div>';
        return;
      }

      compareMount.innerHTML = `
        <div class="compare-table-wrap reveal">
          <table>
            <thead>
              <tr>
                <th>指标</th>
                ${payload.runs.map((run) => `
                  <th>
                    ${escapeHtml(run.model)}
                    <div class="record-meta">
                      <span>${escapeHtml(run.generated_at)}</span>
                      <span>${escapeHtml(run.base_url)}</span>
                    </div>
                  </th>
                `).join('')}
              </tr>
            </thead>
            <tbody>
              ${rows.map((row) => `
                <tr>
                  <td>
                    <strong>${escapeHtml(row.label)}</strong>
                    <div class="record-meta">
                      <span>${escapeHtml(suiteLabel(row.suite))}</span>
                      <span class="mono">${escapeHtml(row.probe)}</span>
                    </div>
                  </td>
                  ${row.cells.map((cell) => `
                    <td>
                      <div class="compare-cell">
                        <span class="status-pill ${escapeHtml(cell.status)}">${escapeHtml(cell.status_label)}</span>
                        <div class="mono">${escapeHtml(cell.value || '-')}</div>
                        <p>${escapeHtml(cell.summary || '')}</p>
                      </div>
                    </td>
                  `).join('')}
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;
    }

    async function refreshHistory() {
      const payload = await fetchJson('/api/history');
      state.history = payload.records || [];
      renderHistory(state.history);
    }

    function selectedRunIds() {
      return Array.from(document.querySelectorAll('.compare-check:checked')).map((input) => input.value);
    }

    runForm.addEventListener('submit', async (event) => {
      event.preventDefault();

      const enabledSuites = Array.from(document.querySelectorAll('.suite-check:checked')).map((input) => input.value);
      const payload = {
        base_url: document.getElementById('baseUrl').value.trim(),
        model: document.getElementById('modelName').value.trim(),
        api_key: document.getElementById('apiKey').value.trim(),
        api_style: document.getElementById('apiStyle').value,
        claimed_family: document.getElementById('claimedFamily').value.trim() || null,
        supports_stream: document.getElementById('supportsStream').checked,
        supports_tools: document.getElementById('supportsTools').checked,
        supports_vision: document.getElementById('supportsVision').checked,
        enabled_suites: enabledSuites,
      };

      runButton.disabled = true;
      setRunState('running', '测试进行中');
      resultMount.innerHTML = '<div class="empty-state reveal">正在运行测试，请保持页面开启。结果会在当前区域自动刷新。</div>';

      try {
        const response = await fetchJson('/api/run', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        state.activeRunId = response.record.run_id;
        renderResult(response.report, response.record);
        await refreshHistory();
        setRunState('done', '测试完成');
        showToast('测试已完成，结果与导出文件已写入历史记录。');
      } catch (error) {
        setRunState('error', '运行失败');
        resultMount.innerHTML = `<div class="empty-state reveal">运行失败：${escapeHtml(error.message)}</div>`;
        showToast(error.message, 'error');
      } finally {
        runButton.disabled = false;
      }
    });

    compareButton.addEventListener('click', async () => {
      const ids = selectedRunIds();
      if (ids.length < 2) {
        showToast('至少勾选两条记录才能对比。', 'error');
        return;
      }

      compareButton.disabled = true;
      compareMount.innerHTML = '<div class="empty-state reveal">正在生成对比矩阵。</div>';

      try {
        const payload = await fetchJson('/api/compare', {
          method: 'POST',
          body: JSON.stringify({ ids }),
        });
        renderComparison(payload);
        showToast('对比矩阵已更新。');
      } catch (error) {
        compareMount.innerHTML = `<div class="empty-state reveal">对比失败：${escapeHtml(error.message)}</div>`;
        showToast(error.message, 'error');
      } finally {
        compareButton.disabled = false;
      }
    });

    refreshHistory().catch((error) => {
      showToast(error.message, 'error');
    });
  </script>
</body>
</html>
"""

from huoyan.web_ui import HISTORY_HTML, INDEX_HTML
