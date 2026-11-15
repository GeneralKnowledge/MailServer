# 0012. Exactly one tool writes each user's Sieve rules

- Status: Accepted (2026-11-15)
- Implemented: Yes, trivially: nothing of ours writes Sieve today, so SOGo is
  the only writer. Proven on Oracle: No

## Context

Dovecot Pigeonhole is in mailcow (ManageSieve on 4190). Rules can live in a
user's active script, which SOGo writes (filters, vacation), or in mailcow's
admin-only per-mailbox pre/post filters. Two editors writing the same active
script overwrite each other.

## Decision

- At any time, exactly **one** tool writes a user's active Sieve script.
- Until [0014](0014-mail-rule-owner.md) is decided, that tool is SOGo, and no
  Avedeus code writes Sieve.
- Admin-level pre/post filters through the mailcow API are separate and stay
  admin-only.

## Consequences

PR 23–25 (rule engine, visual editor, forwarding and vacation) are deferred
while [0014](0014-mail-rule-owner.md) is proposed as "SOGo stays the owner".
If the owner accepts 0014, they are dropped; if not, PR 24 still cannot ship
until 0014 is decided.
