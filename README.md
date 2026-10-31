# machinery

Shared automation for the practice library: fact refresh and verification, run on public Actions.

## fact-refresh

Keeps the facts in [practice-library/graphs](https://github.com/practice-library/graphs) honest without anyone fetching by hand. It has two halves, as `spec/README.md` of that repository lays out:

- **The refresh** (`.github/workflows/fact-refresh.yml`), weekly and on demand. It checks out the graphs repository (input `ref`: a branch, tag or commit; `main` on the schedule), re-reads the source of every due fact, and uploads one report per graph, `<domain>/<vendor>.json`, as the artifact `refresh-report`. Each report is validated against `spec/refresh-report/schema.json` of the same checkout before upload. The workflow holds no secret and pushes nothing.
- **The opener** (`bin/fact-refresh open`), run by a maintainer as the organisation's app. It reads reports and opens pull requests and issues in the graphs repository. It never fetches a source and never merges.

Nothing here names a vendor, a host or a fact. Graphs are every `<domain>/<vendor>` whose `check.json` names `graph/nodes`; hosts, caps, the user agent and the issue threshold come from `policy/sources.json` of the checkout.

### What the refresh does

For each graph, facts are due when their `measuredAt` is at least the host's `minIntervalDays` old, oldest first. Then:

1. **robots.txt first.** It reads robots.txt of every host it would fetch from (RFC 9309: our product token's group, or `*`; `*` and `$` in patterns; the longest rule wins). If a rule disallows a path it would fetch, or robots.txt answers 5xx, 429 or nothing, or is longer than the host's `maxBytes` (a later rule could be cut off), the graph's run is `aborted` before any page is read. A 4xx is `absent`.
2. **Only listed hosts.** A source whose host is not listed for the graph's vendor is reported and never fetched. A source that is not a plain https URL (a port, userinfo, upper case) is never fetched either. The report's schema would reject it, so it is left out of the report and listed in the run's summary under "Left out of the reports". Redirects are followed only to listed hosts whose robots.txt was read and allows the target.
3. **Paced and capped.** Requests are sequential, so there is one connection per host. Two requests to one host are at least 2 seconds apart (`--pace`), and every request sends the listed user agent. The run-wide `maxRequestsPerRun` (robots.txt included, shared by every graph) and each host's own cap, `maxBytes` and `timeoutSeconds` apply. `timeoutSeconds` is a total wall-clock budget per request, so a server dripping bytes cannot stretch it. Facts sharing a source share one request. Facts not reached wait for the next run.
4. **429 and 503.** When `Retry-After` is at most `retryAfterMaxSeconds`, the run waits and retries once. Otherwise, or when the retry fails, the host is stopped for the run and its remaining facts are `unreadable`.
5. **Outcomes.** The quote is matched exactly in the page's visible text, with whitespace runs collapsed, and only as a whole. A letter or digit at either edge of the quote must not continue into the page text, so `$10` is not found in `$100` and `000 per month` is not found in `1,000 per month`. The outcome is `unchanged` when the quote is there, `changed` when the page was read and it is not, and `unreadable` otherwise. An empty or blank stored quote is `unreadable`. A 429 or 5xx is never a verdict.
6. **Distrusted hosts.** When at least two pages from one host were read and none of their quotes was found, the host is most likely serving a challenge or error page. Its facts become `unreadable` (`blocked:`) rather than one `changed` pull request each.

`reason` starts with a finer category:

| `reason` prefix | outcome | meaning |
|---|---|---|
| `quote-moved:` | unchanged | the source redirects; the quote is on the page it ends at |
| `quote-gone:` | changed | the page was read; the quote is not in its text |
| `unreachable:` | unreadable | HTTP error, 429/503, timeout or network error |
| `blocked:` | unreadable, or `aborted` | host not listed, redirect off the list, robots.txt disallows, or no quote found on any page of the host |
| `unreadable:` | unreadable | larger than `maxBytes`, not text, or the stored quote is empty |

With the same checkout and the same responses the report is the same, byte for byte (results sorted by node).

### Running the opener

As the organisation's app, with `GH_TOKEN` set to an installation token, from a clone of this repository:

```sh
bin/fact-refresh download --runs-repo practice-library/machinery --out reports
bin/fact-refresh open --reports reports --graphs ../graphs --graphs-repo practice-library/graphs --dry-run
bin/fact-refresh open --reports reports --graphs ../graphs --graphs-repo practice-library/graphs
```

`--graphs` is a local clone of the graphs repository; the opener fetches its `main` and only reads from it. It needs the [sourcemeta `jsonschema`](https://github.com/sourcemeta/jsonschema) CLI on `PATH` (or `FACTREFRESH_JSONSCHEMA`). It refuses a report that does not match `spec/refresh-report` on `main`, or whose commit is not on `main`. Then, from the newest report of each graph:

- **`unchanged`**: one pull request bumping `measuredAt` and `attrs.fetched_at` of those facts, and only those two lines. That is the one kind the graphs repository's `bump-only` check lets auto-merge. A fact edited on `main` since the report's commit is left out, and `fetched_at` never goes backwards. The body lists each fact with its old and new `fetched_at`.
- **`changed`**: one pull request per fact, adding a line to `<domain>/<vendor>/CHANGES.md` and not touching the node, so a maintainer reviews it.
- **`unreadable`**: an issue labelled `unreadable-source` once the fact is unreadable in each of the last `unreadableRunsBeforeIssue` reports; if one is already open, it gets a comment instead.
- **`aborted`**: an issue labelled `robots` naming the host and the reason; if one is already open, it gets a comment instead.

At most three `fact-refresh/` pull requests are open at once; the rest wait for a later run. Report text (reasons, URLs, hosts) is fenced in code spans in every body, and any other `@` is escaped, so no body mentions anyone.

Reruns do not duplicate anything. A branch is named after the run (`fact-refresh/<domain>-<vendor>/bump-<run>`, `.../changed-<node>-<run>`). No pull request is opened while one for the same graph's bump, or the same fact, is open, or once one was opened from that branch. An issue gets at most one comment per run.

### The schedule

GitHub disables the schedule of a public repository after 60 days without activity. Each scheduled run has a `heartbeat` job that re-enables its own workflow through the API, with the run's own token and `actions: write`. GitHub does not document whether that call resets the inactivity timer, so `download` also warns when no run has succeeded in 14 days.

## Development

```sh
python3 -m unittest discover -s tests -t .   # no network: tests/__init__.py refuses every connection
python3 tests/guards.py                      # removes each guard in turn; its test must then fail
```

Python 3.9 or later, standard library only. HTTP responses are recorded under `tests/fixtures/http`, against example hosts. `tests/fixtures/graphs` is a small graphs checkout. Its `spec/refresh-report/schema.json` is a copy of that file in practice-library/graphs at `766ead28667144138af0203d26e8ee9d707f8eca`; CI validates the recorded report `tests/fixtures/expect/vendors-example.json` against it. The live workflow validates every report against the schema of the checkout it read.

## Checks

`.github/workflows/checks.yml` runs `unit` and the organisation's `leak-scan`, and ends in the `gate` that the ruleset requires (`gate / gate`). Both come from practice-library/.github at release v1.1.0, pinned by full commit. `.github/required-checks.json` lists exactly the gate's needs. Every path is owned by the maintainers (`.github/CODEOWNERS`), so every pull request needs a maintainer's review.
