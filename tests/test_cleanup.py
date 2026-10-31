"""The admin "Clean up" tab (owner, 2026-10-31).

"free up space on the server, go through users who have been inactive for a
week or more and clear out space, and also end streams for people who have not
accepted a clip in a week or more". The owner's choices: free accounts only,
clip videos only, remove the channels, an admin button with a preview first.

What these pin:
  1. the rules (src/dashboard/cleanup.py): who is in, who is never in, and
     the week on each clock;
  2. the run clears only the video (record and picture stay, the card says
     why), removes only the confirmed accounts' channels, spares anybody who
     came back after the preview, and leaves a note in the bell;
  3. every change reaches an open tab live, scoped to its account (CLAUDE.md);
  4. only an admin can preview or run it, and the page asks before running.
"""

from __future__ import annotations

import base64
import json as _j
import os
import time

import pytest
from itsdangerous import TimestampSigner

from src.dashboard import cleanup

DAY = 86400
NOW = 1_800_000_000.0


def _u(uid, **kw):
    return {"id": uid, "username": uid, "created_at": NOW - 30 * DAY, **kw}


def _clip(cid, uid, status="pending", age_days=10, **kw):
    return {"id": cid, "user_id": uid, "status": status, "created_at": NOW - age_days * DAY, **kw}


def _plan(users, clips=(), streams=None, activity=None, sizes=None):
    sizes = sizes or {}
    return cleanup.plan(users, clips, streams or {}, activity or {}, NOW,
                        lambda cid: sizes.get(cid, 0))


# ── 1. the rules ─────────────────────────────────────────────────────────────

def test_a_free_account_away_a_week_has_its_videos_listed():
    p = _plan([_u("a")], [_clip("c1", "a"), _clip("c2", "a")],
              activity={"a": NOW - 8 * DAY}, sizes={"c1": 1000, "c2": 500})
    assert [r["user_id"] for r in p["files"]] == ["a"]
    assert p["files"][0]["clips"] == ["c1", "c2"] and p["files"][0]["bytes"] == 1500
    assert p["totals"]["bytes"] == 1500 and p["totals"]["clip_files"] == 2


@pytest.mark.parametrize("seen", ["activity", "last_login_at", "created_at"])
def test_any_sign_of_life_this_week_spares_the_videos(seen):
    u = _u("a")
    activity = {}
    if seen == "activity":
        activity = {"a": NOW - 2 * DAY}
    else:
        u[seen] = NOW - 2 * DAY
    p = _plan([u], [_clip("c1", "a")], activity=activity, sizes={"c1": 1000})
    assert p["files"] == []


@pytest.mark.parametrize("who", [
    {"is_admin": True},
    {"subscription_status": "active", "plan": "starter"},
    {"subscription_status": "active", "plan": "pro"},
    {"subscription_status": "active"},                       # a $15-era subscriber
    {"subscription_status": "trialing", "trial_ends_at": NOW + 5 * DAY},
    {"subscription_status": "past_due", "plan": "pro"},
    {"is_labeler": True},
])
def test_paying_trial_and_staff_accounts_are_never_touched(who, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)          # the trial clock
    u = _u("a", **who)
    p = _plan([u], [_clip("c1", "a")], streams={"a:lacy": {"user_id": "a", "channel": "lacy"}},
              activity={"a": NOW - 60 * DAY}, sizes={"c1": 1000})
    assert p["files"] == [] and p["streams"] == []


def test_clips_with_no_video_on_disk_are_not_counted():
    p = _plan([_u("a")], [_clip("c1", "a"), _clip("c2", "a")],
              activity={"a": NOW - 8 * DAY}, sizes={"c2": 0})
    assert p["files"] == [], "an account with nothing to clear was listed"


def test_no_clip_approved_in_a_week_lists_the_channels():
    streams = {"a:lacy": {"user_id": "a", "channel": "lacy"},
               "a:jynxzi": {"user_id": "a", "channel": "jynxzi", "status": "paused"}}
    p = _plan([_u("a")], [_clip("c1", "a", status="approved", approved_at=NOW - 9 * DAY)],
              streams=streams)
    assert p["streams"] == [{"user_id": "a", "name": "a", "last_approved": NOW - 9 * DAY,
                             "channels": ["jynxzi", "lacy"]}]
    assert p["totals"]["channels"] == 2


def test_never_approving_counts_once_the_account_is_a_week_old():
    streams = {"a:lacy": {"user_id": "a", "channel": "lacy"}}
    assert [r["user_id"] for r in _plan([_u("a")], [], streams=streams)["streams"]] == ["a"]
    young = _u("a", created_at=NOW - 3 * DAY)
    assert _plan([young], [], streams=streams)["streams"] == [], "a new account had no week"


def test_a_clip_approved_this_week_keeps_the_channels():
    streams = {"a:lacy": {"user_id": "a", "channel": "lacy"}}
    clips = [_clip("c1", "a", status="approved", approved_at=NOW - 2 * DAY),
             _clip("c2", "a", status="pending", age_days=0)]
    assert _plan([_u("a")], clips, streams=streams)["streams"] == []


