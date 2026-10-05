"""Three gaps from the 2026-10-05 audit (owner: "fix 1-3").

1. Password sign-in is password-only (POST /login logs in the FIRST account
   the password verifies against), so two password accounts sharing one would
   sign the second person in as the first. Creating one with a password that
   is already in use is refused. The field no longer says "Admin password",
   since app reviewers and testers use it too.
2. A deleted account's open tabs used to carry on as if nothing happened, and
   its cookie kept working against a user that no longer existed. Now: the
   delete broadcasts `account_deleted` (handled: off to sign-in), the auth
   middleware signs out a cookie whose account is gone, the socket refuses it,
   and refetchAll treats a 401 from /me as signed out.
3. Cancelling a VOD scan now tells the user's other tabs (`vod_cancelled`).
"""

import base64
import inspect
import json

import pytest
from itsdangerous import TimestampSigner
from starlette.websockets import WebSocketDisconnect

from src.auth import users as user_store

PEOPLE = {}


def _fresh():
    PEOPLE.clear()
    PEOPLE.update({
        "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
        "fan":  {"id": "fan", "username": "fan", "subscription_status": "none"},
    })


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    _fresh()
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "delete", lambda uid: PEOPLE.pop(uid, None) is not None)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)

    async def _nostop(uid):
        return None
    monkeypatch.setattr(api, "_stop_user_streams_now", _nostop)
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid, is_admin=None):
        c.cookies.clear()
        p = PEOPLE.get(uid, {})
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": p.get("is_admin", False) if is_admin is None else is_admin,
             "subscription_status": "none"}).encode())).decode())
        return c
    c.login, c.sent = login, sent
    return c


# ── 1. password accounts ─────────────────────────────────────────────────────

def test_a_password_already_in_use_is_refused(client, monkeypatch):
    PEOPLE["rev"] = {"id": "rev", "username": "rev", "password_hash": "h", "salt": "s"}
    monkeypatch.setattr(user_store, "verify", lambda u, pw: pw == "the-same-password")
    made = []
    monkeypatch.setattr(user_store, "create", lambda *a, **k: made.append(a) or {"id": "x", "username": a[0]})
    r = client.login("boss").post("/admin/users", json={"username": "tester", "password": "the-same-password"})
    assert r.status_code == 409 and "password" in r.json()["detail"]
    assert not made
    r = client.post("/admin/users", json={"username": "tester", "password": "a-different-one"})
    assert r.status_code == 201 and made


def test_password_in_use_only_looks_at_password_accounts(monkeypatch):
    monkeypatch.setattr(user_store, "_load", lambda: [{"id": "t"}, {"id": "p", "password_hash": "h"}])
    seen = []
    monkeypatch.setattr(user_store, "verify", lambda u, pw: seen.append(u["id"]) or False)
    assert user_store.password_in_use("x") is False
    assert seen == ["p"]           # a Twitch account is never checked


def test_the_login_field_no_longer_says_admin():
    from src.dashboard import api
    assert 'placeholder="Password"' in api.LOGIN_HTML
    assert 'placeholder="Admin password"' not in api.LOGIN_HTML


# ── 2. deleted accounts ──────────────────────────────────────────────────────

def test_an_admin_delete_signs_their_open_tabs_out_live(client):
    r = client.login("boss").delete("/admin/users/fan")
    assert r.status_code == 200 and "fan" not in PEOPLE
    assert ("account_deleted", "fan") in [(e, u) for e, u, _ in client.sent]


def test_a_self_delete_tells_their_other_tabs_before_the_record_goes():
    from src.dashboard import api
    src = inspect.getsource(api.delete_account)
    assert '"event": "account_deleted"' in src
    assert src.index('"account_deleted"}') < src.index("user_store.delete(uid)")


def test_a_cookie_for_a_deleted_account_is_signed_out(client):
    c = client.login("ghost")                     # not in the store
    # The dashboard's fetch gets an error it can act on…
    assert c.get("/me", headers={"accept": "application/json"}).status_code == 401
    # …and the cookie is gone, so nothing after it is treated as them either.
    c = client.login("ghost")
    r = c.get("/settings", follow_redirects=False, headers={"accept": "text/html"})
    assert r.status_code == 302 and r.headers["location"] == "/login?error=account_removed"
    # The public landing page still renders for them, signed out.
    assert c.get("/", follow_redirects=False).status_code == 200


def test_a_live_account_is_unaffected(client):
    assert client.login("fan").get("/me").status_code == 200


def test_the_socket_refuses_a_deleted_account(client):
    c = client.login("ghost")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws") as ws:
            ws.receive_text()


def test_the_login_page_explains_it():
    from src.dashboard import api
    assert api._ERROR_MESSAGES["account_removed"] == "This account has been deleted."


def test_the_dashboard_handles_it():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "msg.event==='account_deleted'" in h
    assert "window.location.href='/login?error=account_removed'" in h
    # refetchAll (mount + every reconnect) treats a 401 from /me as signed out.
    assert "if(r.status===401){ window.location.href='/login'; return null; }" in h


# ── 3. VOD cancel ────────────────────────────────────────────────────────────

def test_cancelling_a_scan_tells_their_other_tabs(client):
    from src.dashboard import api
    api._vod_jobs["j1"] = {"id": "j1", "user_id": "fan"}
    try:
        r = client.login("fan").delete("/vod/jobs/j1")
        assert r.status_code == 204
        assert ("vod_cancelled", "fan") in [(e, u) for e, u, _ in client.sent]
        assert "j1" not in api._vod_jobs
    finally:
        api._vod_jobs.pop("j1", None)


def test_someone_elses_scan_cannot_be_cancelled(client):
    from src.dashboard import api
    api._vod_jobs["j2"] = {"id": "j2", "user_id": "boss"}
    try:
        assert client.login("fan").delete("/vod/jobs/j2").status_code == 404
        assert "j2" in api._vod_jobs and not client.sent
    finally:
        api._vod_jobs.pop("j2", None)


def test_the_vod_screen_handles_it():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "'vod_cancelled'].includes(msg.event)" in h          # forwarded
    assert "msg.event==='vod_cancelled'" in h                    # handled
