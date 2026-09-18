"""Going idle PAUSES a user's channels. It must never delete them.

THE BUG THIS FILE EXISTS TO PREVENT COMING BACK. `_streams` is both the worker
registry and the user's saved channel list. The idle reaper freed the core by
popping records out of it, which also threw away the configuration: anyone who
closed the tab for eight hours returned to an empty Live Streams tab, while
the FAQ promised they could shut the laptop. Measured on production
2026-09-18, sixty accounts had two channels registered between them, and
twenty two of those accounts had previously produced clips from forty five
channels.

So: the record survives with status "paused", the core and the live slot are
freed, and the next authenticated request from that user starts them again.
"""

import asyncio

import pytest

from src.dashboard import api


@pytest.fixture
def scene(monkeypatch):
    started, stopped, sent = [], [], []

    async def _new(channel, platform, preset, uid=""):
        started.append((channel, platform, preset, uid))

    async def _rm(channel, uid=""):
        stopped.append((channel, uid))

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))

    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_paused_users", set())
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._live_slots.clear()

    def add(uid, channel, platform="twitch", preset="fps", status="live"):
        key = f"{uid}:{channel}"
        api._streams[key] = {"channel": channel, "platform": platform, "preset": preset,
                             "status": status, "user_id": uid, "added_at": 1.0}
        api._live_slots.add(key)
        return key

    return type("S", (), {"add": staticmethod(add), "started": started,
                          "stopped": stopped, "sent": sent})


# ── pausing ──────────────────────────────────────────────────────────────────

def test_idle_keeps_the_channel_and_frees_the_slot(scene):
    scene.add("u1", "jynxzi")
    scene.add("u1", "kestrel")
    n = asyncio.run(api._pause_user_streams("u1"))

    assert n == 2
    assert len(api._streams) == 2, "the reaper deleted the user's channels again"
    assert {r["status"] for r in api._streams.values()} == {"paused"}
    assert all(r.get("paused_at") for r in api._streams.values())
    # What the reaper is actually for: the core and the slots come back.
    assert api._live_slots == set(), "a paused channel still holds a live slot"
    assert sorted(c for c, _u in scene.stopped) == ["jynxzi", "kestrel"]
    assert api._paused_users == {"u1"}
    # Realtime contract: a tab left open sees them go quiet.
    assert [e for e, _u, _m in scene.sent] == ["stream_updated", "stream_updated"]


def test_pausing_twice_changes_nothing_and_stays_quiet(scene):
    scene.add("u1", "jynxzi")
    assert asyncio.run(api._pause_user_streams("u1")) == 1
    scene.sent.clear(); scene.stopped.clear()
    assert asyncio.run(api._pause_user_streams("u1")) == 0
    assert scene.sent == [] and scene.stopped == []


def test_one_users_idle_does_not_touch_another(scene):
    scene.add("u1", "jynxzi")
    k2 = scene.add("u2", "kestrel")
    asyncio.run(api._pause_user_streams("u1"))
    assert api._streams[k2]["status"] == "live"
    assert k2 in api._live_slots
    assert api._paused_users == {"u1"}


# ── resuming ─────────────────────────────────────────────────────────────────

def test_coming_back_restarts_exactly_what_was_paused(scene):
    scene.add("u1", "jynxzi", preset="fps")
    scene.add("u1", "xqc", platform="kick", preset="variety")
    asyncio.run(api._pause_user_streams("u1"))
    scene.sent.clear()

    n = asyncio.run(api._resume_user_streams("u1"))
    assert n == 2
    assert {r["status"] for r in api._streams.values()} == {"starting"}
    assert not any(r.get("paused_at") for r in api._streams.values())
    # The platform and preset survive the round trip, or a Kick channel comes
    # back as a Twitch one and the preset resets to default.
    assert sorted(scene.started) == [("jynxzi", "twitch", "fps", "u1"),
                                     ("xqc", "kick", "variety", "u1")]
    assert api._paused_users == set()
    assert [e for e, _u, _m in scene.sent] == ["stream_updated", "stream_updated"]


def test_resuming_never_touches_a_channel_that_was_not_paused(scene):
    k = scene.add("u1", "jynxzi", status="live")
    assert asyncio.run(api._resume_user_streams("u1")) == 0
    assert api._streams[k]["status"] == "live"
    assert scene.started == []


def test_the_slot_is_not_taken_back_by_the_resume_itself(scene):
    """Admission control belongs to the worker (stream_worker asks
    acquire_live_slot at go-live). If the resume grabbed slots directly, a
    wave of returning users would blow past the cap instead of queueing."""
    scene.add("u1", "jynxzi")
    asyncio.run(api._pause_user_streams("u1"))
    asyncio.run(api._resume_user_streams("u1"))
    assert api._live_slots == set()


# ── the wiring ───────────────────────────────────────────────────────────────

def test_the_reaper_pauses_and_access_loss_still_deletes():
    """Two different events that used to share one function. Idle must not
    delete; a revoked or expired account still stops for good."""
    import inspect
    reaper = inspect.getsource(api.idle_stream_reaper) if hasattr(api, "idle_stream_reaper") else ""
    if not reaper:                       # the task is defined inline
        src = inspect.getsource(api)
        reaper = src[src.index("Background task: stop stream workers"):]
        reaper = reaper[:reaper.index("# ── VOD analysis")]
    assert "_pause_user_streams(uid)" in reaper
    assert "_stop_user_streams_now" not in reaper, "idle deletes channels again"
    # Access loss keeps the old behaviour.
    assert "_stop_user_streams_now" in inspect.getsource(api.admin_revoke_access)


def test_a_returning_user_is_resumed_from_the_middleware():
    import inspect
    src = inspect.getsource(api.AuthMiddleware)
    assert "if uid in _paused_users:" in src
    assert "_resume_user_streams(uid)" in src
    # Discarding before the task starts is the lock against a page load firing
    # a dozen resumes at once.
    i = src.index("_paused_users.discard(uid)")
    assert i < src.index("asyncio.create_task(_resume_user_streams(uid))")


def test_paused_users_is_rebuilt_from_disk_so_a_restart_cannot_strand_anyone():
    import inspect
    src = inspect.getsource(api)
    block = src[src.index("_paused_users: set[str] = {"):]
    block = block[:block.index("}") + 1]
    assert 's.get("status") == "paused"' in block
    assert "_streams.values()" in block


def test_the_dashboard_says_paused_means_it_comes_back():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert h.count("'paused · resumes when you return'") == 2, \
        "both the card and the detail header must explain the state"
    assert "They start again by themselves next time you open Highlightz." in h
    assert "Restart them from the Live Streams tab" not in h, \
        "the toast still tells users to re-add channels by hand"
