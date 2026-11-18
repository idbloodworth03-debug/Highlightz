"""The web process, as an ingest worker sees it.

THE SPLIT, stage 2 (docs/HANDOFF.md). In a worker process `host._remote` is
one of these: the things a stream worker asks of the web process, sent
over the Link instead of called.

WHAT HAPPENS WHEN THE WEB PROCESS DOES NOT ANSWER. It is the same every time:
the careful answer, and a log line. No slot (the channel is "queued" and asks
again in 30 seconds, as when the box is full); nobody else watching; no room
for a Highlight. A worker must never go live, or hand a clip to somebody, on
the strength of a question nobody answered. The two that deliver a clip
(notify_clip_ready, on_clip_file_ready) raise instead, so the caller's own
error handling runs as it would have for a failure in the dashboard.

`streams()` is this worker's MIRROR of its own channels' records: filled when
the web process says "spawn", dropped on "stop". The worker reads its preset
from it and writes its status to it exactly as it wrote to the dashboard's
dict; the web process learns of the change from the `stream_status` /
`stream_updated` event that follows, and decides whether it applies (a
paused channel stays paused, whatever its worker last said).
"""

from __future__ import annotations

import structlog

from src.ingestion.bridge import Link, LinkClosed, RemoteError

log = structlog.get_logger(__name__)

DELIVERY_TIMEOUT_S = 30.0       # handing over a clip: longer than a question
_NO_ANSWER = (LinkClosed, RemoteError, TimeoutError)


class Remote:
    def __init__(self, link: Link) -> None:
        self._link = link
        self._streams: dict[str, dict] = {}

    # ── this worker's own records ────────────────────────────────────────────

    def streams(self) -> dict:
        return self._streams

    def remember(self, record: dict) -> str:
        uid, channel = record.get("user_id") or "", record["channel"]
        key = f"{uid}:{channel}" if uid else channel
        self._streams[key] = dict(record)
        return key

    def forget(self, channel: str, user_id: str = "") -> None:
        self._streams.pop(f"{user_id}:{channel}" if user_id else channel, None)

    # ── what host.py forwards ────────────────────────────────────────────────

    async def broadcast(self, event: dict, user_id: str | None = None) -> None:
        # A score is replaced by the next one a second later: the one message
        # that may be dropped if the web process has stopped reading.
        self._link.cast("broadcast", event, user_id,
                        droppable=event.get("event") == "score_update")

    async def acquire_live_slot(self, stream_key: str) -> bool:
        try:
            return bool(await self._link.call("acquire_live_slot", stream_key))
        except _NO_ANSWER as exc:
            log.warning("ingest_no_answer", asked="acquire_live_slot", error=str(exc)[:160])
            return False

    def release_live_slot(self, stream_key: str) -> None:
        self._link.cast("release_live_slot", stream_key)

    async def watchers_of(self, channel: str) -> list[str]:
        try:
            return list(await self._link.call("watchers_of", channel) or [])
        except _NO_ANSWER as exc:
            log.warning("ingest_no_answer", asked="watchers_of", error=str(exc)[:160])
            return []

    async def suggestion_room(self, uid: str) -> tuple[int, int]:
        try:
            used, room = await self._link.call("suggestion_room", uid)
            return int(used), int(room)
        except _NO_ANSWER as exc:
            log.warning("ingest_no_answer", asked="suggestion_room", error=str(exc)[:160])
            return 0, 0
        except (TypeError, ValueError):
            return 0, 0

    # ── Kick chat (src/chat/platform/kick_events.py, `_remote`) ──────────────

    def kick_listen(self, broadcaster_id: str) -> None:
        self._link.cast("kick_listen", str(broadcaster_id))

    def kick_unlisten(self, broadcaster_id: str) -> None:
        self._link.cast("kick_unlisten", str(broadcaster_id))

    async def kick_ensure_subscribed(self, broadcaster_id: str, slug: str = "",
                                     renew: bool = False) -> bool:
        try:
            return bool(await self._link.call("kick_ensure_subscribed", str(broadcaster_id),
                                              slug, bool(renew), timeout=DELIVERY_TIMEOUT_S))
        except _NO_ANSWER as exc:
            log.warning("ingest_no_answer", asked="kick_ensure_subscribed", error=str(exc)[:160])
            return False

    async def trim_clip_store(self) -> int:
        # Asked of the web process, which alone knows which videos may go.
        try:
            return int(await self._link.call("trim_clip_store", timeout=DELIVERY_TIMEOUT_S) or 0)
        except _NO_ANSWER as exc:
            log.warning("ingest_no_answer", asked="trim_clip_store", error=str(exc)[:160])
            return 0

    async def clip_dropped(self, clip_id: str) -> bool:
        # Asked of the web process, where the clip processor's decisions are.
        # No answer = not dropped: cut the file (host.clip_dropped says why).
        try:
            return bool(await self._link.call("clip_dropped", clip_id))
        except _NO_ANSWER as exc:
            log.warning("ingest_no_answer", asked="clip_dropped", error=str(exc)[:160])
            return False

    async def notify_clip_ready(self, meta: dict):
        return await self._link.call("notify_clip_ready", meta, timeout=DELIVERY_TIMEOUT_S)

    async def on_clip_file_ready(self, clip_id: str):
        return await self._link.call("on_clip_file_ready", clip_id, timeout=DELIVERY_TIMEOUT_S)
