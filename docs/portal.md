# Account portal (PR 14)

> **Implemented in code; not proven on server; not deployed.** The portal
> needs Keycloak, so it waits for the
> [ADR 0006 gate](adr/0006-keycloak-identity-provider.md#validation-gate-before-deployment-added-2026-11-15)
> like everything in [identity.md](identity.md).

`https://<mail host>/account/` is one place for a person's account. It signs
them in with Keycloak and shows their address, quota and mailbox state. For
everything else it **links** to the place that already does it well
([ADR 0016](adr/0016-use-what-exists-first.md)); it rebuilds nothing:

| Need | Where the link goes |
|---|---|
| Mail apps (first step of onboarding): create an app password | mailcow's user page (`/user`, App passwords) |
| Apple setup profile, CalDAV/CardDAV | mailcow's user page |
| Password, two-factor | Keycloak account console, Signing in |
| Where you're signed in, sign out other devices | Keycloak account console, Device activity |
| Recent mail-app logins | mailcow's user page |
| Spam strictness, senders always allowed or blocked (PR 20) | mailcow's user page, *Spam filter* tab |
| Quarantine and how often to be told about it (PR 20) | mailcow's user page, *Settings* |
| Remote images in webmail (PR 20) | Explained in place: off by default (SOGo's own default, `never`); SOGo Preferences → Mail → *Display remote inline images* |
| Mail, contacts, calendar, filters, vacation | SOGo (webmail) |

Onboarding puts the app password first, because with one identity the
account password only works in the browser ([ADR 0015](adr/0015-mail-client-passwords.md)).

## How it works

- Sign-in: OpenID Connect authorization code with PKCE (S256), state and
  nonce, as the confidential client `avedeus-portal` (realm file; exact
  redirect `https://<host>/account/callback`). The code is exchanged directly
  with Keycloak on `avedeus-edge`.
- Tokens stay on the server, in memory; the cookie holds only a random session
  id and the login handshake, signed. A restart signs everyone out; there is
  nothing to back up. Expired access tokens are refreshed; sign-out ends the
  Keycloak session too.
- Account data comes from the control plane's `GET /v1/me` as the `portal`
  caller (its own token, from `172.30.240.13` only) **plus the user's access
  token**. The control plane checks the token's signature against the realm's
  keys, its issuer, expiry and that it was issued to the portal, and finds
  the mailbox through the identity link. No request field can name another
  user.
- It holds no upstream credential (ADR 0003): only its control-plane token
  and its Keycloak client secret.

## Proven in code

- Unit tests with fakes (portal: sign-in, PKCE, state/nonce/expiry refusals,
  refresh, sign-out, headers; control plane: forged, expired, wrong-issuer,
  wrong-client, ID-token and HS256 tokens refused, key rotation without a
  refetch storm).
- **End to end against a real Keycloak 26.8.2** in CI: createAccount makes
  the user, a browser signs in on Keycloak's own login page, the portal
  exchanges the code, and the control plane accepts the real access token.
  mailcow is still a fake there.

## Deploying (after the gate)

1. `PORTAL_OIDC_SECRET` in `identity.env` (the realm client's secret; a
   realm imported before PR 14 needs the client added in the admin console
   with the same settings as the realm file).
2. `infrastructure/registration/portal.env` from `portal.env.example`;
   `PORTAL_TOKEN`, `PORTAL_CLIENTS=172.30.240.13` and `KEYCLOAK_ISSUER` in
   `broker.env`; restart the broker.
3. `docker compose --profile identity up -d portal` in
   `infrastructure/registration`.
4. The `/account/` location is already in `site.identity.custom`.

## Removal test (ADR 0017)

Stop the portal and remove its nginx location: every linked feature still
works in mailcow, SOGo and Keycloak directly. Only the overview page and
quota display go away.