def test_a_pending_clip_is_not_an_approval():
    streams = {"a:lacy": {"user_id": "a", "channel": "lacy"}}
    clips = [_clip("c1", "a", status="pending", age_days=0)]
    assert [r["user_id"] for r in _plan([_u("a")], clips, streams=streams)["streams"]] == ["a"]


def test_another_accounts_channels_and_clips_stay_apart():
    streams = {"a:lacy": {"user_id": "a", "channel": "lacy"},
               "b:lacy": {"user_id": "b", "channel": "lacy"}}
    clips = [_clip("c1", "b", status="approved", approved_at=NOW - DAY)]
    p = _plan([_u("a"), _u("b")], clips, streams=streams)
    assert [r["user_id"] for r in p["streams"]] == ["a"], "b's approval was counted for a"


def test_the_bell_note_says_what_happened_and_how_to_undo_it():
    assert cleanup.notice(0, []) is None
    n = cleanup.notice(0, ["lacy"])
    assert "lacy" in n["body"] and "Add them back" in n["body"]
    n = cleanup.notice(1, [])
    assert "1 saved clip " in n["body"] and "Twitch clips can be downloaded again" in n["body"]
    n = cleanup.notice(3, [f"ch{i}" for i in range(7)])
    assert "3 saved clips" in n["body"] and "and 2 more" in n["body"]


# ── 2-4. the endpoints ───────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.clips import files as clip_files
    from src.dashboard import api

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    store = tmp_path / "clipfiles"
    store.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", store)
    clip_files._forget_scan()
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_user_last_active", {})
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    sent = []

    async def _capture(msg, user_id=None):
        sent.append((msg, user_id))
    monkeypatch.setattr(api, "broadcast", _capture)

    async def _unpub(channel, uid):
        pass
    monkeypatch.setattr(api, "_publish_remove_stream", _unpub)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "adm", "username": "admin", "is_admin": True, "created_at": now - 90 * DAY},
        {"id": "gone", "username": "gone", "created_at": now - 40 * DAY,
         "last_login_at": now - 20 * DAY},
        {"id": "here", "username": "here", "created_at": now - 40 * DAY, "last_login_at": now},
        {"id": "paid", "username": "paid", "created_at": now - 40 * DAY,
         "subscription_status": "active", "plan": "pro", "last_login_at": now - 20 * DAY},
    ]))

    def video(cid, uid, status="pending", age_days=10):
        api._clips[cid] = {"id": cid, "user_id": uid, "status": status, "channel": "lacy",
                           "created_at": now - age_days * DAY}
        for ext, data in ((".mp4", b"v" * 4096), (".jpg", b"j" * 10)):
            p = store / f"{cid}{ext}"
            p.write_bytes(data)
            os.utime(p, (now - age_days * DAY, now - age_days * DAY))

    def stream(channel, uid):
        api._streams[f"{uid}:{channel}"] = {"channel": channel, "user_id": uid,
                                            "platform": "twitch", "preset": "default",
                                            "status": "live", "added_at": now - 30 * DAY}

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid, is_admin=False):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": is_admin,
             "subscription_status": "active"}).encode())).decode())
        return c

    c.login, c.video, c.stream, c.sent = login, video, stream, sent
    c.api, c.store, c.files, c.dir = api, user_store, clip_files, store
    yield c
    clip_files._forget_scan()


def _run(env, preview):
    return env.login("adm", True).post("/admin/cleanup", json={
        "files": [r["user_id"] for r in preview["files"]],
        "streams": [r["user_id"] for r in preview["streams"]]})


def test_the_preview_changes_nothing(env):
    env.video("c1", "gone")
    env.stream("lacy", "gone")
    p = env.login("adm", True).get("/admin/cleanup").json()
    assert [r["user_id"] for r in p["files"]] == ["gone"] and p["files"][0]["bytes"] == 4096
    assert [r["user_id"] for r in p["streams"]] == ["gone"]
    assert p["store_bytes"] == 4096
    assert (env.dir / "c1.mp4").exists() and "gone:lacy" in env.api._streams
    assert env.sent == []


def test_the_run_clears_the_video_and_keeps_the_clip_and_its_picture(env):
    env.video("c1", "gone")
    env.video("k1", "here")
    env.video("p1", "paid")
    p = env.login("adm", True).get("/admin/cleanup").json()
    r = _run(env, p).json()
    assert r["freed_bytes"] == 4096 and r["clip_files"] == 1
    assert not (env.dir / "c1.mp4").exists()
    assert (env.dir / "c1.jpg").exists(), "the card lost its picture"
    assert "c1" in env.api._clips and env.api._clips["c1"]["file_cleared_at"]
    assert (env.dir / "k1.mp4").exists(), "an active account lost a video"
    assert (env.dir / "p1.mp4").exists(), "a paying account lost a video"
    out = env.api._clip_out(env.api._clips["c1"])
    assert out["file_state"] == "expired" and out["has_file"] is False and out["has_thumb"]


