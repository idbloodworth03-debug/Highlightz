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
from src.chat.metrics import (
    HIGH_ENERGY_KEYWORDS, CLIP_TRIGGER_PHRASES,
    emote_weight, emote_homogeneity_of,
)
from src.trigger.rules import get_rules
from src.trigger import scoring

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


_GQL_URL        = "https://gql.twitch.tv/gql"
# Twitch's web-app client ID — used by every public Twitch VOD tool (yt-dlp,
# streamlink, TwitchDownloaderCLI) since there's no official Helix endpoint
# for VOD chat replay.
_GQL_CLIENT_ID  = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_GQL_HASH       = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


async def fetch_vod_chat(
    vod_id: str,
    duration: float = 0.0,
    on_progress=None,
) -> list[dict]:
    """
    Fetch all VOD chat via Twitch GQL, paginating by contentOffsetSeconds.
    Cursor-based pagination is unreliable with this persisted query (the cursor
    field is not returned in edges and pageInfo has no endCursor), so we advance
    by re-requesting from the last message's timestamp and deduplicating.
    on_progress(pct, data) is called with 5–50% during fetch if provided.
    """
    import aiohttp
    messages: list[dict] = []
    seen:  set[tuple]    = set()   # (int_offset, text_prefix) dedup keys
    next_offset          = 0
    page                 = 0
    hdrs = {
        "Client-ID":    _GQL_CLIENT_ID,
        "Content-Type": "application/json",
    }

    stuck_since: int | None = None  # offset where new_count first hit 0
    last_reported_pct = 5.0

    async with aiohttp.ClientSession() as session:
        while True:
            variables = {"videoID": vod_id, "contentOffsetSeconds": int(next_offset)}
            payload = [{
                "operationName": "VideoCommentsByOffsetOrCursor",
                "variables":     variables,
                "extensions":    {
                    "persistedQuery": {
                        "version":    1,
                        "sha256Hash": _GQL_HASH,
                    }
                },
            }]

            async with session.post(_GQL_URL, headers=hdrs, json=payload) as resp:
                status = resp.status
                if status != 200:
                    raw = (await resp.text())[:300]
                    log.warning("vod_gql_failed", vod_id=vod_id, status=status,
                                page=page, body=raw)
                    break
                body = await resp.json()

            try:
                comments_data = body[0]["data"]["video"]["comments"] or {}
            except (IndexError, KeyError, TypeError) as exc:
                log.warning("vod_gql_parse_error", vod_id=vod_id, error=str(exc),
                            body=str(body)[:400])
                break

            edges = comments_data.get("edges") or []
            if not edges:
                # Genuinely no more chat at or after this offset
                break

            new_count   = 0
            last_offset = next_offset
            for edge in edges:
                node   = edge.get("node") or {}
                offset = float(node.get("contentOffsetSeconds", 0))
                author = (node.get("commenter") or {}).get("displayName", "")
                frags  = (node.get("message") or {}).get("fragments") or []
                text   = "".join(f.get("text", "") for f in frags).strip()
                if not text:
                    continue
                key = (int(offset), text[:40])
                if key not in seen:
                    seen.add(key)
                    messages.append({"offset": offset, "text": text, "author": author})
                    new_count += 1
                if offset > last_offset:
                    last_offset = offset

            log.info("vod_gql_page", vod_id=vod_id, page=page,
                     edges=len(edges), new=new_count,
                     from_offset=next_offset, last_offset=last_offset,
                     total=len(messages))

            if new_count > 0:
                # Got new messages — always advance past this second
                stuck_since = None
                next_offset = int(last_offset) + 1
            else:
                # All messages at this offset already seen — step forward 1s.
                # Track how long we've gone with zero new messages so we can
                # stop at a genuine end-of-chat rather than looping forever.
                if stuck_since is None:
                    stuck_since = int(next_offset)
                elif int(next_offset) - stuck_since >= 300:
                    log.info("vod_gql_chat_ended", vod_id=vod_id,
                             offset=next_offset, stuck_since=stuck_since)
                    break
                next_offset = int(next_offset) + 1

            page += 1
            if page % 50 == 0:
                log.info("vod_chat_progress", vod_id=vod_id, page=page,
                         messages=len(messages))

            # Send fetch-phase progress: 5% → 50% proportional to VOD time covered
            if on_progress and duration > 0:
                fetch_pct = 5.0 + 45.0 * min(next_offset / duration, 1.0)
                if fetch_pct - last_reported_pct >= 3.0:
                    last_reported_pct = fetch_pct
                    await on_progress(fetch_pct, {"phase": "fetch",
                                                  "messages": len(messages)})

            await asyncio.sleep(0.05)

    log.info("vod_chat_fetched", vod_id=vod_id, pages=page + 1,
             total_messages=len(messages))
    return sorted(messages, key=lambda m: m["offset"])


