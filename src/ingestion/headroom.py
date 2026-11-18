"""How much room the server really has, measured, so the live limit can follow it.

Owner, 2026-11-18: "I want to maximize live monitored streams as much as
possible without breaking the server."

THE PROBLEM WITH A NUMBER. `MAX_CONCURRENT_STREAMS` was raised three times in a
week (24, 36, 44, 60), each time after somebody ran a script by hand and read
the answer. A fixed number is wrong in both directions: too low on a quiet
night, when the box could carry more, and too high on the night forty thousand
people are in one chat. And what it protects is not one thing. Three things
run out, and which comes first changes (scripts/stream_cost.py):

  the app's one loop   chat scoring, every request and every socket share ONE
                       thread, which can use one core however many the box has;
  the box's CPU        each live channel is four small processes of its own;
  memory               about 170 MB a channel, and there is no swap: running
                       out does not slow the site down, it kills it.

WHAT THIS DOES. Every two seconds it reads all three, the same figures the
script reads, from inside the running app. A channel going live is then let in
by MEASUREMENT: up to `LIVE_AUTO_BASE` channels exactly as before, and beyond
that only while letting one more in would still leave all three under their
lines, up to the hard maximum `MAX_CONCURRENT_STREAMS`. When the server is
full, the next channel waits ("queued", as it always has when the box was
full) and is asked about again every 30 seconds.

IT ONLY EVER SAYS NO. Nothing here can let in more than the hard maximum, and
a channel already live is never put out by it. If the readings stop arriving,
or look wrong, it stops letting channels in past the base: with nothing
measured, the answer is the old fixed number.

WHY IT PREDICTS. A channel costs nothing for the first few seconds (the feed
is still connecting) and the readings are averages, so "is there room right
now" would say yes to twenty channels in the same second after a restart and
find out half a minute later. Every channel let in during the last
`PENDING_S` seconds is counted as if it already cost a full channel's share,
at the measured average cost per channel and never less than a floor.

And only `STEP` NEW channels may be in that unmeasured state at once: past
the base the limit climbs a few channels at a time, each batch measured before
the next. Channels go live over an evening, not in a second, so this is not
felt. A channel that held a slot a moment ago and is reconnecting is not new
(its cost was in the average), so it never holds up the climb.

A RESTART IS NOT NEW LOAD, and neither is everybody reconnecting. Every deploy
restarts the service, and a hiccup at Twitch drops every feed at once; either
way every channel that was live asks for its slot again inside half a minute.
Climbing back up a few at a time would leave channels the box was carrying a
minute ago waiting for minutes, several times a day. So twice a minute the
number of channels the readings say the box is carrying is noted, and that
many are let straight back in for the next few minutes without asking. The
note is also written to a small file (`headroom.json` in the data folder) so a
restart reads it back. It is only ever a number the box was measured
carrying, it goes stale in ten minutes, and it never passes the hard maximum.
What was running comes back up to the WIDER lines (75% of the loop, 85% of
the box): between those and the lines for new channels nothing new gets in,
but a channel whose feed dropped for a moment does not lose its place to a
reading two points over. Past the wider lines the note is scaled down, so
what is let back in is never an overload.

THE LINES are the ones stream_cost.py prints, a little inside them: the loop
65% of its core (the script warns at 70), the box 75% of all cores, and a
tenth of memory (1.5 GB at least) kept back. Studio accounts ("front of the
line") are held to wider ones, which is their reserved room.

`enabled()` is false until LIVE_AUTO_BASE is set, and until then nothing here
is consulted at all: the limit is the fixed number, as before. The readings
are taken either way, for the admin page and the overload email.
"""

from __future__ import annotations

import asyncio
import collections
import json
import math
import os
import time

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

EVERY_S = 2.0
SMOOTH_S = 15.0              # the readings are averages over about this long
STALE_S = 12.0               # older than this is "not measured"
PENDING_S = 45.0             # a channel let in this recently has not shown its cost yet

LOOP_LINE, LOOP_LINE_PRIORITY = 65.0, 75.0       # % of the one core the loop can use
CPU_LINE, CPU_LINE_PRIORITY = 75.0, 85.0         # % of every core
MEM_RESERVE_MB = 1536.0                          # kept back: the OS, captions, a VOD scan
MEM_RESERVE_FRAC = 0.10                          # ...or this much of the box, if that is more
LAG_LINE_S = 0.25                                # the loop waking this late is the loop blocked

