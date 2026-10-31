# Contract v0

How a gateway runs a job through a plugin, and how the work is split so it can run in parallel on public Actions. The schemas are JSON Schema 2020-12 in `v0/`, with examples and counter-examples in `examples/v0/` (`check_examples.py` runs them in CI).

## The process boundary

A plugin is an executable. It reads one JSON request on stdin (`v0/request.schema.json`): strict UTF-8, strict JSON, no duplicate keys. It writes one JSON envelope on stdout (`v0/envelope.schema.json`) followed by one newline, and at most 65536 bytes on stderr, for people only.

What the plugin must also keep, and what only the caller can enforce:
- **No other input:** it reads no files, no environment and no secrets, only what the request declares.
- **Network only where listed:** it opens a connection only to a host listed in `network.hosts`.
- **No network in replay:** with `network.replay`, it opens no connection at all. It answers from the recorded responses, on a virtual clock that starts at `replay.start`, and its waits advance that clock instead of passing. Conformance runs use replay.

A conformance run cannot prove these three. They are properties of where the plugin runs: the caller (a CI job) gives it no secrets and no environment it should not read, and no network beyond what the request lists.

The envelope is `{ok, data, notRead, warnings}`:
- `data` is the verb's result.
- `notRead` lists inputs the plugin deliberately left unused, each with the reason.
- When `ok` is false, `data` is null and `warnings` says why.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | the verb succeeded |
| 1 | the verb failed on a valid request (for example, a merge given two partial results for one unit, or an answer larger than `outputBytes`) |
| 2 | the request is not a valid request (not JSON, no caps, unknown verb, an input that does not match) |
| 3 | the request's `contract` is a version the plugin does not speak |

The caller sets `caps.timeSeconds` and `caps.outputBytes`. The plugin starts no work that could outlast the time cap, and never writes more than `outputBytes` to stdout, the newline included. That holds even for a request it refuses: when `outputBytes` is smaller than the smallest failure envelope, it writes nothing. The caller kills it after `timeSeconds`, and refuses a larger output.

## Verbs

- **`describe`:** returns the plugin's name and version, the contract version of the answer (`contract`), every contract version it speaks (`contracts`), its job, and every verb with whether it is shardable (`v0/describe.schema.json`).
- **A shardable job** answers `plan`, `run-unit` and `merge`. A job that is not shardable answers a single `run`. For the fact-refresh job, `run` takes the same input as `plan` and returns the report (`runInput`, `runData`); this repository's plugin is shardable and does not answer it.

For the `fact-refresh` job (`v0/fact-refresh.schema.json`):

- **`plan`:** one graph, as its nodes and `policy/sources.json`, becomes a list of independent work units, one per source host. The host is the unit's shard key. Each unit also carries:
  - its pacing (seconds between two requests to its host);
  - its request budget, robots.txt included;
  - the due facts, oldest `measuredAt` first.

  Facts on a host not listed for the vendor are decided at once as `unreadable` (`blocked:`). A source the report schema's URL definition rejects is left out and named in `notRead`.

  The run-wide `maxRequestsPerRun` is split in two passes, oldest host first. First, each unit gets what it needs: robots.txt plus one request per distinct source. Then whatever is left goes out, up to each host's own cap. A host left with fewer than 2 requests waits for the next run.
- **`run-unit`:** one unit becomes a partial result:
  - robots.txt of its host first;
  - then its pages, within the budget and pacing;
  - one result per fact reached, in the report's result shape, and every fact not reached in `notReached`.
- **`merge`:** the plan and every partial result become one refresh report.

A quote is `{"text": ...}`, found as a whole in the page's text, or `{"cells": [...]}`, found as consecutive whole cells of one table row on the page. `plan` turns a stored quote written as a markdown table row (`| a | b |`) into cells, because that form never appears in a page's text.

## Running units in parallel

- **At most 3 at once:** the gateway runs `run-unit` calls as a GitHub Actions matrix with `max-parallel` at most 3, one job per unit. The plan's `maxParallel` is never above 3. The account's concurrency is shared with every other repository's checks, and 3 leaves them runners.
- **Missing partials:** `merge` is the only step that sees every partial result. A unit with no partial result has its facts reported as `unreadable`, never `unchanged`.
- **Partials that do not add up are refused (exit 1):** one that duplicates another, names a unit the plan lacks, has another shard key or another host's robots.txt, carries a fact outside its unit, or leaves out a fact of its unit (each fact is in `results` or `notReached`, exactly once). Facts in `notReached` are left out of the report and wait for the next run, as in the single-process job.
- **Aborts:** if any unit's robots.txt disallows a path or cannot be read, the whole report is `aborted`, as the report schema requires. The reasons are joined in unit order, so the report does not depend on the order of the partials.

A note on this plugin, not a rule of the contract: `plan` makes one unit per host and uses the host as the shard key, so no two units share a host and pacing stays inside one job.

## The report is referenced, not redefined

`merge` returns a report that must validate against `spec/refresh-report/schema.json` of practice-library/graphs. The contract schemas reference it by its `$id`, and the pin is the one data row in `pins.json`. The repository has no release tag yet, so the pin is a full commit. When a tag exists, or the schema moves to practice-library/.github, only that row changes. CI checks out the pinned file and confirms its `$id` before running conformance.

## Conformance

`conformance/` is a black-box suite: it sends requests to a plugin executable and checks only what comes back. A plugin's repository runs it in CI against its executable, and a gateway refuses a plugin without a passing run. See `conformance/README.md`.

## Versions

Version 0 may still change. From version 1 on, a contract version changes only additively:
- allowed: a new optional request field, and a new verb;
- an answer gains a field only when the request asks for it with such a new field, so an older caller never sees one;
- never a removed or renamed field, a field made required, a narrower range or pattern, a new value in an enum an older caller reads, or a changed meaning of an exit code;
- anything else is a new major version, and a plugin answers a version it does not speak with exit 3.

A request names its version in `contract`; `describe` answers with the version it was asked in and lists every version it speaks. Before version 1, the framing of version 0 may still change to be compatible with JSON-RPC 2.0.

Not here yet: the gateway (step 2: a reusable matrix workflow in practice-library/.github that runs `plan`, one `run-unit` job per unit with `max-parallel` at most 3, and `merge`), and the Python plugin in its own repository (step 3).
