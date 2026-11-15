# 0014. Who owns mail rules (E4)

- Status: **Proposed** (2026-11-15): option B, SOGo stays the rule owner.
  The owner favours it; it becomes Accepted only when the owner approves this
  ADR. Until then no PR may assume either answer beyond what
  [0012](0012-one-sieve-rule-owner.md)'s interim already says.
- Implemented: No. Proven on Oracle: No

## Question

Given [0012](0012-one-sieve-rule-owner.md) (one writer per script), which
tool owns users' filters and vacation replies?

## Options

| Option | For | Against |
|---|---|---|
| A. Avedeus visual editor, SOGo's Sieve and vacation UI turned off (the original plan's recommendation) | One editor across all apps; rules go through the control plane and the audit log; enables M8 automation | We build and maintain an editor that SOGo already has; existing SOGo scripts must be imported; users lose SOGo's familiar UI |
| B. SOGo stays the owner; Avedeus offers no rule editor (**proposed**) | No new code or service; one filtering interface; SOGo already has a point-and-click filter editor, vacation and forwarding | Rules stay outside the control plane and its audit log; M8 automation shrinks to admin-level filters |

## Why B is proposed

The [mailcow feature-gap audit](../mailcow-gap-audit.md) found that SOGo
already lets users build filters, vacation replies and forwarding without
writing Sieve. Two competing filtering interfaces would add complexity for no
user gain, and [0016](0016-use-what-exists-first.md) says to use what exists
first. The real gap is discoverability, which the user guide and the portal's
links (PR 14) address.

## Consequences if accepted

- PR 23 (rule engine), PR 24 (visual editor) and PR 25 (forwarding and
  vacation) are dropped rather than deferred.
- 0012's interim becomes permanent: SOGo is the only writer of users' active
  Sieve scripts; admin pre/post filters stay admin-only through the mailcow
  API.

## Decision

Proposed, not yet accepted. The roadmap defers PR 23–25 in the meantime.
