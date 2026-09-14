"""Which channels Twitch is refusing to clip, and how often.

WHY IT IS WORTH RECORDING. The per-channel backoff already logs a refusal, but
a log line only answers the question if somebody is looking at the moment it
scrolls past. The question that actually matters — is this one unlucky channel
or is it everywhere — cannot be answered from the logs after the fact without
knowing which day to grep. A tiny counter answers it at a glance.

DELIBERATELY SMALL. One row per channel: how many refusals, when it started,
when it was last seen, the reason, and who was affected. It is a diagnostic
tally, not an event log — nobody needs every individual refusal, and keeping
them would grow without bound on a channel that refuses every trigger.

WHO WAS AFFECTED, AND WHY THAT IS NOW RECORDED. This started as an admin-only
tally and stored no user id at all. That was fine while one person read it and
wrong the moment the same warning had to reach the user whose channel is
broken — with no attribution there is no way to show someone their own channel
without showing them everybody else's, which would leak the customer list to
every account. So each row carries the ids of the users whose clip attempts
were refused.

That is the only personal thing here, it is a user's own id against a channel
they themselves registered, and the row is deleted outright the moment the
channel clips successfully. No clip, no viewer, no token.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json

log = structlog.get_logger(__name__)

_FILE = Path(settings.local_storage_path) / "clip_refusals.json"

# Reasons worth telling apart. They have different fixes: classification is the
# broadcaster setting labels, automod is the broadcaster renaming the stream,
# and not_authorized is the broadcaster's clip permissions.
CLASSIFICATION = "classification"
TITLE_AUTOMOD = "title_automod"
NOT_AUTHORIZED = "not_authorized"

# A ceiling on the affected-user list per channel. A popular streamer watched by
# every account on the platform would otherwise grow one row without bound, and
# the list exists to answer "is this mine", which the newest ids answer just as
# well as all of them.
_MAX_USERS = 200


def _load() -> dict:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        log.warning("clip_refusals_corrupt", path=str(_FILE))
        return {}


def _save(rows: dict) -> None:
    try:
        atomic_write_json(_FILE, rows)
    except Exception as exc:
        # A diagnostic counter must never be the reason a clip pipeline breaks.
        log.warning("clip_refusals_save_failed", error=str(exc))


def record(channel: str, reason: str, user_id: str = "") -> None:
    """Note that Twitch refused to clip `channel` for `user_id`.

    `user_id` is optional so a caller without one still gets the tally; such a
    refusal simply never reaches a user's own screen, which is the correct
    outcome — we cannot tell them it is theirs if we do not know that it is.
    """
    if not channel:
        return
    key = channel.lower()
    rows = _load()
    now = time.time()
    row = rows.get(key) or {"channel": channel, "count": 0,
                            "first_seen": now, "reason": reason}
    row["count"] = int(row.get("count", 0)) + 1
    row["last_seen"] = now
    # The most recent reason wins: a channel that fixed its labels and now trips
    # automod should read as an automod problem, not as history.
    row["reason"] = reason
    row["channel"] = channel
    if user_id:
        users = [u for u in (row.get("users") or []) if u != user_id]
        users.append(user_id)
        row["users"] = users[-_MAX_USERS:]
    rows[key] = row
    _save(rows)


def clear(channel: str) -> list[str]:
    """Forget a channel — it clipped successfully, so whatever was wrong is
    fixed. Without this the tally only ever grows and a channel that recovered
    a month ago still reads as broken.

    Returns the users who were being warned about it, so the caller can take
    the warning off their screens. They are returned rather than looked up
    afterwards because the row is the only record of who saw it, and this
    deletes the row.
    """
    if not channel:
        return []
    rows = _load()
    row = rows.pop(channel.lower(), None)
    if row is None:
        return []
    _save(rows)
    return list(row.get("users") or [])


def all_rows() -> list[dict]:
    """Newest problem first — that is the one worth looking at."""
    return sorted(_load().values(),
                  key=lambda r: r.get("last_seen", 0), reverse=True)


def rows_for_user(user_id: str) -> list[dict]:
    """Only the channels whose refusals hit THIS user.

    The filter is the privacy boundary for the user-facing notice: every other
    row names a channel some other customer is monitoring, and the whole list
    would be a customer list.

    Rows written before refusals were attributed carry no `users` key and match
    nobody. They stay visible to admins and reappear on a user's screen the
    next time the channel refuses, which is when the warning is true again.
    """
    if not user_id:
        return []
    return [r for r in all_rows() if user_id in (r.get("users") or [])]


def users_for(channel: str) -> list[str]:
    """Who is currently being warned about this channel."""
    if not channel:
        return []
    row = _load().get(channel.lower()) or {}
    return list(row.get("users") or [])
