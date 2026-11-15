# 0015. Mail-client passwords after Keycloak (E5)

- Status: **Open**. Needed by PR 12 (one identity).
- Implemented: No. Proven on Oracle: No

## Question

Once Keycloak is the identity provider, how do IMAP/SMTP/CalDAV clients
(phones, Thunderbird) authenticate?

## Options

| Option | For | Against |
|---|---|---|
| A. Keycloak "Mailpassword Flow" (one password everywhere), app passwords still available (recommended in the plan) | Simplest for users; offboarding in Keycloak covers mail clients | The Keycloak password is typed into mail clients, which cannot do MFA |
| B. App passwords only for mail clients | Main password and MFA never leave the browser; a leaked client password is revocable alone | Every user must create app passwords; more support |

## Decision

Not decided. PR 12 cannot be completed until the owner decides; it may be
built against fakes up to that point without assuming an answer.
