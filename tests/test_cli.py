"""The command line: what a refresh run prints and writes to the step summary."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from factrefresh import __main__ as cli
from tests.support import COMMIT, RUN_URL, FakeClock, GraphsCopy, Replay, exchanges


class RefreshSummary(unittest.TestCase):
    def test_facts_left_out_of_the_report_are_listed_in_the_output_and_the_step_summary(self):
        with GraphsCopy() as root, tempfile.TemporaryDirectory() as out:
            path = os.path.join(root, "vendors/example/graph/nodes/limit.example.burst.json")
            with open(path, encoding="utf-8") as f:
                node = json.load(f)
            node["source"] = "https://docs.example.com:8443/limits"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(node, f)
            summary = os.path.join(out, "summary.md")
            stdout = io.StringIO()
            with mock.patch.object(cli, "UrllibTransport", lambda: Replay(exchanges(), FakeClock())), \
                    mock.patch("factrefresh.refresh.Clock", FakeClock), \
                    mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}), redirect_stdout(stdout):
                code = cli.main(["refresh", "--graphs", root, "--out", os.path.join(out, "reports"),
                                 "--run-id", "7", "--run-url", RUN_URL, "--commit", COMMIT, "--pace", "0"])
            with open(summary, encoding="utf-8") as f:
                written = f.read()
            with open(os.path.join(out, "reports", "vendors", "example.json"), encoding="utf-8") as f:
                report = json.load(f)
        self.assertEqual(code, 0)
        for text in (stdout.getvalue(), written):
            self.assertIn("Left out of the reports:", text)
            self.assertIn("vendors/example `limit.example.burst`: blocked:", text)
        self.assertNotIn("limit.example.burst", [r["node"] for r in report["results"]])


class IgnoreInterval(unittest.TestCase):
    """--ignore-interval reaches the run, and the run says so in the step summary, not in the report (the
    refresh-report schema has no place for run options)."""

    def refresh(self, *flags):
        recorded = exchanges()
        recorded["https://docs.example.com/features"] = [
            {"status": 200, "headers": {"content-type": "text/html"}, "body": "<p>Webhooks are available on every plan.</p>"}]
        # the recorded 503 on /pricing stops docs.example.com for the run; a 404 leaves it open for /features
        recorded["https://docs.example.com/pricing"] = [{"status": 404, "body": ""}]
        transport = Replay(recorded, FakeClock())
        with GraphsCopy() as root, tempfile.TemporaryDirectory() as out:
            summary = os.path.join(out, "summary.md")
            with mock.patch.object(cli, "UrllibTransport", lambda: transport), \
                    mock.patch("factrefresh.refresh.Clock", FakeClock), \
                    mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}), redirect_stdout(io.StringIO()):
                code = cli.main(["refresh", "--graphs", root, "--out", os.path.join(out, "reports"), "--graph",
                                 "vendors/example", "--run-id", "7", "--run-url", RUN_URL, "--commit", COMMIT,
                                 "--pace", "0", *flags])
            with open(summary, encoding="utf-8") as f:
                written = f.read()
            with open(os.path.join(out, "reports", "vendors", "example.json"), encoding="utf-8") as f:
                report = json.load(f)
        self.assertEqual(code, 0)
        return transport, written, report

    def test_the_flag_makes_a_fact_read_yesterday_due_and_the_summary_says_so(self):
        transport, written, report = self.refresh("--ignore-interval")
        self.assertIn("https://docs.example.com/features", transport.urls())
        self.assertIn("feature.example.fresh", [r["node"] for r in report["results"]])
        self.assertTrue(written.startswith("Run option: `--ignore-interval`"), written)
        self.assertNotIn("ignore", json.dumps(report).lower())

    def test_without_the_flag_the_summary_and_the_run_are_unchanged(self):
        transport, written, report = self.refresh()
        self.assertNotIn("https://docs.example.com/features", transport.urls())
        self.assertTrue(written.startswith("| Graph |"), written)
