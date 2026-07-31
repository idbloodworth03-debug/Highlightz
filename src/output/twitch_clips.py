"""
Creates clips on Twitch via the Helix Clips API.

Compliance model: the clip is created with the *user's* OAuth token (scope
`clips:edit`), so Twitch hosts the clip on its CDN and attributes it to that
user's account. Highlightz never records or re-hosts any video.

Flow:
  1. resolve_broadcaster_id(login)         — app token, login → numeric id
  2. create_clip(user_token, broadcaster)  — POST /helix/clips → clip slug
  3. get_clip(slug)                         — poll GET /helix/clips for url/embed
"""

import asyncio
import time
import aiohttp
import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

HELIX_BASE = "https://api.twitch.tv/helix"
TOKEN_URL  = "https://id.twitch.tv/oauth2/token"

# Cached app (client-credentials) token for read-only lookups
_app_token: str = ""
_app_token_exp: float = 0.0
_app_token_lock = asyncio.Lock()


async def _get_app_token(session: aiohttp.ClientSession) -> str:
    global _app_token, _app_token_exp
    async with _app_token_lock:
        if _app_token and time.time() < _app_token_exp:
            return _app_token
    async with session.post(
        TOKEN_URL,
        params={
            "client_id":     settings.twitch_client_id,
            "client_secret": settings.twitch_client_secret,
            "grant_type":    "client_credentials",
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    async with _app_token_lock:
        _app_token = data["access_token"]
        _app_token_exp = time.time() + data.get("expires_in", 3600) - 60
        return _app_token


async def resolve_broadcaster_id(login: str) -> str | None:
    """Resolve a Twitch channel login to its numeric broadcaster id."""
    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
        async with session.get(f"{HELIX_BASE}/users", headers=headers,
                               params={"login": login.lower()}) as resp:
            if resp.status != 200:
                log.warning("resolve_broadcaster_failed", login=login, status=resp.status)
                return None
            data = await resp.json()
    rows = data.get("data", [])
    return rows[0]["id"] if rows else None


async def get_recent_clips(broadcaster_id: str, days: int = 30, limit: int = 100) -> list[dict]:
    """Fetch a channel's existing clips from the last `days` days (top by views).
    Returns a list of clip dicts (view_count, created_at, duration, title…)."""
    import datetime as _dt
    started_at = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
        async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                               params={"broadcaster_id": broadcaster_id,
                                       "started_at": started_at,
                                       "first": min(limit, 100)}) as resp:
            if resp.status != 200:
                log.warning("get_recent_clips_failed", broadcaster_id=broadcaster_id,
                            status=resp.status)
                return []
            data = await resp.json()
    return data.get("data", [])


async def get_clips_for_vod(broadcaster_id: str, vod_id: str,
                            limit: int = 100) -> list[dict]:
    """Clips that VIEWERS created from a specific VOD — crowd-sourced ground
    truth for "this was a highlight", with view_count as a built-in quality
    ranking.

    Helix has no video_id filter on Get Clips, so we page the broadcaster's
    recent clips and keep the ones whose video_id matches. Returns a list of
    {offset, duration, view_count, title, url, id} sorted by offset, where
    `offset` is the clip's START second in the VOD.

    Note on vod_offset: Twitch documents it as the clip's END position, so the
    start is (vod_offset - duration). It is null for clips made during a live
    broadcast (the offset is resolved minutes later), and those are skipped.

    Returns [] on any failure — highlight enrichment must never break a scan.
    """
    if not (broadcaster_id and vod_id):
        return []
    try:
        found: list[dict] = []
        async with aiohttp.ClientSession() as session:
            token = await _get_app_token(session)
            headers = {"Client-Id": settings.twitch_client_id,
                       "Authorization": f"Bearer {token}"}
            cursor = ""
            for _ in range(5):          # up to 500 recent clips
                params = {"broadcaster_id": broadcaster_id, "first": 100}
                if cursor:
                    params["after"] = cursor
                async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                                       params=params) as resp:
                    if resp.status != 200:
                        log.warning("vod_clips_lookup_failed", status=resp.status,
                                    vod_id=vod_id)
                        break
                    payload = await resp.json()
                rows = payload.get("data", [])
                if not rows:
                    break
                for r in rows:
                    if str(r.get("video_id") or "") != str(vod_id):
                        continue
                    end = r.get("vod_offset")
                    if end is None:
                        continue        # live-created clip, offset not resolved yet
                    dur = float(r.get("duration") or 30.0)
                    found.append({
                        "id":         r.get("id"),
                        "offset":     max(0.0, float(end) - dur),
                        "duration":   dur,
                        "view_count": int(r.get("view_count") or 0),
                        "title":      (r.get("title") or "").strip(),
                        "url":        r.get("url") or "",
                    })
                cursor = (payload.get("pagination") or {}).get("cursor") or ""
                if not cursor:
                    break
        found.sort(key=lambda c: c["offset"])
        log.info("vod_viewer_clips_found", vod_id=vod_id, count=len(found))
        return found
    except Exception as exc:
        log.warning("vod_clips_lookup_error", vod_id=vod_id, error=str(exc))
        return []


