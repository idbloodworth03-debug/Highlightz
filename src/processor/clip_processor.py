"""
Clip processor: picks up a clip job and creates a clip on Twitch or Kick via
their respective APIs using the requesting user's OAuth token. The clip is
hosted by the platform and attributed to the user — Highlightz never records
or stores video.
"""

import asyncio
import structlog

from src.queue.job_queue import ClipJob
from src.auth import users as user_store
from src.output import twitch_clips
from .metadata import ClipMetadata

log = structlog.get_logger(__name__)


class TwitchAuthExpiredError(RuntimeError):
    """The user's Twitch authorisation is gone and cannot be refreshed.

    Distinct from a generic failure because the CALLER has to behave
    differently: this never succeeds until the user signs in again, so retrying
    burns the moment and the "it will try again" message is untrue. Mirrors
    ClipNotAuthorizedError, which exists for the same reason.
    """


class ClipProcessor:
    def __init__(self, storage=None, buffers=None) -> None:
        # storage/buffers kept for call-site compatibility; no longer used.
        self._broadcaster_cache: dict[str, str] = {}

    async def process(self, job: ClipJob) -> ClipMetadata:
        channel = job.channel

        meta = ClipMetadata(
            id=job.clip_id,
            channel=channel,
            platform=job.platform,
            trigger_score=job.trigger_score,
            trigger_signals=job.trigger_signals,
            chat_snapshot=job.chat_snapshot,
            stream_title=job.stream_title,
            game=job.game,
            virality_score=job.virality_score,
            clip_title=job.clip_title,
            user_id=job.user_id,
        )

        if job.platform == "kick":
            return await self._process_kick(job, meta, channel)

        if job.platform != "twitch":
            raise RuntimeError(f"Clip creation only supports Twitch and Kick (got '{job.platform}')")

        return await self._process_twitch(job, meta, channel)

    async def _process_twitch(self, job: ClipJob, meta: ClipMetadata, channel: str) -> ClipMetadata:
        token = await user_store.get_valid_twitch_token(job.user_id)
        if not token:
            raise TwitchAuthExpiredError(
                f"No valid Twitch token for user '{job.user_id}' — re-login required")

        broadcaster_id = self._broadcaster_cache.get(channel)
        if not broadcaster_id:
            broadcaster_id = await twitch_clips.resolve_broadcaster_id(channel)
            if not broadcaster_id:
                raise RuntimeError(f"Could not resolve broadcaster id for '{channel}'")
            if len(self._broadcaster_cache) >= 1000:
                oldest = next(iter(self._broadcaster_cache))
                del self._broadcaster_cache[oldest]
            self._broadcaster_cache[channel] = broadcaster_id

        log.info("creating_twitch_clip", clip_id=meta.id, channel=channel,
                 broadcaster_id=broadcaster_id, user_id=job.user_id)

        # Wait for post_roll seconds before requesting the clip.
        # Twitch's clip API captures roughly the last 60s of broadcast at call time.
        # The trigger engine's monitoring task already waited for excitement to dull,
        # so post_roll here is typically a small tail (≤5 s). Cap at 55 s to leave
        # a 5 s pre-trigger window in the 60 s capture buffer.
        if job.post_roll > 0:
            wait = min(job.post_roll, 55)
            if job.post_roll > 55:
                log.warning("post_roll_capped", requested=job.post_roll, actual=wait)
            await asyncio.sleep(wait)

        # ClipNotAuthorizedError deliberately propagates rather than being
        # folded into the generic failure below: the caller must stop the
        # stream instead of retrying, because every future attempt gets the
        # same permanent 403.
        slug = await twitch_clips.create_clip(token, broadcaster_id)
        if not slug:
            raise RuntimeError(f"Twitch clip creation failed for '{channel}'")

        clip = await twitch_clips.get_clip(slug)
        if not clip:
            # Twitch accepted the clip request (202) but the clip never became
            # queryable — the capture failed (broadcast buffer too short, VODs
            # disabled, etc.). Fabricating a clips.twitch.tv/{slug} link here just
            # produces a "Clip is no longer available" page, so fail the job
            # instead and let a future trigger try again.
            raise RuntimeError(
                f"Twitch clip '{slug}' for '{channel}' never finished processing — "
                "capture failed (broadcast too short or VODs disabled on the channel)"
            )

        meta.twitch_clip_id   = clip.get("id", slug)
        meta.twitch_url       = clip.get("url", f"https://clips.twitch.tv/{slug}")
        meta.embed_url        = clip.get("embed_url", f"https://clips.twitch.tv/embed?clip={slug}")
        meta.thumbnail_url    = clip.get("thumbnail_url", "")
        meta.duration_seconds = float(clip.get("duration", 0.0) or 0.0)
        if clip.get("title"):
            meta.clip_title = meta.clip_title or clip["title"]

        meta.status = "pending"
        log.info("twitch_clip_ready", clip_id=meta.id, slug=slug, url=meta.twitch_url)
        return meta

    async def _process_kick(self, job: ClipJob, meta: ClipMetadata, channel: str) -> ClipMetadata:
        from src.output import kick_clips
        from src.output.kick_clips import KickScopeError

        token = await user_store.get_kick_token(job.user_id)
        if not token:
            raise RuntimeError(f"No valid Kick token for user '{job.user_id}' — re-link Kick account")

        db_user = user_store.get_by_id(job.user_id)
        kick_slug = db_user.get("kick_slug", channel) if db_user else channel

        log.info("creating_kick_clip", clip_id=meta.id, channel=channel,
                 kick_slug=kick_slug, user_id=job.user_id)

        try:
            clip_url = await kick_clips.create_clip(token, kick_slug)
        except KickScopeError as exc:
            # Propagate with a user-friendly message so the dashboard can surface it
            raise RuntimeError(
                f"Kick clipping requires re-linking your Kick account to grant "
                f"clip permissions. Go to Settings → Kick and re-link."
            ) from exc

        if not clip_url:
            raise RuntimeError(f"Kick clip creation failed for '{channel}'")

        meta.twitch_url = clip_url  # reuse field for clip URL
        meta.status = "pending"
        log.info("kick_clip_ready", clip_id=meta.id, url=clip_url)
        return meta
