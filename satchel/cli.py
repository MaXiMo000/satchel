"""satchel add <url> | search <query> | list | read <id>"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from . import db
from .extract import extract
from .fetch import fetch

DEFAULT_DB = "satchel.db"


def _do_add(args) -> int:
    conn = db.connect(args.db)
    try:
        html = fetch(args.url)
    except Exception as exc:  # noqa: BLE001 - a bad fetch is a clear error, not a crash
        print(f"error: could not fetch {args.url}: {exc}", file=sys.stderr)
        return 1

    article = extract(html, url=args.url)
    if article is None:
        print(f"error: could not extract article text from {args.url}", file=sys.stderr)
        return 1

    try:
        article_id = db.add(conn, args.url, article["title"], article["author"], article["text"])
    except sqlite3.IntegrityError:
        print(f"already saved: {args.url}", file=sys.stderr)
        return 1

    print(f"saved #{article_id}: {article['title'] or args.url}")
    return 0


def _do_search(args) -> int:
    conn = db.connect(args.db)
    rows = db.search(conn, args.query)
    if not rows:
        print("no matches")
        return 0
    for row in rows:
        print(f"#{row['id']:<4} {row['title'] or row['url']}  ({row['url']})")
    return 0


def _do_list(args) -> int:
    conn = db.connect(args.db)
    rows = db.list_all(conn)
    if not rows:
        print("nothing saved yet")
        return 0
    for row in rows:
        print(f"#{row['id']:<4} {row['title'] or row['url']}  ({row['added_at']})")
    return 0


def _do_read(args) -> int:
    conn = db.connect(args.db)
    row = db.get(conn, args.id)
    if row is None:
        print(f"error: no article #{args.id}", file=sys.stderr)
        return 1
    print(row["title"] or row["url"])
    if row["author"]:
        print(f"by {row['author']}")
    print()
    print(row["text"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="satchel")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"path to the sqlite db (default: {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="fetch a URL, extract the article, save it")
    add_p.add_argument("url")
    add_p.set_defaults(func=_do_add)

    search_p = sub.add_parser("search", help="full-text search saved articles")
    search_p.add_argument("query")
    search_p.set_defaults(func=_do_search)

    list_p = sub.add_parser("list", help="list everything saved")
    list_p.set_defaults(func=_do_list)

    read_p = sub.add_parser("read", help="print a saved article's full text")
    read_p.add_argument("id", type=int)
    read_p.set_defaults(func=_do_read)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
