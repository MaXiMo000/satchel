"""Import a reading list from another service's export.

Read-later services die (Omnivore in 2024, Pocket in 2025) and leave their
users with an export file. `satchel import <file>` takes that file as-is:

- Omnivore  -- the export .zip (or its unzipped folder): metadata_*.json plus
               content/<slug>.md. The article text is already in the export,
               so these are saved with no network at all.
- Pocket    -- the CSV export (title,url,time_added,...) or the older
               ril_export.html.
- Instapaper-- the CSV export (URL,Title,Selection,Folder,Timestamp).
- Browsers  -- a bookmarks .html export (Chrome, Firefox, Safari, Edge all
               write the same Netscape format).
- Anything  -- a text file with one URL per line.

Link-only formats are fetched and extracted through the same pipeline as
`satchel add`. When the live page is gone -- the usual fate of a link saved
years ago -- the Internet Archive's closest Wayback Machine snapshot is
tried instead, so link rot doesn't mean the article is lost.
"""
from __future__ import annotations

import concurrent.futures
import csv
import datetime
import html.parser
import json
import pathlib
import sqlite3
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass

from . import db
from .extract import extract
from .fetch import fetch, normalize_url

WAYBACK_API = "https://archive.org/wayback/available?url="


@dataclass
class Item:
    url: str
    title: str | None = None
    author: str | None = None
    added_at: str | None = None   # ISO 8601, UTC
    text: str | None = None       # already-extracted text (Omnivore); None means fetch it


