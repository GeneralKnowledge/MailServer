"""Reconciliation (ADR 0004, 0017; PR 9, extended in PR 31): a simple
scheduled job that compares what mailcow, Keycloak and the control plane's
identity links say, reports drift into the one audit trail, and makes only
safe fixes.

    docker compose exec -T broker python -m broker.reconcile [--dry-run]

Checks:

- Mailboxes the control plane created (its audit log) still exist in mailcow.
  Mailboxes in mailcow it didn't create (postmaster, made in mailcow's UI) are
  "unmanaged": information, not drift. Accounts the deletion job removed
  (`account.purge`) are expected to be gone.
- With Keycloak (PR 31), for every identity link (mailbox <-> Keycloak user):
  the Keycloak user still exists; the mailbox still exists; the user's email
  is the mailbox address (mailcow finds users by email); and both sides agree
  on whether the person can sign in (a suspension half applied, Keycloak
  disabled but the mailbox open or the other way round, is drift).
- Mailboxes without a link, and Keycloak users without a mailbox, are listed.

The one fix it makes, unless --dry-run: a mailbox with no link whose address
is the email of exactly one Keycloak user that has no link either gets that
link recorded (`identity.link`, by "system"). That is the same match mailcow
itself makes, and it writes only the control plane's own record, nothing in
mailcow or Keycloak. Everything else is reported and left for an admin:
changing who can sign in or deleting anything is never automatic.

One `reconcile.report` audit event per run, with counts.
Exit 0: no drift. 1: drift. 2: could not run.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .keycloak import KeycloakClient, KeycloakError
from .mailcow import MailcowClient, MailcowError
from .store import Store

# Mailbox states (mailcow.STATES) in which the person can sign in.
OPEN = {"active"}


def run(mc: MailcowClient, store: Store, domain: str, out=print, kc: KeycloakClient | None = None,
        dry_run: bool = False) -> int:
    try:
        mailboxes = {m["address"]: m for m in mc.mailbox_summaries(domain)}
    except MailcowError as e:
        if not dry_run:
            store.audit(caller="system", action="reconcile.report", outcome="failed", adapter=mc.name,
                        error_kind=e.kind)
        out(f"reconcile: could not list mailcow mailboxes: {e}")
        return 2
    users: list[dict] | None = None
    if kc is not None:
        try:
            users = kc.list_users()
        except KeycloakError as e:
            if not dry_run:
                store.audit(caller="system", action="reconcile.report", outcome="failed", adapter=kc.name,
                            error_kind=e.kind)
            out(f"reconcile: could not list Keycloak users: {e.kind}: {e.detail}")
            return 2

    in_mailcow = set(mailboxes)
    made = store.last_seen("mailbox.create", "ok")
    for a, seq in store.last_seen("account.create", "ok").items():
        made[a] = max(seq, made.get(a, 0))
    purged = store.last_seen("account.purge", "ok")
    # Created and not deleted since (an address can be created again after a deletion).
    created = {a for a, seq in made.items() if purged.get(a, 0) < seq}
    missing = sorted(created - in_mailcow)
    unmanaged = sorted(in_mailcow - created)
    drift: list[str] = [f"missing in mailcow: {a}" for a in missing]
    info: list[str] = [f"unmanaged (not created through the control plane): {a}" for a in unmanaged]
    counts = {"created_by_control_plane": len(created), "in_mailcow": len(in_mailcow),
              "missing_in_mailcow": len(missing), "unmanaged_in_mailcow": len(unmanaged)}

    if users is not None:
        by_id = {u["id"]: u for u in users}
        links = {a.lower(): uid for a, uid in store.links(kc.name).items()}
        linked_ids = set(links.values())
        deletions = {d["address"] for d in store.deletions()}
        k = {"identity_gone": 0, "link_without_mailbox": 0, "email_differs": 0, "access_differs": 0,
             "linked_now": 0, "mailbox_without_identity": 0, "identity_without_mailbox": 0}
        for addr, uid in sorted(links.items()):
            u = by_id.get(uid)
            if u is None:
                k["identity_gone"] += 1
                drift.append(f"{addr}: linked Keycloak user {uid} no longer exists")
                continue
            box = mailboxes.get(addr)
            if box is None:
                if addr.endswith("@" + domain.lower()):
                    k["link_without_mailbox"] += 1
                    drift.append(f"{addr}: linked to Keycloak user {u['username']}, but the mailbox is gone")
                continue
            if u["email"] != addr:
                k["email_differs"] += 1
                drift.append(f"{addr}: its Keycloak user's email is {u['email'] or 'empty'} "
                             "(mailcow finds people by email)")
            mailbox_open = box["state"] in OPEN
            if u["enabled"] != mailbox_open:
                k["access_differs"] += 1
                drift.append(f"{addr}: Keycloak {'enabled' if u['enabled'] else 'disabled'} but mailbox "
                             f"{box['state']}" + (" (deletion scheduled)" if addr in deletions else "")
                             + "; suspend or reactivate it again in the admin console")
        # Unlinked mailboxes: link the one unambiguous match (safe), list the rest.
        free_by_email: dict[str, list[dict]] = {}
        for u in users:
            if u["id"] not in linked_ids and u["email"]:
                free_by_email.setdefault(u["email"], []).append(u)
        for addr in sorted(in_mailcow - set(links)):
            match = free_by_email.get(addr, [])
            if len(match) == 1:
                u = match[0]
                if dry_run:
                    info.append(f"{addr}: would link to Keycloak user {u['username']} (same email)")
                else:
                    store.link_set(addr, kc.name, u["id"])
                    store.audit(caller="system", action="identity.link", outcome="ok", address=addr,
                                adapter=kc.name)
                    info.append(f"{addr}: linked to Keycloak user {u['username']} (same email; fixed)")
                k["linked_now"] += 1
                linked_ids.add(u["id"])
            else:
                k["mailbox_without_identity"] += 1
                info.append(f"{addr}: no Keycloak identity" +
                            (f" ({len(match)} users share this email; link by hand)" if match else ""))
        for u in users:
            if u["id"] not in linked_ids:
                k["identity_without_mailbox"] += 1
                info.append(f"Keycloak user {u['username']} ({u['email'] or 'no email'}) has no mailbox")
        counts.update(k)
        counts["keycloak_users"] = len(users)

    if not dry_run:
        store.audit(caller="system", action="reconcile.report", outcome="failed" if drift else "ok",
                    adapter=mc.name, counts=counts)
    out(f"reconcile: {len(created)} created by the control plane, {len(in_mailcow)} in mailcow on {domain}"
        + (f", {len(users)} people in Keycloak" if users is not None else ""))
    for d in drift:
        out(f"  DRIFT {d}")
    for i in info:
        out(f"  {i}")
    return 1 if drift else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m broker.reconcile",
                                description="Report drift between mailcow, Keycloak and the control plane.")
    p.add_argument("--dry-run", action="store_true", help="report only: record nothing, fix nothing")
    a = p.parse_args(argv)
    s = load_settings()
    mc = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    kc = KeycloakClient(s.keycloak_url, s.keycloak_realm, s.keycloak_client_id, s.keycloak_client_secret) \
        if s.identity_provider == "keycloak" else None
    try:
        store = Store(s.db_path)
    except Exception as e:  # noqa: BLE001
        print(f"reconcile: control-plane database unavailable: {e}")
        return 2
    return run(mc, store, s.mail_domain, kc=kc, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
