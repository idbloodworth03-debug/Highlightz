"""The Scheduler POSTS (owner, 2026-09-15): connected accounts, the three
providers, the worker that uploads at the due time, and the routes.

What these defend, in order of how much it would cost to get wrong:

  1. a platform is never posted to TWICE — a retry, a restart mid-item or a
     second "Post now" click must skip every platform whose result already
     says posted;
  2. tokens never leave the server — not in the file (encrypted), not in the
     API payload, not in a broadcast;
  3. the public /media door opens for one file, for a bounded time, only
     with a token the server minted — a forged or expired one is a 404;
  4. the card can tell "posted for you" from "you post it" — the UI strings
     and the result rows are pinned;
  5. every new route is behind the same Pro gate as the rest of /publish.

The providers are driven against canned HTTP through providers._http, so a
whole resumable upload or chunked post runs without a socket.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from src.publish import connections, media_link, poster, providers, schedule as sched
from src.publish.providers import _http, youtube, tiktok, instagram
from src.uploads import library as lib

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    poster._inflight.clear()
    yield
    connections._conns.clear(); connections._loaded = False
    sched._items.clear(); sched._loaded = False
    lib._uploads.clear()


def _conn(uid="u1", platform="youtube", **kw):
    c = connections.Connection(user_id=uid, platform=platform, account_id="acct",
                               account_name="My Channel", access_token="AT-secret",
                               refresh_token="RT-secret", **kw)
    return connections.save(c)


async def _upload(uid="u1"):
    async def gen():
        yield MP4
    return await lib.save_stream(uid, "clip.mp4", gen(), source="render")


def _item(uid="u1", up=None, platforms=("youtube",), due=0.0, **kw):
    return sched.add(uid, up.id if up else "up1", "clip.mp4", "A great play\nmore words",
                     list(platforms), due, duration_s=30.0, ratio="9:16", fmt="mp4", **kw)


class FakeProvider:
    id = "youtube"; label = "YouTube"; uses_pkce = False
    refresh_margin_s = 60; refreshes_without_refresh_token = False

    def __init__(self):
        self.posted = []
        self.fail = None

    def configured(self): return True

    async def post(self, conn, path, size, caption, fmt, duration_s=0.0):
        self.posted.append((conn.platform, path.name, size, caption))
        if self.fail:
            raise self.fail
        return providers.PostResult(url="https://youtu.be/x1", remote_id="x1")


@pytest.fixture
def fake(monkeypatch):
    fp = FakeProvider()
    monkeypatch.setattr(providers, "get", lambda p: fp if p == "youtube" else None)
    return fp


# ── connections ──────────────────────────────────────────────────────────────

def test_tokens_are_encrypted_on_disk_and_absent_from_the_public_row():
    _conn()
    raw = (connections._INDEX).read_text()
    assert "AT-secret" not in raw and "RT-secret" not in raw
    pub = connections.get("u1", "youtube").public()
    assert "access_token" not in pub and "refresh_token" not in pub
    assert "secret" not in json.dumps(pub)
    # …and they come back intact after a fresh load.
    connections._conns.clear(); connections._loaded = False
    assert connections.get("u1", "youtube").access_token == "AT-secret"


def test_one_users_connection_is_invisible_to_another():
    _conn(uid="owner")
    assert connections.get("someone_else", "youtube") is None
    assert connections.remove("someone_else", "youtube") is None
    assert connections.get("owner", "youtube") is not None


def test_a_broken_connection_is_not_one_the_poster_uses():
    _conn(); _conn(platform="tiktok")
    connections.set_error("u1", "tiktok", "revoked")
    assert connections.connected_platforms("u1") == {"youtube"}
    # But it is still listed, so the card can show the error and "Connect again".
    assert {c.platform for c in connections.for_user("u1")} == {"youtube", "tiktok"}


def test_deleting_the_account_takes_the_tokens_with_it():
    _conn(uid="leaving"); _conn(uid="leaving", platform="tiktok"); _conn(uid="staying")
    assert connections.delete_all_for_user("leaving") == 2
    assert connections.for_user("leaving") == []
    assert len(connections.for_user("staying")) == 1


# ── the signed public media link ─────────────────────────────────────────────

def test_a_signed_link_names_exactly_one_upload_until_it_expires():
    tok = media_link.sign("up-1", ttl_s=60, now=1000.0)
    assert media_link.verify(tok, now=1050.0) == "up-1"
    assert media_link.verify(tok, now=1061.0) is None, "expired token still verifies"


def test_a_tampered_link_is_rejected():
    tok = media_link.sign("up-1", ttl_s=60, now=1000.0)
    uid, exp, mac = tok.split(".")
    assert media_link.verify(f"up-2.{exp}.{mac}", now=1001.0) is None, "id swapped"
    assert media_link.verify(f"{uid}.{int(exp) + 9999}.{mac}", now=1001.0) is None, "expiry moved"
    assert media_link.verify("garbage", now=1001.0) is None
    assert media_link.public_url("up-1").startswith("https://highlightz.app/media/up-1.")


# ── queue: results and status derivation ─────────────────────────────────────

def test_the_items_status_follows_its_per_platform_results():
    it = _item(platforms=("youtube", "tiktok"))
    it = sched.mark_result(it.id, "u1", "youtube", sched.R_POSTING)
    assert it.status == sched.POSTING
    it = sched.mark_result(it.id, "u1", "youtube", sched.R_POSTED, url="https://y/1")
    assert it.status == sched.POSTED
    it = sched.mark_result(it.id, "u1", "tiktok", sched.R_FAILED, error="nope")
    assert it.status == sched.FAILED
    assert it.posted_on() == {"youtube"}
    assert it.public()["results"]["youtube"]["url"] == "https://y/1"


def test_retry_forgets_failures_and_keeps_successes():
    it = _item(platforms=("youtube", "tiktok"))
    sched.mark_result(it.id, "u1", "youtube", sched.R_POSTED, url="https://y/1")
    sched.mark_result(it.id, "u1", "tiktok", sched.R_FAILED, error="nope")
    it = sched.reset_for_retry(it.id, "u1")
    assert it.status == sched.PENDING
    assert set(it.results) == {"youtube"}, "a platform that took the clip was forgotten"


def test_due_for_posting_includes_retryable_failures_but_not_undated_items():
    a = _item(due=time.time() - 10)
    b = _item(due=0)                                  # inbox item, no time
    c = _item(due=time.time() - 10)
    sched.mark_result(c.id, "u1", "youtube", sched.R_FAILED, error="quota", retryable=True)
    ids = {i.id for i in sched.due_for_posting()}
    assert a.id in ids and c.id in ids and b.id not in ids


# ── the poster ───────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_post_item_uploads_to_each_connected_platform_and_records_the_url(fake):
    up = _run(_upload())
    _conn()
    it = _item(up=up, platforms=("youtube", "tiktok"))          # tiktok NOT connected
    seen = []

    async def notify(msg, uid):
        seen.append((msg["event"], msg.get("item", {}).get("status"), uid))

    out = _run(poster.post_item(it, notify))
    assert fake.posted == [("youtube", f"{up.id}.mp4", len(MP4), "A great play\nmore words")]
    assert out.results["youtube"]["status"] == "posted"
    assert out.results["youtube"]["url"] == "https://youtu.be/x1"
    assert "tiktok" not in out.results, "posted to a platform the user has not connected"
    assert out.status == sched.POSTED
    # Realtime: posting, then posted, both scoped to the owner.
    assert [s for e, s, _ in seen if e == "schedule_updated"] == ["posting", "posted"]
    assert all(u == "u1" for _, _, u in seen)


def test_a_platform_that_already_took_the_clip_is_never_posted_to_again(fake):
    up = _run(_upload())
    _conn()
    it = _item(up=up)
    sched.mark_result(it.id, "u1", "youtube", sched.R_POSTED, url="https://y/1")
    assert poster.auto_platforms(sched.get(it.id, "u1")) == []
    assert _run(poster.post_item(sched.get(it.id, "u1"))) is None
    assert fake.posted == []


def test_a_dead_token_marks_the_connection_and_tells_the_tab(fake):
    up = _run(_upload())
    _conn()
    it = _item(up=up)
    fake.fail = providers.ProviderError("Your YouTube connection has expired.", reauth=True)
    events = []

    async def notify(msg, uid):
        events.append(msg["event"])

    out = _run(poster.post_item(it, notify))
    assert out.status == sched.FAILED
    assert out.results["youtube"]["error"].startswith("Your YouTube connection")
    assert connections.get("u1", "youtube").last_error
    assert "publish_connections_changed" in events


def test_a_missing_file_fails_the_item_instead_of_crashing(fake):
    _conn()
    it = _item(platforms=("youtube",))                     # upload "up1" never existed
    out = _run(poster.post_item(it))
    assert out.status == sched.FAILED
    assert "no longer on the server" in out.results["youtube"]["error"]


def test_a_provider_bug_is_a_failed_result_not_a_dead_worker(fake):
    up = _run(_upload())
    _conn()
    it = _item(up=up)
    fake.fail = RuntimeError("kaboom")
    out = _run(poster.post_item(it))
    assert out.status == sched.FAILED and "Unexpected" in out.results["youtube"]["error"]


def test_post_due_posts_timed_items_and_backs_off_retryable_failures(fake):
    up = _run(_upload())
    _conn()
    now = time.time()
    due = _item(up=up, due=now - 5)
    _item(up=up, due=0)                                     # inbox: waits for Post now
    assert _run(poster.post_due(now=now)) == 1
    assert sched.get(due.id, "u1").status == sched.POSTED
    # A quota failure is retried, but not every 30 seconds.
    again = _item(up=up, due=now - 5)
    fake.posted.clear()
    fake.fail = providers.ProviderError("quota", retryable=True)
    _run(poster.post_due(now=now))
    assert sched.get(again.id, "u1").status == sched.FAILED
    fake.fail = None
    assert _run(poster.post_due(now=now + 60)) == 0, "retried inside the backoff"
    assert _run(poster.post_due(now=now + poster.RETRY_AFTER_S + 1)) == 1
    assert sched.get(again.id, "u1").status == sched.POSTED


def test_a_non_retryable_failure_waits_for_the_user(fake):
    up = _run(_upload())
    _conn()
    now = time.time()
    it = _item(up=up, due=now - 5)
    fake.fail = providers.ProviderError("Caption too long.")
    _run(poster.post_due(now=now))
    fake.fail = None
    assert _run(poster.post_due(now=now + 3600)) == 0
    assert sched.get(it.id, "u1").status == sched.FAILED


def test_two_post_now_clicks_do_not_upload_twice(fake):
    up = _run(_upload())
    _conn()
    it = _item(up=up)
    poster._inflight.add(it.id)
    assert _run(poster.post_item(it)) is None
    assert poster.start_now(it) is False
    assert fake.posted == []


# ── providers, against canned HTTP ───────────────────────────────────────────

class Canned:
    """Answers providers._http.request from a script of (method, url-fragment)
    -> Response, and records every call so a test can read the wire."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, frag), resp in self.script:
            if m == method and frag in url:
                return resp
        raise AssertionError(f"unexpected {method} {url}")


