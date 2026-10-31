"""python3 -m factrefresh refresh | download | open (see README.md)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from . import opener
from .graphs import find_graphs, load_policy
from .http import UrllibTransport
from .refresh import DEFAULT_PACE_SECONDS, Refresher


def cmd_refresh(a: argparse.Namespace) -> int:
    commit = a.commit or subprocess.run(["git", "-C", a.graphs, "rev-parse", "HEAD"], capture_output=True,
                                        text=True, check=True).stdout.strip()
    policy = load_policy(a.graphs)
    refresher = Refresher(policy, UrllibTransport(), pace=a.pace)
    notes = []
    lines = ["| Graph | Requests | unchanged | changed | unreadable | Aborted |", "|---|---|---|---|---|---|"]
    for graph in a.graph or find_graphs(a.graphs):
        report = refresher.refresh_graph(a.graphs, graph, commit, a.run_id, a.run_url)
        path = os.path.join(a.out, graph + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        count = {k: sum(1 for r in report["results"] if r["outcome"] == k) for k in ("unchanged", "changed", "unreadable")}
        for g, node, why in refresher.skipped:
            notes.append(f"- {g} `{node}`: {why}")
        lines.append(f"| {graph} | {report['requests']} | {count['unchanged']} | {count['changed']} | "
                     f"{count['unreadable']} | {report.get('aborted', {}).get('reason', '')} |")
    if notes:
        lines += ["", "Left out of the reports:", *notes]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


def cmd_download(a: argparse.Namespace) -> int:
    runs = json.loads(subprocess.run(
        ["gh", "run", "list", "-R", a.runs_repo, "--workflow", a.workflow, "--status", "success",
         "--limit", str(a.limit), "--json", "databaseId,createdAt"], capture_output=True, text=True, check=True).stdout)
    newest = max((r["createdAt"] for r in runs), default=None)
    stale = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if newest is None or newest < stale:
        print(f"warning: no successful {a.workflow} run since {stale[:10]}; check that its schedule is enabled",
              file=sys.stderr)
    for run in runs:
        dest = os.path.join(a.out, str(run["databaseId"]))
        if os.path.isdir(dest):
            continue
        subprocess.run(["gh", "run", "download", str(run["databaseId"]), "-R", a.runs_repo, "-n", "refresh-report",
                        "-D", dest], check=True)
        print(f"downloaded run {run['databaseId']}")
    return 0


def cmd_open(a: argparse.Namespace) -> int:
    repo = opener.Repo(a.graphs)
    main = repo.fetch_main()
    schema = repo.show(main, "spec/refresh-report/schema.json")
    sources = repo.show(main, "policy/sources.json")
    if schema is None or sources is None:
        print("main of the graphs clone has no spec/refresh-report/schema.json or policy/sources.json", file=sys.stderr)
        return 1
    loaded = opener.load_reports(a.reports)
    bad = opener.validate([p for p, _ in loaded], schema, a.validator)
    for p, err in bad.items():
        print(f"refused {p}: does not match spec/refresh-report: {err}", file=sys.stderr)
    reports = [r for p, r in loaded if p not in bad]
    plan = opener.plan_all(reports, repo, main, json.loads(sources)["unreadableRunsBeforeIssue"])
    for e in plan.errors:
        print(e, file=sys.stderr)
    for n in plan.notes:
        print(f"note {n}")
    for line in opener.apply(plan, opener.GitHub(a.graphs_repo), repo, main, a.dry_run):
        print(line)
    return 1 if bad or plan.errors else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="factrefresh")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="re-read every due fact of a graphs checkout and write one report per graph")
    r.add_argument("--graphs", required=True, help="checkout of the graphs repository")
    r.add_argument("--out", required=True, help="directory the reports are written to, as <domain>/<vendor>.json")
    r.add_argument("--run-id", type=int, required=True)
    r.add_argument("--run-url", required=True)
    r.add_argument("--commit", help="the checkout's commit (default: git rev-parse HEAD)")
    r.add_argument("--graph", action="append", help="only this <domain>/<vendor> (repeatable)")
    r.add_argument("--pace", type=float, default=DEFAULT_PACE_SECONDS, help="seconds between two requests to one host")
    r.set_defaults(fn=cmd_refresh)

    d = sub.add_parser("download", help="download the refresh-report artifacts of recent successful runs")
    d.add_argument("--runs-repo", required=True, help="owner/name of the repository the workflow runs in")
    d.add_argument("--workflow", default="fact-refresh.yml")
    d.add_argument("--limit", type=int, default=10)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_download)

    o = sub.add_parser("open", help="open the pull requests and issues the reports call for")
    o.add_argument("--reports", required=True, help="directory holding reports, any depth")
    o.add_argument("--graphs", required=True, help="local clone of the graphs repository (read only)")
    o.add_argument("--graphs-repo", required=True, help="owner/name of the graphs repository")
    o.add_argument("--validator", default=os.environ.get("FACTREFRESH_JSONSCHEMA", "jsonschema"),
                   help="the sourcemeta jsonschema CLI")
    o.add_argument("--dry-run", action="store_true", help="read and plan, write nothing")
    o.set_defaults(fn=cmd_open)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
