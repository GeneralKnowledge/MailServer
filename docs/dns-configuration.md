# DNS configuration

Fill in once the domain is known and its current records have been checked
with `scripts/m0-inspect.sh`. **Read existing records before changing any.**

| Name | Type | Value | Stage |
|---|---|---|---|
| `mail` | A | reserved public IPv4 | M1 |
| PTR (Oracle service request) | PTR | `mail.example.com` | M1 |
| `@` | MX | `10 mail.example.com` | M2, after go-ahead |
| `@` | TXT | `v=spf1 mx -all` (merge into any existing SPF; add the relay's `include:` if a relay is used) | M2 |
| `dkim._domainkey` | TXT | from mailcow: Configuration → ARC/DKIM keys (2048-bit) | M2 |
| `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:postmaster@example.com` → `p=quarantine` after tests pass | M2 |
| `autodiscover` | CNAME | `mail.example.com` | M2 |
| `autoconfig` | CNAME | `mail.example.com` | M2 |
| `_autodiscover._tcp` | SRV | `0 1 443 mail.example.com` | M2 |
| `_submissions._tcp` | SRV | `0 1 465 mail.example.com` | M2 (optional) |
| `_imaps._tcp` | SRV | `0 1 993 mail.example.com` | M2 (optional) |
| `@` | CAA | `0 issue "letsencrypt.org"` (only if CAA is used) | M1 |

mailcow's own DNS check (Configuration → Domains → DNS) shows the exact
values it expects; use it as the final check.

If DNS is on Cloudflare, these records must be **DNS only** (grey cloud).

## Changing MX for a domain already in use

Changing MX moves all incoming mail for the domain to this server. Before
doing it: list existing addresses at the old provider, create matching
mailboxes or aliases in mailcow (or migrate them with mailcow's Sync Jobs),
lower the MX TTL to 300 a day ahead, then switch.