# What one more channel is assumed to cost when the measured average says
# less. Measured on production 2026-11-18: 1.3 to 1.5% of the loop's core,
# 1.5% of one core in its own processes, 160 to 170 MB.
PER_LOOP_FLOOR = 1.0
PER_CORE_FLOOR = 1.5
PER_MEM_MB = 170.0

# How many NEW channels may be live with their cost not yet in the readings.
STEP = 6
# A channel that gave its slot back this recently and takes one again is
# reconnecting, not new.
RETURNING_S = 180.0

# What the box was measured carrying well: how often it is noted, for how long
# a note lets that many back in (long enough for every worker to have asked
# again; they ask every 30 seconds), and how old the file may be at a start.
CARRY_FILE = "headroom.json"
CARRY_EVERY_S = 30.0
CARRY_FOR_S = 180.0
CARRY_MAX_AGE_S = 600.0

# The owner is emailed (src/alerts mails every error) when the loop stays over
# this: channels already live are never put out, so a night that gets heavier
# after they were let in is a person's decision, not this module's.
OVERLOAD_LINE = 85.0
OVERLOAD_AFTER_S = 120.0
OVERLOAD_EVERY_S = 1800.0

_state = {"at": 0.0, "loop": 0.0, "cpu": 0.0, "mem_mb": 0.0, "lag": 0.0, "samples": 0,
          "mem_total_mb": 0.0}
_carried: collections.deque = collections.deque(maxlen=64)     # (when, how many)
_admitted: collections.deque = collections.deque(maxlen=1024)  # (when, is it new)
_released: dict[str, float] = {}                               # channel -> when it let go
_last_no = {"why": "", "at": 0.0}
_over = {"since": None, "told": None}       # None, not 0: the clock is seconds since boot


def forget() -> None:
    """Back to "nothing measured, nothing let in". For tests."""
    _state.update(at=0.0, loop=0.0, cpu=0.0, mem_mb=0.0, lag=0.0, samples=0, mem_total_mb=0.0)
    _carried.clear()
    _admitted.clear()
    _released.clear()
    _last_no.update(why="", at=0.0)
    _over.update(since=None, told=None)


def enabled() -> bool:
    return int(getattr(settings, "live_auto_base", 0) or 0) > 0


def base() -> int:
    return max(0, int(getattr(settings, "live_auto_base", 0) or 0))


def floor(now: float | None = None) -> int:
    """Let in without asking the readings: the base, or what the box was
    measured carrying well in the last few minutes (or just before a
    restart), whichever is more."""
    now = time.monotonic() if now is None else now
    return max(base(), _carried_now(now))


def _carried_now(now: float) -> int:
    while _carried and now - _carried[0][0] > CARRY_FOR_S:
        _carried.popleft()
    return max((n for _, n in _carried), default=0)


def _reserve_mb() -> float:
    return max(MEM_RESERVE_MB, MEM_RESERVE_FRAC * _state["mem_total_mb"])


def _cores() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def _thread_cpu() -> float:
    return time.clock_gettime(time.CLOCK_THREAD_CPUTIME_ID)


def _box_cpu() -> tuple[float, float]:
    """(busy, total) jiffies for every core since boot, from /proc/stat."""
    with open("/proc/stat", encoding="ascii") as f:
        parts = f.readline().split()[1:]
    vals = [float(x) for x in parts[:8]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)            # idle + iowait
    return sum(vals) - idle, sum(vals)


def _mem_mb() -> tuple[float, float]:
    """(available, total) in MB, from /proc/meminfo."""
    got: dict[str, float] = {}
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            key = line.split(":", 1)[0]
            if key in ("MemAvailable", "MemTotal"):
                got[key] = float(line.split()[1]) / 1024.0
                if len(got) == 2:
                    break
    if "MemAvailable" not in got:
        raise OSError("MemAvailable is not in /proc/meminfo")
    return got["MemAvailable"], got.get("MemTotal", 0.0)


def _blend(old: float, new: float, dt: float, first: bool) -> float:
    if first:
        return new
    a = 1.0 - math.exp(-dt / SMOOTH_S)
    return old + a * (new - old)


def record(loop_pct: float, cpu_pct: float, mem_mb: float, lag_s: float, dt: float,
           now: float | None = None, mem_total_mb: float = 0.0) -> None:
    """Fold one reading in. Separate from `run` so the arithmetic is testable
    without a machine to read."""
    first = _state["samples"] == 0
    _state["mem_total_mb"] = mem_total_mb
    _state["loop"] = _blend(_state["loop"], max(0.0, loop_pct), dt, first)
    _state["cpu"] = _blend(_state["cpu"], max(0.0, cpu_pct), dt, first)
    _state["lag"] = _blend(_state["lag"], max(0.0, lag_s), dt, first)
    _state["mem_mb"] = mem_mb                    # not smoothed: it is a level, not a rate
    _state["samples"] += 1
    _state["at"] = time.monotonic() if now is None else now


