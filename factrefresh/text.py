"""Page text and table rows, and whether a quote is in them.

A text quote is matched exactly, character for character, after both sides have their runs of whitespace
(including no-break spaces) collapsed to one space. Nothing else is folded: a quote whose wording or
punctuation changed is not on the page. A quote stored as a markdown table row (`| a | b |`) never appears
in a page's text; it is matched as a sequence of cells against the rows of the page's tables.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Union

_SKIP = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption", "figure",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_WS = re.compile(r"\s+")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.skip = 0
        self.rows: List[List[str]] = []  # every table row, as its cells' text
        self._open: List[List[str]] = []  # rows being read, innermost last (tables may nest)
        self._cell: List[Optional[List[str]]] = []  # the cell being read in each open row

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            return
        if tag in _BLOCK:
            self.out.append(" ")
        if tag == "tr":
            self._open.append([])
            self._cell.append(None)
        elif tag in ("td", "th") and self._open:
            self._end_cell()
            self._cell[-1] = []

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.out.append(" ")
            if self._cell and self._cell[-1] is not None:
                self._cell[-1].append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in _BLOCK:
            self.out.append(" ")
        if tag in ("td", "th") and self._open:
            self._end_cell()
        elif tag == "tr" and self._open:
            self._end_cell()
            self.rows.append(self._open.pop())
            self._cell.pop()

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)
            if self._cell and self._cell[-1] is not None:
                self._cell[-1].append(data)

    def _end_cell(self):
        if self._cell[-1] is not None:
            self._open[-1].append(normalise("".join(self._cell[-1])))
            self._cell[-1] = None


def normalise(text: str) -> str:
    return _WS.sub(" ", text).strip()


def page_text(body: str, fmt: str) -> str:
    """The text a quote is searched in: for html the visible text, otherwise the body as it is."""
    if fmt == "html":
        parser = _Text()
        parser.feed(body)
        parser.close()
        body = "".join(parser.out)
    return normalise(body)


def page_rows(body: str, fmt: str) -> List[List[str]]:
    """The rows of the page's tables, each as its cells' normalised text: the <tr> rows of an html page,
    the `| a | b |` lines of any other page."""
    if fmt == "html":
        parser = _Text()
        parser.feed(body)
        parser.close()
        return parser.rows
    return [row for row in (markdown_row(line) for line in body.splitlines()) if row is not None]


_ROW = re.compile(r"\|.*\|")
_PIPE = re.compile(r"(?<!\\)\|")


def markdown_row(line: str) -> Optional[List[str]]:
    """The cells of a markdown table row (`| a | b |`, `\\|` for a pipe inside a cell), or None."""
    stripped = line.strip()
    if len(stripped) < 2 or not _ROW.fullmatch(stripped):
        return None
    return [normalise(cell.replace("\\|", "|")) for cell in _PIPE.split(stripped[1:-1])]


Quote = Dict[str, Union[str, List[str]]]


def quote_form(stored: Union[str, Quote]) -> Quote:
    """A stored quote as the contract carries it: {"cells": [...]} for a markdown table row, otherwise
    {"text": ...}. A form already given ({"text"} or {"cells"}) is returned as it is."""
    if isinstance(stored, dict):
        return stored
    cells = markdown_row(stored)
    return {"cells": cells} if cells is not None else {"text": stored}


def has_cells(rows: List[List[str]], cells: List[str]) -> bool:
    """The cells appear, in order and next to each other, in one row; each cell is compared whole."""
    want = [normalise(c) for c in cells]
    if not any(want):
        return False
    n = len(want)
    return any(row[i:i + n] == want for row in rows for i in range(len(row) - n + 1))


def quote_is_empty(quote: str) -> bool:
    return not normalise(quote)


def _continues(text: str, at: int, step: int, edge: str) -> bool:
    """Whether the character next to a match (at `at`, going `step`) continues the quote's edge character:
    a letter or digit next to a letter or digit, or a digit group (`1,000`, `2.5`) around a digit edge."""
    if not edge.isalnum() or not 0 <= at < len(text):
        return False
    nxt = text[at]
    if nxt.isalnum():
        return True
    beyond = at + step
    return edge.isdigit() and nxt in ",." and 0 <= beyond < len(text) and text[beyond].isdigit()


def has_quote(text: str, quote: str) -> bool:
    """The quote is in the text as a whole: `$10` is not in `$100`, `000 per month` is not in
    `1,000 per month`. An empty quote is never found."""
    q = normalise(quote)
    if not q:
        return False
    start = text.find(q)
    while start != -1:
        end = start + len(q)
        if not _continues(text, start - 1, -1, q[0]) and not _continues(text, end, 1, q[-1]):
            return True
        start = text.find(q, start + 1)
    return False
