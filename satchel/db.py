"""SQLite storage with full-text search (FTS5), local-first: one file, no
server, no network dependency to search what you've already saved.

The FTS5 table is external-content (`content='articles'`): the searchable
copy of the text isn't duplicated as the source of truth, and triggers keep
it in sync on insert/update/delete so the two can't drift apart.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import time


def default_db_path() -> str:
    """Where satchel.db lives if --db isn't given: one stable, per-user
    location instead of "whatever directory you happened to run the
    command from" -- the latter means `satchel add` from ~/Downloads and
    `satchel list` from ~ silently look at two different, disconnected
    databases, which is indistinguishable from data loss to a new user.
    Respects XDG_DATA_HOME; falls back to the XDG default location.
    """
    data_home = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
    return str(pathlib.Path(data_home) / "satchel" / "satchel.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    author TEXT,
    text TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, author, text, content='articles', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, author, text)
    VALUES (new.id, new.title, new.author, new.text);
END;

CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, author, text)
    VALUES ('delete', old.id, old.title, old.author, old.text);
END;

-- Its own table, so a database made before tags existed gains it on open.
CREATE TABLE IF NOT EXISTS tags (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (article_id, tag)
);

CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, author, text)
    VALUES ('delete', old.id, old.title, old.author, old.text);
    INSERT INTO articles_fts(rowid, title, author, text)
    VALUES (new.id, new.title, new.author, new.text);
END;
"""


def connect(path: str) -> sqlite3.Connection:
    parent = pathlib.Path(path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # a deleted article takes its tags with it
    conn.executescript(SCHEMA)
    return conn


def add(conn: sqlite3.Connection, url: str, title: str | None, author: str | None, text: str,
        added_at: str | None = None) -> int:
    """Returns the article's id. Raises sqlite3.IntegrityError if the url
    is already saved -- the caller decides what "already have this" means
    to them (skip, re-fetch, update), this layer doesn't guess.

    added_at defaults to now; an import passes the date the article was
    originally saved elsewhere, so a 2016 Pocket save doesn't read as new."""
    cur = conn.execute(
        "INSERT INTO articles (url, title, author, text, added_at) VALUES (?, ?, ?, ?, ?)",
        (url, title, author, text, added_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    conn.commit()
    return cur.lastrowid


def clean_tags(tags) -> list[str]:
    """Lowercased, trimmed, deduplicated, order kept: "Python" and "python "
    are one tag, the way every read-later service treated them."""
    seen: list[str] = []
    for tag in tags or ():
        tag = str(tag).strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def add_tags(conn: sqlite3.Connection, article_id: int, tags) -> None:
    conn.executemany("INSERT OR IGNORE INTO tags (article_id, tag) VALUES (?, ?)",
                     [(article_id, t) for t in clean_tags(tags)])
    conn.commit()


def add_tags_by_url(conn: sqlite3.Connection, url: str, tags) -> None:
    row = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
    if row is not None:
        add_tags(conn, row[0], tags)


def tags_for(conn: sqlite3.Connection, article_id: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT tag FROM tags WHERE article_id = ? ORDER BY tag", (article_id,))]


def saved_urls(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT url FROM articles")}


def get(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()


def list_all(conn: sqlite3.Connection, tag: str | None = None) -> list[sqlite3.Row]:
    """Newest first, with each article's tags; only those tagged `tag` if given."""
    wanted = clean_tags([tag])[0] if tag and clean_tags([tag]) else None
    return conn.execute(
        "SELECT id, url, title, author, added_at, "
        "(SELECT group_concat(tag, ', ') FROM "
        " (SELECT tag FROM tags WHERE article_id = articles.id ORDER BY tag)) AS tags "
        "FROM articles WHERE ? IS NULL OR id IN (SELECT article_id FROM tags WHERE tag = ?) "
        "ORDER BY added_at DESC", (wanted, wanted)).fetchall()


def full_articles(conn: sqlite3.Connection, query: str | None = None) -> list[sqlite3.Row]:
    """Every column, text included, newest first; only search matches when
    a query is given."""
    if not query:
        return conn.execute("SELECT * FROM articles ORDER BY added_at DESC").fetchall()
    ids = [row["id"] for row in search(conn, query)]
    rows = {row["id"]: row for row in conn.execute(
        f"SELECT * FROM articles WHERE id IN ({','.join('?' * len(ids))})", ids)}  # noqa: S608 - placeholders only if ids else {}
    return [rows[i] for i in ids]


def search(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT articles.id, articles.url, articles.title, articles.author, articles.added_at
        FROM articles_fts
        JOIN articles ON articles.id = articles_fts.rowid
        WHERE articles_fts MATCH ?
        ORDER BY rank
        """,
        (query,),
    ).fetchall()
