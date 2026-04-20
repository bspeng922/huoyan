from __future__ import annotations

import unittest
from datetime import datetime

from huoyan.models import ProbeResult, ProbeStatus, RunReport
from huoyan.utils import local_now


class TimezoneTests(unittest.TestCase):
    def test_local_now_uses_local_timezone(self) -> None:
        current = local_now()
        expected_offset = datetime.now().astimezone().utcoffset()
        self.assertIsNotNone(current.tzinfo)
        self.assertEqual(current.utcoffset(), expected_offset)

    def test_report_and_probe_defaults_use_local_timezone(self) -> None:
        report = RunReport(overall_status=ProbeStatus.PASS, summary={}, providers=[])
        probe = ProbeResult(suite="test", probe="demo", status=ProbeStatus.PASS, summary="ok")
        expected_offset = datetime.now().astimezone().utcoffset()
        self.assertEqual(report.generated_at.utcoffset(), expected_offset)
        self.assertEqual(probe.started_at.utcoffset(), expected_offset)
        self.assertEqual(probe.finished_at.utcoffset(), expected_offset)
