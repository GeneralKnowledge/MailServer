# MailServer

A private, invitation-based email service built on
[mailcow: dockerized](https://github.com/mailcow/mailcow-dockerized), running
on an Oracle Cloud ARM64 (Ampere) instance.

mailcow is installed from upstream and kept independent of this repository.
This repo holds only our documentation, configuration notes and scripts, and
(from M3) a small separate registration app that provisions mailboxes through
the mailcow API.

## Status

| Milestone | State |
|---|---|
| M0 Environment validation | Desk review done; domain known (avedeus.ovh, DNS on Cloudflare, MX at OVH). Server inspection pending. |
| M1 mailcow installation | Rehearsed on a local x86 mailcow on 2026-11-14 ([rehearsal](docs/rehearsal-2026-11-14.md)); `scripts/setup.sh` and [setup guide](docs/setup-guide.md) ready. Not run on the Oracle ARM server. |
| M2 Email functionality | Local send/receive (SMTP 465, IMAP 993) worked in the rehearsal. DNS, PTR, outbound port 25 and Gmail/Outlook deliverability untested: need the real server. |
| M3 Invitation registration | Built, 46 unit tests, and tested against the **real** mailcow API in the rehearsal. Re-check on the real server. |
| M4 Production preparation | Backup, restore and health scripts working: a full wipe-and-restore succeeded in the 2026-11-14 rehearsal (x86). Still needed: off-site target chosen, and a restore test on the real ARM server. |
| M5 Operations and observability | Planned, not started. See [roadmap](#roadmap). |
| M6 User and mailbox lifecycle | Planned, not started. |
| M7 Service maturity | Planned, not started. |

Nothing has been deployed to the real server yet. The priority is getting M0–M4 running on Oracle and proving real mail delivery before starting M5–M7. Branding ("Avedeus Mail") is in `branding/`.

## Contents

- **[docs/setup-guide.md](docs/setup-guide.md) – start here: step-by-step setup on the Oracle server**

- [docs/m0-readiness-report.md](docs/m0-readiness-report.md) – readiness report
- [docs/m1-plan.md](docs/m1-plan.md) – M1 installation plan
- [docs/signup-extension-evaluation.md](docs/signup-extension-evaluation.md) – MediaLab Sign-up evaluation
- [docs/architecture.md](docs/architecture.md), [deployment.md](docs/deployment.md),
  [dns-configuration.md](docs/dns-configuration.md), [email-testing.md](docs/email-testing.md),
  [administration.md](docs/administration.md), [security.md](docs/security.md),
  [backup-and-recovery.md](docs/backup-and-recovery.md)
- [registration/](registration/) – invite website and admin (FastAPI)
- [infrastructure/](infrastructure/) – compose file for the app, nginx and SOGo files for mailcow
- [branding/](branding/) – name, colours, logos for the invite site and webmail
- [scripts/](scripts/) – `m0-inspect.sh`, `setup.sh`, `health-check.sh`, `backup.sh`, `restore.sh`, `apply-branding.sh`

## Roadmap

M5–M7 come from an outside review (2026-11-15). They are **planned, not
built**. The plan is to make M0–M4 boringly reliable on the real server first.

**M5 – Operations and observability**
- Admin-only operations dashboard (not a new public service): server and
  SMTP/IMAP availability, mail queue and deferred messages, spam-filter
  activity, disk and memory, mailbox storage use, backup age and verification
  status, TLS certificate expiry, DNS and email-authentication status
- Alerts when health checks or backups fail
- External uptime and delivery monitoring

**M6 – User and mailbox lifecycle**
- Suspend and reactivate, change quota, reset password, end active sessions
  where mailcow supports it
- Delete with a confirmation/grace period (soft delete, so data isn't lost by accident)
- Per-account history (creation, admin actions)
- Self-service password recovery; better invitation management
- Consider a small **provisioning broker** that alone holds the mailcow API
  key and exposes only create / check / suspend / quota, so the public
  registration app never holds a full-rights key (see
  [security.md](docs/security.md))

**M7 – Service maturity**
- Automated security checks and dependency update management
- Regular disaster-recovery exercises on the real infrastructure
- Monthly check of upstream mailcow security releases
- Documented operational procedures

## Running the M0 inspection

Copy the script to the server and run it:

```sh
scp scripts/m0-inspect.sh ubuntu@<server-ip>:
ssh ubuntu@<server-ip> 'sudo bash m0-inspect.sh yourdomain.com mail.yourdomain.com'
```

It changes nothing on the system and writes `m0-report-*.txt` in the current
directory.
