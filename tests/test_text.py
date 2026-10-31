from __future__ import annotations

import unittest

from factrefresh import robots
from factrefresh.text import has_quote, page_text

UA = "practice-library-graphs/1 (+https://github.com/practice-library/graphs)"


class Text(unittest.TestCase):
    def test_inline_tags_entities_and_no_break_spaces_do_not_hide_a_quote(self):
        text = page_text("<p>Each key may send <b>20&nbsp;requests</b>\n per second.</p>", "html")
        self.assertTrue(has_quote(text, "Each key may send 20 requests per second."))

    def test_script_text_is_not_page_text(self):
        text = page_text("<script>var s = 'Secret sentence.';</script><p>Visible.</p>", "html")
        self.assertFalse(has_quote(text, "Secret sentence."))

    def test_block_elements_separate_words(self):
        self.assertEqual(page_text("<li>one</li><li>two</li>", "html"), "one two")

    def test_changed_wording_is_not_the_quote(self):
        self.assertFalse(has_quote(page_text("<p>The limit is 30.</p>", "html"), "The limit is 20."))

    def test_text_format_is_matched_as_it_is(self):
        self.assertTrue(has_quote(page_text("a <b> c", "text"), "a <b> c"))


class Robots(unittest.TestCase):
    def test_wildcards_and_end_anchor(self):
        r = robots.parse("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?print\n", UA)
        self.assertFalse(r.allows("https://h.example.com/a/b.pdf"))
        self.assertTrue(r.allows("https://h.example.com/a/b.pdf.html"))
        self.assertFalse(r.allows("https://h.example.com/page?print=1"))

    def test_longest_rule_wins_and_allow_wins_a_tie(self):
        r = robots.parse("User-agent: *\nDisallow: /docs/\nAllow: /docs/public/\n", UA)
        self.assertTrue(r.allows("https://h.example.com/docs/public/x"))
        self.assertFalse(r.allows("https://h.example.com/docs/x"))
        tie = robots.parse("User-agent: *\nDisallow: /a\nAllow: /a\n", UA)
        self.assertTrue(tie.allows("https://h.example.com/a"))

    def test_a_group_naming_us_replaces_star(self):
        r = robots.parse("User-agent: *\nDisallow: /\n\nUser-agent: practice-library-graphs\nAllow: /\n", UA)
        self.assertTrue(r.allows("https://h.example.com/x"))

    def test_consecutive_user_agent_lines_share_a_group(self):
        r = robots.parse("User-agent: a\nUser-agent: *\nDisallow: /x\n", UA)
        self.assertFalse(r.allows("https://h.example.com/x"))

    def test_empty_disallow_allows_everything(self):
        self.assertTrue(robots.parse("User-agent: *\nDisallow:\n", UA).allows("https://h.example.com/x"))