def note_admitted(key: str = "", now: float | None = None) -> None:
    """A channel took a live slot. `key` tells a reconnecting channel from a
    new one; without it the channel is counted as new, the careful way."""
    now = time.monotonic() if now is None else now
    let_go = _released.pop(key, None) if key else None
    _admitted.append((now, let_go is None or now - let_go > RETURNING_S))


def note_released(key: str, now: float | None = None) -> None:
    """A channel that HELD a live slot gave it back."""
    now = time.monotonic() if now is None else now
    if len(_released) > 2048:                    # nobody came back for these
        for k in [k for k, t in _released.items() if now - t > RETURNING_S]:
            del _released[k]
    _released[key] = now


def _pending(now: float) -> int:
    """Channels live whose cost is not in the readings yet, new or not."""
    while _admitted and now - _admitted[0][0] > PENDING_S:
        _admitted.popleft()
    return len(_admitted)


def _pending_new(now: float) -> int:
    _pending(now)
    return sum(1 for _, new in _admitted if new)


def allow(live: int, priority: bool = False, now: float | None = None) -> tuple[bool, str]:
    """May one more channel go live? (yes/no, and why not, in plain words.)

    `live` is how many hold a slot now. Only asked above the base.
    """
    now = time.monotonic() if now is None else now
    if not _fresh(now):
        return _no("the server's load has not been measured in the last few seconds", now)
    pending = _pending(now)
    if _pending_new(now) >= STEP:
        return _no("channels that just went live are still being measured", now)
    per_loop, per_cpu = _per_channel(live, pending)
    more = pending + 1
    loop_line = LOOP_LINE_PRIORITY if priority else LOOP_LINE
    cpu_line = CPU_LINE_PRIORITY if priority else CPU_LINE
    if _state["loop"] + more * per_loop > loop_line:
        return _no(f"the app's main thread is at {_state['loop']:.0f}% of the one core it can use", now)
    if _state["cpu"] + more * per_cpu > cpu_line:
        return _no(f"the server's processors are {_state['cpu']:.0f}% busy", now)
    if _state["mem_mb"] - more * PER_MEM_MB < _reserve_mb():
        return _no(f"only {_state['mem_mb'] / 1024:.1f} GB of memory is free", now)
    if _state["lag"] > LAG_LINE_S and not priority:
        return _no("the app's main thread is being held up", now)
    return True, ""


def _fresh(now: float) -> bool:
    return _state["samples"] >= 3 and now - _state["at"] <= STALE_S


def _per_channel(live: int, pending: int) -> tuple[float, float]:
    """(loop %, box CPU %) one more channel is expected to cost: everything
    measured, shared among the channels whose cost IS in the readings, and
    never under the floors. Everything includes the dashboard and whatever
    else the box is doing, which would not grow with a channel, so this errs
    toward letting in fewer."""
    settled = max(1, live - pending)
    return (max(PER_LOOP_FLOOR, _state["loop"] / settled),
            max(PER_CORE_FLOOR / _cores(), _state["cpu"] / settled))


def _no(why: str, now: float) -> tuple[bool, str]:
    _last_no["why"], _last_no["at"] = why, now
    return False, why


def room(live: int, now: float | None = None) -> int:
    """About how many MORE channels would be let in right now. For the admin
    page; 0 when nothing recent has been measured."""
    now = time.monotonic() if now is None else now
    under_floor = max(0, floor(now) - live)
    if not _fresh(now):
        return under_floor
    pending = _pending(now)
    per_loop, per_cpu = _per_channel(live, pending)
    by = min((LOOP_LINE - _state["loop"]) / per_loop,
             (CPU_LINE - _state["cpu"]) / per_cpu,
             (_state["mem_mb"] - _reserve_mb()) / PER_MEM_MB)
    return max(under_floor, int(by) - pending, 0)


def snapshot(live: int) -> dict:
    """What the admin page shows. Numbers and a sentence; nothing private."""
    now = time.monotonic()
    return {
        "on": enabled(), "base": base(), "measured": _fresh(now),
        "loop_pct": round(_state["loop"], 1), "cpu_pct": round(_state["cpu"], 1),
        "mem_free_gb": round(_state["mem_mb"] / 1024.0, 1),
        "room": room(live, now),
        # Why the last channel was turned away, while that is recent.
        "full_because": _last_no["why"] if now - _last_no["at"] <= 90 else "",
    }


