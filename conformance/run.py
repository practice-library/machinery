#!/usr/bin/env python3
"""Black-box conformance run: sends every case in conformance/cases to a plugin executable, one process per
request, and checks what comes back. Standard library only; JSON Schema validation uses the sourcemeta
jsonschema CLI when --validator is given.

  python3 conformance/run.py --plugin /abs/path/to/plugin --report-schema <refresh-report schema.json> \
      [--validator jsonschema] [--cases conformance/cases]

What it checks of every answer, as a black box:
  process  the plugin ends within caps.timeSeconds (plus GRACE_SECONDS for process start); exits 0, 1, 2,
           3 or 75, the code the case expects, and 0 exactly when ok is true; writes at most caps.outputBytes
           to stdout, the newline included, and at most STDERR_BYTES to stderr; stdout is one JSON value
           and one newline, nothing before or after it;
  schemas  with --validator: every request against the request schema, the envelope, and the verb's data
           (merge's against the pinned report schema);
  expected the case's expected data, as a subset: keys a case does not name are not compared;
  plan     budgets add up to at most the policy's maxRequestsPerRun, maxParallel is at most 3;
  run-unit requests are at most the unit's budget; results and notReached name each fact of the unit
           exactly once, unless the unit aborted;
  merge    every fact of the plan is in the report unless a partial says it was not reached or the
           report is aborted; a fact of a unit without a partial is unreadable; no fact is unchanged
           without a partial result that says so;
  describe it answers in the contract version it was asked in, and names each verb once.
What it cannot check: that the plugin reads no files, environment or secrets, and opens no connection.
Those are properties of where it runs (the caller's CI job), not of its answers.
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
EXIT_CODES = (0, 1, 2, 3, 75)  # 75: a temporary failure, the caller may retry
GRACE_SECONDS = 2  # for process start; the plugin itself has caps.timeSeconds
STDERR_BYTES = 65536  # stderr is for people; a flood is a failure
DEFAULT_CAPS = {"timeSeconds": 30, "outputBytes": 1 << 20}
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
        self.rc = None  # the last run's exit code: 0 valid, 2 a finding, anything else an error

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
        self.rc = out.returncode
        return None if out.returncode == 0 else (out.stdout + out.stderr).strip()[-600:]


def invariants(verb, request, data):
    inp = request.get("input", {})
    if verb == "plan":
        units = data["units"]
        if sum(u["budget"]["requests"] for u in units) > inp["policy"]["maxRequestsPerRun"]:
            return "plan: budgets add up to more than maxRequestsPerRun"
        if data["maxParallel"] > 3:
            return "plan: maxParallel above 3"
    elif verb == "run-unit":
        if data["requests"] > inp["budget"]["requests"]:
            return f"run-unit: {data['requests']} requests, budget {inp['budget']['requests']}"
        allowed = [f["node"] for f in inp["facts"]]
        said = [r["node"] for r in data["results"]] + list(data.get("notReached", []))
        stray = [n for n in said if n not in allowed]
        if stray:
            return f"run-unit: results for facts outside the unit: {stray[:3]}"
        if "aborted" not in data and sorted(said) != sorted(allowed):
            return "run-unit: results and notReached do not name each fact of the unit exactly once"
    elif verb == "describe":
        if data.get("protocol") != request.get("contract"):
            return f"describe: answers in contract {data.get('protocol')!r}, asked in {request.get('contract')!r}"
        names = [v.get("name") for v in data.get("verbs", [])]
        if len(set(names)) != len(names):
            return f"describe: a verb is named twice: {names}"
    elif verb == "merge":
        if "aborted" in data:
            return None
        plan = inp["plan"]
        reported = {r["node"]: r for r in data["results"]}
        said = {r["node"]: r for p in inp["partials"] for r in p["results"]}
        waiting = {n for p in inp["partials"] for n in p.get("notReached", [])}
        for n in waiting:
            if n in reported:
                return f"merge: {n} was not reached but is in the report"
        for u in plan["units"]:
            for f in u["facts"]:
                done = any(p["unit"] == u["id"] for p in inp["partials"])
                if not done and f["node"] not in reported:
                    return f"merge: {f['node']} of the missing unit {u['id']} is not in the report"
                if not done and reported[f["node"]]["outcome"] != "unreadable":
                    return f"merge: {f['node']} of the missing unit {u['id']} is {reported[f['node']]['outcome']}, not unreadable"
                if done and f["node"] not in reported and f["node"] not in waiting:
                    return f"merge: {f['node']} is neither in the report nor not reached"
                r = reported.get(f["node"])
                if r and r["outcome"] == "unchanged" and said.get(f["node"], {}).get("outcome") != "unchanged":
                    return f"merge: {f['node']} is unchanged without a partial result saying so"
    return None


def caps_of(request):
    """The caps the plugin was given, as numbers the runner can hold it to; defaults where a deliberately
    invalid request has none it could keep."""
    caps = request.get("caps") if isinstance(request, dict) else None
    caps = caps if isinstance(caps, dict) else {}
    t, b = caps.get("timeSeconds"), caps.get("outputBytes")
    t = t if type(t) is int and t >= 1 else DEFAULT_CAPS["timeSeconds"]
    b = b if type(b) is int else DEFAULT_CAPS["outputBytes"]
    return t, b


def run_process(plugin, stdin: bytes, time_cap: int, out_cap: int):
    """(exit code, stdout, stderr, None) or (.., why it failed). stdout and stderr go to files, so a flood
    costs disk for at most the time cap, never memory; only their caps plus one byte are read back."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(plugin, stdin=subprocess.PIPE, stdout=out, stderr=err)
        try:
            proc.communicate(stdin, timeout=time_cap + GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return None, b"", b"", f"no answer within timeSeconds ({time_cap})"
        except BrokenPipeError:  # it exited without reading its request
            proc.wait()
        sizes = (out.seek(0, os.SEEK_END), err.seek(0, os.SEEK_END))
        if sizes[0] > out_cap:
            return proc.returncode, b"", b"", f"stdout is {sizes[0]} bytes, over outputBytes ({out_cap})"
        if sizes[1] > STDERR_BYTES:
            return proc.returncode, b"", b"", f"stderr is {sizes[1]} bytes, over {STDERR_BYTES}"
        out.seek(0)
        err.seek(0)
        return proc.returncode, out.read(out_cap + 1), err.read(STDERR_BYTES + 1), None


def one_json_line(stdout: bytes):
    """(the value, None) when stdout is exactly one JSON value and one newline; otherwise (None, why)."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None, "stdout is not UTF-8"
    if not text.endswith("\n"):
        return None, f"stdout does not end with one newline: {text[-80:]!r}"
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None, f"stdout does not start with a JSON value: {text[:200]!r}"
    if text[end:] != "\n":
        return None, f"stdout has more after its JSON value: {text[end:end + 80]!r}"
    return value, None


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
    time_cap, out_cap = caps_of(request)
    code, stdout, stderr, why = run_process(plugin, stdin.encode("utf-8"), time_cap, out_cap)
    if why:
        return why
    if code not in EXIT_CODES:
        return f"exit {code}, not one of {EXIT_CODES}; stderr: {stderr[-300:].decode('utf-8', 'replace')}"
    if code != expect.get("exit", 0):
        return f"exit {code}, expected {expect.get('exit', 0)}; stderr: {stderr[-300:].decode('utf-8', 'replace')}"
    envelope, why = one_json_line(stdout)
    if why:
        return why
    why = validator.check("https://github.com/practice-library/machinery/contract/v0/envelope.schema.json", envelope)
    if why:
        return f"envelope: {why}"
    if not isinstance(envelope, dict) or envelope.get("ok") is not (code == 0):
        return f"exit {code} with ok {envelope.get('ok') if isinstance(envelope, dict) else envelope!r}: ok is true exactly on exit 0"
    if envelope.get("ok") != expect.get("ok", True):
        return f"ok is {envelope.get('ok')}, expected {expect.get('ok', True)}; warnings: {envelope.get('warnings')}"
    if envelope["ok"]:
        data = envelope["data"]
        if verb == "describe":
            ref = "https://github.com/practice-library/machinery/contract/v0/describe.schema.json"
        elif verb == "merge":
            ref = "https://github.com/practice-library/.github/spec/refresh-report/schema.json"
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
