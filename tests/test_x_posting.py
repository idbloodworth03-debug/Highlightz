"""Posting to X (owner, 2026-11-18: "lets get X active now too").

Nothing for X existed before this. Unlike YouTube, TikTok and Instagram there
is no review to wait for; what X has instead is a bill: pay-per-use since
2026-02, about $0.015 a post and about $0.20 for a post with a link in it.
So these hold three things:

  1. the conversation with X is the documented one (PKCE sign-in with the
     client secret as HTTP Basic, the chunked upload in order, a status poll
     before the post, the post carrying the media id), against canned answers;
  2. what protects the bill: the cap per account per 24 hours, links left out
     of the text, and "out of credit" told as ours and logged as an error;
  3. X does not exist anywhere until the owner has put its keys in, and then
     follows PUBLIC_PLATFORMS like the others.

Not covered, and said plainly in HANDOFF: none of this has run against X.
"""

import asyncio
import base64
import json
import time
import urllib.parse

import pytest

from config.settings import settings
from src.publish import connections, platforms, providers, release
from src.publish.providers import _http, x

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"v" * (9 * x.MB)        # three 4 MB segments


def _run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


class Canned:
    """Answers by (method, url fragment); a list of answers is used in order."""
    def __init__(self, script):
        self.script = [(k, list(v) if isinstance(v, list) else v) for k, v in script]
        self.calls = []

    async def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, frag), resp in self.script:
            if m == method and frag in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        raise AssertionError(f"unexpected {method} {url}")


def _resp(status=200, body=None, headers=None):
    return _http.Response(status=status, headers=headers or {},
                          text=json.dumps(body) if body is not None else "")


async def _nosleep(_):
    return None


@pytest.fixture(autouse=True)
def _x(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "x_client_id", "cid")
    monkeypatch.setattr(settings, "x_client_secret", "sec")
    monkeypatch.setattr(settings, "x_daily_posts_per_user", 10)
    monkeypatch.setattr(settings, "x_allow_links", False)
    monkeypatch.setattr(_http, "sleep", _nosleep)
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "conns.json")
    monkeypatch.setattr(connections, "_conns", {})
    monkeypatch.setattr(connections, "_loaded", True)


def _conn(**extra):
    c = connections.Connection(user_id="u1", platform="x", account_id="42", account_name="@nova",
                               access_token="AT", refresh_token="RT",
                               expires_at=time.time() + 7000, extra={"username": "nova", **extra})
    return connections.save(c)


def _happy(status_answers=None):
    return Canned([
        (("POST", "/2/media/upload/initialize"), _resp(200, {"data": {"id": "M1", "media_key": "7_M1"}})),
        (("POST", "/2/media/upload/M1/append"), _resp(200, {})),
        (("POST", "/2/media/upload/M1/finalize"),
         _resp(200, {"data": {"id": "M1", "processing_info": {"state": "pending", "check_after_secs": 1}}})),
        (("GET", "/2/media/upload"), status_answers or [
            _resp(200, {"data": {"processing_info": {"state": "in_progress", "check_after_secs": 1}}}),
            _resp(200, {"data": {"processing_info": {"state": "succeeded"}}})]),
        (("POST", "/2/tweets"), _resp(201, {"data": {"id": "T9", "text": "x"}})),
    ])


# ── 1. the conversation ──────────────────────────────────────────────────────

def test_the_sign_in_asks_for_exactly_what_posting_needs():
    url = x.PROVIDER.authorization_url("STATE", "CHAL")
    assert url.startswith("https://x.com/i/oauth2/authorize?")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert q["response_type"] == ["code"] and q["client_id"] == ["cid"]
    assert q["code_challenge"] == ["CHAL"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [providers.redirect_uri("x")]
    # offline.access is what makes X return a refresh token; without it the
    # connection dies two hours after it is made.
    assert set(q["scope"][0].split()) == {"tweet.read", "tweet.write", "users.read",
                                          "media.write", "offline.access"}
    assert "+" not in url.split("scope=")[1].split("&")[0]


def test_the_exchange_sends_the_secret_as_basic_and_learns_the_handle(monkeypatch):
    canned = Canned([
        (("POST", "/2/oauth2/token"), _resp(200, {"access_token": "AT", "refresh_token": "RT",
                                                   "expires_in": 7200, "scope": "tweet.write"})),
        (("GET", "/2/users/me"), _resp(200, {"data": {"id": "42", "username": "nova", "name": "Nova"}})),
    ])
    monkeypatch.setattr(_http, "request", canned)
    got = _run(x.PROVIDER.exchange("CODE", "VERIFIER"))
    assert got["account_id"] == "42" and got["account_name"] == "@nova"
    assert got["refresh_token"] == "RT" and got["extra"] == {"username": "nova"}
    assert 7100 < got["expires_at"] - time.time() <= 7200
    tok = canned.calls[0][2]
    assert tok["headers"]["Authorization"] == "Basic " + base64.b64encode(b"cid:sec").decode()
    assert tok["data"]["grant_type"] == "authorization_code"
    assert tok["data"]["code_verifier"] == "VERIFIER" and tok["data"]["code"] == "CODE"
    assert "sec" not in json.dumps(tok["data"])              # the secret is never in the body


def test_a_refresh_keeps_the_new_refresh_token(monkeypatch):
    """X's refresh tokens are single use: the old one is dead the moment the
    new one is issued. Keeping the old one strands the account an hour later."""
    c = _conn(); c.expires_at = time.time() + 10
    canned = Canned([(("POST", "/2/oauth2/token"),
                      _resp(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200}))])
    monkeypatch.setattr(_http, "request", canned)
    _run(providers.ensure_fresh(x.PROVIDER, c))
    assert canned.calls[0][2]["data"] == {"grant_type": "refresh_token", "refresh_token": "RT",
                                          "client_id": "cid"}
    stored = connections.get("u1", "x")
    assert (stored.access_token, stored.refresh_token) == ("AT2", "RT2")


