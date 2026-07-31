"""
Twitch clip import — listing a user's own clips through documented Helix.

This is metadata only and can never be a path to the video file (that question
is closed; see src/maintenance/probe_clip_media.py). What it CAN go wrong at:

  * enumerating somebody else's channel — the broadcaster must come from the
    session, never from the request;
  * burning the Helix budget, which is 800 points/min shared across every user
    we serve AND with live clipping;
  * shipping before it is meant to.
"""

import asyncio
import time

import pytest

from src.output import twitch_clips as tc


HELIX_ROW = {
    "id": "SlugOne", "title": "  big play  ", "url": "https://clips.twitch.tv/SlugOne",
    "embed_url": "https://clips.twitch.tv/embed?clip=SlugOne",
    "thumbnail_url": "https://static-cdn.jtvnw.net/x/thumb-0-480x272.jpg",
    "view_count": 42, "duration": 28.5, "created_at": "2026-07-30T10:00:00Z",
    "creator_name": "some_viewer", "game_id": "123",
}


class _FakeResp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload
    async def json(self): return self._payload
    async def text(self): return str(self._payload)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _FakeSession:
    """Captures the outbound request so the query params can be asserted."""
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.calls = status, payload or {}, []
    def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "params": params or {}})
        return _FakeResp(self.status, self.payload)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.fixture
def helix(monkeypatch):
    def install(status=200, payload=None):
        sess = _FakeSession(status, payload)
        monkeypatch.setattr(tc.aiohttp, "ClientSession", lambda *a, **k: sess)
        async def _tok(_s): return "app-token"
        monkeypatch.setattr(tc, "_get_app_token", _tok)
        monkeypatch.setattr(tc.settings, "twitch_client_id", "cid")
        return sess
    return install


def test_maps_the_fields_the_ui_needs(helix):
    helix(200, {"data": [HELIX_ROW], "pagination": {"cursor": "next123"}})
    out = asyncio.run(tc.list_channel_clips("999"))
    c = out["clips"][0]
    assert c["id"] == "SlugOne"
    assert c["title"] == "big play"                 # whitespace trimmed
    assert c["view_count"] == 42 and c["duration"] == 28.5
    assert c["creator_name"] == "some_viewer"       # who clipped it
    assert out["cursor"] == "next123"


def test_paging_is_one_page_per_call_not_looped_to_exhaustion(helix):
    """A big channel has thousands of clips and Helix's budget is shared with
    live clipping. One import must not be able to drain it in one request."""
    sess = helix(200, {"data": [HELIX_ROW] * 100, "pagination": {"cursor": "c2"}})
    asyncio.run(tc.list_channel_clips("999"))
    assert len(sess.calls) == 1, "helper paged internally instead of returning a cursor"
    assert sess.calls[0]["params"]["first"] == 100


def test_cursor_is_passed_through_as_after(helix):
    sess = helix(200, {"data": []})
    asyncio.run(tc.list_channel_clips("999", cursor="abc"))
    assert sess.calls[0]["params"]["after"] == "abc"
    # No cursor means no `after` at all, not an empty one.
    sess2 = helix(200, {"data": []})
    asyncio.run(tc.list_channel_clips("999"))
    assert "after" not in sess2.calls[0]["params"]


def test_page_size_is_clamped_to_the_helix_maximum(helix):
    sess = helix(200, {"data": []})
    asyncio.run(tc.list_channel_clips("999", limit=5000))
    assert sess.calls[0]["params"]["first"] == 100
    sess2 = helix(200, {"data": []})
    asyncio.run(tc.list_channel_clips("999", limit=0))
    assert sess2.calls[0]["params"]["first"] == 1


def test_empty_broadcaster_never_calls_twitch(helix):
    sess = helix(200, {"data": [HELIX_ROW]})
    assert asyncio.run(tc.list_channel_clips("")) == {"clips": [], "cursor": ""}
    assert sess.calls == []


def test_rows_without_an_id_are_dropped(helix):
    helix(200, {"data": [{"title": "junk"}, HELIX_ROW]})
    out = asyncio.run(tc.list_channel_clips("999"))
    assert [c["id"] for c in out["clips"]] == ["SlugOne"]


