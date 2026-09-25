"""Run: python tests/test_import.py

Every supported export format, parsed from files shaped like the real
exports, and the import pipeline end to end with the network mocked out
(fetch/extract/Wayback) so this runs offline and deterministically.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from satchel import db, importers
from satchel.cli import main


class TestParsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, text: str) -> pathlib.Path:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_pocket_csv(self):
        p = self._write("part_000000.csv",
                        "title,url,time_added,cursor,tags,status\n"
                        "Local-first software,https://example.com/lfs,1451606400,,essays,archive\n"
                        "Untitled,https://example.com/b,1700000000,,,unread\n")
        kind, items = importers.parse_export(p)
        self.assertEqual(kind, "pocket")
        self.assertEqual([i.url for i in items], ["https://example.com/lfs", "https://example.com/b"])
        self.assertEqual(items[0].title, "Local-first software")
        self.assertEqual(items[0].added_at, "2016-01-01T00:00:00Z")

    def test_instapaper_csv(self):
        p = self._write("instapaper-export.csv",
                        "URL,Title,Selection,Folder,Timestamp\n"
                        "https://example.com/i,An essay,,Unread,1600000000\n")
        kind, items = importers.parse_export(p)
        self.assertEqual(kind, "instapaper")
        self.assertEqual(items[0].title, "An essay")
        self.assertEqual(items[0].added_at, "2020-09-13T12:26:40Z")

    def test_csv_without_a_url_column_is_a_clear_error(self):
        p = self._write("x.csv", "name,value\na,b\n")
        with self.assertRaisesRegex(ValueError, "'url' column"):
            importers.parse_export(p)

    def test_browser_bookmarks_html(self):
        p = self._write("bookmarks.html", """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
  <DT><H3>Reading</H3>
  <DL><p>
    <DT><A HREF="https://example.com/a" ADD_DATE="1500000000">Article A</A>
    <DT><A HREF="https://example.com/b">Article B</A>
  </DL><p>