def _resp(status=200, body=None, headers=None):
    return _http.Response(status=status, headers=headers or {},
                          text=json.dumps(body) if body is not None else "")


async def _nosleep(_): return None


def test_youtube_posts_through_a_resumable_upload(tmp_path, monkeypatch):
    f = tmp_path / "up9.mp4"; f.write_bytes(MP4)
    canned = Canned([
        (("POST", "upload/youtube/v3/videos"), _resp(200, {}, {"location": "https://u/sess"})),
        (("PUT", "https://u/sess"), _resp(200, {"id": "VID123"})),
    ])
    monkeypatch.setattr(_http, "request", canned)
    conn = connections.Connection(user_id="u1", platform="youtube", access_token="AT")
    res = _run(youtube.PROVIDER.post(conn, f, len(MP4), "Big play!\n#twitch #clips", "mp4", 30))
    assert res.url == "https://www.youtube.com/watch?v=VID123"
    init = canned.calls[0][2]
    assert init["json"]["snippet"]["title"] == "Big play!"
    assert init["json"]["snippet"]["description"] == "Big play!\n#twitch #clips"
    assert init["headers"]["X-Upload-Content-Length"] == str(len(MP4))
    put = canned.calls[1][2]
    assert put["headers"]["Authorization"] == "Bearer AT"


