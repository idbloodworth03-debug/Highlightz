"""No video file for a moment that never becomes a clip (owner, 2026-10-13).

From a support ticket: a Free account on Westcol's Kick stream with a full
review queue. Every trigger still cut a video file — the stream worker cuts
about 30s after the moment, independently of the clip processor — while the
processor skipped the moment as queue-full. 27 files in one day with no clip
record pointing at them: nobody could see, download or edit them, and they sat
in the store until the cap evicted them, pushing out files people CAN download.

Now the processor discards the file for every moment it does not keep, and the
cut checks before writing (and before trimming other people's files to make
room) and again after.

Plus the label that caused the ticket: the Live Streams box that counts approve
+ reject decisions said "Total clips", and an empty queue next to "22" read as
22 clips gone missing. It says "Reviewed" now.
"""

import asyncio
import time

import pytest

from src.clips import files as clip_files
from src.queue.job_queue import ClipJob


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()
    monkeypatch.setattr(clip_files, "_DISCARDED", {})
    return tmp_path / "clipfiles"


def _write(clip_id, data=b"x" * 2048):
    p = clip_files.path_for(clip_id)
    p.write_bytes(data)
    return p


# ── the registry ─────────────────────────────────────────────────────────────

def test_discard_deletes_an_existing_file_and_remembers(store):
    p = _write("c1")
    clip_files.discard("c1")
    assert not p.exists()
    assert clip_files.discarded("c1")
    assert not clip_files.discarded("c2")


def test_a_discard_ages_out(store, monkeypatch):
    clip_files.discard("old")
    later = time.time() + clip_files._DISCARD_TTL_S + 1
    monkeypatch.setattr(clip_files.time, "time", lambda: later)
    assert not clip_files.discarded("old")
    clip_files.discard("new")                    # and is pruned on the next mark
    assert "old" not in clip_files._DISCARDED


# ── the processor: anything not kept is discarded ───────────────────────────

def _job(cid="m1", age=0.0, platform="kick"):
    j = ClipJob(clip_id=cid, channel="westcol", platform=platform, user_id="u1",
                trigger_score=80.0, trigger_signals=[], chat_snapshot=[],
                stream_title="", game="", virality_score=0.0, clip_title="",
                post_roll=0)
    j.created_at = time.time() - age
    return j


@pytest.fixture
def loop(monkeypatch):
    import src.main as M
    from src.dashboard import api as dash
    discarded = []
    monkeypatch.setattr(clip_files, "discard", lambda cid: discarded.append(cid))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dash, "broadcast", _noop)
    monkeypatch.setattr(dash, "notify_clip_missed", _noop)
    monkeypatch.setattr(dash, "pending_room", lambda uid: (0, 200))

    def run(job, processor=None, kept=True):
        served = {"done": False}

        class Q:
            async def pop(self, timeout=5):
                await asyncio.sleep(0)
                if served["done"]:
                    return None
                served["done"] = True
                return job
        monkeypatch.setattr(M, "_queue", Q())
        if processor is not None:
            monkeypatch.setattr(M, "ClipProcessor", lambda *a, **k: processor)

        async def _ready(meta):
            return kept
        monkeypatch.setattr(dash, "notify_clip_ready", _ready)

        async def go():
            task = asyncio.create_task(M.run_clip_processor())
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        asyncio.run(go())
        return discarded
    return run, dash, monkeypatch


class _Meta:
    def __init__(self, cid):
        self.cid = cid

    def to_dict(self):
        return {"id": self.cid}


class _Proc:
    def __init__(self, raises=None):
        self.raises = raises

    async def process(self, job):
        if self.raises:
            raise self.raises
        return _Meta(job.clip_id)


def test_a_queue_full_skip_discards_the_file(loop):
    run, dash, mp = loop
    mp.setattr(dash, "pending_room", lambda uid: (20, 20))
    assert run(_job("full")) == ["full"]


def test_a_stale_drop_discards_the_file(loop):
    run, dash, mp = loop
    assert run(_job("stale", age=500)) == ["stale"]


def test_an_error_discards_the_file(loop):
    run, dash, mp = loop
    assert run(_job("boom"), processor=_Proc(raises=RuntimeError("x"))) == ["boom"]


def test_a_clip_that_is_kept_keeps_its_file(loop):
    run, dash, mp = loop
    assert run(_job("kept"), processor=_Proc(), kept=True) == []


