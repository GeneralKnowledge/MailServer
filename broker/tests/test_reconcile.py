"""PR 31: reconciliation across mailcow, Keycloak and the identity links."""

import uuid

import test_lifecycle
from test_lifecycle import adapters, seed

from broker import reconcile

actx = test_lifecycle.actx   # the lifecycle tests' fixture: mailcow and Keycloak fakes


def user(c, local, email=None, enabled=True):
    uid = str(uuid.uuid4())
    c.kc_fake.users[uid] = {"id": uid, "username": local, "email": email if email is not None else f"{local}@example.com",
                            "enabled": enabled, "requiredActions": []}
    return uid


def box(c, local, active=1):
    addr = f"{local}@example.com"
    c.fake.mailboxes[addr] = {"username": addr, "name": local, "quota": 1, "quota_used": 0, "active": active}
    return addr


def run(c, **kw):
    mc, kc = adapters(c)
    lines = []
    rc = reconcile.run(mc, c.store, "example.com", out=lines.append, kc=kc, **kw)
    return rc, "\n".join(lines), c.store.latest("reconcile.report", "ok" if rc == 0 else "failed")


def test_consistent_accounts_are_quiet(actx):
    uid = seed(actx)
    actx.kc_fake.users[uid]["email"] = "alice@example.com"
    rc, text, report = run(actx)
    assert rc == 0 and "DRIFT" not in text
    assert report["counts"]["keycloak_users"] == 1 and report["counts"]["access_differs"] == 0


def test_identity_gone_mailbox_gone_and_email_changed_are_drift(actx):
    gone = seed(actx, "gone")
    del actx.kc_fake.users[gone]
    nobox = seed(actx, "nobox")
    actx.kc_fake.users[nobox]["email"] = "nobox@example.com"
    del actx.fake.mailboxes["nobox@example.com"]
    moved = seed(actx, "moved")
    actx.kc_fake.users[moved]["email"] = "someone@else.org"
    rc, text, report = run(actx)
    assert rc == 1
    assert "gone@example.com: linked Keycloak user" in text and "no longer exists" in text
    assert "nobox@example.com: linked to Keycloak user nobox, but the mailbox is gone" in text
    assert "moved@example.com: its Keycloak user's email is someone@else.org" in text
    c = report["counts"]
    assert (c["identity_gone"], c["link_without_mailbox"], c["email_differs"]) == (1, 1, 1)


def test_half_applied_suspension_is_drift_and_not_fixed(actx):
    a = seed(actx, "a")
    actx.kc_fake.users[a].update(email="a@example.com", enabled=False)      # Keycloak disabled, mailbox open
    b = seed(actx, "b", active=2)
    actx.kc_fake.users[b]["email"] = "b@example.com"                          # Keycloak open, mailbox login off
    c = seed(actx, "c", active=2)
    actx.kc_fake.users[c].update(email="c@example.com", enabled=False)      # properly suspended
    rc, text, report = run(actx)
    assert rc == 1 and report["counts"]["access_differs"] == 2
    assert "a@example.com: Keycloak disabled but mailbox active" in text
    assert "b@example.com: Keycloak enabled but mailbox login_disabled" in text
    assert not [line for line in text.splitlines() if "DRIFT" in line and "c@example.com" in line]
    assert actx.kc_fake.users[a]["enabled"] is False and actx.fake.mailboxes["b@example.com"]["active"] == 2


def test_unlinked_mailbox_with_one_matching_identity_gets_linked(actx):
    addr = box(actx, "migrated")
    uid = user(actx, "migrated")
    rc, text, _ = run(actx, dry_run=True)
    assert "would link" in text and actx.store.link_get(addr, "keycloak") is None
    assert actx.store.latest("reconcile.report") is None                    # dry run records nothing
    rc, text, report = run(actx)
    assert rc == 0 and "linked to Keycloak user migrated (same email; fixed)" in text
    assert actx.store.link_get(addr, "keycloak") == uid
    ev = actx.store.latest("identity.link")
    assert ev["caller"] == "system" and ev["subject"] == {"address": addr}
    assert report["counts"]["linked_now"] == 1
    rc, text, report = run(actx)                                             # nothing left to do
    assert report["counts"]["linked_now"] == 0 and "Keycloak user migrated" not in text


def test_ambiguous_or_missing_identities_are_only_listed(actx):
    box(actx, "postmaster")
    twin = box(actx, "twin")
    user(actx, "twin1", email="twin@example.com")
    user(actx, "twin2", email="twin@example.com")
    user(actx, "admin-only", email="boss@elsewhere.org")
    rc, text, report = run(actx)
    assert rc == 0
    assert "postmaster@example.com: no Keycloak identity" in text
    assert "twin@example.com: no Keycloak identity (2 users share this email; link by hand)" in text
    assert "Keycloak user admin-only (boss@elsewhere.org) has no mailbox" in text
    assert actx.store.link_get(twin, "keycloak") is None
    assert report["counts"]["mailbox_without_identity"] == 2 and report["counts"]["identity_without_mailbox"] == 3


def test_keycloak_down_is_reported_not_guessed(actx):
    seed(actx)
    actx.kc_fake.down = True
    rc, text, _ = run(actx)
    assert rc == 2 and "could not list Keycloak users" in text
    assert actx.store.latest("reconcile.report", "failed")["adapter"] == "keycloak"


def test_users_are_paged_and_service_accounts_skipped(actx):
    for i in range(450):
        user(actx, f"p{i}", email=f"p{i}@elsewhere.org")
    user(actx, "service-account-mailcow", email="")
    _, kc = adapters(actx)
    people = kc.list_users()
    assert len(people) == 450 and not any(u["username"].startswith("service-account-") for u in people)
