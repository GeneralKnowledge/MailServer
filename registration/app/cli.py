"""Admin command line, run inside the container:

    docker compose exec registration python -m app.cli <command>

Commands (the admin pages moved to the admin console in PR 15; these work
without it, e.g. while Keycloak is not deployed):
    create-invite [--quota MB] [--days N] [--note TEXT]
    list                     list invitations
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
    sub.add_parser("list")
    r = sub.add_parser("revoke"); r.add_argument("id", type=int)
    e = sub.add_parser("extend"); e.add_argument("id", type=int); e.add_argument("--days", type=int, required=True)
    b = sub.add_parser("backup"); b.add_argument("dest")
    a = p.parse_args(argv)

    from .invites import Invitations
    db = _db(); inv = Invitations(db)
    if a.cmd == "create-invite":
        max_quota = int(os.environ.get("MAX_QUOTA_MB", 10240))
        if not 1 <= a.quota <= max_quota:
            print(f"Quota must be 1-{max_quota} MB (MAX_QUOTA_MB).", file=sys.stderr); return 1
        try:
            token, i = inv.create(quota_mb=a.quota, days=a.days, note=a.note, ip="cli")
        except ValueError as e:
            print(e, file=sys.stderr); return 1
        public = os.environ.get("PUBLIC_URL") or f"https://mail.{os.environ.get('MAIL_DOMAIN', 'example.com')}"
        base = public.rstrip("/") + "/" + os.environ.get("BASE_PATH", "/join").strip("/")
        print(f"Invitation #{i.id}\nLink: {base}/i/{token}\nCode: {token}")
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
