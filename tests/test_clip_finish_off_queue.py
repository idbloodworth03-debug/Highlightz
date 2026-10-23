"""A Twitch clip is finished OFF the clip queue (2026-10-23).

WHAT HAPPENED. run_clip_processor runs one job at a time for every account,
channel and platform, and it used to wait inside each Twitch job for Get
Clips to show the clip. When Twitch never makes one, that wait is ~50s. On
2026-10-23 Twitch failed jynxzi's clips for most of an hour: the line was
held for 29 minutes, waits reached 90s, and 9 moments on three channels were
dropped as stale (clip_not_ready_report, owner's run).

NOW. The queue stops at Create Clip, the call Twitch captures from, and hands
the wait to a background task (main._finish_twitch_clip). What the queue did
after the wait happens there through the same helpers. Pinned here, on the
real loop:
  * the next job starts while an earlier clip is still being checked;
  * a clip finished off the queue is stored, announced and keeps its file;
    one Twitch never made is toasted and its file discarded (the not-ready
    tests in test_clip_not_ready.py cover the words and the log level);
  * clips still being checked count toward the review-queue cap, which is
    checked before Create Clip so a full queue never leaves an orphan clip
    on the user's Twitch account;
  * at most _POLL_SLOTS checks run at once, without slowing the queue;
  * a success does not erase a refusal recorded after Twitch accepted it.
"""

import asyncio
import time

import pytest

from src.output import twitch_clips
from src.processor.clip_processor import PendingTwitchClip
from src.processor.metadata import ClipMetadata
from src.queue.job_queue import ClipJob


def _job(cid, user="u1", channel="jynxzi", platform="twitch"):
    j = ClipJob(clip_id=cid, channel=channel, platform=platform, user_id=user,
                trigger_score=80.0, trigger_signals=[], chat_snapshot=[],
                stream_title="", game="", virality_score=0.0, clip_title="",
                post_roll=0)
    j.created_at = time.time()
    return j


@pytest.fixture
def env(monkeypatch):
    import src.main as M
    from src.clips import files as clip_files
    from src.dashboard import api as dash

    rec = {"started": [], "stored": [], "missed": [], "discarded": [], "sent": [],
           "cleared": [], "room": {}}

    async def _bc(msg, user_id=None):
        rec["sent"].append((msg.get("event"), user_id))

    async def _missed(uid, ch, reason="queue_full"):
        rec["missed"].append((uid, ch, reason))

    async def _ready(clip):
        rec["stored"].append(clip)
        return True

    def _clear(channel, older_than=None):
        rec["cleared"].append((channel, older_than))
        return []

    monkeypatch.setattr(dash, "broadcast", _bc)
    monkeypatch.setattr(dash, "notify_clip_missed", _missed)
    monkeypatch.setattr(dash, "notify_clip_ready", _ready)
    monkeypatch.setattr(dash, "pending_room", lambda uid: rec["room"].get(uid, (0, 200)))
    monkeypatch.setattr(clip_files, "discard", lambda cid: rec["discarded"].append(cid))
    monkeypatch.setattr(M._refusals, "clear", _clear)
    M._twitch_in_flight.clear()

    def run(jobs, start, seconds=0.4):
        """Serve `jobs` to the real loop; `start(job)` is the processor's."""
        todo = list(jobs)

        class Q:
            async def pop(self, timeout=5):
                await asyncio.sleep(0)
                return todo.pop(0) if todo else None

        class P:
            async def start(self, job):
                rec["started"].append((job.clip_id, time.monotonic()))
                return await start(job)

        monkeypatch.setattr(M, "_queue", Q())
        monkeypatch.setattr(M, "ClipProcessor", lambda *a, **k: P())

        async def go():
            t0 = time.monotonic()
            task = asyncio.create_task(M.run_clip_processor())
            await asyncio.sleep(seconds)
            in_flight = dict(M._twitch_in_flight)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return t0, in_flight
        t0, in_flight = asyncio.run(go())
        rec["t0"], rec["in_flight_at_end"] = t0, in_flight
        return rec

    yield run, M
    M._twitch_in_flight.clear()


def _pending(job):
    return PendingTwitchClip(ClipMetadata(id=job.clip_id, channel=job.channel, platform="twitch",
                                          user_id=job.user_id), "slug-" + job.clip_id, job.channel)


def test_the_next_job_starts_while_a_clip_is_still_being_checked(env, monkeypatch):
    run, M = env

    async def _slow(slug, *a, **k):          # Twitch never finishes this one in time
        await asyncio.sleep(30)
    monkeypatch.setattr(twitch_clips, "get_clip", _slow)

    async def start(job):
        return _pending(job)

    rec = run([_job("first"), _job("second", user="u2", channel="xqc")], start)
    started = dict(rec["started"])
    assert set(started) == {"first", "second"}, "the second job never started"
    assert started["second"] - started["first"] < 0.2, \
        "the queue waited on the first clip's check before starting the next job"
    # Neither is decided yet: both are counted as in flight, nothing discarded.
    assert rec["in_flight_at_end"] == {"u1": 1, "u2": 1}
    assert rec["stored"] == []


