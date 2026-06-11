"""
Clip processor: picks up a clip job and creates a clip on Twitch via the
Helix Clips API using the requesting user's OAuth token. The clip is hosted by
Twitch and attributed to the user — Highlightz never records or stores video.
"""

import structlog

from src.queue.job_queue import ClipJob
from src.auth import users as user_store
from src.output import twitch_clips
from .metadata import ClipMetadata

log = structlog.get_logger(__name__)


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

        if job.platform != "twitch":
            raise RuntimeError(f"Clip creation only supports Twitch (got '{job.platform}')")

        token = await user_store.get_valid_twitch_token(job.user_id)
        if not token:
            raise RuntimeError(f"No valid Twitch token for user '{job.user_id}' — re-login required")

        broadcaster_id = self._broadcaster_cache.get(channel)
        if not broadcaster_id:
            broadcaster_id = await twitch_clips.resolve_broadcaster_id(channel)
            if not broadcaster_id:
                raise RuntimeError(f"Could not resolve broadcaster id for '{channel}'")
            self._broadcaster_cache[channel] = broadcaster_id

        log.info("creating_twitch_clip", clip_id=meta.id, channel=channel,
                 broadcaster_id=broadcaster_id, user_id=job.user_id)

        slug = await twitch_clips.create_clip(token, broadcaster_id)
        if not slug:
            raise RuntimeError(f"Twitch clip creation failed for '{channel}'")

        clip = await twitch_clips.get_clip(slug)
        if clip:
            meta.twitch_clip_id   = clip.get("id", slug)
            meta.twitch_url       = clip.get("url", f"https://clips.twitch.tv/{slug}")
            meta.embed_url        = clip.get("embed_url", f"https://clips.twitch.tv/embed?clip={slug}")
            meta.thumbnail_url    = clip.get("thumbnail_url", "")
            meta.duration_seconds = float(clip.get("duration", 0.0) or 0.0)
            if clip.get("title"):
                meta.clip_title = meta.clip_title or clip["title"]
        else:
            # Clip was requested but not yet queryable — store the slug so links work.
            meta.twitch_clip_id = slug
            meta.twitch_url     = f"https://clips.twitch.tv/{slug}"
            meta.embed_url      = f"https://clips.twitch.tv/embed?clip={slug}"

        meta.status = "pending"
        log.info("twitch_clip_ready", clip_id=meta.id, slug=slug, url=meta.twitch_url)
        return meta
