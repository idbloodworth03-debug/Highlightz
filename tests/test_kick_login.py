"""Signing in with Kick, either-or with Twitch (owner, 2026-09-16).

WHAT THIS PINS. A Kick sign-in makes an account keyed by kick_id and keeps
identity only: id, username, slug, avatar. The token is used once, to ask
Kick who signed in, and is never written — the legal pages say so, and
test_legal_pages_match_the_code refuses any "no Kick credentials" denial now
that the route exists. A Kick-only account cannot add a Twitch channel (a
Twitch clip is made under the user's own Twitch login) and is told to connect
Twitch from the Account tab; `intent=link` on either OAuth route attaches the
other identity to the signed-in account instead of making a new one.

The purge script for the 2026-06 flow's stored tokens stays tested below:
deleting code did not delete that data.
"""

import base64
import importlib
import json
import re
import sys

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.auth import kick_oauth
from src.dashboard import api


def _routes() -> set[str]:
    return {getattr(r, "path", "") for r in api.app.routes}


KICK_USER = {"id": "77001", "username": "Nova Plays", "slug": "nova-plays",
             "avatar_url": "https://files.kick.com/nova.png", "email": "nova@example.com"}


@pytest.fixture
def scene(tmp_path, monkeypatch):
    from src.auth import users as user_store
    store = tmp_path / "users.json"
    store.write_text("[]")
    monkeypatch.setattr(user_store, "_USERS_FILE", store)
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.bak.json")
    monkeypatch.setattr(api.settings, "kick_client_id", "kick-app")
    monkeypatch.setattr(api.settings, "kick_client_secret", "kick-secret")
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    sent = []

    async def _bcast(msg, user_id=None): sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)

    async def _exchange(code, verifier):
        assert code == "good-code" and verifier, "PKCE verifier did not survive the round trip"
        return {"access_token": "kick-token", "refresh_token": "kick-refresh", "expires_in": 3600}

    async def _get_user(token):
        assert token == "kick-token"
        return dict(KICK_USER)
    monkeypatch.setattr(kick_oauth, "exchange_code", _exchange)
    monkeypatch.setattr(kick_oauth, "get_user", _get_user)

    c = TestClient(api.app, base_url="https://testserver")
    c.store = store
    c.sent = sent

    def login(uid, username="x"):
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(json.dumps({"auth": True, "user_id": uid, "username": username,
                                            "subscription_status": "none"}).encode())
        c.cookies.set("session", signer.sign(data).decode())
    c.login = login
    return c


