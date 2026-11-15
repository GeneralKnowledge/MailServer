# 0014. Who owns mail rules (E4)

- Status: **Open**. Needed by PR 23 (rule engine), blocks PR 24.
- Implemented: No. Proven on Oracle: No

## Question

Given [0012](0012-one-sieve-rule-owner.md) (one writer per script), which
tool owns users' filters and vacation replies?

## Options

| Option | For | Against |
|---|---|---|
| A. Avedeus visual editor, SOGo's Sieve and vacation UI turned off (recommended in the plan) | One editor across all apps; rules go through the control plane and the audit log; enables M8 automation | We build and maintain an editor; existing SOGo scripts must be imported; users lose SOGo's familiar UI |
| B. SOGo stays the owner; Avedeus offers no rule editor | No new code | Rules stay outside the control plane; M8 automation shrinks to admin-level filters |

## Decision

Not decided. Until it is, 0012's interim (SOGo is the only writer) holds.
