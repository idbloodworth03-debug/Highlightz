"""
Streamer research — pre-flight calibration for channels we've never watched.

Fetches the channel's recent Twitch clips (last 90 days, up to 200) and
derives a suggested trigger threshold, an estimated chat velocity, and a
view-count percentile distribution. All values are used to seed the profile
before the first live session so calibration starts from an informed baseline
rather than cold defaults.

This runs once per new channel; live sampling (chat velocity, audio, approve/
reject feedback) takes over from the first session onward.
"""

import datetime
import statistics
import structlog

from src.output.twitch_clips import resolve_broadcaster_id, get_recent_clips

log = structlog.get_logger(__name__)

_RESEARCH_DAYS = 90    # wider window → more clips → better CPD estimate
_RESEARCH_LIMIT = 200  # doubled from 100

# Threshold seeding based on clips-per-day (CPD):
#   0.5 CPD (quiet, rare clips)   → ~55  (sensitive)
#   6   CPD (active channel)      → ~62
#   20+ CPD (clip-factory)        → 72  (conservative — genuine moments are normal)
_SEED_BASE      = 55.0
_SEED_PER_CPD   = 0.85
_SEED_MIN       = 53.0
_SEED_MAX       = 72.0
_CPD_CAP        = 20.0

# Velocity estimate: clip-dense channels have faster chat.
# Rough calibration: 1 CPD ≈ 0.3 msg/s avg; 20 CPD ≈ 4.0 msg/s avg.
# This seeds avg_velocity so the spike-ratio baseline isn't zero on day 1.
_VEL_PER_CPD    = 0.18   # msg/s per clip-per-day
_VEL_MIN        = 0.1
_VEL_MAX        = 5.0


async def research_channel(channel: str, days: int = _RESEARCH_DAYS) -> dict | None:
    """Analyse a channel's pre-existing Twitch clips.

    Returns a dict with:
        clip_count, clips_per_day, median_views, p75_views, p90_views,
        suggested_threshold, estimated_velocity
    or None if the channel can't be resolved or has no clips."""
    broadcaster_id = await resolve_broadcaster_id(channel)
    if not broadcaster_id:
        return None

    clips = await get_recent_clips(broadcaster_id, days=days, limit=_RESEARCH_LIMIT)
    if not clips:
        return None

    count = len(clips)

    # If we hit the cap the window is denser than `days` — measure the real span.
    span_days = float(days)
    if count >= _RESEARCH_LIMIT:
        try:
            stamps = [
                datetime.datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
                for c in clips if c.get("created_at")
            ]
            if stamps:
                oldest = min(stamps)
                now    = datetime.datetime.now(datetime.timezone.utc)
                span_days = max((now - oldest).total_seconds() / 86400.0, 1.0)
        except (ValueError, KeyError):
            pass

    clips_per_day = count / span_days

    views = sorted(c.get("view_count", 0) for c in clips)
    median_views = views[len(views) // 2]
    p75_views    = views[int(len(views) * 0.75)]
    p90_views    = views[int(len(views) * 0.90)]

    # Duration distribution helps distinguish short highlight-clips from
    # longer stream segments — long avg durations suggest manual clipping.
    durations = [c.get("duration", 30) for c in clips]
    avg_duration = statistics.mean(durations) if durations else 30.0

    suggested = _SEED_BASE + min(clips_per_day, _CPD_CAP) * _SEED_PER_CPD
    suggested = round(min(max(suggested, _SEED_MIN), _SEED_MAX), 1)

    # Seed the chat velocity baseline so the spike detector has a reasonable
    # reference on the very first live evaluation rather than starting at 0.
    estimated_velocity = round(
        min(max(_VEL_MIN, clips_per_day * _VEL_PER_CPD), _VEL_MAX), 3
    )

    result = {
        "clip_count":           count,
        "clips_per_day":        round(clips_per_day, 2),
        "span_days":            round(span_days, 1),
        "median_views":         median_views,
        "p75_views":            p75_views,
        "p90_views":            p90_views,
        "avg_clip_duration":    round(avg_duration, 1),
        "suggested_threshold":  suggested,
        "estimated_velocity":   estimated_velocity,
    }
    log.info("streamer_research_complete", channel=channel, **result)
    return result
