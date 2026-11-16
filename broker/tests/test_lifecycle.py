"""PR 16: account lifecycle, admin only (ADR 0021). Suspend (mail keeps
arriving by default), reactivate, quota, forced password reset, and deletion:
suspend now, delete after the grace period and only after a newer backup.

Fakes only. Not proven on a server.
"""

import uuid

import pytest
from conftest import AUTH, MON_AUTH
import test_admin
from test_admin import BEARER, as_admin

from broker import backup, purge
from broker.keycloak import KeycloakClient
from broker.mailcow import MailcowClient

DAY = 86400
actx = test_admin.actx  # the admin fixture: Keycloak on, a verified admin with a second factor
ADDR = "alice@example.com"
URL = f"/v1/accounts/{ADDR}"


def key():
    return {"Idempotency-Key": uuid.uuid4().hex}


def admin(**extra):
    return {**as_admin(), **key(), **extra}


def seed(c, local="alice", linked=True, active=1, used_mb=100):
    addr = f"{local}@example.com"
    c.fake.mailboxes[addr] = {"username": addr, "name": local.title(), "quota": 2048 * 1048576,
                              "quota_used": used_mb * 1048576, "active": active}
    if linked:
        uid = str(uuid.uuid4())
        c.kc_fake.users[uid] = {"id": uid, "username": local, "enabled": True, "requiredActions": []}
        c.store.link_set(addr, "keycloak", uid)
        return uid
    return None


def last(c):
    return c.store.events(1)[0]


def adapters(c):
    mc = MailcowClient(c.settings.mailcow_api_url, c.settings.mailcow_api_key, transport=c.fake.transport())
    kc = KeycloakClient(c.settings.keycloak_url, c.settings.keycloak_realm, c.settings.keycloak_client_id,
                        c.settings.keycloak_client_secret, transport=c.kc_fake.transport())
    return mc, kc


# ---- who may call ------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("POST", "/suspend"), ("POST", "/reactivate"), ("PUT", "/quota"), ("POST", "/password-reset"),
    ("POST", "/deletion"), ("DELETE", "/deletion"), ("GET", ""),
])
def test_only_a_verified_admin_reaches_lifecycle(actx, method, path):
    seed(actx)
    body = {"quota_mb": 10} if method == "PUT" else None
    for headers, status in ((AUTH, 403), (MON_AUTH, 403), (BEARER, 401), (as_admin("bad"), 401)):
        r = actx.client.request(method, URL + path, json=body, headers={**headers, **key()})
        assert r.status_code == status, (headers, r.text)
    assert actx.fake.mailboxes[ADDR]["active"] == 1 and actx.fake.mailboxes[ADDR]["quota"] == 2048 * 1048576


def test_writes_need_an_idempotency_key_and_an_address_on_the_domain(actx):
    seed(actx)
    r = actx.client.post(URL + "/suspend", headers=as_admin())
    assert r.status_code == 422 and "Idempotency-Key" in r.json()["detail"]
    assert actx.client.post("/v1/accounts/bob@other.test/suspend", headers=admin()).status_code == 404
    assert actx.client.post("/v1/accounts/nobody@example.com/suspend", headers=admin()).status_code == 404
    assert last(actx)["action"] == "account.suspend" and last(actx)["outcome"] == "refused"
    assert actx.fake.mailboxes[ADDR]["active"] == 1


# ---- suspend and reactivate ------------------------------------------------------

def test_suspend_keeps_mail_arriving_and_ends_sessions(actx):
    uid = seed(actx)
    r = actx.client.post(URL + "/suspend", headers=admin())
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "address": ADDR, "state": "login_disabled", "identity": "disabled"}
    assert actx.fake.mailboxes[ADDR]["active"] == 2          # mailcow: no login, mail still arrives
    assert actx.kc_fake.users[uid]["enabled"] is False and uid in actx.kc_fake.logouts
    ev = last(actx)
    assert ev["action"] == "account.suspend" and ev["outcome"] == "ok"
    assert ev["actor"] == {"kind": "admin", "id": "admin-1"} and ev["subject"] == {"address": ADDR}
    assert ev["changes"] == {"state": {"from": "active", "to": "login_disabled"}}


def test_suspend_can_also_stop_mail(actx):
    seed(actx)
    r = actx.client.post(URL + "/suspend", json={"stop_receiving": True}, headers=admin())
    assert r.json()["state"] == "inactive" and actx.fake.mailboxes[ADDR]["active"] == 0
    assert actx.client.post(URL + "/suspend", json={"stop_receiving": "yes please"}, headers=admin()).status_code == 422


