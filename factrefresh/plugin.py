"""The fact-refresh job as a contract v0 plugin (contract/README.md): one JSON request on stdin, one JSON
envelope on stdout, and nothing else read or written.

Verbs: describe; plan (one graph to work units, one per source host); run-unit (one unit to a partial
result); merge (the plan and every partial result to one refresh report). The job is shardable, so it has
no run verb.

Exit codes: 0 the verb succeeded; 1 the verb failed on a valid request; 2 the request is not a valid
contract v0 request; 3 the request's contract version is not one this plugin speaks; 75 a temporary failure,
the caller may retry the same request later (here: robots.txt answered 429 Too Many Requests).
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .graphs import Host, Policy
from .http import Response, TransportError, UrllibTransport
from .refresh import DEFAULT_PACE_SECONDS, Refresher, plain_url, stamp
from .text import quote_form

NAME = "fact-refresh-python"
VERSION = "0.1.0"
CONTRACTS = ["0"]
MAX_PARALLEL = 3  # never more than this many run-unit calls at once
OK, FAILED, BAD_REQUEST, UNSUPPORTED, TEMPFAIL = 0, 1, 2, 3, 75  # 75: sysexits EX_TEMPFAIL


class Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# -- replay: recorded responses and a virtual clock, for conformance runs ------------------------------------
class VirtualClock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class ReplayTransport:
    """Answers each URL with its recorded responses in order and opens no connection; a URL with no
    response left is a network error, as an unreachable host would be."""

    def __init__(self, exchanges: Dict[str, List[dict]]) -> None:
        self.left = {url: list(responses) for url, responses in exchanges.items()}

    def get(self, url, headers, timeout, max_bytes):
        queue = self.left.get(url)
        if not queue:
            raise TransportError(f"no recorded response for {url[:200]}")
        r = queue.pop(0)
        if "error" in r:
            raise TransportError(r["error"])
        body = r.get("body", "").encode("utf-8")
        return Response(status=r["status"], headers={k.lower(): v for k, v in r.get("headers", {}).items()},
                        body=body[:max_bytes], truncated=len(body) > max_bytes)


# -- verbs ---------------------------------------------------------------------------------------------------
JOB_SCHEMA = "https://github.com/practice-library/machinery/contract/v0/fact-refresh.schema.json"
REPORT_SCHEMA = "https://github.com/practice-library/.github/spec/refresh-report/schema.json"


def describe() -> Tuple[dict, list]:
    return {
        "name": NAME, "version": VERSION, "protocol": CONTRACTS[0], "contracts": CONTRACTS, "job": "fact-refresh",
        "verbs": [
            {"name": "plan", "shardable": False, "input": f"{JOB_SCHEMA}#/$defs/planInput", "output": f"{JOB_SCHEMA}#/$defs/plan"},
            {"name": "run-unit", "shardable": True, "input": f"{JOB_SCHEMA}#/$defs/unit", "output": f"{JOB_SCHEMA}#/$defs/partial"},
            {"name": "merge", "shardable": False, "input": f"{JOB_SCHEMA}#/$defs/mergeInput", "output": REPORT_SCHEMA},
        ],
    }, []


def plan(inp: dict) -> Tuple[dict, list]:
    graph, policy = inp["graph"], inp["policy"]
    vendor = graph.split("/", 1)[1]
    now = parse_time(inp["now"])
    hosts = {h["host"]: h for h in policy["hosts"] if h["vendor"] == vendor}
    not_read, decided = [], []
    due: Dict[str, List[dict]] = {}
    for node in sorted(inp["nodes"], key=lambda n: n["id"]):
        src = node["source"]
        if not plain_url(src):
            not_read.append({"what": node["id"][:300], "why": f"blocked: source {src[:120]} is not a plain https URL; not fetched"})
            continue
        host = hosts.get(src.split("/")[2])
        if host is None:
            decided.append({"node": node["id"], "source": src, "fetchedAt": inp["now"], "outcome": "unreadable",
                            "reason": f"blocked: {src.split('/')[2][:120]} is not listed for {vendor[:60]} in policy/sources.json; not fetched"[:300]})
            continue
        if (now.date() - datetime.strptime(node["measuredAt"], "%Y-%m-%d").date()).days < int(host["minIntervalDays"]):
            continue  # read recently enough
        due.setdefault(host["host"], []).append(
            {"node": node["id"], "source": src, "measuredAt": node["measuredAt"], "quote": quote_form(node["attrs"]["quote"])})
    order = sorted(due, key=lambda h: (min(f["measuredAt"] for f in due[h]), h))
    # The run-wide cap is split: first what each unit needs (robots.txt and one request per source), oldest
    # facts first; then what is left, up to each host's own cap, in the same order.
    left = int(policy["maxRequestsPerRun"])
    budget: Dict[str, int] = {}
    for h in order:
        need = 1 + len({f["source"] for f in due[h]})
        give = min(need, int(hosts[h]["maxRequestsPerRun"]), left)
        if give < 2:
            for f in due[h]:
                not_read.append({"what": f["node"], "why": "the run-wide request cap leaves no room for its host; waits for the next run"})
            continue
        budget[h] = give
        left -= give
    for h in order:
        if h in budget and left > 0:
            more = min(int(hosts[h]["maxRequestsPerRun"]) - budget[h], left)
            budget[h] += more
            left -= more
    units = []
    for h in order:
        if h not in budget:
            continue
        host = hosts[h]
        units.append({
            "id": f"host:{h}", "shardKey": h, "pacing": {"minIntervalSeconds": DEFAULT_PACE_SECONDS},
            "budget": {"requests": budget[h]},
            "host": {"host": h, "format": host["format"], "maxBytes": host["maxBytes"], "timeoutSeconds": host["timeoutSeconds"]},
            "userAgent": policy["userAgent"], "retryAfterMaxSeconds": policy["retryAfterMaxSeconds"],
            "facts": sorted(due[h], key=lambda f: (f["measuredAt"], f["node"])),
        })
    return {
        "graph": graph, "commit": inp["commit"], "run": inp["run"], "startedAt": inp["now"],
        "maxParallel": max(1, min(MAX_PARALLEL, len(units))), "units": units, "decided": decided,
    }, not_read


def run_unit(unit: dict, network: Optional[dict], caps: dict) -> Tuple[dict, list]:
    h = unit["host"]
    if network is None:
        raise Refused(BAD_REQUEST, "run-unit needs network: hosts listing the unit's host, or replay")
    if "replay" in network:
        clock = VirtualClock(parse_time(network["replay"]["start"]))
        transport = ReplayTransport(network["replay"]["exchanges"])
    else:
        if h["host"] not in network["hosts"]:
            raise Refused(BAD_REQUEST, f"the unit's host {h['host']} is not in network.hosts")
        clock = None
        transport = UrllibTransport()
    budget = int(unit["budget"]["requests"])
    host = Host(host=h["host"], vendor="unit", format=h["format"], max_requests=budget, min_interval_days=0,
                max_bytes=int(h["maxBytes"]), timeout_seconds=int(h["timeoutSeconds"]))
    policy = Policy(user_agent=unit["userAgent"], max_requests=budget,
                    retry_after_max=int(unit["retryAfterMaxSeconds"]), unreadable_runs_before_issue=2,
                    hosts={h["host"]: host})
    refresher = Refresher(policy, transport, clock, pace=float(unit["pacing"]["minIntervalSeconds"]))
    # leave room to write the answer: no request starts that could outlast the caller's time cap
    refresher.deadline = refresher.clock.now() + timedelta(seconds=max(1, int(caps["timeSeconds"]) - 5))
    nodes = [{"id": f["node"], "source": f["source"], "measuredAt": f["measuredAt"], "attrs": {"quote": f["quote"]}}
             for f in unit["facts"]]
    rep = refresher.refresh_nodes(nodes, "unit/unit", "unit", "0" * 40, 1, "https://example.invalid/unit")
    partial = {"unit": unit["id"], "shardKey": unit["shardKey"], "requests": rep["requests"],
               "robots": rep["robots"][0] if rep["robots"] else None, "results": rep["results"],
               "notReached": [f["node"] for f in unit["facts"] if f["node"] not in {r["node"] for r in rep["results"]}]}
    if partial["robots"] is not None and partial["robots"].get("httpStatus") == 429:
        # the host asked us to slow down before any page was read: nothing was decided, so the unit is
        # retried later rather than aborting the whole report
        raise Refused(TEMPFAIL, f"robots.txt of {h['host']} answered 429 Too Many Requests; retry the unit later")
    if "aborted" in rep:
        partial["aborted"] = rep["aborted"]
    not_read = [{"what": node, "why": why} for _, node, why in refresher.skipped]
    return partial, not_read


def merge(inp: dict) -> Tuple[dict, list]:
    p = inp["plan"]
    units = {u["id"]: u for u in p["units"]}
    got: Dict[str, dict] = {}
    for part in inp["partials"]:
        uid = part["unit"]
        if uid not in units:
            raise Refused(FAILED, f"a partial result for {uid}, which is not a unit of the plan")
        if uid in got:
            raise Refused(FAILED, f"two partial results for {uid}")
        unit = units[uid]
        if part["shardKey"] != unit["shardKey"]:
            raise Refused(FAILED, f"the partial result for {uid} has shard key {part['shardKey'][:120]}, not {unit['shardKey']}")
        if part["robots"] is not None and part["robots"]["host"] != unit["host"]["host"]:
            raise Refused(FAILED, f"the partial result for {uid} has robots.txt of {part['robots']['host'][:120]}")
        if part["robots"] is None and part["results"]:
            raise Refused(FAILED, f"the partial result for {uid} has results but never read robots.txt")
        allowed = {f["node"] for f in unit["facts"]}
        said = [r["node"] for r in part["results"]] + list(part["notReached"])
        stray = [n for n in said if n not in allowed]
        if stray:
            raise Refused(FAILED, f"the partial result for {uid} has facts outside its unit: {', '.join(stray[:3])}")
        if "aborted" not in part:
            # every fact of the unit is accounted for exactly once: a result, or not reached (it waits for the next run)
            if len(set(said)) != len(said):
                raise Refused(FAILED, f"the partial result for {uid} names a fact twice")
            missing = sorted(allowed - set(said))
            if missing:
                raise Refused(FAILED, f"the partial result for {uid} leaves out facts of its unit: {', '.join(missing[:3])}")
        got[uid] = part
    robots = sorted((part["robots"] for part in got.values() if part["robots"]), key=lambda r: r["host"])
    aborts = []
    for uid in sorted(got):  # in unit order, so the report does not depend on the order of the partials
        part = got[uid]
        if "aborted" in part:
            aborts.append(part["aborted"]["reason"])
        elif part["robots"] and part["robots"]["verdict"] in ("disallowed", "unreachable"):
            aborts.append(f"robots.txt of {part['robots']['host']} is {part['robots']['verdict']}")
    times = [p["startedAt"]] + [r["fetchedAt"] for r in robots]
    report = {"schemaVersion": 1, "graph": p["graph"], "commit": p["commit"],
              "run": {"id": p["run"]["id"], "url": p["run"]["url"], "startedAt": p["startedAt"], "finishedAt": ""},
              "requests": sum(part["requests"] for part in got.values()), "robots": robots, "results": []}
    if aborts:
        report["aborted"] = {"reason": "; ".join(aborts)[:300]}
    else:
        results = list(p["decided"])
        for uid, unit in units.items():
            if uid in got:
                results += got[uid]["results"]
                continue
            # merge is the only step that sees every partial: a missing one is unreadable, never unchanged
            for f in unit["facts"]:
                results.append({"node": f["node"], "source": f["source"], "fetchedAt": p["startedAt"],
                                "outcome": "unreadable",
                                "reason": f"unreadable: no partial result for {uid}; the unit did not finish"})
        nodes = [r["node"] for r in results]
        if len(set(nodes)) != len(nodes):
            raise Refused(FAILED, "a fact has more than one result")
        report["results"] = sorted(results, key=lambda r: r["node"])
        times += [r["fetchedAt"] for r in results]
    report["run"]["finishedAt"] = max(times)
    return report, []


# -- the process boundary ------------------------------------------------------------------------------------
HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def integer(value) -> Optional[int]:
    """A JSON Schema integer: an int that is not a bool, or a finite float with no fraction (60.0)."""
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def bad(message: str) -> Refused:
    return Refused(BAD_REQUEST, message)


def check_request(request) -> None:
    """contract/v0/request.schema.json, checked by hand: exit 3 for a well-formed version this plugin does not
    speak, exit 2 for anything else the schema rejects."""
    if not isinstance(request, dict):
        raise bad("the request is not a JSON object")
    contract = request.get("contract")
    if not isinstance(contract, str) or not re.fullmatch(r"[0-9]+", contract):
        raise bad("the request has no contract version (digits)")
    if contract not in CONTRACTS:
        raise Refused(UNSUPPORTED, f"contract {contract[:20]} is not one this plugin speaks ({', '.join(CONTRACTS)})")
    extra = sorted(set(request) - {"contract", "verb", "caps", "network", "input"})
    if extra:
        raise bad(f"the request has unknown keys: {', '.join(k[:40] for k in extra[:3])}")
    verb = request.get("verb")  # an unknown verb is refused where the verbs are answered
    caps = request.get("caps")
    if not isinstance(caps, dict) or set(caps) != {"timeSeconds", "outputBytes"}:
        raise bad("caps must have exactly timeSeconds and outputBytes")
    t, b = integer(caps["timeSeconds"]), integer(caps["outputBytes"])
    if t is None or not 1 <= t <= 3600:
        raise bad("caps.timeSeconds must be an integer from 1 to 3600")
    if b is None or not 1024 <= b <= 16777216:
        raise bad("caps.outputBytes must be an integer from 1024 to 16777216")
    caps["timeSeconds"], caps["outputBytes"] = t, b
    if verb == "describe":
        if "input" in request:
            raise bad("describe takes no input")
    elif not isinstance(request.get("input"), dict):
        raise bad(f"{verb} needs an input object")
    if "network" in request:
        check_network(request["network"])


def check_network(network) -> None:
    if not isinstance(network, dict) or len(network) != 1 or not ({"hosts"} == set(network) or {"replay"} == set(network)):
        raise bad("network must be exactly one of hosts or replay")
    if "hosts" in network:
        hosts = network["hosts"]
        if not isinstance(hosts, list) or not all(isinstance(h, str) and HOST.fullmatch(h) for h in hosts):
            raise bad("network.hosts must be a list of host names")
        return
    replay = network["replay"]
    if not isinstance(replay, dict) or set(replay) != {"start", "exchanges"}:
        raise bad("network.replay must have exactly start and exchanges")
    if not isinstance(replay["start"], str) or not INSTANT.fullmatch(replay["start"]):
        raise bad("network.replay.start must be a UTC time like 2026-10-31T12:00:00Z")
    if not isinstance(replay["exchanges"], dict):
        raise bad("network.replay.exchanges must be an object")
    for url, answers in replay["exchanges"].items():
        if not isinstance(answers, list):
            raise bad(f"the recorded answers for {url[:120]} must be a list")
        for a in answers:
            if not isinstance(a, dict) or not set(a) <= {"status", "headers", "body", "error"} or ("status" in a) == ("error" in a):
                raise bad(f"a recorded answer for {url[:120]} must have status or error, not both")
            if "status" in a:
                status = integer(a["status"])
                if status is None or not 100 <= status <= 599:
                    raise bad(f"a recorded status for {url[:120]} must be an integer from 100 to 599")
                a["status"] = status
            if "error" in a and not (isinstance(a["error"], str) and a["error"]):
                raise bad(f"a recorded error for {url[:120]} must be a non-empty string")
            if "body" in a and not isinstance(a["body"], str):
                raise bad(f"a recorded body for {url[:120]} must be a string")
            if "headers" in a and not (isinstance(a["headers"], dict) and all(isinstance(v, str) for v in a["headers"].values())):
                raise bad(f"recorded headers for {url[:120]} must map names to strings")


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"a JSON object has the key {sorted(k for k in keys if keys.count(k) > 1)[0][:40]!r} twice")
    return dict(pairs)


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def parse(raw) -> object:
    """The request from stdin's bytes: strict UTF-8, strict JSON (no NaN, no duplicate keys). Anything it cannot
    read is a bad request (exit 2), never this plugin's failure."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        request = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constant)
        # an escape such as \ud800 decodes to a lone surrogate, which no answer could carry as UTF-8
        json.dumps(request, ensure_ascii=False).encode("utf-8")
        return request
    except RecursionError:
        raise bad("the request is nested too deeply")
    except UnicodeEncodeError:
        raise bad("the request is not JSON that can be UTF-8: a string holds a lone surrogate (\\ud800 to \\udfff)")
    except ValueError as err:  # not UTF-8, JSONDecodeError, NaN, a duplicate key, an integer too long to convert
        raise bad(f"the request is not JSON: {str(err)[:200]}")


