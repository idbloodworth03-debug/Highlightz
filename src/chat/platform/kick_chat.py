"""
Kick chat via Pusher WebSockets (wss://ws-us2.pusher.com).
Calls `on_message` callback for every ChatMessageEvent received.
"""

import asyncio
import json
import structlog
import websockets
from typing import Callable, Awaitable

log = structlog.get_logger(__name__)

PUSHER_KEY = "32cbd69e4b950bf97679"
PUSHER_URL = (
    f"wss://ws-us2.pusher.com/app/{PUSHER_KEY}"
    "?protocol=7&client=python-highlightz&version=1.0&flash=false"
)


class KickChatMonitor:
    def __init__(
        self,
        chatroom_id: str,
        on_message: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.chatroom_id = str(chatroom_id)
        self.on_message = on_message
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as exc:
                log.warning("kick_chat_reconnecting", chatroom_id=self.chatroom_id, error=str(exc))
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        async with websockets.connect(PUSHER_URL, ping_interval=30) as ws:
            # Wait for connection_established
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("event") != "pusher:connection_established":
                raise RuntimeError(f"Unexpected Pusher handshake: {msg}")

            # Subscribe to the chatroom channel
            await ws.send(json.dumps({
                "event": "pusher:subscribe",
                "data": {
                    "auth": "",
                    "channel": f"chatrooms.{self.chatroom_id}.v2",
                },
            }))
            log.info("kick_chat_joined", chatroom_id=self.chatroom_id)

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    event = msg.get("event", "")

                    if event == "App\\Events\\ChatMessageEvent":
                        data = json.loads(msg.get("data", "{}"))
                        author = data.get("sender", {}).get("username", "")
                        content = data.get("content", "")
                        if author and content:
                            await self.on_message(author, content)

                    elif event == "pusher:ping":
                        await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))

                except Exception:
                    pass  # malformed frame — ignore and continue

    def stop(self) -> None:
        self._running = False
