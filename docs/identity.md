# Identity: Keycloak and its database (PR 11)

> **Status: implemented in code, not proven on server, not deployed.**
> Keycloak is not started on the server until the
> [ADR 0006 validation gate](adr/0006-keycloak-identity-provider.md#validation-gate-before-deployment-added-2026-11-15)
> passes. If the gate fails, the integration is paused and mailcow keeps its
> own authentication. Nothing on this page changes the running mail server
> until then.

Keycloak is the platform's **browser** identity: one account for the portal,
admin console and (from PR 12) mailcow's web login. **Mail clients never
authenticate against Keycloak**: they use mailcow app passwords
([ADR 0015](adr/0015-mail-client-passwords.md)). No client in the realm
allows the password grant, and mailcow's Mailpassword Flow is not enabled.

## What mailcow, SOGo and Keycloak already provide (ADR 0016)

Everything here is configuration of Keycloak's own features: users, groups,
password policy, brute-force protection, TOTP, sessions, the account console
and the admin console. Nothing is built. The only new pieces are the compose
file, the realm file, an nginx include and backup lines.

## Files

| File | What it is |
|---|---|
| `infrastructure/identity/docker-compose.yml` | Keycloak 26.8 and Postgres 17 (official multi-arch images, arm64 included), memory and pids limits, JVM heap capped at 512 MB, health checks |
| `infrastructure/identity/realm/avedeus-realm.json` | Realm `avedeus`, its groups (`users`, `admins`) and two clients, as code. Secrets are `${...}` placeholders filled from `identity.env` at first import |
| `infrastructure/identity/identity.env.example` | Everything secret or host-specific |
| `infrastructure/mailcow/configuration/nginx/site.identity.custom` | `/auth/` through mailcow's nginx; admin console and master realm closed unless an address is allowed |
| `scripts/identity-smoke.sh` | Starts the stack and checks it (CI runs it on every push; on the server it is the gate's measurement step) |

## The realm

- **Invite only**: self-registration is off. People are created by the
  control plane when an invitation is redeemed (PR 12).
- Password policy: at least 12 characters, not the username or email, not one
  of the last 3. Brute-force protection on (10 failures, growing wait, no
  permanent lockout). TOTP available to every user; the admin group's TOTP is
  enforced in PR 15.
- Sessions: 30 minutes idle, 10 hours maximum. Login and admin events kept
  30 days; admin events never store request bodies.
- Clients:
  - `mailcow`: confidential, authorization code with PKCE, redirects only to
    the mail host. Its service account may **read** users and groups, which
    mailcow's own IdP sync needs; it cannot change anything.
  - `avedeus-control-plane`: service account only, no browser flows. It may
    manage users (create, disable, group membership) and nothing else in the
    realm, and it is the only client that may change users
    ([ADR 0003](adr/0003-control-plane-owns-upstream-credentials.md)).
  - Both include Keycloak's `kc-admin-api` scope, which Keycloak 26.8
    requires on tokens used against its admin API.

The realm file is imported **only when the realm does not exist yet**. After
the first start, change the realm through the admin console or the admin API
and update the file in the same PR, so the code stays the record.

Deferred to later PRs: the `mailcow_template` user attribute and mailbox
templates (PR 12), a portal client (PR 14), the admin group's TOTP and
IP rules (PR 15), the Avedeus theme (PR 18).

## Network

See [ADR 0019](adr/0019-identity-network-placement.md). Keycloak sits on
`avedeus-edge` at `172.30.240.12` with no published ports. Postgres sits only
on `identity-db`, an internal network with Keycloak as its only other member.
Health and metrics are on Keycloak's management port (9000), never proxied;
metrics are off.

Through nginx (checked in testing with the shipped file): the realm's public
endpoints answer, while everything under `/auth/admin/` (console and admin
REST API) and `/auth/realms/master/` returns 403 unless an `allow` line is
added. The control plane reaches Keycloak directly on `avedeus-edge`, not
through nginx. **Open point for PR 12:** mailcow's IdP sync calls the admin
REST API through the public URL, so PR 12 must open
`/auth/admin/realms/avedeus/` to mailcow's own source address only, and say
so.

## Resources

Measured on x86 in testing after start-up: Keycloak about 670 MB with the
512 MB heap (container limit 1 GB), Postgres about 60 MB (limit 256 MB).
First start takes 1–2 minutes (Keycloak builds its configuration, then
imports the realm). These are not Ampere numbers; gate condition 2 needs them
measured on the real box beside mailcow.

## Deploying (only after the gate passes)

1. Make sure `avedeus-edge` exists: `scripts/create-edge-network.sh`.
2. `cp infrastructure/identity/identity.env.example infrastructure/identity/identity.env`,
   `chmod 600` it, fill every value (`openssl rand -hex 32` for secrets).
   `KC_HOSTNAME` is `https://<mailcow hostname>/auth`.
3. Run `scripts/identity-smoke.sh` with `KEEP=1`. It starts the stack, checks
   the realm, the clients' rights, that the password grant is refused, that
   self-registration is closed and that Postgres is unreachable from
   `avedeus-edge`, round-trips a database dump and prints memory use.
   Record the memory numbers in [platform-baseline.md](platform-baseline.md).
4. Copy `site.identity.custom` to
   `/opt/mailcow-dockerized/data/conf/nginx/`, add an `allow` line for each
   admin address in the two closed locations, then
   `docker compose restart nginx-mailcow` in the mailcow folder.
5. From an allowed address, sign in to `https://<host>/auth/admin/` as the
   bootstrap admin, create a named admin in the master realm with TOTP, sign
   in as that admin, delete the bootstrap admin, and remove the two
   `KC_BOOTSTRAP_ADMIN_*` lines from `identity.env`.
6. Set `IDENTITY_ENABLED=1` in `/etc/mailserver/backup.env` so the daily
   backup includes the Keycloak database and `identity.env`, and fails loudly
   if it can't.

`scripts/health-check.sh` already watches the `identity` containers once they
exist; an unhealthy Keycloak or Postgres raises its own alert.

## Backup and restore

- Backup: `pg_dump` custom format to `extras/keycloak.pgdump`, verified each
  run with `pg_restore --list`, plus `extras/identity.env`.
- Restore: `scripts/restore.sh identity <backup-folder>` saves the current
  database next to the restore, recreates it from the dump and restarts
  Keycloak. Tested against a local stack in PR 11; a restore on the server is
  gate condition 3 (PR 29 scope).

## Removal test (ADR 0017)

`docker compose -f infrastructure/identity/docker-compose.yml down`, delete
`site.identity.custom` from mailcow's nginx folder and restart nginx. mailcow
falls back to its own login; the sign-up app and control plane never depended
on Keycloak in PR 11. From PR 12, removal also means switching mailcow's
identity provider setting back off; PR 12 documents that step.
