import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from .base import BasePlatform, StreamInfo, ChannelOffline

log = structlog.get_logger(__name__)

YT_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubePlatform(BasePlatform):
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_live_video(self, channel_id: str) -> dict:
        session = await self._ensure_session()
        async with session.get(
            f"{YT_BASE}/search",
            params={
                "part": "snippet",
                "channelId": channel_id,
                "eventType": "live",
                "type": "video",
                "key": settings.youtube_api_key,
            },
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _resolve_channel_id(self, handle: str) -> str:
        session = await self._ensure_session()
        async with session.get(
            f"{YT_BASE}/channels",
            params={
                "part": "id",
                "forHandle": handle.lstrip("@"),
                "key": settings.youtube_api_key,
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        items = data.get("items", [])
        if not items:
            raise ValueError(f"YouTube channel '{handle}' not found")
        return items[0]["id"]

    async def get_stream_info(self, channel: str) -> StreamInfo:
        channel_id = await self._resolve_channel_id(channel)
        data = await self._search_live_video(channel_id)
        items = data.get("items", [])
        if not items:
            raise ChannelOffline(f"Channel '{channel}' is not live on YouTube")

        video_id = items[0]["id"]["videoId"]
        snippet = items[0]["snippet"]
        stream_url = f"https://www.youtube.com/watch?v={video_id}"

        return StreamInfo(
            channel=channel,
            platform="youtube",
            stream_url=stream_url,
            chat_channel_id=video_id,
            title=snippet.get("title", ""),
            game="",
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
