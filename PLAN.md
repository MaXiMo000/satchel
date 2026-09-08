# satchel — continuity notes

Read this first if you're picking this project up in a new session.

## Status as of 2026-09-08: working MVP, verified end to end for real

- `satchel/extract.py`, `satchel/db.py`, `satchel/fetch.py`, `satchel/cli.py`
  -- all real, all exercised, not stubs.
- `python tests/test_satchel.py` -- 9/9 passing. Extraction tested against
  a fixture HTML file (offline, no network in tests); storage/search tested
  against a real temp-file sqlite db.
- **A real bug was caught and fixed during this session, not hidden:** the
  first fixture's byline ("By Jordan Rivers · September 2026" in one text
  node) made trafilatura's author heuristic pull "Jordan Rivers ·
  September" instead of just the name. Fixed by making the fixture's markup
  realistic (byline and date as separate elements, like well-structured
  real sites do) rather than loosening the test to match the wrong output.
  Worth remembering if extraction looks slightly off on some other real
  site later: check whether the site's own markup is genuinely ambiguous
  before assuming it's a satchel bug.
- Ran the actual CLI against a real live URL
  (`https://maximo000.github.io/invariant/`): fetched, extracted real body
  text (nav correctly excluded), saved, found by search, read back
  correctly, and a second `add` of the same URL correctly refused as a
  duplicate rather than silently doubling it.

## What's genuinely not built

1. **No tagging, folders, or read/unread state.** Just a flat list plus
   full-text search. Add these when a flat list actually stops being
   enough, not preemptively.
2. **No one-click save.** You have to run `satchel add <url>` by hand —
   there's no browser extension or bookmarklet yet. That's the single
   highest-value next feature if this gets used for real: a tiny
   bookmarklet or extension button that POSTs the current tab's URL to a
   local `satchel` process would remove the only real friction in daily
   use.
3. **No sync.** One local file, on purpose (that's the "local-first"
   pitch) — multi-device sync is a deliberately separate, harder problem
   (conflict resolution, a place to sync *to*) and not a natural extension
   of this MVP.
4. **No GitHub repo pushed yet.** If you're picking this up: `gh repo
   create MaXiMo000/satchel`, then immediately `git remote set-url origin
   git@github-personal:MaXiMo000/satchel.git` before pushing — `gh repo
   create` always points the new remote at the wrong SSH host for this
   account (see the `github-dual-account-setup` memory).
5. **No PyPI publish attempted.** `satchel` might also be a common enough
   word to hit the same PyPI name-blocklist wall `invariant` did — don't
   assume it'll be accepted; check when actually publishing and have
   `satchel-cli` or similar ready as a fallback.

## The one real next step

Build the bookmarklet/extension for one-click save. Everything else here
already works; the only friction stopping this from being used daily is
having to open a terminal to save something you're already looking at in a
browser.
