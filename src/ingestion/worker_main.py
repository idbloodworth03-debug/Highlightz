"""An ingest worker: one of the processes the stream workers run in.

    python -m src.ingestion.worker_main --fd 7 --index 0 --count 4

Started by the web process (src/ingestion/pool.py), never by hand: `--fd` is
its end of a socket the parent made, and the parent is the only thing on the
other end. See docs/HANDOFF.md, "THE SPLIT".

THREE RULES THIS FILE ENFORCES BEFORE ANYTHING ELSE RUNS:

1. THE DASHBOARD CANNOT BE IMPORTED HERE. `src.dashboard.api` loads every
   clip, stream and user into memory at import and writes them back to the
   same files; a second copy of it in another process would overwrite the
   real one's saves with its own stale picture. Nothing in a worker should
   need it (it talks to the web process through src/ingestion/host.py), so an
   import of it is a bug, and it fails loudly here instead of quietly
   corrupting the store.
2. IT DOES NOT OUTLIVE ITS PARENT. Its reason to exist is the web process on
   the other end of the socket. End-of-file on the socket ends it; and the
   kernel is asked to signal it if the parent dies without closing anything.
3. IT SAYS HOW BUSY ITS LOOP IS, every two seconds, because after the split
   that is the number the live limit has to follow for the channels that
   hash to it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.abc
import os
import signal
import socket
import sys
import time

BLOCKED = ("src.dashboard.api", "src.main")
HEARTBEAT_S = 2.0


class _NoDashboard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED:
            raise ImportError(
                f"{name} must not be imported in an ingest worker: it would load and "
                "rewrite the web process's state files. Use src.ingestion.host.")
        return None


def block_dashboard() -> None:
    if not any(isinstance(f, _NoDashboard) for f in sys.meta_path):
        sys.meta_path.insert(0, _NoDashboard())


def die_with_parent() -> None:
    """Ask Linux to send SIGTERM when the parent goes, however it goes."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:
        pass


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return 0.0


def _thread_cpu() -> float:
    try:
        return time.clock_gettime(time.CLOCK_THREAD_CPUTIME_ID)
    except (AttributeError, OSError):
        return 0.0


async def _heartbeat(link, state: dict, every: float = HEARTBEAT_S) -> None:
    """How busy this process's loop is, as a share of the one core it can
    use: the same figure src/ingestion/headroom.py reads for the web loop."""
    wall, cpu = time.monotonic(), _thread_cpu()
    while not link.closed:
        await asyncio.sleep(every)
        now, c = time.monotonic(), _thread_cpu()
        dt = max(1e-6, now - wall)
        beat = {"loop_pct": round(100.0 * (c - cpu) / dt, 1),
                "lag_ms": round(max(0.0, dt - every) * 1000.0, 1),
                "rss_mb": round(_rss_mb(), 1),
                "streams": int(state.get("streams", 0)),
                "up_s": round(now - state["started"])}
        wall, cpu = now, c
        link.cast("heartbeat", beat, droppable=True)


def build_handlers(state: dict) -> dict:
    """What the web process may ask of a worker. Stage 0: enough to prove the
    process is alive and is the kind of process it should be. The stream
    handlers (spawn, stop, profile changes, Kick chat) are added by the
    stages that move that work here."""
    def probe() -> dict:
        import importlib
        blocked = []
        for name in BLOCKED:
            try:
                importlib.import_module(name)
            except ImportError:
                blocked.append(name)
        return {"pid": os.getpid(), "index": state["index"], "count": state["count"],
                "blocked": blocked,
                "dashboard_loaded": any(n in sys.modules for n in BLOCKED)}

    return {"ping": lambda: "pong", "probe": probe}


async def run(fd: int, index: int, count: int) -> int:
    from src.ingestion.bridge import open_link
    state = {"index": index, "count": count, "started": time.monotonic(), "streams": 0}
    link = await open_link(socket.socket(fileno=fd), name=f"ingest-{index}",
                           handlers=build_handlers(state))
    beat = asyncio.create_task(_heartbeat(link, state), name="heartbeat")
    link.cast("hello", {"index": index, "count": count, "pid": os.getpid()})
    try:
        await link.run()                    # returns when the web process is gone
    finally:
        beat.cancel()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="src.ingestion.worker_main")
    ap.add_argument("--fd", type=int, required=True)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--count", type=int, required=True)
    args = ap.parse_args(argv)
    block_dashboard()
    die_with_parent()
    if os.getppid() == 1:                   # the parent went before we asked
        return 0
    try:
        return asyncio.run(run(args.fd, args.index, args.count))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
