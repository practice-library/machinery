#!/usr/bin/env python3
"""Black-box conformance run: sends every case in conformance/cases to a plugin executable, one process per
request, and checks what comes back. Standard library only; JSON Schema validation uses the sourcemeta
jsonschema CLI when --validator is given.

  python3 conformance/run.py --plugin /abs/path/to/plugin --report-schema <refresh-report schema.json> \
      [--validator jsonschema] [--cases conformance/cases]

Every answer is checked for: the exit code the case expects; one JSON envelope on stdout within
caps.outputBytes, in caps.timeSeconds; with --validator, the envelope and the verb's data against the
contract schemas (merge's data against the pinned report schema) and every request against the request
schema; the case's expected data (a subset: keys not named are not compared); and these invariants:
  plan     budgets add up to at most the policy's maxRequestsPerRun, every unit has its own shard key,
           maxParallel is at most 3;
  run-unit requests are at most the unit's budget, results only name the unit's facts;
  merge    every fact of the plan is in the report or the report is aborted, no fact is unchanged without
           a partial result that says so.
Exit 0 when every case passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(os.path.dirname(HERE), "contract", "v0")
FACT = "https://github.com/practice-library/machinery/contract/v0/fact-refresh.schema.json"
INPUT_DEF = {"plan": "planInput", "run-unit": "unit", "merge": "mergeInput"}
DATA_DEF = {"plan": "plan", "run-unit": "partial"}


def deep_merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def matches(actual, expected, path="data"):
    """None when actual has everything expected names; otherwise where they differ."""
    if isinstance(expected, dict) and set(expected) == {"$startsWith"}:
        if isinstance(actual, str) and actual.startswith(expected["$startsWith"]):
            return None
        return f"{path}: {actual!r} does not start with {expected['$startsWith']!r}"
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path}: expected an object, got {type(actual).__name__}"
        for k, v in expected.items():
            if v == {"$absent": True}:
                if k in actual:
                    return f"{path}.{k}: expected absent"
                continue
            if k not in actual:
                return f"{path}.{k}: missing"
            why = matches(actual[k], v, f"{path}.{k}")
            if why:
                return why
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return f"{path}: expected {len(expected)} items, got {len(actual) if isinstance(actual, list) else actual!r}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            why = matches(a, e, f"{path}[{i}]")
            if why:
                return why
        return None
    return None if actual == expected else f"{path}: expected {expected!r}, got {actual!r}"


class Validator:
    def __init__(self, cli, report_schema):
        self.cli, self.report = cli, report_schema
        self.tmp = tempfile.mkdtemp()

    def check(self, schema_ref, instance):
        """None when valid; otherwise the validator's complaint."""
        if not self.cli:
            return None
        schema = os.path.join(self.tmp, "schema.json")
        data = os.path.join(self.tmp, "instance.json")
        with open(schema, "w") as f:
            json.dump({"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": schema_ref}, f)
        with open(data, "w") as f:
            json.dump(instance, f)
        cmd = [self.cli, "validate", schema, data, "--resolve", CONTRACT, "--resolve", self.report]
        out = subprocess.run(cmd, capture_output=True, text=True)
        return None if out.returncode == 0 else (out.stdout + out.stderr).strip()[-600:]


def invariants(verb, request, data):
    inp = request.get("input", {})
    if verb == "plan":
        units = data["units"]
        if sum(u["budget"]["requests"] for u in units) > inp["policy"]["maxRequestsPerRun"]:
            return "plan: budgets add up to more than maxRequestsPerRun"
        keys = [u["shardKey"] for u in units]
        if len(set(keys)) != len(keys):
            return "plan: two units share a shard key"
        if data["maxParallel"] > 3:
            return "plan: maxParallel above 3"
    elif verb == "run-unit":
        if data["requests"] > inp["budget"]["requests"]:
            return f"run-unit: {data['requests']} requests, budget {inp['budget']['requests']}"
        allowed = {f["node"] for f in inp["facts"]}
        stray = [r["node"] for r in data["results"] if r["node"] not in allowed]
        if stray:
            return f"run-unit: results for facts outside the unit: {stray[:3]}"
    elif verb == "merge":
        if "aborted" in data:
            return None
        plan = inp["plan"]
        reported = {r["node"]: r for r in data["results"]}
        said = {r["node"]: r for p in inp["partials"] for r in p["results"]}
        for u in plan["units"]:
            for f in u["facts"]:
                done = any(p["unit"] == u["id"] for p in inp["partials"])
                if not done and f["node"] not in reported:
                    return f"merge: {f['node']} of the missing unit {u['id']} is not in the report"
                r = reported.get(f["node"])
                if r and r["outcome"] == "unchanged" and said.get(f["node"], {}).get("outcome") != "unchanged":
                    return f"merge: {f['node']} is unchanged without a partial result saying so"
    return None


