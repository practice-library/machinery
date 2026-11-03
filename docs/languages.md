# Which language runtimes machinery needs

Status: **signed by the founder on 2026-11-02** (D9 in docs/hld.md). This version answers the review of the signed draft without changing the decision. machinery leads this question. The runtime sections come from the machinery-go controller (machinery-go 03045fd) and the machinery-python controller (machinery-python 233800c).

This file states public requirements and public evidence only. Creating a repository is the founder's alone, and this document proposes none.

## Decision

| Runtime | Decision | Why |
|---|---|---|
| **Go** | **build it** (machinery-go, #17 PR 6) | The default language for runtimes. It gives static binaries for amd64 and arm64 with no native dependencies, real cancellation, and integers written correctly without a workaround. |
| **Python** | **keep it** (machinery-python, #17 PR 4, exists) | fact-refresh, the first real plugin, is Python, and so is the `.github` gateway driver. Its scope is D5 in docs/hld.md. |
| **Rust** | **not now** | No tool that plugs into machinery needs it. It comes in only by the rule below, with a measured gain. |
| **Node / TypeScript** | **no runtime** | TypeScript may be used for types generated from the schemas, never hand-written. |
| **Others** | **none** | No tool needs one. |

**The rule for adding a runtime later:**
1. A named tool must plug into machinery and need that runtime.
2. That need must be shown by measurement on the tool's real input. Rust specifically needs a cut of at least 30 % in peak RSS or CPU, or in build-plus-test file events, against Go, with CI doing the compile.
3. The runtime passes every runtime fixture on both CI arches at a pinned contract release (docs/lld.md §3).
4. The founder creates its repository.

## Evidence

### Go (from the machinery-go controller)

- **Today:** nothing is implemented. The plan is v1alpha1 only, on `gen/go/v0.1.0` (d6024652), with fixtures from `contract/v1alpha1.N`.
- **Cost:**
  - The generated code is free: a tagged module, with `go.sum` pinning connect v2.0.2 and protobuf v1.36.12.
  - Go 1.26.0 is the floor connect-go sets, so it needs a `toolchain` line (#31 item 2).
  - `CGO_ENABLED=0` static binaries cross-build for amd64 and arm64.
  - CI runs on ubuntu-24.04 and ubuntu-24.04-arm.
- **Gaps to close:**
  - protojson isn't the contract's JSON: lone surrogates become U+FFFD and NaN is accepted. Go needs a strict pre-decode like Python's `codec.py`, and must encode every envelope field itself.
  - Go writes integral doubles as integers natively, so it has no #29.
  - `context` gives real cancellation.
- **Bindings:** one core serves both loopback HTTP and stdio (D1), with about 150 lines for stdio.

### Python (from the machinery-python controller)

- **Today:** v1alpha1 is implemented at machinery d6024652:
  - all five calls, over Connect unary JSON on loopback HTTP/1.1;
  - strict decoding, and the rules proto can't state;
  - a Failure on every error, shrunk to fit `outputBytes`;
  - the deadline as min(timeSeconds, Connect-Timeout-Ms);
  - integral numbers written as integers. The controller reports #29 worked around (machinery-python#4).
- **Gaps:**
  - A late plugin's thread is abandoned, not stopped (machinery-python#17). D4 allows that, and the stdio binding ends the process.
  - No gRPC or binary, deliberately.
  - Native wheels for linux x86_64 and macOS arm64 only, with **no linux arm64**.
  - No release yet, and no real plugin yet.
- **Cost:** 8 hash-pinned wheels (2 native), plus machinery's generated wheel.
- **The 2.7 minutes are not a language figure.** The unit job took 2m30s because `tests/guards.py` re-runs the suite 39 times. Conformance took 12 s.

### Rust

- **Measured:** nothing yet. No tool that plugs into machinery has asked for it.
- **The cost if it ever comes:** a third strict JSON decoder, the Failure shrink order, the warning wording the fixtures compare, and a CI matrix. It would gain real cancellation, which Go already has.

### Node / TypeScript

- **Not a runtime.** If a consumer needs TypeScript types, generate them from the JSON Schemas or the proto.
- **Node inside machinery today:** `lib/js/quote-one-line.mjs` and a CI job on pinned Node 24. This is a vendored helper that JS tools copy, not a runtime. The plan is to write a Go twin against the same table (`conformance/quote-one-line.json`), and to drop Node from CI once no JS consumer remains.

## What any runtime must match (the conformance story)

The runtime fixtures, `conformance/runtime/`, arrive in `contract/v1alpha1.1`, together with the stdio binding, so `.1` is the tag both runtimes adopt. They are moved from machinery-python's 56 runtime cases at 233800c, and machinery owns them from then on. Every runtime pins them by contract tag, not by commit. They hold:
- **Strict JSON:** UTF-8, no duplicate keys, no NaN, no lone surrogates, unknown fields refused. Proto's leniencies are kept: the proto field names, and integers as strings.
- **The rules proto can't state:** the caps bounds, input and network presence, the host pattern and replay. The warning wording is the same as Python's `rules.py`, because the fixtures compare it.
- **Integral numbers:** in Struct and Value, **every** integral double is written as an integer, as the `plugin.proto` header and `contract/README.md` require: `9007199254740994` for 2^53+2, `100000000000000000000` for 1e20, `0` for -0. This is not limited to 2^53. Fixtures cover 2^53, 2^53+2, 1e20, 1e300 and -0.
- **Errors:** a Failure detail (base64 binary) on every error, with the same shrink order, and an error body that is shrunk, never stripped. The code and HTTP status follow the table in docs/lld.md §2.1.
- **Limits:** the deadline is min(timeSeconds, Connect-Timeout-Ms). The deadline and output cap are measured on uncompressed bytes.
- **Bindings:** loopback HTTP, with the base URL printed first on stdout, and stdio (D1, docs/lld.md §2.4), both from `contract/v1alpha1.1`. The fixtures run over both.
- **Regressions:** the cases from machinery-python's fixed bugs #5–#16: oversize decode errors, overflow to infinity, the largest finite literal, and status before Failure.

A runtime is accepted when its CI passes all of these against a minimal example plugin, on ubuntu-24.04 and ubuntu-24.04-arm, at a pinned contract release. Languages are compared on conformance time, not unit time.
