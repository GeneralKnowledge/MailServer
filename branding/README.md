# Branding

One place for the service's name, colours and logos. `scripts/apply-branding.sh`
copies them to both the invite website and SOGo webmail.

| File | Used for |
|---|---|
| `brand.env` | Name and colours |
| `logo.svg` | Square icon: invite site header and favicon, SOGo compact logo |
| `logo-full.svg` | Wide logo with the name: SOGo login page and sidebar |

Current brand (2026-11-14): **Avedeus Mail**. Plum `#4b2a63` with antique gold `#b8862b`; the mark is an "A" whose crossbar is an envelope fold.

## What gets changed

- **Invite website:** `registration/app/static/brand.css`, `logo.svg`, `favicon.svg`.
  Rebuild the container afterwards (`docker compose up -d --build` in
  `infrastructure/registration`).
- **SOGo webmail** (in the mailcow directory, files mailcow keeps out of git, so
  updates don't overwrite them):
  - `data/conf/sogo/custom-theme.js`: colour palette
  - `data/conf/sogo/custom-fulllogo.svg`, `custom-shortlogo.svg`: logos
  Then `docker compose restart memcached-mailcow sogo-mailcow`.
- **mailcow admin/login page:** set by hand in the mailcow UI under
  *System → Configuration → Options → Customize*: upload `logo-full.svg` as the
  main logo, and set the page title and login text to the brand name.