def test_two_posts_at_once_spend_the_refresh_token_once(monkeypatch):
    c = _conn(); c.expires_at = time.time() + 10
    n = {"calls": 0}

    async def slow(method, url, **kw):
        n["calls"] += 1
        await asyncio.sleep(0.01)
        return _resp(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200})
    monkeypatch.setattr(_http, "request", slow)

    async def both():
        await asyncio.gather(providers.ensure_fresh(x.PROVIDER, c),
                             providers.ensure_fresh(x.PROVIDER, c))
    _run(both())
    assert n["calls"] == 1, "the second refresh would have sent a token X had already killed"


def test_a_dead_refresh_token_says_connect_again(monkeypatch):
    c = _conn(); c.expires_at = time.time() + 10
    monkeypatch.setattr(_http, "request", Canned([
        (("POST", "/2/oauth2/token"), _resp(400, {"error": "invalid_request",
                                                   "error_description": "Value passed for the token was invalid."}))]))
    with pytest.raises(providers.ProviderError) as e:
        _run(providers.ensure_fresh(x.PROVIDER, c))
    assert e.value.reauth


def test_a_post_uploads_in_order_waits_for_processing_then_posts(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4)
    canned = _happy()
    monkeypatch.setattr(_http, "request", canned)
    res = _run(x.PROVIDER.post(_conn(), f, len(MP4), "Insane clutch!! #valorant", "mp4", 30))
    assert res.url == "https://x.com/nova/status/T9" and res.remote_id == "T9" and res.note == ""
    kinds = [(m, u.split("/2/")[1].split("?")[0]) for m, u, _ in canned.calls]
    assert kinds == [("POST", "media/upload/initialize"),
                     ("POST", "media/upload/M1/append"), ("POST", "media/upload/M1/append"),
                     ("POST", "media/upload/M1/append"),
                     ("POST", "media/upload/M1/finalize"),
                     ("GET", "media/upload"), ("GET", "media/upload"),
                     ("POST", "tweets")]
    init = canned.calls[0][2]["json"]
    assert init == {"media_type": "video/mp4", "total_bytes": len(MP4), "media_category": "tweet_video"}
    # Every byte, once, in numbered segments no bigger than X takes.
    sent = b""
    for i, (_, _, kw) in enumerate(canned.calls[1:4]):
        assert kw["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
        body = kw["content"]
        assert f'name="segment_index"\r\n\r\n{i}\r\n'.encode() in body
        chunk = body.split(b"application/octet-stream\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
        assert len(chunk) <= 5 * x.MB
        sent += chunk
    assert sent == MP4
    assert canned.calls[5][2]["params"] == {"command": "STATUS", "media_id": "M1"}
    assert canned.calls[-1][2]["json"] == {"text": "Insane clutch!! #valorant",
                                           "media": {"media_ids": ["M1"]}}
    assert all(kw["headers"]["Authorization"] == "Bearer AT" for _, _, kw in canned.calls)


def test_a_video_x_cannot_process_is_not_posted(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    canned = _happy([_resp(200, {"data": {"processing_info": {
        "state": "failed", "error": {"message": "Unsupported codec"}}}})])
    monkeypatch.setattr(_http, "request", canned)
    with pytest.raises(providers.ProviderError) as e:
        _run(x.PROVIDER.post(_conn(), f, 1000, "x", "mp4"))
    assert "Unsupported codec" in e.value.message
    assert not any("tweets" in u for _, u, _ in canned.calls)
    assert x._recent(connections.get("u1", "x")) == []           # nothing was billed


def test_x_refusals_are_shown_in_its_own_words(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    canned = _happy()
    canned.script[-1] = (("POST", "/2/tweets"), _resp(403, {
        "title": "Forbidden", "status": 403,
        "detail": "You are not allowed to create a Tweet with duplicate content."}))
    monkeypatch.setattr(_http, "request", canned)
    with pytest.raises(providers.ProviderError) as e:
        _run(x.PROVIDER.post(_conn(), f, 1000, "x", "mp4"))
    assert "duplicate content" in e.value.message and not e.value.retryable and not e.value.reauth


# ── 2. the bill ──────────────────────────────────────────────────────────────

def test_the_defaults_are_the_cautious_ones():
    from config.settings import Settings
    f = Settings.model_fields
    assert f["x_daily_posts_per_user"].default == 10
    assert f["x_allow_links"].default is False
    assert f["x_client_id"].default == "" and f["x_client_secret"].default == ""


def test_an_account_is_held_to_its_posts_a_day_and_the_count_survives(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    monkeypatch.setattr(settings, "x_daily_posts_per_user", 2)
    old = time.time() - 25 * 3600                    # yesterday's does not count
    c = _conn(posts=[old])
    canned = _happy()
    monkeypatch.setattr(_http, "request", canned)
    _run(x.PROVIDER.post(c, f, 1000, "one", "mp4"))
    _run(x.PROVIDER.post(c, f, 1000, "two", "mp4"))
    assert len(x._recent(connections.get("u1", "x"))) == 2       # stored on the connection
    before = len(canned.calls)
    with pytest.raises(providers.ProviderError) as e:
        _run(x.PROVIDER.post(c, f, 1000, "three", "mp4"))
    assert e.value.retryable and "2 posts to X" in e.value.message
    assert len(canned.calls) == before, "a capped post must not upload (or cost) anything"
    # The ledger never reaches the browser.
    assert "posts" not in json.dumps(c.public())
    monkeypatch.setattr(settings, "x_daily_posts_per_user", 0)   # 0 = no cap
    _run(x.PROVIDER.post(c, f, 1000, "three", "mp4"))


def test_links_are_left_out_and_the_card_says_so(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    canned = _happy()
    monkeypatch.setattr(_http, "request", canned)
    res = _run(x.PROVIDER.post(_conn(), f, 1000,
                               "Watch live https://twitch.tv/nova or twitch.tv/nova #fps", "mp4"))
    sent = canned.calls[-1][2]["json"]["text"]
    assert "twitch.tv" not in sent and "http" not in sent and sent == "Watch live or #fps"
    assert res.note == "Links were left out of the X post."
    monkeypatch.setattr(settings, "x_allow_links", True)
    assert x.post_text("go https://twitch.tv/nova") == ("go https://twitch.tv/nova", False)


@pytest.mark.parametrize("caption", ["word " * 200, "日本語のキャプション" * 40, "🔥" * 300,
                                     "a" * 279 + " tail"])
def test_a_long_caption_is_cut_to_what_x_takes(caption):
    text, _ = x.post_text(caption)
    assert x.weighted_len(text) <= x.TEXT_MAX and text.endswith("…")


def test_text_is_counted_the_way_x_counts_it():
    assert x.weighted_len("hello") == 5
    assert x.weighted_len("日本") == 4 and x.weighted_len("🔥") == 2
    assert x.post_text("short one")[0] == "short one"
    assert x.post_text("")[0] == ""


def test_a_post_with_no_text_sends_only_the_video(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    canned = _happy()
    monkeypatch.setattr(_http, "request", canned)
    _run(x.PROVIDER.post(_conn(), f, 1000, "", "mp4"))
    assert canned.calls[-1][2]["json"] == {"media": {"media_ids": ["M1"]}}


def test_out_of_credit_is_ours_and_reaches_the_alert_email(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    monkeypatch.setattr(_http, "request", Canned([
        (("POST", "/2/media/upload/initialize"),
         _resp(402, {"title": "Payment Required", "detail": "credits depleted", "status": 402}))]))
    seen = []
    monkeypatch.setattr(x.log, "error", lambda ev, **kw: seen.append(ev))
    with pytest.raises(providers.ProviderError) as e:
        _run(x.PROVIDER.post(_conn(), f, 1000, "x", "mp4"))
    assert e.value.retryable and not e.value.reauth
    assert "ours to fix" in e.value.message and "credits depleted" not in e.value.message
    assert seen == ["x_credits_depleted"]            # log.error is what src/alerts emails


def test_a_rate_limit_and_a_dead_token_are_told_apart(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"; f.write_bytes(MP4[:1000])
    for status, attr in ((429, "retryable"), (401, "reauth"), (503, "retryable")):
        monkeypatch.setattr(_http, "request", Canned([
            (("POST", "/2/media/upload/initialize"), _resp(status, {}))]))
        with pytest.raises(providers.ProviderError) as e:
            _run(x.PROVIDER.post(_conn(), f, 1000, "x", "mp4"))
        assert getattr(e.value, attr), status


# ── 3. it does not exist until it is set up ──────────────────────────────────

def test_x_is_absent_everywhere_without_its_keys(monkeypatch):
    monkeypatch.setattr(settings, "x_client_id", "")
    monkeypatch.setattr(settings, "uploads_enabled", True)
    monkeypatch.setattr(settings, "public_platforms", "tiktok,youtube,x")
    assert not release.offered("x")
    assert "x" not in release.public_platforms() and "x" not in release.held_platforms()
    assert "x" not in release.auto_live()
    assert [p["id"] for p in platforms.public_specs()] == ["tiktok", "instagram", "youtube"]
    # ...so "coming soon" still names the two it always named.
    monkeypatch.setattr(settings, "public_platforms", "tiktok")
    assert release.join(release.held_platforms()) == "YouTube and Instagram"


def test_with_keys_it_follows_the_same_switch_as_the_others(monkeypatch):
    monkeypatch.setattr(settings, "uploads_enabled", True)
    monkeypatch.setattr(settings, "public_platforms", "tiktok,youtube")
    assert release.offered("x") and "x" in release.held_platforms()
    assert "x" in [p["id"] for p in platforms.public_specs()]
    assert not release.open_for("x", {"id": "u"}) and release.open_for("x", {"is_admin": True})
    monkeypatch.setattr(settings, "public_platforms", "tiktok,youtube,x")
    assert release.live_platforms() == ("youtube", "tiktok", "x")
    assert release.auto_live() == ("youtube", "x")              # posted to by itself
    assert release.open_for("x", {"id": "u"})


def test_x_is_one_of_the_platforms_autopilot_posts_to():
    from src import autopilot
    assert "x" in autopilot.AUTO_PLATFORMS and "tiktok" not in autopilot.AUTO_PLATFORMS
    assert "x" in connections.PLATFORMS and providers.get("x") is x.PROVIDER
    assert platforms.BY_ID["x"].caption_max == 280
    # Any shape is shown as it is on X, so a 16:9 clip gets no ratio warning.
    assert platforms.check_fit("x", 30, "16:9", "hi", "mp4") == []
    assert "rejected" in platforms.check_fit("x", 200, "9:16", "hi", "mp4")[0]


def test_the_connections_row_is_hidden_from_accounts_until_set_up(monkeypatch):
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api._connections_payload)
    assert "if not release.offered(p.id) and not admin and c is None:" in src
    assert api._PUBLISH_ENV_KEYS["x"] == ["X_CLIENT_ID", "X_CLIENT_SECRET"]


# ── the same, through the real API ───────────────────────────────────────────

PEOPLE = {}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.auth import users as user_store
    from src.dashboard import api
    PEOPLE.clear()
    PEOPLE.update({
        "pro":  {"id": "pro", "username": "pro", "plan": "pro", "subscription_status": "active"},
        "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    })
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    for k, v in (("uploads_enabled", True), ("public_platforms", "tiktok,youtube"),
                 ("tiktok_client_key", "k"), ("tiktok_client_secret", "s"),
                 ("google_client_id", "g"), ("google_client_secret", "s")):
        monkeypatch.setattr(settings, k, v)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login = login
    return c


def _rows(c):
    return {r["id"]: r for r in c.get("/publish/connections").json()["platforms"]}


def test_without_keys_only_the_admin_has_an_x_row(client, monkeypatch):
    monkeypatch.setattr(settings, "x_client_id", "")
    assert "x" not in _rows(client.login("pro"))
    assert "x" not in [p["id"] for p in client.get("/publish/platforms").json()["platforms"]]
    row = _rows(client.login("boss"))["x"]
    assert row["configured"] is False
    assert row["env_keys"] == ["X_CLIENT_ID", "X_CLIENT_SECRET"]
    assert row["redirect_uri"].endswith("/publish/connect/x/callback")


def test_with_keys_but_not_public_an_account_sees_x_as_soon(client):
    row = _rows(client.login("pro"))["x"]
    assert row["configured"] is False and row["held"] is True
    r = client.get("/publish/connect/x", follow_redirects=False)
    assert r.status_code == 403 and "coming soon" in r.json()["detail"]
    assert _rows(client.login("boss"))["x"]["configured"] is True     # the owner can test first


def test_once_public_an_account_is_sent_to_x_to_sign_in(client, monkeypatch):
    monkeypatch.setattr(settings, "public_platforms", "tiktok,youtube,x")
    c = client.login("pro")
    assert _rows(c)["x"]["configured"] is True and _rows(c)["x"]["held"] is False
    r = c.get("/publish/connect/x", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://x.com/i/oauth2/authorize?")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) >= 43
    assert q["state"][0]
    assert "x" in c.get("/me").json()["features"]["posting"]
