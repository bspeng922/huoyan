from __future__ import annotations

import unittest

from huoyan.client import ChatResponse, RawHTTPResponse
from huoyan.config import ModelTarget, ProbeSettings, ProviderTarget
from huoyan.suites.agentic import _long_context_probe, _multi_turn_tool_probe


class FakeAgenticClient:
    def __init__(self, *, chat_responses=None, raw_responses=None):
        self._chat_responses = list(chat_responses or [])
        self._raw_responses = list(raw_responses or [])

    async def chat_completion(self, **_: object) -> ChatResponse:
        return self._chat_responses.pop(0)

    async def raw_json_request(self, **_: object) -> RawHTTPResponse:
        return self._raw_responses.pop(0)


class AgenticProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_context_sweeps_until_first_failure(self) -> None:
        model = ModelTarget(model="glm-5.1", claimed_family="glm")
        settings = ProbeSettings(long_context_target_chars=20000)
        client = FakeAgenticClient(
            chat_responses=[
                ChatResponse(
                    content='{"head":"amber-17","middle":"lotus-29","tail":"onyx-41"}',
                    raw={},
                    usage={},
                    status_code=200,
                    elapsed_seconds=0.5,
                ),
                ChatResponse(
                    content='{"head":"amber-17","middle":"lotus-29","tail":"onyx-41"}',
                    raw={},
                    usage={},
                    status_code=200,
                    elapsed_seconds=0.5,
                ),
                ChatResponse(
                    content='{"head":"amber-17","middle":"wrong","tail":"onyx-41"}',
                    raw={},
                    usage={},
                    status_code=200,
                    elapsed_seconds=0.5,
                ),
            ]
        )

        result = await _long_context_probe(client, model, settings)

        self.assertEqual(result.status, result.status.WARN)
        self.assertEqual(result.metrics["max_preserved_target_chars"], 16000)
        self.assertEqual(result.metrics["first_failed_target_chars"], 20000)

    async def test_multi_turn_tool_uses_structured_validation(self) -> None:
        provider = ProviderTarget(
            name="relay-demo",
            base_url="https://example.com/v1",
            api_key="sk-test",
            api_style="openai-chat",
            models=[ModelTarget(model="glm-5.1", claimed_family="glm", supports_tools=True)],
        )
        model = provider.models[0]
        settings = ProbeSettings()
        client = FakeAgenticClient(
            chat_responses=[
                ChatResponse(
                    content="",
                    raw={
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "function": {"name": "get_weather", "arguments": '{"city":"杭州"}'},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    usage={},
                    status_code=200,
                    elapsed_seconds=0.5,
                )
            ],
            raw_responses=[
                RawHTTPResponse(
                    status_code=200,
                    elapsed_seconds=0.5,
                    json_body={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"city":"杭州","temperature":28,"condition":"晴","clothing_advice":"建议穿轻薄衣物"}'
                                }
                            }
                        ]
                    },
                )
            ],
        )

        result = await _multi_turn_tool_probe(client, provider, model, settings)

        self.assertEqual(result.status, result.status.PASS)
        self.assertEqual(result.metrics["matched_fields"], 4)