def test_youtube_quota_is_a_retryable_failure_not_the_users_fault(tmp_path, monkeypatch):
    f = tmp_path / "up9.mp4"; f.write_bytes(MP4)
    monkeypatch.setattr(_http, "request", Canned([
        (("POST", "upload/youtube"), _resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}],
                                                          "message": "quota"}})),
    ]))
    conn = connections.Connection(user_id="u1", platform="youtube", access_token="AT")
    with pytest.raises(providers.ProviderError) as e:
        _run(youtube.PROVIDER.post(conn, f, len(MP4), "x", "mp4"))
    assert e.value.retryable and not e.value.reauth
    assert "quota" in e.value.message.lower()


def test_youtube_exchange_learns_the_channel_name(monkeypatch):
    monkeypatch.setattr(_http, "request", Canned([
        (("POST", "oauth2.googleapis.com/token"), _resp(200, {"access_token": "AT", "refresh_token": "RT",
                                                               "expires_in": 3600, "scope": "s"})),
        (("GET", "youtube/v3/channels"), _resp(200, {"items": [{"id": "UC1", "snippet": {"title": "Lacy"}}]})),
    ]))
    got = _run(youtube.PROVIDER.exchange("code"))
    assert got["account_name"] == "Lacy" and got["account_id"] == "UC1"
    assert got["refresh_token"] == "RT" and got["expires_at"] > time.time()


