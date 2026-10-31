"""The opener's plan and its writes, against a fake clone and a fake gh that records every call."""
from __future__ import annotations

import copy
import json
import os
import re
import unittest

from factrefresh import opener
from tests.support import FIXTURES, GRAPHS

MAIN = "1" * 40
COMMIT = "0e6f5ed237dd52f9fe02a33f8c6b8ec653a3a2c3"  # the golden report's commit
GRAPH = "vendors/example"
DATE_LINE = re.compile(r'^\s*"(measuredAt|fetched_at)": "[^"]*",?$')


def node_files():
    nodes = os.path.join(GRAPHS, GRAPH, "graph", "nodes")
    out = {}
    for name in os.listdir(nodes):
        with open(os.path.join(nodes, name), encoding="utf-8") as f:
            out[f"{GRAPH}/graph/nodes/{name}"] = f.read()
    return out


class FakeRepo:
    def __init__(self, at_main=None, at_commit=None, ancestors=(COMMIT,)):
        self.files = {MAIN: at_main if at_main is not None else node_files(),
                      COMMIT: at_commit if at_commit is not None else node_files()}
        self.ancestors = set(ancestors)

    def show(self, sha, path):
        return self.files.get(sha, {}).get(path)

    def is_ancestor(self, commit, sha):
        return commit in self.ancestors

    def tree(self, sha):
        return "t" * 40


class FakeGh:
    def __init__(self, open_prs=(), ever=(), issues=(), comments=()):
        self.repo, self.owner = "practice-library/graphs", "practice-library"
        self.open_prs, self.ever, self.issues, self.comments = list(open_prs), list(ever), list(issues), list(comments)
        self.writes = []

    def api(self, method, path, body=None, ok404=False):
        if method != "GET":
            self.writes.append((method, path, body))
            return {"sha": "c" * 40, "number": 99}
        if "pulls?state=open" in path:
            return [{"head": {"ref": h}} for h in self.open_prs]
        if "pulls?state=all" in path:
            return [{"number": 5} for b in self.ever if path.split("head=practice-library:")[1].startswith(b + "&")]
        if "/issues?state=open" in path:
            return self.issues
        if path.endswith("/comments?per_page=100"):
            return [{"body": c} for c in self.comments]
        return None  # refs and labels: absent


def golden():
    with open(os.path.join(FIXTURES, "expect", "vendors-example.json"), encoding="utf-8") as f:
        return json.load(f)


def report(run_id, results, aborted=None, started="2026-10-31T12:00:00Z"):
    r = golden()
    r["run"].update(id=run_id, url=f"https://github.com/practice-library/machinery/actions/runs/{run_id}", startedAt=started)
    r["results"] = results
    if aborted:
        r["aborted"] = {"reason": aborted}
        r["robots"][0]["verdict"] = "disallowed"
    return r


def prs(plan):
    return [a for a in plan.actions if isinstance(a, opener.PullRequest)]


def issues(plan):
    return [a for a in plan.actions if isinstance(a, opener.Issue)]


