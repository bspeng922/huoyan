from __future__ import annotations

import unittest

from huoyan.models import ProbeResult, ProbeStatus
from huoyan.suites.authenticity import _capability_threshold, build_scorecard_results


def _probe(probe: str, status: ProbeStatus, *, suite: str = "test", score: float | None = None) -> ProbeResult:
    return ProbeResult(
        suite=suite,
        probe=probe,
        status=status,
        summary=probe,
        score=score,
    )


class ScorecardTests(unittest.TestCase):
    def test_capability_threshold_scales_with_question_count(self) -> None:
        self.assertEqual(_capability_threshold("openai", 6), 5)
        self.assertEqual(_capability_threshold("claude", 6), 5)
        self.assertEqual(_capability_threshold("glm", 6), 4)
        self.assertEqual(_capability_threshold("unknown", 6), 4)

    def test_builds_split_scorecards_without_identity_mixing(self) -> None:
        results = [
            _probe("identity", ProbeStatus.WARN),
            _probe("capability_fingerprint", ProbeStatus.PASS, score=1.0),
            _probe("acrostic_constraints", ProbeStatus.PASS, score=1.0),
            _probe("boundary_reasoning", ProbeStatus.PASS, score=1.0),
            _probe("linguistic_fingerprint", ProbeStatus.PASS, score=1.0),
            _probe("response_consistency", ProbeStatus.WARN, score=0.8),
            _probe("tool_calling", ProbeStatus.PASS, score=1.0),
            _probe("multi_turn_tool", ProbeStatus.PASS, score=1.0),
            _probe("long_context_integrity", ProbeStatus.PASS, score=1.0),
            _probe("stream_integrity", ProbeStatus.PASS, score=1.0),
            _probe("token_alignment", ProbeStatus.SKIP),
            _probe("dependency_substitution", ProbeStatus.PASS, score=1.0),
            _probe("conditional_delivery", ProbeStatus.PASS, score=1.0),
            _probe("error_response_leakage", ProbeStatus.PASS, score=1.0),
            _probe("tls_baseline", ProbeStatus.PASS, score=1.0),
            _probe("security_headers", ProbeStatus.WARN),
            _probe("rate_limit_transparency", ProbeStatus.SKIP),
            _probe("system_prompt_injection", ProbeStatus.SKIP),
        ]

        scorecards = {result.probe: result for result in build_scorecard_results(results)}

        self.assertEqual(set(scorecards), {"capability_score", "protocol_score", "security_score"})
        self.assertEqual(scorecards["capability_score"].status, ProbeStatus.PASS)
        self.assertEqual(scorecards["protocol_score"].status, ProbeStatus.PASS)
        self.assertEqual(scorecards["security_score"].status, ProbeStatus.PASS)
        self.assertAlmostEqual(scorecards["protocol_score"].metrics["coverage_ratio"], 0.6, places=4)
        self.assertEqual(scorecards["capability_score"].metrics["score_scope"], "model_capabilities")