def run_case(plugin, validator, verb, case, defaults):
    request = deep_merge(defaults, case.get("request", {})) if "stdin" not in case else None
    stdin = case["stdin"] if "stdin" in case else json.dumps(request)
    expect = case.get("expect", {})
    if request is not None and not case.get("invalidRequest"):
        why = validator.check("https://github.com/practice-library/machinery/contract/v0/request.schema.json", request)
        if not why and verb in INPUT_DEF:
            why = validator.check(f"{FACT}#/$defs/{INPUT_DEF[verb]}", request["input"])
        if why:
            return f"the case's own request is not valid: {why}"
    caps = (request or {}).get("caps", {"timeSeconds": 30, "outputBytes": 1 << 20})
    try:
        out = subprocess.run(plugin, input=stdin.encode(), capture_output=True, timeout=caps["timeSeconds"] + 5)
    except subprocess.TimeoutExpired:
        return f"no answer within timeSeconds ({caps['timeSeconds']})"
    if out.returncode != expect.get("exit", 0):
        return f"exit {out.returncode}, expected {expect.get('exit', 0)}; stderr: {out.stderr.decode()[-300:]}"
    if len(out.stdout.rstrip(b"\n")) > caps["outputBytes"]:
        return f"stdout is {len(out.stdout)} bytes, over outputBytes ({caps['outputBytes']})"
    try:
        envelope = json.loads(out.stdout)
    except ValueError:
        return f"stdout is not one JSON object: {out.stdout[:200]!r}"
    why = validator.check("https://github.com/practice-library/machinery/contract/v0/envelope.schema.json", envelope)
    if why:
        return f"envelope: {why}"
    if envelope.get("ok") != expect.get("ok", True):
        return f"ok is {envelope.get('ok')}, expected {expect.get('ok', True)}; warnings: {envelope.get('warnings')}"
    if envelope["ok"]:
        data = envelope["data"]
        if verb == "describe":
            ref = "https://github.com/practice-library/machinery/contract/v0/describe.schema.json"
        elif verb == "merge":
            ref = "https://github.com/practice-library/graphs/spec/refresh-report/schema.json"
        else:
            ref = f"{FACT}#/$defs/{DATA_DEF[verb]}"
        why = validator.check(ref, data) or invariants(verb, request, data)
        if why:
            return why
    for key in ("data", "notRead"):
        if key in expect:
            why = matches(envelope[key], expect[key], key)
            if why:
                return why
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plugin", required=True, nargs="+", help="the plugin command (absolute path first)")
    ap.add_argument("--report-schema", required=True, help="the refresh-report schema at the pin in contract/pins.json")
    ap.add_argument("--validator", help="the sourcemeta jsonschema CLI; without it, schemas are not checked")
    ap.add_argument("--cases", default=os.path.join(HERE, "cases"))
    a = ap.parse_args(argv)
    validator = Validator(a.validator, a.report_schema)
    if not a.validator:
        print("warning: no --validator: schemas are not checked, only exit codes, expectations and invariants")
    passed = failed = 0
    for name in sorted(os.listdir(a.cases)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(a.cases, name), encoding="utf-8") as f:
            group = json.load(f)
        for case in group["cases"]:
            verb = case.get("verb", group["verb"])
            why = run_case(a.plugin, validator, verb, case, group.get("defaults", {}))
            if why:
                failed += 1
                print(f"FAIL {name}: {case['name']}: {why}")
            else:
                passed += 1
                print(f"ok   {name}: {case['name']}")
    print(f"{passed} of {passed + failed} cases pass")
    return 0 if failed == 0 and passed > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