def test_a_helix_error_raises_rather_than_returning_an_empty_list(helix):
    """An empty list would render as 'you have no clips', which is a lie the
    user would act on. The endpoint turns this into a 502 they can retry."""
    helix(401, {"error": "Unauthorized"})
    with pytest.raises(RuntimeError):
        asyncio.run(tc.list_channel_clips("999"))


# ── HTTP layer ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    people = {
        "streamer": {"id": "streamer", "subscription_status": "active",
                     "plan": "pro", "twitch_id": "555"},
        "no_twitch": {"id": "no_twitch", "subscription_status": "active",
                      "plan": "pro"},
        "admin":    {"id": "admin", "subscription_status": "active",
                     "plan": "pro", "twitch_id": "777", "is_admin": True},
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(api.settings, "clip_import_enabled", True)
    # Rate limiter and cache are module state; a leftover entry from another
    # test would make these pass or fail for the wrong reason.
    api._import_hits.clear()
    api._import_cache.clear()

    calls = []
    async def fake_list(bid, cursor="", limit=100):
        calls.append({"bid": bid, "cursor": cursor})
        return {"clips": [dict(HELIX_ROW, id=f"c{len(calls)}")], "cursor": ""}
    monkeypatch.setattr("src.output.twitch_clips.list_channel_clips", fake_list)

    c = TestClient(api.app)
    c.calls = calls

    def login(uid):
        c.cookies.clear()
        from itsdangerous import TimestampSigner
        import base64, json as _j
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid, "subscription_status": "active"}).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c
    c.login = login
    return c


def test_the_channel_comes_from_the_session_not_the_request(client):
    """The single most important property here.

    If a client-supplied channel were honoured this stops being "your clips"
    and becomes a general-purpose endpoint for enumerating anyone's clips,
    running on OUR Helix budget and OUR app token.
    """
    c = client.login("streamer")
    r = c.get("/twitch/clips", params={"broadcaster_id": "1", "channel": "xqc",
                                       "twitch_id": "1"})
    assert r.status_code == 200
    assert client.calls[-1]["bid"] == "555", "a request parameter steered the lookup"


def test_a_user_with_no_twitch_link_gets_a_clear_409(client):
    c = client.login("no_twitch")
    r = c.get("/twitch/clips")
    assert r.status_code == 409
    assert "twitch" in r.json()["detail"].lower()


def test_results_are_cached_so_a_rerender_does_not_hit_helix(client):
    c = client.login("streamer")
    assert c.get("/twitch/clips").status_code == 200
    first = len(client.calls)
    for _ in range(5):
        c.get("/twitch/clips")
    assert len(client.calls) == first, "cache miss — every render would cost Helix budget"


def test_each_page_is_cached_separately(client):
    c = client.login("streamer")
    c.get("/twitch/clips")
    c.get("/twitch/clips", params={"cursor": "p2"})
    assert [x["cursor"] for x in client.calls] == ["", "p2"]
    c.get("/twitch/clips", params={"cursor": "p2"})
    assert len(client.calls) == 2, "second page was not cached"


def test_rate_limit_stops_a_hammering_client(client, monkeypatch):
    from src.dashboard import api
    c = client.login("streamer")
    # Distinct cursors so the cache cannot absorb the requests — this is
    # exercising the limiter, not the cache.
    codes = [c.get("/twitch/clips", params={"cursor": f"p{i}"}).status_code
             for i in range(api._IMPORT_MAX + 3)]
    assert 429 in codes, "no rate limit — one user could drain the shared budget"
    assert codes[0] == 200


def test_a_twitch_outage_is_a_502_not_an_empty_library(client, monkeypatch):
    async def boom(*a, **k): raise RuntimeError("helix down")
    monkeypatch.setattr("src.output.twitch_clips.list_channel_clips", boom)
    c = client.login("streamer")
    r = c.get("/twitch/clips")
    assert r.status_code == 502, "an outage rendered as 'you have no clips'"


def test_import_is_off_by_default_and_admins_bypass_it(client, monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(api.settings, "clip_import_enabled", False)
    assert client.login("streamer").get("/twitch/clips").status_code == 503
    api._import_cache.clear()
    assert client.login("admin").get("/twitch/clips").status_code == 200


def test_unauthenticated_requests_are_refused(client):
    client.cookies.clear()
    assert client.get("/twitch/clips",
                      headers={"accept": "application/json"}).status_code == 401
