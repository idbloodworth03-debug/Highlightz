"""Where the app's ONE loop spends its time, timed from inside it.

WHY. Measured on production with 33 channels live (2026-11-18,
scripts/stream_cost.py): the box was 21% busy across its four cores and 70% of
its memory was free, but the one thread that runs chat scoring, every webhook,
every dashboard request and every socket was at 44% of the single core it can
use. That thread, not the server, is what stops the site at about 52 live
channels, and a bigger server does not move it. The owner asked for a second
server; the honest answer was that nothing can be split, or made cheaper, until
somebody knows WHAT that 44% is. This says what.

HOW. Everything the loop does, it does as a "step": one turn of a background
task, one answer to a socket becoming readable, one timer going off. asyncio
runs every step through `Handle._run`. For N seconds that one method is
wrapped to time each step and add it to a running total kept by WHAT the step
belonged to:

  a named task        "worker-<user>:<channel>" is counted as "worker": the
                      kind is kept, the account and the channel are not;
  an unnamed task     by the function it is running ("StreamWorker._read_chat",
                      or uvicorn's "RequestResponseCycle.run_asgi" for every
                      web request together);
  anything else       by the callback's own name (a pipe from ffmpeg becoming
                      readable is "_UnixReadPipeTransport._read_ready").

It is exact, not a sample: every step in the window is counted. Then the
wrapper is taken off again.

THE FIRST VERSION SAMPLED, AND WAS WRONG. It looked at the main thread from a
second thread every 10 ms. A second thread only gets to look when the main one
lets go of the interpreter lock, which a loop doing thousands of sub-millisecond
steps does exactly when it goes back to waiting, so nearly every look saw it
resting: a task burning half a core read as 0.4% busy. Timing the steps has no
such blind spot.

WHAT IT READS. Names of tasks, functions and callbacks. Never an argument, a
local variable or a command line, so the report carries no token, no stream URL
and nobody's data, and is safe to paste.

HOW IT IS ASKED FOR. `scripts/loop_profile.py` drops a request file in the
data folder; `watch_task` (one cheap look a second) sees it, runs the timing
and writes the report beside it. A file and not a signal, on purpose: a signal
sent to a service that is still running the code from BEFORE this existed
would kill it, and a request file that nobody picks up does nothing at all.

WHAT IT COSTS. Two clock reads and a dictionary update per step, about a
microsecond, for the length of the window. Nothing while it is not running
beyond the one look a second.

WHAT IT CANNOT SEE. Inside a step. It says "the workers take 20%", not which
line of the worker. That is the next question, asked of the one activity this
names, and a deterministic profiler would double the loop's load to answer it
for all of them at once.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
import time
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

REQUEST = "loop_profile.request"
REPORT = "loop_profile.txt"
MIN_S, MAX_S, DEFAULT_S = 5.0, 60.0, 20.0
# A single step holding the loop this long is felt by everybody: nothing else
# (no request, no socket, no other channel) runs until it returns.
SLOW_S = 0.050
# Task names that end in something about one account or one channel
# ("worker-<user>:<channel>"). The report keeps the kind and drops the rest.
_PER_ITEM = ("worker-", "post-", "fetch-fallback-", "capture-", "render-", "vod-", "Task-")

_running = threading.Lock()


def task_kind(name: str) -> str:
    """"worker-<user>:<channel>" -> "worker". A fixed name is kept whole."""
    for p in _PER_ITEM:
        if name.startswith(p):
            return p.rstrip("-")
    return name


def label(callback) -> str:
    """What a step belongs to, from the thing asyncio was about to call.
    Names only; any surprise is "callback: ?" rather than an error."""
    try:
        cb = callback
        if cb is None:
            return "callback: (cancelled)"
        while isinstance(cb, functools.partial):
            cb = cb.func
        owner = getattr(cb, "__self__", None)
        if isinstance(owner, asyncio.Task):
            kind = task_kind(owner.get_name())
            if kind != "Task":
                return "task: " + kind
            coro = owner.get_coro()
            return "task: " + (getattr(coro, "__qualname__", None) or type(coro).__name__)
        return "callback: " + (getattr(cb, "__qualname__", None) or type(cb).__name__)
    except Exception:
        return "callback: ?"


def _thread_cpu() -> float:
    """CPU seconds this thread has used. 0.0 where the clock is not offered."""
    try:
        return time.clock_gettime(time.CLOCK_THREAD_CPUTIME_ID)
    except (AttributeError, OSError):
        return 0.0


class Timing:
    """One window of timing. `install` and `finish` run on the loop's thread."""

    def __init__(self):
        self.rows: dict[str, list] = {}        # label -> [seconds, steps, longest]
        self._orig = None
        self._wall0 = self._cpu0 = 0.0

    def install(self) -> None:
        handle = asyncio.events.Handle
        orig = self._orig = handle._run         # noqa: SLF001
        rows, perf = self.rows, time.perf_counter

        def timed(h):
            t0 = perf()
            try:
                return orig(h)
            finally:
                # Never let the bookkeeping raise into the loop.
                try:
                    dt = perf() - t0
                    key = label(h._callback)    # noqa: SLF001
                    row = rows.get(key)
                    if row is None:
                        rows[key] = [dt, 1, dt]
                    else:
                        row[0] += dt
                        row[1] += 1
                        if dt > row[2]:
                            row[2] = dt
                except BaseException:           # noqa: BLE001
                    pass

        self._wall0, self._cpu0 = perf(), _thread_cpu()
        handle._run = timed                      # noqa: SLF001

    def finish(self) -> dict:
        if self._orig is not None:
            asyncio.events.Handle._run = self._orig     # noqa: SLF001
            self._orig = None
        wall = max(1e-9, time.perf_counter() - self._wall0)
        cpu = _thread_cpu() - self._cpu0 if self._cpu0 else 0.0
        return {"seconds": wall, "cpu": cpu,
                "rows": {k: tuple(v) for k, v in self.rows.items()}}


