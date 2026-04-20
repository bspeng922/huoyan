from __future__ import annotations

import unittest

from huoyan.client import RawHTTPResponse
from huoyan.config import ModelTarget, ProbeSettings, ProviderTarget
from huoyan.suites.cost_security import _rate_limit_transparency_probe


class FakeCostClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def raw_json_request(self, **_: object) -> RawHTTPResponse:
        return self._responses.pop(0)


class RateLimitTransparencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_combines_passive_and_active_sampling(self) -> None:
        provider = ProviderTarget(
            name="relay-demo",
            base_url="https://example.com/v1",
            api_key="sk-test",
            api_style="openai-chat",
            models=[ModelTarget(model="glm-5.1", claimed_family="glm")],
        )
        responses = [
            RawHTTPResponse(status_code=200, elapsed_seconds=0.1, headers={}, text="ok"),
            RawHTTPResponse(status_code=200, elapsed_seconds=0.1, headers={}, text="ok"),
        ]
        responses.extend(
            [
                RawHTTPResponse(
                    status_code=429,
                    elapsed_seconds=0.1,
                    headers={"retry-after": "1", "x-ratelimit-limit": "10"},
                    text="rate limited",
                )
            ]
        )
        responses.extend(
            [RawHTTPResponse(status_code=200, elapsed_seconds=0.1, headers={}, text="ok") for _ in range(5)]
        )
        client = FakeCostClient(responses)
        settings = ProbeSettings(concurrency_levels=[4])

        result = await _rate_limit_transparency_probe(client, provider, provider.models[0], settings)

        self.assertEqual(result.status, result.status.PASS)
        self.assertEqual(result.metrics["active_burst_size"], 6)
        self.assertTrue(result.metrics["saw_429"])
        self.assertEqual(len(result.evidence["active_observations"]), 6)
