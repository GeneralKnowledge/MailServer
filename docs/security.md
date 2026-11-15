# Security

| Area | Measure |
|---|---|
| Network | OCI security list allows only 22, 25, 80, 443, 465, 587, 993 (+4190). Port 22 restricted to the admin's IP where possible. Plain IMAP 143 and POP3 110/995 bound to localhost by `scripts/harden-mailcow-conf.sh`, so even a security-list mistake doesn't expose them. |
| Host | `setup.sh`: SSH keys only, no root login, MaxAuthTries 3 (only applied when a key is present, to avoid lock-out; written to `sshd_config.d/01-mailserver.conf` so it wins over image defaults, and checked with `sshd -T`); mailcow checked out at a release tag, never `master`; automatic security updates with a 04:30 reboot when a kernel update needs it; 2 GiB swap. |
| TLS | Let's Encrypt via mailcow ACME for all web, SMTP submission and IMAP. |
| mailcow admin | Default password changed at install; TOTP 2FA required. |
| mailcow API | The read-write key is held only by the **provisioning broker** (`broker/`), lives in `broker.env` (mode 600) and is allow-listed to the broker's address (172.30.240.11). The public registration app has no key: it can only ask the broker to check an address or create a mailbox. The broker enforces its own rules whatever the caller sends: the one domain, username format and reserved names, quota ceiling, at most `MAX_CREATES_PER_DAY` new mailboxes per 24 h, a bearer token, and calls only from the app's address. It offers nothing else: no password changes, deletion, suspension, aliases, forwards, admins or settings. |
| Container network | Our containers are **not** on mailcow's network: Postfix trusts every address there to send mail without a password (`mynetworks_style = subnet`). They sit on `avedeus-edge`, a small internal Docker network that nginx-mailcow also joins (via `docker-compose.override.yml`), so they can reach nginx and nothing else, not even the internet. |
| Admin console (PR 15) | Own container at `/console/` (`admin/`), Keycloak sign-in as `avedeus-admin` whose flow always asks for password **and** one-time code (no SSO-cookie shortcut); only the `admins` group with `otp` in `amr`; `ADMIN_ALLOWED_IPS` (404 for everyone else) and nginx's `/console/` closed by default; server-side sessions, 1 h idle, 8 h max; CSRF tokens, `SameSite=Strict`. The control plane verifies the admin's token again on every call (signature, issuer, expiry, client, group, `otp`) and records the admin as actor; break-glass only from `127.0.0.1` inside the broker container. The sign-up app's internal invitation API answers only the console's own token from `172.30.240.14`, never a request that came through nginx. Before PR 15 the admin pages lived in the sign-up app (argon2 + TOTP); they are gone, and old `ADMIN_*` settings stop it from starting ([ADR 0020](adr/0020-admin-console-and-invitations.md)). |
| Invitations | 192-bit random tokens, stored only as SHA-256; single use; expiry; revocation; atomic claim prevents double use, and each claim carries a random id so only the request holding it can finish it; an invitation can't be revoked while someone is signing up with it (if that happens anyway after a timeout, the event log flags it and the console shows it). A new invitation's link is shown once, in the console's answer to the create request, and never stored. |
| Registration | 10 attempts / hour / IP; username check requires a valid invitation (no enumeration); CSRF tokens; reserved names; password policy (12+ chars, 3 classes or 20+ char passphrase), plus mailcow's own policy. |
| Web app hardening | Strict CSP, no inline scripts, `frame-ancestors 'none'`, `no-referrer`, `no-store`, HSTS, COOP/CORP same-origin, Permissions-Policy; secure + HttpOnly + SameSite cookies. Invite tokens kept out of logs: uvicorn access log off, nginx `access_log off` for `/join/i/`, and the username check sends the token in a header, not the URL. Display names stripped of control characters. Container is read-only, non-root, all capabilities dropped, no-new-privileges, 256 MB / 100 process limits. |
| Brute force on mail logins | mailcow's netfilter container bans repeated failed SMTP/IMAP/SOGo logins. |
| Updates | `unattended-upgrades` for Ubuntu; mailcow `update.sh` monthly or on security releases; registration image rebuilt on dependency updates. |
| Backups | Encrypted off-site via rclone crypt; contain DKIM keys and mail crypt keys, so treat the backup remote credentials as secrets. |

## mailcow settings to set in the UI after install

These live in mailcow's database, so they are steps in
[setup-guide.md](setup-guide.md) rather than files in this repo:

- Admin: change `moohoo`, turn on TOTP, and use a separate admin account name if preferred.
- *System → Configuration → Options → Password policy*: minimum length 12, require
  lower/upper/number (matches the invite site).
- *Access → API*: read-write key allowed from `172.30.240.11` (the broker) only. The
  read-only key stays disabled unless the message trace (PR 34) is used; then it too is
  allowed from `172.30.240.11` only, and only the trace uses it.
- Domain *avedeus.ovh*: DKIM 2048-bit; set mailbox count and total quota limits
  so a bug can't create unlimited mailboxes.
- After deliverability tests pass: DMARC `p=quarantine`, and MTA-STS
  (mailcow serves `mta-sts.avedeus.ovh` when the DNS record exists).

mailcow API keys can't be limited to specific endpoints, so the key is kept
out of the internet-facing app altogether. If the registration container
were compromised, an attacker could create at most `MAX_CREATES_PER_DAY` new
mailboxes on avedeus.ovh (and mailcow's own per-domain mailbox and quota
limits cap that again); existing accounts, mail, aliases and settings stay
out of reach. If the broker itself were compromised, the key is exposed:
it has no public port, a tiny dependency set, and runs read-only with no
capabilities. Mailbox lifecycle operations (suspend, delete, password reset)
must never be added to the public app; when they come they go in the broker
behind a separate admin credential.
