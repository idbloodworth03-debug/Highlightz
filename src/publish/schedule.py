"""The posting queue. Since 2026-09-15 it POSTS.

Every export lands here. For a platform the user has CONNECTED (see
connections.py) the server uploads the clip itself at the scheduled time —
src/publish/poster.py does the work and writes the outcome into `results`,
one entry per platform. For a platform they have not connected the queue
still does what it did before that decision: reminds them, and hands them
the file and a one-tap share. Both halves live on the same card, and the UI
has to say which is which — a queue that looks automatic where it is not
costs someone a posting slot, and one that looks manual where it is not
posts something they did not expect.

Design notes worth keeping:
  * Times are epoch seconds, UTC. The browser converts for display. Storing
    local times means a user who travels, or a server whose TZ changes, gets
    posts due at the wrong hour with no way to tell what was meant.
  * `due` is DERIVED from the clock, never stored. A stored "is due" flag goes
    wrong the moment a process restarts, the clock steps, or an item is edited.
  * Items are scoped by user_id at the store level, so another user's id reads
    as missing rather than as forbidden.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field  # asdict re-exported for tests
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "schedule.json"

PENDING, POSTING, POSTED, FAILED, SKIPPED = "pending", "posting", "posted", "failed", "skipped"
_STATUSES = (PENDING, POSTING, POSTED, FAILED, SKIPPED)
# Per-platform outcome states inside Item.results.
R_POSTING, R_POSTED, R_FAILED = "posting", "posted", "failed"

MAX_PER_USER = 200          # a queue, not an archive
CAPTION_MAX = 2200          # the most permissive platform limit
# How long a pending item stays visible after its time passes before it is
# treated as missed. Creators are not at their desk at the second it fires.
GRACE_S = 24 * 3600


@dataclass
class Item:
    id: str
    user_id: str
    upload_id: str
    filename: str
    caption: str
    platforms: list[str]
    # 0 means "no time picked yet". Every exported clip lands here, and most of
    # them arrive before their owner has decided when to post — forcing a time
    # at export would make the queue a chore instead of an inbox.
    due_at: float = 0.0
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    notified: bool = False      # has the "it's time" event been sent yet
    # Shape of the RENDER, captured at export. Stored so the scheduler can
    # fit-check against each platform without downloading the video to measure
    # it — the whole list would otherwise pull every file on every render.
    duration_s: float = 0.0
    ratio: str = ""
    # Container the render actually came out as. MediaRecorder cannot
    # always make MP4, and a WebM is refused outright by TikTok and
    # Instagram — the fit check needs this or it reports "Fits" on a file
    # that cannot be posted at all.
    fmt: str = ""
    # What happened on each platform the poster tried:
    #   {"youtube": {"status": "posted", "url": ..., "remote_id": ..., "at": ...,
    #                "note": ...},
    #    "tiktok":  {"status": "failed", "error": ..., "retryable": bool, "at": ...}}
    # A platform that is in `platforms` but not here has not been attempted
    # (not connected, or not due yet). The item's own `status` summarises:
    # posting while any platform is in flight, posted when every attempted
    # one landed, failed when any did not.
    results: dict = field(default_factory=dict)
    # Who put it here: "" for an export from the editor, "autopilot" for a
    # clip the server rendered and queued by itself. Autopilot reads its own
    # last due time from this to space posts out.
    source: str = ""

    def public(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        d = asdict(self)
        # All derived, never stored — see module docstring.
        scheduled = self.due_at > 0
        d["scheduled"] = scheduled
        d["due"] = scheduled and self.status == PENDING and now >= self.due_at
        d["missed"] = (scheduled and self.status == PENDING
                       and now >= self.due_at + GRACE_S)
        return d

    def posted_on(self) -> set[str]:
        return {p for p, r in self.results.items() if r.get("status") == R_POSTED}


_items: dict[str, Item] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not _INDEX.exists():
        return
    try:
        raw = json.loads(_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("schedule_index_unreadable", path=str(_INDEX))
        return
    for r in raw:
        try:
            _items[r["id"]] = Item(**r)
        except (TypeError, KeyError):
            continue        # a shape change must not take the whole queue down


def _save() -> None:
    _INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(i) for i in _items.values()], indent=2),
                   encoding="utf-8")
    os.replace(tmp, _INDEX)      # atomic: never leave a half-written queue


def add(user_id: str, upload_id: str, filename: str, caption: str,
        platforms: list[str], due_at: float = 0.0,
        duration_s: float = 0.0, ratio: str = "", fmt: str = "",
        source: str = "") -> Item:
    _load()
    if due_at < 0:
        raise ValueError("Pick a time for this post.")
    if len(caption) > CAPTION_MAX:
        raise ValueError(f"Caption is longer than {CAPTION_MAX} characters.")
    mine = [i for i in _items.values() if i.user_id == user_id]
    if len(mine) >= MAX_PER_USER:
        raise ValueError(
            f"You already have {MAX_PER_USER} posts queued — clear some first.")

    item = Item(id=uuid.uuid4().hex, user_id=user_id, upload_id=upload_id,
                filename=filename, caption=caption,
                platforms=list(platforms), due_at=float(due_at),
                duration_s=float(duration_s), ratio=str(ratio),
                fmt=str(fmt).lower().lstrip("."), source=str(source or ""))
    _items[item.id] = item
    _save()
    return item


def for_user(user_id: str) -> list[Item]:
    _load()
    # Scheduled items first in time order, then undated ones newest-first.
    # Sorting on due_at alone would pin every undated clip (0) above posts that
    # actually have a time.
    return sorted((i for i in _items.values() if i.user_id == user_id),
                  key=lambda i: (i.due_at <= 0, i.due_at, -i.created_at))


def last_due_from(user_id: str, source: str) -> float:
    """The latest due time among this user's items from `source` (0 if
    none). Autopilot spaces its next post after this, so a run of approvals
    in one evening lands as a run of posts over the coming days."""
    _load()
    return max((i.due_at for i in _items.values()
                if i.user_id == user_id and i.source == source and i.due_at > 0),
               default=0.0)


def get(item_id: str, user_id: str) -> Item | None:
    """Scoped by owner: another user's id is indistinguishable from a
    nonexistent one, so this cannot be used to probe for other people's."""
    _load()
    item = _items.get(item_id)
    return item if item and item.user_id == user_id else None


