"""Local capture listener and reader: click a bookmarklet on any page, and
its URL goes through the same fetch/extract/store pipeline the CLI uses;
open the printed link, and the archive reads and searches in a browser.

Loopback-only and token-gated. Why the token matters: while `serve` is
running, *any* tab open in the browser can send a request to
http://127.0.0.1:<port> -- a browser's same-origin policy stops a page
from reading a cross-origin response it didn't get permission for, but it
does not stop the page from sending the request in the first place. With
no shared secret, that's an open invitation for any site you happen to
have open to make this process fetch (and store) an arbitrary URL on your
behalf. The token is generated fresh each time `serve` starts and only
ever appears in the bookmarklet printed below -- it isn't the request
itself that's protected, it's that a request without the right token gets
nothing.
"""
from __future__ import annotations

import html
import http.server
import json
import sqlite3
import secrets
import sys
import urllib.parse

from . import db
from .capture import add_article

DEFAULT_PORT = 8765


def _bookmarklet(port: int, token: str) -> str:
    return (
        "javascript:fetch('http://127.0.0.1:%d/add?token=%s&url='"
        "+encodeURIComponent(location.href))"
        ".then(r=>r.json()).then(j=>alert(j.message))"
        ".catch(e=>alert('satchel: could not reach the local listener -- is `satchel serve` still running?'))"
    ) % (port, urllib.parse.quote(token))


COOKIE = "satchel"

# Text-only pages: no script runs, nothing loads from anywhere, and no other
# site can frame them.
_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_STYLE = """
:root{color-scheme:light dark;--bg:#faf8f3;--ink:#1d1c1a;--muted:#6b6862;--rule:#dedad0;--link:#1f4e79}
@media (prefers-color-scheme:dark){:root{--bg:#191917;--ink:#e8e4da;--muted:#9a968c;--rule:#33322e;--link:#8fb8e0}}
body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.65 Georgia,'Times New Roman',serif}
main{max-width:38rem;margin:0 auto;padding:2.5rem 1rem 5rem}
a{color:var(--link)}
.top{font:600 14px/1 system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-decoration:none}
form{display:flex;gap:.5rem;margin:1.5rem 0}
input{flex:1;min-width:0;font:16px system-ui,sans-serif;padding:.55rem .7rem;border:1px solid var(--rule);border-radius:6px;background:transparent;color:inherit}
button{font:600 15px system-ui,sans-serif;padding:.55rem 1rem;border:0;border-radius:6px;background:var(--ink);color:var(--bg);cursor:pointer}
ul{list-style:none;padding:0;margin:0}
li{padding:.8rem 0;border-bottom:1px solid var(--rule)}
li a{text-decoration:none;font-size:1.05rem}
.meta{display:block;font:13px/1.4 system-ui,sans-serif;color:var(--muted);margin-top:.2rem;overflow-wrap:anywhere}
h1{font-size:2rem;line-height:1.2;margin:1.5rem 0 .5rem}
article p{margin:0 0 1.1em}
.note{font:15px system-ui,sans-serif;color:var(--muted)}
"""


def _page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
            f"<body><main>{body}</main></body></html>").encode("utf-8")


def _when(added_at: str | None) -> str:
    return (added_at or "")[:10]


def _list_page(rows, query: str, note: str = "") -> bytes:
    items = "".join(
        f"<li><a href='/read/{r['id']}'>{html.escape(r['title'] or r['url'])}</a>"
        f"<span class=meta>{html.escape(r['author'] or '')}"
        f"{' · ' if r['author'] else ''}{_when(r['added_at'])} · {html.escape(r['url'])}</span></li>"
        for r in rows)
    count = f"{len(rows)} match{'es' if len(rows) != 1 else ''}" if query else f"{len(rows)} saved"
    return _page("satchel", (
        "<a class=top href='/'>satchel</a>"
        "<form action='/' method=get><input name=q type=search aria-label='Search your archive' "
        f"placeholder='Search everything you saved' value='{html.escape(query, quote=True)}'>"
        "<button>Search</button></form>"
        f"<p class=note>{html.escape(note) or count}</p><ul>{items}</ul>"))


def _article_page(row) -> bytes:
    paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in row["text"].splitlines() if line.strip())
    byline = " · ".join(html.escape(x) for x in (row["author"], _when(row["added_at"])) if x)
    return _page(row["title"] or row["url"], (
        "<a class=top href='/'>&larr; satchel</a>"
        f"<h1>{html.escape(row['title'] or row['url'])}</h1>"
        f"<p class=meta>{byline}<br><a href='{html.escape(row['url'], quote=True)}' rel=noreferrer>"
        f"{html.escape(row['url'])}</a></p><article>{paragraphs}</article>"))


