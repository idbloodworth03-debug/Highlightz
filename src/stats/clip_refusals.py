"""Which channels Twitch is refusing to clip, and how often.

WHY IT IS WORTH RECORDING. The per-channel backoff already logs a refusal, but
a log line only answers the question if somebody is looking at the moment it
scrolls past. The question that actually matters — is this one unlucky channel
or is it everywhere — cannot be answered from the logs after the fact without
knowing which day to grep. A tiny counter answers it at a glance.

DELIBERATELY SMALL. One row per channel: how many refusals, when it started,
when it was last seen, and the reason. It is a diagnostic tally, not an event
log — nobody needs every individual refusal, and keeping them would grow without
bound on a channel that refuses every trigger.

NOTHING PERSONAL IS STORED. A channel name is the same public identifier the
user typed to start monitoring it. No user id, no clip, no viewer.
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


def record(channel: str, reason: str) -> None:
    """Note that Twitch refused to clip `channel`."""
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
    rows[key] = row
    _save(rows)


def clear(channel: str) -> None:
    """Forget a channel — it clipped successfully, so whatever was wrong is
    fixed. Without this the tally only ever grows and a channel that recovered
    a month ago still reads as broken."""
    if not channel:
        return
    rows = _load()
    if rows.pop(channel.lower(), None) is not None:
        _save(rows)


def all_rows() -> list[dict]:
    """Newest problem first — that is the one worth looking at."""
    return sorted(_load().values(),
                  key=lambda r: r.get("last_seen", 0), reverse=True)
