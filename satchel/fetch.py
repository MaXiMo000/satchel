"""Fetch a URL's HTML. stdlib only -- a plain GET is not worth a dependency
when extraction (the actually hard part) already needs one.
"""
from __future__ import annotations

import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_USER_AGENT = "satchel/0.1 (personal reading queue; +https://github.com/MaXiMo000/satchel)"

# The same article shared twice almost always differs only in tracking junk
# or a trailing slash -- strip that before it's used as the dedup key, or
# the one duplicate guard this tool has never actually fires in practice.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "source",
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k not in _TRACKING_PARAMS])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def fetch(url: str, timeout: float = 15.0) -> tuple[bytes, str]:
    """Returns (raw bytes, the final URL after any redirects).

    Raw bytes, not a decoded string -- trafilatura does its own charset
    detection (via cchardet) and does it better than trusting the HTTP
    header alone, which many sites omit or get wrong.

    The final URL matters for dedup: a shortened link (bit.ly, t.co) or a
    plain http:// URL a site 301-redirects to https:// both resolve to the
    same real address the server actually served, and *that* address --
    not whatever the user happened to type or click -- is what two saves
    of "the same" article should be compared against. This is also the
    right way to answer "should http:// and https:// count as the same
    URL": defer to what the server says by following the redirect, rather
    than guessing.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl()
