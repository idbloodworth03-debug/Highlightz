"""A short-lived undo buffer for the destructive things a user can do to clips.

WHY. Reject, Cull and Clear queue all destroy work in one click, and two of
them act in bulk. Rejecting is worse than it looks: as well as removing the
clip it teaches the channel's profile that the moment was bad, raising the
trigger threshold and trimming the weights of whichever signals fired. An
accidental bulk reject therefore does not just lose clips, it makes the
detector worse on that channel — and nothing in the product could take it back.

DESIGN NOTES

  * SNAPSHOT, DO NOT INVERT. record_clip() clamps the threshold to a floor and
    a ceiling and clamps each weight into [0.75, 1.5], so subtracting the step
    back off does not always return you to where you were. The profile's
    mutable scoring state is copied before the nudge and written back verbatim
    on undo, which is exact by construction.
  * FILE DELETION IS DEFERRED, NOT SKIPPED. A clip with a local .mp4 (uploads,
    processed VOD moments) used to have the file unlinked the instant it was
    rejected, which would make undo restore a record pointing at nothing. Files
    are held until the entry falls out of the buffer, then deleted.
  * IN MEMORY, DELIBERATELY. Undo is a few-seconds-after-the-fact affordance,
    not a trash can. Persisting it would mean a restart could resurrect clips a
    user rejected an hour ago, which is a worse surprise than losing the undo.
  * BOUNDED BOTH WAYS. Entries expire on time and the stack is capped, so a
    user culling all afternoon cannot pin thousands of clip records in memory.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

# How long an action stays undoable, and how many are remembered per user.
TTL_SECONDS  = 600      # 10 minutes
MAX_PER_USER = 20


@dataclass
class UndoEntry:
    user_id: str
    kind: str                        # reject | delete | cull | clear
    label: str                       # what the toast says
    clips: list[dict]
    # {channel: {threshold, weights, totals}} taken BEFORE record_clip ran.
    profiles_before: dict[str, dict] = field(default_factory=dict)
    # storage_urls whose files are held back until this entry expires.
    held_files: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    at: float = field(default_factory=time.time)

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) - self.at > TTL_SECONDS

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "clips": len(self.clips), "at": self.at,
                "expires_at": self.at + TTL_SECONDS}


_stacks: dict[str, list[UndoEntry]] = {}


def _evict(user_id: str, on_drop=None) -> None:
    """Drop expired and over-cap entries, handing each to `on_drop` first.

    on_drop is where held files finally get deleted — the buffer is the only
    thing that knows an entry is past saving.
    """
    stack = _stacks.get(user_id) or []
    keep: list[UndoEntry] = []
    dropped: list[UndoEntry] = []
    for e in stack:
        (dropped if e.expired() else keep).append(e)
    while len(keep) > MAX_PER_USER:
        dropped.append(keep.pop(0))          # oldest first
    _stacks[user_id] = keep
    if on_drop:
        for e in dropped:
            try:
                on_drop(e)
            except Exception as exc:          # never let cleanup break a request
                log.warning("undo_drop_failed", entry=e.id, error=str(exc))


def push(entry: UndoEntry, on_drop=None) -> UndoEntry:
    _stacks.setdefault(entry.user_id, []).append(entry)
    _evict(entry.user_id, on_drop)
    log.info("undo_recorded", user_id=entry.user_id, kind=entry.kind,
             clips=len(entry.clips), entry=entry.id)
    return entry


def peek(user_id: str, on_drop=None) -> UndoEntry | None:
    """The most recent still-undoable action, or None."""
    _evict(user_id, on_drop)
    stack = _stacks.get(user_id) or []
    return stack[-1] if stack else None


def pop(user_id: str, entry_id: str | None = None, on_drop=None) -> UndoEntry | None:
    """Take an entry off the stack so it can be applied.

    `entry_id` guards against undoing the wrong thing: a user who clicks a
    stale toast after doing something else should get nothing, not a surprise
    restore of an action they had moved on from.
    """
    _evict(user_id, on_drop)
    stack = _stacks.get(user_id) or []
    if not stack:
        return None
    if entry_id is not None and stack[-1].id != entry_id:
        return None
    return stack.pop()


def clear(user_id: str) -> None:
    _stacks.pop(user_id, None)


def snapshot_profile(profile) -> dict:
    """The mutable scoring state record_clip() touches, and nothing else."""
    return {
        "trigger_threshold": profile.trigger_threshold,
        "signal_weights":    dict(profile.signal_weights),
        "total_clips":       profile.total_clips,
        "approved_clips":    profile.approved_clips,
        "rejected_clips":    profile.rejected_clips,
    }


def restore_profile(profile, snap: dict) -> None:
    profile.trigger_threshold = snap["trigger_threshold"]
    profile.signal_weights    = dict(snap["signal_weights"])
    profile.total_clips       = snap["total_clips"]
    profile.approved_clips    = snap["approved_clips"]
    profile.rejected_clips    = snap["rejected_clips"]
