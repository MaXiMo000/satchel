# Changelog

## Unreleased

- Tags: imports bring over Pocket tags (CSV and `ril_export.html`), Omnivore labels and Firefox bookmark tags; re-running an import adds them to articles already saved. `satchel add --tag`, `satchel list --tag`, and tags in `list` and `read`.

## 0.5.0 - 2026-11-18

- A browser extension for Chrome and Firefox: one click saves the page you are reading, from its own HTML, including pages behind your login.
- `satchel serve --keep-token` keeps one token across restarts, so the extension stays paired.
- `POST /add` on the capture listener, used by the extension.

## 0.4.1 - 2026-10-16

- Documentation: a demo of real output at the top of the README, a security policy, a changelog, and issue templates.

## 0.4.0 - 2026-10-15

- A reader in the browser.
- Markdown and EPUB export.

## 0.3.0 - 2026-09-27

- OS certificate checks.
- An archived copy is used when a site refuses the fetch.
- UTF-8 output on every platform.

## 0.2.0 - 2026-09-26

- `satchel import` for Pocket, Omnivore, Instapaper, bookmarks and URL lists, with Wayback rescue for dead links.

## 0.1.1 - 2026-09-26

- Windows fixes.

## 0.1.0 - 2026-09-09

- First release: fetch, extract, store and full-text search, plus one-click capture.
