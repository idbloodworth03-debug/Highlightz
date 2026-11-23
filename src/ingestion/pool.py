"""The web process's side of the split: start the ingest workers, keep them
running, and know which one a channel belongs to.

See docs/HANDOFF.md, "THE SPLIT", and src/ingestion/bridge.py for the wire.

    pool = Pool(4, handlers={"broadcast": ...}, on_ready=..., on_lost=...)
    await pool.start()
    link = pool.link_for("xqc")          # the worker that owns this channel
    await link.call("ping")

WHAT IT PROMISES THE WEB PROCESS:
  * `on_ready(i)` is called every time worker i is up and has said hello: at
    the start, and again after every restart. That is when the web process
    sends it its channels.
  * `on_lost(i)` is called once each time worker i is gone, BEFORE it is
    started again. Everything that worker held is gone with it (its live
    slots, its pipelines), and the web process has to say so.
  * a worker that dies is started again, quickly at first and then at most
    every ten seconds, for as long as the pool is running.
  * nothing a worker started is left behind: each worker leads its own
    process group, and the whole group is killed when the worker is lost or
    the pool stops, so its streamlink and ffmpeg go with it.

Handlers are called as fn(index, *args): the web process usually needs to
know which worker is speaking.
"""

from __future__ import annotations

import asyncio
import functools
import math
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

import structlog

from src.ingestion.bridge import Link, open_link, owner_of

log = structlog.get_logger(__name__)

HELLO_TIMEOUT_S = 20.0
BACKOFF_S = (0.5, 1.0, 2.0, 5.0, 10.0)
STEADY_S = 60.0                 # up this long = the next failure starts the backoff over
STOP_GRACE_S = 5.0
# A worker killed by SIGTERM while the site has not (yet) said it is stopping:
# how long to wait for it to say so before calling the worker lost. See
# site_stopping().
SITE_STOP_WAIT_S = 2.0
LOOP_SMOOTH_S = 15.0            # the same averaging as src/ingestion/headroom.py

_ROOT = Path(__file__).resolve().parents[2]


class Worker:
    def __init__(self, index: int) -> None:
        self.index = index
        self.proc: asyncio.subprocess.Process | None = None
        self.link: Link | None = None
        self.started_at = 0.0
        self.restarts = 0
        self.failures = 0           # in a row, for the backoff
        self.beat: dict = {}
        self.beat_at = 0.0
        self.ready = False
        # The worker's loop, averaged over about 15 seconds: what the live
        # limit reads for a channel that hashes here (headroom.allow).
        self.loop_avg = 0.0
        self.lag_avg = 0.0