def set_status(item_id: str, user_id: str, status: str) -> Item | None:
    if status not in _STATUSES:
        raise ValueError(f"Unknown status {status!r}")
    item = get(item_id, user_id)
    if not item:
        return None
    item.status = status
    _save()
    return item


def update(item_id: str, user_id: str, *, caption: str | None = None,
           due_at: float | None = None,
           platforms: list[str] | None = None) -> Item | None:
    """Edit a queued post. Re-arming the time clears `notified` so a
    rescheduled item is announced again — otherwise moving a missed post to
    tomorrow would silently never nudge."""
    item = get(item_id, user_id)
    if not item:
        return None
    if caption is not None:
        if len(caption) > CAPTION_MAX:
            raise ValueError(f"Caption is longer than {CAPTION_MAX} characters.")
        item.caption = caption
    if platforms is not None:
        item.platforms = list(platforms)
    if due_at is not None:
        if due_at < 0:
            raise ValueError("Pick a time for this post.")
        if due_at != item.due_at:
            item.notified = False
        item.due_at = float(due_at)
    _save()
    return item


def remove(item_id: str, user_id: str) -> bool:
    item = get(item_id, user_id)
    if not item:
        return False
    _items.pop(item_id, None)
    _save()
    return True


def delete_all_for_user(user_id: str) -> int:
    """Account deletion has to take the queue with it."""
    _load()
    gone = [i.id for i in _items.values() if i.user_id == user_id]
    for i in gone:
        _items.pop(i, None)
    if gone:
        _save()
    return len(gone)


def drop_upload(upload_id: str, user_id: str) -> list[str]:
    """A queued post whose clip has been deleted can never be posted. Leaving
    it in the list is a reminder to do something impossible."""
    _load()
    gone = [i.id for i in _items.values()
            if i.user_id == user_id and i.upload_id == upload_id]
    for i in gone:
        _items.pop(i, None)
    if gone:
        _save()
    return gone


def due_for_posting(now: float | None = None) -> list[Item]:
    """Pending, timed items whose time has come. The poster decides which of
    them have a connected platform to post to; the rest are reminders and go
    through newly_due() as before. Oldest first, so a backlog after a restart
    drains in the order it was meant to go out."""
    _load()
    now = time.time() if now is None else now
    # FAILED items are included so a retryable failure (quota, 5xx) gets
    # another go; the poster applies the backoff and the retryable check.
    return sorted((i for i in _items.values()
                   if i.status in (PENDING, FAILED) and i.due_at > 0 and now >= i.due_at),
                  key=lambda i: i.due_at)


