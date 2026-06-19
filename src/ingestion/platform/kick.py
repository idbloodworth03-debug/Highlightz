"""Kick platform — resolves channel info via the Kick public API."""

import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BasePlatform, StreamInfo

log = structlog.get_logger(__name__)

KICK_API_BASE = "https://api.kick.com/public/v1"


class KickPlatform(BasePlatform):
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_stream_info(self, channel: str) -> StreamInfo:
        session = await self._ensure_session()
        async with session.get(
            f"{KICK_API_BASE}/channels",
            params={"broadcaster_user_login": channel},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        # data is an array; expect at least one entry
        if not data or not isinstance(data, list):
            raise ValueError(f"Channel '{channel}' not found on Kick")

        entry = data[0]
        livestream = entry.get("livestream")
        if livestream is None:
            raise ValueError(f"Channel '{channel}' is not live on Kick")

        chatroom = entry.get("chatroom", {})
        chat_channel_id = str(chatroom.get("id", channel))

        title = (entry.get("channel", {}).get("title", "") or "").encode("ascii", errors="ignore").decode()
        categories = livestream.get("categories", [])
        game = (categories[0].get("name", "") if categories else "").encode("ascii", errors="ignore").decode()
        viewer_count = int(livestream.get("viewers", 0) or 0)

        return StreamInfo(
            channel=channel,
            platform="kick",
            stream_url=f"https://kick.com/{channel}",
            chat_channel_id=chat_channel_id,
            title=title,
            game=game,
            viewer_count=viewer_count,
        )

    async def is_live(self, channel: str) -> bool:
        try:
            await self.get_stream_info(channel)
            return True
        except ValueError:
            return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
