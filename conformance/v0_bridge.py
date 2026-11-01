#!/usr/bin/env python3
"""Serves a contract v0 plugin executable as a contract v1alpha1 service, in Connect unary JSON over HTTP, so
the Connect fixtures can run against today's plugin until it runs on a generated runtime (plan #17, PR 5).
Standard library only; it binds to 127.0.0.1 and prints its base URL as its first line.

  python3 conformance/v0_bridge.py --plugin /abs/path/to/plugin [--port 0]

Per call it does what a runtime's transport does, and nothing the plugin should do itself:
  - only POST /machinery.plugin.v1alpha1.PluginService/<Call> with Content-Type application/json; any other
    path is 404 (which Connect clients read as unimplemented), another content type 415;
  - the body must be strict JSON (UTF-8, no duplicate key, no NaN, no lone surrogate) and an object with only
    the call's fields (caps, network on RunUnit and Run, input on all but Describe); otherwise
    invalid_argument, with no Failure detail, as a generated decoder answers;
  - network is put in v0's form (v0_network): {} or an empty hosts is no connection, replay goes with an
    empty hosts, and exchanges may be left out;
  - the plugin gets the body plus contract "0" and the verb, and is killed after Connect-Timeout-Ms
    (deadline_exceeded);
  - exit 0 is HTTP 200 with the envelope, integral numbers written as integers as a proto runtime writes
    them; Describe's protocol, contracts and schema $ids are rewritten to
    the v1alpha1 package and schemas, which the bridge, not the v0 plugin, knows;
  - any other exit is a Connect error: 1 failed_precondition (resource_exhausted when the warning says the
    answer is larger than outputBytes), 2 invalid_argument, 3 unimplemented, 75 unavailable; its message is
    the warnings joined by "; ", and it carries the envelope as a Failure detail. A plugin that wrote no
    envelope (its refusal did not fit outputBytes) gets an error with no detail;
  - an error body never exceeds caps.outputBytes: the detail is dropped first, then the message.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import failure  # noqa: E402

PACKAGE = "machinery.plugin.v1alpha1"
PREFIX = f"/{PACKAGE}.PluginService/"
FIELDS = {"Describe": {"caps"}, "Plan": {"caps", "input"}, "RunUnit": {"caps", "network", "input"},
          "Merge": {"caps", "input"}, "Run": {"caps", "network", "input"}}
VERB = {"Describe": "describe", "Plan": "plan", "RunUnit": "run-unit", "Merge": "merge", "Run": "run"}
EXIT = {1: "failed_precondition", 2: "invalid_argument", 3: "unimplemented", 75: "unavailable"}
STATUS = {"invalid_argument": 400, "failed_precondition": 400, "resource_exhausted": 429,
          "unimplemented": 501, "unavailable": 503, "deadline_exceeded": 504, "internal": 500}
MAX_BODY = 16 << 20
GRACE_SECONDS = 1


def strict(body: bytes):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(set(keys)) != len(keys):
            raise ValueError("a key given twice")
        return dict(items)

    def constant(name):
        raise ValueError(f"{name} is not JSON")

    value = json.loads(body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    json.dumps(value, ensure_ascii=False).encode("utf-8")  # a lone surrogate cannot be UTF-8
    return value


def integers(value):
    """Integral floats as integers, as a proto runtime writes the doubles of Struct and Value (3, never 3.0)."""
    if isinstance(value, float) and value.is_integer() and abs(value) <= 2 ** 53:
        return int(value)
    if isinstance(value, list):
        return [integers(v) for v in value]
    if isinstance(value, dict):
        return {k: integers(v) for k, v in value.items()}
    return value


def v0_network(network):
    """A v1alpha1 network in v0's form. Proto cannot tell an empty hosts or exchanges from a missing one, so
    {} and {"hosts": []} are no connection, replay goes with an empty hosts, and exchanges may be left out;
    v0 wants exactly one of hosts and replay, and exchanges always. A listed host with replay is left as it is,
    for the plugin to refuse."""
    if not isinstance(network, dict):
        return network
    hosts, replay = network.get("hosts"), network.get("replay")
    if isinstance(replay, dict) and not hosts:
        return {"replay": dict({"exchanges": {}}, **replay)}
    if replay is None and set(network) <= {"hosts"}:
        return {"hosts": hosts or []}
    return network


def v1(value):
    if isinstance(value, str):
        return value.replace("/contract/v0/", "/contract/v1alpha1/")
    if isinstance(value, list):
        return [v1(v) for v in value]
    if isinstance(value, dict):
        return {k: v1(v) for k, v in value.items()}
    return value


class Handler(BaseHTTPRequestHandler):
    plugin: list = []

    def log_message(self, *args):
        pass

    def send(self, status: int, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, code: str, message: str, envelope=None, cap=None):
        body = {"code": code, "message": message}
        if envelope is not None and envelope.get("warnings"):
            body["message"] = "; ".join(envelope["warnings"])
            body["details"] = [{"type": failure.TYPE, "value": failure.encode(envelope)}]
        if cap is not None and len(json.dumps(body).encode("utf-8")) > cap:
            # the body never exceeds outputBytes: the detail goes first, then the message
            body.pop("details", None)
            if len(json.dumps(body).encode("utf-8")) > cap:
                body = {"code": code}
        self.send(STATUS[code], body)

    def do_POST(self):
        call = self.path[len(PREFIX):] if self.path.startswith(PREFIX) else None
        if call not in FIELDS:
            return self.send(404, b"404 page not found\n", "text/plain; charset=utf-8")
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self.send(415, b"unsupported media type\n", "text/plain; charset=utf-8")
        raw = self.headers.get("Content-Length") or "0"
        if not (raw.isascii() and raw.isdigit() and len(raw) <= 10):
            return self.error("invalid_argument", "Content-Length is not a number of bytes")
        length = int(raw)
        if length > MAX_BODY:
            return self.error("resource_exhausted", "the request is too large")
        try:
            request = strict(self.rfile.read(length))
        except (UnicodeDecodeError, ValueError) as err:
            return self.error("invalid_argument", f"the body is not JSON: {str(err)[:200]}")
        if not isinstance(request, dict):
            return self.error("invalid_argument", "the body is not a JSON object")
        extra = sorted(set(request) - FIELDS[call])
        if extra:
            return self.error("invalid_argument", f"unknown field {extra[0][:60]!r} for {call}")
        caps = request.get("caps")
        cap = caps.get("outputBytes") if isinstance(caps, dict) else None
        cap = cap if type(cap) is int and cap > 0 else None
        timeout_ms = self.headers.get("Connect-Timeout-Ms", "")
        limit = int(timeout_ms) / 1000 if timeout_ms.isdigit() and 0 < len(timeout_ms) <= 10 else None
        if "network" in request:
            request = dict(request, network=v0_network(request["network"]))
        stdin = json.dumps(dict(request, contract="0", verb=VERB[call])).encode("utf-8")
        try:
            done = subprocess.run(self.plugin, input=stdin, capture_output=True,
                                  timeout=None if limit is None else limit + GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return self.error("deadline_exceeded", "the plugin did not answer within the deadline", cap=cap)
        try:
            envelope = json.loads(done.stdout.decode("utf-8")) if done.stdout.strip() else None
        except (UnicodeDecodeError, ValueError):
            return self.error("internal", "the plugin's answer is not JSON", cap=cap)
        if done.returncode == 0 and isinstance(envelope, dict):
            if call == "Describe" and isinstance(envelope.get("data"), dict):
                envelope["data"] = dict(v1(envelope["data"]), protocol=PACKAGE, contracts=[PACKAGE])
            return self.send(200, integers(envelope))
        code = EXIT.get(done.returncode, "internal")
        warnings = envelope.get("warnings", []) if isinstance(envelope, dict) else []
        # the warning factrefresh/plugin.py writes for an answer over outputBytes; a fixture fails if it drifts
        if code == "failed_precondition" and any("larger than outputBytes" in w for w in warnings):
            code = "resource_exhausted"
        return self.error(code, f"the plugin exited {done.returncode}", envelope if isinstance(envelope, dict) else None, cap)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plugin", required=True, nargs="+", help="the v0 plugin command (absolute path first)")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    Handler.plugin = a.plugin
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
