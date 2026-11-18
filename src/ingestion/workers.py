"""Starting and stopping stream workers, and the registries of the ones running.

THE SPLIT, stage 2 (docs/HANDOFF.md, "THE SPLIT"). These lived in src/main.py,
which is the web process and which an ingest worker cannot import. They are
what a worker process has to run, so they live here, where both can reach
them: today the web process calls them (src/main.py imports these names, so
`main.spawn_worker` is this function), and a worker process will call the
same ones for the channels that hash to it.

Moved verbatim on 2026-11-18. The only change is that the three places that
touched the dashboard (the once-a-second `score_update`, and the final
"offline" with its `stream_updated`) go through src/ingestion/host.py, as
everything in stream_worker.py already does.

The registries are per process and keyed as before: a worker by
"user_id:channel", SHARED_BUFFERS by channel name. That last one is why a
channel's workers must all live in ONE process (bridge.owner_of).
"""

from __future__ import annotations

import asyncio

import structlog

from src.ingestion import host
from src.ingestion.platform.kick import KickPlatform
from src.ingestion.platform.twitch import TwitchPlatform
from src.ingestion.platform.youtube import YouTubePlatform
from src.ingestion.stream_worker import StreamWorker, WorkerConfig

log = structlog.get_logger(__name__)

# channel -> AudioMeter (transient loudness probes; no media stored)
SHARED_BUFFERS: dict = {}
PLATFORM_MAP = {"twitch": TwitchPlatform, "youtube": YouTubePlatform, "kick": KickPlatform}

_workers: dict[str, asyncio.Task] = {}
_worker_instances: dict[str, "StreamWorker"] = {}
# Where triggered moments are pushed (the Redis clip queue). Set once at
# startup by whoever owns this process: src/main.py today.
_queue = None


def set_queue(queue) -> None:
    global _queue
    _queue = queue



async def spawn_worker(channel: str, platform_name: str, user_id: str = "", preset: str = "default") -> None:
    worker_key = f"{user_id}:{channel}" if user_id else channel
    if worker_key in _workers and not _workers[worker_key].done():
        log.warning("worker_already_running", channel=channel)
        return

    # Kick workers spawn like Twitch ones since 2026-09-15. The public API
    # is asked with an app token (KICK_CLIENT_ID/SECRET) and the site endpoint
    # for the HLS playlist; chat is Kick's signed webhooks (kick_events). A
    # Kick clip is the file cut from live capture, so POST /streams refuses
    # Kick while CLIP_CAPTURE_ENABLED is off.
    platform_cls = PLATFORM_MAP.get(platform_name, TwitchPlatform)
    platform = platform_cls()

    async def _on_score(ch: str, score: float, breakdown: dict) -> None:
        await host.broadcast({
            "event": "score_update",
            "channel": ch,
            "score": score,
            "breakdown": breakdown,
        }, user_id=user_id)

    worker = StreamWorker(
        config=WorkerConfig(channel=channel, platform_name=platform_name, user_id=user_id, preset=preset),
        platform=platform,
        queue=_queue,
        shared_buffers=SHARED_BUFFERS,
        on_score=_on_score,
    )

    async def _run_and_update():
        stream_key = f"{user_id}:{channel}" if user_id else channel
        # Status transitions (starting → live → reconnecting → offline) are
        # driven by the worker itself so _streams always reflects reality.
        try:
            await worker.start()
        except Exception as exc:
            log.error("worker_crashed", channel=channel, error=str(exc))
        finally:
            if stream_key in host.streams():
                host.streams()[stream_key]["status"] = "offline"
                await host.broadcast(
                    {"event": "stream_updated", "stream": host.streams().get(stream_key, {})},
                    user_id=user_id,
                )

    task = asyncio.create_task(_run_and_update(), name=f"worker-{worker_key}")
    _workers[worker_key] = task
    _worker_instances[worker_key] = worker
    log.info("worker_spawned", channel=channel, platform=platform_name)


async def stop_worker(channel: str, user_id: str = "") -> None:
    worker_key = f"{user_id}:{channel}" if user_id else channel
    worker = _worker_instances.pop(worker_key, None)
    if worker:
        worker.stop()
    task = _workers.pop(worker_key, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    SHARED_BUFFERS.pop(channel, None)


async def reap_orphan_capture_task(interval_s: float = 300.0) -> None:
    """Every five minutes, stop capture no running worker holds. See
    src/ingestion/orphans.py; the first pass after a deploy has nothing to do,
    since a restart starts every pipeline from scratch."""
    from src.ingestion import orphans
    await asyncio.sleep(120)
    while True:
        try:
            n = await orphans.reap_orphan_capture(_worker_instances.values())
            if n:
                log.warning("capture_orphans_reaped", count=n)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("capture_orphan_reap_error", error=str(exc))
        await asyncio.sleep(interval_s)
