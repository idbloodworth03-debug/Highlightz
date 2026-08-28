"""Crowd suggestions the user has already said no to.

THE BUG THIS EXISTS TO FIX. A suggested clip that was rejected, deleted or
cleared came back and landed in the queue again.

WHY IT CAME BACK. Suggestions are not clips we create — they are viewer clips
that already exist on Twitch, and the Get Clips poll keeps returning them for
`viewer_clips._WINDOW_SECS` (7 minutes) after they are made. Two things stopped
one being suggested twice, and neither survived the user dismissing it:

  1. `SuggestionBuffer._emitted`, an in-memory set of slugs already emitted.
     Per process. A deploy restarts the service, so it is empty again.
  2. `_our_clip_identity()`, which reads every `twitch_clip_id` out of
     clips.json and excludes them from intake. A landed suggestion is stored
     with the viewer's slug, so this covered it — RIGHT UP UNTIL the user
     removed the clip, which is the one action that deletes the row.

So the durable guard was the clip record itself, and dismissing a suggestion
deleted the only evidence that it had ever been suggested. The reappearance was
not random: clearing the queue is what caused it.

WHAT THIS RECORDS. A tombstone: the user said no to this moment. It is
deliberately the smallest thing that answers the question — no clip, no
metadata, nothing about the viewer who made it.

PER USER, NOT PER CHANNEL. The suggestion buffer is shared by every worker
watching a channel, because five users on one streamer must cost one Helix
poll. Dismissal is not shareable that way: one user clearing their queue must
not silently suppress a suggestion another user has never seen. So the check
happens where the user is known — when a suggestion is landed for a specific
account — rather than inside the shared buffer.

BY MOMENT, NOT ONLY BY SLUG. Several viewers clip the same moment and each gets
a different slug, which is why the in-memory guard already tracked emitted
moments as well as emitted slugs. Suppressing only the exact slug would let the
same 30 seconds come back under a neighbour's clip and look, to the user,
exactly like the bug they reported.

IT MUST BE REVERSIBLE. "Gone unless it comes back from undo" is the actual
requirement, so undo clears the tombstone. Nothing else does: when an undo
entry simply expires, the dismissal was real and stands.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json

log = structlog.get_logger(__name__)

_FILE = Path(settings.local_storage_path) / "dismissed_suggestions.json"

# Two clips this close together are the same moment. Mirrors
# suggested_clips.CLUSTER_SECS, which is the definition the in-memory guard
# already used; imported rather than restated so the two cannot drift.
from src.trigger.suggested_clips import CLUSTER_SECS

# How long a tombstone is kept. A clip can only be re-offered while it is still
# inside the poll's 7-minute lookback, so anything beyond an hour is already
# unreachable — a day is generous cover for clock skew and a long ripening, and
# keeps the file from growing across a channel's whole broadcast history.
RETENTION_SECS = 24 * 60 * 60


def _load() -> dict:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        log.warning("dismissed_suggestions_corrupt", path=str(_FILE))
        return {}


def _save(data: dict) -> None:
    try:
        atomic_write_json(_FILE, data)
    except Exception as exc:
        # Failing to write a tombstone means a suggestion may reappear. That is
        # the bug this fixes, but it is still not worth taking the clip
        # pipeline down over — log it and carry on.
        log.warning("dismissed_suggestions_save_failed", error=str(exc))


def _prune(rows: list[dict], now: float) -> list[dict]:
    return [r for r in rows if now - float(r.get("at", 0)) < RETENTION_SECS]


def _key(clip: dict) -> tuple[str, str, float] | None:
    """(channel, slug, moment) for a clip worth remembering, else None.

    ONLY SUGGESTIONS. A clip we created ourselves is never offered back to us —
    it is excluded by creator id — so a tombstone for one would be dead weight
    that says nothing. `suggested` is the flag that distinguishes them.
    """
    if not clip.get("suggested"):
        return None
    slug = str(clip.get("twitch_clip_id") or "")
    if not slug:
        return None
    return (str(clip.get("channel") or "").lower(), slug,
            float(clip.get("created_at") or 0.0))


def dismiss(user_id: str, clips: list[dict]) -> int:
    """Remember that `user_id` removed these suggestions. Returns how many."""
    if not user_id:
        return 0
    keys = [k for k in (_key(c) for c in clips or []) if k]
    if not keys:
        return 0
    now = time.time()
    data = _load()
    rows = _prune(list(data.get(user_id) or []), now)
    have = {r.get("slug") for r in rows}
    added = 0
    for channel, slug, moment in keys:
        if slug in have:
            continue
        rows.append({"slug": slug, "channel": channel, "moment": moment,
                     "at": now})
        have.add(slug)
        added += 1
    data[user_id] = rows
    _save(data)
    return added


def forget(user_id: str, clips: list[dict]) -> int:
    """Undo: the user took the dismissal back, so the tombstone must go.

    Without this, restoring a cleared suggestion would put the clip back while
    permanently blocking the moment — the user would have their clip and no
    idea why nothing like it ever arrived again.
    """
    if not user_id:
        return 0
    slugs = {k[1] for k in (_key(c) for c in clips or []) if k}
    if not slugs:
        return 0
    data = _load()
    rows = list(data.get(user_id) or [])
    kept = [r for r in rows if r.get("slug") not in slugs]
    if len(kept) == len(rows):
        return 0
    data[user_id] = kept
    _save(data)
    return len(rows) - len(kept)


def is_dismissed(user_id: str, channel: str, slug: str,
                 moment: float, now: float | None = None) -> bool:
    """Has this user already said no to this moment on this channel?"""
    if not user_id:
        return False
    now = time.time() if now is None else now
    chan = (channel or "").lower()
    for r in _prune(list(_load().get(user_id) or []), now):
        if r.get("slug") == slug:
            return True
        # Same moment under a different viewer's slug. Scoped to the channel:
        # two streams can genuinely have a moment at the same instant, and
        # suppressing across channels would drop a suggestion the user never
        # saw, which is a worse failure than showing one twice.
        if r.get("channel") == chan and moment and r.get("moment") and \
                abs(moment - float(r["moment"])) <= CLUSTER_SECS:
            return True
    return False


def count_for(user_id: str) -> int:      # pragma: no cover - diagnostics
    return len(_prune(list(_load().get(user_id) or []), time.time()))