async def list_channel_clips(broadcaster_id: str, cursor: str = "",
                             limit: int = 100) -> dict:
    """One page of a channel's clips, newest-first-ish, for the import screen.

    This is the whole "show me all my Twitch clips" feature: Get Clips is
    documented, needs only an app token, and returns everything the UI wants
    (title, thumbnail, views, duration, embed). No part of it touches the
    media CDN or any undocumented URL — importing METADATA is a completely
    different question from obtaining the video file, which has no supported
    path (see src/maintenance/probe_clip_media.py).

    Returns {"clips": [...], "cursor": str}. An empty cursor means the end.

    Paging is caller-driven rather than looped-to-exhaustion here: a large
    channel has thousands of clips, and Helix's 800 points/min is shared
    across every user we serve. One page per request keeps a single import
    from starving live clipping.

    Note on ordering: Helix sorts by VIEW COUNT, not recency (a documented
    quirk that has bitten this codebase before). The UI says so rather than
    pretending it is chronological.
    """
    if not broadcaster_id:
        return {"clips": [], "cursor": ""}

    params = {"broadcaster_id": broadcaster_id, "first": max(1, min(100, limit))}
    if cursor:
        params["after"] = cursor

    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {token}"}
        async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                               params=params) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                log.warning("channel_clips_failed", status=resp.status,
                            broadcaster_id=broadcaster_id, body=body)
                raise RuntimeError(f"Twitch returned HTTP {resp.status}")
            payload = await resp.json()

    clips = []
    for r in payload.get("data", []):
        if not r.get("id"):
            continue
        clips.append({
            "id":            r["id"],
            "title":         (r.get("title") or "").strip(),
            "url":           r.get("url") or f"https://clips.twitch.tv/{r['id']}",
            "embed_url":     r.get("embed_url") or "",
            "thumbnail_url": r.get("thumbnail_url") or "",
            "view_count":    int(r.get("view_count") or 0),
            "duration":      float(r.get("duration") or 0.0),
            "created_at":    r.get("created_at") or "",
            # Who pressed the clip button — the streamer or a viewer. Worth
            # surfacing: "clips other people made of me" is most of a channel's
            # catalogue and the part streamers have never had in one place.
            "creator_name":  r.get("creator_name") or "",
            "game_id":       r.get("game_id") or "",
        })

    return {"clips": clips,
            "cursor": (payload.get("pagination") or {}).get("cursor") or ""}


async def create_clip(user_token: str, broadcaster_id: str,
                      retries: int = 3, retry_delay: float = 5.0) -> str | None:
    """Create a clip on the broadcaster's live stream using the user's token.
    Returns the clip slug, or None on failure.

    Twitch occasionally returns 'Failed to determine content classification'
    for streamers who haven't set CCLs or on the first clip of a session.
    We retry up to `retries` times before giving up.
    """
    headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {user_token}"}
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            async with session.post(f"{HELIX_BASE}/clips", headers=headers,
                                    params={"broadcaster_id": broadcaster_id}) as resp:
                if resp.status == 202:
                    data = await resp.json()
                    rows = data.get("data", [])
                    if rows:
                        slug = rows[0].get("id")
                        log.info("twitch_clip_requested", slug=slug,
                                 broadcaster_id=broadcaster_id, attempt=attempt + 1)
                        return slug
                    return None

                body = await resp.text()

                # Rate limited: the Helix clip-create limit is global across all
                # callers, so a busy moment can starve one channel. Back off and
                # retry. Honor Ratelimit-Reset (Unix epoch) when present, else
                # exponential backoff.
                if resp.status == 429 and attempt < retries - 1:
                    backoff = retry_delay * (2 ** attempt)   # 5s, 10s, 20s…
                    reset = resp.headers.get("Ratelimit-Reset")
                    if reset:
                        try:
                            backoff = max(0.0, float(reset) - time.time())
                        except ValueError:
                            pass
                    backoff = min(max(backoff, 1.0), 30.0)   # clamp to [1s, 30s]
                    log.warning("twitch_clip_rate_limited", broadcaster_id=broadcaster_id,
                                attempt=attempt + 1, retrying_in=round(backoff, 1))
                    await asyncio.sleep(backoff)
                    continue

                is_ccl_error = "content classification" in body.lower()
                if is_ccl_error and attempt < retries - 1:
                    log.warning("twitch_clip_ccl_retry", broadcaster_id=broadcaster_id,
                                attempt=attempt + 1, retrying_in=retry_delay)
                    await asyncio.sleep(retry_delay)
                    continue

                log.warning("twitch_clip_create_failed", status=resp.status,
                            broadcaster_id=broadcaster_id, body=body[:300],
                            hint="Streamer may need to set Content Classification Labels on their Twitch dashboard"
                                 if is_ccl_error else "")
                return None
    return None


