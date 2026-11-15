# 0010. No public mailbox lifecycle; admin console in its own container

- Status: Accepted (2026-11-15). Locked constraint 2; plan decision E6.
- Implemented: Partly. No lifecycle operation exists anywhere yet (the broker
  offers only check and create). The invite admin pages still live in the
  registration app, behind TOTP and `ADMIN_ALLOWED_IPS`, until PR 15.
  Proven on Oracle: No

## Context

The owner's rule, confirmed by the audit: password reset, suspension and
deletion must never be reachable through the public registration app.

## Decision

- Password reset, suspension, reactivation, quota change and deletion are
  **admin-only control-plane operations**. The `signup` caller can never be
  granted them.
- The admin console moves to its **own container** at `/admin` (PR 15), with
  its own control-plane credential, requiring the Keycloak admin group, TOTP
  and an allowed IP. The public sign-up app then keeps only sign-up.
- Users change their own password and MFA in Keycloak's account console, not
  through an Avedeus lifecycle endpoint.

## Consequences

- PR 16 (account lifecycle) cannot start before PR 15.
- CI checks that the `signup` capability row never lists a lifecycle
  operation (`contracts/tests`).
