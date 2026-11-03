# machinery backlog plan

Status: **signed by the founder on 2026-11-02.** This version answers the review of the signed draft without changing any proposal. Written 2026-11-02 against `main` at 65b3d2c, from the 21 open issues of practice-library/machinery.

Each row proposes one of: **keep**, **merge** (into another issue), **rewrite** (keep the number, restate what is left), **close** or **move** (to the repository that holds the code). "Checked" means the claim was read against the code at 65b3d2c, not only against the issue's text.

## Proposals

| # | Title (short) | Proposal | Reason |
|---|---|---|---|
| 49 | A host listed for several graphs is kept for only the last | **keep, first** | Checked: `factrefresh/graphs.py:43` keys `hosts` by host name alone. 152 facts on graphs `main` are never re-read. It is the cause of most of #42's "not listed" counts. It must land before graphs#68 merges: the worked example lists `api.github.com` and `raw.githubusercontent.com` last, so merging it first would take both hosts from osv (docs/hld.md §7). |
| 22 | Keep `attrs.status` consistent with `attrs.window` | **keep, second** | It has a date: the first windows fall on 2026-11-13 and 2027-05-13. After that, a plain bump writes a node that fails graph-check. |
| 42 | Reports and source scopes for the graphs v1 layout | **rewrite** | Split into (a) emitting the v1 report shape, which depends on #13, and (b) honouring `policy/source-scopes.json`, including `unresolved` hosts. Drop the third paragraph: the "not listed" counts are #49, a refresher bug, not a graphs-side gap. The worked example's flow (docs/hld.md §7) needs this only if its rebuild uses the v1 layout. |
| 47 | A dispatch input to choose the graphs | **keep** | Small: the CLI already has `--graph`. The worked example's flow (docs/hld.md §7) needs it, so that one graph's refresh isn't starved by the 50-request budget. |
| 13 | Validate reports against the pinned `.github` schema | **keep** | Checked: the opener and `fact-refresh.yml` still read the graphs copy. When graphs drops that copy, every report is refused. #42(a) builds on it. |
| 48 | Hosts that answer text/markdown | **keep** | Live drift today: 7 false `quote-gone` on 2026-11-02. Options 1 and 3, as the issue recommends. |
| 45 | Follow robots.txt redirects (RFC 9309) | **keep** | Blocks every fact on www.rust-lang.org. Small and bounded. |
| 44 | Follow-ups from the review of #43 | **keep** | Five small items, one PR. Item 4 needs a contract-README ruling. |
| 14 | `contract/pins.json` can fall behind the workflow pins | **keep** | Small. Proposal: the CI check (option 1), not a README line. |
| 46 | Incomplete certificate chain (egazette.gov.in) | **keep, needs design sign-off** | AIA chasing touches TLS verification. It needs its own LLD section and a security review before code. |
| 17 | Plan: plugin calls as a `.proto` service | **rewrite** | It's the plan of record, but its status is stale. PRs 0–3 have landed: #18, #21, #23/#25/#26 and #30. Restate it as "done / next (PR 4: machinery-python runtime) / open point A", and link docs/hld.md and docs/languages.md. |
| 6 | Contract v0 and conformance (step 1) | **close** | Step 1 landed in #7. The rest is carried by #17 (PRs 6–8) and #10. Its kill criterion (no second plugin or consumer after 6 weeks, so 2026-12-12) moves into docs/hld.md, so it isn't lost. |
| 10 | Exit 75 in the gateway: retry policy | **keep** | A decision for #17 PR 7 (the gateway in `.github`), between gateway retries and merge aborts. It needs a ruling, not code, until then. |
| 3 | Follow-ups from the first QUALITY review | **rewrite** | Checked: 4 of 7 are done: robots truncation, port/userinfo sources and the total deadline in #4, and the gate bump in #12/#20. Left: the heartbeat's 14-day warning, matching folds (NFKC, curly quotes, zero-width, soft hyphen), and the `regulations/` auto-merge refusal. The last one is live now that graphs#42 adds regulations. |
| 8 | A float `maxBytes` crashes the refresh | **close** | Checked: `factrefresh/graphs.py:46` reads it through `int()`, so `1.0E7` no longer crashes. Close with a regression test in the next refresh PR, or keep it open until that test exists if the founder prefers. |
| 24 | Proto follow-ups | **keep** | Checked: item 1 is still wrong at `plugin.proto:65`. Three small items, one PR. |
| 31 | Generated-code follow-ups | **rewrite** | Item 1 (`.gitattributes` wording) and item 4 (the two mutants) are machinery's. Item 2 belongs to #17 PR 6 (machinery-go) and item 3 to PR 4 (machinery-python). Keep 1 and 4 here and point to the runtime PRs for 2 and 3. |
| 29 | Python runtime writes integral numbers as `3.0` | **close, once checked** against machinery-python#4 | machinery-python reports it worked around in `codec.integers` and held by guard rows, at 233800c. It closes only after the fix is read at that commit and the fixtures that fail a runtime writing `3.0` pass against it. Until then it stays open. |
| 15 | format json: a quote can't start a line | **keep** | Real, with a workaround in graphs (vendors/temporal/README.md). Fix: decode the string values (option 2), with a changelog note. |
| 9 | Table rows without closing tags | **keep, low** | No listed host omits the tags today. Pick it up when one does. |
| 19 | PDF reader: earlier-revision cut, bytes after `%%EOF` | **keep, low** | Both fail safe (`unreadable`) or need a one-byte cut. Record them as known limits in the README if not built. |

