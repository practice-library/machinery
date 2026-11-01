# lib/js

Small, dependency-free JavaScript helpers that any tool may use. Nothing here is published to a package registry. A consumer vendors the file, pinned by machinery commit and sha256, and replaces its own copy instead of patching it.

## quote-one-line.mjs

`quoteOneLine(value, { max = 40 })` quotes an untrusted string for one-line terminal output (machinery#32), for a tool that echoes the input it refused:

```js
import { quoteOneLine } from "./quote-one-line.mjs";
console.error(`refused: ${quoteOneLine(arg)} is not a flag`);
```

It works in three steps:
1. It keeps the first `max` code points. A surrogate pair is never split.
2. It applies `JSON.stringify`.
3. It writes every code point of general category Cc, Cf, Zl or Zp that is still raw as `\uxxxx`, or as `\u{x}` above U+FFFF.

Step 3 covers what `JSON.stringify` leaves raw:
- DEL and the C1 controls;
- the bidirectional controls;
- the zero-width and other format characters;
- the tag characters;
- the line and paragraph separators.

A value that is not a string, or a `max` that is not a whole number of 0 or more, throws.

The requirement is data, in `conformance/quote-one-line.json`: each case gives the input code points and the exact output. This module is its first implementation. A Python or Go twin is checked against the same table. The JavaScript tests also compare every code point from U+0000 to U+10FFFF with the runtime's own Unicode categories (`tests/js/`).

To vendor it, copy the file at a machinery commit and check its sha256. At the commit that adds this README the sha256 is:

sha256: 6f3d5e03a7dbe5c0925bf147d1407916d3c67bb30cf39dd08a8c0d0c2c5e5ff8

CI fails if this line and the file disagree.
