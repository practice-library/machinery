"""The refresh run against recorded exchanges. Each test names the rule it guards; tests/guards.py removes
each guard in turn and checks that its test then fails."""
from __future__ import annotations

import json
import os
import unittest

from factrefresh.graphs import find_graphs, load_policy
from factrefresh.refresh import Refresher
from tests.support import COMMIT, GRAPHS, FIXTURES, RUN_URL, FakeClock, GraphsCopy, Replay, edit_policy, exchanges

UA = "practice-library-graphs/1 (+https://github.com/practice-library/graphs)"


def run(root=GRAPHS, recorded=None, graph="vendors/example", pace=2.0):
    clock = FakeClock()
    transport = Replay(exchanges() if recorded is None else recorded, clock)
    refresher = Refresher(load_policy(root), transport, clock, pace=pace)
    report = refresher.refresh_graph(root, graph, COMMIT, 7, RUN_URL)
    return report, transport, clock


def by_node(report):
    return {r["node"]: r for r in report["results"]}


class Basic(unittest.TestCase):
    def test_report_matches_the_recorded_golden(self):
        report, _, _ = run()
        with open(os.path.join(FIXTURES, "expect", "vendors-example.json"), encoding="utf-8") as f:
            self.assertEqual(report, json.load(f))

    def test_same_inputs_give_the_same_report(self):
        a, _, _ = run()
        b, _, _ = run()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_graphs_are_found_from_data(self):
        self.assertEqual(find_graphs(GRAPHS), ["vendors/example", "vendors/other"])

    def test_outcomes(self):
        r = by_node(run()[0])
        self.assertEqual(r["limit.example.burst"]["outcome"], "unchanged")
        self.assertEqual(r["term.example.support"]["outcome"], "unchanged")
        self.assertEqual(r["plan.example.free"]["outcome"], "changed")
        self.assertTrue(r["plan.example.free"]["reason"].startswith("quote-gone:"))


class OnlyListedHosts(unittest.TestCase):
    def test_unlisted_host_is_reported_and_never_fetched(self):
        report, t, _ = run()
        self.assertFalse([u for u in t.urls() if "unlisted.example.net" in u])
        r = by_node(report)["term.example.offsite"]
        self.assertEqual(r["outcome"], "unreadable")
        self.assertTrue(r["reason"].startswith("blocked:"))

    def test_host_listed_for_another_vendor_is_not_fetched_for_this_one(self):
        report, t, _ = run()
        self.assertFalse([u for u in t.urls() if "docs.other.example.org" in u])
        self.assertTrue(by_node(report)["product.example.borrowed"]["reason"].startswith("blocked:"))

    def test_redirect_to_an_unlisted_host_is_not_followed(self):
        rec = exchanges()
        rec["https://docs.example.com/limits"] = [{"status": 302, "headers": {"location": "https://evil.example.net/limits"}}]
        report, t, _ = run(recorded=rec)
        self.assertNotIn("https://evil.example.net/limits", t.urls())
        r = by_node(report)["limit.example.burst"]
        self.assertEqual(r["outcome"], "unreadable")
        self.assertIn("evil.example.net", r["reason"])

    def test_redirect_on_the_same_host_is_followed_and_noted(self):
        rec = exchanges()
        rec["https://docs.example.com/limits"] = [{"status": 301, "headers": {"location": "/v2/limits"}}]
        rec["https://docs.example.com/v2/limits"] = [{"status": 200, "headers": {"content-type": "text/html"}, "file": "limits.html"}]
        r = by_node(run(recorded=rec)[0])["limit.example.burst"]
        self.assertEqual(r["outcome"], "unchanged")
        self.assertEqual(r["finalUrl"], "https://docs.example.com/v2/limits")
        self.assertTrue(r["reason"].startswith("quote-moved:"))


