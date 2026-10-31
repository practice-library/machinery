"""The plugin's verbs called in process, each rule with literal values; and the conformance runner, which must
pass the real plugin and fail one that extracts text naively."""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import unittest

from factrefresh import plugin
from factrefresh.text import has_cells, markdown_row, quote_form

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "conformance", "cases")
START = "2026-10-31T12:00:00Z"
CAPS = {"timeSeconds": 60, "outputBytes": 1048576}


def case(file, name):
    with open(os.path.join(CASES, file), encoding="utf-8") as f:
        group = json.load(f)
    from conformance.run import deep_merge
    return deep_merge(group["defaults"], [c for c in group["cases"] if c["name"] == name][0].get("request", {}))


def call(request):
    out = io.StringIO()
    code = plugin.main(io.StringIO(json.dumps(request)), out)
    return code, json.loads(out.getvalue())


def plan_request(cap, hosts):
    """hosts: [(host, host cap, [(node, source, measuredAt), ...]), ...]"""
    pol = {"userAgent": "practice-library-graphs/1 (+https://github.com/practice-library/graphs)", "maxRequestsPerRun": cap,
           "retryAfterMaxSeconds": 60, "unreadableRunsBeforeIssue": 3,
           "hosts": [{"host": h, "vendor": "example", "format": "html", "maxRequestsPerRun": hc, "minIntervalDays": 7,
                      "maxBytes": 2000000, "timeoutSeconds": 30} for h, hc, _ in hosts]}
    nodes = [{"id": n, "source": s, "measuredAt": m, "attrs": {"quote": "Q."}} for _, _, facts in hosts for n, s, m in facts]
    return {"contract": "0", "verb": "plan", "caps": CAPS, "input": {
        "graph": "vendors/example", "commit": "0" * 40, "run": {"id": 1, "url": "https://github.com/x/y/actions/runs/1"},
        "now": START, "policy": pol, "nodes": nodes}}


class Plan(unittest.TestCase):
    def budgets(self, cap, needs):
        hosts = [(f"h{i}.example.com", 20, [(f"rule.example.h{i}n{j}", f"https://h{i}.example.com/{j}", f"2026-09-0{i}")
                                             for j in range(n)]) for i, n in enumerate(needs, start=1)]
        code, env = call(plan_request(cap, hosts))
        self.assertEqual(code, 0, env)
        return [u["budget"]["requests"] for u in env["data"]["units"]], env

    def test_budgets_fill_needs_oldest_first_then_the_rest_up_to_each_host_cap(self):
        self.assertEqual(self.budgets(50, [1, 1])[0], [20, 20])  # needs 2 + 2, then up to each cap of 20
        self.assertEqual(self.budgets(5, [2, 2])[0], [3, 2])  # 3 for the oldest, 2 left for the next

    def test_a_host_left_with_1_request_waits_with_2_it_runs(self):
        self.assertEqual(self.budgets(4, [2, 2])[0], [4])  # 1 left: robots.txt alone is no use; it goes back to the first
        self.assertEqual(self.budgets(5, [2, 2])[0], [3, 2])  # 2 left: robots.txt and one page

    def test_budgets_never_exceed_the_run_cap(self):
        for cap in (2, 3, 4, 5, 6, 7):
            got, _ = self.budgets(cap, [2, 2, 2])
            self.assertLessEqual(sum(got), cap, cap)

    def test_max_parallel_is_the_unit_count_up_to_3(self):
        for n, want in ((1, 1), (2, 2), (3, 3), (4, 3)):
            _, env = self.budgets(50, [1] * n)
            self.assertEqual(env["data"]["maxParallel"], want, n)

    def test_a_long_unlisted_host_keeps_the_reason_within_300_characters(self):
        host = ".".join(["a" * 60] * 6) + ".example.net"  # 377 characters, a valid host
        req = plan_request(50, [("docs.example.com", 20, [("rule.example.far", f"https://{host}/x", "2026-09-01")])])
        _, env = call(req)
        reason = env["data"]["decided"][0]["reason"]
        self.assertGreater(len(host), 300)
        self.assertLessEqual(len(reason), 300)
        self.assertTrue(reason.startswith("blocked:"))

    def test_a_markdown_row_of_250_cells_stays_cells(self):
        req = plan_request(50, [("docs.example.com", 20, [("plan.example.wide", "https://docs.example.com/t", "2026-09-01")])])
        req["input"]["nodes"][0]["attrs"]["quote"] = "|" + "a|" * 249  # 499 characters, 249 cells
        _, env = call(req)
        self.assertEqual(len(env["data"]["units"][0]["facts"][0]["quote"]["cells"]), 249)

    def test_a_markdown_row_becomes_cells(self):
        req = plan_request(50, [("docs.example.com", 20, [("plan.example.row", "https://docs.example.com/t", "2026-09-01")])])
        req["input"]["nodes"][0]["attrs"]["quote"] = "| Free | 20 |"
        _, env = call(req)
        self.assertEqual(env["data"]["units"][0]["facts"][0]["quote"], {"cells": ["Free", "20"]})


