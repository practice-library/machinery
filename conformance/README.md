# Conformance

A black-box suite for contract v0 plugins. It sends every case in `cases/` to the plugin as its own process (one request on stdin) and checks only what comes back.

```sh
python3 conformance/run.py --plugin /abs/path/to/plugin \
    --report-schema <spec/refresh-report/schema.json at the pin in contract/pins.json> \
    --validator jsonschema        # the sourcemeta jsonschema CLI; without it, schemas are not checked
```

For each case it checks:
- the exit code;
- one JSON envelope on stdout, within `caps.outputBytes` and `caps.timeSeconds`;
- with `--validator`, the case's own request against `contract/v0/request.schema.json` and the verb's input schema, and the envelope and the verb's data against their schemas (merge's data against the pinned report schema);
- the case's expected data, as a subset: keys a case does not name are not compared, and `{"$startsWith": ...}` and `{"$absent": true}` match a prefix or a missing key.

These invariants hold for every answer:
- **plan:** the budgets add up to at most `maxRequestsPerRun`, every unit has its own shard key, and `maxParallel` is at most 3.
- **run-unit:** requests are at most the unit's budget, and results name only the unit's facts.
- **merge:** every fact of a unit without a partial result is in the report, and no fact is `unchanged` unless its partial result says so.

The runner exits 0 only when every case passes. The Python plugin of this repository (`bin/fact-refresh-plugin`) passes all 42 cases in CI. A plugin that extracts text by replacing every tag with a space (`tests/fixtures/plugins/naive.py`) fails 13: the 12 design-review cases and the table-row case.

## Cases

One file per group. Each file has a `verb`, `defaults` (a request that each case's `request` is merged into) and `cases`. A case may give raw `stdin` instead of a request, mark a deliberately invalid request with `invalidRequest`, and expect an `exit` other than 0.

- `describe.json`: describe, and the process boundary: wrong contract version, stdin that is not JSON, unknown verb, no caps, run-unit without network, an answer over `outputBytes`.
- `plan.json`:
  - units by host;
  - a table quote turned into cells;
  - an unlisted host decided, a non-plain source not read;
  - the cap split with an exact sum;
  - never more than 3 in parallel;
  - the `minIntervalDays` boundary (7 days due, 6 not).
- `run-unit-safety.json`:
  - unchanged and changed;
  - `$10` not found in `$100`;
  - robots.txt disallowing a path, or answering 503;
  - 500, and 429 with a long `Retry-After`, never a verdict;
  - a redirect to an unlisted host;
  - a budget of 2;
  - table rows found, changed, or split across rows.
- `run-unit-design-review.json`: the design-review cases, below.
- `merge.json`: complete, one partial missing, none at all, an aborted unit, and duplicate, stray and unknown partials refused.

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
