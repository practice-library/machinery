# Generated code of the plugin contract

`buf generate` writes this directory from `proto/` (package `machinery.plugin.v1alpha1`, plan #17), with local generators only: nothing is fetched from a registry and no account is needed. Do not edit the generated files; CI regenerates them and fails on any difference.

| Path | What | Generator |
|---|---|---|
| `go/` | Go module `github.com/practice-library/machinery/gen/go`: messages and the Connect service | `protoc-gen-go` (protobuf-go v1.36.12) and `protoc-gen-connect-go` (connect-go v2.0.2), built from `go/go.sum` |
| `python/src/machinery/` | Python package `machinery.plugin.v1alpha1`: messages (`plugin_pb`) and the Connect service (`plugin_connect`) | `protoc-gen-py` 0.7.0 and `protoc-gen-connectrpc` 0.13.0, from `requirements.txt` by hash |
| `python/build_wheel.py` | builds the wheel `machinery_gen-<VERSION>-py3-none-any.whl`, the same bytes every time | standard library |
| `VERSION` | the version of both: the Go tag and the wheel | |

## Regenerating

With buf 1.75.0, Go 1.26 or later, and Python 3.11 or later, on linux x86_64 or macOS arm64 (`gen/requirements.txt` has the native protobuf-py-ext wheel's hashes for those two only):

```sh
(cd gen/go && go build -o "$BIN/" google.golang.org/protobuf/cmd/protoc-gen-go connectrpc.com/connect/v2/cmd/protoc-gen-connect-go)
python3 -m venv "$VENV" && "$VENV/bin/python" -m pip install --require-hashes --only-binary :all: -r gen/requirements.txt
PATH="$BIN:$VENV/bin:$PATH" buf generate
```

The `proto` job of `checks.yml` does the same on every pull request. It deletes the generated files first and compares against the commit, so a stale file fails too. It then vets and builds the Go module, builds the wheel twice and compares the bytes, installs the wheel, and imports the messages. The service module imports the Connect runtime, which this repository does not pin (the runtime PR does), so CI only compiles it.

## Releasing

Nothing is published to a package registry: a registry would need a credential, and that is a separate decision. At a commit on `main` where `gen/VERSION` reads `X.Y.Z`, a maintainer:

1. tags the Go module `gen/go/vX.Y.Z`, so `go get github.com/practice-library/machinery/gen/go@vX.Y.Z` resolves and `go.sum` pins it;
2. runs `python3 gen/python/build_wheel.py --out dist` on Linux or macOS, and attaches the wheel to the GitHub release of that tag together with the sha256 it prints. Anyone can check the checksum by running the same command at the same commit on a POSIX system (on Windows, zip records a different system); the `proto` job prints it at every head.

A consumer pins the wheel by that sha256 (`pip install --require-hashes`).

## Known differences from the contract

- protobuf-py 0.7.0 writes an integral double as `3.0`. The contract asks every runtime to write it as `3`, as the Go runtime does. The Python runtime's codec has to handle this (#29).
