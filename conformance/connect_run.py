#!/usr/bin/env python3
"""Black-box conformance run for contract v1alpha1: sends every fixture in conformance/connect to a running
plugin service as Connect unary JSON over HTTP, and checks what comes back. Standard library only; JSON
Schema validation uses the sourcemeta jsonschema CLI when --validator is given.

  python3 conformance/connect_run.py --url http://127.0.0.1:8080 --report-schema <refresh-report schema> \
      [--validator jsonschema] [--fixtures conformance/connect]

Each call is a POST to <url>/<procedure> with Content-Type application/json, Connect-Protocol-Version 1 and
Connect-Timeout-Ms from caps.timeSeconds. Of every answer it checks:
  time     it comes within caps.timeSeconds plus GRACE_SECONDS;
  size     the body is at most caps.outputBytes, success or error;
  code     the Connect code is the one the fixture expects: from the error body's code, or inferred from the
           HTTP status when the body is not a Connect error (404 is unimplemented); a Connect error's HTTP
           status is the one its code maps to;
  numbers  a number with an integral value is written as an integer (3, never 3.0 or 3e0);
  success  HTTP 200, a JSON body that validates against the envelope schema with ok true, and data that
           validates against the call's schema and keeps the call's invariants (as in contract v0);
  failure  failed_precondition, resource_exhausted and unavailable carry a machinery.plugin.v1alpha1.Failure
           detail (the plugin's own errors); any Failure detail decodes to an envelope that validates, with ok
           false, data null and at least one warning, and the error's message is its warnings joined by "; ";
  expected the fixture's envelope, as a subset, against the response or the decoded Failure.
Exit 0 when every fixture passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import failure  # noqa: E402
from run import DEFAULT_CAPS, Validator, deep_merge, invariants, matches  # noqa: E402

PACKAGE = "machinery.plugin.v1alpha1"
CONTRACT = os.path.join(os.path.dirname(HERE), "contract", "v1alpha1")
BASE = "https://github.com/practice-library/machinery/contract/v1alpha1/"
REPORT = "https://github.com/practice-library/.github/spec/refresh-report/schema.json"
VERB = {"Describe": "describe", "Plan": "plan", "RunUnit": "run-unit", "Merge": "merge", "Run": "run"}
REQUEST_DEF = {"Describe": "describeRequest", "Plan": "planRequest", "RunUnit": "runUnitRequest",
               "Merge": "mergeRequest", "Run": "runRequest"}
INPUT_DEF = {"Plan": "planInput", "RunUnit": "unit", "Merge": "mergeInput", "Run": "runInput"}
DATA_DEF = {"Plan": "plan", "RunUnit": "partial", "Run": "runData"}
HTTP_STATUS = {"invalid_argument": 400, "failed_precondition": 400, "resource_exhausted": 429,
               "unimplemented": 501, "unavailable": 503, "deadline_exceeded": 504, "canceled": 499,
               "unknown": 500, "internal": 500, "data_loss": 500, "not_found": 404, "already_exists": 409,
               "aborted": 409, "out_of_range": 400, "permission_denied": 403, "unauthenticated": 401}
INFERRED = {400: "internal", 401: "unauthenticated", 403: "permission_denied", 404: "unimplemented",
            429: "unavailable", 502: "unavailable", 503: "unavailable", 504: "unavailable"}
PLUGIN_CODES = ("failed_precondition", "resource_exhausted", "unavailable")
GRACE_SECONDS = 2


def caps_of(request):
    caps = request.get("caps") if isinstance(request, dict) else None
    caps = caps if isinstance(caps, dict) else {}
    t, b = caps.get("timeSeconds"), caps.get("outputBytes")
    t = t if type(t) is int and 1 <= t <= 3600 else DEFAULT_CAPS["timeSeconds"]
    b = b if type(b) is int and b >= 1 else DEFAULT_CAPS["outputBytes"]
    return t, b


def post(url, body: bytes, time_cap: int, out_cap: int):
    """(HTTP status, content type, body, None) or (.., why it failed); reads at most out_cap + 1 bytes."""
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Connect-Protocol-Version": "1",
        "Connect-Timeout-Ms": str(time_cap * 1000)})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=time_cap + GRACE_SECONDS) as resp:
            status, ctype, data = resp.status, resp.headers.get("Content-Type", ""), resp.read(out_cap + 1)
    except urllib.error.HTTPError as err:
        status, ctype, data = err.code, err.headers.get("Content-Type", ""), err.read(out_cap + 1)
    except (urllib.error.URLError, OSError) as err:
        return None, "", b"", f"no answer within timeSeconds ({time_cap}): {err}"
    if time.monotonic() - started > time_cap + GRACE_SECONDS:
        return None, "", b"", f"the answer came after timeSeconds ({time_cap})"
    if len(data) > out_cap:
        return None, "", b"", f"the body is over outputBytes ({out_cap})"
    return status, ctype.split(";")[0].strip().lower(), data, None


class Integral(ValueError):
    pass


def _number(text: str):
    value = float(text)
    if value.is_integer() and abs(value) <= 2 ** 53:
        raise Integral(text)
    return value


def strict_json(data: bytes):
    """The body's JSON value, or why not. A number with an integral value must be written as an integer
    (3, never 3.0): proto's Struct and Value hold doubles, and every runtime must write them alike."""
    try:
        return json.loads(data.decode("utf-8"), parse_float=_number), None
    except Integral as err:
        return None, f"the body writes the integral number {err} with a fraction or exponent: write it as an integer"
    except (UnicodeDecodeError, ValueError) as err:
        return None, f"the body is not JSON: {str(err)[:120]}"


