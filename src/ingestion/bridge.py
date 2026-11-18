"""The channel between the web process and one ingest worker.

THE SPLIT (docs/HANDOFF.md, "THE SPLIT"): stream workers run in processes of
their own so they stop sharing the web process's one loop. This is the wire
between the two: one connected socket per worker, a line of JSON per message,
the same class on both ends.

    cast(method, *args)        say it; no answer; handled in the order sent
    await call(method, *args)  ask it; the answer, or the other side's error,
                               or TimeoutError

WHY A SOCKET OF OUR OWN and not Redis pub/sub, which already carries "start
this channel" inside the one process:
  * nothing is lost and nothing is reordered, and "the status went live, then
    offline" arriving the other way round is a wrong dashboard;
  * its end IS the news that the other side died. A worker that reads
    end-of-file stops everything it runs; a web process that reads it starts
    another worker. No heartbeat has to be believed for that;
  * an answer has somewhere to come back to.

WHAT IT GUARANTEES, and what the callers above may lean on:
  * casts from one side are handled one at a time in the order they were
    sent. A slow handler holds up the casts behind it (as an `await` in the
    same task did before the split), never a call;
  * calls are handled as they arrive, each in its own task;
  * a handler that raises never ends the link: a call gets the error back, a
    cast is logged;
  * when the link ends, every call still waiting fails with ConnectionError
    at once rather than timing out one by one.

Messages are JSON objects on one line:
    {"k": "cast", "m": "broadcast", "a": [...]}
    {"k": "call", "id": 7, "m": "acquire_live_slot", "a": ["uid:channel"]}
    {"k": "ret",  "id": 7, "v": true}        or   {"k": "ret", "id": 7, "err": "..."}
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

# One message. A clip's chat snapshot is the biggest thing that crosses
# (tens of kilobytes); this is far above it and far below "something is
# writing garbage".
MAX_LINE = 8 * 1024 * 1024
CALL_TIMEOUT_S = 10.0
# Past this much unsent data the other side is not reading. A cast marked
# `droppable` (a score update, superseded a second later) is dropped rather
# than queued behind it; everything else still queues.
HIGH_WATER = 16 * 1024 * 1024

Handler = Callable[..., Any]


class LinkClosed(ConnectionError):
    """The other side is gone (or this side closed)."""


class RemoteError(RuntimeError):
    """A call's handler raised on the other side. The message is its repr."""


class Link:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *,
                 name: str, handlers: dict[str, Handler] | None = None) -> None:
        self._r, self._w = reader, writer
        self.name = name
        self.handlers: dict[str, Handler] = dict(handlers or {})
        self._next_id = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._dropped = 0

    # ── sending ──────────────────────────────────────────────────────────────

    @property
    def closed(self) -> bool:
        return self._closed

    def _write(self, msg: dict) -> None:
        if self._closed:
            raise LinkClosed(f"{self.name}: link closed")
        data = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_LINE:
            raise ValueError(f"{self.name}: message of {len(data)} bytes is over the {MAX_LINE} limit")
        try:
            self._w.write(data)
        except Exception as exc:                       # a transport already torn down
            raise LinkClosed(f"{self.name}: {exc}") from exc

    def cast(self, method: str, *args, droppable: bool = False) -> bool:
        """Say it and move on. False if it was not sent (link closed, or a
        droppable message while the other side is not reading). Never raises
        for a closed link: the callers are fire-and-forget by nature, and the
        link ending is reported once, by `run`, not by every cast after it."""
        if self._closed:
            return False
        if droppable:
            try:
                if self._w.transport.get_write_buffer_size() > HIGH_WATER:
                    self._dropped += 1
                    if self._dropped in (1, 100, 10_000):
                        log.warning("link_dropping", link=self.name, dropped=self._dropped)
                    return False
            except Exception:
                pass
        try:
            self._write({"k": "cast", "m": method, "a": list(args)})
            return True
        except LinkClosed:
            return False

    async def call(self, method: str, *args, timeout: float = CALL_TIMEOUT_S) -> Any:
        """Ask and wait. Raises LinkClosed, RemoteError or TimeoutError."""
        self._next_id += 1
        cid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[cid] = fut
        try:
            self._write({"k": "call", "id": cid, "m": method, "a": list(args)})
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{self.name}: no answer to {method} in {timeout:g}s") from None
        finally:
            self._waiting.pop(cid, None)

    # ── receiving ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Read until the other side closes. Returns then; never raises for
        anything the other side sent."""
        try:
            while True:
                try:
                    line = await self._r.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    # A line over the reader's limit cannot be skipped
                    # safely: where the next message starts is unknown.
                    log.error("link_line_too_long", link=self.name)
                    break
                except (ConnectionError, OSError):
                    break
                if not line:
                    break
                try:
                    msg = json.loads(line)
                    kind = msg["k"]
                except (ValueError, TypeError, KeyError):
                    log.warning("link_bad_message", link=self.name, size=len(line))
                    continue
                if kind == "ret":
                    fut = self._waiting.get(msg.get("id"))
                    if fut is not None and not fut.done():
                        if "err" in msg:
                            fut.set_exception(RemoteError(str(msg["err"])))
                        else:
                            fut.set_result(msg.get("v"))
                elif kind == "cast":
                    await self._handle_cast(msg)        # in order, one at a time
                elif kind == "call":
                    t = asyncio.create_task(self._handle_call(msg))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                else:
                    log.warning("link_unknown_kind", link=self.name, kind=str(kind)[:20])
        finally:
            self.close()

    async def _invoke(self, msg: dict) -> Any:
        fn = self.handlers.get(msg.get("m"))
        if fn is None:
            raise LookupError(f"no handler for {msg.get('m')!r}")
        out = fn(*(msg.get("a") or ()))
        if inspect.isawaitable(out):
            out = await out
        return out

    async def _handle_cast(self, msg: dict) -> None:
        try:
            await self._invoke(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("link_cast_failed", link=self.name, method=str(msg.get("m")),
                        error=repr(exc)[:300])

    async def _handle_call(self, msg: dict) -> None:
        reply: dict = {"k": "ret", "id": msg.get("id")}
        try:
            reply["v"] = await self._invoke(msg)
            json.dumps(reply["v"])                      # an answer that cannot cross is an error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reply.pop("v", None)
            reply["err"] = repr(exc)[:500]
        try:
            self._write(reply)
        except (LinkClosed, ValueError):
            pass

    # ── ending ───────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(LinkClosed(f"{self.name}: link closed"))
        self._waiting.clear()
        for t in list(self._tasks):
            t.cancel()
        try:
            self._w.close()
        except Exception:
            pass


async def open_link(sock, *, name: str, handlers: dict[str, Handler] | None = None) -> Link:
    """A Link over an already-connected socket (one end of a socketpair)."""
    reader, writer = await asyncio.open_connection(sock=sock, limit=MAX_LINE)
    return Link(reader, writer, name=name, handlers=handlers)


def owner_of(channel: str, count: int) -> int:
    """Which worker a channel belongs to: by CHANNEL NAME, not by user.

    The recorder, the shared audio buffer and the viewer-clip poll are one per
    channel inside a process; two users watching one streamer must land in the
    same worker or each of those would exist twice. crc32, not hash(): Python
    salts hash() per process, and both sides have to agree."""
    import zlib
    return zlib.crc32((channel or "").strip().lower().encode()) % max(1, int(count))


__all__ = ["Link", "LinkClosed", "RemoteError", "open_link", "owner_of",
           "MAX_LINE", "CALL_TIMEOUT_S", "Awaitable"]
