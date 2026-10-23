"""Twitch accepting a clip and never finishing it is a warning, not an error.

The owner's alert email of 2026-10-20 18:50 UTC: "clip_processor_error ...
Twitch clip '...' for 'jynxzi' never finished processing". Create Clip had
answered 202, get_clip polled ~50s, and the clip never became playable. That
is Twitch's side, and prod shows it is routine: 107 of 4,076 clips in the 7
days to 2026-10-20 (~2.6%, ~15 a day). As an error, each one emailed.

Now: get_clip's warning (twitch_clip_not_ready, which now names the channel)
is the only log of it, so one does not email and a burst still does. The user
is told what they were told before, which is true for this failure: the next
moment gets a fresh clip. Every other failure is still an error.

Waiting longer was ruled out with the same week of data: of the 3,969 clips
that did finish, the slowest took 42.5s, and none in the last 7s before the
cutoff.
"""

import asyncio
import time

import pytest

from src.output import twitch_clips
from src.queue.job_queue import ClipJob


class _Log:
    """Records what a module logs, by level."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        def _log(event, **kw):
            self.lines.append((level, event, kw))
        return _log

    def events(self, level):
        return [e for lv, e, _ in self.lines if lv == level]


def _job(cid="c1", channel="jynxzi"):
    j = ClipJob(clip_id=cid, channel=channel, platform="twitch", user_id="u1",
                trigger_score=80.0, trigger_signals=[], chat_snapshot=[],
                stream_title="", game="", virality_score=0.0, clip_title="",
                post_roll=0)
    j.created_at = time.time()
    return j


# ── the type ─────────────────────────────────────────────────────────────────

def test_it_is_a_runtime_error_and_none_of_the_refusals():
    """main catches the three refusals first, and they back the channel off or
    stop it. A clip Twitch never finished must not do either: the next moment
    on the same channel usually works."""
    assert issubclass(twitch_clips.ClipNotReadyError, RuntimeError)
    for refusal in (twitch_clips.ClipNotAuthorizedError,
                    twitch_clips.ClipTitleRejectedError,
                    twitch_clips.ClipClassificationError):
        assert not issubclass(twitch_clips.ClipNotReadyError, refusal)


# ── the processor raises it, and get_clip names the channel ──────────────────

def test_the_processor_raises_it_when_twitch_never_finishes(monkeypatch):
    from src.processor import clip_processor as cp
    asked = {}

    async def _token(uid): return "tok"
    async def _bid(login): return "123"
    async def _create(token, bid): return "CautiousHotNuggets"

    async def _get(slug, *a, **k):
        asked.update(slug=slug, **k)
        return None
    monkeypatch.setattr(cp.user_store, "get_valid_twitch_token", _token)
    monkeypatch.setattr(twitch_clips, "resolve_broadcaster_id", _bid)
    monkeypatch.setattr(twitch_clips, "create_clip", _create)
    monkeypatch.setattr(twitch_clips, "get_clip", _get)

    with pytest.raises(twitch_clips.ClipNotReadyError, match="never finished processing"):
        asyncio.run(cp.ClipProcessor().process(_job()))
    assert asked == {"slug": "CautiousHotNuggets", "channel": "jynxzi"}


def test_get_clip_names_the_channel_when_it_gives_up(monkeypatch):
    """So maintenance.why_no_clips can count these per channel; a channel where
    every clip fails is the case worth finding."""
    import aiohttp

    class _Resp:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self): return {"data": []}

    class _Session:
        def get(self, *a, **k): return _Resp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    async def _app_token(session): return "app"
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _Session())
    monkeypatch.setattr(twitch_clips, "_get_app_token", _app_token)
    rec = _Log()
    monkeypatch.setattr(twitch_clips, "log", rec)

    assert asyncio.run(twitch_clips.get_clip("s1", attempts=2, delay=0, channel="jynxzi")) is None
    [(level, event, kw)] = rec.lines
    assert (level, event) == ("warning", "twitch_clip_not_ready")
    assert kw["channel"] == "jynxzi" and kw["slug"] == "s1"


# ── the clip processor loop ──────────────────────────────────────────────────

@pytest.fixture
def loop(monkeypatch):
    """main.run_clip_processor for one job, with what it logs, tells the user
    and discards recorded."""
    import src.main as M
    from src.clips import files as clip_files
    from src.dashboard import api as dash

    rec, sent, discarded = _Log(), [], []
    monkeypatch.setattr(M, "log", rec)
    monkeypatch.setattr(clip_files, "discard", lambda cid: discarded.append(cid))

    async def _bcast(msg, user_id=None):
        sent.append((msg, user_id))

    async def _noop(*a, **k): return None
    monkeypatch.setattr(dash, "broadcast", _bcast)
    monkeypatch.setattr(dash, "notify_clip_missed", _noop)
    monkeypatch.setattr(dash, "pending_room", lambda uid: (0, 200))

    def run(exc):
        served = {"done": False}

        class Q:
            async def pop(self, timeout=5):
                await asyncio.sleep(0)
                if served["done"]:
                    return None
                served["done"] = True
                return _job()

        async def _never(slug, *a, **k):
            return None
        monkeypatch.setattr(twitch_clips, "get_clip", _never)

        class P:
            async def start(self, job):
                if isinstance(exc, twitch_clips.ClipNotReadyError):
                    # The real shape since 2026-10-23: Create Clip took it, the
                    # queue hands it off, and the finish off the queue finds
                    # Twitch never made it.
                    from src.processor.clip_processor import PendingTwitchClip
                    return PendingTwitchClip(object(), "s1", job.channel)
                raise exc
        monkeypatch.setattr(M, "_queue", Q())
        monkeypatch.setattr(M, "ClipProcessor", lambda *a, **k: P())

        async def go():
            task = asyncio.create_task(M.run_clip_processor())
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        asyncio.run(go())
        return rec, sent, discarded
    return run


def test_a_clip_twitch_never_finished_is_not_an_error(loop):
    rec, sent, discarded = loop(twitch_clips.ClipNotReadyError(
        "Twitch clip 'x' for 'jynxzi' never finished processing"))
    assert "clip_processor_error" not in rec.events("error"), \
        "a routine Twitch failure is logged as an error again, so each one emails"
    # The user still hears about it, in the same words as before, and the
    # moment's video file still goes (it has no clip to belong to).
    assert sent == [({"event": "clip_failed", "channel": "jynxzi",
                      "message": "A clip from jynxzi couldn't be captured — "
                                 "it'll try again on the next moment."}, "u1")]
    assert discarded == ["c1"]


def test_any_other_failure_is_still_an_error(loop):
    rec, sent, discarded = loop(RuntimeError("Could not resolve broadcaster id for 'jynxzi'"))
    assert rec.events("error") == ["clip_processor_error"]
    assert [m["event"] for m, _ in sent] == ["clip_failed"]
    assert discarded == ["c1"]
