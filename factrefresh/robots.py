"""robots.txt rules as RFC 9309 defines them: the groups for our product token (or *, when none names
it), `*` and `$` in patterns, and the longest matching rule wins, allow on a tie.

The standard library's robotparser matches plain prefixes only, so a rule such as `Disallow: /*?` would
not stop us; this module exists for that reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple
from urllib.parse import urlsplit


@dataclass
class Rules:
    rules: List[Tuple[bool, str]]  # (allow, pattern)

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        best_len, best_allow = -1, True
        for allow, pattern in self.rules:
            if _matches(pattern, path):
                if len(pattern) > best_len or (len(pattern) == best_len and allow):
                    best_len, best_allow = len(pattern), allow
        return best_allow


def product_token(user_agent: str) -> str:
    """The product token of a user agent: `name/1 (+url)` gives `name`."""
    return user_agent.split("/", 1)[0].strip().lower()


def parse(text: str, user_agent: str) -> Rules:
    token = product_token(user_agent)
    groups: List[Tuple[List[str], List[Tuple[bool, str]]]] = []
    agents: List[str] = []
    rules: List[Tuple[bool, str]] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # a user-agent line after rules starts a new group
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            in_rules = True
            if value:  # an empty Disallow allows everything; an empty Allow says nothing
                rules.append((key == "allow", value))
    if agents:
        groups.append((agents, rules))
    mine = [r for a, rs in groups if token in a for r in rs]
    if any(token in a for a, _ in groups):
        return Rules(mine)
    return Rules([r for a, rs in groups if "*" in a for r in rs])


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match(regex + ("$" if anchored else ""), path) is not None
