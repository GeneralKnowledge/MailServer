"""Basic reconciliation (ADR 0004, 0017): a simple scheduled job, report only.

Compares the mailboxes the control plane created (its audit log) with the
mailboxes mailcow has on the mail domain, writes one `reconcile.report`
audit event with the counts, and prints what differs. It fixes nothing.

    docker compose exec -T broker python -m broker.reconcile

Exit 0: no drift. Exit 1: drift (a mailbox we created is gone from mailcow).
Exit 2: could not run (mailcow or the database unavailable).

Mailboxes that exist in mailcow but were not created through the control
plane (for example postmaster, made in mailcow's UI) are listed as
"unmanaged". That is information, not drift, until account lifecycle moves
into the control plane (PR 16) and the Keycloak links exist (PR 12).
"""

from __future__ import annotations

import sys

from .config import load_settings
from .mailcow import MailcowClient, MailcowError
from .store import Store


def run(mc: MailcowClient, store: Store, domain: str, out=print) -> int:
    try:
        in_mailcow = mc.list_mailboxes(domain)
    except MailcowError as e:
        store.audit(caller="system", action="reconcile.report", outcome="failed", adapter=mc.name,
                    error_kind=e.kind)
        out(f"reconcile: could not list mailcow mailboxes: {e}")
        return 2
    created = {a.lower() for a in store.addresses("mailbox.create", "ok")}
    missing = sorted(created - in_mailcow)
    unmanaged = sorted(in_mailcow - created)
    store.audit(caller="system", action="reconcile.report", outcome="failed" if missing else "ok",
                adapter=mc.name, counts={"created_by_control_plane": len(created), "in_mailcow": len(in_mailcow),
                                         "missing_in_mailcow": len(missing), "unmanaged_in_mailcow": len(unmanaged)})
    out(f"reconcile: {len(created)} created by the control plane, {len(in_mailcow)} in mailcow on {domain}")
    for a in missing:
        out(f"  DRIFT missing in mailcow: {a}")
    for a in unmanaged:
        out(f"  unmanaged (not created through the control plane): {a}")
    return 1 if missing else 0


def main() -> int:
    s = load_settings()
    mc = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    try:
        store = Store(s.db_path)
    except Exception as e:  # noqa: BLE001
        print(f"reconcile: control-plane database unavailable: {e}")
        return 2
    return run(mc, store, s.mail_domain)


if __name__ == "__main__":
    sys.exit(main())
