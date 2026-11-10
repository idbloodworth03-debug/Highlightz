"""Pending Kick clips whose video is gone leave the queue (owner, 2026-11-10:
"Remove them").

Prod that day: 2,781 of 3,165 unreviewed clips had no video, 1,580 of them
Kick. A Kick clip is nothing but the file Highlightz cut, so with the file
gone it can never be reviewed with video; it sat in the queue as a dud. The
sweep removes exactly those, records them as VIDEO_GONE (not EXPIRED, which
would fire the queue-full notice; not REJECTED, which would teach the
formula), tells every open tab, and leaves one bell note a day.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.dashboard import api
from src.stats import stream_stats as ss

NOW = 1_800_000_000.0


def _clip(cid, platform="kick", status="pending", age_s=3600, uid="u1"):
    return {"id": cid, "platform": platform, "status": status, "user_id": uid,
            "channel": "r2m_world", "created_at": NOW - age_s}


@pytest.fixture
def world(monkeypatch, tmp_path):
    from src.clips import files as clip_files
    from src.auth import users as user_store
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_video_gone_noticed", {})
    monkeypatch.setattr(api, "_save_clips", lambda: saves.append(1))
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: deleted.append(c["id"]))
    monkeypatch.setattr(clip_files, "existing_ids", lambda: frozenset(on_disk))
    monkeypatch.setattr(user_store, "add_notices", lambda notes: notices.append(dict(notes)) or len(notes))
    saves, deleted, notices, sent, on_disk = [], [], [], [], set()

    async def _bcast(msg, user_id=None):
        sent.append((msg["event"], msg.get("clip_id"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)

    class W:
        pass
    w = W()
    w.saves, w.deleted, w.notices, w.sent, w.on_disk = saves, deleted, notices, sent, on_disk
    w.run = lambda **kw: asyncio.run(api.sweep_videoless_kick_clips(now=NOW, **kw))
    return w


def test_a_pending_kick_clip_with_no_video_is_removed(world):
    api._clips["dud"] = _clip("dud")
    assert world.run() == 1
    assert "dud" not in api._clips and world.saves == [1] and world.deleted == ["dud"]
    assert ("clip_removed", "dud", "u1") in world.sent


@pytest.mark.parametrize("clip, why", [
    (_clip("tw", platform="twitch"), "a Twitch clip can be fetched again"),
    (_clip("kept", status="approved"), "approved is not pending"),
    (_clip("young", age_s=60), "its video may still be on the way"),
    (_clip("edge", age_s=899), "inside the fifteen-minute grace"),
])
def test_everything_else_stays(world, clip, why):
    api._clips[clip["id"]] = clip
    assert world.run() == 0, why
    assert clip["id"] in api._clips and world.sent == []


def test_a_kick_clip_whose_video_is_on_disk_stays(world):
    api._clips["fine"] = _clip("fine")
    world.on_disk.add("fine")
    assert world.run() == 0 and "fine" in api._clips


def test_it_is_recorded_as_video_gone_not_expired_or_rejected(world):
    api._clips["dud"] = _clip("dud")
    world.run()
    rows = ss.all_rows()
    assert rows and rows[0]["video_gone"] == 1
    assert rows[0]["expired"] == 0 and rows[0]["rejected"] == 0
    assert ss.evictions_since("u1", 0) == 0, "the queue-full notice would have fired"
    assert ss.totals_by_user()["u1"]["video_gone"] == 1


def test_one_bell_note_a_day_per_account(world):
    api._clips["a"] = _clip("a"); api._clips["b"] = _clip("b")
    api._clips["c"] = _clip("c", uid="u2")
    assert world.run() == 3
    assert len(world.notices) == 1 and set(world.notices[0]) == {"u1", "u2"}
    assert "2 unreviewed Kick clips" in world.notices[0]["u1"]["body"]
    assert "1 unreviewed Kick clip " in world.notices[0]["u2"]["body"]
    assert ("inbox_changed", None, "u1") in world.sent and ("inbox_changed", None, "u2") in world.sent
    # Later the same day: removed again, but no second note.
    api._clips["d"] = _clip("d")
    assert world.run() == 1 and len(world.notices) == 1


def test_nothing_to_do_touches_nothing(world):
    assert world.run() == 0 and world.saves == [] and world.sent == []


def test_the_loop_is_started_and_the_admin_tile_shows_the_count():
    import pathlib
    main_src = pathlib.Path("src/main.py").read_text()
    assert "sweep_videoless_kick_clips_task()" in main_src
    assert "Kick removed, video gone" in api.ADMIN_HTML
