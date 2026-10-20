"""Kick broadcasters can opt out (owner, 2026-10-20: "kick opt-out option a").

The Terms say ANY broadcaster can remove their channel at /opt-out, and the
Privacy Policy puts Kick under "the same opt-out terms" — but the page only
took a Twitch sign-in, so a Kick streamer could not. Now they sign in with
Kick, confirm, and from then on:
  - no user can add the channel (by its slug), and every monitor of it running
    now is stopped;
  - it is never recorded — refused by name, and by the owner's Kick id, which
    holds even under a slug the opt-out did not see;
  - the slug blocked is the one from sign-in AND the one Kick's API reports for
    that id, since the two can differ.
Nobody is signed in by the flow. Twitch opt-outs work exactly as before, and a
Twitch opt-out does not block a same-named channel added on Kick (or the other
way round) where the platform is known.
"""

import asyncio
import base64
import json
import re

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.auth import kick_oauth, optout
from src.dashboard import api

KICK_USER = {"id": "4242", "username": "Lacobraaa", "slug": "lacobraaa",
             "avatar_url": "https://files.kick.com/a.webp", "email": "x@example.com"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(optout, "_OPTOUT_FILE", tmp_path / "optout.json")
    return tmp_path


@pytest.fixture
def scene(store, tmp_path, monkeypatch):
    from src.auth import users as user_store
    (tmp_path / "users.json").write_text("[]")
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.bak.json")
    monkeypatch.setattr(api.settings, "kick_client_id", "kick-app")
    monkeypatch.setattr(api.settings, "kick_client_secret", "kick-secret")

    async def _exchange(code, verifier):
        return {"access_token": "kick-token", "expires_in": 3600}

    async def _get_user(token):
        return dict(KICK_USER)
    monkeypatch.setattr(kick_oauth, "exchange_code", _exchange)
    monkeypatch.setattr(kick_oauth, "get_user", _get_user)

    from src.ingestion.platform import kick as kick_platform
    resolved = {"slug": "lacobraaa-official"}

    async def _slug_for(bid):
        assert bid == "4242"
        return resolved["slug"]
    monkeypatch.setattr(kick_platform, "channel_slug_for_user", _slug_for)

    stopped = []

    async def _stop(channel, uid):
        stopped.append((channel, uid))
        return True
    monkeypatch.setattr(api, "stop_stream_internal", _stop)
    monkeypatch.setattr(api, "_streams", {
        "u1:lacobraaa":          {"channel": "lacobraaa", "platform": "kick", "user_id": "u1"},
        "u2:lacobraaa-official": {"channel": "lacobraaa-official", "platform": "kick", "user_id": "u2"},
        "u3:lacobraaa":          {"channel": "lacobraaa", "platform": "twitch", "user_id": "u3"},
    })
    c = TestClient(api.app, base_url="https://testserver")
    c.stopped, c.resolved = stopped, resolved
    return c


def _verify_with_kick(c):
    r = c.get("/auth/kick?intent=optout", follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("https://id.kick.com/oauth/authorize?")
    state = re.search(r"state=([^&]+)", loc).group(1)
    return c.get(f"/auth/kick/callback?code=good&state={state}", follow_redirects=False)


# ── the store ────────────────────────────────────────────────────────────────

def test_a_kick_opt_out_blocks_its_slugs_and_its_owner(store):
    optout.opt_out_kick("4242", {"lacobraaa", "LacoBraaa-Official"}, "Lacobraaa")
    assert optout.is_opted_out("lacobraaa", "kick")
    assert optout.is_opted_out("lacobraaa-official", "kick")
    assert optout.kick_user_opted_out("4242") and not optout.kick_user_opted_out("1")
    assert not optout.is_opted_out("lacobraaa", "twitch")
    assert optout.is_opted_out("lacobraaa"), "a name-only caller must take the safe answer"


def test_twitch_opt_outs_are_unchanged_and_platform_aware(store):
    optout.opt_out("77", "LeavesNow", "LeavesNow")
    raw = json.loads((store / "optout.json").read_text())
    assert raw[0]["twitch_login"] == "leavesnow" and "platform" not in raw[0]
    assert optout.is_opted_out("leavesnow", "twitch") and optout.is_opted_out("leavesnow")
    assert not optout.is_opted_out("leavesnow", "kick")


def test_a_mixed_list_never_breaks_a_lookup(store):
    """The old check read e["twitch_login"] on every entry — a Kick entry would
    have raised, and every caller that treats an error as 'refuse' would have
    refused everything."""
    optout.opt_out_kick("4242", {"lacobraaa"}, "Lacobraaa")
    optout.opt_out("77", "leavesnow", "LeavesNow")
    assert not optout.is_opted_out("someone-else")
    assert optout.remove_opt_out("4242", "kick")
    assert not optout.is_opted_out("lacobraaa", "kick") and optout.is_opted_out("leavesnow", "twitch")
    assert optout.remove_opt_out("77") and optout.get_all() == []


# ── the flow ─────────────────────────────────────────────────────────────────

def test_the_page_offers_kick_when_kick_sign_in_is_set_up(scene, monkeypatch):
    page = scene.get("/opt-out").text
    assert 'href="/auth/kick?intent=optout"' in page and "Verify with Kick" in page
    assert 'href="/auth/twitch?intent=optout"' in page
    monkeypatch.setattr(api.settings, "kick_client_id", "")
    assert "Verify with Kick" not in scene.get("/opt-out").text
    assert scene.get("/auth/kick?intent=optout", follow_redirects=False).headers["location"] == "/opt-out"


def test_verifying_with_kick_confirms_who_it_is_and_signs_nobody_in(scene):
    r = _verify_with_kick(scene)
    assert r.headers["location"] == "/opt-out/confirm"
    page = scene.get("/opt-out/confirm").text
    assert "Lacobraaa" in page and "kick.com/lacobraaa" in page
    assert "Yes, opt out kick.com/lacobraaa" in page
    r = scene.get("/me", headers={"accept": "application/json"})
    assert r.status_code == 401, "the opt-out flow signed someone in"


def test_a_failed_kick_verification_goes_back_to_the_opt_out_page(scene, monkeypatch):
    async def _boom(code, verifier):
        raise RuntimeError("kick said no")
    monkeypatch.setattr(kick_oauth, "exchange_code", _boom)
    assert _verify_with_kick(scene).headers["location"] == "/opt-out"


def test_confirming_blocks_both_slugs_and_stops_every_kick_monitor_now(scene):
    _verify_with_kick(scene)
    r = scene.post("/opt-out/confirm", follow_redirects=False)
    assert r.headers["location"] == "/opt-out/success"
    entry = optout.get_all()[0]
    assert entry["platform"] == "kick" and entry["kick_id"] == "4242"
    assert set(entry["kick_slugs"]) == {"lacobraaa", "lacobraaa-official"}
    assert sorted(scene.stopped) == [("lacobraaa", "u1"), ("lacobraaa-official", "u2")]
    # The same name on Twitch is somebody else's channel: left alone.
    assert ("lacobraaa", "u3") not in scene.stopped


def test_when_kick_cannot_say_the_real_slug_the_sign_in_one_still_holds(scene):
    scene.resolved["slug"] = ""
    _verify_with_kick(scene)
    scene.post("/opt-out/confirm", follow_redirects=False)
    assert optout.get_all()[0]["kick_slugs"] == ["lacobraaa"]


def test_confirm_needs_the_verification_first(scene):
    r = scene.post("/opt-out/confirm", follow_redirects=False)
    assert r.headers["location"] == "/opt-out" and optout.get_all() == []


def test_an_opted_out_kick_channel_cannot_be_added(scene, tmp_path, monkeypatch):
    optout.opt_out_kick("4242", {"lacobraaa"}, "Lacobraaa")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    from src.auth import users as user_store
    (tmp_path / "users.json").write_text(json.dumps([{"id": "u9", "username": "fan", "kick_id": "9"}]))
    scene.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "u9", "username": "fan"}).encode())).decode())
    r = scene.post("/streams", json={"channel": "lacobraaa", "platform": "kick"})
    assert r.status_code == 403 and "opted out" in r.json()["detail"]


