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
    assert src.count("await host.broadcast(") == 6 and src.count("host.streams()") == 5


def test_host_holds_no_reference_to_the_dashboard():
    """Looked up on every call: tests replace the dashboard's functions, and
    a held reference would call the one that was there at import."""
    src = pathlib.Path("src/ingestion/host.py").read_text()
    body = src.split('"""', 2)[2]
    assert body.count("from src.dashboard import api") == 1
    assert "import api as" not in body and "_api = " not in body
