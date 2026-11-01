from __future__ import annotations

import io
import sys
import unittest
from unittest import mock

from factrefresh import robots
from factrefresh.text import Unreadable, has_quote, page_text, pdf_text
from tests.support import pdf, pdf_update

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


def encrypted(data: bytes) -> bytes:
    import pypdf  # imported here, as in factrefresh.text, so the other tests here run without it

    writer = pypdf.PdfWriter(clone_from=io.BytesIO(data))
    writer.encrypt("", "owner", algorithm="RC4-128")  # an empty user password: any reader could open it
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class Pdf(unittest.TestCase):
    def test_a_sentence_in_the_pdf_is_found_and_one_not_in_it_is_not(self):
        text = pdf_text(pdf("Notices are published", "within seven days."))
        self.assertTrue(has_quote(text, "Notices are published within seven days."))
        self.assertFalse(has_quote(text, "Notices are published within eight days."))

    def test_a_truncated_pdf_is_unreadable(self):
        data = pdf("Notices are published within seven days.")
        for cut in (len(data) // 2, data.index(b"xref"), len(data) - 7):  # half, no trailer, no %%EOF
            with self.subTest(cut=cut), self.assertRaisesRegex(Unreadable, "^unreadable: the PDF is cut off"):
                pdf_text(data[:cut])

    def test_a_pdf_saved_twice_reads_its_last_revision(self):
        data = pdf_update(pdf("Notices are published within seven days."), "Notices are published within ten days.")
        self.assertEqual(pdf_text(data), "Notices are published within ten days.")
        self.assertEqual(pdf_text(data + b"\r\n\x00 "), "Notices are published within ten days.")

    def test_a_pdf_cut_inside_its_last_revision_is_unreadable_never_its_older_text(self):
        old = pdf("Notices are published within seven days.")
        data = pdf_update(old, "Notices are published within ten days.")
        for cut in (len(old) + (len(data) - len(old)) // 2, len(data) - 7, len(data) - 2):  # mid-revision, before and inside %%EOF
            with self.subTest(cut=cut), self.assertRaisesRegex(Unreadable, "^unreadable: the PDF is cut off"):
                pdf_text(data[:cut])

    def test_without_the_pdf_reader_installed_a_pdf_is_unreadable(self):
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaisesRegex(Unreadable, "^unreadable: no PDF reader is installed"):
                pdf_text(pdf("Notices are published within seven days."))

    def test_an_encrypted_pdf_is_unreadable_even_with_an_empty_password(self):
        with self.assertRaisesRegex(Unreadable, "^unreadable: the PDF is encrypted"):
            pdf_text(encrypted(pdf("Notices are published within seven days.")))

    def test_a_pdf_without_text_is_unreadable_never_a_page_where_every_quote_is_gone(self):
        with self.assertRaisesRegex(Unreadable, "^unreadable: the PDF has no text layer"):
            pdf_text(pdf())

    def test_a_body_that_is_not_a_pdf_is_unreadable(self):
        for body in (b"", b"<html><p>Notices are published within seven days.</p>", b" %PDF-1.4"):
            with self.subTest(body=body), self.assertRaisesRegex(Unreadable, "^unreadable: not a PDF"):
                pdf_text(body)


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


class WholeQuote(unittest.TestCase):
    def test_a_quote_ending_in_a_digit_does_not_match_a_longer_number(self):
        self.assertFalse(has_quote("It costs $100 a month.", "$10"))
        self.assertTrue(has_quote("It costs $10 a month.", "$10"))

    def test_a_quote_starting_in_a_digit_does_not_match_inside_a_grouped_number(self):
        self.assertFalse(has_quote("Up to 1,000 per month.", "000 per month"))
        self.assertFalse(has_quote("Version 2.5 ships.", "5 ships"))
        self.assertTrue(has_quote("Up to 1,000 per month.", "1,000 per month"))

    def test_a_word_is_not_matched_inside_a_longer_word(self):
        self.assertFalse(has_quote("The limits are unlimited.", "limited."))
        self.assertTrue(has_quote("Requests are limited.", "limited."))

    def test_a_later_whole_match_counts_after_an_inner_one(self):
        self.assertTrue(has_quote("$100 or $10 a month", "$10"))

    def test_an_empty_or_blank_quote_is_never_found(self):
        self.assertFalse(has_quote("anything", ""))
        self.assertFalse(has_quote("anything", " \n\t "))

    def test_table_cells_separate_words(self):
        text = page_text("<table><tr><td>20</td><td>requests</td></tr></table>", "html")
        self.assertTrue(has_quote(text, "20 requests"))
        self.assertFalse(has_quote(text, "20requests"))