class Planning(unittest.TestCase):
    def test_unchanged_facts_make_one_date_bump_and_nothing_else(self):
        plan = opener.plan_all([golden()], FakeRepo(), MAIN, 3)
        bump = [p for p in prs(plan) if "/bump-" in p.branch]
        self.assertEqual(len(bump), 1)
        self.assertEqual(sorted(bump[0].files), [f"{GRAPH}/graph/nodes/limit.example.burst.json",
                                                 f"{GRAPH}/graph/nodes/rule.example.fair-use.json",
                                                 f"{GRAPH}/graph/nodes/term.example.support.json"])
        old_files = node_files()
        for path, new in bump[0].files.items():
            old = old_files[path]
            changed = [l for l in set(old.splitlines()) ^ set(new.splitlines())]
            self.assertTrue(changed and all(DATE_LINE.match(l) for l in changed), path)
            n = json.loads(new)
            self.assertEqual(n["measuredAt"], n["attrs"]["fetched_at"][:10])
            self.assertGreater(n["attrs"]["fetched_at"], json.loads(old)["attrs"]["fetched_at"])
        self.assertIn("| `limit.example.burst` | 2026-09-01T10:00:00Z | 2026-10-31T12:00:02Z |", bump[0].body)

    def test_changed_fact_gets_its_own_pull_request_that_leaves_the_node_alone(self):
        plan = opener.plan_all([golden()], FakeRepo(), MAIN, 3)
        changed = [p for p in prs(plan) if "/changed-" in p.branch]
        self.assertEqual(len(changed), 1)
        self.assertEqual(list(changed[0].files), [f"{GRAPH}/CHANGES.md"])
        self.assertIn("plan.example.free", changed[0].files[f"{GRAPH}/CHANGES.md"])
        self.assertIn("Old fetched_at: 2026-09-02T10:00:00Z", changed[0].body)
        self.assertIn("New fetched_at: 2026-10-31T12:00:04Z", changed[0].body)

    def test_report_whose_commit_is_not_on_main_is_refused(self):
        plan = opener.plan_all([golden()], FakeRepo(ancestors=()), MAIN, 3)
        self.assertEqual(plan.actions, [])
        self.assertTrue(plan.errors)

    def test_fact_changed_on_main_since_the_report_is_not_bumped(self):
        at_main = node_files()
        path = f"{GRAPH}/graph/nodes/limit.example.burst.json"
        at_main[path] = at_main[path].replace("20 requests", "25 requests")
        plan = opener.plan_all([golden()], FakeRepo(at_main=at_main), MAIN, 3)
        bump = [p for p in prs(plan) if "/bump-" in p.branch][0]
        self.assertNotIn(path, bump.files)

    def test_fetched_at_never_goes_backwards(self):
        rep = golden()
        for r in rep["results"]:
            if r["outcome"] == "unchanged":
                r["fetchedAt"] = "2026-08-01T00:00:00Z"
        plan = opener.plan_all([rep], FakeRepo(), MAIN, 3)
        self.assertFalse([p for p in prs(plan) if "/bump-" in p.branch])

    def test_a_bump_that_would_touch_other_lines_is_dropped(self):
        one_line = '{"id": "x.y", "measuredAt": "2026-01-01", "attrs": {"fetched_at": "2026-01-01T00:00:00Z"}}\n'
        self.assertIsNone(opener.bump_text(one_line, "2026-02-02", "2026-02-02T00:00:00Z"))

    def test_unreadable_gets_an_issue_only_after_enough_runs_in_a_row(self):
        miss = {"node": "plan.example.free", "source": "https://docs.example.com/plans", "httpStatus": 500,
                "fetchedAt": "2026-10-31T12:00:04Z", "outcome": "unreadable", "reason": "unreachable: HTTP 500"}
        hist = [report(i, [miss], started=f"2026-10-{20 + i}T12:00:00Z") for i in (1, 2)]
        self.assertFalse(issues(opener.plan_all(hist, FakeRepo(), MAIN, 3)))
        hist.append(report(3, [miss], started="2026-10-23T12:00:00Z"))
        got = issues(opener.plan_all(hist, FakeRepo(), MAIN, 3))
        self.assertEqual([i.title for i in got], ["unreadable-source: plan.example.free"])
        self.assertEqual(got[0].label, "unreadable-source")

    def test_a_readable_run_breaks_the_streak(self):
        miss = {"node": "plan.example.free", "source": "https://docs.example.com/plans", "httpStatus": 500,
                "fetchedAt": "2026-10-31T12:00:04Z", "outcome": "unreadable", "reason": "unreachable: HTTP 500"}
        ok = [r for r in golden()["results"] if r["node"] == "plan.example.free"]
        hist = [report(1, [miss], started="2026-10-21T12:00:00Z"), report(2, ok, started="2026-10-22T12:00:00Z"),
                report(3, [miss], started="2026-10-23T12:00:00Z"), report(4, [miss], started="2026-10-24T12:00:00Z")]
        self.assertFalse(issues(opener.plan_all(hist, FakeRepo(), MAIN, 3)))

    def test_aborted_run_opens_a_robots_issue_and_no_pull_request(self):
        plan = opener.plan_all([report(9, [], aborted="blocked: robots.txt of docs.example.com disallows /plans")],
                               FakeRepo(), MAIN, 3)
        self.assertEqual(prs(plan), [])
        self.assertEqual([(i.label, i.title) for i in issues(plan)], [("robots", "robots: vendors/example")])
        self.assertIn("docs.example.com", issues(plan)[0].body)


