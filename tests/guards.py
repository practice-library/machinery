"""Each guard, removed, must make its test fail.

For every row: copy the package and the tests to a temporary directory, replace the guard's code (which must
occur exactly once, so a row cannot silently stop applying), run the named test and expect it to fail.
Run: python3 tests/guards.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R, O = "factrefresh/refresh.py", "factrefresh/opener.py"
T, P = "tests.test_refresh", "tests.test_opener"

GUARDS = [
    # (what the guard keeps true, file, code, code without the guard, the test that must then fail)
    ("a host listed for another vendor is not fetched", "factrefresh/graphs.py",
     "return h if h is not None and h.vendor == vendor else None", "return h",
     f"{T}.OnlyListedHosts.test_host_listed_for_another_vendor_is_not_fetched_for_this_one"),
    ("a redirect to an unlisted host is not followed", R,
     "if listed is None:\n                    return Page(", "if False:\n                    return Page(",
     f"{T}.OnlyListedHosts.test_redirect_to_an_unlisted_host_is_not_followed"),
    ("a robots.txt disallow aborts before any page", R, "if aborts:", "if False:",
     f"{T}.RobotsFirst.test_disallow_aborts_before_any_page"),
    ("a robots.txt 5xx counts as a disallow", R,
     "if 400 <= resp.status < 500 and resp.status != 429:", "if 400 <= resp.status < 600:",
     f"{T}.RobotsFirst.test_unreachable_robots_aborts"),
    ("a robots.txt 429 is not taken as absent", R,
     "if 400 <= resp.status < 500 and resp.status != 429:", "if 400 <= resp.status < 500:",
     f"{T}.RobotsFirst.test_429_on_robots_is_not_absent"),
    ("our own robots.txt group replaces *", "factrefresh/robots.py",
     "if any(token in a for a, _ in groups):", "if False:",
     f"{T}.RobotsFirst.test_our_own_group_overrides_star"),
    ("robots.txt wildcards match", "factrefresh/robots.py",
     '".*" if ch == "*" else re.escape(ch)', "re.escape(ch)",
     "tests.test_text.Robots.test_wildcards_and_end_anchor"),
    ("a page is retried once, never twice", R, "if not retried and wait is not None", "if wait is not None",
     f"{T}.Unreachable.test_429_twice_stops_the_host_without_a_third_request"),
    ("a long Retry-After is not waited for", R, "and wait <= self.policy.retry_after_max:", ":",
     f"{T}.Unreachable.test_retry_after_beyond_the_limit_is_not_waited_for"),
    ("a host answering 429 twice is stopped for the run", R, "stopped[host.host] = page", "pass",
     f"{T}.Unreachable.test_429_twice_stops_the_host_without_a_third_request"),
    ("a 5xx is unreadable, never a verdict", R, "if not 200 <= resp.status < 300:", "if False:",
     f"{T}.Unreachable.test_5xx_is_unreadable_never_a_verdict"),
    ("a page over maxBytes is unreadable", R, "if resp.truncated:", "if False:",
     f"{T}.Unreachable.test_page_larger_than_max_bytes_is_unreadable"),
    ("a page that is not text is unreadable", R, "if mime and not (", "if False and not (",
     f"{T}.Unreachable.test_page_that_is_not_text_is_unreadable"),
    ("the run-wide cap", R, "if self.run_requests >= self.policy.max_requests:", "if False:",
     f"{T}.Pacing.test_run_cap_bounds_requests_and_takes_the_oldest_first"),
    ("the per-host cap", R, "if self.host_requests.get(host.host, 0) >= host.max_requests:", "if False:",
     f"{T}.Pacing.test_host_cap_bounds_requests_to_that_host"),
    ("requests to one host are paced", R, "if wait > 0:\n                self.clock.sleep(wait)",
     "if False:\n                self.clock.sleep(wait)", f"{T}.Pacing.test_requests_to_one_host_are_paced"),
    ("the listed user agent is sent", R, '"User-Agent": self.policy.user_agent', '"User-Agent": "Python-urllib"',
     f"{T}.Pacing.test_every_request_sends_the_listed_user_agent"),
    ("facts sharing a source share one request", R, "page = pages.get(src)", "page = None",
     f"{T}.Pacing.test_facts_sharing_a_source_share_one_request"),
    ("facts read within minIntervalDays are not due", R, "if age < host.min_interval_days:", "if False:",
     f"{T}.Pacing.test_recently_read_facts_are_not_due"),
    ("script text is not page text", "factrefresh/text.py", "if not self.skip:", "if True:",
     "tests.test_text.Text.test_script_text_is_not_page_text"),
    ("a report whose commit is not on main is refused", O,
     'if not repo.is_ancestor(rep["commit"], main):', "if False:",
     f"{P}.Planning.test_report_whose_commit_is_not_on_main_is_refused"),
    ("a fact changed on main since the report is not bumped", O,
     "if at_commit is None or json.loads(at_main) != json.loads(at_commit):", "if at_commit is None:",
     f"{P}.Planning.test_fact_changed_on_main_since_the_report_is_not_bumped"),
    ("fetched_at never goes backwards", O, 'if not r["fetchedAt"] > old:', "if False:",
     f"{P}.Planning.test_fetched_at_never_goes_backwards"),
    ("a bump changes the two date lines only", O,
     "if not all(_DATE_LINE.match(l[1:]) for l in changed):", "if False:",
     f"{P}.Planning.test_a_bump_that_would_touch_other_lines_is_dropped"),
    ("an unreadable issue waits for enough runs", O, "if len(streak) < runs_before_issue:", "if False:",
     f"{P}.Planning.test_unreadable_gets_an_issue_only_after_enough_runs_in_a_row"),
    ("a readable run breaks the unreadable streak", O,
     'if hit is None or hit["outcome"] != "unreadable":\n            break',
     'if hit is None or hit["outcome"] != "unreadable":\n            continue',
     f"{P}.Planning.test_a_readable_run_breaks_the_streak"),
    ("no second pull request while one is open", O, "if busy:", "if False:",
     f"{P}.Applying.test_no_second_pull_request_while_one_is_open"),
    ("a rerun opens nothing", O, "if ever:", "if False:", f"{P}.Applying.test_rerun_of_the_same_report_opens_nothing"),
    ("an issue gets one comment per run", O, "if any(issue.run_url in s for s in said):", "if False:",
     f"{P}.Applying.test_open_issue_gets_one_comment_per_run"),
    ("a dry run writes nothing", O, 'if dry_run:\n        return f"would open {pr.branch}',
     'if False:\n        return f"would open {pr.branch}', f"{P}.Applying.test_dry_run_writes_nothing"),
]


def main() -> int:
    failed = []
    for what, path, code, mutant, test in GUARDS:
        with tempfile.TemporaryDirectory() as tmp:
            for d in ("factrefresh", "tests"):
                shutil.copytree(os.path.join(ROOT, d), os.path.join(tmp, d),
                                ignore=shutil.ignore_patterns("__pycache__"))
            target = os.path.join(tmp, path)
            with open(target, encoding="utf-8") as f:
                text = f.read()
            if text.count(code) != 1:
                failed.append(f"{what}: the guard's code occurs {text.count(code)} times in {path}")
                continue
            with open(target, "w", encoding="utf-8") as f:
                f.write(text.replace(code, mutant))
            out = subprocess.run([sys.executable, "-m", "unittest", test], cwd=tmp, capture_output=True, text=True)
            if out.returncode == 0:
                failed.append(f"{what}: {test} still passes without the guard")
            else:
                print(f"ok   {what}")
    for f in failed:
        print(f"FAIL {f}")
    print(f"{len(GUARDS) - len(failed)} of {len(GUARDS)} guards are tested")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
