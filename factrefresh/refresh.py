"""The refresh run: for each graph, re-read the source of every fact that is due and say whether its quote
is still there. It follows the rules in spec/README.md of the graphs repository:

1. robots.txt of every host first; a disallow of a path we would fetch, or a robots.txt that cannot be read,
   aborts the graph's run before any page is fetched;
2. only hosts listed for the graph's vendor in policy/sources.json, redirects included, within each host's
   request, byte and time caps, with the listed user agent;
3. one run-wide request cap, robots.txt included; facts not reached wait for the next run, oldest first;
4. on 429 or 503, one retry after a short enough Retry-After, else the host is stopped for the run;
5. unchanged, changed or unreadable for each fact fetched.

Requests are sequential, so there is never more than one connection to a host, and two requests to the
same host are at least `pace` seconds apart. Facts sharing a source share one request.
"""
from __future__ import annotations

import email.utils
import hashlib
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from . import robots as robotstxt
from .graphs import Host, Policy, load_nodes
from .http import Response, TransportError
from .text import has_quote, page_text, quote_is_empty

REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 5
DEFAULT_PACE_SECONDS = 2.0
MIN_PAGES_FOR_HOST_VERDICT = 2  # pages of one host that must all miss their quote before the host is distrusted
# The url pattern of spec/refresh-report: lower-case host, printable ASCII path.
# The url definition of spec/refresh-report: at most 2000 characters, lower-case host, printable ASCII path.
URL_RE = re.compile(r"https://[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+(/[!-~]*)?")
URL_MAX_LENGTH = 2000


def plain_url(url: str) -> bool:
    """Whether the report's schema accepts this URL: the whole string must match (fullmatch, so not even a
    trailing newline is let through) within the schema's length cap."""
    return len(url) <= URL_MAX_LENGTH and URL_RE.fullmatch(url) is not None
TEXT_TYPES = ("application/json", "application/xhtml+xml", "application/xml")


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CapReached(Exception):
    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope  # "run" or "host"


@dataclass
class Page:
    fetched_at: str
    status: Optional[int] = None
    final_url: Optional[str] = None
    text: Optional[str] = None  # set only when the page was read
    reason: Optional[str] = None  # why it could not be read
    retry_after: Optional[int] = None


