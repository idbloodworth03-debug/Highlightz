"""
Creates clips on Kick via the public v1 Clips API.

Kick clips are created with the user's OAuth token (scopes: channel:write).
The clip is hosted by Kick and attributed to the user — Highlightz never
records or re-hosts any video.

Flow:
  POST /public/v1/clips  {"channel_name": slug}  → clip_url
  If public v1 unavailable, falls back to internal v2 clip.init + clip.finalize.
"""

import asyncio
import aiohttp
import structlog

log = structlog.get_logger(__name__)

KICK_API = "https://api.kick.com/public/v1"
_KICK_V2_INIT     = "https://kick.com/api/v2/clip.init"
_KICK_V2_FINALIZE = "https://kick.com/api/v2/clip.finalize"


async def create_clip(
    user_token: str,
    channel_slug: str,
    retries: int = 3,
) -> str | None:
    """Create a clip on Kick. Returns the clip URL or None on failure.

    Tries the public v1 endpoint first; falls back to the v2 internal
    init+finalize flow if the public endpoint is unavailable.
    """
    result = await _create_clip_v1(user_token, channel_slug, retries)
    if result:
        return result
    log.info("kick_clip_v1_unavailable_trying_v2", channel_slug=channel_slug)
    return await _create_clip_v2(user_token, channel_slug, retries)


async def _create_clip_v1(
    user_token: str,
    channel_slug: str,
    retries: int,
) -> str | None:
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type":  "application/json",
    }
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            async with session.post(
                f"{KICK_API}/clips",
                headers=headers,
                json={"channel_name": channel_slug},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    clip_url = data.get("clip_url") or data.get("url", "")
                    if clip_url:
                        log.info("kick_clip_v1_created", url=clip_url,
                                 channel_slug=channel_slug, attempt=attempt + 1)
                        return clip_url
                    # 200 but no URL — unexpected; treat as failure
                    log.warning("kick_clip_v1_no_url", body=str(data)[:200])
                    return None

                if resp.status in (404, 501):
                    # Endpoint not available on this Kick build
                    return None

                if resp.status in (429, 503) and attempt < retries - 1:
                    await asyncio.sleep(5)
                    continue

                body = await resp.text()
                log.warning("kick_clip_v1_failed",
                            status=resp.status, body=body[:200],
                            channel_slug=channel_slug, attempt=attempt + 1)
                return None
    return None


async def _create_clip_v2(
    user_token: str,
    channel_slug: str,
    retries: int,
) -> str | None:
    """Fallback: Kick internal v2 clip.init → clip.finalize flow."""
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type":  "application/json",
    }
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            # Step 1: init
            async with session.post(
                _KICK_V2_INIT,
                headers=headers,
                json={"channel": channel_slug},
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    log.warning("kick_clip_v2_init_failed",
                                status=resp.status, body=body[:200],
                                attempt=attempt + 1)
                    if attempt < retries - 1:
                        await asyncio.sleep(5)
                        continue
                    return None
                init_data = await resp.json()

            clip_id = init_data.get("clip_id") or init_data.get("id", "")
            if not clip_id:
                log.warning("kick_clip_v2_no_clip_id", body=str(init_data)[:200])
                return None

            # Step 2: finalize
            async with session.post(
                _KICK_V2_FINALIZE,
                headers=headers,
                json={"clip_id": clip_id},
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    log.warning("kick_clip_v2_finalize_failed",
                                status=resp.status, body=body[:200],
                                clip_id=clip_id)
                    return None
                final_data = await resp.json()

            clip_url = (
                final_data.get("clip_url")
                or final_data.get("url")
                or f"https://kick.com/{channel_slug}?clip={clip_id}"
            )
            log.info("kick_clip_v2_created", url=clip_url,
                     channel_slug=channel_slug, clip_id=clip_id)
            return clip_url

    return None
