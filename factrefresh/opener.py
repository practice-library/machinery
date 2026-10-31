"""The PR opener: reads refresh reports and opens pull requests and issues in the graphs repository, as
spec/README.md of that repository describes. It runs on a maintainer's machine as the organisation's app,
never fetches a source, and never merges anything.

- unchanged: one pull request per graph bumping measuredAt and attrs.fetched_at of those facts, nothing else
  (the only kind the graphs repository's bump-only check lets auto-merge);
- changed: one pull request per fact adding a line to the graph's CHANGES.md; the node is not touched;
- unreadable: an issue labelled unreadable-source once a fact is unreadable in the last
  unreadableRunsBeforeIssue reports, or a comment on the one already open;
- aborted: an issue labelled robots naming the host and the reason, or a comment on the one already open.

Reruns are idempotent: a branch carries the run id, a pull request is not opened while one for the same
graph (bump) or fact (changed) is open or was ever opened from that branch, and an issue gets at most one
comment per run.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

BRANCH_ROOT = "fact-refresh"
_DATE_LINE = re.compile(r'^\s*"(measuredAt|fetched_at)": "[^"]*",?$')


# -- data the opener acts on ---------------------------------------------------------------------------------
@dataclass
class PullRequest:
    graph: str
    prefix: str  # no new pull request while one from a branch with this prefix is open
    branch: str
    title: str
    body: str
    message: str
    files: Dict[str, str]  # path -> full new content


@dataclass
class Issue:
    label: str
    title: str
    body: str
    run_url: str  # at most one comment per run


@dataclass
class Plan:
    actions: List[object] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # what was skipped, and why
    errors: List[str] = field(default_factory=list)  # reports refused


class Repo:
    """A local clone of the graphs repository, read only: the opener reads files at commits from it."""

    def __init__(self, path: str) -> None:
        self.path = path

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True, check=check)

    def fetch_main(self) -> str:
        self._git("fetch", "--quiet", "origin", "main")
        return self._git("rev-parse", "origin/main").stdout.strip()

    def tree(self, sha: str) -> str:
        return self._git("rev-parse", f"{sha}^{{tree}}").stdout.strip()

    def show(self, sha: str, path: str) -> Optional[str]:
        out = self._git("show", f"{sha}:{path}", check=False)
        return out.stdout if out.returncode == 0 else None

    def is_ancestor(self, commit: str, sha: str) -> bool:
        return self._git("merge-base", "--is-ancestor", commit, sha, check=False).returncode == 0


# -- planning (no network, no writes) ------------------------------------------------------------------------
def slug(graph: str) -> str:
    return graph.replace("/", "-")


def bump_text(text: str, measured: str, fetched: str) -> Optional[str]:
    """The node file with only its measuredAt and attrs.fetched_at values replaced, every other byte kept;
    None when that cannot be done as a change of those two lines alone."""
    new, n1 = re.subn(r'("measuredAt": ")[^"]*(")', lambda m: m.group(1) + measured + m.group(2), text, count=1)
    new, n2 = re.subn(r'("fetched_at": ")[^"]*(")', lambda m: m.group(1) + fetched + m.group(2), new, count=1)
    if n1 != 1 or n2 != 1:
        return None
    want = json.loads(text)
    want["measuredAt"] = measured
    want["attrs"]["fetched_at"] = fetched
    if json.loads(new) != want:
        return None
    changed = [l for l in difflib.unified_diff(text.splitlines(), new.splitlines(), lineterm="", n=0)
               if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    if not all(_DATE_LINE.match(l[1:]) for l in changed):
        return None
    return new


def _streak(history: List[dict], node: str) -> List[dict]:
    """The latest reports, newest first, in which the node is unreadable without a break."""
    out = []
    for rep in reversed(history):
        hit = next((r for r in rep["results"] if r["node"] == node), None)
        if hit is None or hit["outcome"] != "unreadable":
            break
        out.append((rep, hit))
    return out


def plan_graph(graph: str, history: List[dict], repo: Repo, main: str, runs_before_issue: int) -> Plan:
    """history: this graph's accepted reports, oldest first; the newest one decides the pull requests."""
    plan = Plan()
    latest = history[-1]
    run = latest["run"]
    commit = latest["commit"]
    run_line = f"Refresh run [{run['id']}]({run['url']}) read `{graph}` at {commit}."

    if "aborted" in latest:
        hosts = ", ".join(r["host"] for r in latest["robots"] if r["verdict"] in ("disallowed", "unreachable"))
        plan.actions.append(Issue(
            label="robots", title=f"robots: {graph}", run_url=run["url"],
            body=f"The refresh of `{graph}` stopped before fetching any page.\n\n- Host: {hosts}\n"
                 f"- Reason: {latest['aborted']['reason']}\n\n{run_line}\n"))
        return plan

    def node_path(node_id: str) -> str:
        return f"{graph}/graph/nodes/{node_id}.json"

    # unchanged: one date-bump pull request
    files: Dict[str, str] = {}
    rows: List[str] = []
    for r in latest["results"]:
        if r["outcome"] != "unchanged":
            continue
        path = node_path(r["node"])
        at_main, at_commit = repo.show(main, path), repo.show(commit, path)
        if at_main is None:
            plan.notes.append(f"{r['node']}: not on main any more; no bump")
            continue
        if at_commit is None or json.loads(at_main) != json.loads(at_commit):
            plan.notes.append(f"{r['node']}: changed on main since {commit[:12]}; no bump")
            continue
        node = json.loads(at_main)
        if node["source"] != r["source"]:
            plan.notes.append(f"{r['node']}: source differs from the report; no bump")
            continue
        old = node["attrs"]["fetched_at"]
        if not r["fetchedAt"] > old:
            plan.notes.append(f"{r['node']}: fetched_at {old} is not older than {r['fetchedAt']}; no bump")
            continue
        new = bump_text(at_main, r["fetchedAt"][:10], r["fetchedAt"])
        if new is None:
            plan.notes.append(f"{r['node']}: the bump would change more than the two date lines; no bump")
            continue
        files[path] = new
        note = f" ({r['reason']})" if r.get("reason") else ""
        rows.append(f"| `{r['node']}` | {old} | {r['fetchedAt']} |{note}")
    if files:
        prefix = f"{BRANCH_ROOT}/{slug(graph)}/bump-"
        plan.actions.append(PullRequest(
            graph=graph, prefix=prefix, branch=f"{prefix}{run['id']}",
            title=f"{graph}: {len(files)} facts still match their source",
            message=f"{graph}: bump measuredAt and fetched_at of {len(files)} facts\n\n"
                    f"Their quotes are still on their source pages (refresh run {run['id']}).\n",
            body="Every quote below is still on its source page, so only `measuredAt` and `attrs.fetched_at` "
                 "change.\n\n| Fact | Old fetched_at | New fetched_at |\n|---|---|---|\n"
                 + "\n".join(rows) + f"\n\n{run_line}\n",
            files=files))

    # changed: one pull request per fact, recorded in CHANGES.md, the node untouched
    changes_path = f"{graph}/CHANGES.md"
    for r in latest["results"]:
        if r["outcome"] != "changed":
            continue
        at_main = repo.show(main, node_path(r["node"]))
        if at_main is None:
            plan.notes.append(f"{r['node']}: not on main any more; no pull request")
            continue
        old = json.loads(at_main)["attrs"]["fetched_at"]
        current = repo.show(main, changes_path) or f"# Changes to {graph}\n\n"
        if not current.endswith("\n"):
            current += "\n"
        line = (f"- {r['fetchedAt'][:10]} `{r['node']}`: the quote is no longer on {r['finalUrl']} "
                f"(last confirmed {old}, read {r['fetchedAt']}, run {run['url']}).\n")
        prefix = f"{BRANCH_ROOT}/{slug(graph)}/changed-{r['node']}-"
        plan.actions.append(PullRequest(
            graph=graph, prefix=prefix, branch=f"{prefix}{run['id']}",
            title=f"{graph}: quote of {r['node']} no longer on its source",
            message=f"{graph}: record that the quote of {r['node']} is no longer on its source\n",
            body=f"The refresh read the source of `{r['node']}` and the stored quote is not in its text. "
                 f"This records the finding in `{changes_path}` and does not touch the node; the corrected "
                 f"node is pushed to this branch.\n\n"
                 f"- Source: {r['source']}\n- Read from: {r['finalUrl']} (HTTP {r['httpStatus']}, "
                 f"text sha256 `{r['sha256']}`)\n- Old fetched_at: {old}\n- New fetched_at: {r['fetchedAt']}\n"
                 f"- Reason: {r['reason']}\n\n{run_line}\n",
            files={changes_path: current + line}))

    # unreadable: an issue once the streak is long enough
    for r in latest["results"]:
        if r["outcome"] != "unreadable":
            continue
        streak = _streak(history, r["node"])
        if len(streak) < runs_before_issue:
            plan.notes.append(f"{r['node']}: unreadable in {len(streak)} of the last {runs_before_issue} reports; no issue yet")
            continue
        lines = "\n".join(f"- run [{rep['run']['id']}]({rep['run']['url']}) at {hit['fetchedAt']}: {hit['reason']}"
                          for rep, hit in streak[:runs_before_issue])
        plan.actions.append(Issue(
            label="unreadable-source", title=f"unreadable-source: {r['node']}", run_url=run["url"],
            body=f"The source of `{r['node']}` in `{graph}` could not be read in the last {runs_before_issue} "
                 f"refresh runs.\n\n- Source: {r['source']}\n{lines}\n"))
    return plan