class RunUnit(unittest.TestCase):
    def unit(self, time_seconds, n_pages=3):
        req = case("run-unit-safety.json", "both quotes on their pages: unchanged, one request per page after robots.txt")
        facts = [{"node": f"rule.example.p{i}", "source": f"https://docs.example.com/p{i}", "measuredAt": "2026-09-01",
                  "quote": {"text": "Each key may send 20 requests per second."}} for i in range(n_pages)]
        req["input"]["facts"] = facts
        page = req["network"]["replay"]["exchanges"]["https://docs.example.com/a"]
        for f in facts:
            req["network"]["replay"]["exchanges"][f["source"]] = copy.deepcopy(page)
        req["caps"] = {"timeSeconds": time_seconds, "outputBytes": 1048576}
        code, env = call(req)
        self.assertEqual(code, 0, env)
        return env["data"]

    def test_no_request_starts_that_could_outlast_the_time_cap(self):
        # deadline = start + timeSeconds - 5; a request may take timeoutSeconds (30); pages are 2 s apart
        self.assertEqual(self.unit(39)["requests"], 3)  # deadline 34: robots at 0, pages at 2 and 4 (34 <= 34)
        self.assertEqual(self.unit(38)["requests"], 2)  # deadline 33: the page at 4 could end at 34
        self.assertEqual(self.unit(35)["requests"], 1)  # deadline 30: robots.txt only
        self.assertEqual(self.unit(34)["requests"], 0)  # deadline 29: nothing starts
        self.assertIsNone(self.unit(34)["robots"])

    def test_the_units_host_must_be_in_network_hosts(self):
        req = case("run-unit-safety.json", "both quotes on their pages: unchanged, one request per page after robots.txt")
        req["network"] = {"hosts": ["other.example.com"]}
        code, env = call(req)
        self.assertEqual(code, 2)
        self.assertIn("not in network.hosts", env["warnings"][0])

    def test_replay_answers_an_unrecorded_url_as_a_network_error(self):
        req = case("run-unit-safety.json", "both quotes on their pages: unchanged, one request per page after robots.txt")
        del req["network"]["replay"]["exchanges"]["https://docs.example.com/b"]
        _, env = call(req)
        b = [r for r in env["data"]["results"] if r["node"] == "rule.example.b"][0]
        self.assertEqual(b["outcome"], "unreadable")


class Merge(unittest.TestCase):
    def merge(self, name, edit=None):
        req = case("merge.json", name)
        if edit:
            edit(req["input"])
        return call(req)

    def test_a_missing_partial_is_unreadable(self):
        _, env = self.merge("a missing partial makes its unit's facts unreadable, never unchanged")
        b = [r for r in env["data"]["results"] if r["node"] == "rule.example.b"][0]
        self.assertEqual(b["outcome"], "unreadable")

    def test_duplicate_stray_and_unknown_partials_are_refused(self):
        for name in ("two partial results for one unit are refused",
                     "a partial result naming a fact outside its unit is refused",
                     "a partial result for a unit the plan does not have is refused"):
            code, env = self.merge(name)
            self.assertEqual((code, env["ok"]), (1, False), name)

    def test_a_disallowed_robots_record_aborts_even_without_an_aborted_field(self):
        def edit(inp):
            inp["partials"][0]["robots"]["verdict"] = "disallowed"
        _, env = self.merge("every unit answered: one report with the decided result, the partial results and the summed requests", edit)
        self.assertIn("aborted", env["data"])
        self.assertEqual(env["data"]["results"], [])

    def test_two_results_for_one_fact_are_refused(self):
        def edit(inp):
            inp["plan"]["decided"].append(dict(inp["plan"]["decided"][0], node="rule.example.a"))
        code, _ = self.merge("every unit answered: one report with the decided result, the partial results and the summed requests", edit)
        self.assertEqual(code, 1)


