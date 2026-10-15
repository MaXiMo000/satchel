"""Turn raw HTML into (title, author, text). Wraps trafilatura rather than
reimplementing boilerplate-stripping -- a naive "grab the biggest <div>"
approach is exactly the kind of thing that looks fine on one test page and
breaks on the next real site's markup.

Deliberately takes HTML directly (bytes or str), not a URL -- fetching is a
separate, network-dependent step (see fetch.py), and keeping extraction pure
means it can be tested offline against fixture HTML with no network
involved. Bytes are preferred: trafilatura's own charset detection is
better than trusting the HTTP header.
"""
from __future__ import annotations

import json
import re

import trafilatura

_BR_PARAGRAPH = re.compile(rb"(?:<br\s*/?>\s*){2,}", re.IGNORECASE)
# Written into the page as character references, so it survives any page
# encoding, and visible text, so trafilatura keeps it where it keeps the
# words around it.
_MARK = "¶¶¶"


def _mark_br_paragraphs(html: bytes | str) -> tuple[bytes | str, bool]:
    """Mark the paragraph breaks of a page laid out with <br><br>.

    Hand-written pages (Paul Graham's essays, many personal sites) put the
    whole text in one element, break it with double <br>, and hard-wrap the
    source every few dozen characters. trafilatura returns that as one line
    for the whole article, or one per source line, depending on the rest of
    the page. Joining the source lines and leaving a marker at each double
    <br> lets the paragraphs be restored after extraction, whatever
    trafilatura does with the markup. A page with <pre> is left alone: its
    line breaks are content.
    """
    raw = html.encode("utf-8") if isinstance(html, str) else html
    if not _BR_PARAGRAPH.search(raw) or re.search(rb"<pre", raw, re.IGNORECASE):
        return html, False
    raw = re.sub(rb"[ \t]*\r?\n[ \t]*", b" ", raw)
    return _BR_PARAGRAPH.sub(b" &#182;&#182;&#182; ", raw), True


def extract(html: bytes | str, url: str | None = None) -> dict | None:
    """Returns {"title", "author", "text"}, or None if trafilatura couldn't
    find a main content block at all (a login wall, an empty page, a page
    that's entirely JavaScript-rendered with nothing in the raw HTML)."""
    html, marked = _mark_br_paragraphs(html)
    raw = trafilatura.extract(html, url=url, output_format="json", with_metadata=True)
    if not raw:
        return None
    data = json.loads(raw)
    text = data.get("text") or ""
    if marked:
        # " | " is how trafilatura renders the layout-table cells such pages
        # sit in; at the edge of a paragraph it is never part of the text.
        parts = re.split(rf"\s*{_MARK}\s*|\n", text)
        text = "\n".join(p.strip(" |") for p in parts if p.strip(" |"))
    if not text.strip():
        return None
    return {"title": data.get("title"), "author": data.get("author"), "text": text}
