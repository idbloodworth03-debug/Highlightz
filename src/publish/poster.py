"""Posts a queued clip to every platform its owner has connected.

One item at a time, one platform at a time, on the event loop. That is
deliberate for a 1-vCPU box that is also scoring live streams: the uploads
are network-bound and stream from disk in 1 MB blocks, so they cost almost
no CPU, and running them serially means a burst of due posts cannot open
twenty upload sockets at once.

The outcome of every attempt is written to the item's `results` BEFORE the
next platform is tried, and each write is broadcast, so a tab watching the
card sees "Posting to YouTube… ✓ Posted · Posting to TikTok…" as it
happens, and a restart mid-item loses at most the platform in flight — which
is then simply retried, because a platform is only skipped once its result
says `posted`.

`notify` is how the poster reaches the sockets without importing the
dashboard (which imports the queue); api.py passes `broadcast`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import structlog

from src.publish import connections, providers, schedule as sched

log = structlog.get_logger(__name__)

Notify = Callable[[dict, str], Awaitable[None]]

# Items being posted right now, so a "Post now" click during the worker's
# pass (or two clicks) cannot upload the same clip twice.
_inflight: set[str] = set()


def auto_platforms(item: sched.Item) -> list[str]:
    """The platforms this item will be posted to by the server: chosen on
    the card AND connected AND not already posted."""
    connected = connections.connected_platforms(item.user_id)
    done = item.posted_on()
    return [p for p in item.platforms if p in connected and p not in done]


async def _say(notify: Notify | None, item: sched.Item) -> None:
    if notify is None:
        return
    try:
        await notify({"event": "schedule_updated", "item": item.public()}, item.user_id)
    except Exception as exc:                        # a dead socket is not a failed post
        log.warning("poster_notify_failed", error=str(exc))


async def post_item(item: sched.Item, notify: Notify | None = None) -> sched.Item | None:
    """Post one item to every platform it is due on. Returns the item as it
    stands afterwards, or None if there was nothing to do."""
    from src.uploads import library as upload_lib

    targets = auto_platforms(item)
    if not targets or item.id in _inflight:
        return None
    _inflight.add(item.id)
    uid = item.user_id
    try:
        up = upload_lib.get(item.upload_id, uid)
        path = upload_lib.path_for(up) if up else None
        if not path or not path.exists():
            for p in targets:
                item = sched.mark_result(item.id, uid, p, sched.R_FAILED,
                                         error="The clip's file is no longer on the server.")
            await _say(notify, item)
            return item
        size = path.stat().st_size

        for p in targets:
            provider = providers.get(p)
            conn = connections.get(uid, p)
            if provider is None or conn is None:
                continue
            item = sched.mark_result(item.id, uid, p, sched.R_POSTING)
            await _say(notify, item)
            try:
                await providers.ensure_fresh(provider, conn)
                res = await provider.post(conn, path, size, item.caption, item.fmt,
                                          item.duration_s)
                item = sched.mark_result(item.id, uid, p, sched.R_POSTED, url=res.url,
                                         remote_id=res.remote_id, note=res.note)
                log.info("post_published", item=item.id, platform=p, url=res.url)
            except providers.ProviderError as exc:
                item = sched.mark_result(item.id, uid, p, sched.R_FAILED,
                                         error=exc.message, retryable=exc.retryable)
                if exc.reauth:
                    connections.set_error(uid, p, exc.message)
                    if notify is not None:
                        await notify({"event": "publish_connections_changed"}, uid)
                log.warning("post_failed", item=item.id, platform=p, error=exc.message,
                            reauth=exc.reauth, retryable=exc.retryable)
            except Exception as exc:                # a bug must not stall the queue
                item = sched.mark_result(item.id, uid, p, sched.R_FAILED,
                                         error=f"Unexpected error: {exc}"[:300],
                                         retryable=True)
                log.exception("post_crashed", item=item.id, platform=p)
            await _say(notify, item)
        return item
    finally:
        _inflight.discard(item.id)


async def post_due(notify: Notify | None = None, now: float | None = None) -> int:
    """One pass of the worker: post everything that is due and has somewhere
    to go. Returns how many items were attempted."""
    n = 0
    for item in sched.due_for_posting(now):
        if not auto_platforms(item):
            continue
        if item.status == sched.FAILED and not _retry_due(item, now):
            continue
        await post_item(item, notify)
        n += 1
    return n


RETRY_AFTER_S = 15 * 60


def _retry_due(item: sched.Item, now: float | None) -> bool:
    """Whether a FAILED item gets another automatic go: only if every
    failure was the retryable kind (a quota, a 5xx — never "the caption is
    too long"), only every RETRY_AFTER_S so a used-up quota is not hammered
    every 30 seconds, and only inside the same grace window a reminder gets.
    Past that the card's Retry button is the way back."""
    now = time.time() if now is None else now
    failed = [r for r in item.results.values() if r.get("status") == sched.R_FAILED]
    if not failed or not all(r.get("retryable") for r in failed):
        return False
    if now > item.due_at + sched.GRACE_S:
        return False
    return now - max(r.get("at", 0) for r in failed) >= RETRY_AFTER_S


def start_now(item: sched.Item, notify: Notify | None = None) -> bool:
    """Kick a post off in the background for a "Post now" click. False if
    there is nothing to post (no connected platform left)."""
    if not auto_platforms(item) or item.id in _inflight:
        return False
    asyncio.create_task(post_item(item, notify), name=f"post-{item.id}")
    return True
