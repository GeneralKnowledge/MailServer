# 0018. Initial mail domain is a subdomain; the root domain stays untouched

- Status: **Accepted** (2026-11-15, owner). Exact host names: see below.
- Implemented: No (server setup). Proven on Oracle: No

## Context

`avedeus.ovh` currently has MX records pointing at OVH mail. Moving the root
domain's mail to the new server during initial setup would combine a new
server, new DNS and a migration in one step, with a hard rollback.

## Decision

- Mailboxes start on a **subdomain**, initially `@mail.avedeus.ovh`.
- The root `avedeus.ovh` mail configuration (MX, SPF, existing OVH mail) is
  **left untouched** during initial deployment.
- Moving to `@avedeus.ovh` is a **later, separate decision**, taken only after
  delivery, reputation and migration have been tested on the subdomain.

## Open point: the server's hostname

mailcow expects its hostname (`MAILCOW_HOSTNAME`) not to be a mail domain
(its setup prompt says so, and watchdog mail and bounces are sent from that
name). The setup docs use `mail.avedeus.ovh` as the hostname. Either the
hostname changes (for example `mx.avedeus.ovh`, with webmail still reachable
at `mail.avedeus.ovh` through `ADDITIONAL_SERVER_NAMES`) or the address
domain uses another subdomain. The owner chooses; the answer is recorded
here and server setup waits for it.

## Consequences

- Independent DNS and MX testing on the subdomain; rollback is removing the
  subdomain's records.
- The setup guide and DNS docs are updated by the server-setup work once the
  host names are final.
- Native aliases, when used, live on the same mail domain
  ([0013](0013-alias-domain.md) closed as non-blocking).
