"""Per-account early access to the held-back posting features (owner,
2026-10-01, for Meta's App Review).

The reviewer needs a Highlightz account that can connect Instagram and post
while UPLOADS_ENABLED is still off for everybody. Making them an admin would
hand a stranger the admin portal, so an admin flips one account's
`early_access` instead.

What these defend:
  1. with the release flag off, a normal account is refused (503) and an
     early-access one is let through to the plan check — and only the plan
     check, so it still needs Pro to post;
  2. /me reports features.uploads for them, which every screen already reads;
  3. only an admin sets it, and the user's open tab hears it live
     (`roles_updated`, scoped to them, which the dashboard already handles);
  4. creating a password account can turn it on in the same step;
  5. the Autopilot edit follows it, and the admin controls are wired.
"""

import base64
import json

import pytest
from itsdangerous import TimestampSigner

from src.auth import users as user_store

PEOPLE = {}


def _fresh():
    PEOPLE.clear()
    PEOPLE.update({
        "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
        "meta": {"id": "meta", "username": "meta", "subscription_status": "active",
                 "plan": "pro", "plan_source": "granted"},
        "free": {"id": "free", "username": "free", "subscription_status": "none"},
    })


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    _fresh()
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(api.settings, "uploads_enabled", False)
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        p = PEOPLE[uid]
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
             "subscription_status": p.get("subscription_status", "none")}).encode())).decode())
        return c
    c.login, c.sent = login, sent
    return c


def test_the_flag_off_refuses_a_normal_account(client):
    assert client.login("meta").get("/uploads").status_code == 503
    assert client.get("/me").json()["features"]["uploads"] is False


def test_early_access_opens_the_held_back_features(client):
    r = client.login("boss").post("/admin/users/meta/early-access?on=true")
    assert r.status_code == 200 and PEOPLE["meta"]["early_access"] is True
    assert ("roles_updated", "meta") in client.sent
    assert client.login("meta").get("/uploads").status_code == 200
    me = client.get("/me").json()
    assert me["features"]["uploads"] is True and me["early_access"] is True


def test_early_access_does_not_skip_the_plan(client):
    client.login("boss").post("/admin/users/free/early-access?on=true")
    assert client.login("free").get("/uploads").status_code == 403


def test_it_can_be_removed(client):
    client.login("boss").post("/admin/users/meta/early-access?on=true")
    client.post("/admin/users/meta/early-access?on=false")
    assert client.login("meta").get("/uploads").status_code == 503


def test_only_an_admin_sets_it(client):
    assert client.login("meta").post("/admin/users/meta/early-access?on=true").status_code == 403
    assert not PEOPLE["meta"].get("early_access")
    assert client.login("boss").post("/admin/users/nobody/early-access").status_code == 404


def test_a_password_account_can_be_created_with_it(client, monkeypatch):
    made = {}

    def _create(username, password, is_admin=False):
        u = {"id": "rev", "username": username, "is_admin": is_admin, "subscription_status": "none"}
        PEOPLE["rev"] = made["u"] = u
        return u
    monkeypatch.setattr(user_store, "create", _create)
    r = client.login("boss").post("/admin/users", json={
        "username": "meta_reviewer", "password": "a-long-password-1", "early_access": True})
    assert r.status_code == 201
    assert made["u"]["early_access"] is True and made["u"]["is_admin"] is False


def test_the_autopilot_edit_follows_it():
    from src.autopilot import auto_edit
    assert auto_edit.uses_new_edit({"early_access": True})
    assert auto_edit.uses_new_edit({"is_admin": True})
    assert not auto_edit.uses_new_edit({"id": "x"})
    assert not auto_edit.uses_new_edit(None)


def test_admin_controls_are_wired():
    from src.dashboard import api
    from src.dashboard.aurora_html import DASHBOARD_HTML
    html = api.ADMIN_HTML
    assert "dr-early" in html and ".dr-early," in html          # button + click gate
    assert "/early-access?on=" in html
    assert 'id="u-new"' in html and "early_access: true" in html
    # The dashboard already refetches /me on roles_updated.
    assert "msg.event==='roles_updated'" in DASHBOARD_HTML