def _dur(s: float) -> str:
    if s >= 1:
        return f"{s:.1f}s"
    if s >= 0.001:
        return f"{s * 1000:.0f}ms" if s >= 0.01 else f"{s * 1000:.1f}ms"
    return f"{s * 1e6:.0f}us"


def render(result: dict, live: int | None = None, top: int = 22) -> str:
    wall = result["seconds"]
    rows = sorted(result["rows"].items(), key=lambda kv: -kv[1][0])
    busy = sum(v[0] for _k, v in rows)
    steps = sum(v[1] for _k, v in rows)
    pct = lambda s: 100.0 * s / wall                              # noqa: E731
    out = ["=" * 78,
           "WHERE THE APP'S ONE LOOP SPENDS ITS TIME",
           "=" * 78,
           f"  watched for            : {wall:.0f}s, every step timed ({steps:,} of them, {steps / wall:,.0f} a second)",
           f"  busy                   : {pct(busy):.1f}% of the one core it can use",
           f"  waiting for work       : {max(0.0, 100.0 - pct(busy)):.1f}%"]
    if result.get("cpu"):
        # The thread's own CPU clock. It also counts the loop's housekeeping
        # between steps, so it can sit a little ABOVE busy; what matters is
        # when it sits well BELOW, because that gap is not work at all.
        cpu = pct(result["cpu"])
        blocked = pct(busy) - cpu
        out.append(f"  CPU the thread used    : {cpu:.1f}%"
                   + (f"   <- {blocked:.1f}% of the busy time was the loop BLOCKED, "
                      "waiting on a disk or a call, not computing" if blocked >= 2.0 else ""))
    if live is not None:
        out.append(f"  channels live          : {live}"
                   + (f"   (~{pct(busy) / live:.2f}% each)" if live else ""))
    out += ["", "=" * 78, "BY WHAT WAS RUNNING", "=" * 78,
            f"  {'share':>6}  {'steps/s':>8}  {'each':>7}  {'longest':>8}   what"]
    if not rows:
        out.append("  nothing was timed. Either the site did nothing at all in the window,")
        out.append("  or its loop is not the standard asyncio one and this cannot see it.")
    for name, (secs, n, longest) in rows[:top]:
        out.append(f"  {pct(secs):5.1f}%  {n / wall:8,.0f}  {_dur(secs / n):>7}  {_dur(longest):>8}   {name}")
    if len(rows) > top:
        rest = rows[top:]
        out.append(f"  {pct(sum(v[0] for _k, v in rest)):5.1f}%  "
                   f"{sum(v[1] for _k, v in rest) / wall:8,.0f}  {'':>7}  {'':>8}   "
                   f"({len(rest)} smaller ones)")
    slow = sorted(((v[2], k) for k, v in rows if v[2] >= SLOW_S), reverse=True)
    out += ["", "=" * 78,
            f"STEPS THAT HELD THE LOOP FOR {SLOW_S * 1000:.0f}ms OR MORE  (nothing else runs meanwhile)",
            "=" * 78]
    out += [f"  {_dur(s):>8}   {k}" for s, k in slow[:10]] or ["  none in this window"]
    out += ["", "share   = of all the time watched, so the rows add up to the busy figure",
            "steps/s = how often it ran; each = its average length; longest = its worst",
            "Names of tasks and functions only. No values, arguments or command lines."]
    return "\n".join(out) + "\n"


def _store() -> Path:
    return Path(settings.local_storage_path)


def _seconds_asked(text: str) -> float:
    try:
        return max(MIN_S, min(MAX_S, float(text.strip() or DEFAULT_S)))
    except ValueError:
        return DEFAULT_S


def _write(text: str) -> None:
    tmp = _store() / (REPORT + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, _store() / REPORT)


def start(loop, seconds: float = DEFAULT_S, live=None) -> bool:
    """Time the loop for `seconds`, then write the report. Call on the loop's
    own thread. False if a window is already open."""
    if not _running.acquire(blocking=False):
        return False
    timing = Timing()

    def done() -> None:
        try:
            result = timing.finish()                 # the wrapper comes off FIRST
            n = None
            try:
                n = int(live()) if live is not None else None
            except Exception:
                n = None
            _write(render(result, n))
            log.info("loop_profile_written", seconds=round(result["seconds"], 1))
        except Exception:
            log.exception("loop_profile_failed")
        finally:
            timing.finish()                          # idempotent: never left on
            _running.release()

    try:
        timing.install()
        loop.call_later(seconds, done)
    except Exception:
        timing.finish()
        _running.release()
        raise
    return True


async def watch_task(live=None, every: float = 1.0) -> None:
    """Start a window when scripts/loop_profile.py asks for one.

    NEVER RAISES: it is one of the tasks main() gathers, and an exception out
    of any of those stops the whole product. One `exists()` a second.
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(every)
            req = _store() / REQUEST
            if not req.exists():
                continue
            try:
                seconds = _seconds_asked(req.read_text())
            except OSError:
                seconds = DEFAULT_S
            try:
                req.unlink()
            except OSError:
                # Cannot clear it, so do not answer it: it would be answered
                # again every second for ever.
                log.warning("loop_profile_request_not_removable", path=str(req))
                await asyncio.sleep(60)
                continue
            if start(loop, seconds, live):
                log.info("loop_profile_started", seconds=seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("loop_profile_watch_failed")
            await asyncio.sleep(30)
