"""THE SPLIT, stage 1: one seam between a stream worker and the web process.

docs/HANDOFF.md, "THE SPLIT". Every place a stream worker used to reach into
the dashboard now goes through src/ingestion/host.py. This stage ships with
the switch off, so the whole point of these tests is that IN ONE PROCESS
NOTHING CHANGED:

  * each function calls the dashboard function it replaced, with the same
    arguments, and hands back what it returned;
  * the ones that were plain calls (a live slot, who watches, Highlight room)
    still finish without giving the loop a turn, so nothing can now happen
    "in between" that could not before;
  * `streams()` is the dashboard's own dict, not a copy.

And for the stages after it: with a remote set, nothing touches the
dashboard; and no worker-side module imports the dashboard at all.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from src.dashboard import api
from src.ingestion import host


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.setattr(host, "_remote", None)


def _steps(coro):
    """Run a coroutine by hand: (how many times it gave the loop a turn, result)."""
    n = 0
    try:
        while True:
            coro.send(None)
            n += 1
            if n > 50:
                raise AssertionError("never finished")
    except StopIteration as done:
        return n, done.value


def test_streams_is_the_dashboards_own_dict(monkeypatch):
    mine = {"u:c": {"status": "starting"}}
    monkeypatch.setattr(api, "_streams", mine)
    assert host.streams() is mine
    host.streams()["u:c"]["status"] = "live"          # what the worker does
    assert api._streams["u:c"]["status"] == "live"


@pytest.mark.parametrize("name, args, answer", [
    ("acquire_live_slot", ("u:chan",), True),
    ("acquire_live_slot", ("u:chan",), False),
    ("watchers_of", ("chan",), ["u1", "u2"]),
    ("suggestion_room", ("u1",), (3, 5)),
])
def test_what_was_a_plain_call_is_still_one(monkeypatch, name, args, answer):
    seen = []

    def fake(*a):
        seen.append(a)
        return answer
    monkeypatch.setattr(api, name, fake)
    turns, got = _steps(getattr(host, name)(*args))
    assert got == answer and seen == [args]
    assert turns == 0, f"host.{name} gave the loop a turn; the direct call never did"


def test_release_is_a_plain_call_and_passes_through(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "release_live_slot", seen.append)
    assert host.release_live_slot("u:chan") is None
    assert seen == ["u:chan"]


def test_the_awaited_ones_await_the_dashboards(monkeypatch):
    seen = []

    async def bcast(event, user_id=None):
        seen.append(("broadcast", event, user_id))

    async def ready(meta):
        seen.append(("ready", meta))
        return {"queued": True}

    async def file_ready(clip_id):
        seen.append(("file", clip_id))
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "notify_clip_ready", ready)
    monkeypatch.setattr(api, "on_clip_file_ready", file_ready)

    async def go():
        await host.broadcast({"event": "stream_status", "status": "live"}, user_id="u1")
        await host.broadcast({"event": "x"})
        got = await host.notify_clip_ready({"id": "c1"})
        await host.on_clip_file_ready("c1")
        return got
    assert asyncio.run(go()) == {"queued": True}
    assert seen == [("broadcast", {"event": "stream_status", "status": "live"}, "u1"),
                    ("broadcast", {"event": "x"}, None), ("ready", {"id": "c1"}), ("file", "c1")]


def test_an_error_from_the_dashboard_reaches_the_worker_unchanged(monkeypatch):
    def refuse(key):
        raise RuntimeError("store unreadable")
    monkeypatch.setattr(api, "acquire_live_slot", refuse)
    with pytest.raises(RuntimeError, match="store unreadable"):
        asyncio.run(host.acquire_live_slot("u:c"))


def test_with_a_remote_nothing_touches_the_dashboard(monkeypatch):
    calls = []

    class Remote:
        def streams(self):
            return {"mirror": True}

        async def broadcast(self, event, user_id=None):
            calls.append(("broadcast", event, user_id))

        async def acquire_live_slot(self, key):
            calls.append(("acquire", key))
            return 1                                    # truthy from the wire: becomes True

        def release_live_slot(self, key):
            calls.append(("release", key))

        async def watchers_of(self, channel):
            return ("u1", "u2")

        async def suggestion_room(self, uid):
            return [2, 5]                               # JSON has no tuples

        async def notify_clip_ready(self, meta):
            calls.append(("ready", meta["id"]))

        async def on_clip_file_ready(self, clip_id):
            calls.append(("file", clip_id))

    def no_dashboard():
        raise AssertionError("the dashboard was reached from behind a remote")
    monkeypatch.setattr(host, "_api", no_dashboard)
    monkeypatch.setattr(host, "_remote", Remote())

    async def go():
        await host.broadcast({"event": "e"}, user_id="u")
        ok = await host.acquire_live_slot("u:c")
        host.release_live_slot("u:c")
        who = await host.watchers_of("c")
        used, room = await host.suggestion_room("u")
        await host.notify_clip_ready({"id": "c1"})
        await host.on_clip_file_ready("c1")
        return ok, who, used, room
    ok, who, used, room = asyncio.run(go())
    assert ok is True and who == ["u1", "u2"] and (used, room) == (2, 5)
    assert host.streams() == {"mirror": True}
    assert calls == [("broadcast", {"event": "e"}, "u"), ("acquire", "u:c"), ("release", "u:c"),
                     ("ready", "c1"), ("file", "c1")]


# ── the rule for everything that will run in a worker process ────────────────

WORKER_SIDE = ("src/ingestion", "src/trigger", "src/chat", "src/output", "src/queue", "src/profiles")
_IMPORTS_DASHBOARD = re.compile(
    r"^\s*(?:from\s+src\.dashboard(?:\.api)?\s+import\b|import\s+src\.dashboard\b)", re.M)


def test_nothing_on_the_worker_side_imports_the_dashboard():
    """A worker process cannot import src.dashboard.api (worker_main blocks
    it: a second copy would rewrite the web process's files). So nothing that
    runs there may try. host.py is the one door, and it only opens in the web
    process."""
    offenders = []
    for folder in WORKER_SIDE:
        for path in sorted(pathlib.Path(folder).rglob("*.py")):
            if path.as_posix() == "src/ingestion/host.py":
                continue
            if _IMPORTS_DASHBOARD.search(path.read_text()):
                offenders.append(path.as_posix())
    assert offenders == [], offenders


def test_the_stream_worker_goes_through_the_seam_for_everything():
    src = pathlib.Path("src/ingestion/stream_worker.py").read_text()
    assert "dashboard_api" not in src and "_api." not in src
    for call in ("host.release_live_slot(self._stream_key)", "await host.acquire_live_slot(_sk)",
                 "await host.watchers_of(self._config.channel)", "await host.suggestion_room(uid)",
                 "await host.on_clip_file_ready(clip_id)", "await host.notify_clip_ready(meta.to_dict())"):
        assert src.count(call) == 1, call
    assert src.count("await host.broadcast(") == 6
    # Its record is read once (the preset) and its status is only ever SET
    # through set_status, which is where "paused stays paused" is held.
    assert src.count("host.streams()") == 1 and src.count("host.set_status(") == 2
    assert '["status"] =' not in src


def test_host_holds_no_reference_to_the_dashboard():
    """Looked up on every call: tests replace the dashboard's functions, and
    a held reference would call the one that was there at import."""
    src = pathlib.Path("src/ingestion/host.py").read_text()
    body = src.split('"""', 2)[2]
    assert body.count("from src.dashboard import api") == 1
    assert "import api as" not in body and "_api = " not in body


# ── stage 2a: starting and stopping workers lives where a worker can reach it ─

def test_main_still_has_the_names_and_they_are_the_same_objects():
    """src/main.py imports them, so everything that calls main.spawn_worker,
    reads main._workers or shares main.SHARED_BUFFERS is using the one copy."""
    from src import main
    from src.ingestion import workers
    for name in ("spawn_worker", "stop_worker", "reap_orphan_capture_task", "_workers",
                 "_worker_instances", "SHARED_BUFFERS", "PLATFORM_MAP"):
        assert getattr(main, name) is getattr(workers, name), name
    src = pathlib.Path("src/main.py").read_text()
    assert "async def spawn_worker(" not in src and "async def stop_worker(" not in src
    assert "_ingest_workers.set_queue(_queue)" in src


def test_a_spawned_worker_gets_the_queue_and_reports_through_the_seam(monkeypatch):
    from src.ingestion import workers
    made, sent, records = [], [], {"u1:nova": {"channel": "nova", "status": "live"}}

    class FakeWorker:
        def __init__(self, config, platform, queue, shared_buffers, on_score):
            made.append(self)
            self.config, self.queue, self.shared, self.on_score = config, queue, shared_buffers, on_score

        async def start(self):
            await self.on_score("nova", 71.5, {"CHAT_VELOCITY": 0.4})
            raise RuntimeError("the session blew up")

        def stop(self):
            pass

    async def bcast(event, user_id=None):
        sent.append((event["event"], user_id, event))
    monkeypatch.setattr(workers, "StreamWorker", FakeWorker)
    monkeypatch.setattr(workers, "_workers", {})
    monkeypatch.setattr(workers, "_worker_instances", {})
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "_streams", records)
    queue = object()
    workers.set_queue(queue)

    async def go():
        await workers.spawn_worker("nova", "kick", "u1", "fps")
        await workers.spawn_worker("nova", "kick", "u1", "fps")     # already running: not a second one
        task = workers._workers["u1:nova"]
        await task
    try:
        asyncio.run(go())
    finally:
        workers.set_queue(None)
    assert len(made) == 1
    w = made[0]
    assert w.queue is queue and w.shared is workers.SHARED_BUFFERS
    assert (w.config.channel, w.config.platform_name, w.config.user_id, w.config.preset) == ("nova", "kick", "u1", "fps")
    assert sent[0][:2] == ("score_update", "u1") and sent[0][2]["score"] == 71.5
    # A worker that ends, however it ends, leaves its channel "offline" and says so.
    assert records["u1:nova"]["status"] == "offline"
    assert sent[-1][:2] == ("stream_updated", "u1") and sent[-1][2]["stream"]["status"] == "offline"