def check_success(validator, call, request, status, ctype, data, expect):
    if status != 200:
        return f"HTTP {status}, expected 200"
    if ctype != "application/json":
        return f"Content-Type {ctype!r}, expected application/json"
    envelope, why = strict_json(data)
    if why:
        return why
    why = validator.check(BASE + "envelope.schema.json", envelope)
    if why:
        return f"response: {why}"
    if envelope.get("ok") is not True:
        return "a success answers ok true"
    verb = VERB[call]
    if call == "Describe":
        ref = BASE + "describe.schema.json"
    elif call == "Merge":
        ref = REPORT
    else:
        ref = f"{BASE}fact-refresh.schema.json#/$defs/{DATA_DEF[call]}"
    why = validator.check(ref, envelope["data"])
    if why:
        return f"data: {why}"
    why = invariants(verb, dict(request, contract=PACKAGE), envelope["data"])
    if why:
        return why
    return matches(envelope, expect.get("envelope", {}), "response")


def check_error(validator, status, ctype, data, expect):
    body, why = (strict_json(data) if ctype == "application/json" else (None, "not JSON"))
    connect = isinstance(body, dict) and isinstance(body.get("code"), str)
    code = body["code"] if connect else INFERRED.get(status, "unknown")
    if connect and HTTP_STATUS.get(code) != status:
        return f"code {code} with HTTP {status}, expected HTTP {HTTP_STATUS.get(code)}"
    if code != expect["code"]:
        return f"code {code} (HTTP {status}), expected {expect['code']}"
    details = [d for d in (body.get("details") or []) if isinstance(d, dict) and d.get("type") == failure.TYPE] if connect else []
    if len(details) > 1:
        return "more than one Failure detail"
    if not details:
        if code in PLUGIN_CODES:
            return f"{code} without a {failure.TYPE} detail: a plugin's own error carries one"
        if "envelope" in expect and set(expect["envelope"]) != {"ok"}:
            return "the fixture expects a Failure detail, and there is none"
        return None
    try:
        envelope = failure.decode(details[0].get("value"))
    except failure.Malformed as err:
        return f"the Failure detail does not decode: {err}"
    why = validator.check(BASE + "envelope.schema.json", envelope)
    if why:
        return f"Failure: {why}"
    if envelope["ok"] is not False or envelope["data"] is not None or not envelope["warnings"]:
        return "a Failure has ok false, data null and at least one warning"
    if body.get("message") != "; ".join(envelope["warnings"]):
        return f"the message {body.get('message')!r} is not the warnings joined by '; '"
    return matches(envelope, expect.get("envelope", {}), "failure")


def run_fixture(url, validator, group, case):
    procedure = case.get("procedure", group["procedure"])
    call = procedure.rsplit("/", 1)[1]
    expect = case["expect"]
    if "rawBody" in case:
        request, body = None, case["rawBody"].encode("utf-8")
    else:
        request = deep_merge(group.get("defaults", {}), case.get("request", {}))
        body = json.dumps(request).encode("utf-8")
        served = procedure.startswith(f"{PACKAGE}.PluginService/") and call in REQUEST_DEF
        if served and expect["code"] not in ("invalid_argument", "unimplemented"):
            why = validator.check(f"{BASE}request.schema.json#/$defs/{REQUEST_DEF[call]}", request)
            if not why and call in INPUT_DEF:
                why = validator.check(f"{BASE}fact-refresh.schema.json#/$defs/{INPUT_DEF[call]}", request.get("input"))
            if why:
                return f"the fixture's own request is not valid: {why}"
    time_cap, out_cap = caps_of(request)
    status, ctype, data, why = post(f"{url.rstrip('/')}/{procedure}", body, time_cap, out_cap)
    if why:
        return why
    if expect["code"] == "ok":
        return check_success(validator, call, request, status, ctype, data, expect)
    return check_error(validator, status, ctype, data, expect)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True, help="the plugin service's base URL, as http://127.0.0.1:8080")
    ap.add_argument("--report-schema", required=True, help="the refresh-report schema at the pin in contract/pins.json")
    ap.add_argument("--validator", help="the sourcemeta jsonschema CLI; without it, schemas are not checked")
    ap.add_argument("--fixtures", default=os.path.join(HERE, "connect"))
    a = ap.parse_args(argv)
    validator = Validator(a.validator, a.report_schema, CONTRACT)
    if not a.validator:
        print("warning: no --validator: schemas are not checked, only codes, expectations and invariants")
    passed = failed = 0
    for name in sorted(os.listdir(a.fixtures)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(a.fixtures, name), encoding="utf-8") as f:
            group = json.load(f)
        for case in group["cases"]:
            why = run_fixture(a.url, validator, group, case)
            if why:
                failed += 1
                print(f"FAIL {name}: {case['name']}: {why}")
            else:
                passed += 1
                print(f"ok   {name}: {case['name']}")
    print(f"{passed} of {passed + failed} fixtures pass")
    return 0 if failed == 0 and passed > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
