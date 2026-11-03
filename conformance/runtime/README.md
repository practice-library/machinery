# Runtime fixtures

These fixtures check a **runtime**: the library a plugin is served through. They don't check a job. A runtime is accepted when it serves the example plugin below, and that plugin passes every fixture here with `conformance/connect_run.py`. The fixtures in `conformance/connect/` are different: they check the fact-refresh **job**.

Every runtime repository runs these fixtures at a pinned contract release (`contract/v1alpha1.N`), checked by the release's asset sha256 (`docs/lld.md` §6.6). It never pins a bare commit.

## What they check

| File | Call | Cases | What the runtime decides |
|---|---|---|---|
| `describe.json` | `Describe` | 24 | Strict JSON (no duplicate keys, NaN or lone surrogates; unknown fields refused); the caps bounds; `protocol` and `contracts` added by the runtime; a package or call it doesn't serve is `unimplemented` |
| `plan.json` | `Plan` | 11 | A success with every envelope field and integers written as integers, at any size (below); a refusal as `failed_precondition` with its `Failure` detail; a plugin that overruns `timeSeconds` is `deadline_exceeded` |
| `run-unit.json` | `RunUnit` | 24 | The network rules proto can't state: presence, the host pattern, `hosts` or `replay`, and the recorded responses. A request that keeps them reaches the plugin |
| `merge.json` | `Merge` | 1 | An answer larger than `outputBytes` is refused as `resource_exhausted`, never cut, with a `Failure` detail that fits |

Warnings the runtime writes itself are compared word for word, so every runtime uses the same wording.

**Integral numbers, at any size.** `Struct` and `Value` hold doubles. A runtime writes every integral double as an integer, whatever its size: `9007199254740994` for 2^53+2, never `9007199254740994.0`; `100000000000000000000` for 1e20, never `1e+20`; and 1e300 as the 301 digits of the double it is. Four `plan.json` cases hold it: the example plugin copies `run` back, and `run.id` is an integer with no maximum, at 2^53, 2^53+2, 1e20 and 1e300. The runner refuses an integral number written with a fraction or an exponent. A negative zero is written `0`. That is a rule only: the runner reads `-0` and `0` alike, so no fixture can hold it.

## The example plugin each runtime ships

The fixtures expect a plugin that behaves exactly like this. Each runtime writes it in its own language, for example `examples/minimal.py` in machinery-python. It speaks the fact-refresh job's schemas, so the runner can validate its answers, but it fetches nothing.

| Call | It answers |
|---|---|
| `Describe` | `name` `minimal`, `version` `0.1.0`, `job` `fact-refresh`, and three verbs: `plan` (not shardable), `run-unit` (shardable) and `merge` (not shardable). Their `input` and `output` are `$id`s of `contract/v1alpha1/fact-refresh.schema.json#/$defs/` (`planInput`/`plan`, `unit`/`partial`, `mergeInput`), and merge's `output` is the refresh-report schema. The runtime adds `protocol` and `contracts`. |
| `Plan` | If `caps.timeSeconds` is 1, it first sleeps 5 seconds, which is longer than the cap. If `input.nodes` is not empty, it refuses with `failed_precondition` and the one warning `this example plans no node, and the graph has <n>`. Otherwise it answers `graph`, `commit` and `run` copied from the input, `startedAt` set to `input.now`, `maxParallel` 1, and empty `units` and `decided`. |
| `RunUnit` | It always refuses with `unavailable` and the one warning `this example never fetches: try the unit again later`. |
| `Merge` | It answers an object `{"padding": <"x" repeated caps.outputBytes times>}`, which is too large on purpose, so the runtime must refuse it. |
| `Run` | Not implemented, so the runtime answers `unimplemented`. |

On start it prints its base URL as the first line of stdout.

## Running them

From a runtime repository, with machinery's release unpacked at `machinery/`:

```sh
<start the example plugin> > server.url &
python3 machinery/conformance/connect_run.py --url "$(head -1 server.url)" \
  --report-schema <the refresh-report schema at contract/pins.json> \
  --validator jsonschema --fixtures machinery/conformance/runtime
```

## Where they came from

These fixtures were moved unchanged from practice-library/machinery-python at commit `233800cf314027681aeb7d41bd61f1ffd67185b2`, from `conformance/fixtures/`. machinery owns them from now on. Changes are made here, and every runtime picks them up with the next contract release. The table records the files as they were moved; `plan.json` has since gained the four integral-number cases.

| File | git blob at 233800c | sha256 |
|---|---|---|
| `describe.json` | `5c071a7126e14a9f4e9cd7602063587949f7fc0b` | `23318676afe94af739a25d66b0e196ea1df3ff5ea9401d43c7b6750018b12cc6` |
| `plan.json` | `857f24c4f34afdab5dcea48e46e934fcb730a78f` | `837a87a68d5e20c3143e4edac4d7672bd32a88c406c644e5cdf95f4ef7920b43` |
| `run-unit.json` | `9cdf206f7b491093d130176c88cae74a68f1ac1d` | `5d373bcc80ef1ffa4b9a3c6a8d50ee8010da46ec34d9b9f2a2f1242db8783640` |
| `merge.json` | `3fd6fec4d12915d7ffa0dbb02bff87493c2a939c` | `64922272450f5ec22f311855f6b0fbd6831210261a1cf890d9594693d4a44aa0` |

The stdio binding is added in a later change of `contract/v1alpha1.1`.
