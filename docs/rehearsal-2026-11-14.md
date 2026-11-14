# Local rehearsal, 2026-11-14

A real mailcow (master at `84828d9`, 2026-11-05; SOGo 5.12.13) was run in a
throwaway x86 cloud workspace as `mail.avedeus.ovh`, to test this repo before
the real server exists. No real server or DNS was touched. Not covered:
Let's Encrypt, delivery to or from the internet, Oracle networking, and ARM64.

## What was tested

| Area | Result |
|---|---|
| mailcow install following `docs/deployment.md` (generate_config, pull, up) | Works. 18 containers up (ClamAV skipped to save time). |
| Invite site behind mailcow nginx at `/join/` (`site.registration.custom`) | Works: `/join/`, `/join/admin`, static files, health check. |
| Admin login (password + TOTP), creating an invitation | Works. |
| Sign-up creating a mailbox through the real mailcow API | Works, with TLS verification on (SNI `mail.avedeus.ovh` to `nginx-mailcow`, certificate from a local test CA). Name, 2 GB quota and TLS-enforce flags set as intended. |
| New account: SMTP 465 login, send, IMAP 993 login, message delivered | Works. |
| Username check and sign-up refusing a taken mailbox, an existing alias, a reserved name; an invitation can't be used twice | Works. |
| SOGo theme, light and dark | Didn't apply. Fixed (below). |
| `backup.sh` to an rclone crypt remote, then wipe all mailcow and registration volumes, `restore.sh fetch / mailcow / registration` | Worked after three fixes (below). Mailboxes, alias, messages, invitations and login-page branding came back; a new sign-up worked afterwards. |
| `health-check.sh` | All checks pass. |
| Registration unit tests | 36 passed. |

Screenshots: `docs/images/rehearsal-login-light-dark.png`,
`docs/images/rehearsal-sogo-light-dark.png`.

## Found and fixed

1. **SOGo ignored our colours.** SOGo 5.12 links a precompiled
   `css/theme-default.css` (its blue-green) that outranks the palette from
   `custom-theme.js`. Fix: `infrastructure/mailcow/configuration/docker-compose.override.yml`
   mounts an empty file over it (written by `apply-branding.sh`).
2. **People sign in on mailcow's page, not SOGo's.** Current mailcow sends
   `/SOGo` to its own login page, which still showed the cow and green buttons.
   `apply-branding.sh` now sets the logo (light and dark), page names and a
   plum stylesheet (`data/web/css/build/0081-custom-mailcow.css`).
3. **Dark mode didn't match across pages.** The login page has its own switch
   (`mailcow_theme`). SOGo now reads it first, and the invite site's button
   keeps it in step.
4. **SOGo dark palette left light panes** (sidebar and reading pane use light
   hues of the background palette) and plum text on black. Palette and two
   CSS touch-ups adjusted.
5. **Branding doc said mailcow updates leave the SOGo files alone.** They are
   tracked in mailcow's git and `update.sh` merges with "theirs". The docs now
   say to re-run `apply-branding.sh` after each update.
6. **mailcow restore refused to run** on a fresh install:
   `generate_config.sh` leaves `DOCKER_COMPOSE_VERSION` empty until the first
   `update.sh`. `backup.sh` and `restore.sh` now fill it in.
7. **mailcow's restore restarts mailcow without the override file.**
   `restore.sh mailcow` runs `docker compose up -d` afterwards.
8. **`restore.sh registration` failed after a full loss** (it ran `start` on a
   container that no longer existed) and depended on loose file permissions.
   It now uses `up -d` and streams the database in, so the restored files can
   stay root-only (`fetch` now makes them so).
9. **Backup upload check:** `rclone check` can't compare hashes on a crypt
   remote and logged an ERROR line every night. Uses `rclone cryptcheck` for
   crypt remotes.
10. Backups now also keep `docker-compose.override.yml` and the login-page
    stylesheet.

## Workspace-only workarounds (not needed on the server)

This workspace has no IPv6 in the kernel, no outbound DNS, and can't raise
the process-count limit, so these were changed locally and are **not** in the
repo: php-fpm and Dovecot listening on IPv4 only, the unbound health check
skipped, Dovecot's `nproc` ulimit removed, the IPv6 sysctl dropped from
mailcow's backup helper, and the invite app trusting the local test CA via
`SSL_CERT_FILE`. On Oracle's Ubuntu image none of this applies; if the VM has
no IPv6, set `ENABLE_IPV6=false` as `docs/deployment.md` says.

## Worth knowing for the real install

- Pre-setting `API_KEY` and `API_ALLOW_FROM=172.22.1.240` in `mailcow.conf`
  works as an alternative to creating the key in the UI.
- The app's TLS verification needs mailcow's real certificate. Until ACME has
  issued it, sign-ups fail with "Something went wrong" (logged as
  `CERTIFICATE_VERIFY_FAILED`); the invitation is not used up.
