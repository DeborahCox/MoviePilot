"""Unit tests for baseline_diff report comparison."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from baseline_diff import diff_reports, read_report

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" failures="2" errors="1">
    <testcase classname="tests.test_alpha" name="test_ok" />
    <testcase classname="tests.test_alpha" name="test_fail">
      <failure message="assert 1 == 2">trace</failure>
    </testcase>
    <testcase classname="tests.test_beta" name="test_error">
      <error message="boom">trace</error>
    </testcase>
  </testsuite>
</testsuites>
"""


class ReadReportTests(unittest.TestCase):
    def test_collects_failures_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.xml"
            path.write_text(SAMPLE_XML, encoding="utf-8")
            report = read_report(path, 1)
        self.assertFalse(report.missing)
        self.assertEqual(
            report.failures,
            {"tests.test_alpha::test_fail", "tests.test_beta::test_error"},
        )

    def test_missing_report_uses_exit_code_marker(self) -> None:
        report = read_report(Path("/nonexistent/report.xml"), 2)
        self.assertTrue(report.missing)
        self.assertEqual(report.failures, {"__pytest_exit__:2"})


class DiffTests(unittest.TestCase):
    def test_classifies_new_fixed_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "base.xml"
            head_path = root / "head.xml"
            base_path.write_text(
                """<testsuite><testcase classname="t" name="a"><failure/></testcase>
                <testcase classname="t" name="b"><failure/></testcase></testsuite>""",
                encoding="utf-8",
            )
            head_path.write_text(
                """<testsuite><testcase classname="t" name="b"><failure/></testcase>
                <testcase classname="t" name="c"><failure/></testcase></testsuite>""",
                encoding="utf-8",
            )
            diff = diff_reports(read_report(base_path), read_report(head_path))
        self.assertEqual(diff["new_failures"], ["t::c"])
        self.assertEqual(diff["fixed"], ["t::a"])
        self.assertEqual(diff["persistent"], ["t::b"])


if __name__ == "__main__":
    unittest.main()
