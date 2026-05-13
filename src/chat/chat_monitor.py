"""
Thin orchestrator: picks the right platform chat monitor and
exposes a single `run()` coroutine plus a metrics accessor.
"""

import asyncio
import structlog
from typing import Callable, Awaitable

from src.chat.metrics import ChatMetrics
from src.chat.platform.twitch_chat import TwitchChatMonitor
from src.chat.platform.youtube_chat import YouTubeChatMonitor

log = structlog.get_logger(__name__)


class ChatMonitor:
    def __init__(self, platform: str, channel_id: str) -> None:
        self.metrics = ChatMetrics()
        self._platform = platform
        self._channel_id = channel_id
        self._monitor = None

    async def run(self) -> None:
        async def on_message(author: str, message: str) -> None:
            self.metrics.ingest(message)

        if self._platform == "twitch":
            self._monitor = TwitchChatMonitor(self._channel_id, on_message)
        else:
            self._monitor = YouTubeChatMonitor(self._channel_id, on_message)

        await self._monitor.run()

    def stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
