"""The runtime fixtures (conformance/runtime): well formed for the Connect runner, and matching their README.

machinery has no runtime of its own, so these fixtures run in each runtime's repository. Here they're
checked as data: each file loads the way conformance/connect_run.py loads it, every case names a code the
runner knows, and the counts in the README are the counts in the files.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "conformance"))

import connect_run  # noqa: E402

RUNTIME = os.path.join(ROOT, "conformance", "runtime")
CALLS = {"describe.json": "Describe", "plan.json": "Plan", "run-unit.json": "RunUnit", "merge.json": "Merge"}


def groups():
    for name in sorted(os.listdir(RUNTIME)):
        if name.endswith(".json"):
            with open(os.path.join(RUNTIME, name), encoding="utf-8") as f:
                yield name, json.load(f)


class RuntimeFixtures(unittest.TestCase):
    def test_the_four_calls_are_there(self):
        self.assertEqual(sorted(n for n, _ in groups()), sorted(CALLS))

    def test_each_file_names_its_call(self):
        for name, group in groups():
            self.assertEqual(group["procedure"], f"{connect_run.PACKAGE}.PluginService/{CALLS[name]}", name)

    def test_every_case_is_one_the_runner_can_run(self):
        codes = set(connect_run.HTTP_STATUS) | {"ok"}
        for name, group in groups():
            names = [c["name"] for c in group["cases"]]
            self.assertEqual(len(names), len(set(names)), f"{name}: case names repeat")
            for case in group["cases"]:
                self.assertIn(case["expect"]["code"], codes, f"{name}: {case['name']}")
                self.assertFalse("rawBody" in case and "request" in case, f"{name}: {case['name']}")
                if case["expect"]["code"] == "ok":
                    self.assertNotIn("rawBody", case, f"{name}: {case['name']}: a success needs a request")

    def test_the_readme_counts_match_the_files(self):
        with open(os.path.join(RUNTIME, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for name, group in groups():
            row = re.search(rf"^\| `{re.escape(name)}` \| `\w+` \| (\d+) \|", readme, re.M)
            self.assertIsNotNone(row, name)
            self.assertEqual(int(row.group(1)), len(group["cases"]), name)


if __name__ == "__main__":
    unittest.main()