def plan_all(reports: List[dict], repo: Repo, main: str, runs_before_issue: int) -> Plan:
    total = Plan()
    by_graph: Dict[str, List[dict]] = {}
    for rep in reports:
        if not repo.is_ancestor(rep["commit"], main):
            total.errors.append(f"{rep['graph']} run {rep['run']['id']}: commit {rep['commit']} is not on main; refused")
            continue
        by_graph.setdefault(rep["graph"], []).append(rep)
    for graph in sorted(by_graph):
        history = sorted(by_graph[graph], key=lambda r: (r["run"]["startedAt"], r["run"]["id"]))
        p = plan_graph(graph, history, repo, main, runs_before_issue)
        total.actions += p.actions
        total.notes += p.notes
    return total


# -- applying (the only part that writes) --------------------------------------------------------------------
class GitHub:
    """gh api, as whoever GH_TOKEN names: the app, through the app wrapper."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.owner = repo.split("/", 1)[0]

    def api(self, method: str, path: str, body: Optional[dict] = None, ok404: bool = False):
        cmd = ["gh", "api", "-X", method, path]
        if method == "GET" and "per_page" in path:
            cmd += ["--paginate", "--slurp"]
        if body is not None:
            cmd += ["--input", "-"]
        out = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                             capture_output=True, text=True)
        if out.returncode != 0:
            if ok404 and "404" in out.stderr:
                return None
            raise RuntimeError(f"gh api {method} {path}: {out.stderr.strip()[:300]}")
        data = json.loads(out.stdout) if out.stdout.strip() else None
        if "--slurp" in cmd and isinstance(data, list):
            data = [x for page in data for x in page]
        return data


def apply(plan: Plan, gh, repo: Repo, main: str, dry_run: bool) -> List[str]:
    done: List[str] = []
    open_prs = gh.api("GET", f"repos/{gh.repo}/pulls?state=open&per_page=100")
    open_heads = [p["head"]["ref"] for p in open_prs]
    for act in plan.actions:
        if isinstance(act, PullRequest):
            done.append(_apply_pr(act, gh, repo, main, open_heads, dry_run))
        else:
            done.append(_apply_issue(act, gh, dry_run))
    return done


def _apply_pr(pr: PullRequest, gh, repo: Repo, main: str, open_heads: List[str], dry_run: bool) -> str:
    busy = [h for h in open_heads if h.startswith(pr.prefix)]
    if busy:
        return f"skip {pr.branch}: {busy[0]} is still open"
    ever = gh.api("GET", f"repos/{gh.repo}/pulls?state=all&head={gh.owner}:{pr.branch}&per_page=100")
    if ever:
        return f"skip {pr.branch}: pull request #{ever[0]['number']} was already opened from it"
    if dry_run:
        return f"would open {pr.branch}: {pr.title} ({len(pr.files)} files)"
    ref = gh.api("GET", f"repos/{gh.repo}/git/ref/heads/{pr.branch}", ok404=True)
    if ref is None:  # a branch left by an interrupted earlier run is reused as it is
        tree = gh.api("POST", f"repos/{gh.repo}/git/trees", {
            "base_tree": repo.tree(main),
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in sorted(pr.files.items())]})
        commit = gh.api("POST", f"repos/{gh.repo}/git/commits",
                        {"message": pr.message, "tree": tree["sha"], "parents": [main]})
        gh.api("POST", f"repos/{gh.repo}/git/refs", {"ref": f"refs/heads/{pr.branch}", "sha": commit["sha"]})
    made = gh.api("POST", f"repos/{gh.repo}/pulls",
                  {"title": pr.title, "head": pr.branch, "base": "main", "body": pr.body})
    open_heads.append(pr.branch)
    return f"opened #{made['number']} from {pr.branch}"


def _apply_issue(issue: Issue, gh, dry_run: bool) -> str:
    found = [i for i in gh.api("GET", f"repos/{gh.repo}/issues?state=open&labels={issue.label}&per_page=100")
             if i["title"] == issue.title and "pull_request" not in i]
    if found:
        num = found[0]["number"]
        said = [found[0].get("body") or ""] + [c.get("body") or "" for c in
                                                gh.api("GET", f"repos/{gh.repo}/issues/{num}/comments?per_page=100")]
        if any(issue.run_url in s for s in said):
            return f"skip issue #{num}: already has {issue.run_url}"
        if dry_run:
            return f"would comment on #{num}"
        gh.api("POST", f"repos/{gh.repo}/issues/{num}/comments", {"body": issue.body})
        return f"commented on #{num}"
    if dry_run:
        return f"would open issue: {issue.title}"
    if gh.api("GET", f"repos/{gh.repo}/labels/{issue.label}", ok404=True) is None:
        gh.api("POST", f"repos/{gh.repo}/labels", {"name": issue.label, "color": "d93f0b"})
    made = gh.api("POST", f"repos/{gh.repo}/issues", {"title": issue.title, "body": issue.body, "labels": [issue.label]})
    return f"opened issue #{made['number']}: {issue.title}"


# -- reading reports ------------------------------------------------------------------------------------------
def load_reports(directory: str) -> List[Tuple[str, dict]]:
    out = []
    for base, _dirs, names in sorted(os.walk(directory)):
        for name in sorted(names):
            if name.endswith(".json"):
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as f:
                    out.append((path, json.load(f)))
    return out


def validate(paths: List[str], schema_text: str, validator: str) -> Dict[str, str]:
    """Each report against spec/refresh-report/schema.json on main; returns path -> error for the failures."""
    bad: Dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        schema = os.path.join(tmp, "schema.json")
        with open(schema, "w", encoding="utf-8") as f:
            f.write(schema_text)
        for p in paths:
            out = subprocess.run([validator, "validate", schema, p], capture_output=True, text=True)
            if out.returncode != 0:
                bad[p] = (out.stdout + out.stderr).strip()[:300] or f"exit {out.returncode}"
    return bad
