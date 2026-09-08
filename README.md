# satchel

**A tiny local archive of the actual things you read.**

[![ci](https://github.com/MaXiMo000/satchel/actions/workflows/ci.yml/badge.svg)](https://github.com/MaXiMo000/satchel/actions/workflows/ci.yml)

Most "read later" tools save a URL. The link rots, the site adds a
paywall, or you're offline — and the thing you saved is gone. `satchel`
fetches the page once, extracts the actual article text (not the nav, not
the related-links box, not the footer), and stores it in one local SQLite
file with real full-text search. No server, no account, no network needed
to search what you've already saved.

```
$ satchel add https://example.com/some-article
saved #4: Why Local-First Software Is Worth the Extra Effort

$ satchel search "FTS5"
#4    Why Local-First Software Is Worth the Extra Effort  (https://example.com/some-article)

$ satchel read 4
Why Local-First Software Is Worth the Extra Effort
by Jordan Rivers

Most reading tools save a link and call it done...
```

Save with one click instead of a terminal: `satchel serve` runs a local
listener and prints a bookmarklet — click it on any page and that page is
saved through the exact same pipeline `add` uses. See "Capture" below.

## How

- `fetch.py` — plain `urllib` GET. Stdlib, no dependency for the easy
  part. Always rejects non-http(s) schemes; a URL that didn't come from
  the person running satchel (see "Capture") is additionally checked
  against loopback/private/link-local address space before and after
  every redirect hop, not just the first one.
- `capture.py` — fetch → extract → store, in one place. Both `add` and the
  capture listener call this; there is exactly one add pipeline.
- `extract.py` — wraps [trafilatura](https://github.com/adbar/trafilatura)
  to pull title/author/main-text out of real HTML, correctly skipping
  navigation, ads, and related-links boilerplate. Boilerplate-stripping is
  exactly the kind of thing that looks fine on a hand-rolled test page and
  breaks on the next real site's markup — not worth reimplementing.
- `db.py` — one SQLite file, an FTS5 virtual table kept in sync with the
  real table via triggers (external-content FTS5: the searchable index
  isn't a second copy of the truth that can drift from the first).
- `serve.py` — the local capture listener (below).

## Install

```bash
pip install -e .
```

## Use

```bash
satchel add <url>          # fetch, extract, save
satchel list                # everything saved
satchel search <query>      # full-text search
satchel read <id>           # print an article's full text
satchel serve                # one-click capture -- see below
```

All commands take `--db path/to/file.db`. The default, if you don't pass
one, is `~/.local/share/satchel/satchel.db` (respecting `XDG_DATA_HOME`) —
one stable location regardless of which directory you happen to run the
command from, not `./satchel.db` in the current directory. Run
`satchel --help` to see the exact resolved path on your machine.

Saving the same article twice — via a tracking link, a shortener, or a
plain `http://` URL the site itself upgrades to `https://` — is one
duplicate, not two. `add` normalizes the URL it's given, follows
redirects, and normalizes the *actual* address the server served before
checking for a duplicate: it defers to what the server says, rather than
guessing at a scheme policy.

## Capture

```bash
$ satchel serve
satchel capture listening on http://127.0.0.1:8765  (db: ~/.local/share/satchel/satchel.db)

Drag this to your bookmarks bar, then click it on any page to save it:

  javascript:fetch('http://127.0.0.1:8765/add?token=...&url='+encodeURIComponent(location.href))...

^C to stop.
```

Drag the printed link to your bookmarks bar. Click it on any page while
`serve` is running, and that page goes through the same fetch/extract/save
pipeline as `satchel add` — no terminal required for the thing you do most
often.

**This changes the threat model, and it's handled, not ignored.** While
`serve` is running, any tab open in your browser can send it a request —
a browser's same-origin policy stops a page from *reading* a
cross-origin response it wasn't granted, but not from *sending* the
request in the first place. Without a shared secret, that would be an
open invitation for any open tab to make satchel fetch an arbitrary URL.
So: the listener only binds to `127.0.0.1`, a fresh token is generated
every time you run `serve` and only ever appears in the bookmarklet you
just dragged, and every captured URL is checked against loopback,
private (RFC1918), link-local (this is what closes off
`169.254.169.254`-style cloud metadata endpoints), reserved, and
multicast address space — before the first request, and again on every
redirect hop, since checking only the first hop would let a URL redirect
somewhere private after passing the initial check.

What that guard does *not* claim: it validates a hostname's DNS answer at
the moment it's checked, it doesn't pin the connection to that exact
resolved address. A DNS-rebinding attacker with a fast-expiring record
could in principle still slip a private address past the check and have
the actual connection re-resolve to it. Closing that fully means
connecting to a pinned IP rather than by hostname — real added complexity
for a personal tool's local listener. This is a deliberate, documented
partial mitigation, not a claim that it's unbreakable.

Direct `satchel add <url>` from your own terminal is **not** restricted
this way — typing your own local dev server's URL to save a draft you're
writing is a legitimate thing to do, and you are not a threat to
yourself.

## Test

```bash
python tests/test_satchel.py
```

Extraction is tested against a fixture HTML file (`tests/fixtures/`), not
the network — a real bug was caught this way during development: an
ambiguous byline (`"By Jordan Rivers · September 2026"` in one text node)
made trafilatura fold part of the date into the author field. Fixed by
making the fixture look like well-structured real markup (byline and date
as separate elements) rather than loosening the assertion.

The capture listener is tested the same way it's actually used: a real
`http.server.HTTPServer` runs in a background thread and gets real HTTP
requests, including SSRF attempts against loopback and link-local
addresses — checked with the *correct* token, since the interesting
question is whether the guard holds once someone's past the door, not
whether the door itself works.

## What's deliberately not here yet

No tagging, no folders, no read/unread state — a flat list plus full-text
search covers the actual workflow (encounter → capture → search → read);
add these when a flat list genuinely stops being enough, not before. No
multi-device sync — one local file is the whole pitch, and sync is a
separate, harder problem this project isn't trying to solve. No AI
summarization, no embeddings, no recommendation engine: this is an
archive of what you actually read, not a platform.

MIT licensed.
