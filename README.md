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
| M0 Environment validation | Desk review done; server inspection pending (needs domain + server access) |
| M1 mailcow installation | Plan and `scripts/setup.sh` ready; not run |
| M2 Email functionality | DNS plan and test checklist ready; not run |
| M3 Invitation registration | App built and tested against a simulated mailcow API; real API check pending |
| M4 Production preparation | Backup/restore/health scripts and docs ready; off-site target and restore test pending |

Nothing has been deployed yet. Branding ("Avedeus Mail") is in `branding/`.

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

## Running the M0 inspection

Copy the script to the server and run it:

```sh
scp scripts/m0-inspect.sh ubuntu@<server-ip>:
ssh ubuntu@<server-ip> 'sudo bash m0-inspect.sh yourdomain.com mail.yourdomain.com'
```

It changes nothing on the system and writes `m0-report-*.txt` in the current
directory.
