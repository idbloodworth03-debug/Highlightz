"""
Creates clips on Kick via the public v1 Clips API.

Kick clips are created with the user's OAuth token (scope: clips:write).
The clip is hosted by Kick and attributed to the user — Highlightz never
records or re-hosts any video.

Flow:
  POST /public/v1/clips  {"channel_name": slug}  → clip_url
"""

import asyncio
import aiohttp
import structlog

log = structlog.get_logger(__name__)

KICK_API = "https://api.kick.com/public/v1"

# Raised specifically when the token lacks the clips:write scope so callers can
# surface a "re-link your Kick account" message rather than a generic failure.
class KickScopeError(RuntimeError):
    pass


async def create_clip(
    user_token: str,
    channel_slug: str,
    retries: int = 3,
) -> str | None:
    """Create a clip on Kick. Returns the clip URL or None on failure.

    Raises KickScopeError when the token lacks clips:write scope (HTTP 401/403),
    so the caller can prompt the user to re-link their Kick account.
    """
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
                        log.info("kick_clip_created", url=clip_url,
                                 channel_slug=channel_slug, attempt=attempt + 1)
                        return clip_url
                    log.warning("kick_clip_no_url", body=str(data)[:300],
                                channel_slug=channel_slug)
                    return None

                body = await resp.text()

                if resp.status in (401, 403):
                    # Token does not have clips:write scope — user must re-link
                    log.error("kick_clip_scope_error",
                              status=resp.status, body=body[:300],
                              channel_slug=channel_slug,
                              hint="User must re-link Kick account to grant clips:write scope")
                    raise KickScopeError(
                        f"Kick token missing clips:write scope (HTTP {resp.status}) — "
                        "user must re-link their Kick account"
                    )

                if resp.status in (429, 503) and attempt < retries - 1:
                    wait = 5 * (attempt + 1)
                    log.warning("kick_clip_rate_limited", status=resp.status,
                                attempt=attempt + 1, retry_in=wait)
                    await asyncio.sleep(wait)
                    continue

                log.error("kick_clip_failed",
                          status=resp.status, body=body[:300],
                          channel_slug=channel_slug, attempt=attempt + 1)
                return None

    return None
