"""Admin command line, run inside the container:

    docker compose exec registration python -m app.cli <command>

Commands (the admin pages moved to the admin console in PR 15; these work
without it, e.g. while Keycloak is not deployed):
    create-invite [--quota MB] [--days N] [--note TEXT]
    create-invites NAME...   one invitation per name (PR 17), same --quota/--days
    list                     list invitations
    reissue ID [--days N]    a new link for an unused invitation; the old one stops working (PR 17)
    revoke ID
    extend ID --days N
    backup DEST              consistent copy of the SQLite database
"""

from __future__ import annotations

import argparse
import os
import sys


def _db():
    from .db import Database
    return Database(os.environ.get("DATABASE_PATH", "/data/registration.sqlite3"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-invite")
    c.add_argument("--quota", type=int, default=int(os.environ.get("DEFAULT_QUOTA_MB", 2048)))
    c.add_argument("--days", type=int, default=int(os.environ.get("INVITE_DAYS", 7)))
    c.add_argument("--note", default="")
    m = sub.add_parser("create-invites")
    m.add_argument("names", nargs="+")
    m.add_argument("--quota", type=int, default=int(os.environ.get("DEFAULT_QUOTA_MB", 2048)))
    m.add_argument("--days", type=int, default=int(os.environ.get("INVITE_DAYS", 7)))
    ri = sub.add_parser("reissue"); ri.add_argument("id", type=int)
    ri.add_argument("--days", type=int, default=int(os.environ.get("INVITE_DAYS", 7)))
    sub.add_parser("list")
    r = sub.add_parser("revoke"); r.add_argument("id", type=int)
    e = sub.add_parser("extend"); e.add_argument("id", type=int); e.add_argument("--days", type=int, required=True)
    b = sub.add_parser("backup"); b.add_argument("dest")
    a = p.parse_args(argv)

    from .invites import Invitations
    db = _db(); inv = Invitations(db)
    public = os.environ.get("PUBLIC_URL") or f"https://mail.{os.environ.get('MAIL_DOMAIN', 'example.com')}"
    base = public.rstrip("/") + "/" + os.environ.get("BASE_PATH", "/join").strip("/")
    if a.cmd in ("create-invite", "create-invites"):
        max_quota = int(os.environ.get("MAX_QUOTA_MB", 10240))
        if not 1 <= a.quota <= max_quota:
            print(f"Quota must be 1-{max_quota} MB (MAX_QUOTA_MB).", file=sys.stderr); return 1
        if not 1 <= a.days <= 90:
            print("Expiry must be between 1 and 90 days", file=sys.stderr); return 1
        notes = [a.note] if a.cmd == "create-invite" else a.names
        if len(notes) > 50:
            print("At most 50 invitations at once.", file=sys.stderr); return 1
        for note in notes:
            token, i = inv.create(quota_mb=a.quota, days=a.days, note=note, ip="cli")
            print(f"Invitation #{i.id}{f' ({i.note})' if i.note else ''}\nLink: {base}/i/{token}\nCode: {token}")
    elif a.cmd == "reissue":
        try:
            token = inv.reissue(a.id, a.days, ip="cli")
        except ValueError as err:
            print(err, file=sys.stderr); return 1
        if token is None:
            print("not reissuable (redeemed, revoked, in progress or unknown)", file=sys.stderr); return 1
        print(f"Invitation #{a.id}: the old link no longer works.\nLink: {base}/i/{token}\nCode: {token}")
    elif a.cmd == "list":
        for i in inv.list():
            print(f"#{i.id:<4} {i.status:<11} {i.hint}…  {i.quota_mb}MB  {i.redeemed_address or ''}  {i.note}")
    elif a.cmd == "revoke":
        print("revoked" if inv.revoke(a.id, ip="cli") else "not revocable"); return 0
    elif a.cmd == "extend":
        try:
            print("extended" if inv.extend(a.id, a.days, ip="cli") else "not extendable")
        except ValueError as err:
            print(err, file=sys.stderr); return 1
    elif a.cmd == "backup":
        db.backup_to(a.dest); print(f"backup written to {a.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
