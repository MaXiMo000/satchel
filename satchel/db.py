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


def saved_urls(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT url FROM articles")}


def get(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()


def list_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, url, title, author, added_at FROM articles ORDER BY added_at DESC").fetchall()


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