class RobotsFirst(unittest.TestCase):
    def test_robots_is_read_before_any_page(self):
        _, t, _ = run()
        urls = t.urls()
        first_page = min(i for i, u in enumerate(urls) if not u.endswith("/robots.txt"))
        self.assertEqual({u for u in urls[:first_page]},
                         {"https://docs.example.com/robots.txt", "https://help.example.com/robots.txt"})

    def test_disallow_aborts_before_any_page(self):
        rec = exchanges()
        rec["https://docs.example.com/robots.txt"] = [{"status": 200, "body": "User-agent: *\nDisallow: /plans\n"}]
        report, t, _ = run(recorded=rec)
        self.assertTrue(all(u.endswith("/robots.txt") for u in t.urls()))
        self.assertIn("aborted", report)
        self.assertEqual(report["results"], [])
        self.assertEqual(report["robots"][0]["verdict"], "disallowed")

    def test_our_own_group_overrides_star(self):
        rec = exchanges()
        rec["https://docs.example.com/robots.txt"] = [{"status": 200, "body":
            "User-agent: *\nAllow: /\n\nUser-agent: Practice-Library-Graphs\nDisallow: /pricing\n"}]
        self.assertIn("aborted", run(recorded=rec)[0])

    def test_unreachable_robots_aborts(self):
        rec = exchanges()
        rec["https://docs.example.com/robots.txt"] = [{"status": 500, "body": ""}]
        report, t, _ = run(recorded=rec)
        self.assertTrue(all(u.endswith("/robots.txt") for u in t.urls()))
        self.assertIn("aborted", report)
        self.assertEqual(report["robots"][0]["verdict"], "unreachable")

    def test_network_error_on_robots_aborts(self):
        rec = exchanges()
        rec["https://docs.example.com/robots.txt"] = [{"error": "timed out"}]
        report, _, _ = run(recorded=rec)
        self.assertIn("aborted", report)

    def test_429_on_robots_is_not_absent(self):
        rec = exchanges()
        rec["https://docs.example.com/robots.txt"] = [{"status": 429, "headers": {"retry-after": "1"}}]
        report, _, _ = run(recorded=rec)
        self.assertIn("aborted", report)

    def test_robots_4xx_means_absent_and_the_run_goes_on(self):
        report, _, _ = run()
        help_ = [r for r in report["robots"] if r["host"] == "help.example.com"][0]
        self.assertEqual(help_["verdict"], "absent")
        self.assertEqual(by_node(report)["term.example.support"]["outcome"], "unchanged")


class Unreachable(unittest.TestCase):
    def test_5xx_is_unreadable_never_a_verdict(self):
        rec = exchanges()
        rec["https://docs.example.com/plans"] = [{"status": 500, "body": "oops"}]
        r = by_node(run(recorded=rec)[0])["plan.example.free"]
        self.assertEqual(r["outcome"], "unreadable")
        self.assertEqual(r["httpStatus"], 500)

    def test_429_with_short_retry_after_waits_and_retries_once(self):
        rec = exchanges()
        rec["https://docs.example.com/plans"] = [
            {"status": 429, "headers": {"retry-after": "5"}},
            {"status": 200, "headers": {"content-type": "text/html"}, "file": "plans.html"}]
        report, t, clock = run(recorded=rec)
        self.assertEqual(t.urls().count("https://docs.example.com/plans"), 2)
        self.assertIn(5, clock.slept)
        self.assertEqual(by_node(report)["plan.example.free"]["outcome"], "changed")

    def test_429_twice_stops_the_host_without_a_third_request(self):
        rec = exchanges()
        rec["https://docs.example.com/limits"] = [
            {"status": 429, "headers": {"retry-after": "5"}}, {"status": 429, "headers": {"retry-after": "5"}}]
        report, t, _ = run(recorded=rec)
        self.assertEqual(t.urls().count("https://docs.example.com/limits"), 2)
        r = by_node(report)
        # limit.example.burst is the oldest fact; every later docs.example.com fact is unreadable, unfetched
        for node in ("limit.example.burst", "plan.example.free", "price.example.input", "rule.example.fair-use"):
            self.assertEqual(r[node]["outcome"], "unreadable", node)
            self.assertEqual(r[node]["httpStatus"], 429, node)
            self.assertEqual(r[node]["retryAfterSeconds"], 5, node)
        self.assertNotIn("https://docs.example.com/plans", t.urls())
        self.assertEqual(r["term.example.support"]["outcome"], "unchanged")  # another host goes on

    def test_retry_after_beyond_the_limit_is_not_waited_for(self):
        report, t, clock = run()
        r = by_node(report)["price.example.input"]
        self.assertEqual(t.urls().count("https://docs.example.com/pricing"), 1)
        self.assertEqual(r["outcome"], "unreadable")
        self.assertEqual(r["retryAfterSeconds"], 3600)
        self.assertNotIn(3600, clock.slept)

    def test_page_larger_than_max_bytes_is_unreadable(self):
        rec = exchanges()
        rec["https://docs.example.com/plans"] = [{"status": 200, "headers": {"content-type": "text/html"},
                                                  "body": "<p>" + "x" * 5000 + "</p>"}]
        r = by_node(run(recorded=rec)[0])["plan.example.free"]
        self.assertEqual(r["outcome"], "unreadable")
        self.assertIn("maxBytes", r["reason"])

    def test_page_that_is_not_text_is_unreadable(self):
        rec = exchanges()
        rec["https://docs.example.com/plans"] = [{"status": 200, "headers": {"content-type": "application/pdf"}, "body": "%PDF"}]
        self.assertEqual(by_node(run(recorded=rec)[0])["plan.example.free"]["outcome"], "unreadable")


