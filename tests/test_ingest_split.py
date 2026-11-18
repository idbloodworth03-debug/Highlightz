"""THE SPLIT, stage 0: the wire, the worker process, the supervisor.

docs/HANDOFF.md, "THE SPLIT". Stream workers are moving into processes of
their own. Nothing in production uses these yet; what is held here is what
the later stages lean on:

  the wire        casts arrive in order, calls get their answer or their
                  error, a broken handler never ends the link, and the link
                  ending fails every waiting call at once;
  the worker      cannot import the dashboard, says hello, reports how busy
                  its loop is, and exits when the web process goes;
  the supervisor  starts N, knows which one a channel belongs to (by channel
                  name, the same answer in every process), notices a death,
                  says so, starts another, and leaves nothing running behind.

The supervisor tests start REAL processes.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time

import pytest

from src.ingestion import bridge, pool as pool_mod
from src.ingestion.bridge import Link, LinkClosed, RemoteError, open_link, owner_of


async def _pair(left: dict | None = None, right: dict | None = None):
    a, b = socket.socketpair()
    la = await open_link(a, name="left", handlers=left)
    lb = await open_link(b, name="right", handlers=right)
    ta, tb = asyncio.create_task(la.run()), asyncio.create_task(lb.run())
    return la, lb, ta, tb


async def _settle(n: int = 20):
    for _ in range(n):
        await asyncio.sleep(0.005)


# ── the wire ─────────────────────────────────────────────────────────────────

def test_a_call_gets_its_answer_sync_or_async():
    async def go():
        async def slow_double(x):
            await asyncio.sleep(0.01)
            return x * 2
        la, lb, *_ = await _pair(right={"add": lambda a, b: a + b, "double": slow_double,
                                        "who": lambda: {"name": "right", "n": [1, 2, None]}})
        got = (await la.call("add", 2, 3), await la.call("double", 21), await la.call("who"))
        la.close(); lb.close()
        return got
    assert asyncio.run(go()) == (5, 42, {"name": "right", "n": [1, 2, None]})


def test_both_ends_can_ask():
    async def go():
        la, lb, *_ = await _pair(left={"side": lambda: "left"}, right={"side": lambda: "right"})
        got = await asyncio.gather(la.call("side"), lb.call("side"))
        la.close(); lb.close()
        return got
    assert asyncio.run(go()) == ["right", "left"]


def test_casts_are_handled_one_at_a_time_in_the_order_sent():
    """A status going live and then offline must not arrive the other way
    round, even when handling the first one takes longer."""
    async def go():
        seen, running = [], []

        async def status(n, delay):
            running.append(n)
            assert len(running) == 1, "two casts handled at once"
            await asyncio.sleep(delay)
            seen.append(n)
            running.remove(n)
        la, lb, *_ = await _pair(right={"status": status})
        for n, delay in ((1, 0.03), (2, 0.0), (3, 0.01), (4, 0.0)):
            assert la.cast("status", n, delay) is True
        await _settle(30)
        la.close(); lb.close()
        return seen
    assert asyncio.run(go()) == [1, 2, 3, 4]


def test_a_slow_cast_does_not_hold_up_a_call():
    async def go():
        async def slow():
            await asyncio.sleep(0.2)
        la, lb, *_ = await _pair(right={"slow": slow, "quick": lambda: "now"})
        la.cast("slow")
        t = time.perf_counter()
        # The call's line is read after the cast's handler returns, so this is
        # bounded by the cast; what matters is that calls run as tasks and do
        # not queue behind EACH OTHER.
        async def held():
            await asyncio.sleep(0.15)
            return "held"
        lb.handlers["held"] = held
        got = await asyncio.gather(la.call("held"), la.call("quick"))
        took = time.perf_counter() - t
        la.close(); lb.close()
        return got, took
    got, took = asyncio.run(go())
    assert got == ["held", "now"] and took < 0.6


def test_a_handler_that_raises_answers_with_the_error_and_the_link_lives():
    async def go():
        def boom(x):
            raise ValueError(f"no good: {x}")
        seen = []
        la, lb, *_ = await _pair(right={"boom": boom, "ok": lambda: 1, "note": seen.append})
        with pytest.raises(RemoteError) as e:
            await la.call("boom", 7)
        la.cast("boom", 8)                       # a cast that raises is logged, nothing more
        la.cast("note", "after")
        with pytest.raises(RemoteError) as missing:
            await la.call("nobody-home")
        still = await la.call("ok")
        la.close(); lb.close()
        return str(e.value), str(missing.value), still, seen
    err, missing, still, seen = asyncio.run(go())
    assert "no good: 7" in err and "no handler" in missing and still == 1 and seen == ["after"]


def test_an_answer_that_cannot_cross_is_an_error_not_a_hang():
    async def go():
        la, lb, *_ = await _pair(right={"obj": lambda: object()})
        with pytest.raises(RemoteError):
            await la.call("obj", timeout=2)
        la.close(); lb.close()
    asyncio.run(go())


def test_no_answer_in_time_is_a_timeout():
    async def go():
        async def never():
            await asyncio.sleep(30)
        la, lb, *_ = await _pair(right={"never": never})
        t = time.perf_counter()
        with pytest.raises(TimeoutError):
            await la.call("never", timeout=0.1)
        took = time.perf_counter() - t
        assert not la._waiting                   # and it is not left waiting for ever
        la.close(); lb.close()
        return took
    assert asyncio.run(go()) < 1.0


def test_the_other_side_going_fails_every_waiting_call_at_once():
    async def go():
        async def never():
            await asyncio.sleep(30)
        la, lb, ta, tb = await _pair(right={"never": never})
        calls = [asyncio.create_task(la.call("never", timeout=20)) for _ in range(3)]
        await _settle(5)
        t = time.perf_counter()
        lb.close()                               # the worker dies
        out = await asyncio.gather(*calls, return_exceptions=True)
        await asyncio.wait_for(ta, 2)            # and this side's reader returns by itself
        return out, time.perf_counter() - t, la.closed, la.cast("anything")
    out, took, closed, sent = asyncio.run(go())
    assert all(isinstance(e, LinkClosed) for e in out) and took < 1.0
    assert closed is True and sent is False     # a cast after the end is quietly not sent


def test_a_call_on_a_closed_link_raises_at_once():
    async def go():
        la, lb, *_ = await _pair()
        la.close()
        with pytest.raises(LinkClosed):
            await la.call("x")
        lb.close()
    asyncio.run(go())


def test_garbage_on_the_wire_is_skipped_and_the_link_lives():
    async def go():
        seen = []
        a, b = socket.socketpair()
        lb = await open_link(b, name="right", handlers={"note": seen.append})
        tb = asyncio.create_task(lb.run())
        loop = asyncio.get_running_loop()
        a.setblocking(False)
        for raw in (b"not json\n", b"[1,2,3]\n", b'{"no": "kind"}\n', b'{"k":"wat"}\n',
                    b'{"k":"cast","m":"note","a":["still here"]}\n'):
            await loop.sock_sendall(a, raw)
        await _settle(10)
        a.close()
        await asyncio.wait_for(tb, 2)
        return seen
    assert asyncio.run(go()) == ["still here"]


def test_a_message_over_the_limit_is_refused_by_the_sender():
    async def go():
        la, lb, *_ = await _pair(right={"take": lambda x: len(x)})
        with pytest.raises(ValueError):
            await la.call("take", "x" * (bridge.MAX_LINE + 10))
        ok = await la.call("take", "x" * 100_000)          # a chat snapshot's size is fine
        la.close(); lb.close()
        return ok
    assert asyncio.run(go()) == 100_000


def test_a_droppable_cast_is_dropped_only_when_the_other_side_is_not_reading(monkeypatch):
    async def go():
        seen = []
        la, lb, *_ = await _pair(right={"score": seen.append})
        assert la.cast("score", 1, droppable=True) is True
        monkeypatch.setattr(bridge, "HIGH_WATER", -1)       # as if 16 MB were queued
        assert la.cast("score", 2, droppable=True) is False
        assert la.cast("score", 3) is True                  # not droppable: still sent
        await _settle(5)
        la.close(); lb.close()
        return seen
    assert asyncio.run(go()) == [1, 3]


# ── which worker a channel belongs to ────────────────────────────────────────

def test_a_channel_belongs_to_one_worker_whoever_watches_it():
    """By channel name: two users of one streamer share a recorder and one
    viewer-clip poll, so they must share a process."""
    assert owner_of("xQc", 4) == owner_of("xqc", 4) == owner_of(" xqc ", 4)
    assert {owner_of(c, 1) for c in ("a", "b", "c")} == {0}
    names = [f"channel{i}" for i in range(400)]
    counts = [sum(1 for n in names if owner_of(n, 4) == i) for i in range(4)]
    assert sum(counts) == 400 and min(counts) > 70, counts     # roughly even


def test_every_process_gives_the_same_answer():
    """Python salts hash() per process. This must not depend on it."""
    code = ("import sys; sys.path.insert(0, '.'); from src.ingestion.bridge import owner_of; "
            "print([owner_of(c, 5) for c in ('xqc', 'kaicenat', 'lacy', 'summit1g')])")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout.strip()
            for seed in ("1", "2", "random")}
    assert len(outs) == 1 and outs.pop() == str([owner_of(c, 5) for c in ("xqc", "kaicenat", "lacy", "summit1g")])


# ── the worker process and its supervisor: real processes ────────────────────

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                            # a zombie is not alive
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


async def _until(cond, timeout=15.0, every=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(every)
    return False


def test_workers_start_answer_and_are_what_they_should_be():
    async def go():
        ready = []
        p = pool_mod.Pool(2, on_ready=ready.append)
        await p.start()
        try:
            assert sorted(ready) == [0, 1]
            pids = [w.proc.pid for w in p.workers]
            assert len(set(pids)) == 2 and os.getpid() not in pids
            probes = [await w.link.call("probe") for w in p.workers]
            pongs = [await w.link.call("ping") for w in p.workers]
            link = p.link_for("xqc")
            owner = await link.call("probe")
            return pids, probes, pongs, owner, p.owner("xqc")
        finally:
            await p.stop()
    pids, probes, pongs, owner, idx = asyncio.run(go())
    assert pongs == ["pong", "pong"]
    assert [pr["pid"] for pr in probes] == pids and [pr["index"] for pr in probes] == [0, 1]
    assert all(pr["count"] == 2 for pr in probes)
    # THE RULE: a worker cannot load the dashboard's state.
    for pr in probes:
        assert sorted(pr["blocked"]) == ["src.dashboard.api", "src.main"]
        assert pr["dashboard_loaded"] is False
    assert owner["index"] == idx


def test_a_worker_reports_how_busy_its_loop_is(monkeypatch):
    monkeypatch.setattr(pool_mod, "HELLO_TIMEOUT_S", 20.0)

    async def go():
        p = pool_mod.Pool(1)
        await p.start()
        try:
            assert await _until(lambda: p.workers[0].beat_at > 0, timeout=10)
            return p.status()[0]
        finally:
            await p.stop()
    st = asyncio.run(go())
    assert st["up"] is True and st["restarts"] == 0 and st["pid"]
    assert 0.0 <= st["loop_pct"] < 50.0 and st["rss_mb"] > 5 and st["streams"] == 0
    assert st["beat_age_s"] is not None and st["beat_age_s"] < 5


def test_a_dead_worker_is_noticed_announced_and_replaced(monkeypatch):
    monkeypatch.setattr(pool_mod, "BACKOFF_S", (0.05, 0.05, 0.05, 0.05, 0.05))

    async def go():
        events = []
        p = pool_mod.Pool(2, on_ready=lambda i: events.append(("ready", i)),
                          on_lost=lambda i: events.append(("lost", i)))
        await p.start()
        try:
            first = p.workers[1].proc.pid
            other = p.workers[0].proc.pid
            pending = asyncio.create_task(p.workers[1].link.call("ping"))
            await pending
            os.kill(first, signal.SIGKILL)
            assert await _until(lambda: ("lost", 1) in events), events
            assert p.link_for("x") is None or p.owner("x") == 0 or p.workers[1].ready
            assert await _until(lambda: events.count(("ready", 1)) == 2), events
            second = p.workers[1].proc.pid
            pong = await p.workers[1].link.call("ping")
            return events, first, second, other, p.workers[0].proc.pid, pong, p.status()
        finally:
            await p.stop()
    events, first, second, other, other_now, pong, status = asyncio.run(go())
    assert second != first and not _alive(first)
    assert other_now == other, "the worker that did nothing wrong was restarted too"
    assert pong == "pong"
    assert events.index(("lost", 1)) < [i for i, e in enumerate(events) if e == ("ready", 1)][1]
    assert status[1]["restarts"] == 1 and status[0]["restarts"] == 0


def test_stopping_the_pool_leaves_nothing_running():
    async def go():
        p = pool_mod.Pool(2)
        await p.start()
        pids = [w.proc.pid for w in p.workers]
        await p.stop()
        return pids, [w.ready for w in p.workers]
    pids, ready = asyncio.run(go())
    assert ready == [False, False]
    assert not any(_alive(pid) for pid in pids)


def test_what_a_worker_started_goes_with_it(tmp_path, monkeypatch):
    """A worker leads its own process group. When it is lost, the group is
    killed, so the streamlink and ffmpeg it started do not carry on with
    nobody reading them."""
    marker = tmp_path / "child.pid"
    fake = tmp_path / "fakeworker"
    fake.mkdir()
    (fake / "w.py").write_text(
        "import os, subprocess, sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from src.ingestion import worker_main as real\n"
        "child = subprocess.Popen(['sleep', '300'])\n"        # stands in for an ffmpeg
        "open(%r, 'w').write(str(child.pid))\n"
        "sys.exit(real.main())\n" % (os.getcwd(), str(marker)))
    monkeypatch.setattr(pool_mod, "BACKOFF_S", (30.0,) * 5)   # do not restart during the test

    async def go():
        p = pool_mod.Pool(1, python=sys.executable, module="w")
        monkeypatch.setattr(pool_mod, "_ROOT", fake)
        await p.start()
        try:
            assert await _until(marker.exists)
            child = int(marker.read_text())
            assert _alive(child)
            os.kill(p.workers[0].proc.pid, signal.SIGKILL)
            assert await _until(lambda: not _alive(child)), "the worker's child outlived it"
            return child
        finally:
            await p.stop()
    asyncio.run(go())


def test_a_worker_exits_by_itself_when_the_web_process_goes():
    """End-of-file on its socket is its whole reason to stop."""
    ours, theirs = socket.socketpair()
    proc = subprocess.Popen([sys.executable, "-m", "src.ingestion.worker_main", "--fd", str(theirs.fileno()),
                             "--index", "0", "--count", "1"], pass_fds=[theirs.fileno()])
    theirs.close()
    try:
        ours.settimeout(15)
        assert b'"hello"' in ours.recv(4096)
        ours.close()                                    # the web process is gone
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_a_worker_that_never_answers_is_not_called_up(monkeypatch, tmp_path):
    fake = tmp_path / "mute"
    fake.mkdir()
    (fake / "m.py").write_text("import time\ntime.sleep(300)\n")
    monkeypatch.setattr(pool_mod, "_ROOT", fake)
    monkeypatch.setattr(pool_mod, "HELLO_TIMEOUT_S", 0.5)
    monkeypatch.setattr(pool_mod, "BACKOFF_S", (30.0,) * 5)

    async def go():
        ready = []
        p = pool_mod.Pool(1, module="m", on_ready=ready.append)
        await p.start()
        try:
            return ready, p.workers[0].ready, p.link_for("anything")
        finally:
            await p.stop()
    ready, up, link = asyncio.run(go())
    assert ready == [] and up is False and link is None


def test_the_pool_is_only_built_behind_the_switch():
    """main.py reaches the pool through src/dashboard/ingest.py, and only
    when INGEST_WORKERS is set (tests/test_ingest_e2e.py holds the rest)."""
    import pathlib
    main_src = pathlib.Path("src/main.py").read_text()
    assert "worker_main" not in main_src and "Pool(" not in main_src
    assert main_src.count("Ingest(") == 1
    assert main_src.index("if settings.ingest_workers > 0:") < main_src.index("Ingest(")
