"""Local capture listener: click a bookmarklet on any page, and its URL
goes through the same fetch/extract/store pipeline the CLI uses -- no
terminal required for the one action that happens most often.

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

import http.server
import json
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

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
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