## Order

One builder at a time, and at most two PRs open at once:
1. #49
2. #22, before 2026-11-13
3. #47
4. #13
5. #42(a)
6. #42(b)
7. #48
8. #45

The small follow-up PRs (#44, #14, #24, #31(1, 4), #3's remainder) fill gaps between them. #46 waits for its design sign-off. #17's next step (PR 4) belongs to machinery-python.

## Gaps with no issue yet (to file)

- **A contract release that consumers can pin.** The tags `v0.1.0` (contract v0) and `gen/go/v0.1.0` (generated code) aren't contract releases. Decided in D8: the first is `contract/v1alpha1.0`, cut from 65b3d2c (docs/lld.md §6.5).
- **Runtime fixtures belong in machinery.** `conformance/connect/` tests the fact-refresh job, not a runtime. machinery-python keeps its own 56 runtime fixtures. They move to `conformance/runtime/` in `contract/v1alpha1.1`, and every runtime pins them by contract tag.
- **The stdio binding** (D1): its frame, its runner and the fixtures run over it, all in `contract/v1alpha1.1` (docs/lld.md §2.4).
- **A late plugin** (D4): "must answer on time, may abandon", written into the contract README and held by a runtime fixture.
- **The replay start, schema against proto.** `request.schema.json` accepts any timestamp matching its pattern for `network.replay.start`. The `plugin.proto` header says a runtime refuses anything but 2026-10-31T12:00:00Z. One of the two has to change, and the fixtures then hold it.
- **Integral doubles above 2^53.** The `plugin.proto` header and `contract/README.md` already require **every** integral double to be written as an integer, not only those up to 2^53. A runtime fixture in `conformance/runtime/` (`contract/v1alpha1.1`) holds it, with cases at 2^53, 2^53+2 (`9007199254740994`), 1e20 (`100000000000000000000`), 1e300, and -0 (written `0`). machinery-python's codec writes `9007199254740994.0` above 2^53 today, so it will fail that fixture until it's fixed (reported by machinery-go).
- **Release plumbing** (docs/lld.md §4, §6): `checks.yml` on `release/**`, the `contract-archive` and `contract-compat` jobs, `buf breaking` against the latest contract tag, and the gate script of §6.3.
- **`contract/README.md` for `.1`:** replace "v1alpha1 may still change" with the compatibility promise of docs/lld.md §2.3.

## Not decided here

- **#10 and #46:** rulings, not grooming.
- **#17 open point A:** where the first Python plugin lives. D7 keeps it in machinery for now.
