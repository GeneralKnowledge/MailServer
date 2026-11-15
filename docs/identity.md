# Identity: Keycloak, its database, and one identity (PR 11, PR 12)

> **Status: PR 11 and PR 12 are implemented in code, not proven on server, not deployed.**
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
| `infrastructure/identity/realm/avedeus-realm.json` | Realm `avedeus`, its groups (`users`, `admins`) and its clients (mailcow, control plane, portal, admin console), as code. Secrets are `${...}` placeholders filled from `identity.env` at first import |
| `infrastructure/identity/identity.env.example` | Everything secret or host-specific |
| `infrastructure/mailcow/configuration/nginx/site.identity.custom` | `/auth/` through mailcow's nginx; admin console and master realm closed unless an address is allowed |
| `scripts/identity-smoke.sh` | Starts the stack and checks it (CI runs it on every push; on the server it is the gate's measurement step) |

## The realm

- **Invite only**: self-registration is off. People are created by the
  control plane when an invitation is redeemed (PR 12).
- Password policy: at least 12 characters, not the username or email, not one
  of the last 3. Brute-force protection on (10 failures, growing wait, no
  permanent lockout). TOTP available to every user; the admin console's client
  requires it at every sign-in (PR 15). Security keys and backup (recovery)
  codes are set up by each person in the account console (PR 17).
- **Users can't change their own email or username** (PR 17). mailcow finds a
  Keycloak user by email, which is the mailbox address, so a user who could
  edit it in the account console could point it at another address. Both are
  admin-only in the realm's user profile; names stay editable. Checked against
  a real Keycloak in CI (`test_live_users_cannot_change_their_email_or_username`).
  A realm imported before PR 17 needs the same change by hand: Realm settings
  → User profile → `email` and `username` → *Who can edit*: admin only.
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
  - `avedeus-portal` (PR 14): the account portal at `/account/`; browser
    only, exact redirect. See [portal.md](portal.md).
  - `avedeus-admin` (PR 15): the admin console at `/console/`; browser only,
    exact redirect, and its own browser flow "admin console browser":
    password **and** one-time code, both required, no SSO-cookie shortcut.
    Its tokens carry `groups` and `amr`, which the control plane checks. See
    [admin-console.md](admin-console.md) and
    [ADR 0020](adr/0020-admin-console-and-invitations.md).

The realm file is imported **only when the realm does not exist yet**. After
the first start, change the realm through the admin console or the admin API
and update the file in the same PR, so the code stays the record.

- User profile (PR 12): `firstName` and `lastName` are optional, so a
  one-word name never forces an "update profile" prompt, and a
  `mailcow_template` attribute is declared, visible and editable by admins
  only. Keycloak 24+ drops undeclared attributes, so it must be declared.

Since added: the portal client (PR 14), the admin console's client and its
two-factor flow (PR 15). Deferred: the Avedeus theme (PR 18).

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

## One identity (PR 12)

### How an account is made

The sign-up app calls `createAccount` (`POST /v1/accounts`) with the
invitation's reference and one idempotency key per claim. It does not know
whether Keycloak is on. With `IDENTITY_PROVIDER=keycloak` in `broker.env`,
the control plane:

1. checks the address is free in mailcow (mailbox or alias);
2. creates the Keycloak user: username = local part, email = the new
   address (marked verified), group `users`, the password the person chose,
   and `mailcow_template` if `MAILBOX_TEMPLATE` is set. An existing Keycloak
   user with that name is **never adopted**: the request is refused as taken;
3. records the link (address ↔ Keycloak id) in its own database
   ([ADR 0004](adr/0004-sources-of-truth.md));
4. creates the mailbox with `authsource: keycloak`, the invitation's quota,
   and a random mailbox password nobody knows.

If mailcow definitively refuses step 4, the new Keycloak user is deleted
again. If mailcow's reply is lost, the identity is kept and a retry with the
same key continues from step 4. Keycloak's password policy (12 characters)
applies, so a shorter password is refused before anything is created. Every
attempt writes one `account.create` audit event with the invitation
reference.

The sign-up page then says that **mail apps need an app password**, made in
mailcow's user settings ([ADR 0015](adr/0015-mail-client-passwords.md)).
Webmail and other browser sign-ins use the Keycloak password.

`createMailbox` (the PR 9 path) is refused with `conflict` while Keycloak is
on, so no mailbox can be made without its identity.

### Connecting mailcow (server steps, after the gate)

All of these are mailcow's own features, configured, not built (ADR 0016):

1. In mailcow: **System → Configuration → Access → Identity Provider**,
   choose **Keycloak**:
   - Server URL `https://<mail host>/auth`, realm `avedeus`, client
     `mailcow`, its secret (`MAILCOW_OIDC_SECRET` in `identity.env`),
     redirect URL `https://<mail host>`.
   - **Mailpassword Flow: off.** Mail clients use app passwords only
     (ADR 0015).
   - Periodic full sync on (offboarding: a user disabled in Keycloak loses
     login in mailcow; mail is still received).
   - Import users off: the control plane creates mailboxes itself, at
     sign-up, so nothing waits for a sync.
   - Attribute mapping: `mailcow_template` value → the mailbox template
     (create an `avedeus-user` template with the quota and settings
     invitations use, and set `MAILBOX_TEMPLATE=avedeus-user`).
2. mailcow's sync reads users through the admin API at the public URL. In
   `site.identity.custom`, uncomment the `allow` line in the
   `/auth/admin/realms/avedeus/` location for mailcow's own network, **after
   confirming** nginx sees real client addresses for outside traffic (see the
   comment there), and restart nginx.
3. In `broker.env`: `IDENTITY_PROVIDER=keycloak`, `KEYCLOAK_CLIENT_SECRET`
   (= `CONTROL_PLANE_OIDC_SECRET`), `MAILBOX_TEMPLATE`. Restart the broker;
   `/v1/health` now lists `keycloak`, and health-check alerts on it.
4. "Sign in with Avedeus" as mailcow's only login button is PR 18.

### Existing mailboxes (migration runbook)

The test server has one user, so migration is a short manual runbook, not a
tool (ADR 0017):

1. In Keycloak's admin console (realm `avedeus`), create the user: username
   = local part, email = the address, email verified, group `users`,
   `mailcow_template` set, and a temporary password to hand over.
2. In mailcow, edit the mailbox and set its authentication source to
   Keycloak.
3. Ask the person to sign in to webmail through Keycloak, set their own
   password, and create app passwords for their mail apps; existing app
   passwords keep working.
4. Record the link: until an admin operation exists (PR 15/16), note the
   migration in the admin log. Reconciliation (PR 31) will report mailboxes
   without a link.

### Open server checks (not provable against fakes)

- That mailcow's API accepts `authsource: keycloak` on mailbox creation and
  that login for such a mailbox goes through Keycloak, so the random
  mailbox password is never usable.
- Which source address mailcow's sync arrives from at nginx, and that outside
  traffic keeps its real address.
- Whether disabling a user in Keycloak ends live SOGo and IMAP sessions
  (ADR 0006 gate condition 4).
- That app passwords work for IMAP/SMTP on a Keycloak-sourced mailbox with
  Mailpassword Flow off.

What is proven in code: the adapter (`broker/broker/keycloak.py`) runs
against a fake in the unit tests **and against a real Keycloak 26.8.2** in
CI's Identity stack job (`broker/tests/test_keycloak_live.py`): token,
health, create with group and attribute, duplicate refused as taken, short
password refused as password, delete.

## Removal test (ADR 0017)

`docker compose -f infrastructure/identity/docker-compose.yml down`, delete
`site.identity.custom` from mailcow's nginx folder and restart nginx. mailcow
falls back to its own login; the sign-up app and control plane never depended
on Keycloak in PR 11. From PR 12, also set `IDENTITY_PROVIDER=none` in
`broker.env` (sign-up goes back to mailcow's own login, same API) and switch
mailcow's identity provider off; Keycloak-sourced mailboxes then need their
authentication source set back to mailcow and a new password.