def test_tiktok_chunk_plan_follows_the_rules():
    assert tiktok.chunk_plan(10 * tiktok.MB) == (10 * tiktok.MB, 1)
    assert tiktok.chunk_plan(64 * tiktok.MB) == (64 * tiktok.MB, 1)
    size = 100 * tiktok.MB
    chunk, count = tiktok.chunk_plan(size)
    assert chunk == 32 * tiktok.MB and count == 3          # remainder folds into the last


def test_tiktok_posts_and_says_so_when_forced_private(tmp_path, monkeypatch):
    f = tmp_path / "up9.mp4"; f.write_bytes(MP4)
    canned = Canned([
        (("POST", "creator_info/query"), _resp(200, {"data": {"privacy_level_options": ["SELF_ONLY"],
                                                               "max_video_post_duration_sec": 600},
                                                      "error": {"code": "ok"}})),
        (("POST", "video/init"), _resp(200, {"data": {"publish_id": "P1", "upload_url": "https://tt/up"},
                                             "error": {"code": "ok"}})),
        (("PUT", "https://tt/up"), _resp(201)),
        (("POST", "status/fetch"), _resp(200, {"data": {"status": "PUBLISH_COMPLETE",
                                                         "publicaly_available_post_id": [777]}})),
    ])
    monkeypatch.setattr(_http, "request", canned)
    monkeypatch.setattr(_http, "sleep", _nosleep)
    conn = connections.Connection(user_id="u1", platform="tiktok", access_token="AT",
                                  extra={"username": "lacy"})
    res = _run(tiktok.PROVIDER.post(conn, f, len(MP4), "caption #fyp", "mp4", 30))
    assert res.url == "https://www.tiktok.com/@lacy/video/777"
    assert "private" in res.note.lower()
    init = [c for c in canned.calls if "video/init" in c[1]][0][2]["json"]
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": len(MP4),
                                   "chunk_size": len(MP4), "total_chunk_count": 1}
    put = [c for c in canned.calls if c[0] == "PUT"][0][2]["headers"]
    assert put["Content-Range"] == f"bytes 0-{len(MP4) - 1}/{len(MP4)}"


