"""
StreamWorker: orchestrates one live stream end-to-end.
Loads the streamer's profile on startup and updates baselines every 30s.
"""

import asyncio
import time
import traceback
import structlog
from dataclasses import dataclass
from typing import Callable

from config.settings import settings
from src.ingestion.video_buffer import VideoBuffer
from src.ingestion.platform.base import BasePlatform, StreamInfo
from src.chat.platform.twitch_chat import TwitchChatMonitor
from src.chat.platform.youtube_chat import YouTubeChatMonitor
from src.trigger.engine import TriggerEngine
from src.trigger.signals import TriggerEvent
from src.queue.job_queue import JobQueue, ClipJob
from src.profiles.manager import get_profile_manager
from src.profiles.profile import StreamerProfile

log = structlog.get_logger(__name__)

PROFILE_UPDATE_INTERVAL_FAST = 5   # seconds during learning phase (<30 samples)
PROFILE_UPDATE_INTERVAL_SLOW = 30  # seconds once calibrated


@dataclass
class WorkerConfig:
    channel: str
    platform_name: str
    user_id: str = ""
    preset: str = "default"


class StreamWorker:
    def __init__(
        self,
        config: WorkerConfig,
        platform: BasePlatform,
        queue: JobQueue,
        shared_buffers: dict[str, VideoBuffer],
        on_score: Callable | None = None,
    ) -> None:
        self._config = config
        self._platform = platform
        self._queue = queue
        self._shared_buffers = shared_buffers
        self._on_score = on_score
        self._running = False
        self._stream_info: StreamInfo | None = None
        self._buffer: VideoBuffer | None = None
        self._engine: TriggerEngine | None = None
        self._profile: StreamerProfile | None = None
        self._session_start: float = 0.0
        self._last_profile_save: float = 0.0
        self._last_threshold_decay: float = 0.0
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._running = True
        _pm = get_profile_manager(self._config.user_id)
        self._profile = await _pm.load(
            self._config.channel, self._config.platform_name
        )
        log.info("worker_starting", channel=self._config.channel,
                 sessions=self._profile.total_sessions,
                 threshold=round(self._profile.trigger_threshold, 3))

        while self._running:
            try:
                await self._run_session()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("worker_session_error", channel=self._config.channel, error=str(exc), traceback=traceback.format_exc())
                from src.dashboard import api as dashboard_api
                await dashboard_api.broadcast({
                    "event": "stream_error",
                    "channel": self._config.channel,
                    "error": str(exc),
                }, user_id=self._config.user_id)
            if self._running:
                log.info("worker_reconnecting", channel=self._config.channel, delay=30)
                from src.dashboard import api as dashboard_api
                await dashboard_api.broadcast({
                    "event": "stream_status",
                    "channel": self._config.channel,
                    "status": "reconnecting",
                }, user_id=self._config.user_id)
                await asyncio.sleep(30)

    async def _run_session(self) -> None:
        channel = self._config.channel
        self._session_start = time.time()
        self._last_profile_save = self._session_start
        self._profile.total_sessions += 1
        self._profile.last_seen = self._session_start

        self._stream_info = await self._platform.get_stream_info(channel)
        log.info("stream_found", channel=channel, title=self._stream_info.title)
        from src.dashboard import api as dashboard_api
        await dashboard_api.broadcast({
            "event": "stream_status",
            "channel": channel,
            "status": "live",
        }, user_id=self._config.user_id)

        self._buffer = VideoBuffer(channel, self._stream_info.stream_url)
        self._shared_buffers[channel] = self._buffer
        await self._buffer.start()

        self._engine = TriggerEngine(
            channel,
            on_trigger=self._on_trigger,
            on_score=self._on_score,
            profile=self._profile,
            buffer=self._buffer,
            preset=self._config.preset,
        )

        chat_task = asyncio.create_task(self._run_chat(), name=f"chat-{channel}")
        engine_task = asyncio.create_task(self._engine.run_evaluation_loop(), name=f"engine-{channel}")
        liveness_task = asyncio.create_task(self._liveness_check(), name=f"live-{channel}")
        profile_task = asyncio.create_task(self._profile_update_loop(), name=f"profile-{channel}")
        viewer_task = asyncio.create_task(self._viewer_poll_loop(), name=f"viewers-{channel}")

        self._tasks = [chat_task, engine_task, liveness_task, profile_task, viewer_task]

        try:
            done, pending = await asyncio.wait(
                self._tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                if exc:
                    raise exc
        finally:
            await self._cleanup()

    async def _run_chat(self) -> None:
        info = self._stream_info

        async def on_message(author: str, message: str) -> None:
            if self._engine:
                self._engine.ingest_chat(author, message)

        if self._config.platform_name == "twitch":
            monitor = TwitchChatMonitor(
                info.chat_channel_id,
                on_message,
                sub_raid_cb=lambda _: self._engine.notify_sub_raid() if self._engine else None,
            )
        else:
            monitor = YouTubeChatMonitor(info.chat_channel_id, on_message)

        await monitor.run()

    async def _liveness_check(self) -> None:
        while self._running:
            await asyncio.sleep(60)
            is_live = await self._platform.is_live(self._config.channel)
            if not is_live:
                log.info("stream_ended", channel=self._config.channel)
                raise RuntimeError("stream_offline")

    async def _profile_update_loop(self) -> None:
        """Periodically sample current metrics into the profile baseline."""
        while self._running:
            interval = (
                PROFILE_UPDATE_INTERVAL_FAST
                if self._profile.velocity_samples < 30
                else PROFILE_UPDATE_INTERVAL_SLOW
            )
            await asyncio.sleep(interval)
            if not self._engine:
                continue
            snap = self._engine._metrics.snapshot()

            velocity = snap.velocity
            keyword_rate = snap.keyword_hits / max(len(snap.messages), 1)

            if snap.messages:
                from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
                vader = SentimentIntensityAnalyzer()
                compounds = [abs(vader.polarity_scores(m)["compound"]) for m in snap.messages[-30:]]
                avg_sentiment = sum(compounds) / len(compounds)
            else:
                avg_sentiment = 0.0

            self._profile.update_velocity(velocity)
            self._profile.update_keyword_rate(keyword_rate)
            self._profile.update_sentiment(avg_sentiment)
            self._profile.total_watch_seconds += interval
            self._last_profile_save = time.time()

            # Hourly threshold decay — nudge 10% back toward the channel's seed threshold
            # so rejections can't permanently lock the threshold at its max.
            if self._last_threshold_decay == 0.0:
                self._last_threshold_decay = self._last_profile_save
            elif self._last_profile_save - self._last_threshold_decay >= 3600:
                from src.trigger.rules import get_rules
                seed_threshold = get_rules(self._config.channel, self._config.preset).trigger_threshold
                current = self._profile.trigger_threshold
                decayed = current + 0.1 * (seed_threshold - current)
                self._profile.trigger_threshold = round(decayed, 2)
                self._last_threshold_decay = self._last_profile_save
                if abs(current - decayed) > 0.1:
                    log.info("threshold_decayed", channel=self._config.channel,
                             from_=round(current, 2), to=round(decayed, 2),
                             seed=seed_threshold)

            await get_profile_manager(self._config.user_id).save(self._profile)
            from src.dashboard import api as dashboard_api
            await dashboard_api.broadcast({
                "event": "profile_updated",
                "profile": self._profile.to_dict(),
            }, user_id=self._config.user_id)
            log.debug("profile_updated", channel=self._config.channel,
                      avg_velocity=round(self._profile.avg_velocity, 3),
                      threshold=round(self._profile.trigger_threshold, 3),
                      samples=self._profile.velocity_samples)

    async def _viewer_poll_loop(self) -> None:
        """Poll viewer count every 60s and feed it to the trigger engine."""
        while self._running:
            await asyncio.sleep(60)
            if not self._engine:
                continue
            try:
                info = await self._platform.get_stream_info(self._config.channel)
                self._engine.update_viewer_count(info.viewer_count)
            except Exception as exc:
                log.debug("viewer_poll_failed", channel=self._config.channel, error=str(exc))

    async def _on_trigger(self, event: TriggerEvent) -> None:
        info = self._stream_info
        snapshot = self._engine._metrics.snapshot() if self._engine else None

        job = ClipJob(
            channel=event.channel,
            platform=self._config.platform_name,
            trigger_score=event.score,
            trigger_signals=[
                {"type": str(s.type).split(".")[-1], "value": s.value, "metadata": s.metadata}
                for s in event.signals
            ],
            chat_snapshot=snapshot.messages[-30:] if snapshot else [],
            stream_title=info.title if info else "",
            game=info.game if info else "",
            pre_roll=event.pre_roll,
            post_roll=event.post_roll,
            virality_score=event.virality_score,
            clip_title=event.clip_title,
            user_id=self._config.user_id,
        )
        await self._queue.push(job)

    async def _cleanup(self) -> None:
        # Cancel subtasks — guards against the case where CancelledError skips
        # the normal pending-task teardown in _run_session's try block.
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

        if self._profile and self._last_profile_save:
            self._profile.total_watch_seconds += time.time() - self._last_profile_save
            await get_profile_manager(self._config.user_id).save(self._profile)

        if self._buffer:
            await self._buffer.stop()
            self._shared_buffers.pop(self._config.channel, None)
            self._buffer = None
        if self._engine:
            self._engine.stop()
            self._engine = None

    def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