</DL>""")
        kind, items = importers.parse_export(p)
        self.assertEqual(kind, "bookmarks")
        self.assertEqual([(i.url, i.title) for i in items],
                         [("https://example.com/a", "Article A"), ("https://example.com/b", "Article B")])
        self.assertEqual(items[0].added_at, "2017-07-14T02:40:00Z")
        self.assertIsNone(items[1].added_at)

    def test_pocket_legacy_html(self):
        p = self._write("ril_export.html",
                        '<ul><li><a href="https://example.com/p" time_added="1451606400" tags="x">P</a></li></ul>')
        kind, items = importers.parse_export(p)
        self.assertEqual(kind, "pocket")
        self.assertEqual(items[0].added_at, "2016-01-01T00:00:00Z")

    def test_plain_url_list(self):
        p = self._write("urls.txt", "https://example.com/1\n\n# a comment\nnot a url\nhttp://example.com/2\n")
        kind, items = importers.parse_export(p)
        self.assertEqual(kind, "urls")
        self.assertEqual([i.url for i in items], ["https://example.com/1", "http://example.com/2"])

    def _omnivore_folder(self) -> pathlib.Path:
        root = self.dir / "omnivore"
        (root / "content").mkdir(parents=True)
        (root / "metadata_0_to_2.json").write_text(json.dumps([
            {"id": "1", "slug": "saved-with-text", "title": "Has text", "author": "Ada",
             "url": "https://example.com/o1", "savedAt": "2023-05-01T10:00:00.000Z", "labels": []},
            {"id": "2", "slug": "no-content-file", "title": "Link only",
             "url": "https://example.com/o2", "savedAt": "2023-06-01T10:00:00.000Z"},
        ]), encoding="utf-8")
        (root / "content" / "saved-with-text.md").write_text("# Has text\n\nThe body.", encoding="utf-8")
        return root

    def test_omnivore_folder_carries_its_own_text(self):
        kind, items = importers.parse_export(self._omnivore_folder())
        self.assertEqual(kind, "omnivore")
        self.assertEqual(items[0].text, "# Has text\n\nThe body.")
        self.assertEqual(items[0].author, "Ada")
        self.assertEqual(items[0].added_at, "2023-05-01T10:00:00Z")
        self.assertIsNone(items[1].text)  # no content file: will be fetched

    def test_omnivore_zip(self):
        root = self._omnivore_folder()
        z = self.dir / "omnivore-export.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for f in root.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(self.dir).as_posix())
        kind, items = importers.parse_export(z)
        self.assertEqual(kind, "omnivore")
        self.assertEqual(len(items), 2)


def _fake_network(live: dict[str, str], archived: dict[str, str]):
    """live/archived map a URL to article text; anything else is dead."""
    def fake_fetch(url, **kw):
        if url in live:
            return live[url].encode(), url
        if url.startswith("https://web.archive.org/"):
            return archived[url.split("id_/", 1)[1]].encode(), url
        raise OSError("HTTP Error 404: Not Found")

    def fake_extract(html, url=None):
        text = html.decode()
        return {"title": f"T:{text[:10]}", "author": None, "text": text} if text else None

    def fake_wayback(url, timeout=15.0):
        return f"https://web.archive.org/web/20150101000000id_/{url}" if url in archived else None

    return contextlib.ExitStack(), [
        mock.patch.object(importers, "fetch", fake_fetch),
        mock.patch.object(importers, "extract", fake_extract),
        mock.patch.object(importers, "wayback_snapshot", fake_wayback),
    ]


class TestRunImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(str(pathlib.Path(self.tmp.name) / "s.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run(self, items, live, archived, **kw):
        stack, patches = _fake_network(live, archived)
        with stack:
            for p in patches:
                stack.enter_context(p)
            return importers.run_import(self.conn, items, workers=2, **kw)

    def test_live_wayback_export_and_dead_links_are_each_accounted_for(self):
        items = [
            importers.Item(url="https://example.com/live", added_at="2016-01-01T00:00:00Z"),
            importers.Item(url="https://example.com/rotted"),
            importers.Item(url="https://example.com/gone"),
            importers.Item(url="https://example.com/inline", text="already here", title="Inline"),
            importers.Item(url="ftp://example.com/x"),
        ]
        result = self._run(items, live={"https://example.com/live": "live body"},
                           archived={"https://example.com/rotted": "archived body"})
        self.assertEqual((result["saved"], result["from_export"], result["from_wayback"], result["failed"]),
                         (3, 1, 1, 2))
        self.assertEqual({u for u, _ in result["failures"]}, {"https://example.com/gone", "ftp://example.com/x"})
        rows = {r["url"]: r for r in db.list_all(self.conn)}
        self.assertEqual(rows["https://example.com/live"]["added_at"], "2016-01-01T00:00:00Z")
        self.assertIn("https://example.com/rotted", rows)
        self.assertEqual(db.search(self.conn, "archived")[0]["url"], "https://example.com/rotted")

    def test_no_wayback_means_a_dead_link_just_fails(self):
        result = self._run([importers.Item(url="https://example.com/rotted")], live={},
                           archived={"https://example.com/rotted": "archived body"}, wayback=False)
        self.assertEqual((result["saved"], result["failed"]), (0, 1))

    def test_importing_the_same_export_twice_saves_nothing_new(self):
        items = lambda: [importers.Item(url="https://example.com/live?utm_source=x"),
                         importers.Item(url="https://example.com/live")]
        first = self._run(items(), live={"https://example.com/live": "b"}, archived={})
        second = self._run(items(), live={"https://example.com/live": "b"}, archived={})
        self.assertEqual(first["saved"], 1)
        self.assertEqual((second["saved"], second["skipped"]), (0, 2))


class TestFetchDecompression(unittest.TestCase):
    def test_a_gzip_response_nobody_asked_for_is_decompressed(self):
        # The Wayback Machine gzips regardless of Accept-Encoding; handed on
        # compressed, every curly quote in a rescued article became U+FFFD.
        import gzip
        import http.server
        import threading

        from satchel.fetch import fetch

        page = "<html><body><p>Omnivore’s shutdown</p></body></html>".encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = gzip.compress(page)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body, _ = fetch(f"http://127.0.0.1:{server.server_port}/")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(body, page)


class TestWaybackLookup(unittest.TestCase):
    def test_the_looked_up_url_keeps_its_scheme_and_slashes_literal(self):
        # The availability API finds nothing for https%3A%2F%2F... (measured).
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            return Resp(json.dumps({"archived_snapshots": {"closest": {
                "available": True, "status": "200", "timestamp": "20250503155629",
                "url": "http://web.archive.org/web/20250503155629/https://ex.com/a?b=1"}}}).encode())

        with mock.patch.object(importers.urllib.request, "urlopen", fake_urlopen):
            snap = importers.wayback_snapshot("https://ex.com/a?b=1")
        self.assertIn("url=https://ex.com/a%3Fb%3D1", seen["url"])
        self.assertEqual(snap, "https://web.archive.org/web/20250503155629id_/https://ex.com/a?b=1")


class TestCli(unittest.TestCase):
    def test_import_command_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            export = pathlib.Path(d) / "pocket.csv"
            export.write_text("title,url,time_added\nA,https://example.com/live,1451606400\n"
                              "B,https://example.com/gone,1451606400\n", encoding="utf-8")
            failures = pathlib.Path(d) / "failed.txt"
            stack, patches = _fake_network({"https://example.com/live": "live body"}, {})
            out = io.StringIO()
            with stack, contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                for p in patches:
                    stack.enter_context(p)
                code = main(["--db", str(pathlib.Path(d) / "s.db"), "import", str(export),
                             "--failures", str(failures)])
            self.assertEqual(code, 0)
            self.assertIn("pocket export: 2 item(s)", out.getvalue())
            self.assertIn("saved 1", out.getvalue())
            self.assertIn("1 failed", out.getvalue())
            self.assertIn("https://example.com/gone", failures.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
