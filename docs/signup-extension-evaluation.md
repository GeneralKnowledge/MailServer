# Evaluation: MediaLab Works "Sign-up for mailcow"

Evaluated 2026-11-01 from the vendor's public pages:
[product](https://medialab.works/products/mailcow-signup),
[guide](https://medialab.works/products/mailcow-signup/guide),
[changelog](https://medialab.works/products/mailcow-signup/changelog),
[EULA](https://medialab.works/legal/eula), [pricing](https://medialab.works/pricing).
No public source repository was found.

## Findings

| Criterion | Finding |
|---|---|
| Licence | EULA s.11 says the mailcow tools are GPL-3.0 and you receive full source. But download needs a paid licence or a 30-day trial (card required), and the code checks the licence against medialab.works at start-up and daily. Without a valid licence, "registrations close" (7-day offline grace). |
| Price | About €1/month, €10.20/year, or €25.20 "lifetime" (1 year of updates), per mailcow server. Cheap. (Prices taken from a truncated pricing page; confirm at checkout.) |
| Maintenance | Sign-up first shipped in toolkit 0.4.0 on 2026-10-13; latest toolkit 0.8.0 on 2026-10-22. About three weeks old. Sole-trader vendor (Romania). |
| mailcow version support | Not stated. Only "mailcow-dockerized (Linux, docker compose, systemd)". |
| ARM64 | Not stated. It is PHP/JS/shell running inside mailcow's own php-fpm container, so it is probably architecture-neutral, but unverified. |
| Invitations | Codes with use count (1 = single-use), expiry, enable/disable (revocation), and a per-code usage count. No per-redemption log found. |
| mailcow integration | Installs **into** mailcow's tree (`data/web/mlw-signup`), adds tables `mlw_su_*` to mailcow's MariaDB, and creates accounts "with mailcow's own functions". It does **not** use the mailcow REST API. A host systemd timer runs every 5 minutes. |
| Security | Rate limits per IP/day, attempts per code, honeypot/minimum-fill-time, disposable-address blocking, optional admin approval, e-mail confirmation. No captcha documented. |
| Deployment complexity | Low: `install.sh` plus settings in the mailcow UI. |

## Fit against the brief

| Brief requirement | Met? |
|---|---|
| Single-use, 7-day expiry, revocable invitations | Yes |
| Admin can see if an invitation was redeemed | Partly (usage count, not who redeemed) |
| Provision through supported mailcow APIs; never touch mailcow's DB directly | **No.** Uses mailcow internals and writes its own tables into mailcow's DB |
| Deployed independently so mailcow updates don't overwrite it | **No.** Lives inside the mailcow web root |
| Registration app must not have unrestricted admin access | **No.** Runs inside mailcow with full internal privileges |
| Mature, maintained, low risk | **No.** Three weeks old, single vendor, licence phone-home can close registration |
| Low cost | Yes |

## Recommendation

**Do not use it. Build the small independent FastAPI registration app in M3.**

It is well featured and inexpensive, but it breaks three explicit rules of the
brief (supported API only, isolation from mailcow, restricted access) and adds
an external licence dependency that can switch registration off. A separate
app that only calls mailcow's `/api/v1/add/mailbox` and `/api/v1/get/mailbox`
with a restricted, IP-allow-listed API key is a few hundred lines of Python and
keeps mailcow upgrades independent.

Re-evaluate in M3 only if the vendor publishes an API-based, standalone
version with a mailcow compatibility matrix.
