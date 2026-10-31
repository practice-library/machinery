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


def has_quote(text: str, quote: str) -> bool:
    return normalise(quote) in text
