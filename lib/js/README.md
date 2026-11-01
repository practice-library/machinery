# lib/js

Small, dependency-free JavaScript helpers that any tool may use. Nothing here is published to a package registry. A consumer vendors the file, pinned by machinery commit and sha256, and replaces its own copy instead of patching it.

## quote-one-line.mjs

`quoteOneLine(value, { max = 40 })` quotes an untrusted string for one-line terminal output (machinery#32), for a tool that echoes the input it refused. The output is one line with no raw control (Cc), format (Cf), line separator (Zl), paragraph separator (Zp) or lone surrogate. It is not ASCII-only: every other code point, such as é, an emoji or U+00A0, is left as it is. The bidirectional and zero-width characters are format characters, so they are escaped.

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

A value that is not a string, or a `max` that is not a safe integer of 0 or more (`Number.isSafeInteger`), throws.

The escaped set follows the Unicode version of the JavaScript runtime, so a newer runtime may escape format characters that an older one leaves raw (U+0890, U+0891 and U+13439..U+1343F became format characters after Unicode 13). The table's cases hold from Unicode 15 on; CI runs a pinned Node 24.

The requirement is data, in `conformance/quote-one-line.json`: each case gives the input code points and the exact output. This module is its first implementation. A Python or Go twin, when one is written, is checked against the same table. The JavaScript tests also compare every code point from U+0000 to U+10FFFF with the runtime's own Unicode categories (`tests/js/`).

To vendor it, copy the file at a machinery commit and check its sha256. The line below always gives the sha256 of the file at the same commit, and `.gitattributes` fixes its line endings, so a checkout on any OS has these bytes:

sha256: 6f75dafeaec3d53ce68ae3233254055fb1d3b0cd4303ec430a05ad4cefdf11a8

CI fails if this line and the file disagree.
