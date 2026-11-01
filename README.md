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
| M0 Environment validation | Desk review done; server inspection pending |
| M1 mailcow installation | Planned |
| M2 Email functionality | – |
| M3 Invitation registration | Extension evaluated: build own FastAPI app |
| M4 Production preparation | – |

## Contents

- [docs/m0-readiness-report.md](docs/m0-readiness-report.md) – readiness report
- [docs/m1-plan.md](docs/m1-plan.md) – M1 installation plan
- [docs/signup-extension-evaluation.md](docs/signup-extension-evaluation.md) – MediaLab Sign-up evaluation
- [scripts/m0-inspect.sh](scripts/m0-inspect.sh) – read-only server inspection

## Running the M0 inspection

Copy the script to the server and run it:

```sh
scp scripts/m0-inspect.sh ubuntu@<server-ip>:
ssh ubuntu@<server-ip> 'sudo bash m0-inspect.sh yourdomain.com mail.yourdomain.com'
```

It changes nothing on the system and writes `m0-report-*.txt` in the current
directory.
