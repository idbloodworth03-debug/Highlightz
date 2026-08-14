"""
Highlightz — single-process entry point.

Runs concurrently:
  - FastAPI dashboard (uvicorn)
  - Redis pub/sub listener → spawns StreamWorkers
  - Inline clip processor loop (no separate worker process needed)
"""

import asyncio
import json
import time
import structlog
import uvicorn
from redis.asyncio import from_url as redis_from_url

from config.settings import settings
from src.dashboard import api as dashboard_api
from src.dashboard.api import app as dashboard_app
from src.ingestion.stream_worker import StreamWorker, WorkerConfig
from src.ingestion.platform.twitch import TwitchPlatform
from src.ingestion.platform.youtube import YouTubePlatform
from src.ingestion.platform.kick import KickPlatform
from src.queue.job_queue import JobQueue
from src.processor.clip_processor import ClipProcessor
from src.output import twitch_clips

log = structlog.get_logger(__name__)

# channel -> AudioMeter (transient loudness probes; no media stored)
SHARED_BUFFERS: dict = {}
PLATFORM_MAP = {"twitch": TwitchPlatform, "youtube": YouTubePlatform, "kick": KickPlatform}

_workers: dict[str, asyncio.Task] = {}
_worker_instances: dict[str, "StreamWorker"] = {}
_queue: JobQueue | None = None


# ── Worker management ─────────────────────────────────────────────────────────

