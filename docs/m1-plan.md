# M1 – mailcow installation plan

Starts only after the M0 inspection has been run on the server and the
readiness report is confirmed. Nothing here changes DNS except step 2, which
adds **new** `mail.` records only and is done with the operator's go-ahead.

## Before M1 (operator actions, can run in parallel)

| # | Action | Who | Why |
|---|---|---|---|
| P1 | Confirm the domain and current DNS provider | Operator | Hostname, records |
| P2 | Attach a **reserved** public IPv4 to the instance | Operator (OCI console) | Stable IP for PTR/MX |
| P3 | Open OCI ingress: TCP 22, 25, 80, 443, 465, 587, 993 (+4190) | Operator (OCI console) | Mail and web traffic |
| P4 | Request outbound port 25 exemption (service limits request) | Operator | Sending to Gmail/Outlook |
| P5 | Decide relay fallback if P4 is refused | Operator, with recommendation | Sending |

## Steps

1. **Baseline and snapshot.** Run `scripts/m0-inspect.sh` again, save the
   report into `docs/`. Take an OCI boot-volume backup (rollback point).
2. **DNS: forward records only.** Add `A mail.<domain> → <reserved IP>` (TTL
   300). Do not touch MX yet. Then file the PTR service request with Oracle
   for `<IP> → mail.<domain>`.
3. **Host preparation.**
   - `apt update && apt full-upgrade`; enable `unattended-upgrades` for
     security updates.
   - Set hostname to `mail.<domain>`, time zone `Europe/London`, confirm NTP.
   - Create a 2 GiB swap file.
   - Install `git openssl curl gawk coreutils grep jq dnsutils`.
   - Review the image's iptables rules; leave INPUT as SSH-only, make sure
     nothing in FORWARD precedes Docker's chains.
   - SSH: key-only login, no root login.
4. **Docker.** Install Docker Engine and the Compose plugin from Docker's
   official apt repository for `noble`/`arm64`. Verify `docker version` ≥ 24
   and `docker compose version` ≥ 2.
5. **mailcow.** As root with `umask 0022`:
   `git clone https://github.com/mailcow/mailcow-dockerized /opt/mailcow-dockerized`,
   check out the latest release tag, `./generate_config.sh` with
   `mail.<domain>` and `Europe/London`.
   Review `mailcow.conf`: keep `SKIP_LETS_ENCRYPT=n`, `SKIP_CLAMD=n`,
   `SKIP_FTS=n`; disable IPv6 if the host has none.
   Commit a **redacted** copy of our non-default settings to
   `infrastructure/mailcow/configuration/` (never the secrets).
6. **Pull and start.** `docker compose pull` (confirms every image has an
   arm64 build), then `docker compose up -d`.
7. **TLS.** Watch `acme-mailcow` logs until a Let's Encrypt certificate for
   `mail.<domain>` is issued. Check with
   `openssl s_client -connect mail.<domain>:443` and on 465/993.
8. **Admin access.** Log in at `https://mail.<domain>/admin`, change the
   default `admin`/`moohoo` password immediately, enable TOTP 2FA for the
   admin account.
9. **Health.** All containers `Up (healthy)` in `docker compose ps`; mailcow
   UI "System → Status" green; inbound ports reachable from outside (tested
   from an external host with `openssl s_client`/`nc`).
10. **Document.** Write `docs/deployment.md` with the exact versions and
    commands used, and commit `scripts/health-check.sh` (wraps
    `docker compose ps` and a few port/TLS checks).

## M1 exit criteria

- mailcow running, all containers healthy, survives a reboot.
- Valid Let's Encrypt certificate on `mail.<domain>`.
- Admin login works with 2FA; default password gone.
- PTR request filed (or done); port 25 status known.
- No MX change yet; existing mail flow for the domain untouched.

## Rollback

Stop with `docker compose down` in `/opt/mailcow-dockerized`; remove the
`mail.` A record if needed. Restore the boot-volume backup from step 1 for a
full reset. Nothing in M1 affects the domain's existing websites or mail.
