"""
Highlightz — single-process entry point.

Runs concurrently:
  - FastAPI dashboard (uvicorn)
  - Redis pub/sub listener → spawns StreamWorkers
  - Inline clip processor loop (no separate worker process needed)
"""

import asyncio
import json
import structlog
import uvicorn
from redis.asyncio import from_url as redis_from_url

from config.settings import settings
from src.dashboard import api as dashboard_api
from src.dashboard.api import app as dashboard_app
from src.ingestion.stream_worker import StreamWorker, WorkerConfig
from src.ingestion.platform.twitch import TwitchPlatform
from src.ingestion.platform.youtube import YouTubePlatform
from src.ingestion.video_buffer import VideoBuffer
from src.queue.job_queue import JobQueue
from src.processor.clip_processor import ClipProcessor
from src.output.storage import build_storage

log = structlog.get_logger(__name__)

SHARED_BUFFERS: dict[str, VideoBuffer] = {}
PLATFORM_MAP = {"twitch": TwitchPlatform, "youtube": YouTubePlatform}

_workers: dict[str, asyncio.Task] = {}
_queue: JobQueue | None = None


# ── Worker management ─────────────────────────────────────────────────────────

async def spawn_worker(channel: str, platform_name: str) -> None:
    if channel in _workers and not _workers[channel].done():
        log.warning("worker_already_running", channel=channel)
        return

    platform_cls = PLATFORM_MAP.get(platform_name, TwitchPlatform)
    platform = platform_cls()

    async def _on_score(ch: str, score: float, breakdown: dict) -> None:
        await dashboard_api.broadcast({
            "event": "score_update",
            "channel": ch,
            "score": score,
            "breakdown": breakdown,
        })

    worker = StreamWorker(
        config=WorkerConfig(channel=channel, platform_name=platform_name),
        platform=platform,
        queue=_queue,
        shared_buffers=SHARED_BUFFERS,
        on_score=_on_score,
    )

    async def _run_and_update():
        dashboard_api._streams[channel]["status"] = "live"
        await dashboard_api.broadcast({"event": "stream_updated", "stream": dashboard_api._streams[channel]})
        try:
            await worker.start()
        except Exception as exc:
            log.error("worker_crashed", channel=channel, error=str(exc))
        finally:
            if channel in dashboard_api._streams:
                dashboard_api._streams[channel]["status"] = "offline"
                await dashboard_api.broadcast({"event": "stream_updated", "stream": dashboard_api._streams.get(channel, {})})

    task = asyncio.create_task(_run_and_update(), name=f"worker-{channel}")
    _workers[channel] = task
    log.info("worker_spawned", channel=channel, platform=platform_name)


async def stop_worker(channel: str) -> None:
    task = _workers.get(channel)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _workers.pop(channel, None)
    SHARED_BUFFERS.pop(channel, None)


# ── Redis pub/sub listener ────────────────────────────────────────────────────

async def listen_for_new_streams(redis) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe("superclipbot:new_streams", "superclipbot:remove_streams")
    log.info("listening_for_stream_registrations")
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            payload = json.loads(message["data"])
            channel_name = message["channel"]
            if channel_name == "superclipbot:new_streams":
                await spawn_worker(payload["channel"], payload.get("platform", "twitch"))
            elif channel_name == "superclipbot:remove_streams":
                await stop_worker(payload["channel"])
        except Exception as exc:
            log.error("stream_registration_error", error=str(exc))


# ── Inline clip processor loop ────────────────────────────────────────────────

async def run_clip_processor() -> None:
    storage = build_storage()
    processor = ClipProcessor(storage=storage, buffers=SHARED_BUFFERS)
    log.info("clip_processor_started")
    while True:
        try:
            job = await _queue.pop(timeout=5)
            if job is None:
                continue
            log.info("processing_clip_job", clip_id=job.clip_id, channel=job.channel)
            meta = await processor.process(job)
            await dashboard_api.notify_clip_ready(meta.to_dict())
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("clip_processor_error", error=str(exc))
            channel = job.channel if job else "unknown"
            await dashboard_api.broadcast({
                "event": "clip_error",
                "channel": channel,
                "error": str(exc),
            })


# ── Dashboard ─────────────────────────────────────────────────────────────────

async def run_dashboard() -> None:
    config = uvicorn.Config(
        dashboard_app,
        host="0.0.0.0",
        port=8000,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    global _queue

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    _queue = JobQueue()
    await _queue.connect()

    redis = await redis_from_url(settings.redis_url, decode_responses=True)

    # Give dashboard a publisher so adding/removing streams triggers workers
    async def _publish_new_stream(channel: str, platform: str, preset: str) -> None:
        await redis.publish(
            "superclipbot:new_streams",
            json.dumps({"channel": channel, "platform": platform, "preset": preset}),
        )

    async def _publish_remove_stream(channel: str) -> None:
        await redis.publish(
            "superclipbot:remove_streams",
            json.dumps({"channel": channel}),
        )

    dashboard_api.set_stream_publisher(_publish_new_stream, _publish_remove_stream)

    async def _force_clip(channel: str) -> None:
        from src.queue.job_queue import ClipJob
        stream = dashboard_api._streams.get(channel, {})
        job = ClipJob(
            channel=channel,
            platform=stream.get("platform", "twitch"),
            trigger_score=1.0,
            trigger_signals=[{"type": "MANUAL", "value": 1.0, "metadata": {}}],
            chat_snapshot=[],
            stream_title=stream.get("channel", channel),
            game="",
            pre_roll=60,
            post_roll=10,
        )
        await _queue.push(job)

    dashboard_api.set_force_clip_callback(_force_clip)

    tasks = [
        asyncio.create_task(run_dashboard(), name="dashboard"),
        asyncio.create_task(listen_for_new_streams(redis), name="stream-listener"),
        asyncio.create_task(run_clip_processor(), name="clip-processor"),
    ]

    # Restore streams that were running before the last shutdown
    for stream in list(dashboard_api._streams.values()):
        await spawn_worker(stream["channel"], stream.get("platform", "twitch"))

    log.info("superclipbot_started", version="1.0.0")
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _queue.close()
        await redis.aclose()
        log.info("superclipbot_stopped")


if __name__ == "__main__":
    asyncio.run(main())
