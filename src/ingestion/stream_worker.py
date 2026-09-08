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
from src.ingestion.audio_meter import AudioMeter


class CapacityQueued(RuntimeError):
    """The channel IS live, but every live slot on the box is taken.

    Deliberately a sibling of ChannelOffline rather than an error: both mean
    "not running yet, try again shortly", and both are the normal operation of
    a busy box rather than a fault. What it must NOT do is reuse the offline
    status — a card reading "offline" for a channel that is plainly streaming
    is a lie, and the one the viewer is most likely to notice."""
from src.ingestion.platform.base import BasePlatform, ChannelOffline, StreamInfo
from src.chat.platform.twitch_chat import TwitchChatMonitor
from src.chat.platform.youtube_chat import YouTubeChatMonitor
from src.trigger.engine import TriggerEngine
from src.trigger.signals import TriggerEvent
from src.queue.job_queue import JobQueue, ClipJob
from src.profiles.manager import get_profile_manager
from src.profiles.profile import StreamerProfile

log = structlog.get_logger(__name__)

# How many chat messages are stored alongside a clip, so the user can see why it
# was flagged. NAMED because the Privacy Policy quotes this number: it is the
# one place we say how much chat we retain, and a literal here plus a literal
# there is two numbers that can disagree. src/dashboard/api.py imports it.
CHAT_SNAPSHOT_MESSAGES = 30

# Our own clips are real Twitch clips and appear in the same Get Clips results
# as viewers'. Learning from them would be learning from ourselves. Cached
# because it reads two JSON files; refreshed a few times an hour is plenty.
_IDENTITY_TTL = 600.0
_identity_cache: tuple[float, set, set] = (0.0, set(), set())

# The 50% pending-queue reserve that used to live here is gone. Crowd
# suggestions now have their own per-plan budget (`max_suggested` in
# plans.py), which makes the same promise structurally rather than by
# arithmetic: they are not drawing on max_pending at all, so no reserve is
# needed to keep them off it. See _land_suggestions and api.suggestion_room.


def _our_clip_identity() -> tuple[set, set]:
    """(twitch ids of our users, slugs of clips we created)."""
    global _identity_cache
    ts, ids, slugs = _identity_cache
    now = time.time()
    if now - ts < _IDENTITY_TTL:
        return ids, slugs
    import json as _json
    from pathlib import Path as _Path
    base = _Path(settings.local_storage_path)
    ids, slugs = set(), set()
    try:
        users = _json.loads((base / "users.json").read_text())
        users = users if isinstance(users, list) else users.get("users", [])
        ids = {str(u.get("twitch_id")) for u in users if u.get("twitch_id")}
    except Exception:
        pass
    try:
        clips = _json.loads((base / "clips.json").read_text())
        slugs = {str(c.get("twitch_clip_id")) for c in clips if c.get("twitch_clip_id")}
    except Exception:
        pass
    _identity_cache = (now, ids, slugs)
    return ids, slugs

