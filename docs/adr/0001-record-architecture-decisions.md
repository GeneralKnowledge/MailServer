# 0001. Record architecture decisions

- Status: Accepted (2026-11-15)
- Implemented: Yes. Proven on Oracle: n/a

## Context

The integration plan (adopted 2026-11-15) fixes an architecture that many
later PRs depend on. Decisions made in chat or in a plan document are easy to
lose or quietly reverse.

## Decision

Architecture decisions live in `docs/adr/`, one numbered file each, with
these headings: Context, Decision, Consequences, and (when relevant)
Alternatives rejected. A decision is changed by a new ADR that supersedes the
old one, never by editing an accepted ADR's decision.

Each ADR carries two separate lines: **Implemented** (is the code following
it?) and **Proven on Oracle** (has it been checked on the real server?).

Open questions get an ADR with status **Open**, the options, and a
recommendation. They are decided by the owner (Reuben), not by a PR.

## Consequences

- A PR that departs from an accepted ADR names the ADR in its description and
  either justifies the exception or adds a superseding ADR.
- The ADR index in [README.md](README.md) is updated in the same PR.