class Boundary(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(call({"contract": "1", "verb": "describe", "caps": CAPS})[0], 3)
        self.assertEqual(call({"contract": "0", "verb": "describe"})[0], 2)
        self.assertEqual(call({"contract": "0", "verb": "plan", "caps": CAPS})[0], 2)
        self.assertEqual(call({"contract": "0", "verb": "plan", "caps": CAPS, "input": {}})[0], 2)
        self.assertEqual(call({"contract": "0", "verb": "run", "caps": CAPS, "input": {}})[0], 2)

    def test_output_cap_at_the_byte(self):
        req = plan_request(50, [(f"h{i}.example.com", 20, [(f"rule.example.h{i}", f"https://h{i}.example.com/x", "2026-09-01")])
                                for i in range(1, 4)])
        code, env = call(req)
        size = len(json.dumps(env, ensure_ascii=False, separators=(",", ":")).encode())
        self.assertGreater(size, 1025)  # so that size - 1 is a cap the contract allows
        for cap, want in ((size + 1, 0), (size, 0), (size - 1, 1)):
            req["caps"] = {"timeSeconds": 60, "outputBytes": cap}
            self.assertEqual(call(req)[0], want, cap)


class Cells(unittest.TestCase):
    def test_a_markdown_row_and_an_escaped_pipe(self):
        self.assertEqual(markdown_row("| a | b |"), ["a", "b"])
        self.assertEqual(markdown_row("| a \\| b | c |"), ["a | b", "c"])
        self.assertIsNone(markdown_row("a | b"))
        self.assertIsNone(markdown_row("|"))
        self.assertEqual(quote_form("plain | text"), {"text": "plain | text"})

    def test_cells_match_whole_and_consecutive_in_one_row(self):
        rows = [["Free", "20", "5"], ["Pro", "40", "5"]]
        self.assertTrue(has_cells(rows, ["Free", "20"]))
        self.assertTrue(has_cells(rows, ["20", "5"]))
        self.assertFalse(has_cells(rows, ["Free", "5"]))  # not next to each other
        self.assertFalse(has_cells(rows, ["Fre", "20"]))  # a cell is compared whole
        self.assertFalse(has_cells(rows, ["5", "Pro"]))  # not across rows
        self.assertFalse(has_cells(rows, ["", ""]))  # an empty quote is never found
        self.assertFalse(has_cells([["", ""], ["a", ""]], ["", ""]))  # not even next to empty cells


class Conformance(unittest.TestCase):
    """The runner, without a validator (CI runs it with one): the real plugin passes every case; the naive
    one fails exactly the design-review cases and the table case."""

    def run_against(self, *plugin_cmd):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "conformance", "run.py"), "--plugin", *plugin_cmd,
                              "--report-schema", os.devnull], capture_output=True, text=True, cwd=ROOT)
        return out.returncode, [l for l in out.stdout.splitlines() if l.startswith("FAIL")]

    def test_the_real_plugin_passes(self):
        code, fails = self.run_against(os.path.join(ROOT, "bin", "fact-refresh-plugin"))
        self.assertEqual((code, fails), (0, []))

    def test_a_naive_extractor_fails_the_design_review_cases(self):
        code, fails = self.run_against(sys.executable, os.path.join(ROOT, "tests", "fixtures", "plugins", "naive.py"))
        self.assertEqual(code, 1)
        self.assertEqual(sum("run-unit-design-review.json" in f for f in fails), 12)
        self.assertEqual(len(fails), 13, fails)
