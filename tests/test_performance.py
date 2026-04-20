from __future__ import annotations

import unittest

from huoyan.client import ChatResponse, StreamResponse
from huoyan.config import ModelTarget, ProbeSettings
from huoyan.suites.performance import _availability_probe, _stream_probe


class FakePerformanceClient:
    def __init__(self, *, stream_responses=None, chat_outcomes=None):
        self._stream_responses = list(stream_responses or [])
        self._chat_outcomes = list(chat_outcomes or [])

    async def stream_chat_completion(self, **_: object) -> StreamResponse:
        return self._stream_responses.pop(0)

    async def chat_completion(self, **_: object) -> ChatResponse:
        outcome = self._chat_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PerformanceProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_probe_reports_actual_stat_basis(self) -> None:
        model = ModelTarget(model="glm-5.1", claimed_family="glm")
        settings = ProbeSettings(
            performance_stream_samples=3,
            performance_stream_sample_interval_seconds=0,
            stream_max_tokens=512,
        )
        client = FakePerformanceClient(
            stream_responses=[
                StreamResponse(
                    content="内容" * 100,
                    usage={"input_tokens": 40, "output_tokens": 120},
                    status_code=200,
                    elapsed_seconds=3.5,
                    ttft_seconds=1.0,
                    first_content_seconds=1.2,
                    generation_seconds=2.0,
                    content_event_offsets_seconds=[1.2, 1.4, 1.6],
                ),
                StreamResponse(
                    content="内容" * 100,
                    usage={"input_tokens": 40, "output_tokens": 120},
                    status_code=200,
                    elapsed_seconds=4.0,
                    ttft_seconds=2.0,
                    first_content_seconds=2.2,
                    generation_seconds=2.0,
                    content_event_offsets_seconds=[2.2, 2.4, 2.6],
                ),
                StreamResponse(
                    content="内容" * 100,
                    usage={"input_tokens": 40, "output_tokens": 120},
                    status_code=200,
                    elapsed_seconds=4.5,
                    ttft_seconds=3.0,
                    first_content_seconds=3.2,
                    generation_seconds=2.0,
                    content_event_offsets_seconds=[3.2, 3.4, 3.6],
                ),
            ]
        )

        result = await _stream_probe(client, model, settings)

        self.assertEqual(result.metrics["ttft_observed_basis"], "avg")
        self.assertIn("TTFT avg", result.summary)

    async def test_availability_warns_on_single_failure(self) -> None:
        model = ModelTarget(model="glm-5.1", claimed_family="glm")
        settings = ProbeSettings(uptime_samples=5, uptime_interval_seconds=0)
        success = ChatResponse(content="OK", raw={}, usage={}, status_code=200, elapsed_seconds=0.2)
        client = FakePerformanceClient(chat_outcomes=[success, success, success, success, RuntimeError("boom")])

        result = await _availability_probe(client, model, settings)

        self.assertEqual(result.status, result.status.WARN)
        self.assertEqual(result.metrics["failure_count"], 1)
