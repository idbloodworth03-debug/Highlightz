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

import os
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
            _forget_scan()
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


# ONE cached read of the clip store, answering both questions asked of it:
# which ids have a usable file, and how far back the oldest one goes.
#
# They were two caches with two TTLs and two invalidation paths, which meant
# two walks of the same directory and twice the chance of one going stale
# alone. The directory is read once; both answers come out of that read.
#
# The ROOT is part of the key, not decoration. Tests redirect `_ROOT` at a
# tmp_path, and a cache keyed on time alone carries one test's store into the
# next — the exact order-dependent failure tests/conftest.py exists to prevent.
#
# FIVE SECONDS. Short because a cut landing has to become downloadable on the
# next page load, and the socket event only reaches tabs that are already open.
# One scan of ~900 entries is about 1.5 ms, so even continuous traffic costs
# nothing measurable.
_scan: tuple[str, float, frozenset, float] = ("", 0.0, frozenset(), 0.0)
_SCAN_TTL_S = 5.0


def _read_store() -> tuple[frozenset, float]:
    """(ids with a usable file, mtime of the oldest file). Cached."""
    global _scan
    now = time.time()
    root = str(_ROOT)
    where, when, ids, oldest = _scan
    if where == root and when and now - when < _SCAN_TTL_S:
        return ids, oldest

    found, oldest = set(), 0.0
    try:
        with os.scandir(_ROOT) as it:
            for e in it:
                if not e.name.endswith(".mp4"):
                    continue
                try:
                    st = e.stat()
                    if not e.is_file():
                        continue
                except OSError:
                    continue
                # SIZE IS PART OF "EXISTS", and not incidentally. A zero-byte
                # file is a cut that failed halfway; calling it ready offers a
                # download that produces an empty MP4. scandir already carries
                # the size in the directory entry, so the check is free — which
                # is why this is not simply a listing of names.
                if st.st_size > 0:
                    found.add(e.name[:-4])
                if oldest == 0.0 or st.st_mtime < oldest:
                    oldest = st.st_mtime
    except OSError:
        return frozenset(), 0.0

    out = frozenset(found)
    # An EMPTY store is never cached. Reading an empty directory costs nothing,
    # so there is no saving to bank — and caching "no files" would hide the
    # first cut to land in a fresh store for the length of the TTL, which is
    # exactly the moment somebody is watching for it.
    if out or oldest:
        _scan = (root, now, out, oldest)
    return out, oldest


def existing_ids() -> frozenset:
    """Every clip id that has a usable file, from ONE directory read.

    WHY A LISTING AND NOT A STAT PER CLIP. `exists()` is right for one clip and
    wrong for a list: serialising a clip list called it once per record, which
    at production size is 1,606 is_file()+stat() pairs — about 3,500 syscalls
    and 340 ms per GET /clips, synchronously, on the event loop of a one-vCPU
    box that is also running an ffmpeg audio meter per channel. Reading the
    directory once and asking a set instead costs 1.5 ms for the read and
    0.07 ms for all 1,606 lookups.

    Deliberately NOT used by the file-serving endpoint. That one is about to
    hand over actual bytes, so it stats the real path and cannot be told by a
    cache that something is there when it is not.
    """
    return _read_store()[0]


def oldest_mtime() -> float:
    """When the oldest file we still hold was written, or 0 for an empty store.

    THE POINT OF KNOWING THIS is that it is the store's real retention horizon,
    and it is nothing like the configured one. `clip_file_max_age_days` is a
    30-day CEILING; what actually decides how long a file survives is the size
    cap and how fast clips arrive, which on a busy day is a couple of days. A
    clip older than this has no file and never will again — which is a
    different sentence to the user than "the buffer missed this moment",
    because it is a different thing that happened.
    """
    return _read_store()[1]


def _forget_scan() -> None:
    """Drop the cached read — called by anything that adds or removes a file,
    so a cut is downloadable on the next request and a trim does not leave a
    stale horizon behind it."""
    global _scan
    _scan = ("", 0.0, frozenset(), 0.0)


def headroom_ok() -> bool:
    """Whether another cut may be written.

    Checked before a cut rather than after, because the alternative is finding
    out the disk is full by filling it — and this disk is shared with the
    clip store, the user database and billing writes.
    """
    cap = settings.clip_file_max_total_mb * MB
    return not cap or total_bytes() < cap


# What `trim_to_cap` leaves behind, as a fraction of the cap. Not 100%: trimming
# to exactly the cap means the next cut is at the wall again and every single
# clip from then on pays for a trim.
_TRIM_TARGET = 0.9

# A file this new is never evicted, even to make room. Someone watching a clip
# land and reaching for Download is the whole point of the feature, and
# deleting it out from under them to store the next one trades a certain loss
# for a speculative gain.
_TRIM_MIN_AGE_S = 3600.0


def trim_to_cap() -> int:
    """Delete the oldest files until the store is back under the cap.

    WHY THIS EXISTS. `clip_file_max_total_mb` was a WALL: at the cap
    `headroom_ok` returned False and every subsequent cut was skipped, while
    `sweep` only ever freed space by AGE. So a store that filled before its
    files were 30 days old stayed full, and the product quietly stopped
    producing downloadable clips — with symptoms identical to the bug that
    prompted all of this, because the visible result is the same: no Download
    button, no explanation.

    Evicting the oldest is the right trade rather than a reluctant one. These
    files are a working area for getting a moment posted, not an archive — that
    is what the retention clock already says — and the clips people act on are
    the recent ones. Refusing new cuts instead protects month-old files nobody
    opened by breaking the feature for everybody.

    Returns the number of files removed.
    """
    cap = settings.clip_file_max_total_mb * MB
    if not cap:
        return 0
    try:
        entries = []
        for p in _ROOT.glob("*.mp4"):
            try:
                st = p.stat()
                entries.append((st.st_mtime, st.st_size, p))
            except OSError:
                continue
    except OSError:
        return 0

    total = sum(size for _, size, _ in entries)
    if total < cap:
        return 0

    target = cap * _TRIM_TARGET
    youngest_evictable = time.time() - _TRIM_MIN_AGE_S
    removed = 0
    for mtime, size, p in sorted(entries):          # oldest first
        if total <= target:
            break
        if mtime > youngest_evictable:
            # Sorted by age, so everything left is younger still. Stop rather
            # than continue: there is nothing further this can legally free.
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
        log.info("clip_file_swept", clip_id=p.stem, reason="over_cap")
    if removed:
        _forget_scan()
        log.info("clip_file_store_trimmed", removed=removed,
                 now_mb=round(total / MB), cap_mb=settings.clip_file_max_total_mb)
    elif total >= cap:
        # Worth saying: the store is full of files too new to evict, which is
        # the one case where cuts really do have to be skipped.
        log.warning("clip_file_store_full_of_new_files",
                    mb=round(total / MB), cap_mb=settings.clip_file_max_total_mb)
    return removed


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
    if removed:
        _forget_scan()
    return removed


def delete_all_for(clip_ids) -> int:
    """Remove the files for a set of clips — used when an account is deleted."""
    return sum(1 for cid in clip_ids if delete(cid))
