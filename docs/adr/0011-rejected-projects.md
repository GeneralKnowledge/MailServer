# 0011. Rejected projects and alternatives

- Status: Accepted (2026-11-15)
- Implemented: n/a. Proven on Oracle: n/a

A PR must not bring any of these back without naming this ADR and justifying
the change (review rule).

| Rejected | Instead | Why |
|---|---|---|
| **mailcow-rest-api** (jr551) | Our control plane | About six weeks old and pre-1.0, mostly AI-agent commits, one test file; stores mailbox passwords in reversible form; reads mailcow's MySQL directly; bundles a second webmail and LLM features. It widens the trust boundary the audit shrank. Ideas kept for M10: IP-scoped short-lived tokens, an MCP adapter. |
| **addy.io** | mailcow-native aliases | PHP/Laravel with MariaDB and Redis, wants to own Postfix and Rspamd, no OIDC/SSO, so a second account system. |
| **idmail** | mailcow-native aliases | Not a forwarder: an SQLite table the MTA must query (built for Stalwart); mailcow's Postfix cannot read it. Idea kept: SimpleLogin/addy-compatible endpoints. |
| **A second webmail** (Roundcube, SnappyMail) or **Nextcloud Groupware** | SOGo | Duplicates SOGo, adds RAM and another login. |
| **MediaLab "Sign-up for mailcow"** | Our own sign-up app | Runs inside mailcow, uses internals instead of the API, phones home for its licence. |
| **Caddy** or another TLS proxy | mailcow's nginx and ACME | A second certificate manager and port conflicts for no gain. |
| **cAdvisor** | node_exporter + mailcow exporter | Too CPU-hungry for two cores. |
| **Generic OIDC IdPs** (Authelia, Authentik, Zitadel, Pocket ID) | Keycloak | mailcow only creates mailboxes at first login with them; no sync, offboarding or Mailpassword Flow. Kanidm via LDAP remains a fallback spike only ([0006](0006-keycloak-identity-provider.md)). |
| **Per-integration mailcow RW keys as "least privilege"** | One RW key in the control plane | mailcow keys cannot be scoped to endpoints, so each RW key is full admin. |
