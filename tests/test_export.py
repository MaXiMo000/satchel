"""Run: python tests/test_export.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from satchel import db
from satchel.cli import main


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.db_path = str(self.dir / "s.db")
        conn = db.connect(self.db_path)
        db.add(conn, "https://example.com/a", 'Trains: a "short" history', "Ana Ruiz",
               "First paragraph about sleeper cars.\nSecond paragraph & more.", "2026-01-02T00:00:00Z")
        db.add(conn, "https://example.com/b", 'Trains: a "short" history', None,
               "A different article with the same title.", "2026-01-03T00:00:00Z")
        db.add(conn, "https://example.com/c", "Bread", None, "Flour, water, salt.", "2026-01-04T00:00:00Z")
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_one_file_each_with_front_matter(self):
        out = self.dir / "md"
        self.assertEqual(main(["--db", self.db_path, "export", "--markdown", str(out)]), 0)
        files = sorted(out.glob("*.md"))
        self.assertEqual(len(files), 3)  # same title twice, two files
        text = (out / "0001-trains-a-short-history.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith('---\ntitle: "Trains: a \\"short\\" history"\n'))
        self.assertIn('url: "https://example.com/a"', text)
        self.assertIn("First paragraph about sleeper cars.\n\nSecond paragraph & more.", text)

    def test_epub_is_a_valid_container_with_a_chapter_per_article(self):
        book = self.dir / "s.epub"
        self.assertEqual(main(["--db", self.db_path, "export", "--epub", str(book)]), 0)
        with zipfile.ZipFile(book) as z:
            first = z.infolist()[0]
            self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            chapters = [n for n in z.namelist() if n.startswith("OEBPS/a")]
            self.assertEqual(len(chapters), 3)
            for name in z.namelist():
                if name.endswith((".xhtml", ".opf", ".xml")):
                    ET.fromstring(z.read(name))  # every document is well-formed XML
            newest = z.read("OEBPS/a1.xhtml").decode("utf-8")
            self.assertIn("Bread", newest)
            oldest = z.read("OEBPS/a3.xhtml").decode("utf-8")
            self.assertIn("<p>Second paragraph &amp; more.</p>", oldest)

    def test_search_limits_what_is_exported(self):
        out = self.dir / "md"
        self.assertEqual(main(["--db", self.db_path, "export", "--markdown", str(out), "--search", "sleeper"]), 0)
        self.assertEqual([p.name for p in out.glob("*.md")], ["0001-trains-a-short-history.md"])
        self.assertEqual(main(["--db", self.db_path, "export", "--markdown", str(out), "--search", '"bad']), 1)


if __name__ == "__main__":
    unittest.main()
