"""
YouTube Live Chat via polling (YouTube Data API v3).
The API doesn't support WebSocket — we must poll for new messages.
"""

import asyncio
import aiohttp
import structlog
from typing import Callable, Awaitable

from config.settings import settings

log = structlog.get_logger(__name__)

YT_BASE = "https://www.googleapis.com/youtube/v3"
MIN_POLL_INTERVAL = 5.0   # seconds


class YouTubeChatMonitor:
    def __init__(
        self,
        video_id: str,
        on_message: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.video_id = video_id
        self.on_message = on_message
        self._running = False
        self._page_token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def run(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession()
        try:
            live_chat_id = await self._get_live_chat_id()
            log.info("youtube_chat_connected", video_id=self.video_id, chat_id=live_chat_id)
            while self._running:
                poll_interval = await self._poll(live_chat_id)
                await asyncio.sleep(max(poll_interval, MIN_POLL_INTERVAL))
        finally:
            await self._session.close()

    async def _get_live_chat_id(self) -> str:
        async with self._session.get(
            f"{YT_BASE}/videos",
            params={
                "part": "liveStreamingDetails",
                "id": self.video_id,
                "key": settings.youtube_api_key,
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        items = data.get("items", [])
        if not items:
            raise ValueError(f"Video {self.video_id} not found")
        details = items[0].get("liveStreamingDetails", {})
        chat_id = details.get("activeLiveChatId")
        if not chat_id:
            raise ValueError(f"No active live chat for video {self.video_id}")
        return chat_id

    async def _poll(self, live_chat_id: str) -> float:
        params = {
            "part": "snippet,authorDetails",
            "liveChatId": live_chat_id,
            "key": settings.youtube_api_key,
        }
        if self._page_token:
            params["pageToken"] = self._page_token

        async with self._session.get(f"{YT_BASE}/liveChat/messages", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._page_token = data.get("nextPageToken")
        interval_ms = data.get("pollingIntervalMillis", 5000)

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            author = item.get("authorDetails", {}).get("displayName", "")
            text = snippet.get("displayMessage", "")
            if text:
                await self.on_message(author, text)

        return interval_ms / 1000.0

    def stop(self) -> None:
        self._running = False