class Pacing(unittest.TestCase):
    def test_every_request_sends_the_listed_user_agent(self):
        _, t, _ = run()
        self.assertTrue(t.calls)
        self.assertTrue(all(c[1].get("User-Agent") == UA for c in t.calls))

    def test_requests_to_one_host_are_paced(self):
        _, t, _ = run(pace=2.0)
        last = {}
        for url, _h, at in t.calls:
            host = url.split("/")[2]
            if host in last:
                self.assertGreaterEqual((at - last[host]).total_seconds(), 2.0, url)
            last[host] = at

    def test_facts_sharing_a_source_share_one_request(self):
        report, t, _ = run()
        self.assertEqual(t.urls().count("https://docs.example.com/limits"), 1)
        self.assertEqual(by_node(report)["rule.example.fair-use"]["outcome"], "unchanged")

    def test_recently_read_facts_are_not_due(self):
        report, t, _ = run()
        self.assertNotIn("https://docs.example.com/features", t.urls())
        self.assertNotIn("feature.example.fresh", by_node(report))

    def test_run_cap_bounds_requests_and_takes_the_oldest_first(self):
        with GraphsCopy() as root:
            edit_policy(root, lambda p: p.update(maxRequestsPerRun=3))
            report, t, _ = run(root=root)
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(report["requests"], 3)
        # two robots.txt, then the oldest fact's page
        self.assertEqual(t.urls()[2], "https://docs.example.com/limits")
        self.assertNotIn("plan.example.free", by_node(report))

    def test_run_cap_is_shared_by_every_graph_of_the_run(self):
        with GraphsCopy() as root:
            edit_policy(root, lambda p: p.update(maxRequestsPerRun=6))
            clock = FakeClock()
            t = Replay(exchanges(), clock)
            refresher = Refresher(load_policy(root), t, clock)
            refresher.refresh_graph(root, "vendors/example", COMMIT, 7, RUN_URL)
            other = refresher.refresh_graph(root, "vendors/other", COMMIT, 7, RUN_URL)
        self.assertEqual(len(t.calls), 6)
        self.assertEqual(other["requests"], 0)
        self.assertEqual(other["robots"], [])

    def test_host_cap_bounds_requests_to_that_host(self):
        with GraphsCopy() as root:
            edit_policy(root, lambda p: p["hosts"][0].update(maxRequestsPerRun=2))
            report, t, _ = run(root=root)
        self.assertEqual(sum(1 for u in t.urls() if "docs.example.com" in u), 2)
        self.assertEqual(by_node(report)["term.example.support"]["outcome"], "unchanged")


if __name__ == "__main__":
    unittest.main()