PROFILE_UPDATE_INTERVAL_FAST = 3   # seconds during calibration (first 100 samples = ~5 min)
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
        shared_buffers: dict,
        on_score: Callable | None = None,
    ) -> None:
        self._config = config
        self._platform = platform
        self._queue = queue
        self._shared_buffers = shared_buffers
        self._on_score = on_score
        self._running = False
        self._stream_info: StreamInfo | None = None
        self._buffer: AudioMeter | None = None
        # The rolling capture buffer this worker is holding, if capture is on.
        # Held by reference so _cleanup can release its share of the channel's
        # recorder — several workers can be watching the same channel.
        self._recorder = None
        # Detached cut tasks. A cut cannot run inline with the trigger (the
        # tail of the clip has not been broadcast yet, let alone buffered), so
        # each one waits and then cuts. Tracked so stop() can cancel them:
        # otherwise a removed stream keeps writing files for minutes.
        self._cut_tasks: set[asyncio.Task] = set()
        self._engine: TriggerEngine | None = None
        self._profile: StreamerProfile | None = None
        # Resolved once per worker — the viewer-clip watcher needs it and the
        # lookup costs a Helix call.
        self._broadcaster_id: str | None = None
        self._session_start: float = 0.0
        self._last_profile_save: float = 0.0
        self._last_threshold_decay: float = 0.0
        self._tasks: list[asyncio.Task] = []
        # The preset actually in force. Starts as whatever was chosen when the
        # stream was added and is re-resolved each time the channel goes live —
        # see _resolve_preset.
        self._preset: str = config.preset

    @property
    def _stream_key(self) -> str:
        """The key api._streams is filed under. Was rebuilt inline in three
        places; one of them drifting is a slot that never comes back."""
        return (f"{self._config.user_id}:{self._config.channel}"
                if self._config.user_id else self._config.channel)

    async def start(self) -> None:
        self._running = True
        _pm = get_profile_manager(self._config.user_id)
        self._profile = await _pm.load(
            self._config.channel, self._config.platform_name
        )
        await self._research_if_new(_pm)
        log.info("worker_starting", channel=self._config.channel,
                 sessions=self._profile.total_sessions,
                 threshold=round(self._profile.trigger_threshold, 3))

        while self._running:
            # Offline is the expected outcome, not a fault — see ChannelOffline.
            # Separating the two is what stops a waiting channel from logging a
            # traceback and flashing "a stream hit an error" at the user every
            # 30 seconds, while a genuine fault still says so loudly.
            offline = False
            queued  = False
            try:
                try:
                    await self._run_session()
                finally:
                    # UNCONDITIONAL. A slot leaked on an error path is capacity
                    # this process never sees again until it restarts, and the
                    # box would slowly starve with nothing in the logs to say
                    # why. Idempotent, so releasing one we never took is fine —
                    # which is the case for every ChannelOffline, since the
                    # slot is taken after the liveness check.
                    from src.dashboard import api as _api
                    _api.release_live_slot(self._stream_key)
            except asyncio.CancelledError:
                break
            except ChannelOffline:
                offline = True
                log.info("stream_not_live", channel=self._config.channel,
                         retry_in=30)
            except CapacityQueued:
                queued = True
                log.info("stream_queued_for_capacity",
                         channel=self._config.channel, retry_in=30)
            except Exception as exc:
                log.error("worker_session_error", channel=self._config.channel, error=str(exc), traceback=traceback.format_exc())
                from src.dashboard import api as dashboard_api
                await dashboard_api.broadcast({
                    "event": "stream_error",
                    "channel": self._config.channel,
                    "error": "Stream session ended unexpectedly. Reconnecting…",
                }, user_id=self._config.user_id)
            if self._running:
                # "offline" is an existing status the card already renders, so
                # this needs no new event and no new frontend branch — it just
                # stops mislabelling a waiting channel as reconnecting.
                status = ("queued" if queued else
                          "offline" if offline else "reconnecting")
                if not offline and not queued:
                    log.info("worker_reconnecting", channel=self._config.channel, delay=30)
                from src.dashboard import api as dashboard_api
                _sk = self._stream_key
                if _sk in dashboard_api._streams:
                    dashboard_api._streams[_sk]["status"] = status
                await dashboard_api.broadcast({
                    "event": "stream_status",
                    "channel": self._config.channel,
                    "status": status,
                }, user_id=self._config.user_id)
                await asyncio.sleep(30)

    async def _research_if_new(self, pm) -> None:
        """One-time pre-flight research for channels we've never watched.

        Looks at the channel's existing Twitch clips to seed a more accurate
        starting threshold. Only runs for brand-new Twitch profiles that
        haven't learned anything live yet; never overrides live learning."""
        p = self._profile
        if p.researched or self._config.platform_name != "twitch":
            return
        if p.total_sessions > 0 or p.total_clips > 0 or p.velocity_samples > 0:
            # Already has live data — too late to seed, just mark done.
            p.researched = True
            await pm.save(p)
            return
        try:
            from src.profiles.research import research_channel
            stats = await research_channel(self._config.channel)
        except Exception as exc:
            log.warning("streamer_research_failed", channel=self._config.channel,
                        error=str(exc))
            return  # leave researched=False so we retry next session
        p.researched = True
        if stats:
            p.research_clips_per_day = stats["clips_per_day"]
            p.trigger_threshold      = stats["suggested_threshold"]

            # Pre-seed velocity baseline so the spike detector has a
            # non-zero reference on the very first live evaluation.
            # Inject several synthetic samples so the EMA stabilises
            # quickly rather than starting from a single cold reading.
            est_vel = stats["estimated_velocity"]
            for _ in range(20):
                p.update_velocity(est_vel)

            log.info("streamer_research_applied", channel=self._config.channel,
                     clips_per_day=stats["clips_per_day"],
                     span_days=stats.get("span_days"),
                     existing_clips=stats["clip_count"],
                     median_views=stats["median_views"],
                     p90_views=stats.get("p90_views"),
                     seeded_threshold=stats["suggested_threshold"],
                     seeded_velocity=est_vel)
        else:
            log.info("streamer_research_no_clips", channel=self._config.channel)
        await pm.save(p)

    async def _resolve_preset(self, stream_key: str) -> None:
        """Re-pick the preset now that the channel is live and Twitch knows it.

        The pick used to happen once, in POST /streams. Twitch only reports a
        category and a viewer count for a LIVE channel, so adding one that was
        offline — a clipper queueing up the afternoon's roster, which is the
        normal case — always fell back to "default" and stayed there for good.
        The same lookup at go-live has the real answer.

        It also tracks a mid-session category change: a streamer who opens on
        Just Chatting and switches to Valorant gets the fps preset on the next
        session instead of being scored as a talk show all week.

        ONLY when the stored preset is "default", matching POST /streams: an
        explicit choice is a decision and must never be silently overridden.
        (Neither place can tell "chose Default" from "left it on Default" — the
        stream record does not carry that. Same rule in both, so at least the
        behaviour is consistent.)
        """
        chosen = self._config.preset
        if chosen == "default" and self._stream_info is not None:
            from src.trigger.rules import auto_preset
            chosen = auto_preset(getattr(self._stream_info, "game", "") or "",
                                 getattr(self._stream_info, "viewer_count", 0) or 0)
        self._preset = chosen

        # Record it on the profile so every later load decays toward the right
        # seed, including loads from paths that never see a WorkerConfig.
        if self._profile is not None and self._profile.preset != chosen:
            self._profile.preset = chosen
            await get_profile_manager(self._config.user_id).save(self._profile)

        # Log every outcome, not just the interesting one. The previous version
        # logged only when it picked something other than "default", so the most
        # common result was invisible and "is auto-preset working?" could not be
        # answered from the journal at all.
        log.info("preset_resolved", channel=self._config.channel,
                 preset=chosen, configured=self._config.preset,
                 game=getattr(self._stream_info, "game", ""),
                 viewers=getattr(self._stream_info, "viewer_count", 0))

        # Realtime contract: the stream card shows the preset, so a tab that is
        # already open has to see the change without a refresh.
        if chosen == self._config.preset:
            return
        from src.dashboard import api as dashboard_api
        record = dashboard_api._streams.get(stream_key)
        if record is None:
            return
        record["preset"] = chosen
        await dashboard_api.broadcast(
            {"event": "stream_updated", "stream": record},
            user_id=self._config.user_id)

    async def _run_session(self) -> None:
        channel = self._config.channel
        self._session_start = time.time()
        self._last_profile_save = self._session_start
        self._profile.total_sessions += 1
        self._profile.last_seen = self._session_start

        self._stream_info = await self._platform.get_stream_info(channel)
        log.info("stream_found", channel=channel, title=self._stream_info.title)
        from src.dashboard import api as dashboard_api
        _sk = self._stream_key
        if _sk in dashboard_api._streams:
            dashboard_api._streams[_sk]["status"] = "live"
        await dashboard_api.broadcast({
            "event": "stream_status",
            "channel": channel,
            "status": "live",
        }, user_id=self._config.user_id)

        # THE HARDWARE GATE, and it sits here for a reason: get_stream_info
        # above is what raises ChannelOffline, so by this line the channel is
        # confirmed live and about to start costing two subprocesses. Asking
        # any earlier would ration channels that are not running; any later and
        # the meter is already spawned.
        if not dashboard_api.acquire_live_slot(_sk):
            raise CapacityQueued(f"{channel} is live but the box is full")

        await self._resolve_preset(_sk)

        # Audio-only loudness probe (no recording). Disabled if the operator
        # turns off audio detection — the engine then runs chat-only.
        if settings.enable_audio_detection:
            self._buffer = AudioMeter(channel, self._stream_info.stream_url)
            self._shared_buffers[channel] = self._buffer
            await self._buffer.start()
        else:
            self._buffer = None

        # The rolling capture buffer. Deliberately NOT sharing the meter's
        # pipeline — see clip_recorder's module docstring on why the recorder
        # is isolated from the thing that scores. `ensure` returns None when
        # capture is switched off or the broadcaster has opted out, and both
        # are ordinary outcomes: the clip is still made, it just has no local
        # file, exactly as before this feature existed.
        from src.ingestion import clip_recorder
        try:
            self._recorder = await clip_recorder.ensure(
                channel, self._stream_info.stream_url)
        except Exception as exc:
            # Capture is an enhancement. It must never be able to stop a
            # channel being monitored.
            log.warning("clip_recorder_start_failed", channel=channel, error=str(exc))
            self._recorder = None

        self._engine = TriggerEngine(
            channel,
            on_trigger=self._on_trigger,
            on_score=self._on_score,
            profile=self._profile,
            buffer=self._buffer,
            preset=self._preset,
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
        elif self._config.platform_name == "kick":
            from src.chat.platform.kick_chat import KickChatMonitor
            monitor = KickChatMonitor(info.chat_channel_id, on_message)
        else:
            monitor = YouTubeChatMonitor(info.chat_channel_id, on_message)

        await monitor.run()

    async def _liveness_check(self) -> None:
        _consecutive_errors = 0
        while self._running:
            await asyncio.sleep(60)
            try:
                is_live = await self._platform.is_live(self._config.channel)
                _consecutive_errors = 0
            except Exception as exc:
                # Transient API / network error — don't kill the session for a
                # single blip. Allow up to 3 consecutive failures before giving up
                # so a brief Twitch outage doesn't restart every worker.
                _consecutive_errors += 1
                log.warning("liveness_check_error", channel=self._config.channel,
                            error=str(exc), consecutive=_consecutive_errors)
                if _consecutive_errors >= 3:
                    log.error("liveness_check_failed_repeatedly", channel=self._config.channel,
                              consecutive=_consecutive_errors)
                    raise RuntimeError("liveness_check_failed") from exc
                continue
            if not is_live:
                log.info("stream_ended", channel=self._config.channel)
                # A broadcast ending is the normal end of a session, not a
                # crash. RuntimeError here landed in the generic handler and
                # produced a traceback plus an error toast every single time a
                # streamer went to bed.
                raise ChannelOffline(f"{self._config.channel} ended its broadcast")

    async def _profile_update_loop(self) -> None:
        """Periodically sample current metrics into the profile baseline."""
        while self._running:
            interval = (
                PROFILE_UPDATE_INTERVAL_FAST
                if not self._profile.is_calibrated
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

            was_calibrated = self._profile.is_calibrated
            self._profile.update_velocity(velocity)
            self._profile.update_keyword_rate(keyword_rate)
            self._profile.update_sentiment(avg_sentiment)
            if self._engine and self._engine._audio_samples >= 30:
                self._profile.update_audio_db(self._engine._audio_baseline_db)
            self._profile.total_watch_seconds += interval
            self._last_profile_save = time.time()

            if not was_calibrated:
                pct = round(self._profile.calibration_pct, 0)
                if self._profile.is_calibrated:
                    log.info("channel_calibrated", channel=self._config.channel,
                             samples=self._profile.velocity_samples,
                             avg_velocity=round(self._profile.avg_velocity, 3),
                             threshold=round(self._profile.trigger_threshold, 2))
                elif self._profile.velocity_samples % 20 == 0:
                    log.info("calibrating", channel=self._config.channel,
                             pct=pct, samples=self._profile.velocity_samples,
                             avg_velocity=round(self._profile.avg_velocity, 3))

            # Hourly threshold decay — nudge 10% back toward the channel's seed threshold
            # so rejections can't permanently lock the threshold at its max.
            if self._last_threshold_decay == 0.0:
                self._last_threshold_decay = self._last_profile_save
            elif self._last_profile_save - self._last_threshold_decay >= 3600:
                from src.trigger.rules import get_rules
                seed_threshold = get_rules(self._config.channel, self._preset).trigger_threshold
                current = self._profile.trigger_threshold
                # Single decay implementation, shared with the load-time
                # catch-up in ProfileManager.load — two copies would drift.
                # It also mean-reverts signal weights on the same clock: the
                # anti-stale/anti-dead valve, so learned preferences fade
                # toward neutral unless refreshed by new reviews.
                self._profile.decay_elapsed(seed_threshold)
                decayed = self._profile.trigger_threshold
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
        """Poll viewer count every 60s and feed it to the trigger engine.

        Also records viewer-created clips for LEARNING (never for triggering —
        measured Twitch visibility lag is ~167s median, far too late to act
        on). See src/trigger/viewer_clips.py."""
        while self._running:
            await asyncio.sleep(60)
            if not self._engine:
                continue
            try:
                info = await self._platform.get_stream_info(self._config.channel)
                self._engine.update_viewer_count(info.viewer_count)
            except Exception as exc:
                log.debug("viewer_poll_failed", channel=self._config.channel, error=str(exc))
            await self._record_viewer_clips()

    async def _record_viewer_clips(self) -> None:
        """Crowd highlight labels, and the crowd's own picks.

        Two jobs on ONE Helix call. The recording half is observation only — it
        can neither fire a clip nor change a score. The suggesting half turns
        the same rows into review-queue items without consulting our score
        (see src/trigger/suggested_clips.py). Neither may disturb clipping, so
        the whole thing is wrapped and demoted to a debug log on failure.
        """
        from src.trigger import viewer_clips, suggested_clips
        chan = self._config.channel
        # Gate is shared across every worker on this channel: five users
        # watching one streamer must cost one poll, not five (Helix budget is
        # per client-id, shared across all our users).
        if not viewer_clips.due(chan):
            return
        try:
            from src.output.twitch_clips import resolve_broadcaster_id
            bid = self._broadcaster_id or await resolve_broadcaster_id(chan)
            self._broadcaster_id = bid
            if not bid:
                return
            ours_ids, ours_slugs = _our_clip_identity()
            buf = suggested_clips.buffer_for(chan)
            await viewer_clips.poll_and_record(
                chan, bid, self._engine, ours_ids, ours_slugs,
                on_rows=lambda rows: buf.offer(rows, ours_ids, ours_slugs))
        except Exception as exc:
            log.debug("viewer_clip_record_failed", channel=chan, error=str(exc))
            return
        # Outside the try above so a poll failure does not skip suggestions
        # that ripened on an EARLIER poll — the buffer holds them, and a single
        # failed Helix call should not strand a moment the crowd already picked.
        try:
            await self._land_suggestions(buf)
        except Exception as exc:
            log.debug("suggested_clip_failed", channel=chan, error=str(exc))

    async def _land_suggestions(self, buf) -> None:
        """Turn ripe crowd suggestions into pending clips for this user.

        THEIR OWN BUDGET, not a slice of the review queue. Suggestions used to
        share max_pending with triggered clips, held off them by a 50% reserve,
        because a full queue drops the NEWEST arrival and an unreserved
        suggestion stream could cost a user the clips they pay for. A separate
        budget (max_suggested: Free 3) makes that guarantee structurally
        instead of arithmetically — a suggestion is not drawing from the same
        pool at all, so it cannot take a slot a triggered clip wanted however
        busy the channel gets.
        """
        from src.dashboard import api as dashboard_api
        from src.processor.metadata import ClipMetadata
        from src.trigger import dismissed_suggestions

        ripe = buf.ready()
        if not ripe:
            return
        uid = self._config.user_id
        used, room = dashboard_api.suggestion_room(uid)
        info = self._stream_info

        for s in ripe:
            # Already said no to this moment. Checked HERE rather than in the
            # buffer because the buffer is shared by every worker watching this
            # channel — one user clearing their queue must not suppress a
            # suggestion another user has never been shown. The buffer's own
            # guards are in-memory and cover a single process; this one is what
            # survives a restart, which is when the reappearance was happening.
            if dismissed_suggestions.is_dismissed(uid, s.channel, s.slug,
                                                  s.created_at):
                log.info("suggested_clip_skipped_dismissed", channel=s.channel,
                         user_id=uid, slug=s.slug)
                continue
            if used >= room:
                log.info("suggested_clip_skipped_budget", channel=s.channel,
                         user_id=uid, waiting=used, suggestion_cap=room)
                break
            meta = ClipMetadata(
                channel=s.channel,
                platform=self._config.platform_name,
                # NOT the moment the suggestion was processed. The viewer's
                # clip timestamp is when the moment actually happened, which is
                # what the review queue sorts by — and it is what lets
                # notify_clip_ready's dedup window recognise a moment we
                # already clipped ourselves and drop this as a duplicate.
                created_at=s.created_at,
                # A suggestion comes off the same channel, so it is gated the
                # same way — the player has to route it identically.
                age_restricted=bool(info.is_mature) if info else False,
                stream_title=info.title if info else "",
                game=info.game if info else "",
                clip_title=s.title,
                duration_seconds=s.duration or 30.0,
                user_id=uid,
                twitch_clip_id=s.slug,
                twitch_url=s.url,
                embed_url=s.embed_url,
                thumbnail_url=s.thumbnail_url,
                # Zero, and deliberately: no score was consulted to get here.
                trigger_score=0.0,
                virality_score=0.0,
                suggested=True,
                clipper_count=s.clipper_count,
                suggested_views=s.view_count,
            )
            await dashboard_api.notify_clip_ready(meta.to_dict())
            used += 1
            log.info("suggested_clip_landed", channel=s.channel, user_id=uid,
                     slug=s.slug, clippers=s.clipper_count, views=s.view_count)

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
            chat_snapshot=(snapshot.messages[-CHAT_SNAPSHOT_MESSAGES:]
                           if snapshot else []),
            # Read off the Get Streams response we already make for liveness,
            # so it costs nothing. Carried on the JOB rather than looked up in
            # the processor, because the stream may have ended by then and the
            # flag would be gone.
            age_restricted=bool(info.is_mature) if info else False,
            stream_title=info.title if info else "",
            game=info.game if info else "",
            pre_roll=event.pre_roll,
            post_roll=event.post_roll,
            virality_score=event.virality_score,
            clip_title=event.clip_title,
            user_id=self._config.user_id,
        )
        await self._queue.push(job)

        # ── the local file ───────────────────────────────────────────────
        # Cut the same window the platform clip covers out of the rolling
        # buffer, so the moment is a FILE the user can download, edit and
        # schedule without leaving the site.
        #
        # Detached and delayed rather than inline, for two reasons: the clip's
        # TAIL has not been broadcast yet at trigger time, let alone reached
        # the buffer; and the job above must be pushed immediately, because
        # the platform's own clip capture is relative to now and every second
        # spent here is a second of the moment lost on their side.
        if self._recorder is not None:
            task = asyncio.create_task(
                self._cut_local_file(job.clip_id, time.time(),
                                     event.pre_roll, event.post_roll),
                name=f"cut-{job.clip_id[:8]}")
            self._cut_tasks.add(task)
            task.add_done_callback(self._cut_tasks.discard)

    async def _cut_local_file(self, clip_id: str, fired_at: float,
                              pre_roll: float, post_roll: float) -> None:
        """Wait for the moment's tail to land in the buffer, then cut it out.

        Every failure here is silent to the user by design: a clip with no
        local file behaves exactly as clips did before capture existed, so a
        buffer that was not covering the window, a full disk or an ffmpeg
        error all degrade to the old product rather than to an error.
        """
        from src.ingestion import clip_recorder      # noqa: F401  (registry)
        from src.clips import files as clip_files
        seg = max(1, settings.clip_capture_segment_s)
        # Two segments of slack, not one: ffmpeg only closes a segment when
        # the NEXT one starts, so the final moment of the clip is not readable
        # until a further segment has begun.
        await asyncio.sleep(post_roll + seg * 2 + 1.0)
        rec = self._recorder
        if rec is None or not self._running:
            return
        if not clip_files.headroom_ok():
            log.warning("clip_file_skipped_no_headroom", clip_id=clip_id,
                        channel=self._config.channel)
            return
        out = clip_files.path_for(clip_id)
        if out is None:
            return
        try:
            got = await rec.cut(fired_at - pre_roll, fired_at + post_roll, out)
        except Exception as exc:
            log.warning("clip_cut_raised", clip_id=clip_id, error=str(exc))
            return
        if not got:
            return
        log.info("clip_file_ready", clip_id=clip_id, channel=self._config.channel,
                 size_mb=round(clip_files.size_of(clip_id) / (1024 * 1024), 1))
        # Realtime contract (CLAUDE.md): a clip becoming downloadable is
        # user-visible state, so the open tab is told rather than finding out
        # on a reload. The record itself may not exist yet — it is created by
        # the processor, possibly in another process — which is exactly why
        # the file is addressed by clip id and the flag is derived from disk
        # when a clip is serialised, instead of written onto the record here.
        try:
            from src.dashboard import api as dashboard_api
            await dashboard_api.broadcast(
                {"event": "clip_file_ready", "clip_id": clip_id},
                user_id=self._config.user_id)
        except Exception as exc:
            log.warning("clip_file_broadcast_failed", clip_id=clip_id, error=str(exc))

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

        # In-flight cuts die with the session. A worker that has stopped has no
        # business writing more video, and the buffer it would read from is
        # about to be wiped anyway.
        for t in self._cut_tasks:
            t.cancel()
        if self._cut_tasks:
            await asyncio.gather(*self._cut_tasks, return_exceptions=True)
        self._cut_tasks.clear()

        if self._buffer:
            await self._buffer.stop()
            self._shared_buffers.pop(self._config.channel, None)
            self._buffer = None
        if self._recorder is not None:
            # Release our share rather than stopping outright: another user may
            # still be monitoring this channel, and they should not lose their
            # buffer because we stopped watching.
            from src.ingestion import clip_recorder
            try:
                await clip_recorder.release(self._config.channel)
            except Exception as exc:
                log.warning("clip_recorder_release_failed",
                            channel=self._config.channel, error=str(exc))
            self._recorder = None
        if self._engine:
            self._engine.stop()
            self._engine = None
        if self._platform:
            try:
                await self._platform.close()
            except Exception as exc:
                log.warning("platform_close_error", channel=self._config.channel, error=str(exc))

    def stop(self) -> None:
        self._running = False
        # Stop the engine right away so any in-flight post-trigger monitor tasks
        # are cancelled immediately — otherwise a removed stream can still push a
        # clip seconds later (ghost stream).
        if self._engine:
            self._engine.stop()
        for t in self._tasks:
            t.cancel()
