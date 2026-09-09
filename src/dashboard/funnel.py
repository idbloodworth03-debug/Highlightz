"""
The signup funnel, counted rather than tracked.

THE QUESTION THIS ANSWERS. "Nobody is signing up" has several causes that look
identical from the outside: nobody arrives, they arrive and leave, they click
sign-in and abandon Twitch's permission screen, or they finish signing up and
never add a channel. Those need opposite fixes, and until something counts the
steps the choice between them is a guess.

WHY THERE IS NO VISITOR ID, AND WHAT THAT COSTS. The obvious design gives each
visitor a cookie and follows them through. This does not, because the published
Cookie Policy says Highlightz sets exactly one cookie, and quietly adding a
tracking cookie would make a public document false — the same class of problem
as the recording copy. So this holds COUNTS PER DAY and nothing else: no ids, no
addresses, no fingerprints, nothing that is about a person.

The cost is real and worth stating plainly: "40 landings and 5 signups today" is
not "5 of those 40 people signed up". Same-day cohorts drift, someone can land
on Monday and sign up on Friday, and one person reloading the page ten times is
ten landings. It measures SHAPE, not individuals. That is enough to tell an
empty top of funnel from a leaky middle, which is the decision actually waiting
on it, and it is the most you can honestly get without identifying people.

THREE THINGS THAT KEEP IT CHEAP AND SAFE.

* **A pageview is not a disk write.** Counts accumulate in memory and are
  flushed at most once every `_FLUSH_EVERY_S`. The box is a 1 vCPU droplet
  already running eleven workers and an ffmpeg per channel; fsync-per-request
  is not a thing it can afford.
* **Tracking can never break a request.** Every entry point is wrapped, and a
  failure here is logged and swallowed. An analytics bug taking down the
  landing page would be a far worse outcome than losing a count.
* **It cannot grow forever.** Days older than `_KEEP_DAYS` are dropped on
  flush, so the file has a ceiling instead of being one more thing that
  silently fills the disk.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_PATH = Path(settings.local_storage_path) / "funnel.json"

# Order matters: this is the order the admin page renders, and it is the order
# somebody actually moves through the product. Each entry is (key, label, help).
STEPS: tuple[tuple[str, str, str], ...] = (
    ("landing",       "Landing page",     "Signed-out visits to the front page."),
    ("login_view",    "Sign-in page",     "Reached /login. They are looking for the door."),
    ("oauth_start",   "Clicked Twitch",   "Sent to Twitch's permission screen. Everything after this is out of our hands until they come back."),
    ("oauth_return",  "Came back",        "Twitch sent them back to us. The gap above this is people who saw the permission screen and backed out."),
    ("signup",        "New account",      "A Twitch id we had never seen before."),
    ("returning",     "Returning sign-in", "An existing account signing in again. Not part of the funnel — shown so 'came back' adds up."),
    ("first_channel", "Added a channel",  "Their first channel ever. Before this the product does nothing for them."),
    ("first_clip",    "Got a clip",       "The first clip that account ever received. This is the moment the product has actually delivered."),
)

_KEYS = {k for k, _l, _h in STEPS}

# How long counts are kept. Long enough to see a month-over-month change, short
# enough that the file stays small forever.
_KEEP_DAYS = 180

# A pageview must not be an fsync. Counts sit in memory until this much time has
# passed since the last write.
_FLUSH_EVERY_S = 20.0

_counts: dict[str, dict[str, int]] = {}
# step -> account ids already credited with it. The last two steps are
# "first ever", and without a marker every channel added and every clip
# received would count again — which would show a funnel that converts at
# several hundred percent. Account ids only, no Twitch id, name or address;
# these are ids the product already stores on every clip it holds.
_once: dict[str, set[str]] = {}
_loaded = False
_dirty = False
_last_flush = 0.0


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load() -> None:
    """Read counts from disk. Safe to call repeatedly."""
    global _loaded
    _counts.clear()
    _once.clear()
    try:
        if _PATH.exists():
            raw = json.loads(_PATH.read_text() or "{}")
            for day, row in (raw.get("days") or {}).items():
                if isinstance(row, dict):
                    _counts[day] = {k: int(v) for k, v in row.items()
                                    if k in _KEYS and isinstance(v, (int, float))}
            for step, ids in (raw.get("once") or {}).items():
                if step in _KEYS and isinstance(ids, list):
                    _once[step] = {str(i) for i in ids}
    except (json.JSONDecodeError, OSError, ValueError, AttributeError) as exc:
        # A corrupt counter file must not stop the app booting. Losing the
        # history is annoying; failing to start is an outage.
        log.warning("funnel_unreadable", error=str(exc))
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        load()


def _prune() -> None:
    if len(_counts) <= _KEEP_DAYS:
        return
    for day in sorted(_counts)[:-_KEEP_DAYS]:
        _counts.pop(day, None)


def flush(force: bool = False) -> None:
    """Write counts to disk, at most every _FLUSH_EVERY_S unless forced."""
    global _dirty, _last_flush
    if not _dirty:
        return
    now = time.time()
    if not force and (now - _last_flush) < _FLUSH_EVERY_S:
        return
    _prune()
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"days": _counts,
             "once": {k: sorted(v) for k, v in _once.items()}},
            indent=0, sort_keys=True))
        os.replace(tmp, _PATH)       # atomic: never leave a half-written file
        _dirty = False
        _last_flush = now
    except OSError as exc:
        log.warning("funnel_write_failed", error=str(exc))


def record(step: str, n: int = 1) -> None:
    """Count one step. Never raises — see the module docstring.

    Unknown steps are dropped rather than stored: the admin page renders from
    STEPS, so a typo'd key would be counted forever and displayed nowhere.
    """
    global _dirty
    try:
        if step not in _KEYS:
            return
        _ensure_loaded()
        _counts.setdefault(_today(), {})[step] = \
            _counts.setdefault(_today(), {}).get(step, 0) + n
        _dirty = True
        flush()
    except Exception as exc:                       # noqa: BLE001 — see docstring
        log.warning("funnel_record_failed", step=step, error=str(exc))


def record_once(step: str, user_id: str, count: bool = True) -> bool:
    """Count a step the FIRST time an account reaches it, and never again.

    Returns whether it counted, so a caller can log the milestone without
    repeating the "have they done this before" logic. Never raises.

    `count=False` marks the account as seen WITHOUT adding to the total. That
    is how staff are excluded: marking them still costs one entry but means the
    caller's "is this person staff?" lookup happens once per account rather
    than on every clip they ever receive. Skipping the mark entirely would
    leave the account permanently un-seen and re-run that lookup forever.
    """
    global _dirty
    try:
        if step not in _KEYS or not user_id:
            return False
        _ensure_loaded()
        seen = _once.setdefault(step, set())
        if user_id in seen:
            return False
        seen.add(user_id)
        _dirty = True
        if not count:
            flush()
            return False
        record(step)          # record() flushes, which persists `seen` too
        return True
    except Exception as exc:                       # noqa: BLE001
        log.warning("funnel_record_once_failed", step=step, error=str(exc))
        return False


def totals(days: int = 30) -> dict:
    """Counts for the last `days` days, plus the per-day series.

    Returns the shape the admin page renders directly, so the arithmetic lives
    here rather than being redone in JavaScript where it cannot be tested.
    """
    _ensure_loaded()
    flush(force=True)         # so a reader never sees staler numbers than disk
    day_keys = sorted(_counts)[-days:] if days > 0 else sorted(_counts)
    agg = {k: 0 for k, _l, _h in STEPS}
    for day in day_keys:
        for k, v in _counts.get(day, {}).items():
            if k in agg:
                agg[k] += v

    # Drop-off between consecutive funnel steps. `returning` is excluded: it is
    # a sign-in by somebody who already has an account, so counting it as a
    # funnel stage would make the conversion from "came back" look like it
    # leaked people who in fact completed months ago.
    ordered = [k for k, _l, _h in STEPS if k != "returning"]
    rows = []
    for i, key in enumerate(ordered):
        count = agg.get(key, 0)
        prev = agg.get(ordered[i - 1], 0) if i else 0
        rows.append({
            "key":   key,
            "count": count,
            # Percentage of the step before it, and of the top of the funnel.
            "pct_prev": round(count / prev * 100, 1) if i and prev else None,
            "pct_top":  round(count / agg[ordered[0]] * 100, 1) if agg.get(ordered[0]) else None,
        })
    return {
        "days":   len(day_keys),
        "totals": agg,
        "rows":   rows,
        "series": {d: _counts.get(d, {}) for d in day_keys},
        "steps":  [{"key": k, "label": l, "help": h} for k, l, h in STEPS],
    }
