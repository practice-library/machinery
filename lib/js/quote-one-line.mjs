// quoteOneLine: quote an untrusted string for one-line terminal output (machinery#32). No dependencies.
// The requirement is conformance/quote-one-line.json; this is its first implementation.
//
//   import { quoteOneLine } from "./quote-one-line.mjs";
//   console.error(`refused: ${quoteOneLine(arg)} is not a flag`);
//
// It keeps the first `max` code points (default 40; a surrogate pair is never split), applies JSON.stringify,
// then writes every code point of general category Cc, Cf, Zl or Zp that is still raw as \uxxxx, or \u{x}
// above U+FFFF. That covers the C1 controls and DEL, the bidirectional controls, zero-width and other format
// characters, the tag characters and the line and paragraph separators, which JSON.stringify leaves raw.
// The set follows the Unicode version of the JavaScript runtime.

const RAW = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;

function escape(ch) {
  const cp = ch.codePointAt(0);
  return cp > 0xffff ? `\\u{${cp.toString(16)}}` : `\\u${cp.toString(16).padStart(4, "0")}`;
}

export function quoteOneLine(value, { max = 40 } = {}) {
  if (typeof value !== "string") throw new TypeError("quoteOneLine quotes a string");
  if (!Number.isSafeInteger(max) || max < 0) throw new RangeError("max is a whole number of code points, 0 or more");
  let cut = "";
  let n = 0;
  for (const ch of value) {  // by code point: a pair is one, a lone surrogate is one
    if (n === max) break;
    cut += ch;
    n += 1;
  }
  return JSON.stringify(cut).replace(RAW, escape);
}
