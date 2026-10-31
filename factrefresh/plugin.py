"""The fact-refresh job as a contract v0 plugin (contract/README.md): one JSON request on stdin, one JSON
envelope on stdout, and nothing else read or written.

Verbs: describe; plan (one graph to work units, one per source host); run-unit (one unit to a partial
result); merge (the plan and every partial result to one refresh report). The job is shardable, so it has
no run verb.

Exit codes: 0 the verb succeeded; 1 the verb failed on a valid request; 2 the request is not a valid
contract v0 request; 3 the request's contract version is not one this plugin speaks.
"""
from __future__ import annotations

import json
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
OK, FAILED, BAD_REQUEST, UNSUPPORTED = 0, 1, 2, 3


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
def describe() -> Tuple[dict, list]:
    return {
        "name": NAME, "version": VERSION, "contracts": CONTRACTS, "job": "fact-refresh",
        "verbs": {"plan": {"shardable": False}, "run-unit": {"shardable": True}, "merge": {"shardable": False}},
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
               "robots": rep["robots"][0] if rep["robots"] else None, "results": rep["results"]}
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
        allowed = {f["node"] for f in units[uid]["facts"]}
        stray = [r["node"] for r in part["results"] if r["node"] not in allowed]
        if stray:
            raise Refused(FAILED, f"the partial result for {uid} has facts outside its unit: {', '.join(stray[:3])}")
        got[uid] = part
    robots = sorted((part["robots"] for part in got.values() if part["robots"]), key=lambda r: r["host"])
    aborts = [part["aborted"]["reason"] for part in got.values() if "aborted" in part]
    aborts += [f"robots.txt of {r['host']} is {r['verdict']}" for r in robots
               if r["verdict"] in ("disallowed", "unreachable") and not any(r["host"] in a for a in aborts)]
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
def handle(request) -> Tuple[int, dict]:
    if not isinstance(request, dict):
        raise Refused(BAD_REQUEST, "the request is not a JSON object")
    contract, verb, caps = request.get("contract"), request.get("verb"), request.get("caps")
    if not isinstance(contract, str):
        raise Refused(BAD_REQUEST, "the request has no contract version")
    if contract not in CONTRACTS:
        raise Refused(UNSUPPORTED, f"contract {contract[:20]} is not one this plugin speaks ({', '.join(CONTRACTS)})")
    if not isinstance(caps, dict) or not isinstance(caps.get("timeSeconds"), int) or not isinstance(caps.get("outputBytes"), int):
        raise Refused(BAD_REQUEST, "the request has no caps with timeSeconds and outputBytes")
    inp = request.get("input")
    if verb != "describe" and not isinstance(inp, dict):
        raise Refused(BAD_REQUEST, f"{verb} needs an input object")
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
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    cap = None
    try:
        request = json.loads(stdin.read())
        if isinstance(request, dict) and isinstance(request.get("caps"), dict):
            cap = request["caps"].get("outputBytes") if isinstance(request["caps"].get("outputBytes"), int) else None
        code, envelope = handle(request)
    except json.JSONDecodeError as err:
        code, envelope = BAD_REQUEST, failure(f"the request is not JSON: {err}")
    except Refused as err:
        code, envelope = err.code, failure(str(err))
    except Exception as err:  # anything else is this plugin's failure, still answered in the envelope
        code, envelope = FAILED, failure(f"{type(err).__name__}: {str(err)[:300]}")
    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    if cap is not None and len(text.encode("utf-8")) > cap:
        code, text = FAILED, json.dumps(failure(f"the answer is larger than outputBytes ({cap})"))
    stdout.write(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
