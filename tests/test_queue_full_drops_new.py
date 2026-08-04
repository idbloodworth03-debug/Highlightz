"""A full queue refuses the NEW moment. It no longer deletes an old clip.

Before 2026-08-03 the cap evicted the oldest unreviewed clip to make room, so a
busy stream silently destroyed work the user already had. Now the newest moment
is the one refused — less destructive, and it makes "you missed a highlight"
literally true.

The check happens BEFORE the Twitch clip is created. That ordering is the whole
point: creating it and then discarding our record would leave an orphan clip on
the user's Twitch account that never appears in Highlightz, and would spend a
Helix call from a budget shared with every other user.
"""

import asyncio
import inspect

import pytest

from src.dashboard import api
from src.stats import stream_stats as ss


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda: None)
    sent = []

    async def _bcast(payload, user_id=None):
        sent.append(payload)
    monkeypatch.setattr(api, "broadcast", _bcast)

    from src.auth import users as us
    monkeypatch.setattr(us, "get_by_id",
                        lambda uid: {"id": uid, "subscription_status": "none"})  # free: 15
    api._clips.clear()
    yield sent
    api._clips.clear()


def _fill(n, uid="u1"):
    for i in range(n):
        api._clips[f"p{i}"] = {"id": f"p{i}", "user_id": uid, "channel": "aceu",
                               "status": "pending", "created_at": 1000.0 + i}


def _incoming(cid="new"):
    return {"id": cid, "user_id": "u1", "channel": "aceu", "status": "pending",
            "created_at": 9999.0, "platform": "twitch",
            "twitch_url": "https://clips.twitch.tv/N"}


def test_the_new_clip_is_dropped_when_the_queue_is_full(clean):
    _fill(15)
    asyncio.run(api.notify_clip_ready(_incoming()))
    assert "new" not in api._clips, "the new clip was stored over the cap"
    assert len(api._clips) == 15


def test_no_existing_clip_is_destroyed(clean):
    """THE regression this replaces. The old behaviour deleted p0."""
    _fill(15)
    before = set(api._clips)
    asyncio.run(api.notify_clip_ready(_incoming()))
    assert set(api._clips) == before, "a clip the user already had was deleted"


def test_a_clip_is_still_accepted_with_room_to_spare(clean):
    _fill(14)
    asyncio.run(api.notify_clip_ready(_incoming()))
    assert "new" in api._clips


def test_the_user_is_told_it_was_missed_not_deleted(clean):
    _fill(15)
    asyncio.run(api.notify_clip_ready(_incoming()))
    events = [p.get("event") for p in clean]
    assert "clip_missed" in events
    assert "clip_ready" not in events, "announced a clip that was never saved"
    assert "clip_removed" not in events, "claimed something was removed"


def test_a_miss_is_recorded_as_its_own_outcome(clean):
    """Not a 'caught' (no clip exists) and not a rejection (the user never saw
    it). Folding it into either would corrupt the keep rate shown to
    streamers."""
    _fill(15)
    asyncio.run(api.notify_clip_ready(_incoming()))
    row = ss.for_channel("u1", "aceu")
    assert row is not None
    assert row["missed"] == 1
    assert row["caught"] == 0, "a moment that was never clipped counted as caught"
    assert row["rejected"] == 0 and row["approved"] == 0


def test_missed_moments_do_not_change_the_keep_rate(clean):
    """The clip record is shown to streamers. A miss is not a judgement on a
    clip, so it must not move kept-of-caught."""
    ss.record(ss.CAUGHT, {"id": "a", "user_id": "u1", "channel": "aceu", "created_at": 1.0})
    ss.record(ss.APPROVED, {"id": "a", "user_id": "u1", "channel": "aceu", "created_at": 1.0})
    before = ss.for_channel("u1", "aceu")["kept_pct"]
    for i in range(5):
        ss.record(ss.MISSED, {"id": "", "user_id": "u1", "channel": "aceu", "created_at": 1.0})
    after = ss.for_channel("u1", "aceu")
    assert after["kept_pct"] == before == 100
    assert after["missed"] == 5


def test_the_processor_checks_before_creating_the_twitch_clip():
    """Ordering is the point. Checking afterwards leaves an orphan clip on the
    user's Twitch account and spends a Helix call for nothing."""
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    check_at = src.index("pending_room(")
    process_at = src.index("processor.process(job)")
    assert check_at < process_at, "the cap is checked after the clip is created"
    assert "notify_clip_missed" in src
    # Ordering alone is not enough: the check has to be ACTED ON. Found by
    # mutation — replacing the condition with `if False:` left both the call
    # and the ordering intact and this test still passed.
    guard = src[check_at:process_at]
    assert "if _used >= _cap:" in guard, "the pre-check result is not acted on"
    assert "continue" in guard, "a full queue does not skip the clip"


def test_the_in_process_check_is_still_there_for_the_race():
    """The processor's pre-check can go stale between deciding and arriving.
    Without this second check a race puts the queue over cap."""
    src = inspect.getsource(api.notify_clip_ready)
    assert "pending_cap" in src and "dropped" in src


def test_nothing_is_broadcast_while_holding_the_data_lock():
    """notify_clip_missed writes to sockets. Awaiting that under _data_lock
    stalls every other clip in the pipeline."""
    src = inspect.getsource(api.notify_clip_ready)
    lock_at = src.index("async with _data_lock:")
    notify_at = src.index("await notify_clip_missed(")
    outdent = src.index("\n    if dropped:")
    assert lock_at < outdent < notify_at, \
        "the miss is broadcast from inside the data lock"