def mark_result(item_id: str, user_id: str, platform: str, status: str, *,
                url: str = "", remote_id: str = "", error: str = "",
                note: str = "", retryable: bool = False) -> Item | None:
    """Record one platform's outcome and re-derive the item's own status.

    The item is `posting` while any platform is in flight, `failed` if any
    attempted platform failed, `posted` once every attempted platform landed,
    and back to `pending` if nothing has been attempted at all (a retry that
    cleared the results). Kept in one place so the summary can never
    disagree with the per-platform rows the card shows.
    """
    item = get(item_id, user_id)
    if not item:
        return None
    row = {"status": status, "at": time.time()}
    if status == R_POSTED:
        row.update({"url": url, "remote_id": remote_id, "note": note})
    elif status == R_FAILED:
        row.update({"error": error[:300], "retryable": bool(retryable)})
    item.results[platform] = row
    states = {r.get("status") for r in item.results.values()}
    if R_POSTING in states:
        item.status = POSTING
    elif R_FAILED in states:
        item.status = FAILED
    elif states:
        item.status = POSTED
    else:
        item.status = PENDING
    _save()
    return item


def reclaim_posting_orphans() -> list[Item]:
    """Rescue items left mid-post by a restart. Call once at startup.

    THE TRAP THIS EXISTS TO CLOSE. `posting` is a claim about a coroutine that
    lives in this process, and a deploy kills every one of them — but the
    status is on disk and survives. Afterwards the item is stranded in a way
    nothing could undo: `due_for_posting` only returns pending and failed, so
    the worker never looks at it again, and the Retry endpoint refuses a
    `posting` item with 409 "Already posting". The card's chip pulses at 1 Hz
    forever (.sc-chip.posting) and the user reads it as the app buffering.
    Found exactly that way on 2026-09-18, after a deploy landed inside
    TikTok's three-minute publish poll.

    At startup no post CAN be in flight — the tasks died with the process — so
    every `posting` row here is orphaned by definition and there is nothing to
    race with. Marked failed and RETRYABLE: the bytes may well have reached
    the platform, so this says "we lost track", not "it did not work", and
    both the automatic retry and the Retry button take it from here.

    No broadcast: there are no sockets yet. Reconnecting tabs pick the change
    up through refetchAll(), which is what that path is for.
    """
    _load()
    changed = []
    for item in _items.values():
        if item.status != POSTING:
            continue
        for p, r in item.results.items():
            if r.get("status") == R_POSTING:
                item.results[p] = {
                    "status": R_FAILED, "at": time.time(), "retryable": True,
                    "error": "The server restarted while this was posting, so "
                             "we lost track of it. Check the account before "
                             "retrying, in case it went out.",
                }
        states = {r.get("status") for r in item.results.values()}
        item.status = (FAILED if R_FAILED in states else
                       POSTED if states else PENDING)
        changed.append(item)
    if changed:
        _save()
        log.warning("schedule_posting_orphans_reclaimed", count=len(changed))
    return changed


def reset_for_retry(item_id: str, user_id: str) -> Item | None:
    """Forget the failures, keep the successes, and make the item pending
    again so the poster picks it up. A platform that already took the clip is
    never posted to twice — that is what the successes are kept for."""
    item = get(item_id, user_id)
    if not item:
        return None
    item.results = {p: r for p, r in item.results.items() if r.get("status") == R_POSTED}
    item.status = PENDING
    _save()
    return item


def newly_due(now: float | None = None) -> list[Item]:
    """Pending items whose time has come and which have not been announced yet.

    Marks them notified as it returns them, so the caller can broadcast exactly
    once per item. If the process dies between marking and broadcasting the
    reminder is lost for that item — the UI still shows it as due on the next
    fetch, which is why the list is the source of truth and the event is only
    a nudge.
    """
    _load()
    now = time.time() if now is None else now
    out = [i for i in _items.values()
           if i.status == PENDING and not i.notified
           and i.due_at > 0 and now >= i.due_at]
    if out:
        for i in out:
            i.notified = True
        _save()
    return out