class Applying(unittest.TestCase):
    def plan(self):
        return opener.plan_all([golden()], FakeRepo(), MAIN, 3)

    def test_fresh_pull_requests_are_opened_from_a_new_branch(self):
        gh = FakeGh()
        opener.apply(self.plan(), gh, FakeRepo(), MAIN, dry_run=False)
        pulls = [w for w in gh.writes if w[1].endswith("/pulls")]
        self.assertEqual(len(pulls), 2)
        refs = [w[2]["ref"] for w in gh.writes if w[1].endswith("/git/refs")]
        self.assertEqual(refs, ["refs/heads/fact-refresh/vendors-example/bump-7",
                                "refs/heads/fact-refresh/vendors-example/changed-plan.example.free-7"])
        self.assertFalse([w for w in gh.writes if "merge" in w[1]])  # never merges

    def test_no_second_pull_request_while_one_is_open(self):
        gh = FakeGh(open_prs=["fact-refresh/vendors-example/bump-3",
                              "fact-refresh/vendors-example/changed-plan.example.free-3"])
        out = opener.apply(self.plan(), gh, FakeRepo(), MAIN, dry_run=False)
        self.assertEqual(gh.writes, [])
        self.assertTrue(all(o.startswith("skip") for o in out))

    def test_rerun_of_the_same_report_opens_nothing(self):
        gh = FakeGh(ever=["fact-refresh/vendors-example/bump-7",
                          "fact-refresh/vendors-example/changed-plan.example.free-7"])
        opener.apply(self.plan(), gh, FakeRepo(), MAIN, dry_run=False)
        self.assertEqual(gh.writes, [])

    def test_dry_run_writes_nothing(self):
        gh = FakeGh()
        opener.apply(self.plan(), gh, FakeRepo(), MAIN, dry_run=True)
        self.assertEqual(gh.writes, [])

    def test_open_issue_gets_one_comment_per_run(self):
        issue = opener.Issue("robots", "robots: vendors/example", "body run-url-9", "run-url-9")
        plan = opener.Plan(actions=[issue])
        gh = FakeGh(issues=[{"number": 4, "title": issue.title, "body": "first"}], comments=["run-url-9 seen"])
        opener.apply(plan, gh, FakeRepo(), MAIN, dry_run=False)
        self.assertEqual(gh.writes, [])
        gh = FakeGh(issues=[{"number": 4, "title": issue.title, "body": "first"}], comments=["run-url-8"])
        opener.apply(plan, gh, FakeRepo(), MAIN, dry_run=False)
        self.assertEqual([w[1] for w in gh.writes], ["repos/practice-library/graphs/issues/4/comments"])

    def test_new_issue_is_opened_with_its_label(self):
        plan = opener.Plan(actions=[opener.Issue("robots", "robots: vendors/example", "b", "u")])
        gh = FakeGh()
        opener.apply(plan, gh, FakeRepo(), MAIN, dry_run=False)
        made = [w for w in gh.writes if w[1].endswith("/issues")]
        self.assertEqual(made[0][2]["labels"], ["robots"])



def outside_code(body):
    """The parts of a markdown body that are not inside an inline code span."""
    return "".join(p for i, p in enumerate(body.split("`")) if i % 2 == 0)


class Rework(unittest.TestCase):
    def test_a_fact_whose_source_differs_from_the_report_is_not_bumped(self):
        rep = golden()
        for r in rep["results"]:
            if r["node"] == "limit.example.burst":
                r["source"] = "https://docs.example.com/limits-old"
        bump = [p for p in prs(opener.plan_all([rep], FakeRepo(), MAIN, 3)) if "/bump-" in p.branch][0]
        self.assertNotIn(f"{GRAPH}/graph/nodes/limit.example.burst.json", bump.files)
        self.assertIn(f"{GRAPH}/graph/nodes/rule.example.fair-use.json", bump.files)

    def test_no_more_than_three_refresh_pull_requests_are_open_at_once(self):
        rep = golden()
        extra = [dict(r) for r in rep["results"] if r["outcome"] == "changed"][0]
        for node in ("limit.example.burst", "rule.example.fair-use", "term.example.support"):
            rep["results"] = [r for r in rep["results"] if r["node"] != node] + [dict(extra, node=node)]
        plan = opener.plan_all([rep], FakeRepo(), MAIN, 3)
        self.assertEqual(len(prs(plan)), 4)
        gh = FakeGh(open_prs=["unrelated-branch"])
        out = opener.apply(plan, gh, FakeRepo(), MAIN, dry_run=False)
        self.assertEqual(len([w for w in gh.writes if w[1].endswith("/pulls")]), 3)
        self.assertTrue(out[-1].startswith("skip") and "most allowed" in out[-1])
        gh = FakeGh(open_prs=["fact-refresh/vendors-other/bump-1", "fact-refresh/vendors-other/bump-2",
                              "fact-refresh/vendors-other/bump-3"])
        opener.apply(plan, gh, FakeRepo(), MAIN, dry_run=False)
        self.assertEqual(gh.writes, [])

    def test_report_text_stays_inside_its_code_span(self):
        rep = golden()
        for r in rep["results"]:
            if r["outcome"] == "changed":
                r["reason"] = "quote-gone: x` then\n- [click](https://bad.example.net) @maintainers"
        changed = [p for p in prs(opener.plan_all([rep], FakeRepo(), MAIN, 3)) if "/changed-" in p.branch][0]
        self.assertNotIn("[click]", outside_code(changed.body))
        self.assertNotIn("@maintainers", outside_code(changed.body))
        self.assertNotIn("\n- [click]", changed.body)

    def test_no_posted_body_mentions_anyone(self):
        run_url = "https://github.com/practice-library/machinery/actions/runs/7/@practice-library/maintainers"
        rep = report(7, golden()["results"])
        rep["run"]["url"] = run_url
        plan = opener.plan_all([rep], FakeRepo(), MAIN, 3)
        plan.actions.append(opener.Issue("robots", "robots: vendors/example", f"see {run_url}", run_url))
        gh = FakeGh()
        opener.apply(plan, gh, FakeRepo(), MAIN, dry_run=False)
        bodies = [w[2]["body"] for w in gh.writes if w[2] and "body" in w[2]]
        self.assertEqual(len(bodies), 3)
        for body in bodies:
            self.assertNotIn("@", outside_code(body))

if __name__ == "__main__":
    unittest.main()
