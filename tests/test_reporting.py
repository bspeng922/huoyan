from __future__ import annotations

import unittest

from huoyan.models import ModelReport, ProbeResult, ProbeStatus, ProviderReport, RunReport
from huoyan.reporting import render_markdown


class ReportingTests(unittest.TestCase):
    def test_markdown_includes_runtime_metadata_and_scorecards(self) -> None:
        report = RunReport(
            overall_status=ProbeStatus.PASS,
            summary={"pass": 1},
            metadata={
                "app_version": "0.1.0",
                "report_schema_version": "2",
                "score_version": "2026-04-16.scorecards-v1",
                "git_commit": "abcdef123456",
                "git_dirty": True,
            },
            providers=[
                ProviderReport(
                    name="relay-demo",
                    base_url="https://example.com/v1",
                    overall_status=ProbeStatus.PASS,
                    summary={"pass": 1},
                    models=[
                        ModelReport(
                            provider_name="relay-demo",
                            provider_base_url="https://example.com/v1",
                            model="glm-5.1",
                            claimed_family="glm",
                            overall_status=ProbeStatus.PASS,
                            summary={"pass": 1},
                            settings={},
                            results=[
                                ProbeResult(
                                    suite="scorecard",
                                    probe="capability_score",
                                    status=ProbeStatus.PASS,
                                    summary="ok",
                                    metrics={"score": 96.0, "grade": "high", "coverage_ratio": 1.0},
                                )
                            ],
                        )
                    ],
                )
            ],
        )

        markdown = render_markdown(report)

        self.assertIn("评分版本", markdown)
        self.assertIn("能力评分卡", markdown)

    def test_markdown_expands_capability_questions_and_answers(self) -> None:
        report = RunReport(
            overall_status=ProbeStatus.PASS,
            summary={"pass": 1},
            providers=[
                ProviderReport(
                    name="relay-demo",
                    base_url="https://example.com/v1",
                    overall_status=ProbeStatus.PASS,
                    summary={"pass": 1},
                    models=[
                        ModelReport(
                            provider_name="relay-demo",
                            provider_base_url="https://example.com/v1",
                            model="glm-5.1",
                            claimed_family="glm",
                            overall_status=ProbeStatus.PASS,
                            summary={"pass": 1},
                            settings={},
                            results=[
                                ProbeResult(
                                    suite="authenticity",
                                    probe="capability_fingerprint",
                                    status=ProbeStatus.PASS,
                                    summary="ok",
                                    metrics={"correct_count": 4, "total_challenges": 6, "family_threshold": 4},
                                    evidence={
                                        "challenge_results": [
                                            {
                                                "question": "我想洗车，如果我家离洗车店步行只有50米的脚程，你建议我开车去还是走路去？",
                                                "expected_answer": "开车去",
                                                "response_excerpt": "开车去",
                                                "passed": True,
                                            }
                                        ]
                                    },
                                )
                            ],
                        )
                    ],
                )
            ],
        )

        markdown = render_markdown(report)

        self.assertIn("逐题结果", markdown)
        self.assertIn("我想洗车", markdown)
        self.assertIn("开车去", markdown)
