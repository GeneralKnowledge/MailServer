# Admin console (PR 15)

> **Implemented in code; not proven on server; not deployed.** The console
> needs Keycloak, so it waits for the
> [ADR 0006 gate](adr/0006-keycloak-identity-provider.md#validation-gate-before-deployment-added-2026-11-15).
> Until then, invitations are managed with the sign-up app's CLI (below).
> Decision record: [ADR 0020](adr/0020-admin-console-and-invitations.md).

`https://<mail host>/console/` is the Avedeus admin console, in its own
container (`admin/`), away from the public sign-up app
([ADR 0010](adr/0010-no-public-lifecycle-admin-separate.md)). It is at
`/console`, because mailcow's own admin login already uses `/admin`.

| Page | What it shows or does | Comes from |
|---|---|---|
| Invitations | create one or several (PR 17; links shown once), new link for an unused one (PR 17), revoke, extend; recent sign-up failures | the sign-up app's internal API (invitations stay in its database) |
| Accounts | every mailbox on the mail domain: state, size, use, created, linked to Keycloak or not, deletion due | control plane `listAccounts` |
| One account (PR 16) | state, identity, pending deletion, history; suspend, reactivate, quota, ask for a new password, schedule or cancel deletion | control plane `getAccount` and the lifecycle operations ([ADR 0021](adr/0021-account-lifecycle.md)) |
| Message trace | why a message did or didn't arrive (read-only) | control plane `traceMessage` (PR 34) |
| Audit log | the control plane's events, newest first, filter by action and outcome | control plane `readAudit` |

Everything else stays where it already works
([ADR 0016](adr/0016-use-what-exists-first.md)), and the console links there:
aliases, app passwords and other mailbox settings in **mailcow's admin**
(`/admin`), users, groups, sessions and sign-in events in **Keycloak's admin
console**. What the lifecycle buttons do: [administration.md](administration.md#account-lifecycle-pr-16).
Each lifecycle form carries a fresh Idempotency-Key, so a double submit
replays instead of acting twice; scheduling a deletion asks for the address
to be typed.

## Who gets in

1. Only from an address in `ADMIN_ALLOWED_IPS` (others get 404), and nginx's
   `/console/` location is closed until you add an `allow` line.
2. Sign-in is Keycloak, client `avedeus-admin`. Its own browser flow asks for
   the **password and a one-time code every time** (no "already signed in"
   shortcut). Someone without a code sets one up first; that first sign-in
   doesn't count, so they sign in once more with a code.
3. Only members of the `admins` group whose sign-in used the code
   (`otp` in the token's `amr`) get in.
4. Every control-plane call carries the admin's own access token, which the
   control plane verifies again (signature, issuer, expiry, client
   `avedeus-admin`, group, `otp`) and records as the actor in its audit log.
5. Sessions live in memory: 8 hours at most, 1 hour idle. Forms carry a
   CSRF token; the cookie is `SameSite=Strict`.

## Two logs

- **Invitations** (create, revoke, extend, who registered, sign-up failures)
  are in the sign-up app's own event log, with the admin's Keycloak id.
- **Accounts created, lifecycle changes, deletions, refusals, traces** are in
  the control plane's audit log (and each account's page shows its own).
- **Sign-ins** are in Keycloak's events.

This is the cost of keeping invitations in the sign-up app (ADR 0020).

## Without Keycloak (today)

On the server, inside the sign-up container:

```sh
cd /opt/MailServer/infrastructure/registration
sudo docker compose exec registration python -m app.cli create-invite --quota 2048 --days 7 --note "Sam"
sudo docker compose exec registration python -m app.cli list
sudo docker compose exec registration python -m app.cli revoke 3
sudo docker compose exec registration python -m app.cli extend 3 --days 14
sudo docker compose exec broker python -m broker.trace --address sam@mail.avedeus.ovh
sudo docker compose exec broker python -m broker.account show sam@mail.avedeus.ovh
```

The trace and `broker.account` (PR 16) work by break-glass: from inside the broker container the
`ADMIN_TOKEN` alone is enough, audited as `admin-break-glass`.

## Upgrading the sign-up app to PR 15

The sign-up app no longer has admin pages and **refuses to start** while its
`.env` still has `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `ADMIN_TOTP_SECRET`,
`ADMIN_ALLOWED_IPS` or `ADMIN_ALLOW_ANY`. Delete those lines (and
`RL_LOGIN_*` if set), then `docker compose up -d --build`. Invitations and the
event log stay where they are; nothing to migrate.

## Deploying the console (after the gate)

1. `ADMIN_OIDC_SECRET` in `identity.env` (a realm imported before PR 15
   needs the `avedeus-admin` client and the "admin console browser" flow
   added in Keycloak's admin console with the same settings as the realm
   file).
2. Put yourself in the `admins` group in Keycloak.
3. `infrastructure/registration/admin.env` from `admin.env.example`
   (`chmod 600`).
4. `broker.env`: `ADMIN_TOKEN` (same as `CONTROL_PLANE_TOKEN` in admin.env),
   `ADMIN_CLIENTS=127.0.0.1,172.30.240.14`, `KEYCLOAK_ISSUER`.
   `.env`: `ADMIN_CONSOLE_TOKEN` (same as `SIGNUP_CONSOLE_TOKEN` in
   admin.env; a different value from every other token).
5. `docker compose up -d broker registration` and
   `docker compose --profile identity up -d admin-console`.
6. In `site.identity.custom`, add your address to the `/console/` location's
   `allow` line, copy it to mailcow and restart nginx.

## Proven in code

- **PR 16:** unit tests with fakes for every lifecycle operation (refusals,
  idempotent replays, a failure part-way then a retry, deletion waiting for
  the grace period and a newer backup, stopped when reactivated in mailcow),
  the console's account page (CSRF, typed confirmation, key per form) and the
  break-glass CLI; Keycloak's disable, sign-out and required action against a
  real Keycloak in CI.
- Unit tests with fakes: the console (sign-in, refusal without the group or
  the code, IP gate, CSRF, link shown once, idle timeout, sign-out), the
  sign-up app's internal API (token, address, refused through nginx, off
  without a token, event log names the admin), and the control plane's admin
  checks (wrong client, group, missing code, bad signature; break-glass).
- **Against a real Keycloak 26.8.2** in CI: the `avedeus-admin` flow makes a
  new admin set up a code, that first token is refused by the control plane,
  the next sign-in asks for password and code again, and the control plane
  accepts that token and records the admin. The realm smoke test checks the
  client's exact redirect.

Not proven: anything on the server (nginx, addresses, the console reaching
the sign-up app on `avedeus-edge`).

## Removal test (ADR 0017)

Stop the console and remove its nginx location and `ADMIN_CONSOLE_TOKEN`:
invitations work from the CLI, account lifecycle and the trace from the broker
CLI, mailboxes and users from mailcow's and Keycloak's consoles. Nothing else changes.
