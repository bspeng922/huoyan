from __future__ import annotations

import unittest

from huoyan.client import OpenAICompatClient
from huoyan.config import ModelTarget, ProviderTarget


class AnthropicMessageConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ProviderTarget(
            name="anthropic-demo",
            base_url="https://example.com/v1/messages",
            api_key="sk-test",
            api_style="anthropic-messages",
            models=[ModelTarget(model="claude-demo", claimed_family="claude", supports_vision=True)],
        )
        self.client = OpenAICompatClient(self.provider)

    def test_preserves_url_images_for_anthropic(self) -> None:
        _, converted = self.client._convert_messages_for_anthropic(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
                    ],
                }
            ]
        )

        self.assertEqual(converted[0]["content"][1]["type"], "image")
        self.assertEqual(converted[0]["content"][1]["source"]["type"], "url")
        self.assertEqual(converted[0]["content"][1]["source"]["url"], "https://example.com/cat.png")

    def test_preserves_data_uri_images_for_anthropic(self) -> None:
        _, converted = self.client._convert_messages_for_anthropic(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                    ],
                }
            ]
        )

        image_block = converted[0]["content"][1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["source"]["type"], "base64")
        self.assertEqual(image_block["source"]["media_type"], "image/png")
        self.assertEqual(image_block["source"]["data"], "QUJD")