class Refresher:
    def __init__(self, policy: Policy, transport, clock: Optional[Clock] = None,
                 pace: float = DEFAULT_PACE_SECONDS) -> None:
        self.policy = policy
        self.transport = transport
        self.clock = clock or Clock()
        self.pace = pace
        self.run_requests = 0  # across every graph of the run
        self.host_requests: Dict[str, int] = {}
        self.last_request: Dict[str, datetime] = {}
        self.graph_requests = 0
        self.skipped: List[Tuple[str, str, str]] = []

    # -- one request -------------------------------------------------------------------------------------
    def _request(self, host: Host, url: str) -> Response:
        if self.run_requests >= self.policy.max_requests:
            raise CapReached("run")
        if self.host_requests.get(host.host, 0) >= host.max_requests:
            raise CapReached("host")
        last = self.last_request.get(host.host)
        if last is not None:
            wait = self.pace - (self.clock.now() - last).total_seconds()
            if wait > 0:
                self.clock.sleep(wait)
        self.run_requests += 1
        self.graph_requests += 1
        self.host_requests[host.host] = self.host_requests.get(host.host, 0) + 1
        headers = {"User-Agent": self.policy.user_agent, "Accept": "text/html, text/plain, text/markdown, */*;q=0.1"}
        try:
            return self.transport.get(url, headers, host.timeout_seconds, host.max_bytes)
        finally:
            self.last_request[host.host] = self.clock.now()

    def _retry_after(self, value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        value = value.strip()
        if value.isdigit():
            return int(value)
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None or when.tzinfo is None:
            return None
        return max(0, math.ceil((when - self.clock.now()).total_seconds()))

    # -- robots.txt --------------------------------------------------------------------------------------
    def _robots(self, host: Host, urls: List[str]) -> Tuple[Optional[dict], Optional[robotstxt.Rules], Optional[str]]:
        """(record, rules, abort reason); record is None when the cap was reached before it was read."""
        url, at_host = f"https://{host.host}/robots.txt", host
        for _hop in range(MAX_HOPS + 1):
            try:
                resp = self._request(at_host, url)
            except CapReached:
                return None, None, None
            except TransportError as err:
                rec = {"host": host.host, "fetchedAt": stamp(self.clock.now()), "verdict": "unreachable"}
                return rec, None, f"robots.txt of {host.host} could not be read: {err}"
            if resp.status not in REDIRECTS:
                break
            # RFC 9309 follows up to five redirects; we follow them only to hosts listed for the same vendor.
            target = urljoin(url, resp.headers.get("location", ""))
            listed = self.policy.host_for(urlsplit(target).hostname or "", host.vendor) if plain_url(target) else None
            if listed is None:
                break  # an unlisted target: recorded as unreachable below
            url, at_host = target, listed
        rec = {"host": host.host, "fetchedAt": stamp(self.clock.now()), "httpStatus": resp.status}
        if 200 <= resp.status < 300 and resp.truncated:
            # A robots.txt cut at maxBytes may have lost a later Disallow: it cannot be read whole.
            rec["verdict"] = "unreachable"
            return rec, None, f"robots.txt of {host.host} is larger than maxBytes ({at_host.max_bytes}); not read whole"
        if 200 <= resp.status < 300:
            rules = robotstxt.parse(resp.body.decode("utf-8", "replace"), self.policy.user_agent)
            denied = [u for u in urls if not rules.allows(u)]
            if denied:
                rec["verdict"] = "disallowed"
                return rec, None, f"robots.txt of {host.host} disallows {urlsplit(denied[0]).path}"
            rec["verdict"] = "allowed"
            return rec, rules, None
        # A 429 asks us to slow down, not that there is no robots.txt: treat it as unreachable, like a 5xx.
        if 400 <= resp.status < 500 and resp.status != 429:
            rec["verdict"] = "absent"
            return rec, robotstxt.Rules([]), None
        rec["verdict"] = "unreachable"
        return rec, None, f"robots.txt of {host.host} could not be read: HTTP {resp.status}"

    # -- one page ----------------------------------------------------------------------------------------
    def _page(self, src: str, host: Host, vendor: str, rules: Dict[str, robotstxt.Rules],
              stopped: Dict[str, Page]) -> Page:
        url = src
        for _hop in range(MAX_HOPS + 1):
            if host.host in stopped:
                return stopped[host.host]
            retried = False
            while True:
                try:
                    resp = self._request(host, url)
                except TransportError as err:
                    return Page(stamp(self.clock.now()), reason=f"unreachable: {err}")
                at = stamp(self.clock.now())
                if resp.status in (429, 503):
                    wait = self._retry_after(resp.headers.get("retry-after"))
                    if not retried and wait is not None and wait <= self.policy.retry_after_max:
                        retried = True
                        self.clock.sleep(wait)
                        continue
                    told = f"Retry-After {wait}" if wait is not None else "no usable Retry-After"
                    why = "after the one retry" if retried else (
                        "beyond retryAfterMaxSeconds" if wait is not None else "so no retry")
                    page = Page(at, status=resp.status, retry_after=wait,
                                reason=f"unreachable: {resp.status} with {told}, {why}; {host.host} stopped for this run")
                    stopped[host.host] = page
                    return page
                break
            if resp.status in REDIRECTS:
                target = urljoin(url, resp.headers.get("location", ""))
                if not resp.headers.get("location"):
                    return Page(at, status=resp.status, reason=f"unreachable: HTTP {resp.status} without Location")
                if not plain_url(target):
                    return Page(at, status=resp.status, reason=f"blocked: redirect to {target[:120]}, not a plain https URL")
                nhost = urlsplit(target).hostname or ""
                listed = self.policy.host_for(nhost, vendor)
                if listed is None:
                    return Page(at, status=resp.status,
                                reason=f"blocked: redirect to {nhost}, not listed for {vendor} in policy/sources.json")
                if listed.host not in rules:
                    return Page(at, status=resp.status,
                                reason=f"blocked: redirect to {nhost}, whose robots.txt was not read this run")
                if not rules[listed.host].allows(target):
                    return Page(at, status=resp.status,
                                reason=f"blocked: redirect to {target[:120]}, which robots.txt of {nhost} disallows")
                url, host = target, listed
                continue
            if not 200 <= resp.status < 300:
                return Page(at, status=resp.status, reason=f"unreachable: HTTP {resp.status}")
            ctype = resp.headers.get("content-type", "")
            mime = ctype.split(";", 1)[0].strip().lower()
            if mime and not (mime.startswith("text/") or mime in TEXT_TYPES or mime.endswith("+json")):
                return Page(at, status=resp.status, final_url=url, reason=f"unreadable: not text ({mime})")
            if resp.truncated:
                return Page(at, status=resp.status, final_url=url,
                            reason=f"unreadable: larger than maxBytes ({host.max_bytes}) of {host.host}")
            m = re.search(r"charset=([\w.-]+)", ctype, re.I)
            try:
                body = resp.body.decode(m.group(1) if m else "utf-8", "replace")
            except LookupError:
                body = resp.body.decode("utf-8", "replace")
            return Page(at, status=resp.status, final_url=url, text=page_text(body, host.format))
        return Page(stamp(self.clock.now()), reason=f"unreachable: more than {MAX_HOPS} redirects")

    # -- one graph ---------------------------------------------------------------------------------------
    def refresh_graph(self, root: str, graph: str, commit: str, run_id: int, run_url: str) -> dict:
        vendor = graph.split("/", 1)[1]
        self.graph_requests = 0
        self.skipped: List[Tuple[str, str, str]] = []  # (graph, node, reason): facts left out of the report
        started = self.clock.now()
        today: date = started.date()
        results: List[dict] = []
        due: List[Tuple[dict, Host]] = []
        for node in load_nodes(root, graph):
            src = node["source"]
            if not plain_url(src):
                # A port, userinfo, upper case or a scheme other than https: never fetched. The report's
                # url pattern rejects such a source too, so it is logged instead of reported, and the rest
                # of the graph's report stays valid.
                self.skipped.append((graph, node["id"], f"blocked: source {src[:120]} is not a plain https URL; not fetched"))
                continue
            parts = urlsplit(src)
            host = self.policy.host_for(parts.hostname or "", vendor) if parts.scheme == "https" else None
            if host is None:
                # Not fetched: an unknown host is skipped and reported, never fetched.
                results.append({"node": node["id"], "source": src, "fetchedAt": stamp(started),
                                "outcome": "unreadable",
                                "reason": f"blocked: {parts.hostname} is not listed for {vendor} in policy/sources.json; not fetched"})
                continue
            age = (today - date.fromisoformat(node["measuredAt"])).days
            if age < host.min_interval_days:
                continue  # read recently enough; minIntervalDays of its host
            due.append((node, host))
        due.sort(key=lambda p: (p[0]["measuredAt"], p[0]["id"]))

        robots: List[dict] = []
        rules: Dict[str, robotstxt.Rules] = {}
        aborts: List[str] = []
        for name in sorted({h.host for _, h in due}):
            rec, rule, reason = self._robots(self.policy.hosts[name], [n["source"] for n, h in due if h.host == name])
            if rec is None:
                break  # run-wide cap reached: hosts not read are not fetched
            robots.append(rec)
            if reason:
                aborts.append(reason)
            else:
                rules[name] = rule

        report: dict = {
            "schemaVersion": 1, "graph": graph, "commit": commit,
            "run": {"id": run_id, "url": run_url, "startedAt": stamp(started), "finishedAt": ""},
            "requests": 0, "robots": robots, "results": [],
        }
        if aborts:
            report["aborted"] = {"reason": ("blocked: " + "; ".join(aborts))[:300]}
        else:
            pages: Dict[str, Page] = {}
            stopped: Dict[str, Page] = {}
            read: List[Tuple[str, str, dict]] = []  # (host, source, result)
            for node, host in due:
                if host.host not in rules:
                    continue
                src = node["source"]
                page = pages.get(src)
                if page is None:
                    try:
                        page = self._page(src, host, vendor, rules, stopped)
                    except CapReached as cap:
                        if cap.scope == "run":
                            break  # the rest waits for the next run
                        continue  # this host's cap: its other facts wait too
                    pages[src] = page
                read.append((host.host, src, self._result(node, page)))
            results += self._distrust_hosts_where_every_quote_failed(read)
            report["results"] = sorted(results, key=lambda r: r["node"])
        report["requests"] = self.graph_requests
        report["run"]["finishedAt"] = stamp(self.clock.now())
        return report

    @staticmethod
    def _distrust_hosts_where_every_quote_failed(read: List[Tuple[str, str, dict]]) -> List[dict]:
        """A host that answered 200 for at least MIN_PAGES_FOR_HOST_VERDICT pages without one quote on any of
        them is most likely serving a challenge or error page, not changed facts: its `changed` results become
        `unreadable`, so they never become one pull request per fact."""
        hosts = sorted({h for h, _, _ in read})
        for host in hosts:
            mine = [(src, r) for h, src, r in read if h == host and r["outcome"] in ("changed", "unchanged")]
            pages = {src for src, _ in mine}
            if len(pages) < MIN_PAGES_FOR_HOST_VERDICT or any(r["outcome"] == "unchanged" for _, r in mine):
                continue
            for _, r in mine:
                r["outcome"] = "unreadable"
                r["reason"] = (f"blocked: no quote was found on any of the {len(pages)} pages read from {host}, "
                               f"which looks like a challenge or error page, not a verdict")
        return [r for _, _, r in read]

    @staticmethod
    def _result(node: dict, page: Page) -> dict:
        r: dict = {"node": node["id"], "source": node["source"]}
        if page.final_url:
            r["finalUrl"] = page.final_url
        if page.status is not None:
            r["httpStatus"] = page.status
        r["fetchedAt"] = page.fetched_at
        if page.text is None:
            r["outcome"] = "unreadable"
            r["reason"] = page.reason
            if page.retry_after is not None:
                r["retryAfterSeconds"] = page.retry_after
            return r
        r["sha256"] = hashlib.sha256(page.text.encode("utf-8")).hexdigest()
        if quote_is_empty(node["attrs"]["quote"]):
            r["outcome"] = "unreadable"
            r["reason"] = "unreadable: the stored quote is empty, so nothing can be confirmed"
            return r
        if has_quote(page.text, node["attrs"]["quote"]):
            r["outcome"] = "unchanged"
            if page.final_url != node["source"]:
                r["reason"] = f"quote-moved: the source redirects to {page.final_url}; the quote is there"[:300]
        else:
            r["outcome"] = "changed"
            r["reason"] = "quote-gone: the page was read and the quote is not in its text"
        return r
