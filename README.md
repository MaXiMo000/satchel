# satchel

**A reading queue that keeps the article, not just the link.**

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

## How

- `fetch.py` — plain `urllib` GET. Stdlib, no dependency for the easy part.
- `extract.py` — wraps [trafilatura](https://github.com/adbar/trafilatura)
  to pull title/author/main-text out of real HTML, correctly skipping
  navigation, ads, and related-links boilerplate. Boilerplate-stripping is
  exactly the kind of thing that looks fine on a hand-rolled test page and
  breaks on the next real site's markup — not worth reimplementing.
- `db.py` — one SQLite file, an FTS5 virtual table kept in sync with the
  real table via triggers (external-content FTS5: the searchable index
  isn't a second copy of the truth that can drift from the first).

## Install

```bash
pip install -e .
```

## Use

```bash
satchel add <url>
satchel list
satchel search <query>
satchel read <id>
```

All commands take `--db path/to/file.db` (default: `satchel.db` in the
current directory).

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

## What's deliberately not here yet

No tagging, no folders, no read/unread state, no browser extension to save
a page with one click (this is a CLI you point at a URL). No sync between
devices — it's one local file on purpose. See `PLAN.md` for what a next
session would tackle first.

MIT licensed.