_vader = SentimentIntensityAnalyzer()


async def _load_profile(user_id: str, channel: str):
    """The requesting user's learned profile for this channel — the same
    threshold/spike calibration the live engine uses. None on any failure so a
    VOD scan can never break on profile I/O."""
    try:
        from src.profiles.manager import get_profile_manager
        pm = get_profile_manager(user_id)
        return await pm.load(channel)
    except Exception:
        return None


def _vod_threshold(profile, rules) -> tuple[float, float]:
    """(score threshold, spike multiplier) for a VOD scan.

    Uses the channel's LEARNED values when a profile exists — the user's
    approve/reject history moves profile.trigger_threshold (e.g. a heavily-
    rejected channel sits at 80), and calibration derives an adaptive spike
    multiplier from the channel's own variance. The preset is only the
    cold-start fallback. The ×0.50 scale maps the full-signal threshold
    (chat=48 + audio=38 + viewer=7 + silence=12 ≈ 105 ceiling) onto VOD's
    chat-only ceiling (~57 with the multi-signal bonus)."""
    base = profile.trigger_threshold if profile is not None else rules.trigger_threshold
    spike_mult = (
        profile.velocity_spike_multiplier
        if profile is not None and profile.velocity_samples >= 30
        else rules.velocity_multiplier
    )
    return base * 0.50, spike_mult


def _top_moments(moments: list[dict], duration_secs: float) -> list[dict]:
    """Keep only the best moments of a scan: rank by score and cap at
    ~3/hour (min 3, max 12), returned in chronological order. Emitting every
    above-threshold run buries the good moments under mediocre ones — a
    3-hour VOD should surface its highlights, not its entire pulse."""
    cap = int(min(12, max(3, (duration_secs / 3600.0) * 3)))
    if len(moments) <= cap:
        return moments
    best = sorted(moments, key=lambda m: m["score"], reverse=True)[:cap]
    return sorted(best, key=lambda m: m["offset_seconds"])


