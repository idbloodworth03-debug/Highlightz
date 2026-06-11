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


async def _get_app_token(session: aiohttp.ClientSession) -> str:
    global _app_token, _app_token_exp
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


async def create_clip(user_token: str, broadcaster_id: str) -> str | None:
    """Create a clip on the broadcaster's live stream using the user's token.
    Returns the clip slug, or None on failure."""
    headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {user_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{HELIX_BASE}/clips", headers=headers,
                                params={"broadcaster_id": broadcaster_id}) as resp:
            if resp.status == 202:
                data = await resp.json()
                rows = data.get("data", [])
                if rows:
                    slug = rows[0].get("id")
                    log.info("twitch_clip_requested", slug=slug, broadcaster_id=broadcaster_id)
                    return slug
                return None
            body = await resp.text()
            log.warning("twitch_clip_create_failed", status=resp.status,
                        broadcaster_id=broadcaster_id, body=body[:200])
            return None


async def get_clip(slug: str, attempts: int = 8, delay: float = 2.0) -> dict | None:
    """Poll Get Clips until the freshly-created clip is queryable.
    Returns the clip object (url, embed_url, thumbnail_url, duration, title)."""
    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
        for _ in range(attempts):
            async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                                   params={"id": slug}) as resp:
                if resp.status == 200:
                    rows = (await resp.json()).get("data", [])
                    if rows:
                        return rows[0]
            await asyncio.sleep(delay)
    log.warning("twitch_clip_not_ready", slug=slug)
    return None
