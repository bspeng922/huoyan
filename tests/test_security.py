from __future__ import annotations

import unittest

from huoyan.client import ChatResponse
from huoyan.config import ModelTarget, ProbeSettings
from huoyan.suites.security_audit import _system_prompt_injection_probe


class FakeSecurityClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat_completion(self, **_: object) -> ChatResponse:
        return self._responses.pop(0)


class SystemPromptInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_disclosure_stays_skip(self) -> None:
        client = FakeSecurityClient(
            [
                ChatResponse(content="NONE_RECEIVED", raw={}, usage={}, status_code=200, elapsed_seconds=0.2),
                ChatResponse(content="0", raw={}, usage={}, status_code=200, elapsed_seconds=0.2),
            ]
        )

        result = await _system_prompt_injection_probe(client, ModelTarget(model="glm-5.1"), ProbeSettings())

        self.assertEqual(result.status, result.status.SKIP)
        self.assertTrue(result.metrics["denied_receiving_instructions"])
