"""The one place "fetch, extract, store" happens -- used by both the CLI's
`add` command and the local capture listener (`serve.py`), so there is
exactly one add pipeline, not two that can drift apart.
"""
from __future__ import annotations

import sqlite3
import urllib.error

from . import db
from .extract import extract
from .fetch import UnsafeURLError, _check_scheme, fetch, normalize_url


def add_article(conn: sqlite3.Connection, raw_url: str, *, restrict_private_network: bool = False) -> dict:
    """Fetch, extract, and store one article.

    Returns {"ok": bool, "message": str, "id": int | None}.

    restrict_private_network is False for direct CLI use (a human typing a
    URL into their own terminal isn't a threat to themselves) and True for
    the capture listener (see serve.py) -- there, the URL comes from
    whatever page happened to be open in the browser, not from the person
    running satchel, and that's exactly the boundary an SSRF guard exists
    for.
    """
    url = normalize_url(raw_url)
    source = ""
    try:
        html, final_url = fetch(url, restrict_private_network=restrict_private_network)
    except urllib.error.HTTPError as exc:
        # The server answered and refused -- StackOverflow sends 403 to
        # anything that is not a browser. The page is public, so its
        # archived copy is the honest next place to look. Only on an HTTP
        # answer: a URL that never connected may be an intranet address,
        # and asking archive.org about it would leak it.
        found = _from_wayback(url) if exc.code in _REFUSED or exc.code >= 500 else None
        if found is None:
            return {"ok": False, "message": f"could not fetch {url}: {exc}", "id": None}
        html, final_url, source = *found, " (from the Wayback Machine; the live site refused)"
    except Exception as exc:  # noqa: BLE001 - a bad/unsafe fetch is a clear result, not a crash
        return {"ok": False, "message": f"could not fetch {url}: {exc}", "id": None}
    # Normalize again after following redirects -- the URL actually served
    # (past a shortener, or an http->https upgrade) is the real dedup key.
    return _store(conn, normalize_url(final_url), html, source)


def add_page(conn: sqlite3.Connection, raw_url: str, html: str) -> dict:
    """Store a page the browser already has open, from its own HTML.

    Nothing is fetched, so there is no request for an SSRF guard to police,
    and the saved text is exactly what the reader saw -- including a page
    behind their own login, which a second fetch could never reach.
    """
    try:
        _check_scheme(raw_url)
    except UnsafeURLError as exc:
        return {"ok": False, "message": str(exc), "id": None}
    return _store(conn, normalize_url(raw_url), html)


def _store(conn: sqlite3.Connection, url: str, html: bytes | str, source: str = "") -> dict:
    article = extract(html, url=url)
    if article is None:
        return {"ok": False, "message": f"could not extract article text from {url}", "id": None}

    try:
        article_id = db.add(conn, url, article["title"], article["author"], article["text"])
    except sqlite3.IntegrityError:
        return {"ok": False, "message": f"already saved: {url}", "id": None}

    return {"ok": True, "message": f"saved #{article_id}: {article['title'] or url}{source}", "id": article_id}


_REFUSED = {403, 404, 410, 429, 451}


def _from_wayback(url: str) -> tuple[bytes, str] | None:
    """(html, the original url) from the closest archived copy, or None."""
    from .importers import wayback_snapshot
    try:
        snap = wayback_snapshot(url)
        return (fetch(snap)[0], url) if snap else None
    except Exception:  # noqa: BLE001 - the archive being down is just "not found"
        return None
