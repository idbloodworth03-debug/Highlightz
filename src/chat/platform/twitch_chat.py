"""
Twitch chat via IRC over WebSocket (ws://irc-ws.chat.twitch.tv:443).
Calls `on_message` callback for every PRIVMSG received.
"""

import asyncio
import websockets
import structlog
from typing import Callable, Awaitable

from config.settings import settings

log = structlog.get_logger(__name__)

IRC_URI = "wss://irc-ws.chat.twitch.tv:443"
PING_INTERVAL = 60


class TwitchChatMonitor:
    def __init__(
        self,
        channel: str,
        on_message: Callable[[str, str], Awaitable[None]],
        sub_raid_cb: Callable | None = None,
    ) -> None:
        self.channel = channel.lower().lstrip("#")
        self.on_message = on_message
        self._sub_raid_cb = sub_raid_cb
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as exc:
                log.warning("twitch_chat_reconnecting", channel=self.channel, error=str(exc))
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        async with websockets.connect(IRC_URI, ping_interval=PING_INTERVAL) as ws:
            await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
            token = settings.twitch_oauth_token or "SCHMOOPIIE"
            if not token.startswith("oauth:"):
                token = f"oauth:{token}"
            await ws.send(f"PASS {token}")
            await ws.send(f"NICK justinfan{hash(self.channel) % 99999}")
            await ws.send(f"JOIN #{self.channel}")
            log.info("twitch_chat_joined", channel=self.channel)

            async for raw in ws:
                if not self._running:
                    break
                await self._handle(raw, ws)

    async def _handle(self, raw: str, ws) -> None:
        if raw.startswith("PING"):
            await ws.send("PONG :tmi.twitch.tv")
            return

        if "PRIVMSG" in raw:
            try:
                # :username!username@username.tmi.twitch.tv PRIVMSG #channel :message
                parts = raw.split("PRIVMSG", 1)
                author_part = parts[0].split("!")[0].lstrip(":")
                # Strip leading tags prefix from author if present
                if author_part.startswith("@"):
                    author_part = author_part.split(" ")[-1]
                author_part = author_part.lstrip(":")
                message = parts[1].split(":", 1)[1].strip()
                await self.on_message(author_part, message)
            except (IndexError, ValueError):
                pass

        if "USERNOTICE" in raw and self._sub_raid_cb is not None:
            try:
                # Parse msg-id from tags prefix: @badge-info=...;msg-id=raid;...
                msg_id = None
                if raw.startswith("@"):
                    tags_str = raw.split(" ", 1)[0].lstrip("@")
                    for tag in tags_str.split(";"):
                        if tag.startswith("msg-id="):
                            msg_id = tag.split("=", 1)[1]
                            break
                if msg_id in ("sub", "resub", "subgift", "anonsubgift", "raid"):
                    self._sub_raid_cb(msg_id)
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
