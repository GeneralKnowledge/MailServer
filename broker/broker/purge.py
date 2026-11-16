"""The deletion job (PR 16, ADR 0021): removes accounts whose deletion is due.

    docker compose exec -T broker python -m broker.purge [--dry-run]

Run daily by scripts/purge-deleted.sh, after the nightly backup. For each
scheduled deletion it removes the account only when all of these hold:

- the grace period has passed (DELETION_GRACE_DAYS, default 30);
- a backup that *started after* the deletion was scheduled has completed
  (scripts/backup.sh records it with `python -m broker.backup --mark-complete`),
  so the last copy of the mailbox is in a backup;
- the mailbox is still suspended in mailcow (someone reactivating it in
  mailcow's own admin stops the deletion, and it is reported).

Then the mailbox (mailcow) and the identity (Keycloak) are deleted, the link
and the schedule row are removed, and one `account.purge` audit event is
written per account. Nothing else is deleted; nothing here is reachable over HTTP.

Exit 0: nothing failed (accounts may still be waiting). 1: a deletion failed
or was stopped. 2: could not run.
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import load_settings
from .keycloak import KeycloakClient, KeycloakError
from .mailcow import MailcowClient, MailcowError
from .store import Store


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


def run(mc: MailcowClient, kc: KeycloakClient | None, store: Store, *, now: float, dry_run: bool = False,
        out=print) -> int:
    backup = store.latest("backup.completed")
    backup_started = float((backup or {}).get("counts", {}).get("started_at", 0))
    counts = {"due": 0, "deleted": 0, "waiting": 0, "waiting_for_backup": 0, "stopped": 0, "failed": 0}
    for d in store.deletions():
        addr = d["address"]
        if d["due_at"] > now:
            counts["waiting"] += 1
            out(f"  waiting  {addr}: due {_day(d['due_at'])}")
            continue
        counts["due"] += 1
        if backup_started <= d["scheduled_at"]:
            counts["waiting_for_backup"] += 1
            out(f"  waiting  {addr}: due, but no backup has completed that started after {_day(d['scheduled_at'])}")
            continue

        def event(outcome: str, error_kind: str | None = None, adapter: str = mc.name) -> None:
            if not dry_run:
                store.audit(caller="system", action="account.purge", outcome=outcome, operation=None,
                            address=addr, error_kind=error_kind, adapter=adapter)

        try:
            info = mc.mailbox_info(addr)
        except MailcowError as e:
            counts["failed"] += 1
            event("failed", e.kind)
            out(f"  FAILED   {addr}: mailcow: {e.detail}")
            continue
        if info is not None and info["state"] == "active":
            counts["stopped"] += 1
            event("refused", "conflict")
            out(f"  STOPPED  {addr}: the mailbox was reactivated outside the control plane; "
                "cancel the deletion or schedule it again")
            continue
        uid = store.link_get(addr, kc.name) if kc else None
        if dry_run:
            out(f"  would delete {addr}" + (" and its Keycloak user" if uid else ""))
            continue
        try:
            if info is not None:
                mc.delete_mailbox(addr)
        except MailcowError as e:
            counts["failed"] += 1
            event("failed", e.kind)
            out(f"  FAILED   {addr}: mailcow: {e.detail}")
            continue
        try:
            if uid:
                kc.delete_user(uid)
        except KeycloakError as e:
            # The mailbox is gone; the row stays, so the next run deletes the identity.
            counts["failed"] += 1
            event("failed", e.kind, "keycloak")
            out(f"  FAILED   {addr}: mailbox deleted, Keycloak user not: {e.detail} (the next run retries)")
            continue
        if uid:
            store.link_delete(addr, kc.name)
            store.places_forget(uid)   # PR 26: the networks they signed in from
        store.deletion_remove(addr)
        counts["deleted"] += 1
        event("ok", adapter="keycloak" if uid else mc.name)
        out(f"  deleted  {addr}")
    if not dry_run:
        store.audit(caller="system", action="purge.report", outcome="failed" if counts["failed"] or counts["stopped"]
                    else "ok", counts=counts)
    out("purge: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()))
    return 1 if counts["failed"] or counts["stopped"] else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m broker.purge", description="Delete accounts whose deletion is due.")
    p.add_argument("--dry-run", action="store_true", help="say what would be deleted, change nothing")
    a = p.parse_args(argv)
    s = load_settings()
    mc = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    kc = KeycloakClient(s.keycloak_url, s.keycloak_realm, s.keycloak_client_id, s.keycloak_client_secret) \
        if s.identity_provider == "keycloak" else None
    try:
        store = Store(s.db_path)
    except Exception as e:  # noqa: BLE001
        print(f"purge: control-plane database unavailable: {e}")
        return 2
    return run(mc, kc, store, now=time.time(), dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
