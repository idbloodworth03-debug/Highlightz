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
    # Every platform open: these tests cover posting itself, not which
    # platforms are released (tests/test_tiktok_rollout.py).
    monkeypatch.setattr(api.settings, "public_platforms", "youtube,tiktok,instagram")

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


TIKTOK_OK = {"privacy_level": "SELF_ONLY", "allow_comment": False, "allow_duet": False,
             "allow_stitch": False, "commercial": False, "consent": True}


def _post(c, platforms, tiktok=TIKTOK_OK, **kw):
    """TikTok's rules put its choices on the person, so a TikTok post always
    carries them; other platforms carry none."""
    body = {"clip_id": "c1", "platforms": platforms, "caption": "Ace!", **kw}
    if tiktok is not None:
        body["options"] = {"tiktok": tiktok}
    return c.login("pro").post("/publish/post-now", json=body)


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


# ── TikTok's Direct Post rules, on the endpoint ──────────────────────────────

def test_a_tiktok_post_without_the_persons_choices_is_refused(env):
    _conn("pro", "tiktok")
    r = _post(env, ["tiktok"], tiktok=None)
    assert r.status_code == 400 and "who can view" in r.json()["detail"].lower()
    assert sched.for_user("pro") == [] and env.started == []


@pytest.mark.parametrize("edit,needle", [
    ({"privacy_level": ""}, "who can view"),
    ({"privacy_level": "EVERYONE_AND_THEIR_DOG"}, "who can view"),
    ({"consent": False}, "usage confirmation"),
    ({"commercial": True}, "promotes yourself"),
])
def test_the_rules_it_enforces_before_anything_is_queued(env, edit, needle):
    _conn("pro", "tiktok")
    r = _post(env, ["tiktok"], tiktok={**TIKTOK_OK, **edit})
    assert r.status_code == 400 and needle in r.json()["detail"].lower(), r.text
    assert sched.for_user("pro") == []


def test_branded_content_cannot_be_private(env, monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(api.settings, "tiktok_audited", True)
    _conn("pro", "tiktok")
    r = _post(env, ["tiktok"], tiktok={**TIKTOK_OK, "commercial": True, "branded_content": True,
                                       "privacy_level": "SELF_ONLY"})
    assert r.status_code == 400 and "private" in r.json()["detail"].lower()


def test_branded_content_waits_for_the_audit(env, monkeypatch):
    """Every post is private until TikTok approves the app, and branded content
    cannot be private — so it is refused up front rather than failing at TikTok."""
    from src.dashboard import api
    monkeypatch.setattr(api.settings, "tiktok_audited", False)
    _conn("pro", "tiktok")
    r = _post(env, ["tiktok"], tiktok={**TIKTOK_OK, "commercial": True, "branded_content": True,
                                       "privacy_level": "PUBLIC_TO_EVERYONE"})
    assert r.status_code == 400 and "approved" in r.json()["detail"].lower()


def test_the_persons_choices_are_stored_on_the_item(env):
    _conn("pro", "tiktok")
    picks = {**TIKTOK_OK, "allow_comment": True, "commercial": True, "your_brand": True}
    assert _post(env, ["tiktok"], tiktok=picks).status_code == 202
    item = sched.for_user("pro")[0]
    assert item.options["tiktok"] == {"privacy_level": "SELF_ONLY", "allow_comment": True,
        "allow_duet": False, "allow_stitch": False, "commercial": True, "your_brand": True,
        "branded_content": False, "consent": True}


def test_other_platforms_carry_no_tiktok_options(env):
    _conn("pro", "instagram")
    assert _post(env, ["instagram"], tiktok=None).status_code == 202
    assert sched.for_user("pro")[0].options == {}


def test_the_creator_endpoint_reports_the_account_and_the_audit_state(env, monkeypatch):
    from src.dashboard import api
    from src.publish import providers
    _conn("pro", "tiktok")

    async def fake_info(conn):
        return {"nickname": "Ian", "username": "ian", "avatar_url": "https://x/a.jpg",
                "privacy_level_options": ["SELF_ONLY"], "comment_disabled": False,
                "duet_disabled": True, "stitch_disabled": True, "max_video_post_duration_sec": 600.0}

    async def no_refresh(provider, conn): return None
    monkeypatch.setattr(providers.get("tiktok"), "creator_info", fake_info)
    monkeypatch.setattr(providers, "ensure_fresh", no_refresh)
    monkeypatch.setattr(api.settings, "tiktok_audited", False)
    r = env.login("pro").get("/publish/tiktok/creator")
    assert r.status_code == 200, r.text
    assert r.json()["nickname"] == "Ian" and r.json()["audited"] is False
    assert r.json()["duet_disabled"] is True and "access_token" not in r.text


def test_the_creator_endpoint_needs_a_connected_account(env):
    r = env.login("pro").get("/publish/tiktok/creator")
    assert r.status_code == 400 and "connect" in r.json()["detail"].lower()
    assert env.login("starter").get("/publish/tiktok/creator").status_code == 403


def test_the_dialog_follows_tiktoks_direct_post_rules():
    """developers.tiktok.com content-sharing guidelines, checked 2026-09-29:
    creator name, a preview, a visibility choice with no default from
    creator_info's own list, interactions off until ticked and greyed when the
    creator disabled them, a commercial-content switch that starts off with
    Your brand / Branded content, no private branded content, and the
    music-usage declaration above the button."""
    dlg = _fn(_page(), "PostNowDialog")
    assert "fetch('/publish/tiktok/creator')" in dlg and "Posting as" in dlg
    assert "<video className=\"tt-vid\"" in dlg, "no preview of what is being posted"
    assert "useState('')" in dlg and "Select…" in dlg, "visibility has a default"
    assert "privacy_level_options" in dlg, "the list is not the account's own"
    for k in ("comment_disabled", "duet_disabled", "stitch_disabled"):
        assert f"td.{k}" in dlg, f"{k} does not grey the box out"
    for name in ("ttCom", "ttDuet", "ttStitch", "ttDisc", "ttBrand", "ttBranded"):
        assert f"useState(false)" in dlg and f"[{name}, " in dlg
    assert "Disclose commercial content" in dlg and "Your brand" in dlg and "Branded content" in dlg
    assert "Branded content visibility cannot be set to private." in dlg
    assert "You need to indicate if your content promotes yourself, a third party, or both." in dlg
    assert "By posting, you agree to TikTok's" in dlg
    assert "Music Usage Confirmation" in dlg and "Branded Content Policy" in dlg
    assert "consent: true" in dlg
    assert "TikTok can take a few minutes to process" in dlg
