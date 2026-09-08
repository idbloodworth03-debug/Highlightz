"""
Clip files — the video Highlightz cut for a clip, on our disk.

WHERE THIS SITS. `src/uploads/library.py` holds files the USER handed us.
This holds files WE produced, by cutting a moment out of the rolling capture
buffer (`src/ingestion/clip_recorder.py`). They are kept apart because their
lifetimes and their justifications differ: an upload belongs to the user until
they delete it, while a cut clip is a working copy that exists so the moment
can be downloaded, edited and posted, and is swept on a retention clock.

THE ADDRESSING TRICK, and why it matters. A file is named by the clip id and
nothing else. That id is minted on the ClipJob before the job is queued, which
means the worker that owns the capture buffer knows the final name before the
clip record exists — so the worker can write the file and the dashboard can
find it later by asking the filesystem, with no message passing between them.
That matters because clip jobs cross a Redis queue and may be processed in
another process entirely; a design that needed the two halves to talk would
have to invent a channel that does not exist.

The id is still validated before it becomes a path. It is ours today, but
"this value is trusted because of where it comes from" is exactly the
assumption that stops being true after a refactor, and the cost of the check
is one regex.
"""

import re
import time
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_ROOT = Path(settings.local_storage_path) / "clipfiles"

# Clip ids are UUID4 today. The pattern is deliberately a little wider than
# that (ids from older records and from the VOD scanner differ in shape) but
# still admits nothing that can traverse or escape.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

MB = 1024 * 1024


def root() -> Path:
    return _ROOT


def path_for(clip_id: str) -> Path | None:
    """Where a clip's file lives, or None if the id is not a safe name."""
    if not clip_id or not _ID_RE.match(clip_id):
        return None
    return _ROOT / f"{clip_id}.mp4"


def exists(clip_id: str) -> bool:
    p = path_for(clip_id)
    try:
        return bool(p and p.is_file() and p.stat().st_size > 0)
    except OSError:
        return False


def size_of(clip_id: str) -> int:
    p = path_for(clip_id)
    try:
        return p.stat().st_size if p and p.is_file() else 0
    except OSError:
        return 0


def delete(clip_id: str) -> bool:
    """Remove a clip's file. Safe to call for a clip that never had one."""
    p = path_for(clip_id)
    if not p:
        return False
    try:
        if p.is_file():
            p.unlink()
            log.info("clip_file_deleted", clip_id=clip_id)
            return True
    except OSError as exc:
        log.warning("clip_file_delete_failed", clip_id=clip_id, error=str(exc))
    return False


def total_bytes() -> int:
    total = 0
    try:
        for p in _ROOT.glob("*.mp4"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def headroom_ok() -> bool:
    """Whether another cut may be written.

    Checked before a cut rather than after, because the alternative is finding
    out the disk is full by filling it — and this disk is shared with the
    clip store, the user database and billing writes.
    """
    cap = settings.clip_file_max_total_mb * MB
    return not cap or total_bytes() < cap


def sweep(live_ids: set[str] | None = None) -> int:
    """Delete files that are too old, or that no clip record points at.

    Two jobs in one pass because they share a directory listing:

    * **Age.** Clip files are a working area for getting a moment posted, not
      an archive. `clip_file_max_age_days` is the ceiling.
    * **Orphans.** A file whose clip record is gone is disk nothing can ever
      free through the UI — the record was the only handle on it. Callers pass
      the set of ids that still exist; passing None skips this half, so a
      caller that cannot enumerate records safely does no harm.

    Returns the number of files removed.
    """
    cutoff = time.time() - settings.clip_file_max_age_days * 86400
    removed = 0
    try:
        entries = list(_ROOT.glob("*.mp4"))
    except OSError:
        return 0
    for p in entries:
        try:
            clip_id = p.stem
            too_old = p.stat().st_mtime < cutoff
            orphan = live_ids is not None and clip_id not in live_ids
            if too_old or orphan:
                p.unlink()
                removed += 1
                log.info("clip_file_swept", clip_id=clip_id,
                         reason="age" if too_old else "orphan")
        except OSError:
            continue
    return removed


def delete_all_for(clip_ids) -> int:
    """Remove the files for a set of clips — used when an account is deleted."""
    return sum(1 for cid in clip_ids if delete(cid))
