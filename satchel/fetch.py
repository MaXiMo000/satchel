"""Fetch a URL's HTML. stdlib only -- a plain GET is not worth a dependency
when extraction (the actually hard part) already needs one.
"""
from __future__ import annotations

import urllib.request

_USER_AGENT = "satchel/0.1 (personal reading queue; +https://github.com/MaXiMo000/satchel)"


def fetch(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")
