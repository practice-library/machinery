"""Page text, and whether a quote is in it.

The quote is matched exactly, character for character, after both sides have their runs of whitespace
(including no-break spaces) collapsed to one space. Nothing else is folded: a quote whose wording or
punctuation changed is not on the page.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List

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

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag in _BLOCK:
            self.out.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self.out.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


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
