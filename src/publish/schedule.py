"""A posting queue that REMINDS. It does not post.

This is the honest shape of a scheduler when the app deliberately does not hold
platform credentials (see platforms.py for why that trade was made). At the
scheduled time the user still taps share — what this removes is the part
creators actually lose track of: which clip was meant to go out, when, and with
what caption.

Everything user-facing must say so. A queue that looks like automation and
silently isn't would cost someone a posting slot they were counting on, which
is worse than not having the feature.

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

PENDING, POSTED, SKIPPED = "pending", "posted", "skipped"
_STATUSES = (PENDING, POSTED, SKIPPED)

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
        duration_s: float = 0.0, ratio: str = "", fmt: str = "") -> Item:
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
                fmt=str(fmt).lower().lstrip("."))
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
