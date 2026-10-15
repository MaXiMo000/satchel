"""Take the archive elsewhere: Markdown files for a notes app, or one EPUB
for an e-reader. Both are written with the standard library only.
"""
from __future__ import annotations

import html
import pathlib
import re
import sqlite3
import time
import uuid
import zipfile


def _slug(title: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.UNICODE)
    return re.sub(r"[\s_-]+", "-", s).strip("-")[:limit].strip("-") or "article"


def _yaml(value: str | None) -> str:
    # Double-quoted YAML scalar: the two characters that end or escape it.
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_markdown(rows: list[sqlite3.Row], directory: str | pathlib.Path) -> list[pathlib.Path]:
    """One .md per article, front matter first. The id prefix keeps two
    articles with the same title from overwriting each other."""
    out = pathlib.Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for row in rows:
        title = row["title"] or row["url"]
        path = out / f"{row['id']:04d}-{_slug(title)}.md"
        paragraphs = [line.strip() for line in row["text"].splitlines() if line.strip()]
        path.write_text(
            "---\n"
            f"title: {_yaml(title)}\n"
            f"url: {_yaml(row['url'])}\n"
            f"author: {_yaml(row['author'])}\n"
            f"saved: {_yaml(row['added_at'])}\n"
            "---\n\n"
            f"# {title}\n\n" + "\n\n".join(paragraphs) + "\n",
            encoding="utf-8", newline="\n")
        written.append(path)
    return written


def _xhtml(title: str, body: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f"<head><title>{html.escape(title)}</title></head><body>{body}</body></html>")


def to_epub(rows: list[sqlite3.Row], path: str | pathlib.Path, title: str = "satchel") -> int:
    """One EPUB 3 book, one chapter per article, newest first."""
    book_id = f"urn:uuid:{uuid.uuid4()}"
    chapters = []
    for n, row in enumerate(rows, 1):
        name = row["title"] or row["url"]
        paragraphs = "".join(f"<p>{html.escape(line.strip())}</p>"
                             for line in row["text"].splitlines() if line.strip())
        byline = html.escape(" · ".join(x for x in (row["author"], (row["added_at"] or "")[:10]) if x))
        body = (f"<h1>{html.escape(name)}</h1><p><small>{byline}<br/>"
                f"{html.escape(row['url'])}</small></p>{paragraphs}")
        chapters.append((f"a{n}.xhtml", name, _xhtml(name, body)))

    nav = "".join(f'<li><a href="{f}">{html.escape(t)}</a></li>' for f, t, _ in chapters)
    manifest = "".join(f'<item id="c{n}" href="{f}" media-type="application/xhtml+xml"/>'
                       for n, (f, _, _) in enumerate(chapters, 1))
    spine = "".join(f'<itemref idref="c{n}"/>' for n in range(1, len(chapters) + 1))
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="id">{book_id}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
           '<dc:language>en</dc:language>'
           f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta>'
           '</metadata>'
           f'<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           f"{manifest}</manifest><spine>{spine}</spine></package>")

    with zipfile.ZipFile(path, "w") as z:
        # The mimetype entry must come first and be stored uncompressed.
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>", compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/book.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", _xhtml(title, f'<nav epub:type="toc"><ol>{nav}</ol></nav>'),
                   compress_type=zipfile.ZIP_DEFLATED)
        for f, _, doc in chapters:
            z.writestr(f"OEBPS/{f}", doc, compress_type=zipfile.ZIP_DEFLATED)
    return len(chapters)
