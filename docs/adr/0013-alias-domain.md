# 0013. Alias domain (E3)

- Status: **Open**. Needed by PR 10 (alias back end).
- Implemented: No. Proven on Oracle: No

## Question

Which domain do user aliases live on?

## Options

| Option | For | Against |
|---|---|---|
| A. `avedeus.ovh` itself (recommended in the plan) | Nothing new in DNS; replies work through mailcow's sender ACL; aliases look like normal addresses | Aliases and real mailboxes share one namespace, so reserved names and collision checks matter more; a burned alias domain is the main domain |
| B. A subdomain, e.g. `alias.avedeus.ovh` | Separates alias reputation and namespace; needed anyway if SimpleLogin is ever adopted | Extra DNS (MX, SPF, DKIM, DMARC) and a second mailcow domain to maintain |

The plan recommends A, moving to a subdomain only if SimpleLogin is adopted.
Related open item in project memory: whether mail uses `@avedeus.ovh` at all
or a subdomain; that decision may settle this one.

## Decision

Not decided. PR 10 may be built against fakes with the alias domain as a
setting, but must not ship with a default that assumes an answer.
