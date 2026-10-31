#!/usr/bin/env python3
"""Every example in contract/examples/v0 must validate against its schema, and every counter-example must be
rejected with a finding (the validator's exit code 2), not with an error.

  python3 contract/check_examples.py --validator jsonschema --report-schema <refresh-report schema.json>

Exit 0 when each holds, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from conformance.run import Validator  # noqa: E402

FINDING = 2  # the validator's exit code for an instance it rejects


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--validator", required=True, help="the sourcemeta jsonschema CLI")
    ap.add_argument("--report-schema", required=True, help="the refresh-report schema at the pin in contract/pins.json")
    ap.add_argument("--examples", default=os.path.join(HERE, "examples", "v0"))
    a = ap.parse_args(argv)
    validator = Validator(a.validator, a.report_schema)
    passed = failed = 0
    for name in sorted(os.listdir(a.examples)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(a.examples, name), encoding="utf-8") as f:
            groups = json.load(f)["groups"]
        for group in groups:
            for want_valid, key in ((True, "valid"), (False, "invalid")):
                for ex in group[key]:
                    why = validator.check(group["schema"], ex["instance"])
                    if validator.rc == (0 if want_valid else FINDING):
                        passed += 1
                    else:
                        failed += 1
                        said = f": {why}" if why else ""
                        print(f"FAIL {name}: {ex['name']}: expected {'valid' if want_valid else 'invalid'}{said}")
    print(f"{passed} of {passed + failed} examples and counter-examples hold")
    return 0 if failed == 0 and passed > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