def _sign_in_with_kick(c):
    """Start the flow (state + PKCE land in the session cookie) and come back."""
    r = c.get("/auth/kick", follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    loc = r.headers["location"]
    assert loc.startswith("https://id.kick.com/oauth/authorize?")
    state = re.search(r"state=([^&]+)", loc).group(1)
    assert "code_challenge_method=S256" in loc and "code_challenge=" in loc
    r = c.get(f"/auth/kick/callback?code=good-code&state={state}", follow_redirects=False)
    return r


# ── the routes and the button ────────────────────────────────────────────────

def test_the_kick_oauth_routes_exist():
    assert {"/auth/kick", "/auth/kick/callback"} <= _routes()


def test_the_sign_in_page_offers_kick_only_when_the_app_is_configured(scene, monkeypatch):
    html = scene.get("/login").text
    assert 'href="/auth/kick"' in html and "Continue with Kick" in html
    assert 'href="/auth/twitch"' in html and "Continue with Twitch" in html
    monkeypatch.setattr(api.settings, "kick_client_id", "")
    html = scene.get("/login").text
    assert "/auth/kick" not in html, "a Kick button that would 503 is shown without an app"


def test_starting_kick_sign_in_without_an_app_explains_instead_of_failing(scene, monkeypatch):
    monkeypatch.setattr(api.settings, "kick_client_id", "")
    r = scene.get("/auth/kick", follow_redirects=False)
    assert r.headers["location"] == "/login?error=kick_signin_disabled"


# ── signing in ───────────────────────────────────────────────────────────────

def test_a_kick_sign_in_creates_an_account_with_identity_and_no_token(scene):
    r = _sign_in_with_kick(scene)
    assert r.headers["location"] == "/", r.text
    users = json.loads(scene.store.read_text())
    assert len(users) == 1
    u = users[0]
    assert u["kick_id"] == "77001" and u["kick_slug"] == "nova-plays"
    assert u["username"] == "Nova Plays" and u["avatar_url"].endswith("nova.png")
    assert "twitch_id" not in u
    for f in ("kick_access", "kick_refresh", "kick_expires_at"):
        assert f not in u, f"a Kick token field was written: {f}"
    assert u.get("email") == "nova@example.com"
    # Signed in: /me knows the account and which identities it holds.
    me = scene.get("/me").json()
    assert me["user_id"] == u["id"]
    assert me["platforms"] == {"twitch": False, "kick": True}
    assert me["kick_signin"] is True


def test_signing_in_again_finds_the_same_account(scene):
    _sign_in_with_kick(scene)
    first = json.loads(scene.store.read_text())[0]["id"]
    scene.cookies.clear()
    _sign_in_with_kick(scene)
    users = json.loads(scene.store.read_text())
    assert len(users) == 1 and users[0]["id"] == first


def test_a_bad_state_or_a_kick_error_goes_back_to_the_sign_in_page(scene):
    scene.get("/auth/kick", follow_redirects=False)
    r = scene.get("/auth/kick/callback?code=good-code&state=wrong", follow_redirects=False)
    assert r.headers["location"] == "/login?error=invalid_state"
    scene.get("/auth/kick", follow_redirects=False)
    r = scene.get("/auth/kick/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == "/login?error=kick_failed"
    assert json.loads(scene.store.read_text()) == []


# ── what a Kick-only account can and cannot do ───────────────────────────────

def test_a_kick_only_account_can_add_a_kick_channel_but_not_a_twitch_one(scene, monkeypatch):
    _sign_in_with_kick(scene)
    r = scene.post("/streams", json={"channel": "jynxzi", "platform": "twitch", "preset": "fps"})
    assert r.status_code == 403
    assert "Connect your Twitch account" in r.json()["detail"]
    assert "Account tab" in r.json()["detail"]


def test_a_kick_only_account_can_link_twitch_and_then_add_twitch_channels(scene, monkeypatch):
    from src.auth import twitch_oauth
    _sign_in_with_kick(scene)
    uid = json.loads(scene.store.read_text())[0]["id"]

    async def _tx(code): return {"access_token": "tw-at", "refresh_token": "tw-rt", "expires_in": 3600}
    async def _tu(token): return {"id": "9001", "login": "novafps", "username": "NovaFPS",
                                  "avatar_url": "", "email": ""}
    monkeypatch.setattr(twitch_oauth, "exchange_code", _tx)
    monkeypatch.setattr(twitch_oauth, "get_user", _tu)
    monkeypatch.setattr(api.settings, "twitch_client_id", "tw-app")

    r = scene.get("/auth/twitch?intent=link", follow_redirects=False)
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    r = scene.get(f"/auth/twitch/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?linked=twitch"

    users = json.loads(scene.store.read_text())
    assert len(users) == 1 and users[0]["id"] == uid, "linking made a second account"
    u = users[0]
    assert u["twitch_id"] == "9001" and u["kick_id"] == "77001"
    assert u.get("tw_access"), "the Twitch token (needed to make Twitch clips) was not stored"
    assert ("identity_linked", uid) in scene.sent
    assert scene.get("/me").json()["platforms"] == {"twitch": True, "kick": True}


def test_linking_a_twitch_account_that_already_has_its_own_account_is_refused(scene, monkeypatch):
    from src.auth import twitch_oauth, users as user_store
    user_store.upsert_twitch_user(twitch_id="9001", login="novafps", username="NovaFPS")
    _sign_in_with_kick(scene)

    async def _tx(code): return {"access_token": "a", "refresh_token": "r", "expires_in": 1}
    async def _tu(token): return {"id": "9001", "login": "novafps", "username": "NovaFPS", "avatar_url": "", "email": ""}
    monkeypatch.setattr(twitch_oauth, "exchange_code", _tx)
    monkeypatch.setattr(twitch_oauth, "get_user", _tu)
    monkeypatch.setattr(api.settings, "twitch_client_id", "tw-app")
    r = scene.get("/auth/twitch?intent=link", follow_redirects=False)
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    r = scene.get(f"/auth/twitch/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?link_error=twitch_taken"
    assert len(json.loads(scene.store.read_text())) == 2


def test_a_twitch_account_can_link_kick(scene):
    from src.auth import users as user_store
    u = user_store.upsert_twitch_user(twitch_id="9001", login="novafps", username="NovaFPS")
    scene.login(u["id"], "NovaFPS")
    r = scene.get("/auth/kick?intent=link", follow_redirects=False)
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    r = scene.get(f"/auth/kick/callback?code=good-code&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?linked=kick"
    users = json.loads(scene.store.read_text())
    assert len(users) == 1
    assert users[0]["kick_id"] == "77001" and users[0]["twitch_id"] == "9001"
    assert users[0]["username"] == "NovaFPS", "linking Kick renamed a Twitch account"
    assert "kick_access" not in users[0]


# ── the store never holds a Kick token ───────────────────────────────────────

def test_nothing_in_the_user_store_writes_a_kick_token():
    import inspect
    from src.auth import users as user_store
    for name in ("upsert_kick_user", "link_kick_identity"):
        src = inspect.getsource(getattr(user_store, name))
        for f in ("kick_access", "kick_refresh", "kick_expires_at"):
            assert f not in src, f"users.{name} writes {f}"
    assert not hasattr(user_store, "get_kick_token")
    assert not hasattr(user_store, "_store_refreshed_kick_tokens")


def test_the_callback_drops_the_token_after_the_who_is_this_call():
    import inspect
    src = inspect.getsource(api.kick_callback)
    assert "del tokens" in src
    assert "kick_access" not in src


def test_legacy_kick_credentials_are_still_redacted_from_api_responses():
    from src.auth import users as user_store
    legacy = {"id": "u1", "username": "nova", "kick_access": "enc-secret",
              "kick_refresh": "enc-secret", "kick_id": "77", "kick_slug": "nova"}
    pub = user_store._public(legacy)
    assert "kick_access" not in pub and "kick_refresh" not in pub
    assert pub["kick_id"] == "77"


# ── the dashboard ────────────────────────────────────────────────────────────

def test_the_dashboard_gates_twitch_channels_and_offers_connect_for_both():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    body = re.sub(r"/\*.*?\*/", "", h, flags=re.S)
    assert "const needsTwitch = activePlatform === 'twitch' && !!(me && me.platforms && me.platforms.kick && !me.platforms.twitch && !me.is_admin);" in body
    assert 'href="/auth/twitch?intent=link"' in body
    assert 'href="/auth/kick?intent=link"' in body
    assert "msg.event==='identity_linked'" in body
    assert "_params.get('linked')" in body and "_params.get('link_error')" in body
    # A Kick-only account starts on Kick.
    assert "data.platforms.kick && !data.platforms.twitch && !localStorage.getItem('hz_platform')" in body


# ── the purge script for the 2026-06 flow's stored tokens ────────────────────

@pytest.fixture()
def populated_store(tmp_path, monkeypatch):
    from src.auth import users as user_store
    store = tmp_path / "users.json"
    store.write_text(json.dumps([
        {"id": "u1", "username": "nova", "kick_access": "enc-a",
         "kick_refresh": "enc-r", "kick_expires_at": 123.0,
         "kick_id": "77", "kick_slug": "nova", "tw_access": "enc-tw"},
        {"id": "u2", "username": "lacy"},
        {"id": "u3", "username": "aceu", "kick_access": "enc-a"},
    ]))
    monkeypatch.setattr(user_store, "_USERS_FILE", store)
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.bak.json")
    return store


def test_the_purge_strips_old_credentials_but_keeps_identity(populated_store, monkeypatch):
    import scripts.purge_kick_credentials as purge
    importlib.reload(purge)
    monkeypatch.setattr(sys, "argv", ["purge_kick_credentials.py", "--apply"])
    assert purge.main() == 0
    users = json.loads(populated_store.read_text())
    by_id = {u["id"]: u for u in users}
    assert len(users) == 3
    for uid in ("u1", "u3"):
        for f in ("kick_access", "kick_refresh", "kick_expires_at"):
            assert f not in by_id[uid]
    assert by_id["u1"]["kick_id"] == "77" and by_id["u1"]["kick_slug"] == "nova"
    assert by_id["u1"]["tw_access"] == "enc-tw"
    assert by_id["u2"] == {"id": "u2", "username": "lacy"}
    once = populated_store.read_text()
    assert purge.main() == 0 and populated_store.read_text() == once
