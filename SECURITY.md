# Security

## Reporting a problem

Report vulnerabilities privately through GitHub: **Security -> Report a
vulnerability** on this repository. Please do not open a public issue for
anything exploitable. You will get an acknowledgement within 72 hours.

## What satchel handles

- **Fetching is guarded against SSRF.** Addresses that resolve to private,
  loopback, link-local or reserved ranges are refused before a request is
  made.
- **`satchel serve` listens on localhost only**, for the bookmarklet and the
  reader.
- **Your library stays local**: one SQLite file, no account, no telemetry.

## Supply chain

- GitHub Actions are pinned to full commit SHAs.
- Releases publish to PyPI through trusted publishing (OIDC); no long-lived
  token is stored in the repository.
