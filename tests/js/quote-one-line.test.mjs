// lib/js/quote-one-line.mjs against the requirement table (conformance/quote-one-line.json), and against the
// runtime's own Unicode categories for every code point from U+0000 to U+10FFFF. Run: node --test tests/js/quote-one-line.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { quoteOneLine } from "../../lib/js/quote-one-line.mjs";

const table = JSON.parse(readFileSync(new URL("../../conformance/quote-one-line.json", import.meta.url), "utf8"));

// a code point as a one-code-point string; U+D800..U+DFFF become lone surrogates
const char = (cp) => (cp > 0xffff ? String.fromCodePoint(cp) : String.fromCharCode(cp));

test("every case of the requirement table", () => {
  assert.ok(table.cases.length > 40);
  for (const c of table.cases) {
    const options = "max" in c ? { max: c.max } : {};
    assert.equal(quoteOneLine(c.input.map(char).join(""), options), c.output, c.name);
  }
});

// The sweep takes category membership from the runtime with the same category set as the module, so it checks the
// escaping, not the choice of categories. The table and the list below, written out by hand, check the choice.
const CATEGORY = /^[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]$/u;
const UNSAFE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Cs}]/u;  // what must never be in the output raw

test("every code point: escaped exactly when its category says so, and never left raw when unsafe", () => {
  let escaped = 0;
  for (let cp = 0; cp <= 0x10ffff; cp += 1) {
    const ch = char(cp);
    const out = quoteOneLine(ch);
    if (UNSAFE.test(out)) assert.fail(`U+${cp.toString(16)} leaves ${JSON.stringify(out)} with a raw control`);
    const json = JSON.stringify(ch);
    if (CATEGORY.test(ch) && json === `"${ch}"`) {
      const want = cp > 0xffff ? `"\\u{${cp.toString(16)}}"` : `"\\u${cp.toString(16).padStart(4, "0")}"`;
      if (out !== want) assert.fail(`U+${cp.toString(16)}: ${out}, want ${want}`);
      escaped += 1;
    } else if (out !== json) {
      assert.fail(`U+${cp.toString(16)}: ${out}, want JSON.stringify's ${json}`);
    }
  }
  // DEL and the 32 C1 controls, about 170 format characters, and the 2 separators: 205 in Unicode 15/16
  assert.ok(escaped >= 200, `only ${escaped} code points escaped beyond JSON.stringify`);
});

test("characters that must be escaped, listed by hand and not from a category", () => {
  const must = [0x7f, 0x80, 0x85, 0x9b, 0x9f, 0xad, 0x061c, 0x180e, 0x200b, 0x200c, 0x200d, 0x200e, 0x200f, 0x2028, 0x2029,
    0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2060, 0x2066, 0x2067, 0x2068, 0x2069, 0xfeff, 0xe0001, 0xe0041, 0xe007f];
  for (const cp of must) {
    const want = cp > 0xffff ? `"\\u{${cp.toString(16)}}"` : `"\\u${cp.toString(16).padStart(4, "0")}"`;
    assert.equal(quoteOneLine(String.fromCodePoint(cp)), want, `U+${cp.toString(16)}`);
  }
});

test("the output is one line", () => {
  for (const cp of [0x0a, 0x0d, 0x85, 0x2028, 0x2029, 0x0b, 0x0c]) {
    assert.doesNotMatch(quoteOneLine(`a${char(cp)}b`), /[\n\r\u000b\u000c\u0085\u2028\u2029]/);
  }
});

test("a value that is not a string, or a max that is not a count, is refused", () => {
  for (const value of [undefined, null, 1, ["a"], { toString: () => "a" }]) {
    assert.throws(() => quoteOneLine(value), TypeError);
  }
  for (const max of [-1, 1.5, Number.NaN, Infinity, "3", 2 ** 53]) {
    assert.throws(() => quoteOneLine("abc", { max }), RangeError);
  }
  assert.equal(quoteOneLine("abc", { max: Number.MAX_SAFE_INTEGER }), '"abc"');
});

test("a long value gives its first max code points", () => {
  const long = "a".repeat(5_000_000);
  assert.equal(quoteOneLine(long), `"${"a".repeat(40)}"`);
});
