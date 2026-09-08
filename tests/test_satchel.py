"""Run: python tests/test_satchel.py

No network here -- extraction is tested against a fixture HTML file, and
search/storage against a real (temp-file) sqlite db. Both are the parts
that can actually break silently; fetching a URL is a one-line urllib call.
"""
from __future__ import annotations

import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from satchel import cli, db
from satchel.extract import extract
from satchel.fetch import fetch, normalize_url

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "article.html").read_text()


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_tracking_params(self):
        self.assertEqual(
            normalize_url("https://example.com/a?utm_source=twitter&id=1"),
            "https://example.com/a?id=1",
        )

    def test_strips_trailing_slash(self):
        self.assertEqual(normalize_url("https://example.com/a/"), "https://example.com/a")

    def test_leaves_meaningful_query_and_no_trailing_slash_untouched(self):
        self.assertEqual(normalize_url("https://example.com/a?id=1"), "https://example.com/a?id=1")

    def test_strips_fragment(self):
        self.assertEqual(normalize_url("https://example.com/a#section-2"), "https://example.com/a")

    def test_does_not_touch_multiple_legitimate_query_params_or_their_order(self):
        self.assertEqual(
            normalize_url("https://example.com/a?page=2&sort=new"),
            "https://example.com/a?page=2&sort=new",
        )

    def test_does_not_itself_rewrite_scheme(self):
        # Scheme policy is handled by following the real redirect (see
        # TestFetch below), not by normalize_url guessing -- an http URL
        # with no redirect stays http rather than being silently upgraded.
        self.assertEqual(normalize_url("http://example.com/a"), "http://example.com/a")


class TestFetch(unittest.TestCase):
    def test_returns_bytes_and_the_post_redirect_url(self):
        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"<html></html>"
        fake_response.geturl.return_value = "https://example.com/final"
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            body, final_url = fetch("http://bit.ly/shortlink")

        self.assertEqual(body, b"<html></html>")
        self.assertEqual(final_url, "https://example.com/final")


class TestDefaultDbPath(unittest.TestCase):
    def test_respects_xdg_data_home(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg-test-home"}):
            self.assertEqual(db.default_db_path(), "/tmp/xdg-test-home/satchel/satchel.db")

    def test_falls_back_to_xdg_default_when_unset(self):
        env = dict(os.environ)
        env.pop("XDG_DATA_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            path = db.default_db_path()
            self.assertTrue(path.endswith(".local/share/satchel/satchel.db"))

    def test_connect_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = str(pathlib.Path(tmp) / "a" / "b" / "c" / "satchel.db")
            conn = db.connect(nested)
            conn.close()
            self.assertTrue(pathlib.Path(nested).exists())


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

    def test_accepts_bytes_like_real_fetch_returns(self):
        result = extract(FIXTURE.encode("utf-8"))
        self.assertEqual(result["title"], "Why Local-First Software Is Worth the Extra Effort")


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


class TestCliAdd(unittest.TestCase):
    """cli._do_add end to end, with fetch() mocked so no network is needed --
    real extract() and real db.py run underneath."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(pathlib.Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--db", self.db_path, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_saved_url_is_the_post_redirect_canonical_one_not_the_tracking_link(self):
        with mock.patch("satchel.cli.fetch",
                         return_value=(FIXTURE.encode("utf-8"),
                                       "https://example.com/some-article?utm_source=twitter")):
            code, out, err = self._run("add", "http://bit.ly/shortlink")
        self.assertEqual(code, 0)

        conn = db.connect(self.db_path)
        row = db.get(conn, 1)
        self.assertEqual(row["url"], "https://example.com/some-article")

    def test_same_article_via_two_different_tracking_links_is_one_duplicate(self):
        with mock.patch("satchel.cli.fetch",
                         return_value=(FIXTURE.encode("utf-8"), "https://example.com/some-article")):
            code1, _, _ = self._run("add", "https://example.com/some-article?utm_source=twitter")
            code2, _, err2 = self._run("add", "https://example.com/some-article/?utm_campaign=newsletter")

        self.assertEqual(code1, 0)
        self.assertEqual(code2, 1)
        self.assertIn("already saved", err2)

        conn = db.connect(self.db_path)
        self.assertEqual(len(db.list_all(conn)), 1)


class TestCli(unittest.TestCase):
    """Exercises satchel.cli.main() directly -- these are the paths a real
    invocation actually takes, not just the db/extract layers underneath."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(pathlib.Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--db", self.db_path, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_malformed_fts5_query_is_a_clean_error_not_a_traceback(self):
        # Regression test: an unbalanced quote used to escape db.search()
        # as an uncaught sqlite3.OperationalError all the way out of the
        # CLI. It must now be a normal exit-1 error message.
        code, out, err = self._run("search", 'unterminated "quote')
        self.assertEqual(code, 1)
        self.assertIn("couldn't parse", err)
        self.assertNotIn("Traceback", err)

    def test_bare_search_operator_is_also_a_clean_error(self):
        code, out, err = self._run("search", "-")
        self.assertEqual(code, 1)
        self.assertIn("couldn't parse", err)


if __name__ == "__main__":
    unittest.main()
