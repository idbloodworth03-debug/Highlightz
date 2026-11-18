"""What a stream worker needs from the web process, in ONE place.

THE SPLIT, stage 1 (docs/HANDOFF.md, "THE SPLIT"). A stream worker used to
reach into the dashboard module wherever it needed something: its record in
`_streams`, `broadcast`, a live slot, who else watches a channel, where to
hand a finished clip. That works only while both live in one process. Every
one of those crossings now goes through this module, so that:

  * in ONE process (INGEST_WORKERS unset, which is production today) each
    function here calls the dashboard exactly as the worker did before. No
    function here does anything of its own, and none of them yields to the
    loop where the direct call did not: `await host.acquire_live_slot(k)` runs
    the same synchronous check, at the same point, as
    `dashboard_api.acquire_live_slot(k)` did;
  * in an ingest worker process the dashboard cannot be imported at all
    (worker_main blocks it), and `_remote` is the client that sends each of
    these over the wire to the web process instead.

The dashboard is looked up at call time, not held: tests replace its
functions, and so does the dashboard itself in places.

IF YOU ARE ADDING A CROSSING: add it here, add the web-side handler when the
remote exists, and never import src.dashboard.api from anything under
src/ingestion, src/trigger or src/chat (tests/test_ingest_host.py fails if
you do).
"""

from __future__ import annotations

# Set in an ingest worker to the object that forwards these to the web
# process. None means "we are in the web process": call the dashboard.
_remote = None


def _api():
    from src.dashboard import api
    return api


def streams() -> dict:
    """The stream records, keyed "user_id:channel" (or "channel").

    In one process this IS the dashboard's dict: reading it reads the truth
    and setting `["status"]` on a record changes what every tab is told next.
    In a worker it is that worker's own mirror of its records; the web
    process learns of a change from the `stream_status` / `stream_updated`
    event the worker sends straight after, as it always has."""
    return _remote.streams() if _remote is not None else _api()._streams


def set_status(stream_key: str, status: str) -> bool:
    """A worker reporting what its channel is doing: starting, live, offline,
    reconnecting, queued. True if the record now says so.

    A CHANNEL THE DASHBOARD PAUSED STAYS PAUSED. Pausing sets the record to
    "paused" and THEN stops the worker, and a worker on its way out used to
    write "offline" over it (found 2026-11-18, reading this path for the
    split; reproduced). The record then said "offline" with no worker
    running, so the owner's next visit found nothing marked paused to
    restart: the channel stayed dead until the next idle pass re-paused it,
    or the next deploy. "paused" is the dashboard's word, not the worker's,
    and no worker report may replace it. Coming back sets "starting" first,
    which a worker may then replace as before."""
    rec = streams().get(stream_key)
    if rec is None or rec.get("status") == "paused":
        return False
    rec["status"] = status
    return True


async def broadcast(event: dict, user_id: str | None = None) -> None:
    if _remote is not None:
        return await _remote.broadcast(event, user_id=user_id)
    await _api().broadcast(event, user_id=user_id)


async def acquire_live_slot(stream_key: str) -> bool:
    """May this channel go live now? False means wait (queued) and ask again."""
    if _remote is not None:
        return bool(await _remote.acquire_live_slot(stream_key))
    return _api().acquire_live_slot(stream_key)


def release_live_slot(stream_key: str) -> None:
    """Unconditional and idempotent, as the dashboard's is: called on every
    way out of a session, including ones that never took a slot."""
    if _remote is not None:
        return _remote.release_live_slot(stream_key)
    _api().release_live_slot(stream_key)


async def watchers_of(channel: str) -> list[str]:
    if _remote is not None:
        return list(await _remote.watchers_of(channel))
    return _api().watchers_of(channel)


async def suggestion_room(uid: str) -> tuple[int, int]:
    if _remote is not None:
        used, room = await _remote.suggestion_room(uid)
        return used, room
    return _api().suggestion_room(uid)


async def trim_clip_store() -> int:
    """Make room in the clip store; bytes freed. ONLY THE WEB PROCESS TRIMS.

    The trim decides what to delete from what the dashboard knows: approved
    clips go last, a Studio account's are kept, free accounts' go first
    (clips.files.protected_ids / kept_ids / eviction_ranks, all read from the
    dashboard's clip records). A worker process cannot see those records, and
    files.py falls back to "evict by age alone" when it cannot, which in a
    worker would delete approved videos. So a worker asks; it never trims.
    In one process this is the same call in the same thread as before."""
    if _remote is not None:
        return int(await _remote.trim_clip_store() or 0)
    import asyncio
    from src.clips import files as clip_files
    return await asyncio.to_thread(clip_files.trim_to_cap)


async def notify_clip_ready(meta: dict):
    if _remote is not None:
        return await _remote.notify_clip_ready(meta)
    return await _api().notify_clip_ready(meta)


async def on_clip_file_ready(clip_id: str):
    if _remote is not None:
        return await _remote.on_clip_file_ready(clip_id)
    return await _api().on_clip_file_ready(clip_id)
