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
    ("an issue gets one comment per run", O,
     "if any(issue.run_url in s or no_mentions(issue.run_url) in s for s in said):", "if False:",
     f"{P}.Applying.test_open_issue_gets_one_comment_per_run"),
    ("a dry run writes nothing", O, "if dry_run:\n        open_heads.append(pr.branch)",
     "if False:\n        open_heads.append(pr.branch)", f"{P}.Applying.test_dry_run_writes_nothing"),
    # quality rework of PR #2
    ("a quote is not matched inside a longer word or number", "factrefresh/text.py",
     "if nxt.isalnum():\n        return True", "if False:\n        return True",
     "tests.test_text.WholeQuote.test_a_quote_ending_in_a_digit_does_not_match_a_longer_number"),
    ("a quote is not matched inside a grouped number", "factrefresh/text.py",
     'return edge.isdigit() and nxt in ",." and', 'return False and nxt in ",." and',
     "tests.test_text.WholeQuote.test_a_quote_starting_in_a_digit_does_not_match_inside_a_grouped_number"),
    ("an empty quote is never found", "factrefresh/text.py", "if not q:\n        return False",
     "if False:\n        return False", "tests.test_text.WholeQuote.test_an_empty_or_blank_quote_is_never_found"),
    ("an empty stored quote is unreadable", R, 'if quote_is_empty(node["attrs"]["quote"]):', "if False:",
     f"{T}.Rework.test_an_empty_stored_quote_is_unreadable_never_unchanged"),
    ("table cells separate words", "factrefresh/text.py",
     '"section", "table", "tbody", "td", "tfoot", "th",', '"section", "table", "tbody", "tfoot",',
     "tests.test_text.WholeQuote.test_table_cells_separate_words"),
    ("a host where every quote fails is distrusted", R,
     'for _, r in mine:\n                r["outcome"] = "unreadable"',
     'for _, r in []:\n                r["outcome"] = "unreadable"',
     f"{T}.Rework.test_a_host_where_every_quote_fails_is_distrusted"),
    ("a redirect target must be allowed by robots.txt", R, "if not rules[listed.host].allows(target):", "if False:",
     f"{T}.Rework.test_a_redirect_robots_txt_disallows_is_not_followed"),
    ("a redirect target's robots.txt must have been read", R, "if listed.host not in rules:", "if False:",
     f"{T}.Rework.test_a_redirect_to_a_listed_host_whose_robots_txt_was_not_read_is_not_followed"),
    ("a redirect target must be a plain https URL", R,
     "if not URL_RE.match(target):\n                    return Page(", "if False:\n                    return Page(",
     f"{T}.Rework.test_a_redirect_to_a_url_that_is_not_plain_https_is_not_followed"),
    ("a robots.txt redirect to an unlisted host is not followed", R,
     "break  # an unlisted target: recorded as unreachable below", "listed = at_host",
     f"{T}.Rework.test_a_robots_txt_redirect_to_an_unlisted_host_is_not_followed"),
    ("redirects stop after MAX_HOPS", R, "MAX_HOPS = 5", "MAX_HOPS = 6",
     f"{T}.Rework.test_redirects_stop_after_max_hops"),
    ("Retry-After as an HTTP date is read", R, "when = email.utils.parsedate_to_datetime(value)", "when = None",
     f"{T}.Rework.test_retry_after_as_an_http_date_is_honoured"),
    ("a fact whose source differs from the report is not bumped", O, 'if node["source"] != r["source"]:',
     "if False:", f"{P}.Rework.test_a_fact_whose_source_differs_from_the_report_is_not_bumped"),
    ("at most three refresh pull requests are open", O, "if len(ours) >= MAX_OPEN_PULL_REQUESTS:", "if False:",
     f"{P}.Rework.test_no_more_than_three_refresh_pull_requests_are_open_at_once"),
    ("report text is fenced in a code span", O, """flat = " ".join(str(text).split()).replace("`", "'")""",
     "flat = str(text)", f"{P}.Rework.test_report_text_stays_inside_its_code_span"),
    ("no posted body mentions anyone", O, 'p.replace("@", "&#64;") if i % 2 == 0 else p', "p",
     f"{P}.Rework.test_no_posted_body_mentions_anyone"),
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
