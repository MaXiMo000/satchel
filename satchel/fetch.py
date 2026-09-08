"""Fetch a URL's HTML. stdlib only -- a plain GET is not worth a dependency
when extraction (the actually hard part) already needs one.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_USER_AGENT = "satchel/0.1 (personal reading queue; +https://github.com/MaXiMo000/satchel)"

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """A URL failed safety validation before satchel would fetch it: an
    unsupported scheme, or -- when restrict_private_network=True -- an
    address that isn't reachable on the public internet."""


def _check_scheme(url: str) -> None:
    scheme = urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"unsupported URL scheme {scheme!r} (only http/https)")


def _check_not_private(url: str) -> None:
    """Refuses a URL whose host resolves to loopback, private, link-local,
    reserved, multicast, or unspecified address space -- the exact ranges
    an SSRF payload targets (localhost services, 169.254.169.254-style
    cloud metadata endpoints, other hosts on the local network).

    This validates the hostname's *current* DNS answer, at the moment it's
    checked -- it does not pin the connection to that exact resolved
    address. A sufficiently motivated attacker who controls DNS for a
    domain (a near-zero TTL, answering differently a moment later) could
    still slip a private address past this check and have urllib re-
    resolve to it at actual connect time -- classic DNS rebinding. Fully
    closing that needs connecting to the pinned IP directly rather than by
    hostname, which is real added complexity for a personal tool's local
    capture endpoint; this is a deliberate, documented partial mitigation,
    not a claim of a hard guarantee against a determined network attacker.
    """
    host = urlsplit(url).hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve {host}: {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            raise UnsafeURLError(f"{host} resolves to a non-public address ({addr}) -- refusing")


class _RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect target before following it. Without
    this, a URL that passes validation up front could still redirect
    somewhere private, and urllib would follow it anyway -- the initial
    check alone only guards the first hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_scheme(newurl)
        _check_not_private(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

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


def fetch(url: str, timeout: float = 15.0, restrict_private_network: bool = False) -> tuple[bytes, str]:
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

    Only http/https are ever accepted, always. restrict_private_network
    additionally refuses a URL (or a redirect to one) that resolves to
    loopback/private/link-local/reserved address space -- pass True when
    the URL didn't come from the person running satchel (see serve.py).
    """
    _check_scheme(url)
    if restrict_private_network:
        _check_not_private(url)
        opener = urllib.request.build_opener(_RestrictedRedirectHandler)
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl()
