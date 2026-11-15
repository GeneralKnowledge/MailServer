# Security

| Area | Measure |
|---|---|
| Network | OCI security list allows only 22, 25, 80, 443, 465, 587, 993 (+4190). Port 22 restricted to the admin's IP where possible. Plain IMAP 143 and POP3 110/995 bound to localhost by `scripts/harden-mailcow-conf.sh`, so even a security-list mistake doesn't expose them. |
| Host | `setup.sh`: SSH keys only, no root login, MaxAuthTries 3 (only applied when a key is present, to avoid lock-out); automatic security updates with a 04:30 reboot when a kernel update needs it; 2 GiB swap. |
| TLS | Let's Encrypt via mailcow ACME for all web, SMTP submission and IMAP. |
| mailcow admin | Default password changed at install; TOTP 2FA required. |
| mailcow API | Read-write key allow-listed to the registration container's IP (172.22.1.240) only; lives in `.env` (mode 600), never sent to browsers. |
| Invite admin | argon2 password + TOTP; a TOTP code works only once (replay blocked); 5 attempts / 15 min / IP **and** 30 / hour across all IPs; server-side sessions (sign-out really ends the session, *Sign out everywhere* ends all), 1 h idle and 8 h absolute limit; optional `ADMIN_ALLOWED_IPS` makes `/join/admin` return 404 to everyone else; sign-in log including failures on the admin page. |
| Invitations | 192-bit random tokens, stored only as SHA-256; single use; expiry; revocation; atomic claim prevents double use. |
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
- *Access → API*: read-write key only, allowed from `172.22.1.240` only; leave
  the read-only key disabled.
- Domain *avedeus.ovh*: DKIM 2048-bit; set mailbox count and total quota limits
  so a bug can't create unlimited mailboxes.
- After deliverability tests pass: DMARC `p=quarantine`, and MTA-STS
  (mailcow serves `mta-sts.avedeus.ovh` when the DNS record exists).

Known limitation: mailcow API keys can't be limited to specific endpoints.
The key could do anything the mailcow API allows if the registration
container were compromised. Mitigations are the IP allow-list, the small
attack surface of the app, and the container restrictions above.