def test_a_clip_finished_off_the_queue_is_stored_and_keeps_its_file(env, monkeypatch):
    run, M = env

    async def _ready(slug, *a, **k):
        return {"id": slug, "url": f"https://clips.twitch.tv/{slug}", "thumbnail_url": "t.jpg",
                "embed_url": "e", "duration": 28.0, "title": "Huge play"}
    monkeypatch.setattr(twitch_clips, "get_clip", _ready)
    pendings = []

    async def start(job):
        p = _pending(job)
        pendings.append(p)
        return p

    rec = run([_job("ok")], start)
    [stored] = rec["stored"]
    assert stored["id"] == "ok" and stored["twitch_url"] == "https://clips.twitch.tv/slug-ok"
    assert stored["duration_seconds"] == 28.0 and stored["clip_title"] == "Huge play"
    assert rec["discarded"] == [], "a kept clip lost its file"
    assert rec["in_flight_at_end"] == {}, "a finished clip is still counted as in flight"
    # The refusal record is cleared only of refusals older than Twitch's 202.
    assert rec["cleared"] == [("jynxzi", pendings[0].requested_at)]


def test_a_clip_twitch_never_made_is_discarded_and_no_longer_counted(env, monkeypatch):
    run, M = env

    async def _never(slug, *a, **k):
        return None
    monkeypatch.setattr(twitch_clips, "get_clip", _never)

    async def start(job):
        return _pending(job)

    rec = run([_job("lost")], start)
    assert rec["stored"] == [] and rec["discarded"] == ["lost"]
    assert ("clip_failed", "u1") in rec["sent"]
    assert rec["in_flight_at_end"] == {}
    assert rec["cleared"] == [], "a lost clip cleared the channel's refusal record"


def test_clips_still_being_checked_count_toward_the_review_queue_cap(env, monkeypatch):
    """19 of 20 slots used and one clip in flight: the next moment for that
    account is skipped as a full queue BEFORE Create Clip. Another account's
    job behind it is not held up."""
    run, M = env

    async def _slow(slug, *a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(twitch_clips, "get_clip", _slow)

    async def start(job):
        return _pending(job)

    import src.dashboard.api as dash
    rooms = {"u1": (19, 20)}
    monkeypatch.setattr(dash, "pending_room", lambda uid: rooms.get(uid, (0, 200)))
    rec = run([_job("a"), _job("b"), _job("c", user="u2", channel="xqc")], start)
    assert [cid for cid, _ in rec["started"]] == ["a", "c"], "b was clipped into a full queue"
    assert rec["missed"] == [("u1", "jynxzi", "queue_full")]
    assert "b" in rec["discarded"]


def test_at_most_poll_slots_checks_run_at_once_and_the_queue_still_moves(env, monkeypatch):
    run, M = env
    monkeypatch.setattr(M, "_POLL_SLOTS", 2)
    live = {"now": 0, "max": 0}

    async def _slow(slug, *a, **k):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        try:
            await asyncio.sleep(30)
        finally:
            live["now"] -= 1
    monkeypatch.setattr(twitch_clips, "get_clip", _slow)

    async def start(job):
        return _pending(job)

    jobs = [_job(f"j{k}", user=f"u{k}") for k in range(4)]
    rec = run(jobs, start)
    assert len(rec["started"]) == 4, "the slot limit held up the queue"
    assert live["max"] == 2, f"{live['max']} checks ran at once"


def test_a_kick_clip_is_still_finished_in_line(env, monkeypatch):
    """Kick has no clip to wait for: start() returns the finished record and
    the queue stores it itself, as before."""
    run, M = env

    async def start(job):
        return ClipMetadata(id=job.clip_id, channel=job.channel, platform="kick", user_id=job.user_id)

    rec = run([_job("k", platform="kick", channel="westcol")], start)
    assert [c["id"] for c in rec["stored"]] == ["k"]
    assert rec["discarded"] == [] and rec["in_flight_at_end"] == {}
    assert rec["cleared"] == [("westcol", None)]


def test_a_success_does_not_erase_a_refusal_recorded_after_twitch_accepted_it(tmp_path, monkeypatch):
    from src.stats import clip_refusals as refusals
    monkeypatch.setattr(refusals, "_FILE", tmp_path / "refusals.json")
    accepted = time.time()
    time.sleep(0.01)
    refusals.record("jynxzi", refusals.TITLE_AUTOMOD, "u1")
    assert refusals.clear("jynxzi", older_than=accepted) == []          # newer: it stays
    assert [r["channel"] for r in refusals.all_rows()] == ["jynxzi"]
    assert refusals.clear("jynxzi", older_than=time.time() + 1) == ["u1"]
    assert refusals.all_rows() == []
    refusals.record("xqc", refusals.CLASSIFICATION, "u2")
    assert refusals.clear("xqc") == ["u2"]                               # no bound: as before