def test_reactivate(actx):
    uid = seed(actx)
    actx.client.post(URL + "/suspend", headers=admin())
    r = actx.client.post(URL + "/reactivate", headers=admin())
    assert r.status_code == 200 and r.json()["state"] == "active" and r.json()["identity"] == "enabled"
    assert actx.fake.mailboxes[ADDR]["active"] == 1 and actx.kc_fake.users[uid]["enabled"] is True
    assert last(actx)["changes"] == {"state": {"from": "login_disabled", "to": "active"}}


def test_an_account_without_an_identity_is_suspended_in_mailcow_only(actx):
    seed(actx, linked=False)
    r = actx.client.post(URL + "/suspend", headers=admin())
    assert r.json()["identity"] == "not_linked" and actx.fake.mailboxes[ADDR]["active"] == 2
    assert last(actx)["adapter"] == "mailcow"


def test_replay_returns_the_first_answer_and_changes_nothing_more(actx):
    seed(actx)
    h = admin()
    first = actx.client.post(URL + "/suspend", headers=h)
    actx.fake.mailboxes[ADDR]["active"] = 1  # changed elsewhere meanwhile
    again = actx.client.post(URL + "/suspend", headers=h)
    assert again.status_code == 200 and again.json() == first.json()
    assert actx.fake.mailboxes[ADDR]["active"] == 1
    other = actx.client.post(URL + "/suspend", json={"stop_receiving": True}, headers=h)
    assert other.status_code == 409


def test_a_failure_part_way_is_audited_and_a_retry_finishes(actx):
    uid = seed(actx)
    actx.fake.fail_next_edit = True
    h = admin()
    r = actx.client.post(URL + "/suspend", headers=h)
    assert r.status_code == 502 and "mailcow" in r.json()["detail"]
    assert last(actx)["outcome"] == "failed" and last(actx)["error_kind"] == "unavailable"
    assert actx.kc_fake.users[uid]["enabled"] is False      # Keycloak went first
    r = actx.client.post(URL + "/suspend", headers=h)        # same key: the failed attempt left no record
    assert r.status_code == 200 and actx.fake.mailboxes[ADDR]["active"] == 2


def test_a_missing_keycloak_user_is_a_conflict_not_a_crash(actx):
    uid = seed(actx)
    del actx.kc_fake.users[uid]
    r = actx.client.post(URL + "/suspend", headers=admin())
    assert r.status_code == 409 and "no longer exists" in r.json()["detail"]
    assert actx.fake.mailboxes[ADDR]["active"] == 1


# ---- quota and password reset -----------------------------------------------------

def test_quota_is_bounded_and_audited(actx):
    seed(actx, used_mb=1500)
    r = actx.client.put(URL + "/quota", json={"quota_mb": 4096}, headers=admin())
    assert r.status_code == 200 and actx.fake.mailboxes[ADDR]["quota"] == 4096 * 1048576
    assert last(actx)["changes"] == {"quota_mb": {"from": 2048, "to": 4096}}
    for q, why in ((0, "1-"), (999999, "1-"), (1000, "already uses")):
        r = actx.client.put(URL + "/quota", json={"quota_mb": q}, headers=admin())
        assert r.status_code == 422 and why in r.json()["detail"]
    assert actx.client.put(URL + "/quota", json={"quota_mb": 10, "extra": 1}, headers=admin()).status_code == 422
    assert actx.fake.mailboxes[ADDR]["quota"] == 4096 * 1048576


def test_password_reset_goes_through_keycloak(actx):
    uid = seed(actx)
    r = actx.client.post(URL + "/password-reset", headers=admin())
    assert r.status_code == 200 and r.json()["required_action"] == "UPDATE_PASSWORD"
    assert actx.kc_fake.users[uid]["requiredActions"] == ["UPDATE_PASSWORD"] and uid in actx.kc_fake.logouts
    ev = last(actx)
    assert ev["action"] == "account.password_reset" and ev["adapter"] == "keycloak"
    actx.client.post(URL + "/password-reset", headers=admin())
    assert actx.kc_fake.users[uid]["requiredActions"] == ["UPDATE_PASSWORD"]   # not added twice


def test_password_reset_without_an_identity_points_to_mailcow(actx):
    seed(actx, linked=False)
    r = actx.client.post(URL + "/password-reset", headers=admin())
    assert r.status_code == 409 and "mailcow" in r.json()["detail"]


