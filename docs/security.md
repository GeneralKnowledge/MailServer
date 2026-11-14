# Security

| Area | Measure |
|---|---|
| Network | OCI security list allows only 22, 25, 80, 443, 465, 587, 993 (+4190). SSH key-only, ideally restricted to admin IPs. |
| TLS | Let's Encrypt via mailcow ACME for all web, SMTP submission and IMAP. |
| mailcow admin | Default password changed at install; TOTP 2FA required. |
| mailcow API | Read-write key allow-listed to the registration container's IP (172.22.1.240) only; lives in `.env` (mode 600), never sent to browsers. |
| Invite admin | argon2 password + TOTP, 5 attempts / 15 min / IP, 1 h idle and 8 h absolute session limit, sign-in log on the admin page. |
| Invitations | 192-bit random tokens, stored only as SHA-256; single use; expiry; revocation; atomic claim prevents double use. |
| Registration | 10 attempts / hour / IP; username check requires a valid invitation (no enumeration); CSRF tokens; reserved names; password policy (12+ chars, 3 classes or 20+ char passphrase), plus mailcow's own policy. |
| Web app hardening | Strict CSP, no inline scripts, `frame-ancestors 'none'`, `no-referrer` (tokens are in URLs), `no-store`, secure + HttpOnly cookies. Container is read-only, runs as non-root, all capabilities dropped. |
| Brute force on mail logins | mailcow's netfilter container bans repeated failed SMTP/IMAP/SOGo logins. |
| Updates | `unattended-upgrades` for Ubuntu; mailcow `update.sh` monthly or on security releases; registration image rebuilt on dependency updates. |
| Backups | Encrypted off-site via rclone crypt; contain DKIM keys and mail crypt keys, so treat the backup remote credentials as secrets. |

Known limitation: mailcow API keys can't be limited to specific endpoints.
The key could do anything the mailcow API allows if the registration
container were compromised. Mitigations are the IP allow-list, the small
attack surface of the app, and the container restrictions above.
