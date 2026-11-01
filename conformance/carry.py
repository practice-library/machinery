#!/usr/bin/env python3
"""Carries contract v0's conformance cases (conformance/cases) over to contract v1alpha1's Connect JSON
fixtures (conformance/connect). Standard library only.

  python3 conformance/carry.py           # writes conformance/connect/*.json
  python3 conformance/carry.py --check   # fails unless the files are exactly what it writes (CI)

Every v0 case is carried, by its name in "carries", or listed in "notCarried" with the reason; cases that
v1alpha1 adds (ADDED) are marked "added". A case is
carried as it is unless CHANGED below says how its call changes:
  - the request loses contract and verb: the call's path names them;
  - "contract/v0/" in any string becomes "contract/v1alpha1/";
  - the exit code becomes a Connect code (EXIT_CODE), and ok, data and notRead become the expected envelope,
    matched against the response or, on an error, against the decoded Failure detail.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V0, OUT = os.path.join(HERE, "cases"), os.path.join(HERE, "connect")
PACKAGE = "machinery.plugin.v1alpha1"
SERVICE = f"{PACKAGE}.PluginService"
CALL = {"describe": "Describe", "plan": "Plan", "run-unit": "RunUnit", "merge": "Merge", "run": "Run"}
EXIT_CODE = {0: "ok", 1: "failed_precondition", 2: "invalid_argument", 3: "unimplemented", 75: "unavailable"}
CAPS = {"timeSeconds": 60, "outputBytes": 1048576}

# v0 cases whose call changes beyond the mechanical carry, each with the case it becomes, or with why it is
# not carried. Keys are v0 case names.
CHANGED = {
    "describe names contract 0 and the three shardable verbs, each with its schemas": {
        "name": "describe names its package and the three shardable verbs, each with its schemas",
        "expect": {"code": "ok", "envelope": {"ok": True, "data": {"protocol": PACKAGE, "contracts": [PACKAGE]}}},
        "keepData": True,
    },
    "a contract version the plugin does not speak is exit 3": {
        "name": "a package the plugin does not serve is unimplemented",
        "procedure": "machinery.plugin.v1alpha99.PluginService/Describe",
        "request": {}, "expect": {"code": "unimplemented"},
    },
    "an unknown verb is exit 2": {
        "name": "an unknown call is unimplemented (v0: exit 2)",
        "procedure": f"{SERVICE}/FetchEverything",
        "request": {}, "expect": {"code": "unimplemented"},
    },
    "stdin that is not JSON is exit 2": {
        "name": "a body that is not JSON is invalid_argument",
        "rawBody": "not json", "expect": {"code": "invalid_argument"},
    },
    "a request without caps is exit 2": {
        "name": "a request without caps is invalid_argument",
        "rawBody": "{}", "expect": {"code": "invalid_argument"},
    },
    "an answer larger than outputBytes is exit 1, not a cut answer": {
        "name": "an answer larger than outputBytes is resource_exhausted, not a cut answer",
        "expect": {"code": "resource_exhausted", "envelope": {"ok": False}},
    },
    "a contract that is not digits is exit 2": {
        "notCarried": "the request names no contract: the package in the call's path is the version, and a package "
                      "the plugin does not serve is the unimplemented case",
    },
    "a key given twice is exit 2": {
        "name": "a key given twice is invalid_argument",
        "rawBody": '{"caps": {"timeSeconds": 60, "timeSeconds": 61, "outputBytes": 1048576}}',
        "expect": {"code": "invalid_argument"},
    },
    "NaN is not JSON: exit 2": {
        "name": "NaN is not JSON: invalid_argument",
        "rawBody": '{"caps": {"timeSeconds": NaN, "outputBytes": 1048576}}',
        "expect": {"code": "invalid_argument"},
    },
    "a lone surrogate escaped in a string is exit 2, with an envelope": {
        "name": "a lone surrogate escaped in a string is invalid_argument",
        "procedure": f"{SERVICE}/Plan",
        "rawBody": '{"caps": {"timeSeconds": 60, "outputBytes": 1048576}, "input": {"graph": "\\ud800"}}',
        "expect": {"code": "invalid_argument"},
    },
}


# Cases v1alpha1 adds, by v0 file: rules the move to proto's JSON mapping makes worth a case of their own.
# Proto cannot tell an empty hosts or exchanges from a missing one, so these forms must work as their
# full forms do (contract/v1alpha1/request.schema.json).
def _unit_input():
    with open(os.path.join(V0, "run-unit-safety.json"), encoding="utf-8") as f:
        return json.load(f)["defaults"]["input"]


_ROBOTS_429 = {"https://docs.example.com/robots.txt": [{"status": 429, "headers": {"retry-after": "60"}, "body": ""}]}
ADDED = {
    "describe.json": [{
        "name": "network {} on RunUnit is an empty hosts: the unit's host is not listed, invalid_argument from the plugin",
        "procedure": f"{SERVICE}/RunUnit",
        "request": {"network": {}, "input": "UNIT"},
        "expect": {"code": "invalid_argument", "envelope": {"ok": False, "warnings": [{"$startsWith": "the unit's host"}]}},
    }, {
        "name": "replay without exchanges: every URL is a network error, so the unit aborts",
        "procedure": f"{SERVICE}/RunUnit",
        "request": {"network": {"replay": {"start": "2026-10-31T12:00:00Z"}}, "input": "UNIT"},
        "expect": {"code": "ok", "envelope": {"ok": True, "data": {"results": []}}},
    }],
    "run-unit-safety.json": [{
        "name": "replay with an empty hosts is replay: robots.txt 429 is unavailable",
        "request": {"network": {"hosts": [], "replay": {"exchanges": _ROBOTS_429}}},
        "expect": {"code": "unavailable", "envelope": {"ok": False}},
    }, {
        "name": "a recorded status that is not an integer is invalid_argument (a ListValue item is a double)",
        "request": {"network": {"replay": {"exchanges": {"https://docs.example.com/robots.txt": [{"status": 200.5}]}}}},
        "expect": {"code": "invalid_argument"},
    }],
}


def v1(value):
    """contract/v0/ to contract/v1alpha1/ in every string of a JSON value."""
    if isinstance(value, str):
        return value.replace("/contract/v0/", "/contract/v1alpha1/")
    if isinstance(value, list):
        return [v1(v) for v in value]
    if isinstance(value, dict):
        return {k: v1(v) for k, v in value.items()}
    return value


def frame(request):
    return v1({k: v for k, v in request.items() if k not in ("contract", "verb")})


def expect_of(v0_expect):
    code = EXIT_CODE[v0_expect.get("exit", 0)]
    envelope = {"ok": v0_expect.get("ok", code == "ok")}
    for key in ("data", "notRead"):
        if key in v0_expect:
            envelope[key] = v1(v0_expect[key])
    return {"code": code, "envelope": envelope}


def carry_group(group, source):
    verb = group["verb"]
    out = {
        "carriedFrom": f"conformance/cases/{source}",
        "procedure": f"{SERVICE}/{CALL[verb]}",
        "defaults": frame(group.get("defaults", {})),
        "cases": [],
        "notCarried": [],
    }
    if "about" in group:
        out = {"about": group["about"], **out}
    for case in group["cases"]:
        change = CHANGED.get(case["name"], {})
        if "notCarried" in change:
            out["notCarried"].append({"name": case["name"], "why": change["notCarried"]})
            continue
        name = re.sub(r"\bexit (\d+)\b", lambda m: EXIT_CODE[int(m.group(1))], case["name"])
        c = {"name": change.get("name", name), "carries": case["name"]}
        call = case.get("verb", verb)
        procedure = change.get("procedure", f"{SERVICE}/{CALL[call]}")
        if procedure != out["procedure"]:
            c["procedure"] = procedure
        if "rawBody" in change:
            c["rawBody"] = change["rawBody"]
        elif "request" in change:
            c["request"] = change["request"]
        elif "stdin" in case:
            raise SystemExit(f"{source}: {case['name']!r} sends raw stdin; say in CHANGED what it becomes")
        else:
            c["request"] = frame(case.get("request", {}))
        if "about" in case:
            c["about"] = case["about"]
        expect = expect_of(case.get("expect", {}))
        if "expect" in change:
            if change.get("keepData"):
                data = dict(expect["envelope"].get("data", {}), **change["expect"]["envelope"]["data"])
                expect = {"code": change["expect"]["code"], "envelope": dict(change["expect"]["envelope"], data=data)}
            else:
                expect = change["expect"]
        c["expect"] = expect
        out["cases"].append(c)
    for case in ADDED.get(source, []):
        case = json.loads(json.dumps(case))
        if case.get("request", {}).get("input") == "UNIT":
            case["request"]["input"] = _unit_input()
        out["cases"].append(dict(case, added=True))
    if not out["notCarried"]:
        del out["notCarried"]
    return out


def render():
    files = {}
    seen = set()
    for name in sorted(os.listdir(V0)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(V0, name), encoding="utf-8") as f:
            group = json.load(f)
        carried = carry_group(group, name)
        files[name] = json.dumps(carried, indent=2, ensure_ascii=False) + "\n"
        seen |= {c["name"] for c in group["cases"]}
    unused = set(CHANGED) - seen
    if unused:
        raise SystemExit(f"CHANGED names cases that do not exist: {sorted(unused)}")
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail unless conformance/connect is exactly what this writes")
    a = ap.parse_args(argv)
    files = render()
    if a.check:
        stale = []
        for name, text in files.items():
            path = os.path.join(OUT, name)
            if not os.path.exists(path) or open(path, encoding="utf-8").read() != text:
                stale.append(name)
        extra = sorted(set(n for n in os.listdir(OUT) if n.endswith(".json")) - set(files)) if os.path.isdir(OUT) else []
        for name in stale + extra:
            print(f"::error file=conformance/connect/{name}::not what conformance/carry.py writes; run it and commit")
        groups = [json.loads(t) for t in files.values()]
        total = sum(len([c for c in g["cases"] if "carries" in c]) + len(g.get("notCarried", [])) for g in groups)
        added = sum(len([c for c in g["cases"] if c.get("added")]) for g in groups)
        print(f"{len(files)} files, {total} v0 cases accounted for, {added} added" + ("" if not stale + extra else "; stale"))
        return 1 if stale or extra else 0
    os.makedirs(OUT, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(text)
    print(f"wrote {len(files)} files to conformance/connect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
