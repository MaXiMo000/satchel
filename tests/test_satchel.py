"""Run: python tests/test_satchel.py

No network here -- extraction is tested against a fixture HTML file, and
search/storage against a real (temp-file) sqlite db. Both are the parts
that can actually break silently; fetching a URL is a one-line urllib call.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from satchel import db
from satchel.extract import extract

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "article.html").read_text()


class TestExtract(unittest.TestCase):
    def test_picks_the_article_title_not_the_browser_title(self):
        result = extract(FIXTURE)
        self.assertEqual(result["title"], "Why Local-First Software Is Worth the Extra Effort")

    def test_finds_the_byline_author(self):
        result = extract(FIXTURE)
        self.assertEqual(result["author"], "Jordan Rivers")

    def test_excludes_nav_and_related_links_boilerplate(self):
        result = extract(FIXTURE)
        self.assertNotIn("You might also like", result["text"])
        self.assertNotIn("All rights reserved", result["text"])

    def test_includes_the_real_paragraphs(self):
        result = extract(FIXTURE)
        self.assertIn("SQLite's FTS5 extension", result["text"])

    def test_empty_html_returns_none_not_a_crash(self):
        self.assertIsNone(extract("<html><body></body></html>"))


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(str(pathlib.Path(self.tmp.name) / "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_add_and_get_round_trips(self):
        article_id = db.add(self.conn, "https://example.com/a", "Title", "Author", "Full article text here.")
        row = db.get(self.conn, article_id)
        self.assertEqual(row["url"], "https://example.com/a")
        self.assertEqual(row["title"], "Title")

    def test_duplicate_url_raises(self):
        db.add(self.conn, "https://example.com/a", "T", "A", "text")
        with self.assertRaises(Exception):
            db.add(self.conn, "https://example.com/a", "T2", "A2", "text2")

    def test_full_text_search_finds_a_saved_article_by_body_text(self):
        db.add(self.conn, "https://example.com/local-first",
               "Why Local-First Software Is Worth the Extra Effort", "Jordan Rivers",
               "SQLite's FTS5 extension has solved offline full-text search for over a decade.")
        db.add(self.conn, "https://example.com/unrelated", "Something Else", None,
               "This article is about a completely different topic entirely.")

        results = db.search(self.conn, "FTS5")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.com/local-first")

    def test_search_with_no_matches_returns_empty_not_an_error(self):
        db.add(self.conn, "https://example.com/a", "T", "A", "some text")
        self.assertEqual(db.search(self.conn, "nonexistentword12345"), [])


if __name__ == "__main__":
    unittest.main()