def test_without_keycloak_lifecycle_is_break_glass_only(env):
    # IDENTITY_PROVIDER=none: there is no admin identity to check, so only
    # break-glass from inside the broker container (ADR 0020) can call.
    from fastapi.testclient import TestClient

    from broker.config import load_settings
    from broker.main import create_app
    from broker.store import Store
    from fake_mailcow import FakeMailcow
    s = load_settings({**env, "ADMIN_TOKEN": "a" * 40, "ADMIN_CLIENTS": "*"})
    fake = FakeMailcow()
    app = create_app(s, mailcow=MailcowClient(s.mailcow_api_url, s.mailcow_api_key, transport=fake.transport()),
                     store=Store(":memory:"))
    fake.mailboxes[ADDR] = {"username": ADDR, "quota": 1048576, "active": 1}
    r = TestClient(app).post(URL + "/suspend", headers={**BEARER, **key()})
    assert r.status_code == 403 and "identity provider" in r.json()["detail"]
    inside = TestClient(app, client=("127.0.0.1", 40000))
    r = inside.post(URL + "/suspend", headers={**BEARER, **key()})
    assert r.status_code == 200 and r.json()["identity"] == "not_linked" and fake.mailboxes[ADDR]["active"] == 2
    assert inside.post(URL + "/password-reset", headers={**BEARER, **key()}).status_code == 409


# ---- get one account --------------------------------------------------------------

def test_get_account_shows_state_identity_deletion_and_history(actx):
    seed(actx)
    actx.client.post(URL + "/suspend", headers=admin())
    actx.client.get("/v1/accounts/bob@example.com", headers=as_admin())
    r = actx.client.get(f"/v1/accounts/{ADDR.upper()}", headers=as_admin())
    d = r.json()
    assert r.status_code == 200 and d["address"] == ADDR and d["state"] == "login_disabled"
    assert d["identity"] == {"linked": True, "enabled": False, "password_reset_required": False}
    assert d["deletion"] is None
    assert [e["action"] for e in d["history"]] == ["account.suspend"]
    assert actx.client.get("/v1/accounts/bob@example.com", headers=as_admin()).status_code == 404


# ---- deletion ---------------------------------------------------------------------

def test_schedule_and_cancel_deletion(actx):
    uid = seed(actx)
    r = actx.client.post(URL + "/deletion", headers=admin())
    assert r.status_code == 202, r.text
    d = r.json()
    assert d["state"] == "login_disabled" and d["due_at"] == "2027-02-14T08:00:00Z"  # 30 days on
    assert actx.fake.mailboxes[ADDR]["active"] == 2 and actx.kc_fake.users[uid]["enabled"] is False
    assert actx.client.post(URL + "/deletion", headers=admin()).status_code == 409
    assert actx.client.post(URL + "/reactivate", headers=admin()).status_code == 409
    assert actx.client.post(URL + "/suspend", headers=admin()).status_code == 409
    listed = actx.client.get("/v1/accounts", headers=as_admin()).json()["accounts"][0]
    assert listed["deletion_due"] == d["due_at"]
    got = actx.client.get(URL, headers=as_admin()).json()["deletion"]
    assert got["due_at"] == d["due_at"] and got["newer_backup"] is False

    r = actx.client.delete(URL + "/deletion", headers=admin())
    assert r.status_code == 200 and r.json()["state"] == "active"
    assert actx.fake.mailboxes[ADDR]["active"] == 1 and actx.kc_fake.users[uid]["enabled"] is True
    assert actx.store.deletions() == []
    assert last(actx)["changes"]["deletion_due"] == {"from": d["due_at"], "to": None}
    assert actx.client.delete(URL + "/deletion", headers=admin()).status_code == 404


def test_cancelling_returns_a_suspended_account_to_suspended(actx):
    uid = seed(actx)
    actx.client.post(URL + "/suspend", json={"stop_receiving": True}, headers=admin())
    actx.client.post(URL + "/deletion", headers=admin())
    assert actx.fake.mailboxes[ADDR]["active"] == 0              # stays fully off
    r = actx.client.delete(URL + "/deletion", headers=admin())
    assert r.json()["state"] == "inactive" and actx.kc_fake.users[uid]["enabled"] is False


def run_purge(c, now, **kw):
    mc, kc = adapters(c)
    lines = []
    rc = purge.run(mc, kc, c.store, now=now, out=lines.append, **kw)
    return rc, "\n".join(lines)


