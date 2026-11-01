// Each guard of lib/js/quote-one-line.mjs, removed, must make its tests fail.
// For every row: copy the module, its tests and the requirement table to a temporary directory, replace the
// guard's code (which must occur exactly once, so a row cannot silently stop applying), run the tests and
// expect them to fail. Run: node tests/js/guards.mjs
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const MODULE = "lib/js/quote-one-line.mjs";

const GUARDS = [
  // [what the guard keeps true, code, code without the guard]
  ["format characters are escaped", "[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]", "[\\p{Cc}\\p{Zl}\\p{Zp}]"],
  ["controls are escaped", "[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]", "[\\p{Cf}\\p{Zl}\\p{Zp}]"],
  ["the line separator is escaped", "[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]", "[\\p{Cc}\\p{Cf}\\p{Zp}]"],
  ["the paragraph separator is escaped", "[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]", "[\\p{Cc}\\p{Cf}\\p{Zl}]"],
  ["every match is escaped, not only the first", "\\p{Zp}]/gu;", "\\p{Zp}]/u;"],
  ["astral code points are written as \\u{x}", "cp > 0xffff ?", "cp > 0x10ffff ?"],
  ["BMP escapes have four digits", 'cp.toString(16).padStart(4, "0")', "cp.toString(16)"],
  ["max keeps exactly max code points", "if (n === max) break;", "if (n > max) break;"],
  ["a surrogate pair is never split", "for (const ch of value) {", 'for (const ch of value.split("")) {'],
  ["a value that is not a string is refused", 'if (typeof value !== "string") throw', "if (false) throw"],
  ["max is a whole number, 0 or more", "if (!Number.isSafeInteger(max) || max < 0) throw", "if (false) throw"],
];

let failed = 0;
for (const [what, code, mutant] of GUARDS) {
  const tmp = mkdtempSync(join(tmpdir(), "quote-guard-"));
  try {
    for (const d of ["lib/js", "tests/js"]) cpSync(join(ROOT, d), join(tmp, d), { recursive: true });
    cpSync(join(ROOT, "conformance/quote-one-line.json"), join(tmp, "conformance/quote-one-line.json"));
    const target = join(tmp, MODULE);
    const text = readFileSync(target, "utf8");
    const count = text.split(code).length - 1;
    if (count !== 1) {
      console.log(`FAIL ${what}: the guard's code occurs ${count} times in ${MODULE}`);
      failed += 1;
      continue;
    }
    writeFileSync(target, text.replace(code, mutant));
    const run = spawnSync(process.execPath, ["--test", "tests/js/quote-one-line.test.mjs"], { cwd: tmp, encoding: "utf8" });
    if (run.status === 0) {
      console.log(`FAIL ${what}: the tests still pass without the guard`);
      failed += 1;
    } else {
      console.log(`ok   ${what}`);
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}
console.log(`${GUARDS.length - failed} of ${GUARDS.length} guards are tested`);
process.exit(failed ? 1 : 0);
