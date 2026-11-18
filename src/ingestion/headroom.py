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
number of channels the readings say the box is carrying is noted, and a
channel that HELD A SLOT in the last few minutes is let straight back in, up
to that many, without asking. The note is also written to a small file
(`headroom.json` in the data folder) with which channels they were, so a
restart reads it back. It is only ever a number the box was measured
carrying, it goes stale in ten minutes, and it never passes the hard maximum.
It is for the channels that were running and for nobody else: a NEW channel
is always measured against its own plan's line, or a free account's channel
could walk into the place a paying one left.
What was running comes back up to the WIDER lines (75% of the loop, 85% of
the box): between those and the lines for new channels nothing new gets in,
but a channel whose feed dropped for a moment does not lose its place to a
reading two points over. Past the wider lines the note is scaled down, so
what is let back in is never an overload.

THE LINES are the ones stream_cost.py prints, a little inside them: the loop
65% of its core (the script warns at 70), the box 75% of all cores, and a
tenth of memory (1.5 GB at least) kept back. A paying plan is held to wider
ones, which is its reserved room: Pro 70% and 80%, Studio ("front of the
line") 75% and 85%. So when the server is full for a free account there is
still room for a Pro channel, and when it is full for Pro there is still
room for Studio (plans.uid_priority_level).

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

# % of the one core the loop can use, and % of every core: for everybody,
# for Pro (ahead), and for Studio (the front of the line).
LOOP_LINE, LOOP_LINE_AHEAD, LOOP_LINE_PRIORITY = 65.0, 70.0, 75.0
CPU_LINE, CPU_LINE_AHEAD, CPU_LINE_PRIORITY = 75.0, 80.0, 85.0
_LINES = {0: (LOOP_LINE, CPU_LINE), 1: (LOOP_LINE_AHEAD, CPU_LINE_AHEAD),
          2: (LOOP_LINE_PRIORITY, CPU_LINE_PRIORITY)}
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
# Who held a slot just before this process started (from the file), and until
# when that counts. `any` is a file written before it listed them.
_before = {"keys": frozenset(), "any": False, "until": 0.0}
_last_no = {"why": "", "at": 0.0}
_over = {"since": None, "told": None}       # None, not 0: the clock is seconds since boot
# The same, per ingest worker (THE SPLIT): "worker 2" -> its own since / told.
_over_workers: dict[str, dict] = {}
_loops_failed = {"at": None}


def forget() -> None:
    """Back to "nothing measured, nothing let in". For tests."""
    _state.update(at=0.0, loop=0.0, cpu=0.0, mem_mb=0.0, lag=0.0, samples=0, mem_total_mb=0.0)
    _carried.clear()
    _admitted.clear()
    _released.clear()
    _before.update(keys=frozenset(), any=False, until=0.0)
    _last_no.update(why="", at=0.0)
    _over.update(since=None, told=None)
    _over_workers.clear()
    _loops_failed.update(at=None)


def enabled() -> bool:
    return int(getattr(settings, "live_auto_base", 0) or 0) > 0


def base() -> int:
    return max(0, int(getattr(settings, "live_auto_base", 0) or 0))


def floor(key: str = "", now: float | None = None) -> int:
    """How many may be live before THIS channel is measured: the base, for
    anybody; and for a channel that was running a moment ago (or just before
    a restart), what the box was measured carrying then."""
    now = time.monotonic() if now is None else now
    return max(base(), _carried_now(now) if returning(key, now) else 0)


def returning(key: str, now: float | None = None) -> bool:
    """Did this channel hold a live slot in the last few minutes?"""
    if not key:
        return False
    now = time.monotonic() if now is None else now
    let_go = _released.get(key)
    if let_go is not None and now - let_go <= RETURNING_S:
        return True
    return now < _before["until"] and (_before["any"] or key in _before["keys"])


def _level(priority) -> int:
    """plans.uid_priority_level (2 Studio, 1 Pro, 0 the rest). True still
    means Studio, as it did when this was a yes/no."""
    if priority is True:
        return 2
    try:
        return max(0, min(2, int(priority or 0)))
    except (TypeError, ValueError):
        return 0


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


def allow(live: int, priority=0, now: float | None = None,
          loop: dict | None = None) -> tuple[bool, str]:
    """May one more channel go live? (yes/no, and why not, in plain words.)

    `live` is how many hold a slot now; `priority` is the account's place in
    the line (2 Studio, 1 Pro, 0 the rest). Only asked above the floor.

    `loop` is for THE SPLIT (docs/HANDOFF.md). With stream workers in
    processes of their own, a channel's cost lands on the loop of the worker
    it hashes to, not on this process's. The caller then passes that
    worker's reading: {"pct": averaged % of its core, "lag_s", "live": how
    many of ITS channels hold a slot, "age_s": how old the reading is,
    "name"}. The box's processors and memory are judged as always. Without
    it (one process) nothing here is different.
    """
    now = time.monotonic() if now is None else now
    if not _fresh(now):
        return _no("the server's load has not been measured in the last few seconds", now)
    if loop is not None and float(loop.get("age_s", STALE_S + 1)) > STALE_S:
        return _no(f"{loop.get('name', 'that worker')} has not reported its load in the last few seconds", now)
    pending = _pending(now)
    if _pending_new(now) >= STEP:
        return _no("channels that just went live are still being measured", now)
    per_loop, per_cpu = _per_channel(live, pending)
    more = pending + 1
    level = _level(priority)
    loop_line, cpu_line = _LINES[level]
    if loop is not None:
        # That worker's loop, shared among ITS settled channels. Channels
        # still unmeasured are all counted against it, wherever they went:
        # careful rather than exact, and it only slows the climb.
        pct = max(0.0, float(loop.get("pct") or 0.0))
        per_theirs = max(PER_LOOP_FLOOR, pct / max(1, int(loop.get("live") or 0) - pending))
        if pct + more * per_theirs > loop_line:
            return _no(f"{loop.get('name', 'that worker')} is at {pct:.0f}% of the one core it can use", now)
        if _state["cpu"] + more * per_cpu > cpu_line:
            return _no(f"the server's processors are {_state['cpu']:.0f}% busy", now)
        if _state["mem_mb"] - more * PER_MEM_MB < _reserve_mb():
            return _no(f"only {_state['mem_mb'] / 1024:.1f} GB of memory is free", now)
        if float(loop.get("lag_s") or 0.0) > LAG_LINE_S and level < 2:
            return _no(f"{loop.get('name', 'that worker')} is being held up", now)
        return True, ""
    if _state["loop"] + more * per_loop > loop_line:
        return _no(f"the app's main thread is at {_state['loop']:.0f}% of the one core it can use", now)
    if _state["cpu"] + more * per_cpu > cpu_line:
        return _no(f"the server's processors are {_state['cpu']:.0f}% busy", now)
    if _state["mem_mb"] - more * PER_MEM_MB < _reserve_mb():
        return _no(f"only {_state['mem_mb'] / 1024:.1f} GB of memory is free", now)
    if _state["lag"] > LAG_LINE_S and level < 2:
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
    under_floor = max(0, base() - live)
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


def _overdue(state: dict, pct: float, now: float) -> float | None:
    """The one rule for "a loop has stayed overloaded": over OVERLOAD_LINE for
    OVERLOAD_AFTER_S, said once and then at most every OVERLOAD_EVERY_S while
    it lasts. Returns how long it has been over when it is time to say so."""
    if pct < OVERLOAD_LINE:
        state["since"] = None
        return None
    if state["since"] is None:
        state["since"] = now
    told = state["told"]
    if now - state["since"] >= OVERLOAD_AFTER_S and (told is None or now - told >= OVERLOAD_EVERY_S):
        state["told"] = now
        return now - state["since"]
    return None


def _watch_overload(now: float) -> None:
    for_s = _overdue(_over, _state["loop"], now)
    if for_s is not None:
        log.error("app_loop_overloaded", loop_pct=round(_state["loop"], 1),
                  for_s=round(for_s),
                  fix="lower MAX_CONCURRENT_STREAMS in .env (the most that are ever "
                      "live) and restart, or run scripts/loop_profile.py to see what "
                      "is using the loop")


def watch_worker_loops(readings, now: float | None = None) -> None:
    """The same alarm for each ingest worker's loop (THE SPLIT).

    WHY IT IS NEEDED. With the split the web process's loop, the one
    `_watch_overload` reads, is nearly idle whatever the channels are doing:
    the scoring runs in the workers. A worker whose channels got heavier after
    they were let in would sit at 100% of its core, every one of its channels
    scoring late, and nothing would say so. The gate keeps NEW channels off a
    worker over its line; like the web loop's alarm, this is for the ones
    already there, which are never put out.

    `readings` are dashboard/ingest.py's `loop_readings()`: name, the 15 s
    average, how old it is, how many live channels. A reading that is too old
    is a worker that is down or silent; `ingest_worker_lost` has already said
    that, and it is not "overloaded", so its clock starts again."""
    now = time.monotonic() if now is None else now
    seen = set()
    for r in readings or ():
        name = str(r.get("name") or "") if isinstance(r, dict) else ""
        if not name:
            continue
        seen.add(name)
        state = _over_workers.setdefault(name, {"since": None, "told": None})
        try:
            age, pct = float(r.get("age_s", 1e9)), float(r.get("pct") or 0.0)
        except (TypeError, ValueError):
            continue
        if age > STALE_S:
            state["since"] = None
            continue
        for_s = _overdue(state, pct, now)
        if for_s is not None:
            log.error("ingest_loop_overloaded", worker=name, loop_pct=round(pct, 1),
                      live=r.get("live"), for_s=round(for_s),
                      fix="this worker's channels are more than one loop can score on "
                          "time. Raise INGEST_WORKERS in .env and restart (the channels "
                          "are dealt out again over more workers), or lower "
                          "MAX_CONCURRENT_STREAMS. scripts/loop_profile.py shows what "
                          "each worker is busy with")
    for name in [n for n in _over_workers if n not in seen]:
        _over_workers.pop(name, None)


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


def _write_carry(n: int, keys=()) -> None:
    path = _carry_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"live": int(n), "at": time.time(), "keys": sorted(str(k) for k in keys)}, f)
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
        keys = got.get("keys")
        if keys is not None:
            keys = frozenset(str(k) for k in keys)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0
    if n <= 0 or not 0 <= age <= CARRY_MAX_AGE_S:
        return 0
    _carried.append((now, n))
    _before.update(keys=keys or frozenset(), any=keys is None, until=now + CARRY_FOR_S)
    return n


async def run(live_keys=lambda: (), every: float = EVERY_S, loops=None) -> None:
    """Keep the readings current. NEVER RAISES: it is one of the tasks main()
    gathers, and an exception out of any of those stops the whole product.
    A reading that fails is simply not recorded, and after STALE_S of that
    `allow` says no past the base.

    `live_keys` returns the channels holding a live slot, for the note a
    restart reads back. `loops`, with the split on, returns every ingest
    worker's loop reading for the overload alarm (`watch_worker_loops`); a
    failure there is its own and never costs this process a reading."""
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
            if loops is not None:
                try:
                    watch_worker_loops(loops(), wall)
                except Exception:
                    # Once per half hour, not once per two seconds.
                    at = _loops_failed["at"]
                    if at is None or wall - at >= OVERLOAD_EVERY_S:
                        _loops_failed["at"] = wall
                        log.exception("headroom_worker_loops_unreadable")
            if enabled() and wall - last_carry >= CARRY_EVERY_S:
                last_carry = wall
                keys = list(live_keys())
                n = note_carried(len(keys), wall)
                if n > 0:
                    await asyncio.to_thread(_write_carry, n, keys)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("headroom_reading_failed")
            prev_wall = time.monotonic()
            await asyncio.sleep(10)
