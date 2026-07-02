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
            params = [("id", s) for s in chunk]
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