async def spawn_worker(channel: str, platform_name: str, user_id: str = "", preset: str = "default") -> None:
    worker_key = f"{user_id}:{channel}" if user_id else channel
    if worker_key in _workers and not _workers[worker_key].done():
        log.warning("worker_already_running", channel=channel)
        return

    # Kick is closed off ("under construction") — its public API has no clips
    # endpoint and returns 401 on every channel lookup, so a Kick worker can only
    # reconnect-loop and spam errors. Skip spawning entirely until Kick is live.
    if platform_name == "kick":
        log.info("kick_worker_skipped", channel=channel,
                 reason="kick clipping closed off (under construction)")
        return

    platform_cls = PLATFORM_MAP.get(platform_name, TwitchPlatform)
    platform = platform_cls()

    async def _on_score(ch: str, score: float, breakdown: dict) -> None:
        await dashboard_api.broadcast({
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
            if stream_key in dashboard_api._streams:
                dashboard_api._streams[stream_key]["status"] = "offline"
                await dashboard_api.broadcast(
                    {"event": "stream_updated", "stream": dashboard_api._streams.get(stream_key, {})},
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
                msg_user_id = payload.get("user_id", "")
                if msg_user_id:
                    from src.auth import users as _user_store
                    if not _user_store.get_by_id(msg_user_id):
                        log.warning("stream_msg_unknown_user", user_id=msg_user_id)
                        continue
                await spawn_worker(
                    payload["channel"],
                    payload.get("platform", "twitch"),
                    msg_user_id,
                    payload.get("preset", "default"),
                )
            elif channel_name == "superclipbot:remove_streams":
                await stop_worker(payload["channel"], payload.get("user_id", ""))
        except Exception as exc:
            log.error("stream_registration_error", error=str(exc))


# ── Inline clip processor loop ────────────────────────────────────────────────

# A clip job targets a specific moment that Twitch's capture buffer only holds
# for ~60s. Past that the POST /clips either grabs the wrong window (still live)
# or 404s "Channel offline." (stream ended). 90s gives the processor headroom for
# normal jitter while guaranteeing a backlog can never replay stale moments.
MAX_CLIP_JOB_AGE_SECS = 90.0


async def run_clip_processor() -> None:
    processor = ClipProcessor()
    log.info("clip_processor_started")
    while True:
        job = None
        try:
            job = await _queue.pop(timeout=5)
            if job is None:
                continue
            age = time.time() - job.created_at
            if age > MAX_CLIP_JOB_AGE_SECS:
                # The moment is gone — clipping now would 404 (offline) or capture
                # the wrong window. Drop it so the processor catches up to real time
                # instead of grinding through a stale backlog.
                log.warning("clip_job_stale_dropped", clip_id=job.clip_id,
                            channel=job.channel, age_s=round(age, 1))
                continue
            # Queue full? Do not clip at all. Checked HERE, before the Helix
            # call, for two reasons: creating the clip and then discarding our
            # record would leave an orphan on the user's Twitch account that
            # never appears in Highlightz, and it would spend a Helix call from
            # a budget shared with every other user.
            _used, _cap = dashboard_api.pending_room(job.user_id)
            if _used >= _cap:
                log.info("clip_skipped_queue_full", channel=job.channel,
                         user_id=job.user_id, pending=_used, cap=_cap)
                await dashboard_api.notify_clip_missed(job.user_id, job.channel)
                continue

            log.info("processing_clip_job", clip_id=job.clip_id, channel=job.channel,
                     user_id=job.user_id, platform=job.platform, age_s=round(age, 1))
            # Budget: post_roll sleep (≤30s) + create_clip retries (≤15s) +
            # get_clip polling (≤50s) + overhead. 180s leaves comfortable margin
            # so a valid-but-slow clip isn't cut off mid-poll and lost.
            meta = await asyncio.wait_for(processor.process(job), timeout=180.0)
            await dashboard_api.notify_clip_ready(meta.to_dict())
        except asyncio.CancelledError:
            break
        except twitch_clips.ClipNotAuthorizedError:
            # The broadcaster has clipping restricted. This will NEVER succeed,
            # so retrying is pure waste: before this branch existed the bot
            # spent whole streams scoring correctly, firing hundreds of times
            # and producing nothing, while the user saw an empty queue and
            # reasonably concluded the product was broken.
            #
            # Stop the stream and say why. Removing it also frees the slot,
            # which matters on a plan capped at 3 or 10 streams.
            _uid = getattr(job, "user_id", "") if job else ""
            _ch  = getattr(job, "channel", "") if job else ""
            log.warning("clip_channel_not_clippable", channel=_ch, user_id=_uid)
            if _ch:
                try:
                    await dashboard_api.stop_stream_internal(_ch, _uid)
                except Exception:
                    log.warning("not_clippable_stop_failed", channel=_ch)
            if _uid:
                try:
                    await dashboard_api.broadcast(
                        {"event": "clip_failed", "channel": _ch or "that channel",
                         "message": (f"{_ch} has clipping restricted on Twitch, so we "
                                     f"can't create clips there. Monitoring stopped.")},
                        user_id=_uid,
                    )
                except Exception:
                    pass
            continue
        except Exception as exc:
            import traceback as _tb
            log.error("clip_processor_error", error=str(exc),
                      channel=getattr(job, "channel", "?") if job else "?",
                      user_id=getattr(job, "user_id", "?") if job else "?",
                      traceback=_tb.format_exc())
            # Surface the failure to the user's open tab. Without this a triggered
            # clip that fails to capture (ghost clip, rate-limit, token expiry)
            # silently never appears, with no explanation — the realtime contract
            # requires the user to learn about it live.
            _uid = getattr(job, "user_id", "") if job else ""
            if _uid:
                _ch = getattr(job, "channel", "") or "your stream"
                try:
                    await dashboard_api.broadcast(
                        {"event": "clip_failed", "channel": _ch,
                         "message": f"A clip from {_ch} couldn't be captured — it'll try again on the next moment."},
                        user_id=_uid,
                    )
                except Exception:
                    pass


# ── Dashboard ─────────────────────────────────────────────────────────────────

async def sweep_dead_clips_task() -> None:
    """Periodic task: remove clips that were deleted on Twitch's side after we
    captured them (some channels' mods mass-delete clips), so the review queue
    and library never show dead 'clip is no longer available' links.
    First pass shortly after boot, then every 6 hours."""
    from src.output import twitch_clips
    await asyncio.sleep(300)   # let startup settle; app token etc. ready
    while True:
        try:
            removed = await dashboard_api.sweep_dead_twitch_clips(
                twitch_clips.get_existing_clip_ids)
            if removed:
                log.info("dead_clip_sweep_done", removed=removed)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("dead_clip_sweep_error", error=str(exc))
        await asyncio.sleep(6 * 3600)


async def auto_delete_old_clips() -> None:
    """Daily task: delete approved clips older than 30 days to free disk space.

    SWEEPS SHORTLY AFTER BOOT, THEN DAILY. The loop used to sleep for a full day
    BEFORE its first pass, which meant it only ever ran on a process that had
    been up for 24 uninterrupted hours. During any active development period —
    where a deploy restarts the service several times a day — the sweep never
    executed at all and the clips directory only ever grew. Deleting is
    idempotent, so running it on every boot is safe; the short initial delay
    just keeps it out of the way while workers are starting.
    """
    import time
    MAX_AGE = 30 * 86400  # 30 days in seconds
    await asyncio.sleep(180)          # let startup settle, then sweep
    while True:
        try:
            now = time.time()
            # Identify and remove all stale clips under a single lock acquisition
            async with dashboard_api._data_lock:
                to_delete = [
                    c for c in list(dashboard_api._clips.values())
                    if c.get("status") == "approved" and now - c.get("created_at", now) > MAX_AGE
                ]
                for clip in to_delete:
                    dashboard_api._clips.pop(clip["id"], None)
                if to_delete:
                    dashboard_api._save_clips()
            # File deletion and broadcast outside the lock
            for clip in to_delete:
                dashboard_api._delete_clip_file(clip)
                await dashboard_api.broadcast(
                    {"event": "clip_removed", "clip_id": clip["id"]},
                    user_id=clip.get("user_id"),
                )
            if to_delete:
                log.info("auto_deleted_old_clips", count=len(to_delete))
        except Exception as exc:
            log.error("auto_delete_error", error=str(exc))
        # At the END of the body, not the start: the sweep has already run once
        # by the time we get here. Moving it without adding this back would
        # turn the loop into a busy spin on a single shared vCPU.
        await asyncio.sleep(86400)


async def run_dashboard() -> None:
    config = uvicorn.Config(
        dashboard_app,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
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
    async def _publish_new_stream(channel: str, platform: str, preset: str, user_id: str = "") -> None:
        await redis.publish(
            "superclipbot:new_streams",
            json.dumps({"channel": channel, "platform": platform, "preset": preset, "user_id": user_id}),
        )

    async def _publish_remove_stream(channel: str, user_id: str = "") -> None:
        await redis.publish(
            "superclipbot:remove_streams",
            json.dumps({"channel": channel, "user_id": user_id}),
        )

    dashboard_api.set_stream_publisher(_publish_new_stream, _publish_remove_stream)

    async def _force_clip(channel: str, user_id: str = "") -> None:
        from src.queue.job_queue import ClipJob
        stream_key = f"{user_id}:{channel}" if user_id else channel
        stream = dashboard_api._streams.get(stream_key, {})
        platform = stream.get("platform", "twitch")
        # Only Twitch supports programmatic clip creation. Kick has no public clip
        # API, so a Kick job would dead-end in the processor and fail silently —
        # don't enqueue it; tell the user why instead.
        if platform != "twitch":
            if user_id:
                try:
                    await dashboard_api.broadcast(
                        {"event": "clip_failed", "channel": channel,
                         "message": "Manual clips are only available on Twitch right now."},
                        user_id=user_id,
                    )
                except Exception:
                    pass
            return
        job = ClipJob(
            channel=channel,
            platform=platform,
            trigger_score=100.0,
            trigger_signals=[{"type": "MANUAL", "value": 1.0, "metadata": {}}],
            chat_snapshot=[],
            stream_title=stream.get("channel", channel),
            game="",
            pre_roll=60,
            post_roll=10,
            user_id=user_id,
        )
        await _queue.push(job)

    dashboard_api.set_force_clip_callback(_force_clip)

    # Seed admin account from existing password if no users exist yet
    from src.auth import users as user_store
    user_store.ensure_admin_exists(settings.dashboard_password)
    # One-time, idempotent: everyone who existed before the self-serve trial
    # replaced the free tier keeps free access permanently. Must run BEFORE any
    # request is served, or a legacy user could be told their trial ended.
    user_store.grandfather_existing_accounts()

    tasks = [
        asyncio.create_task(run_dashboard(), name="dashboard"),
        asyncio.create_task(listen_for_new_streams(redis), name="stream-listener"),
        asyncio.create_task(run_clip_processor(), name="clip-processor"),
        asyncio.create_task(auto_delete_old_clips(), name="auto-delete"),
        asyncio.create_task(sweep_dead_clips_task(), name="dead-clip-sweep"),
        asyncio.create_task(dashboard_api.idle_stream_reaper(), name="idle-reaper"),
        asyncio.create_task(dashboard_api.schedule_due_task(), name="schedule-due"),
    ]

    # Restore streams that were running before the last shutdown
    for stream in list(dashboard_api._streams.values()):
        await spawn_worker(stream["channel"], stream.get("platform", "twitch"),
                           stream.get("user_id", ""), stream.get("preset", "default"))

    log.info("superclipbot_started", version="1.0.0")
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Stop workers gracefully before cancelling their tasks
        for w in list(_worker_instances.values()):
            w.stop()
        for t in tasks:
            t.cancel()
        worker_tasks = list(_workers.values())
        await asyncio.gather(*tasks, *worker_tasks, return_exceptions=True)
        _workers.clear()
        _worker_instances.clear()
        await _queue.close()
        await redis.aclose()
        log.info("superclipbot_stopped")


if __name__ == "__main__":
    asyncio.run(main())
