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
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from satchel import cli, db
from satchel.extract import extract
from satchel.fetch import UnsafeURLError, fetch, normalize_url

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

        with mock.patch("urllib.request.OpenerDirector.open", return_value=fake_response):
            body, final_url = fetch("http://bit.ly/shortlink")

        self.assertEqual(body, b"<html></html>")
        self.assertEqual(final_url, "https://example.com/final")

    def test_non_http_scheme_is_always_rejected_even_without_restriction(self):
        # Scheme checking isn't part of restrict_private_network -- it's
        # always on, for every caller, CLI included.
        with self.assertRaises(UnsafeURLError):
            fetch("file:///etc/passwd")

    def test_ftp_scheme_is_rejected(self):
        with self.assertRaises(UnsafeURLError):
            fetch("ftp://example.com/a")

    def test_unrestricted_fetch_allows_localhost(self):
        # The plain CLI path (a human typing their own URL) is not the
        # threat model restrict_private_network exists for -- a locally
        # running dev server they want to save from must still work.
        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"<html></html>"
        fake_response.geturl.return_value = "http://127.0.0.1:3000/draft"
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        with mock.patch("urllib.request.OpenerDirector.open", return_value=fake_response):
            body, final_url = fetch("http://127.0.0.1:3000/draft")
        self.assertEqual(final_url, "http://127.0.0.1:3000/draft")

    def test_restricted_fetch_refuses_loopback(self):
        with self.assertRaises(UnsafeURLError):
            fetch("http://127.0.0.1:6379/", restrict_private_network=True)

    def test_restricted_fetch_refuses_localhost_by_name(self):
        with self.assertRaises(UnsafeURLError):
            fetch("http://localhost/", restrict_private_network=True)

    def test_restricted_fetch_refuses_cloud_metadata_link_local_address(self):
        # 169.254.169.254 -- the AWS/GCP/Azure instance-metadata endpoint,
        # the single most common real-world SSRF target.
        with self.assertRaises(UnsafeURLError):
            fetch("http://169.254.169.254/latest/meta-data/", restrict_private_network=True)

    def test_restricted_fetch_refuses_rfc1918_private_range(self):
        with self.assertRaises(UnsafeURLError):
            fetch("http://10.0.0.5/internal", restrict_private_network=True)

    def test_restricted_fetch_allows_a_real_public_address(self):
        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"<html></html>"
        fake_response.geturl.return_value = "https://example.com/a"
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        with mock.patch("urllib.request.OpenerDirector.open", return_value=fake_response), \
             mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            body, final_url = fetch("https://example.com/a", restrict_private_network=True)
        self.assertEqual(final_url, "https://example.com/a")

    def test_restricted_fetch_refuses_a_redirect_to_a_private_address(self):
        # The initial URL is public; the *redirect target* is private. If
        # only the first hop were checked, this would slip through --
        # exercise the redirect handler directly rather than standing up a
        # real HTTP server to prove the second hop, not just the first, is
        # validated.
        from satchel.fetch import _RestrictedRedirectHandler
        handler = _RestrictedRedirectHandler()
        req = urllib.request.Request("https://example.com/a")
        with self.assertRaises(UnsafeURLError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/internal")


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
        with mock.patch("satchel.capture.fetch",
                         return_value=(FIXTURE.encode("utf-8"),
                                       "https://example.com/some-article?utm_source=twitter")):
            code, out, err = self._run("add", "http://bit.ly/shortlink")
        self.assertEqual(code, 0)

        conn = db.connect(self.db_path)
        row = db.get(conn, 1)
        self.assertEqual(row["url"], "https://example.com/some-article")

    def test_same_article_via_two_different_tracking_links_is_one_duplicate(self):
        with mock.patch("satchel.capture.fetch",
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


class TestServe(unittest.TestCase):
    """Runs a real satchel.serve.serve() HTTPServer in a background thread
    and hits it with real HTTP requests -- this is the capture listener a
    bookmarklet actually talks to, so it's tested as one, not by calling
    internal functions directly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(pathlib.Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def _running_server(self):
        import http.server

        from satchel import serve as serve_module

        token = "test-token-abc123"
        with mock.patch("secrets.token_urlsafe", return_value=token):
            handler_cls = serve_module._make_handler(self.db_path, token)
        httpd = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield port, token
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def _get(self, port: int, path: str) -> tuple[int, dict]:
        import json
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_wrong_token_is_refused(self):
        with self._running_server() as (port, token):
            status, body = self._get(port, "/add?token=wrong&url=https://example.com/a")
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_missing_token_is_refused(self):
        with self._running_server() as (port, token):
            status, body = self._get(port, "/add?url=https://example.com/a")
        self.assertEqual(status, 403)

    def test_missing_url_is_a_clean_400(self):
        with self._running_server() as (port, token):
            status, body = self._get(port, f"/add?token={token}")
        self.assertEqual(status, 400)
        self.assertIn("no url", body["message"])

    def test_unknown_path_is_404(self):
        with self._running_server() as (port, token):
            status, body = self._get(port, "/whatever")
        self.assertEqual(status, 404)

    def test_valid_capture_saves_the_article(self):
        with mock.patch("satchel.capture.fetch",
                         return_value=(FIXTURE.encode("utf-8"), "https://example.com/some-article")):
            with self._running_server() as (port, token):
                status, body = self._get(
                    port, f"/add?token={token}&url=https://example.com/some-article")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

        conn = db.connect(self.db_path)
        self.assertEqual(len(db.list_all(conn)), 1)

    def test_capture_of_a_private_network_url_is_refused_even_with_a_valid_token(self):
        # The listener always calls add_article with restrict_private_
        # network=True -- a page's bookmarklet cannot use satchel as an
        # SSRF pivot into the local network, token or no token.
        with self._running_server() as (port, token):
            status, body = self._get(
                port, f"/add?token={token}&url=http://169.254.169.254/latest/meta-data/")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("resolves to a non-public address", body["message"])

    def test_bookmarklet_contains_the_real_port_and_token(self):
        from satchel.serve import _bookmarklet
        js = _bookmarklet(8765, "abc123")
        self.assertIn("127.0.0.1:8765", js)
        self.assertIn("abc123", js)
        self.assertTrue(js.startswith("javascript:"))


if __name__ == "__main__":
    unittest.main()
