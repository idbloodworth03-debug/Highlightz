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


def configure_logging(index: int, link=None) -> None:
    """The same lines the web process writes (src/main.py), each marked with
    which worker wrote it, to the same journal (stdout is inherited).

    AN ERROR HERE WOULD OTHERWISE REACH NOBODY. The owner is emailed by a tap
    on the WEB process's log (src/alerts); a worker's log.error never passes
    it. So every error and critical line is also sent across, where it is
    counted like one of the web process's own."""
    import structlog

    def mark(_logger, _method, event_dict):
        event_dict.setdefault("ingest", index)
        return event_dict

    def forward(_logger, method, event_dict):
        if link is not None and method in ("error", "critical", "exception"):
            try:
                fields = {}
                for k, v in event_dict.items():
                    if k in ("event", "timestamp", "level", "exception"):
                        continue
                    if isinstance(v, str):
                        fields[k] = v[:500]
                    elif v is None or isinstance(v, (int, float, bool)):
                        fields[k] = v
                link.cast("log_error", str(event_dict.get("event", "")), method, fields, droppable=True)
            except Exception:
                pass
        return event_dict

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.format_exc_info,
        mark,
        forward,
        structlog.processors.JSONRenderer(),
    ])


def build_handlers(state: dict, remote=None) -> dict:
    """What the web process may ask of a worker.

    spawn(record)            start watching one channel. `record` is the web
                             process's stream record; it becomes this
                             worker's mirror of it (remote.streams()).
    stop(channel, user_id)   stop watching it and forget the record.
    Both are casts, so they are handled in the order the web process sent
    them: "stop, then start again" for one channel cannot swap.
    ping / probe             is it alive, and is it the kind of process it
                             should be.
    Profile changes and Kick chat arrive with stage 3."""
    def count() -> None:
        from src.ingestion import workers
        state["streams"] = sum(1 for t in workers._workers.values() if not t.done())

    async def spawn(record: dict) -> None:
        from src.ingestion import workers
        remote.remember(record)
        await workers.spawn_worker(record["channel"], record.get("platform") or "twitch",
                                   record.get("user_id") or "", record.get("preset") or "default")
        count()

    async def stop(channel: str, user_id: str = "") -> None:
        from src.ingestion import workers
        from src.profiles.manager import get_profile_manager
        final = None
        try:
            await workers.stop_worker(channel, user_id)
        finally:
            remote.forget(channel, user_id)
            count()
            # This process no longer holds the channel's profile. Hand the web
            # process the last state and forget it here, so that if the
            # channel comes back to this worker it is read from the file
            # again (the dashboard may have changed it in between).
            try:
                pm = get_profile_manager(user_id or None)
                prof = await pm.get(channel)
                final = prof.to_dict() if prof is not None else None
                pm._cache.pop(channel, None)        # noqa: SLF001
            except Exception:
                final = None
            remote._link.cast("stopped", channel, user_id, final)   # noqa: SLF001

    def kick_chat(broadcaster_id: str, author: str, content: str) -> None:
        from src.chat.platform import kick_events
        kick_events._dispatch(str(broadcaster_id), author, content)     # noqa: SLF001

    async def profile_apply(uid: str, channel: str, op: dict, platform: str = "twitch"):
        """A change made in the dashboard (approve, reject, undo, sensitivity,
        preset) to a profile this worker holds: made on the same object its
        stream worker scores against."""
        from src.profiles import ops
        return await ops.apply(uid or None, channel, op, platform)

    def probe() -> dict:
        import importlib
        blocked = []
        for name in BLOCKED:
            try:
                importlib.import_module(name)
            except ImportError:
                blocked.append(name)
        from src.ingestion import host
        return {"pid": os.getpid(), "index": state["index"], "count": state["count"],
                "blocked": blocked,
                "dashboard_loaded": any(n in sys.modules for n in BLOCKED),
                "remote": host._remote is not None,
                "watching": sorted(remote.streams()) if remote is not None else []}

    out = {"ping": lambda: "pong", "probe": probe}
    if remote is not None:
        out.update(spawn=spawn, stop=stop, profile_apply=profile_apply, kick_chat=kick_chat)
    return out


async def _prepare(link, state: dict):
    """Everything a stream worker needs to run HERE instead of in the web
    process: the seam pointed at the wire, and somewhere to push moments.
    Returns the remote and the tasks to cancel on the way out."""
    from src.ingestion import host, workers
    from src.ingestion.remote import Remote
    remote = Remote(link)
    host._remote = remote                       # noqa: SLF001  (the one place it is set)
    # Kick chat arrives at the web process; here it is asked for and
    # forwarded (kick_events._remote), and the `kick_chat` handler below puts
    # each message where the worker's chat session is waiting for it.
    from src.chat.platform import kick_events
    kick_events._remote = remote                # noqa: SLF001
    tasks = []
    try:
        from src.billing.plans import uid_priority_level
        from src.queue.job_queue import JobQueue
        queue = JobQueue(priority_of=uid_priority_level)
        await queue.connect()
        workers.set_queue(queue)
    except Exception as exc:
        # Without the queue a worker can watch and score but not hand a
        # moment on. Say so loudly; the web process hears it as an error.
        import structlog
        structlog.get_logger(__name__).error("ingest_queue_unavailable", error=repr(exc)[:200])
    tasks.append(asyncio.create_task(workers.reap_orphan_capture_task(), name="capture-orphan-reaper"))
    return remote, tasks


async def run(fd: int, index: int, count: int) -> int:
    from src.ingestion.bridge import open_link
    state = {"index": index, "count": count, "started": time.monotonic(), "streams": 0}
    link = await open_link(socket.socket(fileno=fd), name=f"ingest-{index}")
    configure_logging(index, link)
    remote, tasks = await _prepare(link, state)
    link.handlers.update(build_handlers(state, remote))
    tasks.append(asyncio.create_task(_heartbeat(link, state), name="heartbeat"))
    link.cast("hello", {"index": index, "count": count, "pid": os.getpid()})
    try:
        await link.run()                    # returns when the web process is gone
    finally:
        for t in tasks:
            t.cancel()
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
