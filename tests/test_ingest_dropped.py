"""THE SPLIT: a moment the clip processor dropped gets no file, in a worker too.

docs/HANDOFF.md, "THE SPLIT". The stream worker cuts a clip's video about 30
seconds after the trigger; the clip processor decides much sooner whether the
moment becomes a clip at all, and marks the ones that will not
(clips.files.discard, 2026-10-13: 27 files nobody could see from one channel
in one day). That mark is in ONE process's memory, the web process's. A
worker process looked in its own empty copy, so with every channel in a
worker the 2026-10-13 problem was back at full size. The cut now asks the web
process (host.clip_dropped).

Held here:
  * with the split off nothing changed: the same lookup, at the same point,
    without giving the loop a turn;
  * in a worker the question goes to the web process and the worker's own
    copy of the registry is not what answers;
  * nobody answering means "not dropped": the file is cut, and the web
    process removes it when it is reported if it was dropped after all;
  * a REAL worker process running the REAL cut: a moment dropped before the
    cut is never written, one dropped while the cut ran is removed, and one
    that was kept arrives.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import time

import pytest

from src.clips import files as clip_files
from src.dashboard import api, ingest as ingest_mod
from src.ingestion import host, pool as pool_mod

REPO = os.getcwd()


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "store" / "clipfiles"
    root.mkdir(parents=True)
    monkeypatch.setattr(clip_files, "_ROOT", root)
    monkeypatch.setattr(clip_files, "_DISCARDED", {})
    clip_files._forget_scan()
    return root


# ── one process: exactly what it was ────────────────────────────────────────

def test_in_one_process_it_is_the_same_lookup_and_does_not_yield(store, monkeypatch):
    monkeypatch.setattr(host, "_remote", None)
    clip_files.discard("gone")
    for clip_id, want in (("gone", True), ("kept", False), ("", False)):
        coro = host.clip_dropped(clip_id)
        with pytest.raises(StopIteration) as done:      # finished on its first step:
            coro.send(None)                             # the loop was never given a turn
        assert done.value.value is want, clip_id


def test_the_cut_asks_through_the_seam_both_times():
    src = pathlib.Path("src/ingestion/stream_worker.py").read_text()
    cut = src[src.index("async def _cut_local_file"):src.index("async def _stop_capture")]
    assert cut.count("if await host.clip_dropped(clip_id):") == 2, "before the cut and after it"
    assert "clip_files.discarded(" not in src, "the cut reads its own process's registry again"
    # Before the trim, which would otherwise evict a video somebody can
    # download to make room for one nobody can.
    assert cut.index("host.clip_dropped") < cut.index("host.trim_clip_store")


# ── in a worker: the web process answers ────────────────────────────────────

def test_a_worker_asks_the_web_process_and_not_its_own_copy(store, monkeypatch):
    asked = []

    class Remote:
        async def clip_dropped(self, clip_id):
            asked.append(clip_id)
            return clip_id == "dropped-there"
    monkeypatch.setattr(host, "_remote", Remote())
    clip_files.discard("only-marked-here")              # this process's copy says dropped...
    assert asyncio.run(host.clip_dropped("only-marked-here")) is False     # ...and is not what answers
    assert asyncio.run(host.clip_dropped("dropped-there")) is True
    assert asked == ["only-marked-here", "dropped-there"]


def test_nobody_answering_means_cut_the_file():
    """A file nobody wanted is cleaned up afterwards. A file somebody wanted
    and did not get is lost for good. So no answer is "not dropped"."""
    from src.ingestion.bridge import LinkClosed, RemoteError
    from src.ingestion.remote import Remote

    class DeadLink:
        def __init__(self, exc):
            self.exc = exc

        async def call(self, method, *args, timeout=None):
            raise self.exc
    for exc in (LinkClosed("gone"), TimeoutError("slow"), RemoteError("boom")):
        assert asyncio.run(Remote(DeadLink(exc)).clip_dropped("c1")) is False

    class Link:
        async def call(self, method, *args, timeout=None):
            return method == "clip_dropped" and args == ("c1",)
    assert asyncio.run(Remote(Link()).clip_dropped("c1")) is True
    assert asyncio.run(Remote(Link()).clip_dropped("c2")) is False


def test_the_web_side_answers_from_the_processors_own_marks(store, monkeypatch):
    ing = ingest_mod.Ingest(1, module="nothing-is-started")
    assert ing.pool._handlers["clip_dropped"] == ing.h_dropped, "no worker's question would be answered"
    assert ing.h_dropped(0, "c1") is False
    clip_files.discard("c1")
    assert ing.h_dropped(0, "c1") is True and ing.h_dropped(0, "c2") is False
    assert ing.h_dropped(0, None) is False and ing.h_dropped(0, "") is False


def test_a_file_reported_for_a_dropped_moment_is_removed_and_goes_no_further(store, monkeypatch):
    """The last place to catch it: both of the worker's questions went
    unanswered, the file was cut, and here it is being reported."""
    told = []

    async def file_ready(clip_id):
        told.append(clip_id)
    monkeypatch.setattr(api, "on_clip_file_ready", file_ready)
    ing = ingest_mod.Ingest(1, module="nothing-is-started")

    clip_files.discard("dropped")
    (store / "dropped.mp4").write_bytes(b"x" * 2048)    # cut by the worker after the mark
    (store / "kept.mp4").write_bytes(b"x" * 2048)
    assert asyncio.run(ing.h_file_ready(0, "dropped")) is True
    assert not (store / "dropped.mp4").exists() and told == []
    assert asyncio.run(ing.h_file_ready(0, "kept")) is True
    assert (store / "kept.mp4").exists() and told == ["kept"]


def test_a_file_another_process_wrote_is_seen_by_the_next_page_load(store, monkeypatch):
    """/clips reads a cached listing of the folder. The worker dropping ITS
    copy of that cache does nothing for the web process."""
    async def file_ready(clip_id):
        return None
    monkeypatch.setattr(api, "on_clip_file_ready", file_ready)
    ing = ingest_mod.Ingest(1, module="nothing-is-started")
    forgot = []
    monkeypatch.setattr(clip_files, "_forget_scan", lambda: forgot.append(True))
    asyncio.run(ing.h_file_ready(0, "fresh"))
    assert forgot == [True]


# ── a real worker process running the real cut ──────────────────────────────

FAKE_WORKER = '''
import asyncio, sys, time, types
sys.path.insert(0, %(repo)r)
from src.ingestion import host, worker_main, workers
from src.ingestion.stream_worker import StreamWorker          # the REAL cut
from src.clips import files as clip_files


class Recorder:
    def __init__(self, channel, user_id):
        self.channel, self.user_id = channel, user_id

    async def cut(self, start, end, out):
        await host.broadcast({"event": "test_cut_started", "channel": self.channel}, user_id=self.user_id)
        if self.channel.startswith("during"):
            await asyncio.sleep(1.2)                 # the processor drops it while this runs
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"v" * 4096)
        return True


class Scripted:
    def __init__(self, config, platform, queue, shared_buffers, on_score):
        self.c, self._stop = config, asyncio.Event()

    async def start(self):
        c = self.c
        me = types.SimpleNamespace(_recorder=Recorder(c.channel, c.user_id), _running=True, _config=c)
        try:
            # post_roll of minus three seconds: the wait before the cut is zero.
            await StreamWorker._cut_local_file(me, "id-" + c.channel, time.time(), 0.0, -3.0)
            await host.broadcast({"event": "test_cut_over", "channel": c.channel,
                                  "there": clip_files.exists("id-" + c.channel),
                                  "own_copy": clip_files.discarded("id-" + c.channel)}, user_id=c.user_id)
            await self._stop.wait()
        except asyncio.CancelledError:
            return

    def stop(self):
        self._stop.set()


workers.StreamWorker = Scripted
sys.exit(worker_main.main())
'''


async def _until(cond, timeout=25.0, every=0.03):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(every)
    return False


def test_a_real_worker_does_not_keep_a_file_for_a_moment_that_was_dropped(tmp_path, store, monkeypatch):
    (tmp_path / "fakedrop.py").write_text(FAKE_WORKER % {"repo": REPO})
    monkeypatch.setattr(pool_mod, "_ROOT", tmp_path)
    # The worker process works out its clip folder from the environment it is
    # started with; `store` is the same folder in this process.
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "store"))
    sent, filed = [], []

    async def bcast(event, user_id=None):
        sent.append(event)
        if event.get("event") == "test_cut_started" and event["channel"].startswith("during"):
            clip_files.discard("id-" + event["channel"])       # the clip processor, mid-cut

    async def file_ready(clip_id):
        filed.append(clip_id)

    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {"id": uid} if uid else None)
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "on_clip_file_ready", file_ready)
    for name in ("before", "during", "kept"):
        api._streams[f"u1:{name}"] = {"channel": name, "platform": "twitch", "preset": "default",
                                      "status": "offline", "user_id": "u1", "added_at": 1.0}
    clip_files.discard("id-before")                            # dropped long before the cut

    def over(name):
        return next((e for e in sent if e.get("event") == "test_cut_over" and e["channel"] == name), None)

    async def go():
        ing = ingest_mod.Ingest(1, module="fakedrop")
        await ing.start()
        try:
            assert await _until(lambda: all(over(n) for n in ("before", "during", "kept"))), sent
        finally:
            await ing.stop()
    asyncio.run(go())

    started = [e["channel"] for e in sent if e.get("event") == "test_cut_started"]
    # Dropped before: the recorder was never asked, so nothing was written
    # and nothing was trimmed to make room for it.
    assert "before" not in started and over("before")["there"] is False
    # Dropped while the cut ran: written, then removed by the worker itself.
    assert "during" in started and over("during")["there"] is False
    # Kept: there, announced to the tab, and handed to the web process.
    assert "kept" in started and over("kept")["there"] is True
    assert (store / "id-kept.mp4").is_file()
    assert not (store / "id-before.mp4").exists() and not (store / "id-during.mp4").exists()
    assert filed == ["id-kept"], "a dropped moment's file was announced, or the kept one was not"
    ready = [e["clip_id"] for e in sent if e.get("event") == "clip_file_ready"]
    assert ready == ["id-kept"]
    # And it was the web process that knew: the worker's own registry was
    # empty the whole time.
    assert all(over(n)["own_copy"] is False for n in ("before", "during", "kept"))
