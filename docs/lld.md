# machinery LLD

Status: **signed by the founder on 2026-11-02**, with docs/hld.md. This version answers the review of the signed draft without changing any decision. Written against `main` at 65b3d2c. Sections marked **proposed** describe the target, not what exists today. Everything else describes what exists, and was read from the code.

## 1. Layout

| Path | What | Status |
|---|---|---|
| `proto/machinery/plugin/v1alpha1/plugin.proto` | The plugin calls as one Connect service: `Describe`, `Plan`, `RunUnit`, `Merge`, `Run`, with `Caps`, `Network`, `Replay`, `NotRead` and `Failure` | exists (#21) |
| `proto/check.jq` | Checks that the proto's messages and fields match the v1alpha1 schemas exactly | exists. Nested messages aren't compared (#24) |
| `buf.yaml`, `buf.gen.yaml` | buf lint, format and generate config. Local generators only, no registry | exists |
| `contract/v0/` | Contract v0: JSON Schema 2020-12 for the stdin/stdout process boundary | exists (#7). Retired by #17 PR 8 once nothing calls it |
| `contract/v1alpha1/` | The data documents of v1alpha1: `request`, `envelope`, `describe`, `fact-refresh` | exists (#23) |
| `contract/examples/`, `contract/check_examples.py` | Valid and invalid examples for each schema, run in CI | exists. No v1alpha1 invalid example yet (#44) |
| `contract/pins.json` | The one data row pinning the refresh-report schema to a practice-library/.github release (tag and commit) | exists. Can fall behind the workflow pins (#14) |
| `conformance/` | The black-box suite: v0 cases over stdin (`run.py`), Connect JSON fixtures over HTTP (`connect/`, `connect_run.py`), the v0 bridge, the carry-over check and `quote-one-line.json` | exists (#7, #25, #26, #33, #34) |
| `gen/go/` | Go module `github.com/practice-library/machinery/gen/go`, generated, tagged `gen/go/vX.Y.Z` | exists (#30), released as gen v0.1.0 |
| `gen/python/` | The Python package `machinery.plugin.v1alpha1`, generated, and a reproducible wheel builder | exists (#30) |
| `gen/VERSION` | The version of both generated artefacts (0.1.0) | exists |
| `lib/js/quote-one-line.mjs` | A dependency-free JavaScript helper, vendored by consumers by commit and sha256 | exists (#34). Raises a language question: see docs/languages.md |
| `factrefresh/`, `bin/fact-refresh`, `bin/fact-refresh-plugin` | The fact-refresh job: the refresher, the opener and the v0 plugin | exists. Its home after #17 PR 5 is open point A |
| `.github/workflows/fact-refresh.yml` | The weekly and on-demand refresh, plus the schedule heartbeat | exists |
| `.github/workflows/checks.yml` | CI (section 4) | exists |
| `tests/` | Unit tests, the runner's own tests, `guards.py` (mutations) and the JS tests | exists |
| `conformance/runtime/` | The runtime fixtures, moved from machinery-python's 56 cases at 233800c | **planned** for `contract/v1alpha1.1` |
| `.github/workflows/checks.yml`, `release/**` | CI on release branches as well as `main` (§6) | **planned** |
| `docs/` | This LLD, the HLD, the backlog plan and the language study | signed 2026-11-02 |

## 2. Contracts and interfaces

### 2.1 The plugin service (v1alpha1)

- **Calls:** a shardable job answers `Plan`, `RunUnit` and `Merge`. A job that isn't shardable answers one `Run`. Every plugin answers `Describe`.
- **Data:** a job's input travels as `google.protobuf.Struct` and its result as `google.protobuf.Value`. Each is validated against the JSON Schema whose `$id` `Describe` names, so a job's schema is written once.
- **Wire:** Connect JSON. Today it is served on loopback HTTP; stdio is added by D1 (§2.4). A plain-JSON caller needs only a JSON codec. Typed callers may use the generated code.
- **Caps on every call:** `timeSeconds` 1–3600 (also the call's deadline) and `outputBytes` 1 KiB–16 MiB.
- **Network** (`contract/v1alpha1/request.schema.json`):
  - It is required on `RunUnit` and `Run`, and absent on the others.
  - It is an object with an optional `hosts` list and an optional `replay`. `{}` and `{"hosts": []}` both mean no connection.
  - `replay` may appear only when `hosts` is empty or absent. Its `start` is a UTC timestamp matching the schema's pattern, and its `exchanges` may be left out.
  - Proto's JSON mapping can't tell an empty list from a missing one, so the rules are stated that way.
  - **A gap between layers:** the proto header also says a runtime refuses a replay `start` other than 2026-10-31T12:00:00Z, and the schema doesn't. Which layer is right is tracked as a backlog gap (docs/backlog-plan.md), not settled here.
- **Outcomes:** a success has `ok: true`. A failure is a Connect error carrying one `Failure` detail:

| Connect code | Meaning | v0 exit |
|---|---|---|
| `FAILED_PRECONDITION` | a valid request that failed | 1 |
| `RESOURCE_EXHAUSTED` | an answer over `outputBytes` | 1 |
| `INVALID_ARGUMENT` | not a valid request | 2 |
| `UNIMPLEMENTED` | a package or call the plugin doesn't serve | 3 |
| `UNAVAILABLE` | try again later; the only retryable code, and only with a `Failure` detail | 75 |
| `DEADLINE_EXCEEDED` | over `timeSeconds` | (killed) |

- **Numbers:** in `Struct` and `Value` they are doubles, exact up to 2^53. Every runtime writes an integral one as an integer (`3`, never `3.0`), which is #29 for Python.
- **Strict decoding:** unknown fields are refused. connect-go discards them by default, so the Go runtime installs a strict codec.

### 2.2 The fact-refresh job

Its documents are in `contract/v1alpha1/fact-refresh.schema.json`: `planInput`, `plan`, `unit`, `partial`, `mergeInput`, `runInput` and `runData`. The report is **referenced, not redefined**: `spec/refresh-report/schema.json` of practice-library/.github, pinned in `contract/pins.json`. Its rules (one unit per host, at most 3 in parallel, missing partials become `unreadable`, partials that don't add up are refused, robots aborts) are in `contract/README.md` and stay normative.

### 2.3 Versioning

The rule is `contract/README.md`'s "Versions": a version changes only additively, and "anything else is a new major version". How it maps to packages and tags:

- **A package is a version:** `machinery.plugin.v1alpha1`. An incompatible change makes a new package, for example `v1alpha2` (and later `v1`, `v2`), with its own `contract/<package>/` and proto directory, and its tags start at `contract/<package>.0`. A runtime answers a package it doesn't serve with `UNIMPLEMENTED`.
- **A tag is a release of one package:** `contract/v1alpha1.N`, with N rising by one per release.
- **Where the promise starts.** `contract/v1alpha1.0` is the **baseline**. Its archive holds `contract/README.md` as it reads at 65b3d2c ("v1alpha1 may still change", and `buf breaking` from v1), and that text is true of `.0`. **From `contract/v1alpha1.1` on**, every release of the package is compatible with every earlier one, `.0` included. `.1`'s `contract/README.md` states that promise in place of the "may still change" line, so each archive's normative text matches its release.
- **What checks it.** No tool proves compatibility, so three things together give evidence and a reviewed judgement:
  - **Wire:** `buf breaking` against the previous contract tag covers the proto: fields, numbers, types and calls.
  - **Regression coverage, not proof:** the **planned** `contract-compat` (§4). The previous tag's valid request examples and request fixtures must still be valid under the new schemas, and its job fixtures must still pass against this repository's plugin. A finite set of examples can't show that every earlier valid request stays valid.
  - **Structural review of the schemas, required:** the release PR lists every changed keyword in `contract/` (`required`, `minimum`/`maximum`, `pattern`, `enum`, `additionalProperties`, types) with a line each on why it doesn't narrow what an earlier release accepted or change what an earlier caller reads. The single-pass review checks that list, along with the release note's statement on meanings and runtime behaviour. A change that can't be shown compatible goes to a new package.
- **Generated code** keeps its own version (`gen/VERSION`, tag `gen/go/vX.Y.Z`). Each contract release names the `gen` release built from the same proto, which must be byte-identical. `contract/v1alpha1.0` names `gen/go/v0.1.0`.

### 2.4 Transport bindings (D1, signed: stdio)

Today the contract names one binding: Connect JSON over HTTP/1.1 on 127.0.0.1. D1 adds stdio. The unix socket (B) was the alternative, and was not chosen.

- **Stdio.**
  - One process per call.
  - The frame on stdin is one JSON line `{"path": "/machinery.plugin.v1alpha1.PluginService/<Call>", "timeoutMs": N}`, followed by the Connect-JSON request body.
  - On stdout comes one line `{"status": <connect code or "ok">}`, followed by the response body or the Connect error body.
  - The exit status is 0 on ok and 1 on any error; the Connect code is the authority.
  - The host kills the process at `timeoutMs`, plus a grace period.

**Which release carries it:** `contract/v1alpha1.1`. That release has the frame above in `contract/README.md`, the stdio runner (`conformance/stdio_run.py`), the runtime fixtures, and the job and runtime fixtures run over both bindings. `.0` names loopback HTTP only.

**How a host knows a runtime's bindings:** not from `Describe`. `describe.schema.json` sets `additionalProperties: false`, and `contract/README.md` lets an answer gain a field only when the request asks for it, so an unrequested bindings field would break both. A runtime states its bindings in its README and its release notes, and its conformance run covers each binding it states. A field the caller can request, or a new package, would be needed before `Describe` could carry it.

## 3. Tests and conformance

- **Unit tests** (`tests/test_*.py`) for the refresher, opener, plugin, runner, HTTP and text.
- **Mutation guards** (`tests/guards.py`). Each runner check and safety rule is mutated, and the suite must fail.
- **v0 conformance:** 63 cases, which `bin/fact-refresh-plugin` passes. A naive tag-stripping plugin fails 13 of them; that's the canary.
- **v1alpha1 conformance:** Connect JSON fixtures carried from v0, with a carry-over check that every v0 case has a v1alpha1 twin. The Describe fixtures name an expected **subset**: `connect_run.py` checks the whole answer against `describe.schema.json` separately, so a plugin can't leave out `name` or `version`. A consumer that needs a complete Describe response to build a stand-in plugin uses `contract/examples/v1alpha1/describe.json`, not the fixture.
- **What conformance can't prove:** that a plugin reads no files, environment or secrets and opens no unlisted connection. Those are properties of the caller's CI job.
- **Requirement tables as data:** `conformance/quote-one-line.json`. Any language's twin of a helper is checked against the same table.
- **Runtime fixtures in `conformance/runtime/`** (planned for `contract/v1alpha1.1`). They're moved from machinery-python's 56 at 233800c, and machinery owns them from then on. machinery-python and machinery-go pin them by contract tag. They test a runtime (strict JSON, the rules proto can't state, caps, deadline, Failure shrinking, the code/HTTP table), apart from `conformance/connect/`, which tests the fact-refresh job.
- **Proposed rule for a new runtime:** it is accepted when its CI passes every runtime fixture against a minimal example plugin, on both CI arches (ubuntu-24.04 and ubuntu-24.04-arm), at a pinned contract release. Language comparisons use conformance time, not unit time. machinery-python's 2.7 minutes were mostly 39 guard re-runs; its conformance took 12 s.

## 4. CI (`checks.yml`)

| Job | What it checks |
|---|---|
| `unit` | the Python tests |
| `contract-schemas` | the examples against their schemas |
| `recorded-report` | a recorded report against the pinned schema |
| `conformance` | v0 cases and Connect fixtures against this repository's plugin, with the schema pinned in `contract/pins.json` |
| `proto` | buf format and lint, regenerate and diff, Go vet and build, the wheel built twice and byte-compared |
| `js` | `lib/js` against `quote-one-line.json` and the full code-point sweep, on pinned Node 24 |
| `leak-scan`, `denylist`, `removed-paths`, `release-note`, `schema-loosening`, `gate` | the practice-library/.github gate, adopted at v2.4.0 (#20) |
| `contract-archive` (**planned**) | builds the contract archive of §6 at every head and prints its sha256 |
| `contract-compat` (**planned**) | the previous contract tag's requests and fixtures against this head (§2.3) |
| `buf breaking` (**planned**, in `proto`) | against the previous contract tag, from `contract/v1alpha1.0` on |

**Triggers** today are pull requests to `main` and pushes to `main`. They are **planned** to cover `release/**` as well, so every child PR into a release branch runs the same checks as a PR to `main`.

Every tool is pinned: buf by sha256, pypdf and the generators by hash, Go via `go.sum`, Node by version. `jq` isn't pinned yet (#24).

## 5. Dry run: the worked example (the paths and calls, on paper)

See docs/hld.md §7 for the walk. At the level of code it touches:

1. **`factrefresh/graphs.py` `load_policy`** keys `hosts` by name today (`:43`). With the example's entries, `api.github.com` and `raw.githubusercontent.com` resolve to the example only, and osv and gitleaks are refused.
   - **The fix (#49):** key on `(host, vendor)`.
   - **The test:** a fixture with one host listed for two vendors, where both graphs' facts are read. A guard row reverts the key.
2. **`.github/workflows/fact-refresh.yml`** gains a `graphs` input, passed as repeated `--graph` (#47). The dispatch for the example's flow is `graphs: vendors/<example>` with `ignore-interval` ticked. That is 30 requests: 2 robots.txt plus 28 sources.
3. **`factrefresh/refresh.py`** is unchanged for the example. Both hosts' robots.txt answer 404 (`absent`), the `raw` host's format is `text`, the api host's is `json`, and #15 applies to the one json source.
4. **`factrefresh/opener.py`** opens:
   - one bump PR, `fact-refresh/vendors-<example>/bump-<run>`, for the unchanged facts, at most 126;
   - one `changed` PR per fact that differs;
   - an `unreadable-source` issue after the threshold.

   graphs#69 asks the bump PR to carry fetch evidence, which is open.
5. **The report** is validated against `contract/pins.json`'s schema once #13 lands, and the checkout's copy until then.
6. **In the rebuild at the next upstream release**, graphs re-points 28 URLs to the new commit. If it uses the v1 layout, #42(a) and (b) come first.
7. **Through a runtime** (after D3): `Plan` takes the example's graph and gives 2 units (raw and api). Then come 2 `RunUnit` calls, then one `Merge`. Under D1(A) that is 4 processes. The job fixtures need no case specific to the example. One proposed case: a host listed for two vendors (#49) in the `plan` fixtures.

## 6. Release steps

### 6.1 Who writes, and with what permissions

**Every public write is made by the practice-library bot**, through the organisation's app wrapper, which runs one `gh` command with a short-lived installation token. No person's account and no workflow token publishes anything. The bot's writes are:
- pushing branches and opening PRs;
- the annotated tag, made through the git data API (`gh api .../git/tags`, then `.../git/refs`);
- the GitHub release (`gh release create --verify-tag`);
- every asset upload.

**The app's installation**, scoped to the repositories it serves, and no others, has only these permissions:

| Permission | Level | Why |
|---|---|---|
| Metadata | read | always required |
| Contents | write | branches, the tag object and ref, releases and assets |
| Pull requests | write | opening, updating and merging PRs |
| Issues | write | the signed backlog actions (close, comment, edit) |
| Workflows | write | only because a PR may change `.github/workflows/` (§4's triggers) |
| Checks | read | the gate (§6.3) |
| Actions | read | the gate's CI artefact and run lookups |

It has **no** Administration, Secrets, Variables, Environments, Packages, Pages, Deployments or Members permission. Branch protection and rulesets are not the bot's to change.

Nothing goes to a package registry. Publication runs on the maintainer's machine and public runners only, at $0.

### 6.2 The branch flow

1. A branch per release, `release/contract-v1alpha1.N`, cut from `main` by the bot.
2. Child PRs merge into it, each with one review pass at its exact head. `checks.yml` runs on them, which is the planned `release/**` trigger in §4.
3. One release PR from the branch to `main`, authored by the bot. Its body carries:
   - the self-check and the conformance result;
   - the `contract-archive` sha256 at its head;
   - the review receipts;
   - the release note that says why each contract change is compatible (§2.3).
4. A reviewer who is not the author gives an ACCEPT at the exact head, with every required check green. The repository's approver then signs at that head, and the bot merges it with the head sha bound (`gh api -X PUT .../pulls/<n>/merge -f sha=<head>`). Nothing bound for `main` is squashed.

`contract/v1alpha1.0` needs no release branch, because it is cut from a commit already on `main` (§6.5).

### 6.3 The gate before publishing

Let `C` be the full 40-character commit and `A` the archive the bot has built. The bot publishes only when every check below holds. Each is a read-only `gh api` call, and its result goes in the release body.

1. **`C` is on `main`.** `GET compare/C...main` returns `status` of `identical` (main is at C) or `ahead` (main has commits after C, so C is an ancestor). `behind` or `diverged` fails.
2. **Every required check passed at exactly `C`.**
   - Read `GET commits/C/check-runs?per_page=100`, following every `next` page, and keep the latest run of each name.
   - Map each job id in `.github/required-checks.json` to its check-run name. A plain job is its id (`unit`, `conformance`, `proto`, `js`). A job that calls a reusable workflow is `<id> / <inner job>`. At 65b3d2c those are `contract-schemas / schema-check`, `recorded-report / schema-check`, `leak-scan / leak-scan`, `denylist / denylist`, `removed-paths / removed-paths`, `release-note / release-note` and `schema-loosening / schema-loosening`. Add **`gate / gate`**, which the ruleset requires.
   - Each mapped run must exist, have `head_sha` equal to `C`, come from the GitHub Actions app, and have `conclusion: success`. Missing, skipped, neutral or anything else fails. Runs not on the list (`heartbeat`, `refresh`) are ignored.
3. **`A` is the right archive.** `sha256(A)` equals the sha256 CI printed at `C` (`contract-archive`), or, for `.0`, both of §6.5's independent builds.
4. **The tag is new, or already names `C`.** If `refs/tags/contract/v1alpha1.N` is missing, the bot creates the tag object pointing at `C`, then the ref. If it exists and names `C` (a retry), it's used as it is. If it names anything else, stop. Tags are never moved or deleted.

**Binding the release and the uploads to `C` and `A`,** retries included:
- **Before `gh release create`**, and again before **each** upload, the bot re-resolves the tag to its commit and checks it equals `C`.
- **Each asset has its own check before it's uploaded:**
  - **The archive:** `sha256` of the file equals `sha256(A)`, the hash the gate verified in check 3.
  - **`SHA256SUMS`:** its bytes equal exactly the one expected line, `<sha256(A)>  machinery-contract-v1alpha1.N.tar` followed by a newline. Its own hash is never compared with the archive's.
- **`gh release create --verify-tag`** never creates a tag, and the release names `C` in its body.
- **A retry** after a partial publish first finds the existing release for the tag, draft or not (`gh api .../releases`, matched on `tag_name`). It never creates a second one, because a draft doesn't claim its tag and a second `gh release create` would make another draft. Then it lists that release's assets.
  - An asset already there, with the expected name and size, is downloaded and checked again by its own rule above.
  - A matching asset is kept.
  - A mismatching one, archive or `SHA256SUMS`, stops the release, which is left as a **draft** for a person. Nothing is deleted or overwritten.
- **The release is created as a draft** and published, which removes the draft flag, only after every asset has passed. A consumer never sees a release with missing or unchecked assets.

If any check fails, nothing further is written.

### 6.4 What a contract release holds

| Item | What it is |
|---|---|
| The tag | `contract/v1alpha1.N`, annotated, naming its commit |
| The archive | `machinery-contract-v1alpha1.N.tar`, made by the **pinned command** `git -c tar.umask=0002 -c core.attributesFile=/dev/null archive --format=tar --prefix=machinery-contract-v1alpha1.N/ <commit> proto contract conformance`. The archive's bytes depend on configuration as well as on the commit (`tar.umask=0000` gives different bytes), so the configuration is part of the command. |
| What the archive contains | `proto/` (the service), `contract/` (the v1alpha1 and v0 schemas, the examples, `pins.json` and its README), and `conformance/` (job fixtures, runtime fixtures from `.1` on, the runners and the requirement tables) |
| `SHA256SUMS` | the archive's sha256, in `sha256sum` format |
| The release body | the commit, the gate results of §6.3, the `gen` release built from the same proto, and the release note |

The generated code stays in its own releases, `gen/go/vX.Y.Z` with the wheel attached. A contract release only names the one that matches it.

### 6.5 The first release: `contract/v1alpha1.0`

- **Commit:** `65b3d2ce34c67c7887a2564b3f694239aebb642d`, the head of `main` after #43.
- **Gate:** every required check at that commit passed: unit, contract-schemas, recorded-report, conformance, proto, js, leak-scan, denylist, removed-paths, release-note, schema-loosening and gate. `heartbeat` was skipped, and it isn't required.
- **Its gen release:** `gen/go/v0.1.0` (d6024652). `proto/` and `gen/` are byte-identical between the two commits.
- **Since 269fd0f** (the commit an existing consumer pins), it adds only the optional `ignoreInterval` input and its cases (#43), which is additive.
- **Archive sha256:** `f9c513a94e735ec850f1ba09d8a13f919342b34c2e4aad1c8eaa4a774cffad4f`, built with the pinned command of §6.4 on git 2.54.0 (`tar.umask=0002`). `contract-archive` doesn't exist at that commit, so the bot checks it another way. It builds the archive twice, from two fresh clones, with two different git versions. Both sha256 values go in the release body, and any mismatch stops the release.
- **No runtime fixtures yet.** Those come in `contract/v1alpha1.1`.

### 6.6 How a consumer pins and checks a release

A consumer keeps a **vendored, verified copy**. It needs no module dependency, and the generated Go module and Python wheel don't supply the fixtures anyway. Its pin keeps **both** of these:

- **The release record:** `tag`, `commit`, `asset` and the asset's `sha256`. For `.0`:
  ```
  tag     contract/v1alpha1.0
  commit  65b3d2ce34c67c7887a2564b3f694239aebb642d
  asset   machinery-contract-v1alpha1.0.tar
  sha256  f9c513a94e735ec850f1ba09d8a13f919342b34c2e4aad1c8eaa4a774cffad4f
  ```
- **One record per vendored file:** its path in the archive, its sha256 and its git blob id. A consumer whose pin is already a per-file manifest keeps that manifest and adds the release record. It doesn't replace the manifest.

To update it:
1. Download the asset and `SHA256SUMS` from the release of the tag.
2. Check the asset's sha256 against the pin's release record. A mismatch refuses the update.
3. Check that the tag names the pinned commit: `gh api .../git/ref/tags/<tag>`, then the tag object's `object.sha`.
4. Unpack only the paths the consumer uses. Recompute each file's sha256 and blob id, and write them into the per-file records.
5. **Tests:** the consumer's pin reader validates the release record (tag form, 40-hex commit, asset name, sha256) **and** still checks every vendored file against its record. Reading the commit from the pin must not remove that independent per-file check.
6. **Goldens** produced from the vendored fixtures are regenerated and reviewed in the same change.

**From commit 269fd0f to `contract/v1alpha1.0`,** seven files in the archive's paths changed (`git diff --name-status 269fd0f 65b3d2c -- proto contract conformance`). All of them come from #43's optional `ignoreInterval`, which is additive:

| File | Who vendors it |
|---|---|
| `conformance/connect/plan.json`: two new Plan cases. New sha256 `d8e10f8184d6c179f2215b47be306cf95e296066ae8cf741bff1a85c7a740a54`, git blob `d70f40cb521e25bbfa6dfeb648c5889f88bc0bde` | v1alpha1 consumers |
| `contract/v1alpha1/fact-refresh.schema.json`: the optional `ignoreInterval` | v1alpha1 consumers |
| `conformance/cases/plan.json` and `contract/v0/fact-refresh.schema.json`: the same, for v0 | v0 consumers, such as the gateway |
| `contract/examples/v0/fact-refresh.json`, `contract/README.md`, `conformance/README.md`: examples and prose | readers |

`proto/` is unchanged. A consumer that runs the Plan fixtures must update any golden that lists their cases.

**Verifying an asset is not regenerating it.** A consumer always checks the **published** sha256. It never relies on its own `git archive`, whose bytes depend on git's version and configuration. Only the bot regenerates the archive, with the pinned command, before publishing (§6.3).

## 7. After a release

- machinery-go and machinery-python update their pins to the new tag in their next PR. Neither pins a bare commit for contract files.
- From `contract/v1alpha1.0` on, `buf breaking` and `contract-compat` run against the latest contract tag. The compatibility promise of §2.3 holds from `.1`.
- machinery-go and machinery-python adopt `.1`: it carries the runtime fixtures and the stdio binding (§2.4). `.0` serves consumers of the job fixtures over loopback HTTP.
