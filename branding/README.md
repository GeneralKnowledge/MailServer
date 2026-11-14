# Branding

One place for the service's name, colours and logos. `scripts/apply-branding.sh`
copies them to both the invite website and SOGo webmail.

| File | Used for |
|---|---|
| `brand.env` | Name and colours |
| `logo.svg` | Square icon: invite site header and favicon, SOGo compact logo |
| `logo-full.svg` | Wide logo with the name: mailcow login page, SOGo |
| `logo-full-dark.svg` | Same, with light lettering for dark mode (mailcow login page) |

Current brand (2026-11-14): **Avedeus Mail**. Plum `#4b2a63` with antique gold `#b8862b`; the mark is an "A" whose crossbar is an envelope fold.

## What gets changed

- **Invite website:** `registration/app/static/brand.css`, `logo.svg`, `favicon.svg`.
  Rebuild the container afterwards (`docker compose up -d --build` in
  `infrastructure/registration`).
- **SOGo webmail** (in the mailcow directory):
  - `data/conf/sogo/custom-theme.js`: colour palette and dark mode
  - `data/conf/sogo/custom-fulllogo.svg`, `custom-shortlogo.svg`: logos
  - `data/conf/sogo/custom-theme-default.css`: an empty file. SOGo links its
    own precompiled theme (blue-green), which outranks `custom-theme.js`;
    `infrastructure/mailcow/configuration/docker-compose.override.yml` mounts
    this empty file over it. Without the override SOGo keeps its own colours
    and the script prints a warning.
  These files are listed in mailcow's `.gitignore` but are still tracked in its
  git, so `update.sh` can replace them when upstream changes them. Re-run the
  script after every mailcow update.
  The script restarts `memcached-mailcow` and `sogo-mailcow`.
- **mailcow login page** (`https://mail.<domain>/`, and where `/SOGo` sends
  people to sign in) and admin UI:
  - logos and page names in Redis (`MAIN_LOGO`, `MAIN_LOGO_DARK`,
    `TITLE_NAME`, `MAIN_NAME`), the same keys as *System → Configuration →
    Options → Customize*. They are part of mailcow's Redis backup.
  - `data/web/css/build/0081-custom-mailcow.css`: plum buttons and accent
    (mailcow's own git-ignored hook for custom CSS). A copy is generated in
    `infrastructure/mailcow/configuration/web/`.

## Dark mode

All three pages are on the same origin and share the choice through
`localStorage`:

- **mailcow login page:** its moon switch stores `mailcow_theme`
  (`dark`/`light`); with none set it follows the device.
- **Invite website:** follows the device by default; the *Theme* button
  cycles auto → dark → light. It stores `mail-theme` and also sets
  `mailcow_theme` (auto clears it), so the login page and SOGo follow.
- **SOGo webmail:** `custom-theme.js` uses `mailcow_theme`, else `mail-theme`,
  else the device setting. In dark mode it switches to a plum-black palette.
  The open message stays on a light card, because mail bodies are written
  for a light background.

Checked in a running mailcow on 2026-11-14 (see
`docs/rehearsal-2026-11-14.md`).
