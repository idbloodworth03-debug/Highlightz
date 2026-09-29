"""One-step "Post now" for a clip, from the Scheduler and the clip library.

Owner, 2026-09-28: "I have no post now button make that a thing in the
scheduler and also make it a thing in the clip library. Make sure there is
the option to have it post to tiktok or instagram."

The only Post now used to live in a queued item's drawer and was hidden until
an account was picked and connected, so a clip that had never been queued had
no way to be posted. What these defend:

  1. a post goes ONLY to the platforms the caller named — never to an account
     that merely happens to be connected, and never to one on an old card
     the clip already sat on;
  2. a platform that is not connected is refused by name, not skipped;
  3. a platform that already took the clip is never posted to twice;
  4. the buttons exist where the owner asked for them, and nothing in the
     dialog is pre-ticked.
"""

import asyncio
import base64
import json

import pytest
from itsdangerous import TimestampSigner

from src.publish import connections, poster, schedule as sched
from src.uploads import library as lib

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
PEOPLE = {
    "pro": {"id": "pro", "subscription_status": "active", "plan": "pro"},
    "starter": {"id": "starter", "subscription_status": "active", "plan": "starter"},
}


def _conn(uid, platform):
    return connections.save(connections.Connection(
        user_id=uid, platform=platform, account_id="a", account_name="Nova",
        access_token="AT", refresh_token="RT"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    poster._inflight.clear()
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(api.settings, "uploads_enabled", True)

    sent, started = [], []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(poster, "start_now",
                        lambda item, notify=None: started.append(list(poster.auto_platforms(item))) or True)

    async def gen():
        yield MP4
    up = asyncio.run(lib.save_stream("pro", "clip.mp4", gen(), source="clip"))

    async def _into_library(clip_id, uid):
        return up
    monkeypatch.setattr(api, "_clip_into_library", _into_library)
    api._clips["c1"] = {"id": "c1", "user_id": "pro", "channel": "novafps",
                        "clip_title": "Ace", "duration_seconds": 30}

    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid,
             "subscription_status": PEOPLE[uid]["subscription_status"]}).encode())).decode())
        return c
    c.login, c.sent, c.started, c.upload = login, sent, started, up
    yield c
    api._clips.pop("c1", None)
    connections._conns.clear(); connections._loaded = False
    sched._items.clear(); sched._loaded = False
    lib._uploads.clear()


def _post(c, platforms, **kw):
    return c.login("pro").post("/publish/post-now",
                               json={"clip_id": "c1", "platforms": platforms, "caption": "Ace!", **kw})


def test_it_posts_to_exactly_the_platform_ticked(env):
    _conn("pro", "tiktok"); _conn("pro", "instagram")
    r = _post(env, ["tiktok"])
    assert r.status_code == 202, r.text
    item = sched.for_user("pro")[0]
    assert item.platforms == ["tiktok"], "posted somewhere that was not ticked"
    assert env.started == [["tiktok"]]
    assert item.ratio == "16:9" and item.duration_s == 30 and item.fmt == "mp4"
    assert ("schedule_added", "pro") in env.sent


def test_instagram_can_be_chosen_as_well_as_tiktok(env):
    _conn("pro", "tiktok"); _conn("pro", "instagram")
    assert _post(env, ["tiktok", "instagram"]).status_code == 202
    assert sorted(env.started[0]) == ["instagram", "tiktok"]


def test_a_platform_that_is_not_connected_is_refused_by_name(env):
    _conn("pro", "tiktok")
    r = _post(env, ["tiktok", "instagram"])
    assert r.status_code == 400 and "Instagram" in r.json()["detail"]
    assert sched.for_user("pro") == [] and env.started == [], "half-posted"


def test_a_broken_connection_counts_as_not_connected(env):
    _conn("pro", "tiktok")
    connections.set_error("pro", "tiktok", "revoked")
    r = _post(env, ["tiktok"])
    assert r.status_code == 400 and "TikTok" in r.json()["detail"]


def test_it_needs_a_platform_to_be_named(env):
    _conn("pro", "tiktok")
    assert _post(env, []).status_code == 400
    assert _post(env, ["myspace"]).status_code == 400


def test_a_card_scheduled_for_another_platform_is_not_dragged_along(env):
    """The poster posts every connected platform on a card. Reusing one
    scheduled for YouTube tomorrow would send the clip there now."""
    _conn("pro", "tiktok"); _conn("pro", "youtube")
    old = sched.add("pro", env.upload.id, "clip.mp4", "later", ["youtube", "tiktok"],
                    due_at=9_999_999_999)
    assert _post(env, ["tiktok"]).status_code == 202
    assert env.started == [["tiktok"]]
    assert len(sched.for_user("pro")) == 2, "the scheduled card was hijacked"
    assert sched.get(old.id, "pro").platforms == ["youtube", "tiktok"]


def test_a_card_for_the_same_platform_is_reused_not_duplicated(env):
    _conn("pro", "tiktok")
    sched.add("pro", env.upload.id, "clip.mp4", "old", ["tiktok"])
    assert _post(env, ["tiktok"]).status_code == 202
    items = sched.for_user("pro")
    assert len(items) == 1 and items[0].caption == "Ace!"
    assert ("schedule_updated", "pro") in env.sent


def test_a_platform_that_already_took_it_is_not_posted_to_twice(env):
    _conn("pro", "tiktok")
    it = sched.add("pro", env.upload.id, "clip.mp4", "x", ["tiktok"])
    sched.mark_result(it.id, "pro", "tiktok", sched.R_POSTED, url="https://t/1")
    r = _post(env, ["tiktok"])
    assert r.status_code == 409 and "TikTok" in r.json()["detail"]
    assert env.started == []


def test_it_needs_a_plan_with_the_scheduler(env):
    r = env.login("starter").post("/publish/post-now", json={"clip_id": "c1", "platforms": ["tiktok"]})
    assert r.status_code == 403


# ── the buttons ──────────────────────────────────────────────────────────────

def _page():
    from src.dashboard.aurora_html import DASHBOARD_HTML
    return DASHBOARD_HTML


def _fn(page, name):
    start = page.index(f"function {name}(")
    return page[start:page.index("\nfunction ", start + 10)]


def test_the_clip_library_and_clip_popup_have_post_now():
    page = _page()
    lib_screen = _fn(page, "LibraryScreen")
    assert "onPost={onPost}" in lib_screen
    assert "onPost(clip)" in _fn(page, "RdClip") and "Post</button>" in _fn(page, "RdClip")
    modal = _fn(page, "ClipModal")
    assert "Post now</button>" in modal and "onPost(clip)" in modal
    assert "onPost:onPostClip" in page and "<PostNowDialog clip={postClip}" in page


def test_the_scheduler_has_post_now_and_it_is_always_visible():
    page = _page()
    sched_screen = _fn(page, "ScheduleScreen")
    assert "Post a clip now" in sched_screen and 'mode="now"' in sched_screen
    drawer = _fn(page, "ScheduleDrawer")
    # No longer hidden behind "an account is connected and picked".
    assert "auto.length > 0 && !posting && !done" not in drawer
    assert "disabled={auto.length === 0}" in drawer


def test_the_dialog_offers_tiktok_and_instagram_and_ticks_nothing():
    dlg = _fn(_page(), "PostNowDialog")
    assert "['tiktok', 'instagram', 'youtube']" in dlg
    assert "useState(() => new Set())" in dlg, "something is pre-ticked"
    assert "fetch('/publish/post-now'" in dlg
    # Live: follows the App's queue state, not a poll of its own.
    assert "queue || []).find(" in dlg and "setInterval" not in dlg
