import asyncio
import aiohttp
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from .base import BasePlatform, StreamInfo

log = structlog.get_logger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX_BASE = "https://api.twitch.tv/helix"


class TwitchPlatform(BasePlatform):
    def __init__(self) -> None:
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        session = await self._ensure_session()
        async with session.post(
            TOKEN_URL,
            params={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "grant_type": "client_credentials",
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            self._token = data["access_token"]
            return self._token

    def _headers(self, token: str) -> dict:
        return {
            "Client-Id": settings.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_stream_info(self, channel: str) -> StreamInfo:
        token = await self._get_token()
        session = await self._ensure_session()

        async with session.get(
            f"{HELIX_BASE}/streams",
            headers=self._headers(token),
            params={"user_login": channel},
        ) as resp:
            if resp.status == 401:
                self._token = None  # force refresh on next retry
                resp.raise_for_status()
            resp.raise_for_status()
            data = await resp.json()

        streams = data.get("data", [])
        if not streams:
            raise ValueError(f"Channel '{channel}' is not live on Twitch")

        stream = streams[0]
        stream_url = f"https://www.twitch.tv/{channel}"

        return StreamInfo(
            channel=channel,
            platform="twitch",
            stream_url=stream_url,
            chat_channel_id=channel.lower(),
            title=stream.get("title", "").encode("ascii", errors="ignore").decode(),
            game=stream.get("game_name", "").encode("ascii", errors="ignore").decode(),
            viewer_count=stream.get("viewer_count", 0),
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