def test_stopping_a_worker_forgets_it_and_its_buffer(monkeypatch):
    from src.ingestion import workers
    stopped = []

    class W:
        def stop(self):
            stopped.append(True)

    async def go():
        async def forever():
            await asyncio.sleep(60)
        monkeypatch.setattr(workers, "_workers", {"u1:nova": asyncio.create_task(forever())})
        monkeypatch.setattr(workers, "_worker_instances", {"u1:nova": W()})
        monkeypatch.setattr(workers, "SHARED_BUFFERS", {"nova": object(), "other": object()})
        await workers.stop_worker("nova", "u1")
        await workers.stop_worker("nova", "u1")                     # twice is harmless
        return dict(workers._workers), dict(workers._worker_instances), list(workers.SHARED_BUFFERS)
    assert asyncio.run(go()) == ({}, {}, ["other"])
    assert stopped == [True]


# ── a paused channel stays paused when its worker stops ──────────────────────
# Found reading this path for the split, and older than it: pausing sets the
# record to "paused" and THEN stops the worker, and the worker's last act was
# to write "offline" over it. Nothing was then marked paused, so the owner's
# next visit restarted nothing.

def test_a_worker_report_never_replaces_paused(monkeypatch):
    recs = {"u:c": {"status": "paused"}, "u:d": {"status": "live"}}
    monkeypatch.setattr(api, "_streams", recs)
    assert host.set_status("u:c", "offline") is False and recs["u:c"]["status"] == "paused"
    assert host.set_status("u:c", "live") is False and recs["u:c"]["status"] == "paused"
    assert host.set_status("u:d", "offline") is True and recs["u:d"]["status"] == "offline"
    assert host.set_status("u:gone", "live") is False and "u:gone" not in recs
    recs["u:c"]["status"] = "starting"                 # they came back: the dashboard's word
    assert host.set_status("u:c", "live") is True