def _watch_overload(now: float) -> None:
    if _state["loop"] < OVERLOAD_LINE:
        _over["since"] = None
        return
    if _over["since"] is None:
        _over["since"] = now
    told = _over["told"]
    if now - _over["since"] >= OVERLOAD_AFTER_S and (told is None or now - told >= OVERLOAD_EVERY_S):
        _over["told"] = now
        log.error("app_loop_overloaded", loop_pct=round(_state["loop"], 1),
                  for_s=round(now - _over["since"]),
                  fix="lower MAX_CONCURRENT_STREAMS in .env (the most that are ever "
                      "live) and restart, or run scripts/loop_profile.py to see what "
                      "is using the loop")


def _carry_path() -> str:
    return os.path.join(settings.local_storage_path, CARRY_FILE)


def carryable(live: int, now: float | None = None) -> int:
    """How many live channels the readings say the box is carrying. 0 means
    "note nothing": not measured, or short of memory. Channels whose cost is
    not in the readings yet are not counted, and past the WIDER lines the
    number is scaled down to what would sit on them, so what is let back in
    is never an overload."""
    now = time.monotonic() if now is None else now
    if not _fresh(now) or _state["mem_mb"] < _reserve_mb():
        return 0
    settled = live - _pending(now)
    if settled <= 0:
        return 0
    scale = min(1.0, LOOP_LINE_PRIORITY / max(_state["loop"], 1e-9),
                CPU_LINE_PRIORITY / max(_state["cpu"], 1e-9))
    return int(settled * scale)


def note_carried(live: int, now: float | None = None) -> int:
    """Note what is being carried well and return what a restart should be
    told: the most noted in the last few minutes (0 = nothing to write)."""
    now = time.monotonic() if now is None else now
    n = carryable(live, now)
    if n > 0:
        _carried.append((now, n))
    return _carried_now(now)


def _write_carry(n: int) -> None:
    path = _carry_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"live": int(n), "at": time.time()}, f)
    os.replace(tmp, path)


def load_carry(now: float | None = None) -> int:
    """Read what the box was carrying before this start. Called once, at
    start. Anything wrong with the file means nothing is carried."""
    now = time.monotonic() if now is None else now
    try:
        with open(_carry_path(), encoding="utf-8") as f:
            got = json.load(f)
        age = time.time() - float(got["at"])
        n = int(got["live"])
    except (OSError, ValueError, TypeError, KeyError):
        return 0
    if n <= 0 or not 0 <= age <= CARRY_MAX_AGE_S:
        return 0
    _carried.append((now, n))
    return n


async def run(live=lambda: 0, every: float = EVERY_S) -> None:
    """Keep the readings current. NEVER RAISES: it is one of the tasks main()
    gathers, and an exception out of any of those stops the whole product.
    A reading that fails is simply not recorded, and after STALE_S of that
    `allow` says no past the base.

    `live` returns how many channels hold a live slot, for the note a restart
    reads back."""
    try:
        carried = load_carry()
        if enabled():
            log.info("headroom_started", base=base(), carried=carried)
    except Exception:
        log.exception("headroom_carry_unreadable")
    last_carry = time.monotonic()
    prev_wall = time.monotonic()
    try:
        prev_cpu = _thread_cpu()
        prev_busy, prev_total = _box_cpu()
    except Exception:
        log.exception("headroom_cannot_measure")
        prev_cpu, prev_busy, prev_total = 0.0, 0.0, 0.0
    while True:
        try:
            await asyncio.sleep(every)
            wall = time.monotonic()
            dt = max(1e-6, wall - prev_wall)
            cpu = _thread_cpu()
            busy, total = _box_cpu()
            mem, mem_total = _mem_mb()
            loop_pct = 100.0 * (cpu - prev_cpu) / dt
            box_pct = 100.0 * (busy - prev_busy) / max(1.0, total - prev_total)
            # How late the loop woke this task: time it could not spend on
            # anything, whatever was holding it.
            record(loop_pct, box_pct, mem, max(0.0, dt - every), dt, wall, mem_total)
            prev_wall, prev_cpu, prev_busy, prev_total = wall, cpu, busy, total
            _watch_overload(wall)
            if enabled() and wall - last_carry >= CARRY_EVERY_S:
                last_carry = wall
                n = note_carried(int(live()), wall)
                if n > 0:
                    await asyncio.to_thread(_write_carry, n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("headroom_reading_failed")
            prev_wall = time.monotonic()
            await asyncio.sleep(10)
