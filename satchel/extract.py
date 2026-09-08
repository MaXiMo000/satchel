"""Turn raw HTML into (title, author, text). Wraps trafilatura rather than
reimplementing boilerplate-stripping -- a naive "grab the biggest <div>"
approach is exactly the kind of thing that looks fine on one test page and
breaks on the next real site's markup.

Deliberately takes HTML as a string, not a URL -- fetching is a separate,
network-dependent step (see fetch.py), and keeping extraction pure means it
can be tested offline against fixture HTML with no network involved.
"""
from __future__ import annotations

import json

import trafilatura


def extract(html: str, url: str | None = None) -> dict | None:
    """Returns {"title", "author", "text"}, or None if trafilatura couldn't
    find a main content block at all (a login wall, an empty page, a page
    that's entirely JavaScript-rendered with nothing in the raw HTML)."""
    raw = trafilatura.extract(html, url=url, output_format="json", with_metadata=True)
    if not raw:
        return None
    data = json.loads(raw)
    text = data.get("text") or ""
    if not text.strip():
        return None
    return {"title": data.get("title"), "author": data.get("author"), "text": text}