def test_purge_waits_for_the_grace_period_and_a_newer_backup(actx):
    uid = seed(actx)
    t0 = actx.clock.t
    backup.mark_complete(actx.store, int(t0) - 3600, t0)         # an older backup does not count
    actx.client.post(URL + "/deletion", headers=admin())
    rc, out = run_purge(actx, t0 + 10 * DAY)
    assert rc == 0 and "waiting" in out and ADDR in actx.fake.mailboxes
    rc, out = run_purge(actx, t0 + 31 * DAY)
    assert rc == 0 and "no backup has completed" in out and ADDR in actx.fake.mailboxes
    actx.clock.t = t0 + 31 * DAY
    backup.mark_complete(actx.store, int(t0) + DAY, actx.clock.t)  # started after scheduling
    rc, out = run_purge(actx, t0 + 31 * DAY, dry_run=True)
    assert rc == 0 and "would delete" in out and ADDR in actx.fake.mailboxes
    rc, out = run_purge(actx, t0 + 31 * DAY)
    assert rc == 0 and "deleted  " + ADDR in out
    assert ADDR not in actx.fake.mailboxes and uid not in actx.kc_fake.users
    assert actx.store.deletions() == [] and actx.store.link_get(ADDR, "keycloak") is None
    ev = [e for e in actx.store.events() if e["action"] == "account.purge"]
    assert len(ev) == 1 and ev[0]["outcome"] == "ok" and ev[0]["caller"] == "system"
    assert actx.store.events(1)[0]["action"] == "purge.report"


def test_purge_stops_if_the_mailbox_was_reactivated_in_mailcow(actx):
    seed(actx)
    t0 = actx.clock.t
    actx.client.post(URL + "/deletion", headers=admin())
    backup.mark_complete(actx.store, int(t0) + 1, t0 + 2)
    actx.fake.mailboxes[ADDR]["active"] = 1                      # someone used mailcow's admin
    rc, out = run_purge(actx, t0 + 31 * DAY)
    assert rc == 1 and "STOPPED" in out and ADDR in actx.fake.mailboxes


def test_purge_retries_the_identity_if_keycloak_fails(actx):
    uid = seed(actx)
    t0 = actx.clock.t
    actx.client.post(URL + "/deletion", headers=admin())
    backup.mark_complete(actx.store, int(t0) + 1, t0 + 2)
    actx.kc_fake.down = True
    rc, out = run_purge(actx, t0 + 31 * DAY)
    assert rc == 1 and "Keycloak user not" in out and ADDR not in actx.fake.mailboxes
    assert actx.store.deletions()                                # row kept for the next run
    actx.kc_fake.down = False
    rc, out = run_purge(actx, t0 + 32 * DAY)
    assert rc == 0 and uid not in actx.kc_fake.users and actx.store.deletions() == []


def test_mark_complete_refuses_a_start_in_the_future(actx):
    with pytest.raises(ValueError):
        backup.mark_complete(actx.store, int(actx.clock.t) + 60, actx.clock.t)


def test_reconcile_does_not_report_deleted_accounts_as_drift(actx):
    from broker import reconcile
    seed(actx)
    actx.store.audit(caller="signup", action="account.create", outcome="ok", address=ADDR)
    t0 = actx.clock.t
    actx.client.post(URL + "/deletion", headers=admin())
    backup.mark_complete(actx.store, int(t0) + 1, t0 + 2)
    run_purge(actx, t0 + 31 * DAY)
    mc, _ = adapters(actx)
    assert reconcile.run(mc, actx.store, "example.com", out=lambda _: None) == 0


# ---- break-glass command line ---------------------------------------------------

def test_command_line_goes_through_the_control_plane(actx, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient

    from broker import account
    seed(actx)
    inside = TestClient(actx.app, client=("127.0.0.1", 40000), base_url="http://127.0.0.1:8100")
    monkeypatch.setenv("ADMIN_TOKEN", test_admin.ADMIN_TOKEN)
    out = []
    assert account.main(["suspend", ADDR, "--stop-receiving"], client=inside, out=out.append) == 0
    assert actx.fake.mailboxes[ADDR]["active"] == 0 and '"state": "inactive"' in out[-1]
    assert last(actx)["actor"] == {"kind": "token", "id": "admin-break-glass"}
    assert account.main(["quota", ADDR, "999999"], client=inside, out=out.append) == 1 and "refused" in out[-1]
    assert account.main(["quota", ADDR], client=inside, out=out.append) == 2
    assert account.main(["show", ADDR], client=inside, out=out.append) == 0 and "account.suspend" in out[-1]
    monkeypatch.delenv("ADMIN_TOKEN")
    assert account.main(["show", ADDR], client=httpx.Client(), out=out.append) == 2
