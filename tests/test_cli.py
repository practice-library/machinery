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
