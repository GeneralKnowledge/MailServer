# Plan: M5–M7

Status: **plan only, nothing built.** Written 2026-11-15, before deployment,
from the outside review's roadmap. Build order still puts M0–M4 on the real
Oracle server first; parts of M5 and M7 are cheap enough to build alongside.

Guiding rules, carried over from the brief:

- Reuse mailcow's own features and API before writing code.
- No new public-facing services. Everything new is admin-only.
- Keep custom code in this repo, outside the mailcow tree.
- Small, separately deployable pieces; nothing that blocks a mailcow update.

Open decisions are collected at the end ([Decisions needed](#decisions-needed)).

---

## M5 – Operations and observability

**Goal:** one admin page that answers "is the service healthy, is mail
flowing, are backups good?", plus alerts that reach you when it isn't, plus
an outside view that still works when the server is down.

### Components

| # | Piece | What it is | Where it runs |
|---|---|---|---|
| 5.1 | **Status collector** | `scripts/health-check.sh --json`, run by cron every 5 min, writes `/var/lib/mailserver/status.json` (host facts the app can't see: disk, memory, swap, container states, cert expiry, backup age and last verification result, open ports from outside, DNS/auth record checks). | Host (cron) |
| 5.2 | **Ops dashboard** | New admin page `/join/admin/ops` in the existing invite app. Reads `status.json` (mounted read-only) and the mailcow API with a **read-only** API key. | Existing registration container |
| 5.3 | **Alerts** | The collector compares with the previous run and sends one message per state change (problem / recovered), not every 5 minutes. | Host |
| 5.4 | **External monitoring** | An outside service probes `https://mail.avedeus.ovh` and ports 25/465/993, and receives "heartbeat" pings from the backup job (alert if a ping is missing). | SaaS, free tier |
| 5.5 | **Delivery probe** | Daily: send a message from a probe mailbox to an external mailbox (e.g. a Gmail account that auto-forwards back), check it returns within 15 min and that its headers show SPF/DKIM/DMARC pass and inbox placement where detectable. | Host (cron) |
| 5.6 | **DNS / authentication checks** | Part of the collector: MX, SPF, DKIM, DMARC, PTR, MTA-STS, TLS-RPT records match the expected values; Spamhaus/other blocklist lookup of the server IP. | Host |

### Dashboard contents and data sources

| Panel | Source |
|---|---|
| Service up, containers healthy | mailcow API `get/status/containers`; collector |
| SMTP / IMAP reachable from outside | External monitor (5.4) status, shown as a link/badge |
| Mail queue, deferred messages, oldest message age | mailcow API `get/mailq/all` |
| Spam filtering activity (scanned, rejected, greylisted, per day) | mailcow API `get/logs/rspamd-history` / `rspamd-stats` |
| Failed logins and bans | mailcow API `get/logs/netfilter`, `get/fail2ban` |
| Disk, memory, swap, load | collector |
| Mail storage: total `/var/vmail`, per-mailbox use vs quota, top 10 | mailcow API `get/status/vmail`, `get/mailbox/all` |
| Backups: last run, age, size, verification result, last off-site upload | collector (reads `/var/log/mail-backup.log` and backup folders) |
| TLS certificate expiry | collector |
| DNS and email authentication | collector (5.6) |
| Last delivery probe | collector (5.5) |
| Invitations: open, used this month, provisioning failures | registration DB (already exists) |

All read-only. The page shows a single traffic light at the top (green /
amber / red) using thresholds below, then the panels.

| Signal | Amber | Red |
|---|---|---|
| Disk use | 75% | 85% |
| Cert expiry | < 21 days | < 7 days |
| Backup age | > 26 h | > 50 h or verification failed |
| Deferred queue | > 20 or oldest > 2 h | > 100 or oldest > 12 h |
| Delivery probe | 1 failure | 2 in a row |
| Any container | starting > 10 min | unhealthy / stopped |
| IP on a blocklist | – | listed |

### Security notes

- Second mailcow API key: **read-only**. Since the 2026-11-15 audit fixes, no
  upstream key may live in the public app, so this key belongs to the broker
  (or a separate internal collector), which hands the dashboard only the
  figures it needs. The dashboard never uses the read-write key.
- The page sits behind the existing admin login (password + TOTP,
  `ADMIN_ALLOWED_IPS`). Nothing new is exposed.
- `status.json` contains no secrets; mounted read-only.

### Build order

1. 5.1 collector `--json` + 5.3 alerts (most value, smallest change).
2. 5.4 external monitor + backup heartbeat (configuration, no code).
3. 5.2 dashboard page.
4. 5.6 DNS/blocklist checks, then 5.5 delivery probe.

### Done when

The dashboard shows real values on the Oracle server; stopping a container,
filling a test disk, and skipping a backup each produce exactly one alert and
one recovery message; the external monitor alerts when the VM is stopped.

---

## M6 – User and mailbox lifecycle

**Goal:** everyday account management from one admin screen, with an audit
trail, without the public registration app holding a full-power API key.

### What mailcow already provides (use, don't rebuild)

| Need | mailcow feature (API / UI) |
|---|---|
| Suspend / reactivate | Edit mailbox `active`: 1 active, 0 inactive; mailcow also has a state that blocks login but keeps receiving mail (value 2; to confirm on the real API) |
| Change quota | Edit mailbox `quota` |
| Reset password | Edit mailbox `password`/`password2`, optionally `force_pw_update` |
| Self-service password recovery | mailcow's built-in **"forgot password" by recovery email** (per-mailbox `recovery_email`, admin-enabled `pw_reset` permission) |
| Delete | `delete/mailbox` (immediate, permanent) |
| End active sessions | No single API call. Password change ends future logins; SOGo profile reset (`delete/sogo_profile`) and a Dovecot `kick` via the server need checking. **Open question.** |

### Components

| # | Piece | What it is |
|---|---|---|
| 6.1 | **Provisioning broker** | Small internal service (FastAPI, ~200 lines) that alone holds the mailcow **read-write** key. It offers only: check address, create mailbox, set active state, set quota, set password, schedule/cancel/perform deletion, set recovery email. It enforces rules itself (only the avedeus.ovh domain, quota ceiling, max new mailboxes per day, no admin/domain endpoints). Listens only on an internal Docker network; no port published. |
| 6.2 | **Registration app changes** | Talks to the broker instead of mailcow; loses the read-write key. The public side can only call "check" and "create". |
| 6.3 | **Admin "Accounts" page** | `/join/admin/accounts`: list mailboxes (address, created, invitation used, quota and use, state, last login from mailcow), and actions: suspend, reactivate, change quota, reset password (shows a one-time temporary password, forces change at next login), schedule deletion, cancel deletion. Admin actions reach the broker with a separate admin credential the public routes never see. |
| 6.4 | **Soft delete** | "Delete" = suspend (active 0) + mark `delete_after = now + 30 days` in the broker's DB. A daily job deletes for real only after the grace period, and only after confirming a backup newer than the suspension exists. Cancel restores access. Optional: export the mailbox with Dovecot before final deletion and keep it with the backups. |
| 6.5 | **Account history** | Append-only events per address: invited (by which invitation), created, suspended, reactivated, quota changed, password reset, deletion scheduled/cancelled/done, with time, admin and IP. Shown on the account's page. Extends the existing `events` table. |
| 6.6 | **Invitation improvements** | Invitations can carry an intended name/note and quota preset (exists), plus: bulk create, resend link (create a replacement and revoke the old one in one click), and "who invited whom" if more than one admin is ever added. |
| 6.7 | **Recovery email at sign-up** | Optional field on the sign-up form, passed to mailcow's `recovery_email`, so mailcow's own reset flow works. |

### Architecture change

```
Before:  public invite app ──(read-write key)──► mailcow API
After:   public invite app ──(create/check only)──► broker ──(read-write key)──► mailcow API
         admin pages ───────(admin credential)────┘
         ops dashboard ─────(read-only key)───────────────────────────────► mailcow API
```

If the public app were compromised, an attacker could create mailboxes within
the broker's limits, but not read mail, change other accounts, add domains or
touch mailcow settings.

### Build order

1. 6.1 broker + 6.2 switch-over (security gain, no visible change).
   **Built 2026-11-15** (audit fix 7, `broker/`) with check and create only;
   tested against the fake API, real-API check still needs the rehearsal.
2. 6.5 history, then 6.3 accounts page with suspend/reactivate/quota/reset.
3. 6.4 soft delete.
4. 6.7 recovery email, 6.6 invitation improvements.

### Done when

The registration container's environment holds no mailcow key; every
action in 6.3 works against the real API and leaves a history entry; a
scheduled deletion can be cancelled within 30 days and completes after it; a
user can reset a forgotten password through mailcow without the admin.

---

## M7 – Service maturity

**Goal:** routine work happens on a schedule, regressions are caught before
deploy, and recovery is practised rather than hoped for.

### Components

| # | Piece | What it is |
|---|---|---|
| 7.1 | **CI on GitHub** | On every push/PR: registration tests, `shellcheck` on scripts, Python lint, `pip-audit` for known-vulnerable dependencies, Docker build for **arm64 and amd64**, image scan (Trivy). |
| 7.2 | **Dependency updates** | Dependabot (or Renovate) for Python packages, the Python base image and GitHub Actions; weekly, grouped, CI must pass. |
| 7.3 | **mailcow update procedure** | Written monthly procedure (backup → `update.sh --check` → read release notes → update → `apply-branding.sh` → health check → test mail). Watching mailcow releases is done by subscribing to the GitHub repo's releases; out-of-band security releases are applied within a week. |
| 7.4 | **Scheduled security checks** | Monthly: external TLS scan (e.g. testssl.sh), open-port scan from outside, `pip-audit`/Trivy on the running image, review of admin sign-in log and netfilter bans, check of DMARC aggregate reports. Results appended to a log in the repo. |
| 7.5 | **Disaster-recovery exercises** | Quarterly: restore last night's off-site backup onto a temporary Oracle ARM VM, run health check and test mail, record time taken in the restore log, destroy the VM. Yearly: full rebuild from this repo + backup only, timed. |
| 7.6 | **Runbooks** | Short step lists in `docs/runbooks/`: mail not arriving, mail going to spam / IP blocklisted, disk full, certificate failed to renew, container unhealthy, server lost (rebuild), admin locked out, user locked out, suspected compromise (rotate keys, revoke sessions). |
| 7.7 | **Secrets rotation** | Yearly or on suspicion: mailcow API keys, invite app session secret and admin TOTP, rclone crypt credentials (re-encrypt), DKIM key (2048-bit roll with overlap). Each with a runbook. |

### Build order

1. 7.1 CI and 7.2 Dependabot (can be done now, before the server).
2. 7.6 runbooks for the five most likely incidents.
3. 7.3 update procedure and 7.4 checks, once the server exists.
4. 7.5 first DR exercise as part of M4 sign-off, then quarterly.
5. 7.7 rotation runbooks.

### Done when

CI blocks a failing change; one quarter of scheduled checks and one DR
exercise are recorded; every runbook has been walked through once.

---

## Overall order and effort

| Step | Milestone pieces | Needs the server? | Rough effort |
|---|---|---|---|
| 1 | M0–M4 on Oracle (setup guide) | Yes | 1–2 days of your time + Oracle's wait |
| 2 | 7.1 CI, 7.2 Dependabot | No | half a day |
| 3 | 5.1 collector + 5.3 alerts, 5.4 external monitor | Yes (to test) | 1 day |
| 4 | 6.1 broker + 6.2 switch-over | Yes (to test) | 1 day |
| 5 | 5.2 dashboard, 5.6 DNS checks | Yes | 1–2 days |
| 6 | 6.5 history, 6.3 accounts page, 6.4 soft delete | Yes | 2 days |
| 7 | 7.6 runbooks, 7.3 update procedure, first 7.5 DR exercise | Yes | 1 day |
| 8 | 5.5 delivery probe, 6.6/6.7, 7.4, 7.7 | Yes | 1–2 days |

Steps 2 and the code of 3–6 can be written before the server exists and
tested against the simulated API, but none is "done" until it has run
against the real mailcow.

---

## Decisions needed

| # | Question | Options | Recommendation |
|---|---|---|---|
| D1 | Where do alerts go? | Email to your personal address; push to phone (ntfy.sh or Pushover); both | **Both**: push for red, email for everything. Email alone fails when the mail server is the problem. |
| D2 | External monitor | UptimeRobot, Better Stack, healthchecks.io (heartbeats), self-hosted Uptime Kuma elsewhere | **UptimeRobot** (ports/HTTP) + **healthchecks.io** (backup heartbeat); both free tiers. |
| D3 | Delivery probe target | A Gmail account you create for this; Outlook too; skip | **One dedicated Gmail account** first; add Outlook if Microsoft delivery causes trouble. |
| D4 | Dashboard placement | Inside the invite app's admin (recommended); separate admin-only app; Grafana stack | **Inside the invite app**: no new service, same login. Grafana is more than this needs. |
| D5 | Build the broker? | Now with M6; later; never (accept the documented risk) | **With M6**, before the accounts page, since the accounts page would otherwise widen what the public app can do. |
| D6 | Soft-delete grace period | 7 / 30 / 90 days | **30 days**, matching off-site backup retention. |
| D7 | Self-service password recovery | mailcow's built-in recovery email; none (admin resets only) | **mailcow's built-in**, with the recovery address optional at sign-up. |
| D8 | Who gets "suspend but keep receiving"? | Use for abuse cases only; never | Use for **abuse holds**; normal suspension blocks both. |
| D9 | Repo hosting for CI | GitHub Actions (free for this size) | **GitHub Actions**. |
