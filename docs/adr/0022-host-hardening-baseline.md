# 0022. One host baseline for every machine, applied with Ansible

- Status: **Accepted** (2026-11-17). Owner asked for a security standard
  and a reusable hardening baseline that covers every machine, including
  future GPU nodes.
- Implemented: Yes in code (`hardening/`, CI job "Host baseline" applies it
  to a fresh Ubuntu VM and tests exposure from outside). Proven on Oracle: No.

## Context

The project will run more than one machine: the mailcow host, Oracle Always
Free and Hetzner servers, and later cheap GPU nodes. `scripts/setup.sh`
hardens SSH and turns on updates for the mail host only, and nothing checks
afterwards that a host still matches. Public hosts are scanned within
minutes, and Docker-published ports silently bypass ufw.

## Decision

1. **One standard** ([server-security-standard.md](../server-security-standard.md)),
   controls S1–S7 and three profiles (mail host, general server, GPU node).
2. **Ansible** applies it (`hardening/`, one role, idempotent): it is
   agentless, readable, and the same run works for one host or ten. A shell
   script was the alternative; it does not re-apply cleanly or show a diff.
3. **A read-only audit script** checks a host against it, without Ansible,
   so existing machines can be audited today and every host re-checks
   itself daily (the digest).
4. **Docker-published ports are filtered in `DOCKER-USER`** (the guard),
   loaded with ufw. Off on the mail host until watched next to mailcow's
   own netfilter on the server; the audit covers the gap.
5. **SSH over Tailscale** by default; OpenSSH stays the server (Tailscale
   SSH off) so keys, login alerts and fail2ban apply in one place.
6. **Monitoring stays alerts and diagnostics**: a push per SSH login and a
   daily digest through the existing ntfy/healthchecks helpers. No
   Prometheus/Grafana, no CrowdSec, no WAF.
7. Oracle's stock iptables rules are handed over to ufw, keeping Oracle's
   169.254.0.0/16 OUTPUT rules.

## Consequences

- `setup.sh` leaves SSH and update settings alone once the baseline is
  applied; the baseline removes `setup.sh`'s older files.
- A new machine is an inventory line plus one run.
- Removal test: deleting `hardening/` leaves hosts configured but
  unmanaged; nothing else in the platform depends on it.
