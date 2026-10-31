# Conformance

A black-box suite for contract v0 plugins. It sends every case in `cases/` to the plugin as its own process (one request on stdin) and checks only what comes back.

```sh
python3 conformance/run.py --plugin /abs/path/to/plugin \
    --report-schema <spec/refresh-report/schema.json at the pin in contract/pins.json> \
    --validator jsonschema        # the sourcemeta jsonschema CLI; without it, schemas are not checked
```

For each case it checks the process:
- **time:** the plugin ends within `caps.timeSeconds`, plus 2 seconds for process start, or it is killed and the case fails;
- **exit code:** one of 0, 1, 2, 3 and 75, the one the case expects, and 0 exactly when `ok` is true;
- **output:** at most `caps.outputBytes` on stdout, the newline included, and at most 65536 bytes on stderr;
- **one answer:** stdout is one JSON value followed by one newline, with nothing before or after it (no log line, no second answer, no cut-off line).

Then the answer:
- **schemas:** with `--validator`, the case's own request against `contract/v0/request.schema.json` and the verb's input schema, and the envelope and the verb's data against their schemas (merge's data against the pinned report schema);
- **expected data:** a subset match. Keys a case does not name are not compared, and `{"$startsWith": ...}` and `{"$absent": true}` match a prefix or a missing key.

These invariants hold for every answer:
- **describe:** it answers in the contract version it was asked in, and names each verb once.
- **plan:** the budgets add up to at most `maxRequestsPerRun`, and `maxParallel` is at most 3.
- **run-unit:** requests are at most the unit's budget. Unless the unit aborted, `results` and `notReached` name each fact of the unit exactly once.
- **merge:** every fact of a unit without a partial result is in the report. A fact of a unit with one is in the report unless the partial says it was not reached, and then it is not. No fact is `unchanged` unless its partial result says so.

What the runner cannot check: that a plugin reads no files, environment or secrets, and opens no connection beyond what the request lists. Those are properties of where the plugin runs, not of its answers. The caller's CI job keeps them by giving the plugin nothing else; a conformance pass does not prove them.

The runner's own checks are tested against plugins that break them one way each (`tests/fixtures/plugins/bad.py`, `tests/test_runner.py`), and each check is mutated in `tests/guards.py`.

The runner exits 0 only when every case passes. The Python plugin of this repository (`bin/fact-refresh-plugin`) passes all 59 cases in CI. A plugin that extracts text by replacing every tag with a space (`tests/fixtures/plugins/naive.py`) fails 13: the 12 design-review cases and the table-row case.

## Cases

One file per group. Each file has a `verb`, `defaults` (a request that each case's `request` is merged into) and `cases`. A case may give raw `stdin` instead of a request, mark a deliberately invalid request with `invalidRequest`, and expect an `exit` other than 0.

- `describe.json`: describe, and the process boundary:
  - a contract version the plugin does not speak (exit 3), or one that is not digits;
  - stdin that is not JSON, a key given twice, NaN;
  - an unknown verb, an unknown top-level key, describe with an input, run-unit without network;
  - no caps, `timeSeconds` true or negative, `outputBytes` below 1024 (the refusal still fits in it);
  - an answer over `outputBytes`.
- `plan.json`:
  - units by host;
  - a table quote turned into cells;
  - an unlisted host decided, a non-plain source not read;
  - the cap split with an exact sum;
  - never more than 3 in parallel;
  - the `minIntervalDays` boundary (7 days due, 6 not).
- `run-unit-safety.json` (a fact the budget does not reach is in `notReached`):
  - unchanged and changed;
  - `$10` not found in `$100`;
  - robots.txt disallowing a path, or answering 503 (the report aborts), or 429 (exit 75, retried later);
  - 500, and 429 with a long `Retry-After`, never a verdict;
  - a redirect to an unlisted host;
  - a budget of 2;
  - table rows found, changed, or split across rows.
- `run-unit-design-review.json`: the design-review cases, below.
- `merge.json`:
  - complete, one partial missing, none at all, an aborted unit;
  - the same report whatever the order of the partials, including two aborted units in both orders;
  - a fact not reached left out of the report, and a partial that leaves a fact out refused;
  - duplicate and unknown partials refused, and partials with a fact outside their unit or in no unit, another shard key, or another host's robots.txt.

## The design-review cases

These cases come from stored quotes and public pages, measured on 2026-10-31:
- **Quotes:** the 81 quotes stored in practice-library/graphs `vendors/github`, at `7a773162`.
- **Pages:** the 40 distinct pages they cite, each fetched once from the listed host. robots.txt was read first, requests were 1.5 s apart, and the graphs user agent was sent. 6 of the 40 sources answer 301 to another page on the same host; each redirect was followed once.

| Extractor | Quotes found on their pages |
|---|---|
| Every tag replaced by a space | 69 of 81 (12 false `quote-gone`) |
| Inline tags removed without a space, with entities, whitespace, NFKC and curly-quote folding | 79 of 81 |
| This repository's plugin, before table quotes | 79 of 81 |
| This repository's plugin, with table quotes matched as cells | 81 of 81 |

Of the 12 quotes the first extractor misses:
- **10** sit next to an inline element that closes right before punctuation (`<code>GITHUB_TOKEN</code>,`, `<strong>public</strong>.`, `<em>For users</em>:`). A space inserted there breaks the quote.
- **2** are markdown table rows (`| a | b |`), which never appear in a page's text. These are the 2 the careful extractor also misses.

Each case is the smallest `<p>`, `<li>` or `<tr>` around one of these quotes, with its stored quote; a conforming plugin must report `unchanged`. The table quotes are stored as cell sequences. The generator checked each snippet: the careful extractor finds the quote in it, and the first one does not.

The page snippets are from GitHub Docs (https://docs.github.com) by GitHub, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); the source repository is github/docs. Markup is trimmed to the element around the quote and attributes are removed; the text is unchanged.