def output_cap(request) -> Optional[int]:
    """The caller's outputBytes as given, even when it is outside the schema's range: never write more."""
    if isinstance(request, dict) and isinstance(request.get("caps"), dict):
        return integer(request["caps"].get("outputBytes"))
    return None


def dump(envelope: dict) -> bytes:
    """The envelope as one UTF-8 line. Text that cannot be UTF-8 (a lone surrogate) is written as a JSON
    escape instead, so an answer is always written."""
    try:
        return (json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    except UnicodeEncodeError:
        return (json.dumps(envelope, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def fit(code: int, envelope: dict, cap: Optional[int]) -> Tuple[int, bytes]:
    """The answer as written, newline included, within cap. A success that does not fit becomes a failure; a
    failure's warning is shortened until it fits; below the smallest failure envelope nothing is written."""
    out = dump(envelope)
    if cap is None or len(out) <= cap:
        return code, out
    # conformance/v0_bridge.py reads "larger than outputBytes" as resource_exhausted: keep the words
    if envelope["ok"]:
        code, envelope = FAILED, failure(f"the answer is larger than outputBytes ({cap})")
    message = envelope["warnings"][0]
    lo, hi = 1, len(message)  # the longest prefix that fits, by bisection
    best = b""
    while lo <= hi:
        mid = (lo + hi) // 2
        out = dump(failure(message[:mid]))
        if len(out) <= cap:
            best, lo = out, mid + 1
        else:
            hi = mid - 1
    return code, best


def handle(request) -> Tuple[int, dict]:
    check_request(request)
    verb, caps, inp = request["verb"], request["caps"], request.get("input")
    try:
        if verb == "describe":
            data, not_read = describe()
        elif verb == "plan":
            data, not_read = plan(inp)
        elif verb == "run-unit":
            data, not_read = run_unit(inp, request.get("network"), caps)
        elif verb == "merge":
            data, not_read = merge(inp)
        elif verb == "run":
            raise Refused(BAD_REQUEST, "this job is shardable: use plan, run-unit and merge")
        else:
            raise Refused(BAD_REQUEST, f"unknown verb {str(verb)[:40]}")
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as err:
        raise Refused(BAD_REQUEST, f"the {verb} input does not match the contract: {type(err).__name__}: {str(err)[:200]}")
    return OK, {"ok": True, "data": data, "notRead": not_read, "warnings": []}


def failure(message: str) -> dict:
    return {"ok": False, "data": None, "notRead": [], "warnings": [message[:500]]}


def main(stdin=None, stdout=None) -> int:
    """stdin and stdout are binary streams (the process's own by default)."""
    stdin, stdout = stdin or sys.stdin.buffer, stdout or sys.stdout.buffer
    request, cap = None, None
    try:
        request = parse(stdin.read())
        cap = output_cap(request)
        code, envelope = handle(request)
    except Refused as err:
        code, envelope = err.code, failure(str(err))
    except Exception as err:  # anything else is this plugin's failure, still answered in the envelope
        code, envelope = FAILED, failure(f"{type(err).__name__}: {str(err)[:300]}")
    code, out = fit(code, envelope, cap)
    stdout.write(out)
    stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