def test_a_clip_refused_at_storage_is_discarded(loop):
    """notify_clip_ready said no — a duplicate, or the queue filled in the race."""
    run, dash, mp = loop
    assert run(_job("dup"), processor=_Proc(), kept=False) == ["dup"]


# ── notify_clip_ready reports what it did ────────────────────────────────────

@pytest.fixture
def ready(tmp_path, monkeypatch):
    from src.dashboard import api
    from src.stats import stream_stats as ss
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(api, "notify_clip_missed", _noop)
    monkeypatch.setattr(api, "_fetch_when_capture_misses", _noop)
    from src.auth import users as us
    monkeypatch.setattr(us, "get_by_id", lambda uid: {"id": uid, "subscription_status": "none"})
    api._clips.clear()
    yield api
    api._clips.clear()


def _clip(cid, ts):
    return {"id": cid, "user_id": "u1", "channel": "westcol", "status": "pending",
            "created_at": ts, "platform": "kick"}


def test_notify_says_true_when_it_stores(ready):
    assert asyncio.run(ready.notify_clip_ready(_clip("a", 1000.0))) is True
    assert "a" in ready._clips


def test_notify_says_false_for_a_duplicate(ready):
    asyncio.run(ready.notify_clip_ready(_clip("a", 1000.0)))
    assert asyncio.run(ready.notify_clip_ready(_clip("b", 1010.0))) is False


def test_notify_says_false_when_the_queue_is_full(ready):
    from src.billing.plans import PLAN_LIMITS
    for i in range(PLAN_LIMITS["free"]["max_pending"]):
        ready._clips[f"p{i}"] = _clip(f"p{i}", 100.0 * i)
    assert asyncio.run(ready.notify_clip_ready(_clip("late", 99999.0))) is False


# ── the cut ──────────────────────────────────────────────────────────────────

@pytest.fixture
def cut(monkeypatch):
    from src.ingestion import stream_worker as sw
    from src.dashboard import api as dash

    async def _nosleep(*a, **k):
        return None
    monkeypatch.setattr(sw.asyncio, "sleep", _nosleep)
    sent, trims = [], []

    async def _bcast(msg, user_id=None):
        sent.append(msg.get("event"))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dash, "broadcast", _bcast)
    monkeypatch.setattr(dash, "on_clip_file_ready", _noop)
    monkeypatch.setattr(clip_files, "headroom_ok", lambda: False)
    monkeypatch.setattr(clip_files, "trim_to_cap", lambda *a, **k: trims.append(1) or 1)

    class Rec:
        def __init__(self, during=None):
            self.calls, self.during = 0, during

        async def cut(self, start, end, out):
            self.calls += 1
            if self.during:
                self.during()
            out.write_bytes(b"v" * 4096)
            return True

    def make(rec):
        w = sw.StreamWorker.__new__(sw.StreamWorker)
        w._recorder, w._running = rec, True

        class Cfg:
            channel, user_id = "westcol", "u1"
        w._config = Cfg()
        return w
    return make, Rec, sent, trims


def test_a_discarded_moment_is_never_cut_or_made_room_for(cut):
    make, Rec, sent, trims = cut
    clip_files._DISCARDED["skip"] = time.time()
    rec = Rec()
    asyncio.run(make(rec)._cut_local_file("skip", time.time(), 5, 0))
    assert rec.calls == 0, "cut a file for a moment the processor dropped"
    assert not trims, "evicted other files to make room for one nobody can reach"
    assert not clip_files.exists("skip") and "clip_file_ready" not in sent


def test_a_discard_during_the_cut_removes_the_file(cut):
    make, Rec, sent, trims = cut
    rec = Rec(during=lambda: clip_files._DISCARDED.__setitem__("mid", time.time()))
    asyncio.run(make(rec)._cut_local_file("mid", time.time(), 5, 0))
    assert rec.calls == 1
    assert not clip_files.exists("mid"), "kept a file the processor had dropped"
    assert "clip_file_ready" not in sent


def test_a_kept_moment_is_cut_as_before(cut):
    make, Rec, sent, trims = cut
    rec = Rec()
    asyncio.run(make(rec)._cut_local_file("ok", time.time(), 5, 0))
    assert clip_files.exists("ok") and "clip_file_ready" in sent


# ── the label ────────────────────────────────────────────────────────────────

def test_the_live_streams_box_says_reviewed():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert '<div className="k">Reviewed</div><div className="v">{p.total_clips||0}</div>' in h
    assert '<div className="k">Total clips</div><div className="v">{p.total_clips||0}</div>' not in h