def test_tiktok_picks_public_when_the_audit_allows_it(tmp_path, monkeypatch):
    """This name was aspirational until TIKTOK_AUDITED existed: the provider
    took the most public level on offer whether or not the app could use it,
    and video/init refused the post. The flag is now the thing that decides,
    so the test finally sets the condition it is named after. Its twin —
    unaudited, same offer, must still be SELF_ONLY — is in
    tests/test_tiktok_privacy.py."""
    from config.settings import settings
    monkeypatch.setattr(settings, "tiktok_audited", True)
    f = tmp_path / "up9.mp4"; f.write_bytes(MP4)
    canned = Canned([
        (("POST", "creator_info/query"), _resp(200, {"data": {"privacy_level_options":
                                                               ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]}})),
        (("POST", "video/init"), _resp(200, {"data": {"publish_id": "P1", "upload_url": "https://tt/up"}})),
        (("PUT", "https://tt/up"), _resp(201)),
        (("POST", "status/fetch"), _resp(200, {"data": {"status": "PUBLISH_COMPLETE"}})),
    ])
    monkeypatch.setattr(_http, "request", canned)
    monkeypatch.setattr(_http, "sleep", _nosleep)
    conn = connections.Connection(user_id="u1", platform="tiktok", access_token="AT")
    res = _run(tiktok.PROVIDER.post(conn, f, len(MP4), "c", "mp4"))
    assert res.note == ""
    init = [c for c in canned.calls if "video/init" in c[1]][0][2]["json"]
    assert init["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_instagram_hands_over_a_signed_public_url_and_polls_to_publish(tmp_path, monkeypatch):
    f = tmp_path / "up9.mp4"; f.write_bytes(MP4)
    canned = Canned([
        (("POST", "/17841/media_publish"), _resp(200, {"id": "M9"})),
        (("POST", "/17841/media"), _resp(200, {"id": "C1"})),
        (("GET", "/C1"), _resp(200, {"status_code": "FINISHED"})),
        (("GET", "/M9"), _resp(200, {"permalink": "https://www.instagram.com/reel/abc/"})),
    ])
    monkeypatch.setattr(_http, "request", canned)
    monkeypatch.setattr(_http, "sleep", _nosleep)
    conn = connections.Connection(user_id="u1", platform="instagram", access_token="AT",
                                  account_id="17841", extra={"ig_user_id": "17841"})
    res = _run(instagram.PROVIDER.post(conn, f, len(MP4), "cap", "mp4", 30))
    assert res.url == "https://www.instagram.com/reel/abc/"
    create = canned.calls[0][2]["data"]
    assert create["media_type"] == "REELS"
    assert create["video_url"].startswith("https://highlightz.app/media/up9.")
    assert media_link.verify(create["video_url"].rsplit("/", 1)[1]) == "up9"


def test_instagram_refuses_webm_before_touching_the_network(tmp_path, monkeypatch):
    f = tmp_path / "up9.webm"; f.write_bytes(MP4)
    monkeypatch.setattr(_http, "request", Canned([]))
    conn = connections.Connection(user_id="u1", platform="instagram", access_token="AT")
    with pytest.raises(providers.ProviderError) as e:
        _run(instagram.PROVIDER.post(conn, f, len(MP4), "cap", "webm"))
    assert "WebM" in e.value.message


def test_ensure_fresh_refreshes_an_expiring_token_and_persists_it(monkeypatch):
    c = _conn(expires_at=time.time() + 30)
    monkeypatch.setattr(_http, "request", Canned([
        (("POST", "oauth2.googleapis.com/token"), _resp(200, {"access_token": "NEW", "expires_in": 3600})),
    ]))
    _run(providers.ensure_fresh(youtube.PROVIDER, c))
    assert c.access_token == "NEW"
    connections._conns.clear(); connections._loaded = False
    assert connections.get("u1", "youtube").access_token == "NEW"


def test_a_revoked_google_grant_is_a_reauth_not_a_retry(monkeypatch):
    c = _conn(expires_at=time.time() + 30)
    monkeypatch.setattr(_http, "request", Canned([
        (("POST", "oauth2.googleapis.com/token"), _resp(400, {"error": "invalid_grant"})),
    ]))
    with pytest.raises(providers.ProviderError) as e:
        _run(providers.ensure_fresh(youtube.PROVIDER, c))
    assert e.value.reauth


def test_every_provider_redirects_back_to_its_own_callback():
    for p in providers.all_providers():
        url = p.authorization_url("st4te", "chal" if p.uses_pkce else None)
        assert "publish%2Fconnect%2F" + p.id + "%2Fcallback" in url, url
        assert "state=st4te" in url
        assert p.configured() is False, "app keys are set in the test environment?"
    assert "code_challenge=chal" in tiktok.PROVIDER.authorization_url("s", "chal")
    assert "prompt=consent" in youtube.PROVIDER.authorization_url("s")


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    people = {
        "pro_user":     {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
        "starter_user": {"id": "starter_user", "subscription_status": "active", "plan": "starter"},
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    # https: the session cookie is Secure in production config, and a cookie
    # the OAuth start writes (the state) must survive to the callback.
    c = TestClient(api.app, base_url="https://testserver")

    def login(uid):
        c.cookies.clear()
        from itsdangerous import TimestampSigner
        import base64
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(json.dumps({
            "auth": True, "user_id": uid,
            "subscription_status": people[uid]["subscription_status"]}).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    c.sent = sent
    c.api = api
    return c


def test_connections_are_a_pro_matter_and_list_all_three_platforms(client):
    assert client.login("starter_user").get("/publish/connections").status_code == 403
    r = client.login("pro_user").get("/publish/connections")
    assert r.status_code == 200
    rows = r.json()["platforms"]
    assert [x["id"] for x in rows] == ["youtube", "tiktok", "instagram"]
    assert all(x["configured"] is False and x["connected"] is False for x in rows)
    # The admin setup card (2026-09-17) needs the exact callback each console
    # must have, and the .env keys — both derived server-side so the card can
    # never show a URL the server would not send.
    base = client.api.settings.public_base_url.rstrip("/")
    for x in rows:
        assert x["redirect_uri"] == f"{base}/publish/connect/{x['id']}/callback"
    assert rows[0]["env_keys"] == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    assert rows[1]["env_keys"] == ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]
    assert rows[2]["env_keys"] == ["INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"]


def test_the_scheduler_shows_admins_a_setup_card_for_unconfigured_platforms():
    """Owner (2026-09-17): "we need to get youtube, tiktok and instagram all
    connected." The code side is done; the missing half is three app
    registrations. The card puts the callback URL and the .env keys in front
    of the admin, in the app, so nothing has to be typed from memory."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    chips = h[h.index("function AccountChips("):h.index("function InboxTray(")]
    assert 'className="sc-setup"' in chips
    assert "me && me.is_admin && (connections||[]).some(c=>!c.configured)" in chips
    assert "<code>{c.redirect_uri}</code>" in chips
    assert "navigator.clipboard.writeText(c.redirect_uri)" in chips
    assert "(c.env_keys||[]).map(" in chips


def test_connect_says_so_when_the_operator_has_not_set_the_platform_up(client):
    r = client.login("pro_user").get("/publish/connect/youtube", follow_redirects=False)
    assert r.status_code == 503
    assert "not set up" in r.json()["detail"]
    assert client.get("/publish/connect/myspace", follow_redirects=False).status_code == 404


def test_connect_sends_the_user_to_google_and_the_callback_stores_no_token_in_the_payload(client, monkeypatch):
    monkeypatch.setattr(client.api.settings, "google_client_id", "cid")
    monkeypatch.setattr(client.api.settings, "google_client_secret", "sec")
    c = client.login("pro_user")
    r = c.get("/publish/connect/youtube", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid" in loc
    state = loc.split("state=")[1].split("&")[0]
    # Carry the session the start wrote (it holds the state) into the
    # callback, the way a browser would; the jar otherwise holds two.
    c.cookies.clear(); c.cookies.set("session", r.cookies["session"])

    async def _exchange(code, verifier=None):
        assert code == "the-code"
        return {"account_id": "UC1", "account_name": "Lacy", "access_token": "AT",
                "refresh_token": "RT", "expires_at": time.time() + 3600, "scopes": "s", "extra": {}}
    monkeypatch.setattr(youtube.PROVIDER, "exchange", _exchange)
    r = c.get(f"/publish/connect/youtube/callback?code=the-code&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?connected=youtube"
    assert ("publish_connections_changed", "pro_user") in client.sent
    row = [x for x in c.get("/publish/connections").json()["platforms"] if x["id"] == "youtube"][0]
    assert row["connected"] and row["account_name"] == "Lacy"
    assert "AT" not in json.dumps(row) and "access_token" not in row
    assert connections.get("pro_user", "youtube").access_token == "AT"

    # And a mismatched state is refused, not stored.
    r = c.get("/publish/connect/youtube/callback?code=x&state=wrong", follow_redirects=False)
    assert r.headers["location"].startswith("/?connect_error=")


def test_disconnect_forgets_the_token_and_tells_the_tabs(client):
    c = client.login("pro_user")
    _conn(uid="pro_user")
    assert c.delete("/publish/connections/youtube").status_code == 204
    assert connections.get("pro_user", "youtube") is None
    assert ("publish_connections_changed", "pro_user") in client.sent
    assert c.delete("/publish/connections/youtube").status_code == 404


def test_the_public_media_door_opens_only_for_a_valid_token(client):
    up = _run(_upload("pro_user"))
    client.cookies.clear()                                   # signed out, like Instagram
    good = media_link.sign(up.id)
    r = client.get(f"/media/{good}")
    assert r.status_code == 200 and r.content == MP4
    assert r.headers["content-type"].startswith("video/mp4")
    assert client.get(f"/media/{media_link.sign('nope')}").status_code == 404
    assert client.get(f"/media/{media_link.sign(up.id, ttl_s=-5)}").status_code == 404
    assert client.get(f"/media/{good}x").status_code == 404


def test_post_now_refuses_with_nothing_connected_and_kicks_the_poster_otherwise(client, monkeypatch):
    c = client.login("pro_user")
    up = _run(_upload("pro_user"))
    it = _item(uid="pro_user", up=up)
    r = c.post(f"/publish/schedule/{it.id}/post")
    assert r.status_code == 400 and "connect" in r.json()["detail"].lower()

    _conn(uid="pro_user")
    started = []
    monkeypatch.setattr(poster, "start_now", lambda item, notify=None: started.append(item.id) or True)
    r = c.post(f"/publish/schedule/{it.id}/post")
    assert r.status_code == 202, r.text
    assert r.json()["platforms"] == ["youtube"] and started == [it.id]
    assert ("schedule_updated", "pro_user") in client.sent
    assert c.post("/publish/schedule/nope/post").status_code == 404
    assert client.login("starter_user").post(f"/publish/schedule/{it.id}/post").status_code == 403


def test_post_now_retries_failures_but_not_platforms_already_posted(client, monkeypatch):
    c = client.login("pro_user")
    up = _run(_upload("pro_user"))
    _conn(uid="pro_user"); _conn(uid="pro_user", platform="tiktok")
    it = _item(uid="pro_user", up=up, platforms=("youtube", "tiktok"))
    sched.mark_result(it.id, "pro_user", "youtube", sched.R_POSTED, url="https://y/1")
    sched.mark_result(it.id, "pro_user", "tiktok", sched.R_FAILED, error="nope")
    monkeypatch.setattr(poster, "start_now", lambda item, notify=None: True)
    r = c.post(f"/publish/schedule/{it.id}/post")
    assert r.status_code == 202 and r.json()["platforms"] == ["tiktok"]


def test_the_queue_payload_carries_results_for_the_card(client):
    c = client.login("pro_user")
    up = _run(_upload("pro_user"))
    it = _item(uid="pro_user", up=up)
    sched.mark_result(it.id, "pro_user", "youtube", sched.R_POSTED, url="https://y/1", note="n")
    row = c.get("/publish/schedule").json()["items"][0]
    assert row["status"] == "posted"
    assert row["results"]["youtube"] == {"status": "posted", "url": "https://y/1",
                                         "remote_id": "", "note": "n",
                                         "at": row["results"]["youtube"]["at"]}


# ── the UI says which is which ───────────────────────────────────────────────

def test_the_scheduler_ui_tells_posted_for_you_from_post_it_yourself():
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    drawer = html[html.index("function ScheduleDrawer("):html.index("function ScheduleScreen(")]
    # The tag that marks a platform the server posts to, and the two labels
    # the time picker switches between.
    assert "<small title=\"Highlightz posts this one for you\">Auto</small>" in drawer
    assert "'Posts at' : 'Remind at'" in drawer
    assert "'Retry' : 'Post now'" in drawer
    # Share / Mark posted only for platforms the user posts by hand.
    assert "manual.length > 0 && <button" in drawer and "Mark posted" in drawer
    # Results in the drawer, with the link.
    assert "res.status === 'posted'" in drawer and 'href={res.url}' in drawer
    screen = html[html.index("function ScheduleScreen("):html.index("function UploadScreen(")]
    assert "Scheduler is a Pro feature" in screen
    assert "<AccountChips" in screen
    # The old promise is gone from the whole bundle.
    assert "never posts for you" not in html.lower()


def test_the_scheduler_is_a_week_of_half_hours():
    """Owner, 2026-09-18: "I need a simpler week calendar on the screen with
    time stamps almost like a teams calendar. I want 30 min intervals on each
    day and I want to be able to click in each 30 min time stamp and schedule
    a video to post."

    What it replaced: a month grid (three chips per day, no times) plus a
    separate day list to answer "when today", and scheduling meant opening a
    clip and typing into a datetime field. The empty half-hour cell is the
    control now.
    """
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    for comp in ("WeekCalendar", "SlotPicker", "InboxTray", "ScheduleDrawer", "AccountChips"):
        assert f"function {comp}(" in html, f"{comp} missing"
    assert "function MonthCalendar(" not in html and "function DayList(" not in html, \
        "the month grid and day list are back alongside the week"

    cal = html[html.index("function WeekCalendar("):html.index("function SlotPicker(")]
    # 48 rows of 30 minutes, 7 day columns.
    assert "length: WK_SLOTS" in cal and "i < 7" in cal
    # Clicking an EMPTY cell schedules; a cell with a clip opens it instead,
    # or the click would bury the clip under a picker.
    assert "onClick={()=>{ if (!it) onSlot(d, s); }}" in cal
    assert "e.stopPropagation(); onOpen(it);" in cal
    assert "draggable={st !== 'posting' && st !== 'posted'}" in cal, \
        "a posted or mid-upload clip can be dragged"

    screen = html[html.index("function ScheduleScreen("):html.index("function UploadScreen(")]
    assert "<WeekCalendar" in screen and "<InboxTray" in screen and "<SlotPicker" in screen
    # A drop lands on the half hour it was dropped on — no default hour to
    # guess any more. The instant is built from LOCAL fields, never by
    # string-concatenating UTC.
    assert "SC_DEFAULT_HOUR" not in html, "the guessed default hour is back"
    assert "const move = (id, d, slot) => at(id, slotTime(d, slot));" in screen
    assert "Math.floor(when.getTime()/1000)" in screen
    # The open drawer is derived from the queue, so a socket update reaches it.
    assert "items.find(i=>i.id === openId)" in screen


def test_the_half_hour_slots_are_real_times_not_just_stripes():
    """The grid's whole claim is that a cell IS a time. slotTime builds it
    from local calendar fields (so DST and zone come from the browser), and
    slotOf maps an existing due_at back to the same cell — if those two
    disagreed, a clip would render in a slot other than the one that posts
    it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "const WK_SLOT_MIN = 30;" in html
    assert "const WK_SLOTS = (24 * 60) / WK_SLOT_MIN;" in html
    assert "Math.floor(slot / 2), (slot % 2) * WK_SLOT_MIN" in html, "slotTime lost its minutes"
    assert "d.getHours() * 2 + (d.getMinutes() >= WK_SLOT_MIN ? 1 : 0)" in html, \
        "slotOf no longer inverts slotTime"
    # The scroll position is computed from the row height, so the constant and
    # the stylesheet must not drift apart.
    assert "const WK_SLOT_PX = 28;" in html
    assert ".wk-cell{height:28px" in html


def test_the_slot_picker_only_offers_clips_that_have_no_time_yet():
    """Offering an already-scheduled clip would silently move it off its own
    day — the way to move one is to drag it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    screen = html[html.index("function ScheduleScreen("):html.index("function UploadScreen(")]
    assert "<SlotPicker at={slotAt} items={inbox}" in screen
    assert "const inbox = items.filter(i=>!i.due_at" in screen


def test_the_scheduler_is_wired_for_realtime():
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "msg.event==='publish_connections_changed'" in html
    assert "refetchConnections();" in html[html.index("const refetchAll"):html.index("const wsBootstrapped")]
    assert "_params.get('connected')" in html and "_params.get('connect_error')" in html
    assert "connections={connections}" in html


def test_the_scheduler_screen_can_actually_scroll():
    """THE BUG: ScheduleScreen was the only screen rooted on `rd-wrap`, which
    matches NO rule in the stylesheet. `.rd-screen` is a fixed-height flex
    column with overflow:hidden, so a screen that brings no scroller of its
    own simply has its overflow clipped and unreachable.

    It went unnoticed because the month grid fit inside the viewport. The
    taller week grid did not — measured on 2026-09-19, 1178px of content in
    an 832px box, with the bottom of the page impossible to reach.

    Every other screen uses `.rd-scroll{flex:1;overflow-y:auto;min-height:0}`
    and this one now does too."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    screen = html[html.index("function ScheduleScreen("):html.index("function UploadScreen(")]
    assert 'className="rd-wrap"' not in screen, "the Scheduler is back on a class with no CSS"
    # Both roots: the Pro gate returns early and needs one too.
    assert screen.count('className="rd-scroll"') == 2
    assert ".rd-scroll{flex:1;overflow-y:auto;min-height:0" in html


def test_the_week_grid_hands_the_scroll_back_when_it_runs_out():
    """`overscroll-behavior:contain` on a 520px grid filling most of an 830px
    screen traps the wheel: a pointer resting over the calendar could not
    reach anything below it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    i = html.index(".wk-body{")
    assert "overscroll-behavior" not in html[i:i + 160]


def test_no_other_screen_was_left_on_the_class_with_no_rule():
    """If `rd-wrap` styles nothing, any screen still using it has the same
    clipped-and-unreachable bug waiting."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert ".rd-wrap{" not in html, "rd-wrap gained a rule; this test is now the wrong guard"
    assert 'className="rd-wrap"' not in html


def test_the_calendar_is_a_flat_surface_not_a_glass_one():
    """Owner: "I dont like the weird aura gradient going on inside the
    scheduler."

    `.glass` is redefined inside an `@supports` block LATER in the stylesheet
    than `.sc-cal`, with a 165deg gradient border and a fill of `--panel` at
    .035 alpha that lets `.rd-app`'s three background radials through. An
    earlier `.sc-cal` rule loses that on source order however opaque it is,
    which is why the opt-out has to live inside the same block.

    On a small card that treatment is a rim light. Wrapped around a 650px
    grid of times it is an aura sweeping over the half-hour lines."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    block = html[html.index("@supports (background:linear-gradient(#000,#000) padding-box)"):]
    block = block[:block.index(".rd-modal{")]
    assert ".sc-cal{" in block, "the calendar is back on the gradient border"
    i = block.index(".glass{")
    assert block.index(".sc-cal{") > i, "the opt-out sits before .glass and loses"
    assert "background:var(--rd-bg-2)" in block
    assert "box-shadow:none" in block