class Pool:
    def __init__(self, count: int, *, handlers: dict[str, Callable] | None = None,
                 on_ready: Callable[[int], Awaitable[None] | None] | None = None,
                 on_lost: Callable[[int], Awaitable[None] | None] | None = None,
                 python: str | None = None, module: str = "src.ingestion.worker_main") -> None:
        if count < 1:
            raise ValueError("a pool needs at least one worker")
        self.count = int(count)
        self._handlers = dict(handlers or {})
        self._on_ready, self._on_lost = on_ready, on_lost
        self._python, self._module = python or sys.executable, module
        self.workers = [Worker(i) for i in range(self.count)]
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._site_stopping = False

    # ── which worker ─────────────────────────────────────────────────────────

    def owner(self, channel: str) -> int:
        return owner_of(channel, self.count)

    def link_for(self, channel: str) -> Link | None:
        """The link to the worker that owns `channel`, or None while that
        worker is down. Callers treat None as "not now": the channel is sent
        again by on_ready when the worker is back."""
        w = self.workers[self.owner(channel)]
        return w.link if w.ready and w.link is not None and not w.link.closed else None

    def status(self) -> list[dict]:
        now = time.monotonic()
        return [{"index": w.index, "pid": w.proc.pid if w.proc else None, "up": w.ready,
                 "up_s": round(now - w.started_at) if w.ready else 0, "restarts": w.restarts,
                 "beat_age_s": round(now - w.beat_at, 1) if w.beat_at else None, **w.beat,
                 "loop_avg": round(w.loop_avg, 1)}
                for w in self.workers]

    # ── running them ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start every worker and return once each is up (or has failed its
        first start and is being retried in the background)."""
        first = [asyncio.get_running_loop().create_future() for _ in self.workers]
        for w, fut in zip(self.workers, first):
            self._tasks.append(asyncio.create_task(self._keep(w, fut), name=f"ingest-pool-{w.index}"))
        await asyncio.gather(*first)

    async def _keep(self, w: Worker, first: asyncio.Future) -> None:
        """One worker's whole life: start, serve until it is gone, clean up,
        wait, start again. Never raises; ends when the pool stops."""
        while not self._stopping:
            was_up = False
            serve: asyncio.Task | None = None
            try:
                hello = await self._launch(w)
                # ONE reader for the link's whole life. "Started" has to mean
                # "answering", not "forked", so wait for its hello while that
                # reader runs; if the reader ends first, it died on the way up.
                serve = asyncio.create_task(w.link.run(), name=f"ingest-link-{w.index}")
                await asyncio.wait({hello, serve}, timeout=HELLO_TIMEOUT_S,
                                   return_when=asyncio.FIRST_COMPLETED)
                was_up = hello.done() and not serve.done()
                if not was_up:
                    log.error("ingest_worker_silent", index=w.index,
                              pid=w.proc.pid if w.proc else None,
                              exited=w.proc.returncode if w.proc else None)
            except asyncio.CancelledError:
                if serve is not None:
                    serve.cancel()
                raise
            except Exception as exc:
                log.error("ingest_worker_start_failed", index=w.index, error=repr(exc)[:300])
            if was_up:
                w.started_at, w.ready = time.monotonic(), True
                log.info("ingest_worker_up", index=w.index, pid=w.proc.pid, restarts=w.restarts)
            if not first.done():
                first.set_result(was_up)
            if was_up:
                await self._call_back(self._on_ready, w.index, "on_ready")
                try:
                    await serve                         # until end-of-file
                except asyncio.CancelledError:
                    if self._stopping or not serve.cancelled():
                        raise
                if self._stopping:
                    break
                up_for = time.monotonic() - w.started_at
                if await self._went_with_site(w):
                    # Stopped WITH the site (a deploy, a restart), not lost:
                    # nothing to announce, nothing to start again.
                    w.ready = False
                    log.info("ingest_worker_stopped_with_site", index=w.index,
                             pid=w.proc.pid if w.proc else None, up_s=round(up_for))
                    await self._reap(w)
                    break
                log.error("ingest_worker_lost", index=w.index, pid=w.proc.pid if w.proc else None,
                          up_s=round(up_for))
                w.failures = 0 if up_for >= STEADY_S else w.failures + 1
            else:
                if serve is not None:
                    serve.cancel()
                w.failures += 1
            w.ready = False
            await self._reap(w)
            if was_up:
                await self._call_back(self._on_lost, w.index, "on_lost")
            if self._stopping:
                break
            w.restarts += 1
            await asyncio.sleep(BACKOFF_S[min(w.failures, len(BACKOFF_S)) - 1] if w.failures else 0.0)

    async def _launch(self, w: Worker) -> asyncio.Future:
        """Start the process and open the link to it. Returns a future that
        completes when the worker says hello."""
        ours, theirs = socket.socketpair()
        hello: asyncio.Future = asyncio.get_running_loop().create_future()
        w.beat, w.beat_at = {}, 0.0
        w.loop_avg = w.lag_avg = 0.0
        try:
            w.proc = await asyncio.create_subprocess_exec(
                self._python, "-m", self._module, "--fd", str(theirs.fileno()),
                "--index", str(w.index), "--count", str(self.count),
                pass_fds=[theirs.fileno()], cwd=str(_ROOT), stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True)             # its own process group: see _reap
        except BaseException:
            ours.close()
            raise
        finally:
            theirs.close()

        def said_hello(_index, info=None):
            if not hello.done():
                hello.set_result(info or {})

        def beat(_index, data=None):
            data = dict(data or {})
            now = time.monotonic()
            try:
                loop, lag = float(data.get("loop_pct") or 0.0), float(data.get("lag_ms") or 0.0) / 1000.0
                if w.beat_at:
                    a = 1.0 - math.exp(-max(0.0, now - w.beat_at) / LOOP_SMOOTH_S)
                    w.loop_avg += a * (loop - w.loop_avg)
                    w.lag_avg += a * (lag - w.lag_avg)
                else:
                    w.loop_avg, w.lag_avg = loop, lag
            except (TypeError, ValueError):
                pass
            w.beat, w.beat_at = data, now

        handlers = {name: functools.partial(fn, w.index) for name, fn in self._handlers.items()}
        handlers.update(hello=functools.partial(said_hello, w.index),
                        heartbeat=functools.partial(beat, w.index))
        w.link = await open_link(ours, name=f"ingest-{w.index}", handlers=handlers)
        return hello

    async def _reap(self, w: Worker) -> None:
        """Make sure the worker AND everything it started are gone."""
        if w.link is not None:
            w.link.close()
        proc = w.proc
        if proc is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)            # the group it leads: its streamlink, its ffmpeg
            except (ProcessLookupError, PermissionError):
                break
            try:
                await asyncio.wait_for(proc.wait(), STOP_GRACE_S)
                break
            except asyncio.TimeoutError:
                continue
        try:
            os.killpg(proc.pid, signal.SIGKILL)     # stragglers in the group after the leader went
        except (ProcessLookupError, PermissionError):
            pass

    async def _call_back(self, fn, index: int, what: str) -> None:
        if fn is None:
            return
        try:
            out = fn(index)
            if asyncio.iscoroutine(out):
                await out
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("ingest_pool_callback_failed", callback=what, index=index, error=repr(exc)[:300])

    # ── the site stopping ────────────────────────────────────────────────────
    # THE FALSE DEATH ON EVERY DEPLOY (found in the first production trial,
    # 2026-11-23: "workers that died: 1" right after the restart that started
    # it). systemd stops the service by sending SIGTERM to EVERY process in it
    # at once, workers included, and a worker exits on it at once. The web
    # process is still shutting down at that moment, and stop() below, the
    # only thing that told the pool, is in main()'s finally block, which
    # never runs on SIGTERM: uvicorn re-raises the signal once its own
    # shutdown is done, and the default action ends the process there. So
    # the pool read the worker's end as a crash: an error (an alert email),
    # its channels shown as reconnecting, a new worker started into a dying
    # service, and split_check / `split_trial.py all` counting a death for an
    # hour after every deploy.
    #
    # main.py now calls site_stopping() from uvicorn's own signal handler,
    # before anything else, so the flag is up before the worker's end is read.
    # And a worker that ends by SIGTERM without the flag is given
    # SITE_STOP_WAIT_S for it to arrive (the two signals land together and
    # either can be seen first). A worker killed any other way, or by a
    # SIGTERM with the site staying up (somebody's `kill`), is lost exactly
    # as before.

    def site_stopping(self) -> None:
        """The site has been told to stop. Only sets a flag, so it is safe to
        call from a signal handler."""
        self._site_stopping = True

    async def _went_with_site(self, w: Worker) -> bool:
        if self._site_stopping or self._stopping:
            return True
        rc = None
        if w.proc is not None:
            try:
                rc = await asyncio.wait_for(w.proc.wait(), 1.0)
            except asyncio.TimeoutError:
                rc = w.proc.returncode
        if rc != -signal.SIGTERM:
            return False
        end = time.monotonic() + SITE_STOP_WAIT_S
        while time.monotonic() < end:
            if self._site_stopping or self._stopping:
                return True
            await asyncio.sleep(0.05)
        return self._site_stopping or self._stopping

    async def stop(self) -> None:
        self._stopping = True
        for w in self.workers:
            if w.link is not None:
                w.link.close()                      # end-of-file: the worker exits by itself
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.gather(*(self._reap(w) for w in self.workers), return_exceptions=True)
        for w in self.workers:
            w.ready = False
