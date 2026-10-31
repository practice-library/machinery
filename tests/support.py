"""Recorded HTTP exchanges and a clock that only moves when told: tests never touch the network."""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from factrefresh.http import Response, TransportError

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
GRAPHS = os.path.join(FIXTURES, "graphs")
HTTP = os.path.join(FIXTURES, "http")
START = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
COMMIT = "0e6f5ed237dd52f9fe02a33f8c6b8ec653a3a2c3"
RUN_URL = "https://github.com/practice-library/machinery/actions/runs/7"


class FakeClock:
    def __init__(self, start: datetime = START) -> None:
        self.t = start
        self.slept: List[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += timedelta(seconds=seconds)


def exchanges() -> Dict[str, List[dict]]:
    with open(os.path.join(HTTP, "exchanges.json"), encoding="utf-8") as f:
        return json.load(f)


class Replay:
    """Answers each URL with its recorded responses in order; an unrecorded URL or one asked more often
    than recorded fails the test, so an extra request is never silent."""

    def __init__(self, recorded: Dict[str, List[dict]], clock: FakeClock, step: float = 0.25) -> None:
        self.recorded = copy.deepcopy(recorded)
        self.clock = clock
        self.step = step
        self.calls: List[tuple] = []  # (url, headers, at)

    def get(self, url, headers, timeout, max_bytes):
        self.calls.append((url, dict(headers), self.clock.now()))
        self.clock.t += timedelta(seconds=self.step)
        queue = self.recorded.get(url)
        if not queue:
            raise AssertionError(f"unrecorded request: {url}")
        r = queue.pop(0)
        if r.get("error"):
            raise TransportError(r["error"])
        if "file" in r:
            with open(os.path.join(HTTP, r["file"]), "rb") as f:
                body = f.read()
        else:
            body = r.get("body", "").encode("utf-8")
        return Response(status=r["status"], headers={k.lower(): v for k, v in r.get("headers", {}).items()},
                        body=body[:max_bytes], truncated=len(body) > max_bytes)

    def urls(self) -> List[str]:
        return [c[0] for c in self.calls]


class GraphsCopy:
    """A writable copy of the fixture checkout, for tests that change the policy or a node."""

    def __enter__(self) -> str:
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "graphs")
        shutil.copytree(GRAPHS, self.root)
        return self.root

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.dir)


def edit_policy(root: str, fn) -> None:
    path = os.path.join(root, "policy", "sources.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    fn(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