def _iso(unix_seconds) -> str | None:
    try:
        ts = int(float(unix_seconds))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_string(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- parsers ----------------------------------------------------------------

def _parse_omnivore(root: pathlib.Path) -> list[Item]:
    items = []
    for meta_file in sorted(root.glob("metadata_*.json")):
        for entry in json.loads(meta_file.read_text(encoding="utf-8")):
            url = entry.get("url")
            if not url:
                continue
            text = None
            slug = entry.get("slug")
            if slug:
                content = root / "content" / f"{slug}.md"
                if content.is_file():
                    text = content.read_text(encoding="utf-8").strip() or None
            items.append(Item(url=url, title=entry.get("title"), author=entry.get("author"),
                              added_at=_iso_string(entry.get("savedAt")), text=text))
    return items


def _parse_csv(path: pathlib.Path) -> tuple[str, list[Item]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "csv", []
    cols = {c.strip().lower() for c in rows[0] if c}

    def get(row, name):
        for k, v in row.items():
            if k and k.strip().lower() == name:
                return (v or "").strip()
        return ""

    if {"url", "time_added"} <= cols:
        kind, date_col = "pocket", "time_added"
    elif {"url", "timestamp"} <= cols:
        kind, date_col = "instapaper", "timestamp"
    elif "url" in cols:
        kind, date_col = "csv", None
    else:
        raise ValueError(f"{path.name}: a CSV import needs a 'url' column (found: {', '.join(sorted(cols))})")
    items = [Item(url=get(r, "url"), title=get(r, "title") or None,
                  added_at=_iso(get(r, date_col)) if date_col else None)
             for r in rows if get(r, "url")]
    return kind, items


class _LinkParser(html.parser.HTMLParser):
    """Collects every <a href> with its text. Netscape bookmark files use
    ADD_DATE; Pocket's ril_export.html uses time_added."""

    def __init__(self):
        super().__init__()
        self.items: list[Item] = []
        self._current: Item | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        if a.get("href"):
            self._current = Item(url=a["href"], added_at=_iso(a.get("add_date") or a.get("time_added")))

    def handle_data(self, data):
        if self._current is not None and data.strip():
            self._current.title = (self._current.title or "") + data.strip()

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            self.items.append(self._current)
            self._current = None


def parse_export(path: str | pathlib.Path) -> tuple[str, list[Item]]:
    """(format name, items). Raises ValueError for a file it can't read as
    any supported export."""
    path = pathlib.Path(path)
    if path.is_dir():
        if list(path.glob("metadata_*.json")):
            return "omnivore", _parse_omnivore(path)
        raise ValueError(f"{path}: a directory import must be an unzipped Omnivore export (metadata_*.json)")
    if zipfile.is_zipfile(path):
        import tempfile
        with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="satchel-import-") as tmp:
            zf.extractall(tmp)
            roots = [p.parent for p in pathlib.Path(tmp).rglob("metadata_*.json")]
            if not roots:
                raise ValueError(f"{path.name}: not an Omnivore export (no metadata_*.json inside)")
            return "omnivore", _parse_omnivore(roots[0])
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _parse_csv(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm") or "<a " in text.lower():
        parser = _LinkParser()
        parser.feed(text)
        kind = "pocket" if "time_added" in text.lower() else "bookmarks"
        return kind, parser.items
    return "urls", [Item(url=line.strip()) for line in text.splitlines()
                    if line.strip().startswith(("http://", "https://"))]


# -- fetching ---------------------------------------------------------------

def wayback_snapshot(url: str, timeout: float = 15.0) -> str | None:
    """The closest archived copy's raw-content URL, or None. The `id_` form
    returns the page as originally archived, without the Wayback toolbar
    that would otherwise end up in the extracted text."""
    # ":" and "/" stay literal: the availability API finds nothing for a
    # fully percent-encoded https%3A%2F%2F... (measured). "?", "&" and "="
    # are still encoded, so a URL's own query string can't leak into the
    # API's.
    api = WAYBACK_API + urllib.parse.quote(url, safe=":/")
    req = urllib.request.Request(api, headers={"User-Agent": "satchel (+https://github.com/MaXiMo000/satchel)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available") or not closest.get("url") or str(closest.get("status")) != "200":
        return None
    ts = closest.get("timestamp")
    snap = closest["url"]
    if ts:
        snap = snap.replace(f"/web/{ts}/", f"/web/{ts}id_/", 1)
    return snap.replace("http://web.archive.org", "https://web.archive.org", 1)


def fetch_article(url: str, *, wayback: bool = True) -> tuple[dict | None, str, str]:
    """(article or None, final url, where it came from: 'live' | 'wayback' | error text)."""
    live_error = "no article text"
    try:
        html_bytes, final_url = fetch(url)
        article = extract(html_bytes, url=final_url)
        if article:
            return article, final_url, "live"
    except Exception as exc:  # noqa: BLE001 - any failure falls through to the archive
        live_error = f"{type(exc).__name__}: {exc}"
        final_url = url
    if wayback:
        try:
            snap = wayback_snapshot(url)
            if snap:
                html_bytes, _ = fetch(snap)
                article = extract(html_bytes, url=url)
                if article:
                    return article, url, "wayback"
        except Exception:  # noqa: BLE001 - the archive being down is just "not found"
            pass
    return None, final_url, live_error


def run_import(conn: sqlite3.Connection, items: list[Item], *, wayback: bool = True,
               workers: int = 8, progress=None) -> dict:
    """Saves every item not already in the db. Returns counts plus the
    failures (url, reason) so nothing is silently dropped."""
    progress = progress or (lambda msg: None)
    have = db.saved_urls(conn)
    counts = {"saved": 0, "from_export": 0, "from_wayback": 0, "skipped": 0, "failed": 0}
    failures: list[tuple[str, str]] = []
    to_fetch: list[Item] = []
    seen: set[str] = set()

    def save(url, title, author, text, added_at) -> None:
        try:
            db.add(conn, url, title, author, text, added_at=added_at)
            have.add(url)
            counts["saved"] += 1
        except sqlite3.IntegrityError:
            counts["skipped"] += 1

    for item in items:
        if not item.url.startswith(("http://", "https://")):
            counts["failed"] += 1
            failures.append((item.url, "not an http(s) URL"))
            continue
        url = normalize_url(item.url)
        if url in have or url in seen:
            counts["skipped"] += 1
            continue
        seen.add(url)
        if item.text:
            save(url, item.title, item.author, item.text, item.added_at)
            counts["from_export"] += 1
        else:
            item.url = url
            to_fetch.append(item)

    # ponytail: plain thread pool, no per-host rate limit; add one if an
    # import ever hammers a single site.
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_article, it.url, wayback=wayback): it for it in to_fetch}
        for done, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            it = futures[fut]
            article, final_url, source = fut.result()
            if article is None:
                counts["failed"] += 1
                failures.append((it.url, source))
                progress(f"[{done}/{len(to_fetch)}] failed  {it.url}")
                continue
            # Keyed on the saved URL, not the post-redirect one, so importing
            # the same export twice recognizes every item the second time.
            save(it.url, article["title"] or it.title, article["author"], article["text"], it.added_at)
            if source == "wayback":
                counts["from_wayback"] += 1
            progress(f"[{done}/{len(to_fetch)}] {source:7} {it.url}")

    counts["failures"] = failures
    return counts
