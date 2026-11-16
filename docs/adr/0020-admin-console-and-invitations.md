# 0020. Admin console split; invitations stay in the sign-up app

- Status: **Accepted** (2026-11-15, PR 15). The owner chose where invitations
  live on 2026-11-15 ("Stay in the sign-up app"), against the recommended
  option of moving them into the control plane.
- Implemented: Yes in code (`admin/`, `registration/app/main.py` internal
  API, `broker/broker/main.py` admin checks, `avedeus-admin` in the realm;
  the Keycloak part checked against a real Keycloak in CI). Proven on Oracle:
  No (not deployed; the console needs Keycloak, ADR 0006 gate).

## Context

[ADR 0010](0010-no-public-lifecycle-admin-separate.md) moves the admin pages
out of the public sign-up app into their own container, behind the Keycloak
admin group, a second factor and an IP allow-list. The invitations those
pages manage live in the sign-up app's own SQLite database, and databases
are not shared ([ADR 0007](0007-no-shared-databases.md)). The roadmap left
open whether invitations move into the control plane or the sign-up app
exposes them through an internal API.

## Decision

1. **Invitations stay in the sign-up app.** It keeps its database, the
   two-phase claim, and its event log. It gains a small **internal API**
   (`/internal/v1/invitations`: list with recent sign-up failures, create,
   revoke, extend) for the admin console only:
   - outside the public path (`/join/`), so mailcow's nginx never forwards it;
   - refused if the request came through nginx (an `X-Forwarded-For` header);
   - only from the console's fixed address (`172.30.240.14`,
     `ADMIN_CONSOLE_CLIENTS`) and only with the console's own token
     (`ADMIN_CONSOLE_TOKEN`, different from every other credential);
   - off when no token is set;
   - the admin's Keycloak id (`X-Admin-Actor`) goes into the event log
     with each change.

   The invitation's secret token is returned once, at creation, and only its
   hash is stored, as before. The control plane still sees only the opaque
   `inv-<id>` reference in createAccount.
2. **The admin console** is its own container (`admin/`, `172.30.240.14`,
   compose profile `identity`) at **`/console/`**, not `/admin`: mailcow's own
   admin login already lives at `/admin` and must keep it
   ([ADR 0008](0008-no-changes-inside-mailcow.md)). This is a named deviation
   from the path in ADR 0010; the rest of ADR 0010 holds.
   - Sign-in is Keycloak, client `avedeus-admin` (code + PKCE), whose own
     browser flow is password **and** one-time code, both required, with no
     SSO-cookie shortcut and `prompt=login`, so every console sign-in asks
     again. Someone without a code is made to set one up first; that first
     sign-in does not count as two-factor, so they sign in once more.
   - The console lets in only members of `admins` whose ID token shows
     `otp` in `amr`, and only from `ADMIN_ALLOWED_IPS` (nginx's `/console/`
     location is closed by default as well).
   - It holds no upstream credential ([ADR 0003](0003-control-plane-owns-upstream-credentials.md)):
     its Keycloak client secret, its control-plane token and its sign-up
     internal-API token.
3. **The control plane checks the person behind every admin call.** The
   `admin` caller (token + address) must also send the admin's Keycloak
   access token (`X-User-Token`). The control plane verifies it like the
   portal's (signature from the realm keys, issuer, expiry) and also that it
   was issued to `avedeus-admin`, carries the `admins` group and has `otp` in
   `amr`. The admin's `sub` is the audit actor.

   **Break-glass:** from `127.0.0.1` (inside the broker container,
   `python -m broker.trace`) the admin token alone is enough, audited as actor
   `token/admin-break-glass`. This keeps the server usable without Keycloak
   and is reachable only with a shell on the server.
4. **New read-only control-plane operations for the console:** `listAccounts`
   (mailcow's mailboxes on the mail domain, read with the read-only key, plus
   whether each is linked to a Keycloak identity) and `readAudit` (recent
   events, filter by action prefix and outcome). `readAudit` is pulled forward
   from PR 28 in its smallest form; PR 28 adds filters by account and time.
   `getAccount` moves to PR 16 with the lifecycle operations.
5. **The admin pages leave the sign-up app.** Its password + TOTP login,
   admin sessions and `ADMIN_*` settings are removed; it refuses to start if
   old `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` / `ADMIN_TOTP_SECRET` /
   `ADMIN_ALLOWED_IPS` settings are still in its `.env`, so an upgrade can't
   silently keep a dead secret. Until Keycloak is deployed, invitations are
   managed with the sign-up app's CLI over SSH
   (`python -m app.cli create-invite | list | revoke | extend`).

## Consequences

- **The trade-off the owner accepted:** two experience apps talk to each
  other directly (console → sign-up app), not only through the control plane.
  Invitation changes are logged in the sign-up app's event log, not the
  control plane's audit log, so there are two logs to read; the console shows
  both. The sign-up app trusts the console's token and address for invitation
  changes: it does not verify the admin's Keycloak token itself (that would
  put JWT verification and a route to Keycloak into the public app). A
  compromised console could create invitations; it still could not create an
  account without someone redeeming one through createAccount, which keeps its
  daily cap.
- **Removal test ([ADR 0017](0017-minimum-complete-platform.md)):** remove the
  `admin/` container and `ADMIN_CONSOLE_TOKEN`, and the internal API is off;
  invitations work from the CLI; mailcow's and Keycloak's own admin consoles
  still manage mailboxes and users; the control plane's admin operations stay
  reachable by break-glass. Nothing else changes.
- **What already exists ([ADR 0016](0016-use-what-exists-first.md)):** mailcow's
  admin UI manages mailboxes (suspend, delete, quota, passwords, aliases) and
  Keycloak's admin console manages users, groups, sessions and sign-in events;
  the console links to both and rebuilds neither. The only new pages are the
  ones nothing upstream offers: invitations, the message trace and the
  control-plane audit log. The accounts list is read-only until PR 16.
- Without Keycloak there is no web admin until the ADR 0006 gate passes; the
  CLI covers invitations meanwhile. With one user on the test server, that is
  acceptable.
- If invitations ever move into the control plane after all, the console
  changes one client; the sign-up app keeps its public pages.
