"""What the refresh reads from a checkout of the graphs repository: the source policy and the graphs.

Nothing here names a vendor, a host or a fact: they are all data in the checkout.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Host:
    host: str
    vendor: str
    format: str
    max_requests: int
    min_interval_days: int
    max_bytes: int
    timeout_seconds: int


@dataclass(frozen=True)
class Policy:
    user_agent: str
    max_requests: int
    retry_after_max: int
    unreadable_runs_before_issue: int
    hosts: Dict[str, Host]

    def host_for(self, host: str, vendor: str) -> Optional[Host]:
        """The listed host, only when it is listed for this vendor."""
        h = self.hosts.get(host)
        return h if h is not None and h.vendor == vendor else None


def load_policy(root: str) -> Policy:
    with open(os.path.join(root, "policy", "sources.json"), encoding="utf-8") as f:
        raw = json.load(f)
    hosts = {}
    for h in raw["hosts"]:
        hosts[h["host"]] = Host(
            host=h["host"], vendor=h["vendor"], format=h["format"],
            max_requests=int(h["maxRequestsPerRun"]), min_interval_days=int(h["minIntervalDays"]),
            max_bytes=int(h["maxBytes"]), timeout_seconds=int(h["timeoutSeconds"]),
        )
    return Policy(
        user_agent=raw["userAgent"], max_requests=int(raw["maxRequestsPerRun"]),
        retry_after_max=int(raw["retryAfterMaxSeconds"]),
        unreadable_runs_before_issue=int(raw["unreadableRunsBeforeIssue"]), hosts=hosts,
    )


def find_graphs(root: str) -> List[str]:
    """Every <domain>/<vendor> whose check.json is one object naming graph/nodes as its instances: the same
    rule as scripts/graphs.sh of the graphs repository. Sorted, so runs are deterministic."""
    found = []
    for domain in sorted(os.listdir(root)):
        if domain.startswith(".") or domain == "selftest":
            continue
        ddir = os.path.join(root, domain)
        if not os.path.isdir(ddir):
            continue
        for vendor in sorted(os.listdir(ddir)):
            check = os.path.join(ddir, vendor, "check.json")
            if not os.path.isfile(check):
                continue
            with open(check, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("instances") == "graph/nodes":
                found.append(f"{domain}/{vendor}")
    return found


def load_nodes(root: str, graph: str) -> List[dict]:
    ndir = os.path.join(root, graph, "graph", "nodes")
    nodes = []
    for name in sorted(os.listdir(ndir)):
        if name.endswith(".json"):
            with open(os.path.join(ndir, name), encoding="utf-8") as f:
                nodes.append(json.load(f))
    return nodes
