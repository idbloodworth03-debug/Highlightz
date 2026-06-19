"""
Twitch VOD analyzer: fetches VOD chat, replays it through a time-aware
scoring pass, and surfaces highlight moments as VOD timestamp links.

Chat is fetched via the Twitch v5 /videos/{id}/comments endpoint (still
functional). Moments are scored using a sliding-window variant of the same
chat-velocity + keyword + sentiment logic as the live trigger engine, but
anchored to VOD-relative timestamps rather than wall-clock time so a 3-hour
VOD scans in seconds.
"""

import asyncio
import re as _re
import time
import uuid
import structlog
from collections import defaultdict, deque

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from config.settings import settings
from src.chat.metrics import HIGH_ENERGY_KEYWORDS, CLIP_TRIGGER_PHRASES
from src.trigger.rules import get_rules

log = structlog.get_logger(__name__)

_DURATION_RE = _re.compile(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?')
_VOD_URL_RE  = _re.compile(r'(?:twitch\.tv/videos/|^)(\d{6,})')


def parse_vod_id(url_or_id: str) -> str | None:
    """Extract numeric VOD id from a URL or bare id string."""
    m = _VOD_URL_RE.search(url_or_id.strip())
    return m.group(1) if m else None


def _parse_duration(s: str) -> float:
    m = _DURATION_RE.fullmatch(s or "")
    if not m:
        return 0.0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def _fmt_offset(offset: float) -> str:
    offset = max(0, int(offset))
    h, rem = divmod(offset, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


async def _get_app_token() -> str:
    import aiohttp
    from src.output.twitch_clips import _get_app_token as _tok
    async with aiohttp.ClientSession() as s:
        return await _tok(s)


async def fetch_vod_info(vod_id: str, token: str) -> dict:
    """Fetch VOD metadata via Helix. Returns {} if not found."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        hdrs = {
            "Client-ID":     settings.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }
        async with session.get(
            "https://api.twitch.tv/helix/videos",
            headers=hdrs, params={"id": vod_id},
        ) as resp:
            if resp.status != 200:
                return {}
            data = (await resp.json()).get("data", [])
    if not data:
        return {}
    v     = data[0]
    thumb = v.get("thumbnail_url", "").replace("%{width}", "1280").replace("%{height}", "720")
    return {
        "id":            v["id"],
        "title":         v.get("title", ""),
        "channel":       v.get("user_login", ""),
        "game":          v.get("game_name", ""),
        "duration":      _parse_duration(v.get("duration", "0s")),
        "thumbnail_url": thumb,
        "url":           f"https://www.twitch.tv/videos/{vod_id}",
    }


async def fetch_vod_chat(vod_id: str, token: str) -> list[dict]:
    """
    Fetch all VOD chat via Twitch's v5 comments endpoint.
    Returns list sorted by offset_seconds ascending.
    """
    import aiohttp
    messages: list[dict] = []
    cursor = None
    async with aiohttp.ClientSession() as session:
        hdrs = {
            "Client-ID":     settings.twitch_client_id,
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.twitchtv.v5+json",
        }
        while True:
            params: dict = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            else:
                params["content_offset_seconds"] = 0
            async with session.get(
                f"https://api.twitch.tv/v5/videos/{vod_id}/comments",
                headers=hdrs, params=params,
            ) as resp:
                if resp.status != 200:
                    log.warning("vod_chat_fetch_failed", vod_id=vod_id, status=resp.status)
                    break
                data = await resp.json()
            for c in data.get("comments", []):
                offset = float(c.get("content_offset_seconds", 0))
                text   = c.get("message", {}).get("body", "")
                author = c.get("commenter", {}).get("display_name", "")
                if text:
                    messages.append({"offset": offset, "text": text, "author": author})
            cursor = data.get("_next")
            if not cursor:
                break
            await asyncio.sleep(0.02)
    return sorted(messages, key=lambda m: m["offset"])


_vader = SentimentIntensityAnalyzer()


def _score_window(
    window_texts: list[str],
    lt_count: int,
    lt_secs: float,
    window_secs: float,
    rules,
) -> tuple[float, dict]:
    """
    Score a sliding window of chat messages relative to a long-term baseline.
    Returns (score 0-100, breakdown dict).
    """
    cur_vel   = len(window_texts) / max(window_secs, 1.0)
    lt_vel    = lt_count / max(lt_secs, 1.0)
    spike     = cur_vel / max(lt_vel, 0.01)
    vel_score = min(spike / rules.velocity_multiplier, 1.0)

    kw_hits      = sum(1 for m in window_texts
                       if bool(set(_re.findall(r"\w+", m.lower())) & HIGH_ENERGY_KEYWORDS))
    trigger_hits = sum(1 for m in window_texts if CLIP_TRIGGER_PHRASES.search(m))
    kw_rate      = kw_hits / max(len(window_texts), 1)
    kw_score     = min(kw_rate * 3 + min(trigger_hits * 0.15, 0.3), 1.0)

    if window_texts:
        recent   = window_texts[-20:]
        compounds = [abs(_vader.polarity_scores(m)["compound"]) for m in recent]
        sent_score = min((sum(compounds) / len(compounds)) * 2.0, 1.0)
    else:
        sent_score = 0.0

    raw = vel_score * 30 + kw_score * 15 + sent_score * 8
    active = sum(1 for v in (vel_score, kw_score, sent_score) if v > 0.25)
    if active >= 2:
        raw *= 1.25
    score = min(raw, 100)

    return score, {
        "CHAT_VELOCITY": round(vel_score, 3),
        "KEYWORD":       round(kw_score, 3),
        "SENTIMENT":     round(sent_score, 3),
    }


async def run_vod_analysis(
    vod_id: str,
    channel: str,
    preset: str,
    user_id: str,
    on_progress,
    on_moment,
    on_done,
    on_error,
) -> None:
    """
    Full VOD analysis pipeline.
    Calls async callbacks as it progresses; safe to cancel.
    """
    try:
        token = await _get_app_token()
        info  = await fetch_vod_info(vod_id, token)
        if not info:
            await on_error(f"VOD {vod_id} not found or is private")
            return

        chan      = channel or info["channel"]
        duration  = info["duration"] or 0.0
        thumb     = info["thumbnail_url"]
        game      = info["game"]
        vod_title = info["title"]

        await on_progress(0.0, {
            "vod_title": vod_title, "channel": chan,
            "duration": duration, "game": game, "thumbnail_url": thumb,
        })

        messages = await fetch_vod_chat(vod_id, token)
        if not messages:
            await on_done([])
            return

        rules     = get_rules(chan, preset)
        threshold = rules.trigger_threshold

        WINDOW   = 15.0    # scoring window in seconds
        LT_WIN   = 300.0   # long-term baseline window
        COOLDOWN = 60.0    # min gap between moments
        STEP     = 1.0     # evaluation granularity

        duration = max(duration, messages[-1]["offset"] + 30)

        # Index messages into 1-second buckets for O(1) lookup
        msg_by_sec: dict[int, list[str]] = defaultdict(list)
        for m in messages:
            msg_by_sec[int(m["offset"])].append(m["text"])

        recent_deq: deque = deque()   # (offset, text) within WINDOW
        lt_deq:     deque = deque()   # (offset, text) within LT_WIN

        last_moment_offset = -9999.0
        moments: list[dict] = []
        last_pct = -1.0
        offset   = 0.0

        while offset <= duration:
            sec = int(offset)
            for text in msg_by_sec.get(sec, []):
                recent_deq.append((offset, text))
                lt_deq.append((offset, text))

            # Prune sliding windows
            while recent_deq and recent_deq[0][0] < offset - WINDOW:
                recent_deq.popleft()
            while lt_deq and lt_deq[0][0] < offset - LT_WIN:
                lt_deq.popleft()

            if offset - last_moment_offset >= COOLDOWN and offset >= WINDOW:
                window_texts = [t for _, t in recent_deq]
                score, breakdown = _score_window(
                    window_texts, len(lt_deq), LT_WIN, WINDOW, rules)

                if score >= threshold:
                    last_moment_offset = offset
                    ts = _fmt_offset(offset)
                    sig_list = [{"type": k, "value": v} for k, v in breakdown.items()]
                    moment = {
                        "id":              str(uuid.uuid4()),
                        "offset_seconds":  offset,
                        "timestamp":       ts,
                        "score":           round(score, 1),
                        "trigger_score":   round(score / 100, 3),
                        "trigger_signals": sig_list,
                        "clip_title":      f"{chan} — {ts}",
                        "stream_title":    vod_title,
                        "virality_score":  round(score * 0.7, 1),
                        "twitch_url":      f"https://www.twitch.tv/videos/{vod_id}?t={ts}",
                        "embed_url":       f"https://player.twitch.tv/?video={vod_id}&t={ts}",
                        "thumbnail_url":   thumb,
                        "channel":         chan,
                        "game":            game,
                        "platform":        "twitch",
                        "vod_id":          vod_id,
                        "vod_title":       vod_title,
                        "status":          "pending",
                        "created_at":      time.time(),
                        "user_id":         user_id,
                        "is_vod_moment":   True,
                    }
                    moments.append(moment)
                    await on_moment(moment)

            pct = min(100.0, (offset / duration) * 100) if duration > 0 else 100.0
            if pct - last_pct >= 5.0:
                last_pct = pct
                await on_progress(pct, {})
                await asyncio.sleep(0)  # yield event loop

            offset += STEP

        await on_done(moments)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("vod_analysis_error", vod_id=vod_id, error=str(exc))
        await on_error(str(exc))
