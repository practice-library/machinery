#!/usr/bin/env python3
"""Builds the wheel of the generated Python code (gen/python/src/machinery), byte for byte the same from the
same commit, and prints its sha256 and file name. Standard library only: no build backend to pin.

  python3 gen/python/build_wheel.py --out DIR

The version is gen/VERSION. The wheel holds the generated package's .py files and its dist-info. Every entry
is dated 1980-01-01, has mode 0644 and is stored, not deflated (deflate output differs between zlib
versions); entries are in sorted order. So anyone can check the release's checksum by building it again.
Nothing is published to a registry: the release attaches the wheel and its sha256 (gen/README.md).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
import zipfile

GEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(GEN, "python", "src")
PACKAGE = "machinery"
NAME = "machinery_gen"
REQUIRES = ("protobuf-py==0.7.0", "connectrpc==0.13.0")
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\n")
EPOCH = (1980, 1, 1, 0, 0, 0)


def version(path: str = os.path.join(GEN, "VERSION")) -> str:
    """The X.Y.Z in the file, which must be exactly that and one newline."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    if not VERSION_RE.fullmatch(text):
        raise SystemExit(f"{path} must be one line X.Y.Z")
    return text[:-1]


def sources(src: str = SRC) -> list[tuple[str, bytes]]:
    """(path in the wheel, bytes) of every .py file under src/machinery, sorted by path; nothing else."""
    found = []
    for root, _dirs, files in os.walk(os.path.join(src, PACKAGE)):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, "rb") as f:
                    found.append((os.path.relpath(path, src).replace(os.sep, "/"), f.read()))
    if not any(p.endswith("/plugin_pb.py") for p, _ in found):
        raise SystemExit(f"no generated code under {src}: run buf generate first")
    return sorted(found)


def record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def build(out: str, ver: str, files: list[tuple[str, bytes]]) -> str:
    dist = f"{NAME}-{ver}.dist-info"
    metadata = "".join([
        "Metadata-Version: 2.1\n", f"Name: {NAME.replace('_', '-')}\n", f"Version: {ver}\n",
        "Summary: Generated code of the machinery plugin contract (package machinery.plugin.v1alpha1)\n",
        "Requires-Python: >=3.11\n", *(f"Requires-Dist: {r}\n" for r in REQUIRES)]).encode("utf-8")
    wheel = b"Wheel-Version: 1.0\nGenerator: gen/python/build_wheel.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    entries = files + [(f"{dist}/METADATA", metadata), (f"{dist}/WHEEL", wheel)]
    record = "".join(f"{p},{record_hash(d)},{len(d)}\n" for p, d in entries) + f"{dist}/RECORD,,\n"
    entries.append((f"{dist}/RECORD", record.encode("utf-8")))
    path = os.path.join(out, f"{NAME}-{ver}-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, EPOCH)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, data)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="the directory to write the wheel to")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    path = build(a.out, version(), sources())
    with open(path, "rb") as f:
        print(f"{hashlib.sha256(f.read()).hexdigest()}  {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
