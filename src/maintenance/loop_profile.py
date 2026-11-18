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

WITH THE SPLIT (INGEST_WORKERS, docs/HANDOFF.md "THE SPLIT") there is more
than one loop: the web process's, and one in each ingest worker. A window is
then opened in every one of them at the same moment (`measure` runs in the
worker, asked over the link; `others` below is how the web process is given
that), and the report is the web process's table followed by one per worker.
A worker that is down, or does not answer, gets a line saying so and the rest
of the report is still written.
"""

from __future__ import annotations

import asyncio
import functools
import gc
import os
import threading
import time
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

REQUEST = "loop_profile.request"
REPORT = "loop_profile.txt"
TITLE = "WHERE THE APP'S ONE LOOP SPENDS ITS TIME"
WEB_TITLE = "THE WEB PROCESS: WHERE ITS LOOP SPENDS ITS TIME"
# How long the report waits, after this process's own window has closed, for
# the other processes' tables. Their windows opened with this one, so they are
# normally in already; scripts/loop_profile.py gives up 20 s after the window.
OTHERS_WAIT_S = 12.0
MIN_S, MAX_S, DEFAULT_S = 5.0, 60.0, 20.0
# A single step holding the loop this long is felt by everybody: nothing else
# (no request, no socket, no other channel) runs until it returns.
SLOW_S = 0.050
# Task names that end in something about one account or one channel
# ("worker-<user>:<channel>"). The report keeps the kind and drops the rest.
# The second line is from the first report off production (2026-11-18), which
# listed "recorder-prune-<channel>" twenty-two times and hid the total among
# "223 smaller ones".
_PER_ITEM = ("worker-", "post-", "fetch-fallback-", "capture-", "render-", "vod-", "Task-",
             "audiometer-reader-", "engine-", "profile-", "recorder-prune-", "finish-clip-")

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
        # label -> [seconds, steps, longest, cpu seconds, cpu of the longest]
        self.rows: dict[str, list] = {}
        # (generation, ran on the loop's thread) -> [runs, seconds, longest]
        self.gc: dict[tuple, list] = {}
        self._orig = None
        self._gc_cb = None
        self._wall0 = self._cpu0 = 0.0

    def install(self) -> None:
        handle = asyncio.events.Handle
        orig = self._orig = handle._run         # noqa: SLF001
        rows, perf, cpu_now = self.rows, time.perf_counter, _thread_cpu

        # WALL AND CPU, each step (2026-11-18). The first report could say
        # the loop spent 18 points of its 57 "blocked, not computing" and
        # could not say WHERE. A step whose wall time is far above its CPU
        # time was waiting: on a disk, on a network call made the blocking
        # way, or on another thread that held the interpreter. The two clock
        # reads cost about a tenth of a microsecond a step.
        def timed(h):
            t0, c0 = perf(), cpu_now()
            try:
                return orig(h)
            finally:
                # Never let the bookkeeping raise into the loop.
                try:
                    dt, dc = perf() - t0, cpu_now() - c0
                    key = label(h._callback)    # noqa: SLF001
                    row = rows.get(key)
                    if row is None:
                        rows[key] = [dt, 1, dt, dc, dc]
                    else:
                        row[0] += dt
                        row[1] += 1
                        row[3] += dc
                        if dt > row[2]:
                            row[2], row[4] = dt, dc
                except BaseException:           # noqa: BLE001
                    pass

        # PYTHON TIDYING MEMORY. A collection stops every thread until it is
        # done, and a full one over a big heap can take a large part of a
        # second. It starts wherever memory happened to be asked for, so in
        # the table it shows up as a random step being slow. Timed here so it
        # can be told apart. The callback runs inside the collector: it only
        # reads a clock and adds, and never raises.
        loop_thread = threading.get_ident()
        stats, began = self.gc, {}

        def on_gc(phase, info):
            try:
                me = threading.get_ident()
                if phase == "start":
                    began[me] = perf()
                    return
                t0 = began.pop(me, None)
                if t0 is None:
                    return
                dt = perf() - t0
                key = (int(info.get("generation", 0)), me == loop_thread)
                row = stats.get(key)
                if row is None:
                    stats[key] = [1, dt, dt]
                else:
                    row[0] += 1
                    row[1] += dt
                    if dt > row[2]:
                        row[2] = dt
            except BaseException:               # noqa: BLE001
                pass

        self._gc_cb = on_gc
        gc.callbacks.append(on_gc)
        self._wall0, self._cpu0 = perf(), _thread_cpu()
        handle._run = timed                      # noqa: SLF001

    def finish(self) -> dict:
        if self._orig is not None:
            asyncio.events.Handle._run = self._orig     # noqa: SLF001
            self._orig = None
        if self._gc_cb is not None:
            try:
                gc.callbacks.remove(self._gc_cb)
            except ValueError:
                pass
            self._gc_cb = None
        wall = max(1e-9, time.perf_counter() - self._wall0)
        cpu = _thread_cpu() - self._cpu0 if self._cpu0 else 0.0
        return {"seconds": wall, "cpu": cpu,
                "rows": {k: tuple(v) for k, v in self.rows.items()},
                "gc": {k: tuple(v) for k, v in self.gc.items()}}


def _five(v) -> tuple:
    """A row as (seconds, steps, longest, cpu, cpu of the longest). Rows from
    before the CPU clock was read per step have three fields: the last two
    are then None, which the report prints as blank."""
    v = tuple(v)
    return v + (None,) * (5 - len(v)) if len(v) < 5 else v[:5]


def _add(a, b):
    return None if a is None or b is None else a + b


def grouped(rows: dict) -> dict:
    """Task rows that differ only after their last hyphen become one row.

    task_kind() knows the per-channel names it has been told about. This
    catches the ones it has not: "newthing-xqc" and "newthing-lacy" are summed
    as "newthing-*", so a job that runs once per channel shows its TOTAL, and
    no channel's name is printed for it. A name seen only once is left alone.
    """
    by: dict[str, list[str]] = {}
    for name in rows:
        if name.startswith("task: ") and "-" in name[6:]:
            by.setdefault(name.rsplit("-", 1)[0], []).append(name)
    out = dict(rows)
    for prefix, names in by.items():
        if len(names) < 2 or prefix in rows:
            continue
        for n in names:
            out.pop(n)
        five = [_five(rows[n]) for n in names]
        worst = max(five, key=lambda v: v[2])
        cpu = None if any(v[3] is None for v in five) else sum(v[3] for v in five)
        out[f"{prefix}-*  ({len(names)} of them)"] = (
            sum(v[0] for v in five), sum(v[1] for v in five), worst[2], cpu, worst[4])
    return out


def _dur(s: float) -> str:
    if s >= 1:
        return f"{s:.1f}s"
    if s >= 0.001:
        return f"{s * 1000:.0f}ms" if s >= 0.01 else f"{s * 1000:.1f}ms"
    return f"{s * 1e6:.0f}us"


def to_wire(result: dict) -> dict:
    """A window's result as plain JSON, for an ingest worker to send back over
    the link: rows become lists, and the memory-tidying table loses its tuple
    keys (JSON has none)."""
    return {"seconds": result["seconds"], "cpu": result.get("cpu") or 0.0,
            "rows": {k: list(v) for k, v in result["rows"].items()},
            "gc": [[gen, bool(on_loop), *v] for (gen, on_loop), v in (result.get("gc") or {}).items()]}


def from_wire(data: dict) -> dict:
    """The other direction. Anything malformed is left out rather than raised:
    this is read in the web process, from another process."""
    data = data if isinstance(data, dict) else {}
    rows = {}
    for k, v in (data.get("rows") or {}).items():
        try:
            # (seconds, steps, longest, cpu, cpu of the longest): steps is a
            # count, the rest are seconds, and the last two may be missing.
            row = [None if x is None else float(x) for x in v]
            if len(row) < 3 or row[0] is None or row[1] is None or row[2] is None:
                continue
            row[1] = int(row[1])
            rows[str(k)] = tuple(row)
        except (TypeError, ValueError):
            continue
    tidy = {}
    for g in data.get("gc") or []:
        try:
            tidy[(int(g[0]), bool(g[1]))] = (int(g[2]), float(g[3]), float(g[4]))
        except (TypeError, ValueError, IndexError):
            continue
    try:
        seconds, cpu = float(data.get("seconds") or 0.0), float(data.get("cpu") or 0.0)
    except (TypeError, ValueError):
        seconds, cpu = 0.0, 0.0
    return {"seconds": max(1e-9, seconds), "cpu": cpu, "rows": rows, "gc": tidy}


def render(result: dict, live: int | None = None, top: int = 22, title: str | None = None) -> str:
    wall = result["seconds"]
    rows = sorted(((k, _five(v)) for k, v in grouped(result["rows"]).items()),
                  key=lambda kv: -kv[1][0])
    busy = sum(v[0] for _k, v in rows)
    steps = sum(v[1] for _k, v in rows)
    pct = lambda s: 100.0 * s / wall                              # noqa: E731
    out = ["=" * 78,
           title or TITLE,
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
    def waited(secs, cpu) -> str:
        return "" if cpu is None else f"{pct(max(0.0, secs - cpu)):5.1f}%"

    out += ["", "=" * 78, "BY WHAT WAS RUNNING", "=" * 78,
            f"  {'share':>6}  {'waiting':>7}  {'steps/s':>8}  {'each':>7}  {'longest':>8}   what"]
    if not rows:
        out.append("  nothing was timed. Either the site did nothing at all in the window,")
        out.append("  or its loop is not the standard asyncio one and this cannot see it.")
    for name, (secs, n, longest, cpu, _lc) in rows[:top]:
        out.append(f"  {pct(secs):5.1f}%  {waited(secs, cpu):>7}  {n / wall:8,.0f}  "
                   f"{_dur(secs / n):>7}  {_dur(longest):>8}   {name}")
    if len(rows) > top:
        rest = [v for _k, v in rows[top:]]
        rest_cpu = None if any(v[3] is None for v in rest) else sum(v[3] for v in rest)
        out.append(f"  {pct(sum(v[0] for v in rest)):5.1f}%  "
                   f"{waited(sum(v[0] for v in rest), rest_cpu):>7}  "
                   f"{sum(v[1] for v in rest) / wall:8,.0f}  {'':>7}  {'':>8}   "
                   f"({len(rest)} smaller ones)")

    # Where the waiting was, most first: the rows to go and read. A row is
    # listed when the waiting is worth a look (0.3 points of the window) AND
    # is a real part of that row (15% of its own time): a row that computes
    # flat out still shows a few percent, from the OS taking the core away
    # for a moment.
    stuck = sorted(((v[0] - v[3], k) for k, v in rows
                    if v[3] is not None and pct(v[0] - v[3]) >= 0.3
                    and v[0] - v[3] >= 0.15 * v[0]), reverse=True)
    if stuck:
        out += ["", "=" * 78,
                "WAITING INSIDE A STEP  (the loop was held up, not computing)", "=" * 78]
        out += [f"  {pct(s):5.1f}%   {k}" for s, k in stuck[:10]]
        out += ["  On the rows that read sockets, accept connections or write logs, with",
                "  long steps that computed for almost none of their length: ANOTHER",
                "  THREAD WAS BUSY IN PYTHON. Only one thread runs Python at a time, and",
                "  every system call hands the interpreter over and waits to get it back.",
                "  On a row that makes no system calls of its own (a task doing its own",
                "  work): that row reads a disk or calls something the blocking way."]

    slow = sorted(((v[2], k, v[4]) for k, v in rows if v[2] >= SLOW_S), reverse=True)
    out += ["", "=" * 78,
            f"STEPS THAT HELD THE LOOP FOR {SLOW_S * 1000:.0f}ms OR MORE  (nothing else runs meanwhile)",
            "=" * 78]
    out += [f"  {_dur(s):>8}   {k}" + ("" if lc is None else f"   (computing for {_dur(lc)} of it)")
            for s, k, lc in slow[:10]] or ["  none in this window"]

    tidy = result.get("gc") or {}
    if tidy:
        out += ["", "=" * 78,
                "PYTHON TIDYING MEMORY  (every thread stops while it runs)", "=" * 78]
        names = {0: "quick", 1: "medium", 2: "FULL"}
        for (gen, on_loop), (runs, secs, longest) in sorted(tidy.items(), key=lambda kv: -kv[1][1]):
            out.append(f"  {pct(secs):5.1f}%   {names.get(gen, str(gen)):<6} {runs:>6,} runs, "
                       f"longest {_dur(longest):>6}   "
                       + ("on the loop's thread (counted in the rows above as computing)"
                          if on_loop else "on another thread (the loop waits for it)"))
    out += ["", "share   = of all the time watched, so the rows add up to the busy figure",
            "waiting = the part of that share the loop was not computing",
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


async def measure(seconds: float = DEFAULT_S) -> dict:
    """Time THIS process's loop for `seconds` and return the result in the
    form that crosses the link (`to_wire`). What an ingest worker runs when
    the web process asks it (worker_main, `loop_profile`). Raises if a window
    is already open here."""
    if not _running.acquire(blocking=False):
        raise RuntimeError("a timing window is already open in this process")
    timing = Timing()
    try:
        timing.install()
        await asyncio.sleep(seconds)
        return to_wire(timing.finish())
    finally:
        timing.finish()                              # idempotent: never left on
        _running.release()


# Tasks this module started and must keep alive itself: the loop only holds a
# weak reference to a task, and one nobody else holds can be collected mid-way.
_tasks: set = set()


def _keep(task) -> None:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _sections(others, seconds: float) -> list[str]:
    out = await others(seconds)
    return [str(s) for s in (out or [])]


async def _write_with(text: str, extra, seconds: float) -> None:
    """Write the report once the other processes' tables are in, or without
    them if they are not: what THIS process measured is never thrown away
    because another one was slow. Gives the lock back."""
    try:
        try:
            parts = await asyncio.wait_for(extra, OTHERS_WAIT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                     # noqa: BLE001
            parts = ["=" * 78 + "\nTHE INGEST WORKERS' LOOPS COULD NOT BE READ "
                     f"({type(exc).__name__}). The table above is the web process alone.\n"]
        _write(text + "".join("\n" + p for p in parts))
        log.info("loop_profile_written", seconds=round(seconds, 1), others=len(parts))
    except Exception:
        log.exception("loop_profile_failed")
    finally:
        _running.release()


def start(loop, seconds: float = DEFAULT_S, live=None, others=None) -> bool:
    """Time the loop for `seconds`, then write the report. Call on the loop's
    own thread. False if a window is already open.

    `others`, with the split on: `await others(seconds)` returns the report
    sections of the other processes' loops (dashboard/ingest.py,
    `loop_profiles`), timed over the same window. It is started here, beside
    this process's own window, and the report is written when both are in."""
    if not _running.acquire(blocking=False):
        return False
    timing = Timing()
    extra = None

    def done() -> None:
        handed_on = False
        try:
            result = timing.finish()                 # the wrapper comes off FIRST
            n = None
            try:
                n = int(live()) if live is not None else None
            except Exception:
                n = None
            if extra is None:
                _write(render(result, n))
                log.info("loop_profile_written", seconds=round(result["seconds"], 1))
            else:
                _keep(loop.create_task(
                    _write_with(render(result, n, title=WEB_TITLE), extra, result["seconds"]),
                    name="loop-profile-write"))
                handed_on = True                     # that task gives the lock back
        except Exception:
            log.exception("loop_profile_failed")
        finally:
            timing.finish()                          # idempotent: never left on
            if not handed_on:
                if extra is not None:
                    extra.cancel()
                _running.release()

    try:
        timing.install()
        if others is not None:
            extra = loop.create_task(_sections(others, seconds), name="loop-profile-others")
            _keep(extra)
        loop.call_later(seconds, done)
    except Exception:
        timing.finish()
        if extra is not None:
            extra.cancel()
        _running.release()
        raise
    return True


async def watch_task(live=None, every: float = 1.0, others=None) -> None:
    """Start a window when scripts/loop_profile.py asks for one.

    NEVER RAISES: it is one of the tasks main() gathers, and an exception out
    of any of those stops the whole product. One `exists()` a second.

    `others`: see `start`. None without the split, and then nothing here is
    different from before it existed.
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
            began = start(loop, seconds, live) if others is None \
                else start(loop, seconds, live, others)
            if began:
                log.info("loop_profile_started", seconds=seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("loop_profile_watch_failed")
            await asyncio.sleep(30)