def test_pausing_a_running_channel_leaves_it_paused_and_coming_back_restarts_it(monkeypatch):
    """The real pause, the real worker wrapper, the real resume. Only the
    stream worker itself and Redis are stood in for."""
    from src.ingestion import workers
    started, sent = [], []

    class FakeWorker:
        def __init__(self, config, **kw):
            self.channel = config.channel

        async def start(self):
            started.append(self.channel)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return                                  # as StreamWorker.start does

        def stop(self):
            pass

    async def bcast(event, user_id=None):
        sent.append((event.get("event"), (event.get("stream") or {}).get("status")))

    async def publish_new(channel, platform, preset, uid=""):
        await workers.spawn_worker(channel, platform, uid, preset)

    async def publish_remove(channel, uid=""):
        await workers.stop_worker(channel, uid)

    monkeypatch.setattr(workers, "StreamWorker", FakeWorker)
    monkeypatch.setattr(workers, "_workers", {})
    monkeypatch.setattr(workers, "_worker_instances", {})
    monkeypatch.setattr(api, "_streams", {"u1:nova": {"channel": "nova", "platform": "kick", "preset": "default",
                                                      "status": "live", "user_id": "u1"}})
    monkeypatch.setattr(api, "_paused_users", set())
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "_publish_new_stream", publish_new)
    monkeypatch.setattr(api, "_publish_remove_stream", publish_remove)
    api._live_slots.add("u1:nova")

    async def go():
        await workers.spawn_worker("nova", "kick", "u1")
        await asyncio.sleep(0)
        assert await api._pause_user_streams("u1", hold="queue_full") == 1
        after_pause = dict(api._streams["u1:nova"])
        while_paused = list(sent)
        running = dict(workers._workers)
        assert await api._resume_user_streams("u1") == 1
        await asyncio.sleep(0)
        after_resume = dict(api._streams["u1:nova"])
        await workers.stop_worker("nova", "u1")
        return after_pause, running, after_resume, while_paused
    after_pause, running, after_resume, while_paused = asyncio.run(go())
    assert after_pause["status"] == "paused" and after_pause["hold"] == "queue_full", after_pause
    assert running == {}, "the paused channel's worker was still registered"
    assert "u1:nova" not in api._live_slots
    # Every report a tab got while it was paused said paused, the last one included.
    assert while_paused == [("stream_updated", "paused"), ("stream_updated", "paused")], while_paused
    assert after_resume["status"] == "starting" and "hold" not in after_resume
    assert started == ["nova", "nova"], "coming back did not start the channel again"