# ── never recorded ───────────────────────────────────────────────────────────

def test_the_recorder_refuses_a_kick_owner_by_id_under_any_slug(store, monkeypatch):
    from src.ingestion import clip_recorder as rec
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    monkeypatch.setattr(rec, "_recorders", {})
    optout.opt_out_kick("4242", {"lacobraaa"}, "Lacobraaa")
    got = asyncio.run(rec.ensure("renamed-channel", "hls://x", platform="kick", broadcaster_id="4242"))
    assert got is None and rec._recorders == {}


def test_the_worker_hands_the_recorder_the_platform_and_owner():
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker)
    assert "broadcaster_id=self._stream_info.broadcaster_id" in src


# ── Kick's API: the slug for an owner id ─────────────────────────────────────

def test_the_slug_lookup_reads_the_channel_for_that_id(monkeypatch):
    from src.ingestion.platform.kick import KickPlatform
    monkeypatch.setattr(api.settings, "kick_client_id", "kick-app")
    monkeypatch.setattr(api.settings, "kick_client_secret", "kick-secret")
    p = KickPlatform()
    p._app_token, p._app_token_exp = "tok", 9e12
    seen = {}

    async def _get(url, headers=None, params=None):
        seen.update(url=url, params=params)
        return 200, {"data": [{"broadcaster_user_id": 4242, "slug": "LacoBraaa-Official"}]}
    p._get_json = _get
    assert asyncio.run(p.slug_for_broadcaster("4242")) == "lacobraaa-official"
    assert seen["url"].endswith("/channels") and seen["params"] == {"broadcaster_user_id": "4242"}

    async def _other(url, headers=None, params=None):
        return 200, {"data": [{"broadcaster_user_id": 1, "slug": "someone-else"}]}
    p._get_json = _other
    assert asyncio.run(p.slug_for_broadcaster("4242")) == ""


# ── the admin list ───────────────────────────────────────────────────────────

def test_the_admin_list_shows_kick_entries_and_remove_works(store):
    h = api._ADMIN_OPTOUT_HTML
    assert "onclick=\"remove(" not in h, "JSON in a double-quoted onclick broke the Remove button"
    assert 'data-rm="' in h and "?platform=" in h and "kick.com/" in h


def test_the_privacy_policy_says_what_a_kick_opt_out_keeps():
    assert "Twitch or Kick user id, channel name and display name" in api.PRIVACY_HTML
