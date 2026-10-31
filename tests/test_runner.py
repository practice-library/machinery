"""The conformance runner's own checks, each against a plugin that breaks it one way (tests/fixtures/plugins/
bad.py); each check is also mutated in tests/guards.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = os.path.join(ROOT, "tests", "fixtures", "plugins", "bad.py")
sys.path.insert(0, ROOT)

from conformance.run import invariants  # noqa: E402


def run(way, exit_code=0, ok=True):
    """The runner's exit code and output for one describe case answered by bad.py <way>."""
    with tempfile.TemporaryDirectory() as cases:
        with open(os.path.join(cases, "describe.json"), "w") as f:
            json.dump({"verb": "describe",
                       "defaults": {"contract": "0", "verb": "describe", "caps": {"timeSeconds": 1, "outputBytes": 4096}},
                       "cases": [{"name": "describe", "expect": {"exit": exit_code, "ok": ok}}]}, f)
        out = subprocess.run([sys.executable, os.path.join(ROOT, "conformance", "run.py"), "--plugin", sys.executable, BAD, way,
                              "--report-schema", os.devnull, "--cases", cases], capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout


class Runner(unittest.TestCase):
    def fails(self, way, why, **expect):
        code, out = run(way, **expect)
        self.assertEqual(code, 1, out)
        self.assertIn(why, out)

    def test_a_good_plugin_passes(self):
        code, out = run("good")
        self.assertEqual(code, 0, out)

    def test_stdout_at_output_bytes_passes(self):
        self.assertEqual(run("stdout-at-cap")[0], 0)

    def test_stdout_one_byte_over_output_bytes(self):
        self.fails("stdout-one-over", "stdout is 4097 bytes, over outputBytes (4096)")

    def test_stderr_at_its_cap_passes(self):
        self.assertEqual(run("stderr-at-cap")[0], 0)

    def test_stderr_one_byte_over_its_cap(self):
        self.fails("stderr-one-over", "stderr is 65537 bytes, over 65536")

    def test_a_plugin_that_hangs_is_stopped(self):
        self.fails("hang", "no answer within timeSeconds (1)")

    def test_a_cut_off_answer(self):
        self.fails("partial-line", "does not end with one newline")

    def test_an_answer_without_its_newline(self):
        self.fails("no-newline", "does not end with one newline")

    def test_a_line_before_the_answer(self):
        self.fails("line-before", "does not start with a JSON value")

    def test_a_line_after_the_answer(self):
        self.fails("line-after", "has more after its JSON value")

    def test_two_answers(self):
        self.fails("two-answers", "has more after its JSON value")

    def test_an_exit_code_outside_the_four(self):
        self.fails("exit-4", "exit 4, not one of (0, 1, 2, 3, 75)", exit_code=4, ok=False)

    def test_ok_true_on_a_failure_exit(self):
        self.fails("ok-on-failure", "ok is true exactly on exit 0", exit_code=1, ok=True)

    def test_describe_naming_a_verb_twice(self):
        self.fails("verb-twice", "describe: a verb is named twice")

    def test_describe_in_another_contract(self):
        self.fails("wrong-contract", "describe: answers in contract '7', asked in '0'")


class Invariants(unittest.TestCase):
    UNIT = {"budget": {"requests": 3}, "facts": [{"node": "rule.example.a"}, {"node": "rule.example.b"}]}

    def test_plan_budgets_add_up_to_at_most_the_run_cap(self):
        request = {"input": {"policy": {"maxRequestsPerRun": 6}}}
        plan = lambda *budgets: {"maxParallel": 2, "units": [{"budget": {"requests": b}} for b in budgets]}
        self.assertIsNone(invariants("plan", request, plan(3, 2)))  # 5
        self.assertIsNone(invariants("plan", request, plan(3, 3)))  # 6
        self.assertIn("more than maxRequestsPerRun", invariants("plan", request, plan(3, 4)))  # 7

    def test_plan_max_parallel_at_most_3(self):
        request = {"input": {"policy": {"maxRequestsPerRun": 50}}}
        self.assertIsNone(invariants("plan", request, {"maxParallel": 3, "units": []}))
        self.assertIn("maxParallel above 3", invariants("plan", request, {"maxParallel": 4, "units": []}))

    def test_run_unit_requests_at_most_its_budget(self):
        part = {"results": [{"node": "rule.example.a"}], "notReached": ["rule.example.b"]}
        self.assertIsNone(invariants("run-unit", {"input": self.UNIT}, dict(part, requests=3)))
        self.assertIn("4 requests, budget 3", invariants("run-unit", {"input": self.UNIT}, dict(part, requests=4)))

    def missing_unit(self, results):
        unit = dict(self.UNIT, id="host:a.example.com")
        return invariants("merge", {"input": {"plan": {"units": [unit]}, "partials": []}}, {"results": results})

    def test_merge_reports_a_missing_units_facts_as_unreadable(self):
        a, b = {"node": "rule.example.a", "outcome": "unreadable"}, {"node": "rule.example.b", "outcome": "unreadable"}
        self.assertIsNone(self.missing_unit([a, b]))
        self.assertIn("is not in the report", self.missing_unit([a]))
        self.assertIn("is changed, not unreadable", self.missing_unit([a, dict(b, outcome="changed")]))
        self.assertIn("is unchanged, not unreadable", self.missing_unit([a, dict(b, outcome="unchanged")]))

    def test_merge_never_unchanged_without_a_partial_saying_so(self):
        unit = dict(self.UNIT, id="host:a.example.com")
        request = {"input": {"plan": {"units": [unit]}, "partials": [
            {"unit": unit["id"], "results": [{"node": "rule.example.a", "outcome": "changed"},
                                             {"node": "rule.example.b", "outcome": "unchanged"}], "notReached": []}]}}
        ok = [{"node": "rule.example.a", "outcome": "changed"}, {"node": "rule.example.b", "outcome": "unchanged"}]
        self.assertIsNone(invariants("merge", request, {"results": ok}))
        self.assertIn("rule.example.a is unchanged without a partial result saying so",
                      invariants("merge", request, {"results": [dict(ok[0], outcome="unchanged"), ok[1]]}))

    def test_run_unit_names_each_fact_once(self):
        part = {"requests": 2, "results": [{"node": "rule.example.a"}], "notReached": ["rule.example.b"]}
        self.assertIsNone(invariants("run-unit", {"input": self.UNIT}, part))
        self.assertIn("exactly once", invariants("run-unit", {"input": self.UNIT}, dict(part, notReached=[])))
        self.assertIn("exactly once", invariants("run-unit", {"input": self.UNIT},
                                                 dict(part, notReached=["rule.example.a", "rule.example.b"])))
        self.assertIsNone(invariants("run-unit", {"input": self.UNIT}, dict(part, results=[], notReached=[], aborted={"reason": "x"})))

    def merge(self, results, not_reached):
        unit = dict(self.UNIT, id="host:a.example.com")
        request = {"input": {"plan": {"units": [unit]}, "partials": [
            {"unit": unit["id"], "results": [{"node": "rule.example.a", "outcome": "unchanged"}], "notReached": not_reached}]}}
        return invariants("merge", request, {"results": results})

    def test_merge_leaves_out_exactly_the_facts_not_reached(self):
        a, b = {"node": "rule.example.a", "outcome": "unchanged"}, {"node": "rule.example.b", "outcome": "unreadable"}
        self.assertIsNone(self.merge([a], ["rule.example.b"]))
        self.assertIn("was not reached but is in the report", self.merge([a, b], ["rule.example.b"]))
        self.assertIn("neither in the report nor not reached", self.merge([a], []))


class Examples(unittest.TestCase):
    """contract/check_examples.py holds a counter-example only when the validator reports a finding (exit 2),
    not when it fails for another reason."""

    def check(self, validator_exit):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "validator")
            with open(fake, "w") as f:
                f.write(f"#!{sys.executable}\nimport sys\nsys.exit({validator_exit})\n")
            os.chmod(fake, 0o755)
            examples = os.path.join(tmp, "examples")
            os.mkdir(examples)
            with open(os.path.join(examples, "one.json"), "w") as f:
                json.dump({"groups": [{"schema": "https://example.com/s.json", "valid": [],
                                       "invalid": [{"name": "rejected", "instance": {}}]}]}, f)
            out = subprocess.run([sys.executable, os.path.join(ROOT, "contract", "check_examples.py"), "--validator", fake,
                                  "--report-schema", os.devnull, "--examples", examples], capture_output=True, text=True)
        return out.returncode

    def test_a_finding_holds_a_counter_example(self):
        self.assertEqual(self.check(2), 0)

    def test_a_validator_error_does_not(self):
        self.assertEqual(self.check(1), 1)
        self.assertEqual(self.check(3), 1)


if __name__ == "__main__":
    unittest.main()