def test_a_video_fetched_again_drops_the_cleared_mark(env, monkeypatch):
    import asyncio
    from src.autopilot import runner
    env.video("c1", "gone")
    _run(env, env.login("adm", True).get("/admin/cleanup").json())
    assert env.api._clips["c1"]["file_cleared_at"]
    (env.dir / "c1.mp4").write_bytes(b"v" * 10)              # the Twitch fetch landed
    monkeypatch.setattr(env.api, "library_copy_if_approved", lambda cid: asyncio.sleep(0))
    monkeypatch.setattr(runner, "kick", lambda coro: coro.close())
    env.api._clips["c1"]["thumbnail_url"] = "https://x/t.jpg"   # no frame to make
    asyncio.run(env.api.on_clip_file_ready("c1"))
    assert "file_cleared_at" not in env.api._clips["c1"]


def test_a_video_from_the_last_hour_is_never_cleared(env):
    env.video("c1", "gone", age_days=0)
    p = env.login("adm", True).get("/admin/cleanup").json()
    assert p["files"] == []


def test_the_run_removes_the_channels_and_nothing_else(env):
    env.stream("lacy", "gone")
    env.stream("lacy", "paid")
    p = env.login("adm", True).get("/admin/cleanup").json()
    r = _run(env, p).json()
    assert r["channels"] == 1 and r["stream_accounts"] == 1
    assert "gone:lacy" not in env.api._streams
    assert "paid:lacy" in env.api._streams, "a paying account lost a channel"
    u = env.store.get_by_id("gone")
    assert u and "subscription_status" not in u, "removing channels changed the account"


def test_somebody_who_came_back_after_the_preview_is_spared(env):
    env.video("c1", "gone")
    env.stream("lacy", "gone")
    p = env.login("adm", True).get("/admin/cleanup").json()
    env.api._user_last_active["gone"] = time.time()       # they signed back in
    env.api._clips["c2"] = {"id": "c2", "user_id": "gone", "status": "approved",
                            "approved_at": time.time(), "created_at": time.time()}
    r = _run(env, p).json()
    assert r["clip_files"] == 0 and r["channels"] == 0 and r["skipped_accounts"] == 1
    assert (env.dir / "c1.mp4").exists() and "gone:lacy" in env.api._streams


def test_nobody_who_was_not_confirmed_is_touched(env):
    env.video("c1", "gone")
    env.stream("lacy", "gone")
    r = env.login("adm", True).post("/admin/cleanup", json={"files": ["here"], "streams": []})
    assert r.json()["clip_files"] == 0
    assert (env.dir / "c1.mp4").exists() and "gone:lacy" in env.api._streams
    assert env.login("adm", True).post("/admin/cleanup", json={}).status_code == 400


def test_every_change_reaches_the_open_tab_scoped_to_its_account(env):
    env.video("c1", "gone")
    env.stream("lacy", "gone")
    _run(env, env.login("adm", True).get("/admin/cleanup").json())
    events = [(m["event"], uid) for m, uid in env.sent]
    assert ("clip_updated", "gone") in events
    assert ("stream_removed", "gone") in events
    assert ("inbox_changed", "gone") in events
    assert {uid for _, uid in events} == {"gone"}
    clip = [m["clip"] for m, _ in env.sent if m["event"] == "clip_updated"][0]
    assert clip["file_state"] == "expired" and clip["file_cleared_at"]


def test_the_account_gets_one_note_in_its_bell(env):
    env.video("c1", "gone")
    env.video("c2", "gone")
    env.stream("lacy", "gone")
    _run(env, env.login("adm", True).get("/admin/cleanup").json())
    inbox = env.login("gone").get("/inbox").json()
    notes = [i for i in inbox["items"] if i["kind"] == "notice"]
    assert len(notes) == 1 and not notes[0]["read"] and inbox["unread"] >= 1
    assert "lacy" in notes[0]["body"] and "2 saved clips" in notes[0]["body"]
    env.login("gone").post("/inbox/read")
    assert env.login("gone").get("/inbox").json()["unread"] == 0


def test_only_an_admin_can_preview_or_run_it(env):
    env.video("c1", "gone")
    assert env.login("here").get("/admin/cleanup").status_code == 403
    r = env.login("here").post("/admin/cleanup", json={"files": ["gone"], "streams": []})
    assert r.status_code == 403 and (env.dir / "c1.mp4").exists()


# ── the pages ────────────────────────────────────────────────────────────────

def test_the_admin_tab_previews_first_and_confirms_before_running():
    from src.dashboard.api import ADMIN_HTML
    assert 'data-tab="cleanup"' in ADMIN_HTML and 'id="panel-cleanup"' in ADMIN_HTML
    at = ADMIN_HTML.index("getElementById('cu-run').addEventListener")
    seg = ADMIN_HTML[at:at + 1200]
    assert "confirm(" in seg and seg.index("confirm(") < seg.index("apiJson('/admin/cleanup', 'POST'")
    assert "Preview again first" in seg, "a stale preview can be run"


def test_the_dashboard_knows_the_notice_and_the_cleared_video():
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "notice:       ['alert', 'Notice']" in DASHBOARD_HTML
    assert "clip.file_state === 'expired' && clip.file_cleared_at" in DASHBOARD_HTML
    assert "msg.event==='inbox_changed'" in DASHBOARD_HTML
