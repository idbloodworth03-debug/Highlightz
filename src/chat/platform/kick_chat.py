"""
Kick chat for one live session, fed by Kick's chat webhooks.

Nothing here connects to Kick. Kick POSTs every chat message to /kick/webhook;
src/chat/platform/kick_events.py verifies it and hands it to whoever is
listening for that channel. This is the listener, with the same `run()` and
`on_message(author, content)` shape as the Twitch and YouTube monitors, so the
stream worker treats all three alike.

It replaced a Pusher websocket that Kick retired (2026-10-14) — see
kick_events' docstring for what happened and why webhooks.
"""

import asyncio
import time
from typing import Awaitable, Callable

import structlog

from src.chat.platform import kick_events

log = structlog.get_logger(__name__)


class KickChatMonitor:
    # A live channel with no chat at all for this long gets its subscription
    # renewed (Kick deliveries can stall — KickDevDocs #300). A busy chat is
    # never three minutes quiet; a small one can be, and then the renewal
    # costs two API calls and loses nothing. kick_events rate-limits it.
    SILENCE_S = 180.0
    TICK_S = 30.0
    STATS_EVERY_S = 600.0

    def __init__(
        self,
        broadcaster_id: str,
        on_message: Callable[[str, str], Awaitable[None]],
        slug: str = "",
    ) -> None:
        self.broadcaster_id = str(broadcaster_id or "")
        self.slug = (slug or "").lower()
        self.on_message = on_message
        self._running = False
        self._stopped = False
        self._queue: asyncio.Queue | None = None

    async def run(self) -> None:
        if self._stopped:
            return
        self._running = True
        try:
            if not self.broadcaster_id:
                # Kick's user id for the channel could not be learned (see
                # src/ingestion/platform/kick.py), so there is nothing to
                # subscribe to. The worker scores on audio and viewers. This
                # WAITS rather than returning: the worker ends the session as
                # soon as any of its tasks finishes, so a monitor that returned
                # would restart the whole session every half-minute.
                log.warning("kick_chat_unavailable", slug=self.slug,
                            why="no broadcaster id; scoring on audio and viewers")
                self._queue = asyncio.Queue()
                while self._running and await self._queue.get() is not None:
                    pass
                return
            self._queue = kick_events.listen(self.broadcaster_id)
            try:
                await kick_events.ensure_subscribed(self.broadcaster_id, self.slug)
                await self._consume(self._queue)
            finally:
                kick_events.unlisten(self.broadcaster_id, self._queue)
        finally:
            self._running = False

    async def _consume(self, q: asyncio.Queue) -> None:
        quiet_since = stats_at = time.time()
        heard = 0
        flowing = False
        while self._running:
            try:
                item = await asyncio.wait_for(q.get(), timeout=self.TICK_S)
            except asyncio.TimeoutError:
                item = ()
            if item is None:
                break
            now = time.time()
            if item:
                author, content = item
                if not flowing:
                    # Once per session, and again after a renewal brings a
                    # quiet chat back: the line that says chat is reaching
                    # the engine.
                    flowing = True
                    log.info("kick_chat_flowing", slug=self.slug,
                             broadcaster_id=self.broadcaster_id)
                quiet_since = now
                heard += 1
                try:
                    await self.on_message(author, content)
                except Exception as exc:
                    log.warning("kick_chat_message_failed", slug=self.slug,
                                error=str(exc)[:200])
            elif now - quiet_since >= self.SILENCE_S:
                await kick_events.ensure_subscribed(self.broadcaster_id, self.slug,
                                                    renew=True)
                quiet_since, flowing = now, False
            if now - stats_at >= self.STATS_EVERY_S:
                log.info("kick_chat_stats", slug=self.slug, messages=heard,
                         window_s=round(now - stats_at))
                heard, stats_at = 0, now

    def stop(self) -> None:
        self._stopped = True
        self._running = False
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
