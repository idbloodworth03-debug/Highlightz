"""
Viewer-clip watcher — crowd highlight labels, for LEARNING.

When a viewer clips a moment, a human has decided it mattered. That is the
judgment every other signal in this project is a proxy for, and the 1001 blind
human scores showed those proxies are weak (trigger_score correlates +0.081
with human-judged virality).

**This does not trigger clips, by measurement.** Phase 0 measured how long
Twitch takes to make a viewer's clip visible in Helix:

    stableronaldo, 89 clips over 25 clean minutes:
      median 167.4s   p90 369.3s   max 461.1s   min 10.8s

Acting on a moment ~3 minutes after it happened would capture the aftermath,
not the payoff — Create Clip only reaches back ~60s. The threshold for
"trigger" was set at <45s BEFORE that data was collected, precisely so the
call could not be rationalised afterwards. It failed, so this watcher observes
and records; it never fires.

Latency is irrelevant for learning: we look BACKWARD. The engine keeps ~15
minutes of score history, so a clip surfacing 3 minutes late is still paired
with what we thought at the moment it was made.

Each record answers: "viewers clipped here — what did WE think at the time?"
That yields, for free and at volume:
  * a real accuracy benchmark (did we catch the moments humans cared about?)
  * per-channel threshold evidence grounded in crowd behaviour
  * thousands of labels, versus the 1001 the team hand-scored

Safety: our own clips are real Twitch clips and appear in this same API.
They are excluded by creator_id AND by stored slug — counting them would make
the bot learn from itself, and (had this driven a trigger) loop forever.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_LOG_FILE = Path(settings.local_storage_path) / "viewer_clips.jsonl"

# How often any single channel is polled, regardless of how many users watch
# it. Helix allows 800 points/min across ALL our users, so this must scale with
# CHANNELS, not with subscribers — five users on one channel is one poll.
POLL_INTERVAL = 90.0
# Lookback window per poll. Comfortably wider than POLL_INTERVAL so nothing
# falls between polls, and wide enough to catch the measured p90 lag.
_WINDOW_SECS = 420.0

# How far around a viewer clip's created_at to look for OUR peak score. A
# Twitch clip covers roughly the preceding 30s, and a viewer takes a few more
# seconds to react and press the button — so the moment sits before the
# timestamp, never after it by much. Asymmetric on purpose.
_PEAK_LOOKBACK  = 60.0
_PEAK_LOOKAHEAD = 10.0

# channel -> last poll time, and channel -> slugs already recorded.
_last_poll: dict[str, float] = {}
_seen: dict[str, set[str]] = {}


def _parse_ts(s: str) -> float:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def due(channel: str, now: float | None = None) -> bool:
    """True when this channel is due a poll. Shared across every worker
    watching it, so cost scales with channels rather than with users."""
    now = time.time() if now is None else now
    return now - _last_poll.get(channel, 0.0) >= POLL_INTERVAL


def _record(rows: list[dict]) -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_FILE.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


async def poll_and_record(channel: str, broadcaster_id: str, engine,
                          our_creator_ids: set[str], our_slugs: set[str]) -> int:
    """Poll one channel's recent viewer clips and log each against our score
    at the moment it was made. Returns how many new clips were recorded.

    Never raises: this is observation, and it must not be able to disturb
    clipping. Any failure logs a warning and reports 0.
    """
    now = time.time()
    _last_poll[channel] = now
    seen = _seen.setdefault(channel, set())
    try:
        import aiohttp
        from src.output.twitch_clips import HELIX_BASE, _get_app_token

        fmt = "%Y-%m-%dT%H:%M:%SZ"
        params = {
            "broadcaster_id": broadcaster_id,
            "first": 100,
            # BOTH bounds are required: started_at alone makes Twitch default
            # the window to a WEEK and return the channel's whole history.
            "started_at": datetime.fromtimestamp(now - _WINDOW_SECS, timezone.utc).strftime(fmt),
            "ended_at":   datetime.fromtimestamp(now + 120, timezone.utc).strftime(fmt),
        }
        async with aiohttp.ClientSession() as session:
            token = await _get_app_token(session)
            headers = {"Client-Id": settings.twitch_client_id,
                       "Authorization": f"Bearer {token}"}
            async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                                   params=params) as resp:
                if resp.status != 200:
                    log.warning("viewer_clip_poll_failed", channel=channel,
                                status=resp.status)
                    return 0
                rows = (await resp.json()).get("data", [])
    except Exception as exc:
        log.warning("viewer_clip_poll_error", channel=channel, error=str(exc))
        return 0

    out = []
    for c in rows:
        slug = c.get("id")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        # Never learn from ourselves.
        if str(c.get("creator_id")) in our_creator_ids or slug in our_slugs:
            continue
        ts = _parse_ts(c.get("created_at", ""))
        if not ts:
            continue
        our_score = engine.score_at(ts) if engine else None
        # A viewer clips AFTER the moment lands, so `ts` points at the
        # aftermath. Take our peak across the window the clip plausibly covers
        # — a point sample at `ts` systematically understates what we scored
        # and would make the bot look like it missed moments it caught.
        peak, peak_n = (engine.score_window(ts - _PEAK_LOOKBACK, ts + _PEAK_LOOKAHEAD)
                        if engine else (None, 0))
        out.append({
            "ts":           round(ts, 1),
            "recorded_at":  round(now, 1),
            "lag":          round(now - ts, 1),
            "channel":      channel,
            "clip_id":      slug,
            "creator":      c.get("creator_name") or "",
            "creator_id":   str(c.get("creator_id") or ""),
            "title":        (c.get("title") or "")[:120],
            # The pairing that makes this a label: what WE thought at that
            # moment. None means the moment fell outside our score history
            # (stream started later, or we were not watching).
            "our_score":    our_score,
            # What we peaked at across the clipped window — the number that
            # actually answers "did we see this moment at all?".
            "our_peak":     peak,
            "peak_n":       peak_n,
            "peak_window":  [_PEAK_LOOKBACK, _PEAK_LOOKAHEAD],
            "threshold":    round(engine.profile.trigger_threshold, 1)
                            if engine and engine.profile else None,
        })

    # Keep the per-channel seen-set from growing without bound over a long
    # stream; anything older than the window cannot come back.
    if len(seen) > 5000:
        seen.clear()

    if out:
        _record(out)
        matched = sum(1 for r in out if r["our_score"] is not None)
        log.info("viewer_clips_recorded", channel=channel, new=len(out),
                 with_score=matched,
                 median_lag=round(sorted(r["lag"] for r in out)[len(out) // 2], 1))
    return len(out)
