"""Who has connected which posting account: the admin's list and the user's own.

Owner, 2026-09-28: "a way to see what tiktok account is connected for all
users inside the admin page as well as instagrams … I also want the user to
be able to see all their stored accounts inside their settings."

What these defend:

  1. the admin list is ADMIN ONLY and shows every user's connections, with
     the user resolved to a name and every platform the server can connect
     (Instagram included) counted — nothing is hard-coded per platform;
  2. no token, in either form, ever reaches that response;
  3. a user's own list (/publish/connections) is theirs alone;
  4. the realtime contract: the Settings card reads the App's `connections`
     state, which `publish_connections_changed` and `refetchAll` keep
     current, and the admin page re-reads while it is open.
"""

import base64
import json
import re

import pytest
from itsdangerous import TimestampSigner

from src.publish import connections

PEOPLE = {
    "boss": {"id": "boss", "twitch_login": "boss", "is_admin": True},
    "ann":  {"id": "ann", "twitch_login": "ann_clips", "subscription_status": "active", "plan": "pro"},
    "bo":   {"id": "bo", "kick_slug": "bo_kick", "subscription_status": "active", "plan": "pro"},
}


def _conn(uid, platform, name, **kw):
    return connections.save(connections.Connection(
        user_id=uid, platform=platform, account_id="acct-" + name, account_name=name,
        access_token="AT-secret-" + name, refresh_token="RT-secret-" + name, **kw))


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    yield
    connections._conns.clear(); connections._loaded = False


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_load", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c

    c.login = login
    return c


# ── the admin's list ─────────────────────────────────────────────────────────

def test_admin_sees_every_users_connections_with_names(client):
    _conn("ann", "tiktok", "ann.tok", connected_at=100)
    _conn("bo", "instagram", "bo.insta", connected_at=200)
    _conn("bo", "tiktok", "bo.tok", connected_at=300)
    d = client.login("boss").get("/admin/connections").json()
    got = [(r["user"], r["platform"], r["account_name"]) for r in d["rows"]]
    # newest first, users named rather than uuid'd
    assert got == [("bo_kick", "tiktok", "bo.tok"), ("bo_kick", "instagram", "bo.insta"),
                   ("ann_clips", "tiktok", "ann.tok")]


def test_every_connectable_platform_is_counted_even_with_nobody_on_it(client):
    _conn("ann", "tiktok", "ann.tok")
    d = client.login("boss").get("/admin/connections").json()
    assert set(d["platforms"]) >= {"tiktok", "instagram", "youtube"}
    assert d["platforms"]["tiktok"]["connected"] == 1
    assert d["platforms"]["instagram"]["connected"] == 0


def test_a_dead_connection_is_flagged_not_hidden(client):
    _conn("ann", "tiktok", "ann.tok")
    connections.set_error("ann", "tiktok", "revoked by the user")
    d = client.login("boss").get("/admin/connections").json()
    assert d["rows"][0]["last_error"] == "revoked by the user"
    assert d["platforms"]["tiktok"]["broken"] == 1


def test_no_token_ever_reaches_the_admin_response(client):
    _conn("ann", "tiktok", "ann.tok")
    _conn("bo", "instagram", "bo.insta")
    body = client.login("boss").get("/admin/connections").text
    assert "secret" not in body
    assert "access_token" not in body and "refresh_token" not in body


def test_only_an_admin_can_read_the_list(client):
    _conn("ann", "tiktok", "ann.tok")
    r = client.login("ann").get("/admin/connections", follow_redirects=False)
    assert r.status_code in (401, 403) or r.status_code in (302, 307)
    assert "ann.tok" not in r.text
    client.cookies.clear()
    r = client.get("/admin/connections", follow_redirects=False)
    assert r.status_code != 200 and "ann.tok" not in r.text


# ── the user's own list ──────────────────────────────────────────────────────

def test_a_user_sees_only_their_own_accounts(client):
    _conn("ann", "tiktok", "ann.tok")
    _conn("bo", "tiktok", "bo.tok")
    rows = client.login("ann").get("/publish/connections").json()["platforms"]
    mine = [r for r in rows if r["connected"]]
    assert [(r["id"], r["account_name"]) for r in mine] == [("tiktok", "ann.tok")]
    assert "bo.tok" not in json.dumps(rows)
    assert "secret" not in json.dumps(rows)


# ── the pages ────────────────────────────────────────────────────────────────

def _page():
    from src.dashboard import aurora_html
    return aurora_html.DASHBOARD_HTML


def _account_screen(page):
    start = page.index("function AccountScreen(")
    return page[start:page.index("\nfunction ", start + 10)]


def test_the_account_overview_lists_stored_posting_accounts_from_live_state():
    """Owner, 2026-09-28: the accounts go in the Account screen's "Profile &
    Platforms" card, next to Twitch and Kick, not in Settings."""
    page = _page()
    body = _account_screen(page)
    assert "Profile &amp; Platforms" in body
    assert "connections = []" in page[page.index("function AccountScreen("):][:200]
    assert "'/publish/connections/' + c.id" in body          # disconnect
    assert "'/publish/connect/' + c.id" in body              # connect / reconnect
    # Fed by the App's live state, not fetched once inside the screen.
    assert "fetch('/publish/connections')" not in body
    assert re.search(r"<AccountScreen[^>]*connections", page)
    # …and it is not ALSO in Settings.
    settings = page[page.index("function SettingsScreen("):page.index("function tutBold(")]
    assert "Connected accounts" not in settings and "/publish/connect" not in settings


def test_the_connections_state_stays_live_and_resyncs():
    page = _page()
    assert "msg.event==='publish_connections_changed'" in page
    ws = page[page.index("msg.event==='publish_connections_changed'"):][:400]
    assert "refetchConnections()" in ws
    # refetchAll pulls it on mount and on every socket reconnect.
    start = page.index("const refetchAll = useCallback")
    ra = page[start:page.index("hz_refetch", start)]
    assert "refetchConnections()" in ra


def test_the_admin_page_has_the_tab_and_rereads_while_open():
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert 'data-tab="accounts"' in html and 'id="panel-accounts"' in html
    assert "api('/admin/connections')" in html
    assert "loadAccounts()" in html
    # No WebSocket on that page, so it polls while the panel is the one showing.
    assert "setInterval(function(){ if(accountsOpen()) loadAccounts(); }" in html


def test_the_admin_user_list_can_sort_by_most_recently_active():
    """Owner, 2026-09-30: "give me a way to sort the admin page by most
    recently active"."""
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert 'id="u-sort"' in html and '<option value="active">' in html
    assert "active:   u => u.last_active_at || 0" in html
    assert "if(key) rows.sort(" in html


def test_the_admin_user_list_can_sort_by_most_recently_active():
    """Owner, 2026-09-30: "give me a way to sort the admin page by most
    recently active"."""
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert 'id="u-sort"' in html and '<option value="active">' in html
    assert "active:   u => u.last_active_at || 0" in html
    assert "if(key) rows.sort(" in html
