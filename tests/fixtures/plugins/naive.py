"""A plugin that should fail conformance: the real one, but every tag becomes a space and table quotes are
never matched as cells, as the design review's naive extractor did."""
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))))

from factrefresh import refresh, text  # noqa: E402
from factrefresh.plugin import main  # noqa: E402


def naive_text(body, fmt):
    body = re.sub(r"(?is)<(script|style|noscript|template|svg|head)\b.*?</\1\s*>", " ", body)
    return text.normalise(html.unescape(re.sub(r"<[^>]*>", " ", body)))


refresh.page_text = naive_text
refresh.has_cells = lambda rows, cells: False
sys.exit(main())
