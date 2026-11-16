# 0014. Who owns mail rules (E4)

- Status: **Accepted** (2026-11-15, owner): option B, SOGo owns user-facing
  mail rules.
- Implemented: Yes, trivially: nothing of ours writes Sieve. Proven on
  Oracle: No (SOGo's filter, vacation and forwarding UI to be checked in the
  M0–M4 rehearsal).

## Question

Given [0012](0012-one-sieve-rule-owner.md) (one writer per script), which
tool owns users' filters and vacation replies?

## Options

| Option | For | Against |
|---|---|---|
| A. Avedeus visual editor, SOGo's Sieve and vacation UI turned off | One editor across all apps; rules through the control plane and audit log | We build and maintain an editor SOGo already has; scripts must be imported; users lose SOGo's familiar UI |
| B. SOGo stays the owner; Avedeus offers no rule editor (**chosen**) | No new code or service; one filtering interface; SOGo already has a point-and-click filter editor, vacation and forwarding | Rules stay outside the control plane's audit log |

## Decision

SOGo is the sole owner of user-facing mail rules, forwarding and vacation
settings. Avedeus links to SOGo's editor and explains how to use it. There is
no second rule engine or competing editor.

## Consequences

- PR 23 (rule engine), PR 24 (visual editor) and PR 25 (forwarding and
  vacation) are dropped from the roadmap.
- [0012](0012-one-sieve-rule-owner.md)'s interim is now permanent: SOGo is the
  only writer of users' active Sieve scripts. Admin pre/post filters stay
  admin-only through the mailcow API.
- Reason: the [gap audit](../mailcow-gap-audit.md) found SOGo already covers
  this, and [0016](0016-use-what-exists-first.md) says use what exists first.
