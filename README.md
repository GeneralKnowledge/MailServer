# MailServer

A private, invitation-based email service built on
[mailcow: dockerized](https://github.com/mailcow/mailcow-dockerized), running
on an Oracle Cloud ARM64 (Ampere) instance.

Avedeus is a small platform that integrates existing open-source services;
mail is its first application. The architecture, its five locked constraints
and the decisions behind it are in [docs/architecture.md](docs/architecture.md)
and [docs/adr/](docs/adr/).

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
| Audit fixes 1–7 | Done in code (PR #1): backups, app fixes, CI, SSH/admin, alerts, network isolation, provisioning broker. Not proven on Oracle. |
| M5 Integration foundation | PR 8 (architecture, ADRs, contracts) in review. PR 9–13 planned. |
| M6–M10 | Planned, not started. See [docs/roadmap.md](docs/roadmap.md). |

Nothing has been deployed to the real server yet. The priority is getting M0–M4 running on Oracle and proving real mail delivery; M5–M10 code can be written earlier but is not done until it runs there. Branding ("Avedeus Mail") is in `branding/`.

## Contents

- **[docs/setup-guide.md](docs/setup-guide.md) – start here: step-by-step setup on the Oracle server**

- [docs/m0-readiness-report.md](docs/m0-readiness-report.md) – readiness report
- [docs/m1-plan.md](docs/m1-plan.md) – M1 installation plan
- [docs/signup-extension-evaluation.md](docs/signup-extension-evaluation.md) – MediaLab Sign-up evaluation
- [docs/architecture.md](docs/architecture.md) – platform layers, constraints, capabilities;
  [adr/](docs/adr/) – decisions; [roadmap.md](docs/roadmap.md) – M5–M10;
  [platform-baseline.md](docs/platform-baseline.md) – when the foundations are done;
  [integration-contract.md](docs/integration-contract.md) – rules for adapters and new services
- [contracts/](contracts/) – control-plane OpenAPI, caller capabilities, audit-event schema (checked in CI)
- [broker/](broker/) – provisioning broker, the first version of the control plane
- [deployment.md](docs/deployment.md),
  [dns-configuration.md](docs/dns-configuration.md), [email-testing.md](docs/email-testing.md),
  [administration.md](docs/administration.md), [security.md](docs/security.md),
  [server-security-standard.md](docs/server-security-standard.md) (every machine; `hardening/`),
  [backup-and-recovery.md](docs/backup-and-recovery.md)
- [registration/](registration/) – invite website and admin (FastAPI)
- [infrastructure/](infrastructure/) – compose file for the app, nginx and SOGo files for mailcow
- [branding/](branding/) – name, colours, logos for the invite site and webmail
- [scripts/](scripts/) – `m0-inspect.sh`, `setup.sh`, `health-check.sh`, `backup.sh`, `restore.sh`, `apply-branding.sh`

## Roadmap

[docs/roadmap.md](docs/roadmap.md) is the plan (adopted 2026-11-15). In short:
M0–M4 on the real Oracle server first, then

- **M5 Integration foundation**: a narrow control plane, Keycloak (after a
  validation gate), one identity with app passwords for mail clients, health
  contract
- **M6 User experience**: portal that links to existing mailcow, SOGo and
  Keycloak functionality, separate admin console, account lifecycle,
  self-service, one look
- **M7 Privacy**: privacy configuration and discoverability (alias work is
  parked)
- **M8 Automation**: new-sign-in notifications (SOGo owns mail rules)
- **M9 Operations**: audit viewer, recovery drills and runbooks, delivery and
  DNS checks, reconciliation, admin message trace; metrics stack deferred
- **M10 Extensions**: deferred until an actual integration needs them

The initial ship target is the "minimum complete platform" in the roadmap.
Before building anything, check whether mailcow, SOGo or Keycloak already
provides it ([gap audit](docs/mailcow-gap-audit.md)).

## Running the M0 inspection

Copy the script to the server and run it:

```sh
scp scripts/m0-inspect.sh ubuntu@<server-ip>:
ssh ubuntu@<server-ip> 'sudo bash m0-inspect.sh yourdomain.com mail.yourdomain.com'
```

It changes nothing on the system and writes `m0-report-*.txt` in the current
directory.
