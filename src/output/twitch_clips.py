"""
Posts a clip to Twitch via the Helix Clips API.
Note: Twitch Clips API creates a clip from the live stream directly;
this is used alongside our own clip extraction as a backup/bonus.
"""

import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

log = structlog.get_logger(__name__)

HELIX_BASE = "https://api.twitch.tv/helix"


class TwitchClipsPublisher:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def create_clip(self, broadcaster_id: str) -> str | None:
        session = await self._ensure_session()
        token = settings.twitch_oauth_token
        if not token.startswith("oauth:"):
            token = f"oauth:{token}"

        headers = {
            "Client-Id": settings.twitch_client_id,
            "Authorization": f"Bearer {settings.twitch_oauth_token}",
        }
        async with session.post(
            f"{HELIX_BASE}/clips",
            headers=headers,
            params={"broadcaster_id": broadcaster_id},
        ) as resp:
            if resp.status != 202:
                log.warning("twitch_clip_failed", status=resp.status)
                return None
            data = await resp.json()
            clips = data.get("data", [])
            if clips:
                clip_id = clips[0].get("id")
                edit_url = clips[0].get("edit_url", "")
                log.info("twitch_clip_created", clip_id=clip_id)
                return edit_url
        return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
