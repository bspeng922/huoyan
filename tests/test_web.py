from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from huoyan.models import ModelReport, ProbeResult, ProbeStatus, ProviderReport, RunReport
from huoyan.web import create_app


def _build_report(
    *,
    model_name: str,
    base_url: str,
    overall_status: ProbeStatus = ProbeStatus.PASS,
    capability_score: float = 96.0,
    ttft_seconds: float = 1.28,
) -> RunReport:
    return RunReport(
        overall_status=overall_status,
        summary={"pass": 2, "warn": 1},
        providers=[
            ProviderReport(
                name="web-console",
                base_url=base_url,
                overall_status=overall_status,
                summary={"pass": 2, "warn": 1},
                audit_log_entries=[{"mode": "chat_completion", "status_code": 200}],
                models=[
                    ModelReport(
                        provider_name="web-console",
                        provider_base_url=base_url,
                        model=model_name,
                        claimed_family="openai",
                        overall_status=overall_status,
                        summary={"pass": 2, "warn": 1},
                        settings={},
                        results=[
                            ProbeResult(
                                suite="scorecard",
                                probe="capability_score",
                                status=overall_status,
                                summary="capability ok",
                                metrics={
                                    "score": capability_score,
                                    "grade": "high" if capability_score >= 90 else "moderate",
                                    "coverage_ratio": 1.0,
                                },
                            ),
                            ProbeResult(
                                suite="performance",
                                probe="ttft_tps",
                                status=ProbeStatus.WARN,
                                summary="ttft sampled",
                                metrics={
                                    "ttft_seconds": ttft_seconds,
                                    "output_token_throughput_per_second": 18.4,
                                },
                            ),
                            ProbeResult(
                                suite="security_audit",
                                probe="stream_integrity",
                                status=ProbeStatus.PASS,
                                summary="stream ok",
                                metrics={"event_count": 42},
                            ),
                        ],
                    )
                ],
            )
        ],
        audit_log_entries=[{"mode": "chat_completion", "status_code": 200}],
    )


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(self.tempdir.name))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @patch("huoyan.web.run_app", new_callable=AsyncMock)
    def test_run_endpoint_persists_record_and_exports(self, mock_run_app: AsyncMock) -> None:
        mock_run_app.return_value = _build_report(
            model_name="glm-5",
            base_url="https://relay.example.com/v1",
        )

        response = self.client.post(
            "/api/run",
            json={
                "base_url": "https://relay.example.com/v1",
                "model": "glm-5",
                "api_key": "sk-test-1234567890",
                "supports_stream": True,
                "supports_tools": True,
                "supports_vision": False,
                "enabled_suites": ["authenticity", "performance"],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        record = payload["record"]

        self.assertEqual(record["model"], "glm-5")
        self.assertEqual(record["overall_status"], "pass")
        self.assertEqual(record["api_style"], "openai-chat")
        self.assertIn("json", record["download_urls"])
        self.assertIn("md", record["download_urls"])
        self.assertIn("ndjson", record["download_urls"])

        history = self.client.get("/api/history")
        self.assertEqual(history.status_code, 200)
        records = history.json()["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["run_id"], record["run_id"])

        export_json = self.client.get(record["download_urls"]["json"])
        export_md = self.client.get(record["download_urls"]["md"])
        export_ndjson = self.client.get(record["download_urls"]["ndjson"])
        self.assertEqual(export_json.status_code, 200)
        self.assertEqual(export_md.status_code, 200)
        self.assertEqual(export_ndjson.status_code, 200)

        history_index = Path(self.tempdir.name) / "web" / "history.json"
        self.assertTrue(history_index.exists())

    @patch("huoyan.web.run_app", new_callable=AsyncMock)
    def test_compare_endpoint_returns_probe_matrix(self, mock_run_app: AsyncMock) -> None:
        mock_run_app.side_effect = [
            _build_report(
                model_name="glm-5",
                base_url="https://relay-a.example.com/v1",
                capability_score=96.0,
                ttft_seconds=1.20,
            ),
            _build_report(
                model_name="qwen-max",
                base_url="https://relay-b.example.com/v1",
                overall_status=ProbeStatus.WARN,
                capability_score=84.0,
                ttft_seconds=2.35,
            ),
        ]

        first = self.client.post(
            "/api/run",
            json={
                "base_url": "https://relay-a.example.com/v1",
                "model": "glm-5",
                "api_key": "sk-first-1234567890",
                "enabled_suites": ["performance"],
            },
        )
        second = self.client.post(
            "/api/run",
            json={
                "base_url": "https://relay-b.example.com/v1",
                "model": "qwen-max",
                "api_key": "sk-second-1234567890",
                "enabled_suites": ["performance"],
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        compare = self.client.post(
            "/api/compare",
            json={
                "ids": [
                    first.json()["record"]["run_id"],
                    second.json()["record"]["run_id"],
                ]
            },
        )

        self.assertEqual(compare.status_code, 200)
        payload = compare.json()
        self.assertEqual(len(payload["runs"]), 2)

        capability_row = next(row for row in payload["rows"] if row["probe"] == "capability_score")
        self.assertEqual(len(capability_row["cells"]), 2)
        self.assertIn("96", capability_row["cells"][0]["value"])
        self.assertIn("84", capability_row["cells"][1]["value"])

    @patch("huoyan.web.run_app")
    def test_start_run_endpoint_exposes_job_progress(self, mock_run_app) -> None:
        async def fake_run_app(config, progress_callback=None):
            if progress_callback is not None:
                await progress_callback(
                    {"type": "probe_started", "suite": "performance", "probe": "ttft_tps"}
                )
                await progress_callback(
                    {"type": "probe_finished", "suite": "performance", "probe": "ttft_tps"}
                )
                await asyncio.sleep(0.05)
            return _build_report(
                model_name="glm-5",
                base_url="https://relay.example.com/v1",
            )

        mock_run_app.side_effect = fake_run_app

        started = self.client.post(
            "/api/run/start",
            json={
                "base_url": "https://relay.example.com/v1",
                "model": "glm-5",
                "api_key": "sk-test-1234567890",
                "enabled_suites": ["performance"],
            },
        )

        self.assertEqual(started.status_code, 200)
        job = started.json()["job"]
        self.assertEqual(job["progress_total"], 6)
        self.assertEqual(job["status"], "queued")

        latest = None
        for _ in range(20):
            status = self.client.get(f"/api/run/jobs/{job['job_id']}")
            self.assertEqual(status.status_code, 200)
            latest = status.json()["job"]
            if latest["status"] in {"running", "completed"} and latest["progress_completed"] >= 1:
                break
            time.sleep(0.02)

        self.assertIsNotNone(latest)
        self.assertIn(latest["status"], {"running", "completed"})
        self.assertGreaterEqual(latest["progress_completed"], 1)
        self.assertEqual(latest["progress_total"], 6)
        if latest["status"] == "completed":
            self.assertEqual(latest["progress_completed"], 6)
            self.assertEqual(latest["progress_percent"], 100.0)
            self.assertEqual(latest["result"]["record"]["model"], "glm-5")

    @patch("huoyan.web.run_app", new_callable=AsyncMock)
    def test_run_endpoint_infers_non_chat_api_style_from_base_url(self, mock_run_app: AsyncMock) -> None:
        mock_run_app.return_value = _build_report(
            model_name="claude-3-7-sonnet",
            base_url="https://relay.example.com/v1/messages",
        )

        response = self.client.post(
            "/api/run",
            json={
                "base_url": "https://relay.example.com/v1/messages",
                "model": "claude-3-7-sonnet",
                "api_key": "sk-test-1234567890",
                "enabled_suites": ["agentic"],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["record"]["api_style"], "anthropic-messages")

    def test_index_page_is_served(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Huoyan Web Console", response.text)
        self.assertIn('id="homePage"', response.text)
        self.assertIn('href="/history"', response.text)
        self.assertNotIn('id="historyMount"', response.text)
        self.assertNotIn('id="apiStyle"', response.text)
        self.assertNotIn("Run</strong>", response.text)
        self.assertIn('id="progressCard"', response.text)

    def test_history_page_is_served(self) -> None:
        response = self.client.get("/history")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Huoyan Test History", response.text)
        self.assertIn('id="historyPage"', response.text)
        self.assertIn('id="historyMount"', response.text)
        self.assertIn("对比所选记录", response.text)
