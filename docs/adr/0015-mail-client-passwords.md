# 0015. Mail-client passwords after Keycloak (E5)

- Status: **Accepted** (2026-11-15, owner): option B, app passwords only.
- Implemented: No (PR 12). Proven on Oracle: No (test with real mail clients
  in the M0–M4 rehearsal).

## Question

Once Keycloak is the identity provider, how do IMAP/SMTP/CalDAV clients
(phones, Thunderbird) authenticate?

## Options

| Option | For | Against |
|---|---|---|
| A. Keycloak "Mailpassword Flow" (one password everywhere) | Simplest for users; Keycloak offboarding covers clients | The Keycloak password is typed into mail apps, which cannot do MFA |
| B. App passwords only for mail clients (**chosen**) | Main password and MFA never leave the browser; each app's credential is revocable on its own | Every user must create app passwords; more support |

## Decision

- **Keycloak** is for browser-based identity: the portal, admin console,
  mailcow UI and SOGo sign-in.
- **Mail clients use mailcow-generated, protocol-scoped app passwords**
  (IMAP, SMTP, POP3, DAV, EAS, Sieve), created by the user on mailcow's own
  user page. These are native to mailcow; we build nothing for them.
- The user's Keycloak password is never exposed to ordinary mail
  applications. Keycloak's Mailpassword Flow is not enabled.

## Consequences

- PR 12 configures mailcow's IdP without the Mailpassword Flow and checks that
  app passwords still work for IdP-linked mailboxes.
- Onboarding (the user guide, PR 14's links and the Apple setup profile "with
  app password") must make creating an app password the obvious first step
  for phones and desktop clients.
- Offboarding must also disable app passwords; PR 16 checks that mailcow's
  offboarding does this, as part of the existing session server check.
