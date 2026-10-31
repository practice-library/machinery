"""Plugins that break the process rules the conformance runner checks, one way each: python3 bad.py <way>.
Each answers describe correctly in every other respect, so only the check for its way can catch it."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))))

from factrefresh.plugin import describe  # noqa: E402

way = sys.argv[1]
sys.stdin.buffer.read()
data, _ = describe()
envelope = {"ok": True, "data": data, "notRead": [], "warnings": []}
answer = json.dumps(envelope, separators=(",", ":")).encode()
out, code = answer + b"\n", 0


def sized(total):
    """A valid answer of exactly total bytes, the newline included."""
    base = len(json.dumps(dict(envelope, warnings=[""]), separators=(",", ":")).encode()) + 1
    return json.dumps(dict(envelope, warnings=["w" * (total - base)]), separators=(",", ":")).encode() + b"\n"


if way == "stdout-at-cap":  # the case's outputBytes is 4096
    out = sized(4096)
elif way == "stdout-one-over":
    out = sized(4097)
elif way == "stderr-at-cap":
    sys.stderr.buffer.write(b"e" * 65536)
elif way == "stderr-one-over":
    sys.stderr.buffer.write(b"e" * 65537)
elif way == "hang":
    time.sleep(30)
elif way == "partial-line":  # cut off before its end and its newline
    out = answer[:-5]
elif way == "no-newline":
    out = answer
elif way == "line-before":
    out = b"starting\n" + answer + b"\n"
elif way == "line-after":
    out = answer + b"\n" + b"done\n"
elif way == "two-answers":
    out = answer + b"\n" + answer + b"\n"
elif way == "exit-4":  # the case expects 4, so only the check of the four codes catches it
    code = 4
    out = json.dumps({"ok": False, "data": None, "notRead": [], "warnings": ["x"]}).encode() + b"\n"
elif way == "ok-on-failure":  # exit 1 with ok true; the case expects exit 1
    code = 1
elif way == "wrong-contract":  # describe answers in a contract it was not asked in
    out = json.dumps(dict(envelope, data=dict(data, protocol="7")), separators=(",", ":")).encode() + b"\n"
elif way == "verb-twice":
    out = json.dumps(dict(envelope, data=dict(data, verbs=data["verbs"] + data["verbs"][:1])), separators=(",", ":")).encode() + b"\n"
elif way != "good":
    sys.exit(f"unknown way {way}")
sys.stdout.buffer.write(out)
sys.stdout.flush()
sys.exit(code)
