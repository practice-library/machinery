"""The plugin's verbs called in process, each rule with literal values; and the conformance runner, which must
pass the real plugin and fail one that extracts text naively."""
from __future__ import annotations

import copy
import io
import itertools
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


def raw(stdin: bytes):
    out = io.BytesIO()
    code = plugin.main(io.BytesIO(stdin), out)
    return code, out.getvalue()


def call(request):
    code, out = raw(json.dumps(request).encode())
    return code, json.loads(out)


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

    def test_facts_not_reached_are_named(self):
        self.assertEqual(self.unit(39)["notReached"], ["rule.example.p2"])  # robots.txt and 2 pages
        self.assertEqual(self.unit(38)["notReached"], ["rule.example.p1", "rule.example.p2"])
        self.assertEqual(self.unit(39, n_pages=2)["notReached"], [])
        self.assertEqual(self.unit(34)["notReached"], ["rule.example.p0", "rule.example.p1", "rule.example.p2"])

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
        for name, why in (("two partial results for one unit are refused", "two partial results for host:docs.example.com"),
                          ("a partial result naming a fact outside its unit is refused", "has facts outside its unit: rule.example.b"),
                          ("a partial result for a unit the plan does not have is refused", "which is not a unit of the plan")):
            code, env = self.merge(name)
            self.assertEqual((code, env["ok"]), (1, False), name)
            self.assertIn(why, env["warnings"][0], name)

    def test_a_disallowed_robots_record_aborts_even_without_an_aborted_field(self):
        def edit(inp):
            inp["partials"][0]["robots"]["verdict"] = "disallowed"
        _, env = self.merge("every unit answered: one report with the decided result, the partial results and the summed requests", edit)
        self.assertIn("aborted", env["data"])
        self.assertEqual(env["data"]["results"], [])

    COMPLETE = "every unit answered: one report with the decided result, the partial results and the summed requests"

    def two_fact_unit(self, inp):
        u = inp["plan"]["units"][0]
        u["facts"].append(dict(u["facts"][0], node="rule.example.a2", source="https://docs.example.com/a2"))

    def test_the_report_does_not_depend_on_the_order_of_the_partials(self):
        base = case("merge.json", self.COMPLETE)
        p1, p2 = base["input"]["partials"]
        aborted = [dict(p, results=[], aborted={"reason": f"blocked: robots.txt of {p['shardKey']} disallows /"},
                        robots=dict(p["robots"], verdict="disallowed")) for p in (p1, p2)]
        unreachable = [dict(p, robots=dict(p["robots"], verdict="unreachable")) for p in (p1, p2)]
        for parts in ([p1, p2], aborted, unreachable, [aborted[0], unreachable[1]]):
            outs = set()
            for order in itertools.permutations(parts):
                req = copy.deepcopy(base)
                req["input"]["partials"] = list(order)
                outs.add(raw(json.dumps(req).encode()))
            self.assertEqual(len(outs), 1, parts)
        req = copy.deepcopy(base)
        req["input"]["partials"] = [aborted[1], unreachable[0]]
        _, env = call(req)
        self.assertEqual(env["data"]["aborted"]["reason"],
                         "robots.txt of docs.example.com is unreachable; blocked: robots.txt of help.example.com disallows /")

    def test_a_partial_that_leaves_out_a_fact_is_refused(self):
        code, env = self.merge(self.COMPLETE, self.two_fact_unit)
        self.assertEqual(code, 1)
        self.assertIn("leaves out facts of its unit: rule.example.a2", env["warnings"][0])

    def test_a_fact_not_reached_is_left_out_of_the_report(self):
        def edit(inp):
            self.two_fact_unit(inp)
            inp["partials"][0]["notReached"] = ["rule.example.a2"]
        code, env = self.merge(self.COMPLETE, edit)
        self.assertEqual(code, 0)
        self.assertNotIn("rule.example.a2", [r["node"] for r in env["data"]["results"]])

    def test_an_aborted_partial_need_not_account_for_its_facts(self):
        _, env = self.merge("a unit aborted on robots.txt aborts the whole report")
        self.assertIn("aborted", env["data"])

    def test_a_fact_both_reached_and_not_reached_is_refused(self):
        def edit(inp):
            inp["partials"][0]["notReached"] = ["rule.example.a"]
        code, env = self.merge(self.COMPLETE, edit)
        self.assertEqual(code, 1)
        self.assertIn("names a fact twice", env["warnings"][0])

    def test_a_fact_in_no_unit_is_refused(self):
        def edit(inp):
            r = inp["partials"][0]["results"][0]
            inp["partials"][0]["results"].append(dict(r, node="rule.example.nowhere"))
        code, env = self.merge(self.COMPLETE, edit)
        self.assertEqual(code, 1)
        self.assertIn("outside its unit: rule.example.nowhere", env["warnings"][0])

    def test_a_not_reached_fact_in_no_unit_is_refused(self):
        def edit(inp):
            inp["partials"][0]["notReached"] = ["rule.example.nowhere"]
        self.assertEqual(self.merge(self.COMPLETE, edit)[0], 1)

    def test_a_partial_with_another_shard_key_or_robots_host_is_refused(self):
        def key(inp):
            inp["partials"][0]["shardKey"] = "help.example.com"
        def robots(inp):
            inp["partials"][0]["robots"] = dict(inp["partials"][1]["robots"])
        code, env = self.merge(self.COMPLETE, key)
        self.assertEqual(code, 1)
        self.assertIn("has shard key help.example.com", env["warnings"][0])
        code, env = self.merge(self.COMPLETE, robots)
        self.assertEqual(code, 1)
        self.assertIn("has robots.txt of help.example.com", env["warnings"][0])

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
        self.assertEqual(len(raw(json.dumps(req).encode())[1]), 1578)  # the answer and its newline
        for cap, want in ((1579, 0), (1578, 0), (1577, 1)):
            req["caps"] = {"timeSeconds": 60, "outputBytes": cap}
            code, out = raw(json.dumps(req).encode())
            self.assertEqual(code, want, cap)
            self.assertLessEqual(len(out), cap)

    def describe(self, **over):
        req = {"contract": "0", "verb": "describe", "caps": dict(CAPS)}
        req.update(over)
        return raw(json.dumps(req).encode())

    def test_a_refusal_fits_the_cap_it_was_given(self):
        # the smallest failure envelope, a 1-character warning and the newline, is 55 bytes
        for cap, length in ((100, 46), (56, 2), (55, 1)):
            code, out = self.describe(caps={"timeSeconds": 60, "outputBytes": cap})
            self.assertEqual(code, 2)
            self.assertEqual(len(out), 54 + length)  # every byte up to the cap is used
            self.assertTrue(out.endswith(b"\n"))
            self.assertEqual(len(json.loads(out)["warnings"][0]), length, cap)
        self.assertEqual(self.describe(caps={"timeSeconds": 60, "outputBytes": 54}), (2, b""))
        self.assertEqual(self.describe(caps={"timeSeconds": 60, "outputBytes": -1}), (2, b""))

    def test_caps_are_integers_in_the_schema_range(self):
        for caps, want in (({"timeSeconds": True, "outputBytes": 1048576}, 2), ({"timeSeconds": 60, "outputBytes": True}, 2),
                           ({"timeSeconds": 0, "outputBytes": 1048576}, 2), ({"timeSeconds": 1, "outputBytes": 1048576}, 0),
                           ({"timeSeconds": -1, "outputBytes": 1048576}, 2),
                           ({"timeSeconds": 3600, "outputBytes": 1048576}, 0), ({"timeSeconds": 3601, "outputBytes": 1048576}, 2),
                           ({"timeSeconds": 60, "outputBytes": 1023}, 2), ({"timeSeconds": 60, "outputBytes": 1024}, 0),
                           ({"timeSeconds": 60, "outputBytes": 16777216}, 0), ({"timeSeconds": 60, "outputBytes": 16777217}, 2),
                           ({"timeSeconds": 60.0, "outputBytes": 1048576}, 0), ({"timeSeconds": 60.5, "outputBytes": 1048576}, 2),
                           ({"timeSeconds": "60", "outputBytes": 1048576}, 2), ({"timeSeconds": 60}, 2),
                           ({"timeSeconds": 60, "outputBytes": 1048576, "memory": 1}, 2)):
            self.assertEqual(self.describe(caps=caps)[0], want, caps)

    def test_the_request_has_only_the_schemas_keys(self):
        self.assertEqual(self.describe()[0], 0)
        self.assertEqual(self.describe(input={})[0], 2)
        self.assertEqual(self.describe(debug=True)[0], 2)
        for contract, want in (("0", 0), ("1", 3), ("01", 3), ("0\n", 2), (" 0", 2), ("v0", 2), ("", 2), (0, 2)):
            self.assertEqual(self.describe(contract=contract)[0], want, repr(contract))
        self.assertEqual(self.describe(contract="1", debug=True)[0], 3)  # a newer version may have keys v0 lacks
        code, out = raw(json.dumps({"contract": "0", "verb": "plan", "caps": CAPS, "input": []}).encode())
        self.assertEqual(code, 2)
        self.assertIn("plan needs an input object", json.loads(out)["warnings"][0])

    def test_network_matches_the_schema(self):
        ok = {"status": 200, "headers": {"a": "b"}, "body": "x"}
        replay = lambda *answers: {"replay": {"start": START, "exchanges": {"https://a.example.com/": list(answers)}}}
        for network, want in (({"hosts": ["docs.example.com"]}, 0), ({"hosts": []}, 0), ({}, 2),
                              ({"hosts": ["Docs.example.com"]}, 2), ({"hosts": ["localhost"]}, 2), ({"hosts": "docs.example.com"}, 2),
                              ({"hosts": ["docs.example.com/x"]}, 2), ({"hosts": {"docs.example.com": 1}}, 2),
                              ({"replay": {"start": START, "exchanges": {}, "speed": 2}}, 2), ({"replay": {"start": START, "exchanges": []}}, 2),
                              ({"hosts": [], "replay": replay(ok)["replay"]}, 2), (replay(ok), 0), (replay({"error": "reset"}), 0),
                              (replay({"status": 200, "error": "reset"}), 2), (replay({"body": "x"}), 2), (replay(dict(ok, status=True)), 2),
                              (replay(dict(ok, status=99)), 2), (replay(dict(ok, status=100)), 0), (replay(dict(ok, status=599)), 0),
                              (replay(dict(ok, status=600)), 2), (replay({"error": ""}), 2), (replay(dict(ok, body=1)), 2),
                              (replay(dict(ok, headers={"a": 1})), 2), (replay(dict(ok, extra=1)), 2),
                              ({"replay": {"start": "2026-10-31 12:00:00", "exchanges": {}}}, 2),
                              ({"replay": {"start": START + "\n", "exchanges": {}}}, 2),
                              ({"replay": {"start": START, "exchanges": {"u": {}}}}, 2), ({"replay": {"start": START}}, 2)):
            self.assertEqual(self.describe(network=network)[0], want, network)

    def test_unreadable_stdin_is_a_bad_request(self):
        good = b'{"contract": "0", "verb": "describe", "caps": {"timeSeconds": 60, "outputBytes": 1048576}}'
        self.assertEqual(raw(good)[0], 0)
        for stdin, why in ((b"\xff" + good, "not JSON"), (good.replace(b'"describe"', b'"d\xe9scribe"'), "not JSON"),
                           (b"[" * 100000 + b"]" * 100000, "nested too deeply"),
                           (good.replace(b'"verb": "describe"', b'"verb": "plan", "verb": "describe"'), "'verb' twice"),
                           (good.replace(b"60", b"NaN"), "NaN is not JSON"), (good.replace(b"60", b"-Infinity"), "-Infinity is not JSON"),
                           (good.replace(b"60", b"1" * 5000), ""), (b"", "not JSON"), (b"   ", "not JSON"), (b"[]", "not a JSON object"),
                           (b"null", "not a JSON object"), (good + b"{}", "not JSON")):
            code, out = raw(stdin)
            self.assertEqual(code, 2, stdin[:60])
            env = json.loads(out)
            self.assertFalse(env["ok"])
            self.assertIn(why, env["warnings"][0], stdin[:60])

    def test_the_executable_reads_bytes_so_bad_utf8_is_exit_2(self):
        out = subprocess.run([os.path.join(ROOT, "bin", "fact-refresh-plugin")], input=b"\xff{}", capture_output=True)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertFalse(json.loads(out.stdout)["ok"])

    def test_describe_answers_in_the_contract_it_was_asked_in(self):
        _, env = call({"contract": "0", "verb": "describe", "caps": CAPS})
        self.assertEqual(env["data"]["contract"], "0")


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