def _score_window(
    window: list[dict],
    lt_count: int,
    lt_secs: float,
    window_secs: float,
    clip_it_senders: int,
    rules,
    acceleration: float = 1.0,
    spike_multiplier: float | None = None,
) -> tuple[float, dict]:
    """
    Score a sliding window of chat messages relative to a long-term baseline,
    using the exact same chat-signal formula as the live trigger engine
    (src/trigger/scoring.py): emote-weighted velocity, unique-sender diversity,
    emote homogeneity (crowdspeak), keyword density, and sentiment — including
    the v2 velocity boosts (chat acceleration + message-length collapse) so this
    path stays in lockstep with the live engine. `acceleration` is the recent-vs-
    older-half rate ratio for this window (mirrors ChatMetrics.velocity_acceleration);
    avg message length is derived from the window here.

    `window` is a list of {"text", "author"} dicts. Audio / viewer / silence
    signals don't exist for VOD replay, so only the chat signals contribute and
    the caller scales the threshold to compensate for the missing headroom.
    Returns (score 0-100, breakdown dict).
    """
    texts = [m["text"] for m in window]
    msg_count = len(texts)

    cur_vel = msg_count / max(window_secs, 1.0)
    lt_vel  = lt_count / max(lt_secs, 1.0)
    spike   = cur_vel / max(lt_vel, 0.01)

    weighted_vel   = sum(emote_weight(t) for t in texts) / max(window_secs, 1.0)
    unique_senders = len({m["author"] for m in window if m["author"]})

    # Message-length collapse: short, frantic bursts during real hype. Mirrors
    # ChatMetrics.avg_message_length (None when empty → no length boost).
    avg_len = (sum(len(t) for t in texts) / len(texts)) if texts else None

    vel_score = scoring.velocity_score(
        spike, spike_multiplier if spike_multiplier is not None else rules.velocity_multiplier,
        cur_vel, weighted_vel, unique_senders,
        acceleration=acceleration,
        avg_message_length=avg_len)
    homo_score = scoring.emote_homogeneity_score(emote_homogeneity_of(texts))

    kw_hits      = sum(1 for t in texts
                       if bool(set(_re.findall(r"\w+", t.lower())) & HIGH_ENERGY_KEYWORDS))
    trigger_hits = sum(1 for t in texts if CLIP_TRIGGER_PHRASES.search(t))
    kw_score     = scoring.keyword_score(kw_hits, msg_count, trigger_hits)

    if texts:
        recent    = texts[-20:]
        compounds = [abs(_vader.polarity_scores(t)["compound"]) for t in recent]
        sent_score = scoring.sentiment_score(sum(compounds) / len(compounds))
    else:
        sent_score = 0.0

    W = scoring.CHAT_WEIGHTS
    raw = (
        vel_score  * W["CHAT_VELOCITY"] +
        kw_score   * W["KEYWORD"] +
        sent_score * W["SENTIMENT"] +
        homo_score * W["EMOTE_HOMOGENEITY"]
    )
    active = sum(1 for v in (vel_score, kw_score, sent_score, homo_score) if v > 0.5)
    if active >= scoring.MULTI_SIGNAL_MIN_ACTIVE:
        raw *= scoring.MULTI_SIGNAL_BONUS
    score = min(raw, 100)

    # Clip-it community trip-wire — same as live: enough unique users explicitly
    # asking for a clip forces the score to the floor.
    if clip_it_senders >= scoring.CLIP_IT_MIN_SENDERS:
        score = max(score, scoring.CLIP_IT_FLOOR)

    return score, {
        "CHAT_VELOCITY":     round(vel_score, 3),
        "KEYWORD":           round(kw_score, 3),
        "SENTIMENT":         round(sent_score, 3),
        "EMOTE_HOMOGENEITY": round(homo_score, 3),
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

        await on_progress(5.0, {"phase": "fetch", "messages": 0})
        messages = await fetch_vod_chat(vod_id, duration=duration,
                                        on_progress=on_progress)
        if not messages:
            await on_error(
                f"No chat messages found for VOD {vod_id}. "
                "The VOD may be subscriber-only, deleted, or too old."
            )
            return

        rules = get_rules(chan, preset)
        # Score with the channel's LEARNED calibration (same as live): the
        # user's approve/reject history sets the threshold, and the profile's
        # variance sets the spike multiplier. Preset values are cold-start only.
        profile = await _load_profile(user_id, chan)
        threshold, spike_mult = _vod_threshold(profile, rules)

        WINDOW    = 15.0    # scoring window in seconds
        LT_WIN    = 300.0   # long-term baseline window
        COOLDOWN  = 90.0    # min gap between moments (was 60s — raised to reduce
                            # back-to-back clips from the same extended highlight)
        STEP      = 1.0     # evaluation granularity
        CLIP_IT_W = 10.0    # clip-it trip-wire window (matches live _CLIP_IT_WINDOW)
        CHAT_LAG  = 2.0     # chat trails the on-screen event by ~2s (live lag correction)

        total_msgs = len(messages)
        duration   = max(duration, messages[-1]["offset"] + 30)

        log.info("vod_analysis_scanning", vod_id=vod_id, messages=total_msgs,
                 duration_s=int(duration), threshold=round(threshold, 1),
                 spike_multiplier=round(spike_mult, 2),
                 calibration="profile" if profile is not None else "preset")

        # Index messages into 1-second buckets for O(1) lookup. Each entry keeps the
        # author so weighted velocity, unique-sender diversity and the clip-it
        # trip-wire can be computed exactly like the live path.
        msg_by_sec: dict[int, list[dict]] = defaultdict(list)
        for m in messages:
            msg_by_sec[int(m["offset"])].append(m)

        recent_deq:  deque = deque()   # (offset, msg) within WINDOW
        lt_deq:      deque = deque()   # (offset, msg) within LT_WIN
        clip_it_deq: deque = deque()   # (offset, author) of clip-it requests within CLIP_IT_W

        last_moment_offset = -9999.0
        moments: list[dict] = []
        last_pct     = -1.0
        peak_score   = 0.0   # track for diagnostics
        offset       = 0.0
        score_timeline: dict[int, float] = {}   # second → score for end-offset detection
        trigger_offsets: dict[str, int]  = {}   # moment_id → raw trigger offset (pre CHAT_LAG)

        # Run-tracking: a "moment" is one contiguous stretch where score stays at
        # or above threshold, emitted once at the stretch's peak. Without this a
        # long sustained-hype segment fires a near-identical clip every COOLDOWN
        # seconds (e.g. a 15-min run → 15 dupes with the same VOD thumbnail).
        in_run         = False
        run_peak_score = 0.0
        run_peak_off   = 0.0
        run_peak_bd: dict = {}

        async def _emit_moment(peak_off: float, peak_sc: float, breakdown: dict) -> None:
            # Collects a candidate moment. Emission to the user happens AFTER the
            # scan, once _top_moments has ranked candidates and kept the best —
            # otherwise mediocre above-threshold runs reach the queue before we
            # know whether better ones exist later in the VOD.
            # Chat lag correction: chat trails the on-screen event, so seek the
            # VOD link a couple seconds earlier than the chat spike.
            link_offset = max(0.0, peak_off - CHAT_LAG)
            ts = _fmt_offset(link_offset)
            sig_list = [{"type": k, "value": v} for k, v in breakdown.items()]
            mid = str(uuid.uuid4())
            moment = {
                "id":              mid,
                "offset_seconds":  link_offset,
                "timestamp":       ts,
                "score":           round(peak_sc, 1),
                "trigger_score":   round(peak_sc, 1),   # 0-100, same scale as live clips
                "trigger_signals": sig_list,
                "clip_title":      f"{chan} — {ts}",
                "stream_title":    vod_title,
                "virality_score":  round(peak_sc * 0.7, 1),
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
            trigger_offsets[mid] = int(peak_off)  # stored separately, never in the clip dict
            moments.append(moment)

        while offset <= duration:
            sec = int(offset)
            for msg in msg_by_sec.get(sec, []):
                recent_deq.append((offset, msg))
                lt_deq.append((offset, msg))
                if msg["author"] and CLIP_TRIGGER_PHRASES.search(msg["text"]):
                    clip_it_deq.append((offset, msg["author"]))

            # Prune sliding windows
            while recent_deq and recent_deq[0][0] < offset - WINDOW:
                recent_deq.popleft()
            while lt_deq and lt_deq[0][0] < offset - LT_WIN:
                lt_deq.popleft()
            while clip_it_deq and clip_it_deq[0][0] < offset - CLIP_IT_W:
                clip_it_deq.popleft()

            # Score every eligible second regardless of cooldown so score_timeline
            # is fully populated — the post-scan end-offset pass needs scores for
            # the seconds immediately after each trigger (which are in cooldown).
            score = None
            if (offset >= WINDOW
                    and len(recent_deq) >= 3
                    and len(lt_deq) >= 10):
                window = [m for _, m in recent_deq]
                clip_it_senders = len({a for _, a in clip_it_deq})
                # Use actual elapsed time so baseline isn't artificially low
                # during the first 300 seconds.
                lt_actual = min(offset, LT_WIN)
                # Chat acceleration: recent-half vs older-half message rate within
                # the window. Mirrors ChatMetrics.velocity_acceleration (neutral
                # 1.0 on thin data) so VOD and live apply the identical v2 boost.
                half  = WINDOW / 2.0
                offs  = [o for o, _ in recent_deq]
                if len(offs) >= 6:
                    mid      = offset - half
                    recent_n = sum(1 for o in offs if o >= mid)
                    older_n  = len(offs) - recent_n
                    accel    = (recent_n / half) / max(older_n / half, 1e-6) if older_n > 0 else 1.0
                else:
                    accel = 1.0
                score, breakdown = _score_window(
                    window, len(lt_deq), lt_actual, WINDOW, clip_it_senders, rules,
                    acceleration=accel, spike_multiplier=spike_mult)

                if score > peak_score:
                    peak_score = score

                score_timeline[sec] = score

            # One moment per contiguous above-threshold run, anchored at its peak.
            # While the run holds, only track the peak; emit a single clip when the
            # run ends (score drops below threshold, or chat goes quiet so this
            # second is unscored). The COOLDOWN floor still separates back-to-back
            # runs so a brief dip-and-recover can't double-clip the same moment.
            if score is not None and score >= threshold:
                if not in_run:
                    in_run = True
                    run_peak_score = -1.0
                if score > run_peak_score:
                    run_peak_score = score
                    run_peak_off   = offset
                    run_peak_bd    = breakdown
            elif in_run:
                in_run = False
                if run_peak_off - last_moment_offset >= COOLDOWN:
                    last_moment_offset = run_peak_off
                    await _emit_moment(run_peak_off, run_peak_score, run_peak_bd)

            # Scoring occupies 50–100% of the progress bar
            pct = 50.0 + (min(offset / duration, 1.0) * 50.0) if duration > 0 else 100.0
            if pct - last_pct >= 3.0:
                last_pct = pct
                await on_progress(pct, {"phase": "score"})
                await asyncio.sleep(0)  # yield event loop

            offset += STEP

        # Flush a run still open at the final second (hype that ran to the end of
        # the VOD) so its peak isn't lost.
        if in_run and run_peak_off - last_moment_offset >= COOLDOWN:
            last_moment_offset = run_peak_off
            await _emit_moment(run_peak_off, run_peak_score, run_peak_bd)

        # Rank & trim: keep only the VOD's best moments (~3/hour, max 12) so the
        # queue gets highlights, not the stream's entire pulse.
        found_total = len(moments)
        moments = _top_moments(moments, duration)
        if found_total > len(moments):
            log.info("vod_moments_trimmed", vod_id=vod_id,
                     found=found_total, kept=len(moments))

        # Post-scan: find when excitement dulled for each moment (score < 40 % of peak).
        # Uses the collected score_timeline so no extra API calls are needed.
        DECAY_FACTOR = 0.40
        MAX_LOOK_AHEAD = 60  # seconds
        for m in moments:
            trig_sec = trigger_offsets.get(m["id"], int(m["offset_seconds"]))
            peak_s   = m["score"]
            end_sec  = trig_sec  # default: excitement ends immediately at trigger
            for s in range(trig_sec, min(trig_sec + MAX_LOOK_AHEAD, int(duration) + 1)):
                if score_timeline.get(s, 0) >= peak_s * DECAY_FACTOR:
                    end_sec = s
                else:
                    break
            excitement_secs = max(1, end_sec - trig_sec)
            m["end_offset_seconds"]       = max(0.0, end_sec - CHAT_LAG)
            m["excitement_duration_seconds"] = excitement_secs
            m["end_timestamp"]            = _fmt_offset(m["end_offset_seconds"])

        # Emit only the survivors (chronological), now fully annotated.
        for m in moments:
            await on_moment(m)

        log.info("vod_analysis_complete", vod_id=vod_id, moments=len(moments),
                 found=found_total,
                 peak_score=round(peak_score, 1), threshold=round(threshold, 1),
                 total_messages=total_msgs)
        await on_done(moments)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("vod_analysis_error", vod_id=vod_id, error=str(exc))
        await on_error(str(exc))
