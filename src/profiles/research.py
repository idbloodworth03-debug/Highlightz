"""
Streamer research — pre-flight calibration for channels we've never watched.

Before the first monitoring session for a channel, we look at the clips that
already exist on Twitch (last 30 days). A channel that produces clips
constantly is a high-energy channel where hype is routine, so we seed a
slightly higher trigger threshold; a channel with few clips gets a more
sensitive one. If the channel has no clips at all we leave the defaults —
nothing to learn from.

This is a one-time seed only: live learning (chat velocity, audio baseline,
approve/reject feedback) takes over from the first session onward.
"""

import datetime
import structlog

from src.output.twitch_clips import resolve_broadcaster_id, get_recent_clips

log = structlog.get_logger(__name__)

_RESEARCH_DAYS = 30

# Threshold seeding: 52 + clips_per_day * 0.75, clamped to [50, 70].
#   0.5 clips/day (quiet channel)   → ~52.4 (sensitive)
#   8   clips/day (active channel)  → 58
#   24+ clips/day (constant hype)   → 70 (conservative)
_SEED_BASE      = 52.0
_SEED_PER_CPD   = 0.75
_SEED_MIN       = 50.0
_SEED_MAX       = 70.0
_CPD_CAP        = 24.0


async def research_channel(channel: str, days: int = _RESEARCH_DAYS) -> dict | None:
    """Analyse a channel's pre-existing Twitch clips.

    Returns {"clip_count", "clips_per_day", "median_views", "suggested_threshold"}
    or None if the channel can't be resolved or has no clips (caller should
    disregard and keep defaults)."""
    broadcaster_id = await resolve_broadcaster_id(channel)
    if not broadcaster_id:
        return None

    clips = await get_recent_clips(broadcaster_id, days=days, limit=100)
    if not clips:
        return None

    count = len(clips)

    # If we hit the 100-clip API cap, the window is denser than `days`:
    # measure the actual span the API returned instead.
    span_days = float(days)
    if count >= 100:
        try:
            stamps = [
                datetime.datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
                for c in clips if c.get("created_at")
            ]
            if stamps:
                oldest = min(stamps)
                now = datetime.datetime.now(datetime.timezone.utc)
                span_days = max((now - oldest).total_seconds() / 86400.0, 1.0)
        except (ValueError, KeyError):
            pass

    clips_per_day = count / span_days

    views = sorted(c.get("view_count", 0) for c in clips)
    median_views = views[len(views) // 2]

    suggested = _SEED_BASE + min(clips_per_day, _CPD_CAP) * _SEED_PER_CPD
    suggested = round(min(max(suggested, _SEED_MIN), _SEED_MAX), 1)

    result = {
        "clip_count":          count,
        "clips_per_day":       round(clips_per_day, 2),
        "median_views":        median_views,
        "suggested_threshold": suggested,
    }
    log.info("streamer_research_complete", channel=channel, **result)
    return result