async def search_channels(query: str, first: int = 8) -> list[dict]:
    """Helix Search Channels — partial-name typeahead for the add-stream box.
    Returns light rows; [] on any error (suggestions are never worth failing)."""
    if not query:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            token = await _get_app_token(session)
            headers = {"Client-Id": settings.twitch_client_id,
                       "Authorization": f"Bearer {token}"}
            async with session.get(f"{HELIX_BASE}/search/channels", headers=headers,
                                   params={"query": query, "first": str(first)}) as resp:
                if resp.status != 200:
                    log.warning("channel_search_failed", status=resp.status, query=query)
                    return []
                rows = (await resp.json()).get("data", [])
        return [{
            "login":   r.get("broadcaster_login", ""),
            "name":    r.get("display_name", ""),
            "avatar":  r.get("thumbnail_url", ""),
            "is_live": bool(r.get("is_live")),
            "game":    r.get("game_name", ""),
        } for r in rows if r.get("broadcaster_login")]
    except Exception as exc:
        log.warning("channel_search_failed", query=query, error=str(exc))
        return []


async def get_top_streams(first: int = 12) -> list[dict]:
    """Helix Get Streams — the most-watched live channels right now, for the
    'popular' zero-state of the add-stream box. [] on any error."""
    try:
        async with aiohttp.ClientSession() as session:
            token = await _get_app_token(session)
            headers = {"Client-Id": settings.twitch_client_id,
                       "Authorization": f"Bearer {token}"}
            async with session.get(f"{HELIX_BASE}/streams", headers=headers,
                                   params={"first": str(first)}) as resp:
                if resp.status != 200:
                    log.warning("top_streams_failed", status=resp.status)
                    return []
                rows = (await resp.json()).get("data", [])
        return [{
            "login":   r.get("user_login", ""),
            "name":    r.get("user_name", ""),
            "game":    r.get("game_name", ""),
            "viewers": int(r.get("viewer_count") or 0),
        } for r in rows if r.get("user_login")]
    except Exception as exc:
        log.warning("top_streams_failed", error=str(exc))
        return []


async def get_existing_clip_ids(slugs: list[str]) -> set[str] | None:
    """Which of these clip slugs does Twitch still have? Batched Get Clips
    (up to 100 ids per request). Returns the set of ids present, or None if the
    lookup failed — callers MUST treat None as 'unknown', never as 'all gone',
    or an API blip would mass-delete healthy clips.

    Broadcasters/mods can delete clips after creation (some channels mass-
    delete), which leaves dead links in the review queue; this powers the sweep
    that clears them."""
    if not slugs:
        return set()
    found: set[str] = set()
    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
        for i in range(0, len(slugs), 100):
            chunk = slugs[i:i + 100]
            # Explicit first=100: if Helix ever applied its default page size
            # (20) to id-queries, a truncated-but-200 response would make the
            # sweep read up to 80 healthy clips as "deleted". Never rely on the
            # default when the answer's completeness decides deletions.
            params = [("id", s) for s in chunk] + [("first", "100")]
            async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                                   params=params) as resp:
                if resp.status != 200:
                    log.warning("clip_existence_check_failed", status=resp.status,
                                chunk_size=len(chunk))
                    return None
                rows = (await resp.json()).get("data", [])
            found |= {r.get("id") for r in rows if r.get("id")}
    return found


async def get_clip(slug: str, attempts: int = 20, delay: float = 2.5) -> dict | None:
    """Poll Get Clips until the freshly-created clip is queryable.
    Returns the clip object (url, embed_url, thumbnail_url, duration, title), or
    None if the clip never materialized (Twitch accepted the request but the
    capture failed — e.g. the broadcast buffer was too short or VODs are off).

    Twitch clip processing is asynchronous and commonly takes 20-40s after the
    202, so we poll for ~50s before giving up. A clip that is never queryable
    will show 'Clip is no longer available' on Twitch, so the caller MUST treat
    a None return as a failure rather than fabricating a watch link.
    """
    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
        for attempt in range(attempts):
            async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                                   params={"id": slug}) as resp:
                if resp.status == 200:
                    rows = (await resp.json()).get("data", [])
                    # Require url + thumbnail_url + duration>0: Twitch populates all
                    # three only when the video is actually captured and playable.
                    # A ghost clip (buffer too short, stream ended mid-capture) can
                    # return a url while thumbnail_url is empty and duration is 0,
                    # causing "Clip is no longer available" in the embed.
                    c = rows[0] if rows else None
                    if (c and c.get("url") and c.get("thumbnail_url")
                            and float(c.get("duration") or 0) > 0):
                        log.info("twitch_clip_ready_after", slug=slug,
                                 seconds=round(attempt * delay, 1))
                        return c
            await asyncio.sleep(delay)
    log.warning("twitch_clip_not_ready", slug=slug,
                waited_seconds=round(attempts * delay, 1),
                hint="Clip capture likely failed — broadcast buffer too short or VODs disabled")
    return None
