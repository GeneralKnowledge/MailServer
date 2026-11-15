"""Admin command line, run inside the container:

    docker compose exec registration python -m app.cli <command>

Commands:
    hash-password            prompt for a password, print its argon2 hash (ADMIN_PASSWORD_HASH)
    new-totp [account]       print a new TOTP secret (ADMIN_TOTP_SECRET) and otpauth:// URI
    create-invite [--quota MB] [--days N] [--note TEXT]
    list                     list invitations
    revoke ID
    backup DEST              consistent copy of the SQLite database
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from .security import hash_password, new_totp_secret, totp_uri


def _db():
    from .db import Database
    return Database(os.environ.get("DATABASE_PATH", "/data/registration.sqlite3"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash-password")
    t = sub.add_parser("new-totp"); t.add_argument("account", nargs="?", default="admin")
    c = sub.add_parser("create-invite")
    c.add_argument("--quota", type=int, default=int(os.environ.get("DEFAULT_QUOTA_MB", 2048)))
    c.add_argument("--days", type=int, default=int(os.environ.get("INVITE_DAYS", 7)))
    c.add_argument("--note", default="")
    sub.add_parser("list")
    r = sub.add_parser("revoke"); r.add_argument("id", type=int)
    b = sub.add_parser("backup"); b.add_argument("dest")
    a = p.parse_args(argv)

    if a.cmd == "hash-password":
        pw = getpass.getpass("Admin password: ")
        if len(pw) < 14:
            print("Use at least 14 characters.", file=sys.stderr); return 1
        if pw != getpass.getpass("Repeat: "):
            print("Passwords differ.", file=sys.stderr); return 1
        print(hash_password(pw)); return 0
    if a.cmd == "new-totp":
        s = new_totp_secret()
        print(f"ADMIN_TOTP_SECRET={s}\n{totp_uri(s, a.account)}"); return 0

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
    elif a.cmd == "backup":
        db.backup_to(a.dest); print(f"backup written to {a.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
