# machinery HLD

Status: **signed by the founder on 2026-11-02**, with D1–D9 as recommended. This version answers the review of the signed draft without changing any decision. Written against `main` at 65b3d2c.

Snapshots cited: practice-library/.github v3.1.0 (fa14c829), graphs `main` 7ad258e, machinery-python 233800c, machinery-go 03045fd.

This file states public requirements and public evidence only. The rationale behind private decisions stays in the founder's private record.

## 1. Purpose

machinery is **the plugin contract, its conformance suite and the shared automation that runs on it**. It lets a tool written in any approved language plug into a host (a CI gateway, or a command-line consumer) through one service definition. Work splits into units that run in parallel on public Actions, within the account's concurrency.

It starts from the founder's decision of 2026-10-31 (#6): machinery becomes a contract plus a gateway, and each language implementation is its own repository. #17 set the plan: the calls are a `.proto` service served with Connect, and the data documents stay JSON Schema.

## 2. What it owns

- **The contract:**
  - `plugin.proto` v1alpha1 (`Describe`, `Plan`, `RunUnit`, `Merge`, `Run`);
  - the v1alpha1 JSON Schemas for requests, the envelope, describe and each job's documents;
  - contract v0 until it is retired (#17 PR 8);
  - the versioning rule (docs/lld.md §2.3).
- **Contract releases:** `contract/v1alpha1.N` tags, each with a verifiable archive (D8, docs/lld.md §6).
- **Conformance:**
  - the job fixtures (`conformance/connect/`);
  - the runtime fixtures (`conformance/runtime/`, moved from machinery-python in `contract/v1alpha1.1`);
  - the requirement tables, such as `quote-one-line.json`;
  - the runners.
- **The generated code:** the Go module and the Python wheel, released by tag, with nothing in a package registry.
- **The fact-refresh job:** its refresher, opener and plugin, until open point A (D7) moves it.
- **Small shared helpers** that are specified as data and implemented once (`lib/js/quote-one-line.mjs`).

## 3. What it does not own

| Not machinery's | Owner |
|---|---|
| The language runtimes | practice-library/machinery-go and machinery-python (#17 PRs 4 and 6) |
| The gateway workflow | practice-library/.github: `plugin-matrix.yml`, `tooling/practice_ci/plugin_gateway.py` (.github PR #80, .github#41) |
| The refresh-report and sources schemas | practice-library/.github, which machinery pins (.github#17) |
| The facts themselves | practice-library/graphs |
| A consumer's adapter | The consumer's own repository |
| Repositories | Creating one is reserved to the founder. docs/languages.md proposes none. |

## 4. Consumers

| Consumer | What it uses | Evidence |
|---|---|---|
| The `.github` gateway | Runs a plugin over plan, run-unit and merge. Today **contract v0 only**, "with bounded parallel units in one job" (`plugin-matrix.yml` at v3.1.0, line 3) | .github#41, PR #80 |
| machinery-python | Pins the wheel from release `gen/go/v0.1.0`. Serves v1alpha1 only. Will pin `contract/v1alpha1.N` for fixtures | machinery-python 233800c |
| machinery-go | Will pin `gen/go/v0.1.0` (d6024652) and `contract/v1alpha1.N`. Nothing is implemented yet | machinery-go#1, 03045fd |
| Command-line consumers | They call plugins over a binding that needs only a standard-library JSON client (D1), and pin a contract release (D8). | — |
| graphs | The fact-refresh pull requests and issues. graphs#69 (blocking) says a bump can certify a quote that doesn't cover the value | graphs#69, #10, #11, #36 |
| JS tools | Vendor `lib/js/quote-one-line.mjs` by commit and sha256 | machinery#32, #34 |

## 5. How it is released

A feature branch per release, child PRs into it with one review pass each at the exact head, and one release PR to `main` authored by the practice-library bot. **Every public write is made by the bot:**
- the pushes and the PRs;
- the tag;
- the release;
- the assets.

Publication happens only after every required check has passed at the exact commit being tagged. The tag, the release and every asset are bound to that commit and to the archive's sha256, retries included. The bot's app has a fixed, minimal set of permissions (docs/lld.md §6.1). The steps, the archive and the consumer's verification are in docs/lld.md §6.

## 6. Decisions (signed 2026-11-02, as recommended)

| # | Decision | Unblocks | As signed |
|---|---|---|---|
| D1 | **The transport.** See §6.1. | stdlib-only consumers; the runtimes' bindings; D4 | **(A) Stdio** is added as a binding. Loopback HTTP stays as the reference binding. |
| D2 | **What protobuf is for.** | consumers that speak only JSON | `.proto` is the schema of the calls. The wire stays JSON (Connect JSON), and a JSON-only caller needs no protobuf or Connect library. Requirement **R2:** every call can be made by a caller that has a standard-library JSON codec and nothing else. |
| D3 | **The gateway is still v0.** `plugin-matrix.yml` runs contract v0 over subprocess stdin/stdout. machinery-python serves only v1alpha1, so the shipped gateway can't run it. .github#41 is closed although its hold said to wait for Connect. | #17 PR 7; retiring v0 (#17 PR 8) | The gateway's v1alpha1 work goes as a new request to the `.github` controller. v0 stays until a v1alpha1 gateway runs fact-refresh end to end. |
| D4 | **A late plugin.** | the runtime fixtures; machinery-python#17 | The runtime **must answer on time** (`DEADLINE_EXCEEDED`), and **may** abandon the work. Over stdio, the process ends after the answer, so abandoned work can't outlive its call. Over loopback, the host supervises the process. |
| D5 | **Python's scope.** | machinery-python's release | A Python runtime may run networked jobs, and may depend on third-party packages pinned by hash. machinery-python#1's "Standard library only; single-file zipapp" no longer applies. |
| D6 | **The kill criterion** (#6): if no second plugin or consumer exists after 6 weeks, fold back into one repository. Six weeks from 2026-10-31 is **2026-12-12**. | the repository split | Kept. A consumer pinning a contract release counts. |
| D7 | **Open point A of #17:** where fact-refresh, the first Python plugin, lives. | #17 PR 5 | It stays in machinery until a v1alpha1 gateway runs it. Then decide with D6. |
| D8 | **Contract releases.** | consumers pinning a release instead of a commit; `buf breaking` | The **first tag is `contract/v1alpha1.0`, cut from `main` at 65b3d2ce34c67c7887a2564b3f694239aebb642d.** Every required check passed there: unit, contract-schemas, recorded-report, conformance, proto, js and the gate. Its proto and generated code are byte-identical to release `gen/go/v0.1.0` (d6024652). Since 269fd0f, the commit consumers pin today, it adds only the optional `ignoreInterval` (#43). Contents, archive and verification: docs/lld.md §6. `.0` is the baseline. `contract/v1alpha1.1` adds `conformance/runtime/` and the stdio binding (D1), and from `.1` on every release is compatible with every earlier one (docs/lld.md §2.3). |
| D9 | **Languages** | new runtimes | See docs/languages.md: Go and Python, no new repository. |

### 6.1 D1: the transport

**The requirements:**
- **R1:** a host must be able to call a plugin **without opening a network socket**.
- **R2:** it must be able to do so with only a standard-library JSON codec (D2).

**What exists:**
- v1alpha1 is Connect JSON over HTTP/1.1 on 127.0.0.1 today: `conformance/connect_run.py` and machinery-python's runtime. That meets R2 but not R1.
- The `.github` gateway already runs plugins as subprocesses over stdin and stdout (`plugin_gateway.py`, contract v0).

| | **(A) Stdio, signed** | **(B) Unix socket, not chosen** |
|---|---|---|
| **Shape** | The host runs the plugin once per call. A frame on stdin names the call and the deadline, then one Connect-JSON request follows. One response or Connect error comes back on stdout. | The runtime also listens on a unix-domain socket. The host connects and POSTs Connect JSON. |
| **Meets R1** | yes | yes, but each host has to permit a socket per plugin |
| **Runtimes** | A stdio entry point beside the listener. machinery-go estimates about 150 lines on its core. | A unix listener: about 20 lines in Go |
| **Gateway (D3)** | It keeps subprocess execution, so retiring v0 becomes a rebinding | A server per job |
| **D4** | Abandoned work ends with the process | The host supervises a long-lived process |
| **Cost** | A process start per call. Fact-refresh makes one `RunUnit` per host, so a run is tens of calls. | A socket lifecycle per job |

The frame is specified in docs/lld.md §2.4. The same fixtures run over every binding a runtime offers.

## 7. Dry run: the worked example

On paper only. It walks one upstream change of the founder's worked example (a vendor graph in practice-library/graphs, graphs#68, held under graphs#91) through machinery's slice of the five steps. Here the vendor is written `vendors/<example>`. Facts were read at the graph's open branch, at 2fa6281.

| Step | What machinery does | What it imports from graphs | What is missing |
|---|---|---|---|
| **1. graphs holds the facts** | Nothing. machinery reads graphs and never writes to it, except through the opener's pull requests. | `vendors/<example>`: 126 nodes, all sourced at one upstream commit (1c6ac8c3, its pinned release tag). There are 28 distinct sources: 27 files on `raw.githubusercontent.com` and 1 commit on `api.github.com`. Plus `policy/sources.json` for the vendor: raw at most 28 requests a run, api at most 2, `minIntervalDays` 7. | graphs#91 has to land first. |
| **2. fact-refresh re-reads the pin and proposes an update** | `refresh --graph vendors/<example> --ignore-interval` reads the 2 robots.txt files and the 28 sources, 30 requests in all. The opener files one bump-only PR for the unchanged quotes, one `CHANGES.md` PR per changed quote, and issues for unreadable ones. | The nodes, `policy/sources.json`, and the report schema pinned from `.github` (`contract/pins.json`) | (a) **#49 bites here.** `api.github.com` is listed for temporal, gitleaks, osv and the example, and `raw.githubusercontent.com` for gitleaks, osv and the example. The example is last in both, so its facts are read, but **osv loses both hosts and gitleaks loses raw.** Merging graphs#68 before #49 is fixed silently stops osv's facts being re-read. (b) **#47:** a run's 50 requests are shared with every graph. (c) **#42**, only if the rebuild at the next upstream release uses the v1 layout. (d) **A pin move isn't a refresh.** Every source URL names one commit, so a refresh can only re-confirm that release. Moving on is a graphs rebuild that re-points the URLs, then one `--ignore-interval` run verifies every quote. (e) graphs#69: a bump-only PR must carry fetch evidence. |
| **3. machinery-go and machinery-python serve it** | It provides the contract release, the job fixtures and the runtime fixtures that both runtimes pass. fact-refresh is a Python plugin. | Nothing beyond step 2 | No v1alpha1 gateway yet (D3). Step 2 runs through `bin/fact-refresh` directly. machinery-python has no release. Python wheels don't cover linux arm64. |
| **4. A consumer reads the pinned facts** | Nothing directly. A consumer reads graphs at a pinned ref. machinery's part is that those facts were re-verified at that ref. If it calls a plugin, it does so over the stdio binding (D1), pinned to a contract release (D8). | — | D8's first tag, and the stdio binding |
| **5. Decisions and sign-offs are recorded** | Nothing. PR bodies and release PRs carry the receipts. | — | — |

**What the dry run changes in the plan:**
- **#49 must land before graphs#68 merges.**
- **#47 is the first feature the example's flow needs.**
- **#42 drops behind both** unless the rebuild uses the v1 layout.

## 8. Stale claims elsewhere (for the owners, not machinery's to fix)

- **machinery-go#1 and machinery-python#1** are titled "waits for contract v0". The machinery-go README says it implements v0, which it doesn't.
- **machinery-python's wheel** comes from a release named `gen/go/v0.1.0`. D8 names contract releases apart from generated-code releases.
- **.github#41** says the repository was "frozen at v2.6.0", yet v3.0.0 and v3.1.0 were tagged on 2026-11-02.

## 9. Risks

- **graphs#69 (blocking in graphs):** a bump-only change certifies a fact without fetch evidence. fact-refresh's `unchanged` path must carry that evidence, or graphs will keep blocking auto-merge.
- **#49:** 152 facts are silently never re-read today (temporal 29, gitleaks 22, in-commercial-communications 64, in-data-governance 37). Adding the worked example makes it worse for osv.
- **Scope creep:** the contract, gateway, two runtimes and the consumers are five moving parts in five repositories. A limit of two PRs open at once, one builder at a time, and D6's kill date are the brakes.