def _make_handler(db_path: str, token: str) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib's name
            pass  # quiet by default; every response already carries the result

        def _reply(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            # The token is the real access control here, not CORS -- once a
            # request has the right token it's allowed to succeed, so there's
            # nothing extra protected by hiding the response from whatever
            # page's bookmarklet sent it.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, status: int, body: bytes, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for name, value in {**_HEADERS, **(extra or {})}.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reader_allowed(self, qs: dict) -> bool | None:
            """True to serve, False to refuse, None when a redirect was sent.

            The Host check is what stops DNS rebinding: a site that points
            its own name at 127.0.0.1 can make the browser connect here, but
            the request still names that site as its Host. The token, given
            once in the printed link and kept as a cookie, is what stops
            other local users and anything else that finds the port.
            """
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return False
            given = qs.get("token", [""])[0]
            if given and secrets.compare_digest(given, token):
                self._html(303, b"", {
                    "Location": urllib.parse.urlsplit(self.path).path,
                    "Set-Cookie": f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/",
                })
                return None
            jar = self.headers.get("Cookie", "")
            held = dict(c.strip().split("=", 1) for c in jar.split(";") if "=" in c).get(COOKIE, "")
            return bool(held) and secrets.compare_digest(held, token)

        def _read(self, parsed) -> None:
            qs = urllib.parse.parse_qs(parsed.query)
            allowed = self._reader_allowed(qs)
            if allowed is None:
                return
            if not allowed:
                self._html(403, _page("satchel", "<p class=note>Open the link <code>satchel serve</code> "
                                                  "printed when it started.</p>"))
                return
            conn = db.connect(db_path)
            try:
                if parsed.path == "/":
                    query = qs.get("q", [""])[0].strip()
                    note = ""
                    try:
                        rows = db.search(conn, query) if query else db.list_all(conn)
                    except sqlite3.OperationalError:
                        rows = []
                        note = ("That search could not be read: quotes must be balanced, "
                                "and AND, OR, NOT and * are search operators.")
                    self._html(200, _list_page(rows, query, note))
                    return
                row = db.get(conn, int(parsed.path.removeprefix("/read/")))
            finally:
                conn.close()
            if row is None:
                self._html(404, _page("satchel", "<p class=note>Nothing saved under that number.</p>"))
            else:
                self._html(200, _article_page(row))

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/" or (parsed.path.startswith("/read/")
                                      and parsed.path.removeprefix("/read/").isdigit()):
                self._read(parsed)
                return
            if parsed.path != "/add":
                self._reply(404, {"ok": False, "message": "not found"})
                return

            qs = urllib.parse.parse_qs(parsed.query)
            given_token = qs.get("token", [""])[0]
            if not secrets.compare_digest(given_token, token):
                self._reply(403, {"ok": False, "message": "bad or missing token"})
                return

            url = qs.get("url", [""])[0]
            if not url:
                self._reply(400, {"ok": False, "message": "no url given"})
                return

            conn = db.connect(db_path)
            try:
                result = add_article(conn, url, restrict_private_network=True)
            finally:
                conn.close()
            self._reply(200 if result["ok"] else 400, result)

    return Handler


class _StrictPortServer(http.server.HTTPServer):
    """HTTPServer, but refuse to silently share a port that's already bound.

    Stdlib's HTTPServer sets allow_reuse_address = 1 (SO_REUSEADDR) to let a
    restarted server reclaim a socket still in TIME_WAIT on POSIX. Windows
    gives SO_REUSEADDR much looser semantics: it lets a *second* listener
    bind to a port an existing process is actively listening on, rather than
    raising -- so two `satchel serve` runs on the same port would silently
    both come up on Windows instead of the second one failing loud the way
    it does everywhere else. Not worth the TIME_WAIT convenience for a
    personal tool restarted rarely.
    """

    allow_reuse_address = False


def serve(db_path: str, port: int = DEFAULT_PORT) -> int:
    token = secrets.token_urlsafe(16)
    try:
        httpd = _StrictPortServer(("127.0.0.1", port), _make_handler(db_path, token))
    except OSError as exc:
        print(f"error: could not listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1

    print(f"satchel capture listening on http://127.0.0.1:{port}  (db: {db_path})")
    print()
    print("Drag this to your bookmarks bar, then click it on any page to save it:")
    print()
    print(f"  {_bookmarklet(port, token)}")
    print()
    print("Read and search everything you saved:")
    print()
    print(f"  http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}")
    print()
    print("Only this machine can reach it, and only with the token above -- restart")
    print("`serve` and re-drag the bookmarklet if you ever want a fresh one.")
    print()
    print("^C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0
