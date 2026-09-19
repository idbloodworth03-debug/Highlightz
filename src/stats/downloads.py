"""Every clip file that leaves the server, as an append-only log.

WHY AN EVENT LOG AND NOT A TALLY. `clip_refusals` next door is deliberately a
counter — nobody needs each individual refusal. This is the opposite case:
the question is "show me every clip that was downloaded", and a count of 400
answers none of it. Which account, which channel, which clip, when, how big.
So one line per download, newest read first.

WHAT COUNTS AS A DOWNLOAD. Only `?download=1` — the Download button, the
request that puts an mp4 in somebody's downloads folder. The same endpoint
also serves the file inline to play a clip on its card, and logging that
would bury the thing being asked for under a line per press of play. Copying
a clip into the editor library never touches this endpoint at all: that is a
server-side copy and no file leaves the box.

WHAT IS NOT HERE. No IP address, no user agent, no token, and no path to the
video. This records that an owner took their own clip; it is not a trail on
what anybody watched, and it gives an admin nothing they could use to open
somebody else's file. The Privacy Policy's promise — a clip's video is
available only to the account it belongs to — is enforced by the endpoint's
ownership check and is not weakened by writing a line here.

BOUNDED. The file is pruned to _MAX_LINES the first time it is read after
crossing it, oldest first. A log nobody trims is a disk-full incident with a
date on it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_FILE = Path(settings.local_storage_path) / "downloads.jsonl"

# Roughly a year of heavy use at this size of platform, and about 6 MB of
# disk. Past it the oldest lines go.
_MAX_LINES = 50_000
# Rows a single read will hand back however many are asked for. The admin view
# is a recent-activity list, not an export.
_MAX_ROWS = 1_000


def record(clip: dict, user_id: str, size: int = 0) -> None:
    """Append one download. Best-effort and never raises — this is telemetry
    and must not be able to fail a file the user asked for."""
    try:
        if not user_id or not clip:
            return
        line = {
            "ts": time.time(),
            "user_id": user_id,
            "clip_id": clip.get("id"),
            "channel": clip.get("channel") or "",
            "platform": clip.get("platform") or "twitch",
            # What the user sees on the card, so a row is recognisable without
            # looking the clip up — and clips get deleted, at which point this
            # line is the only thing left that says what it was.
            "title": (clip.get("clip_title") or clip.get("stream_title") or "")[:120],
            "vod": bool(clip.get("is_vod_moment")),
            "size": int(size or 0),
        }
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        with _FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception as exc:
        log.warning("download_log_write_failed", error=str(exc))


def _read() -> list[dict]:
    if not _FILE.exists():
        return []
    rows = []
    try:
        with _FILE.open(encoding="utf-8") as fh:
            for raw in fh:
                try:
                    r = json.loads(raw)
                except Exception:
                    continue                    # a torn line is not a reason to lose the rest
                if isinstance(r, dict):
                    rows.append(r)
    except OSError as exc:
        log.warning("download_log_read_failed", error=str(exc))
        return []
    if len(rows) > _MAX_LINES:
        _prune(rows[-_MAX_LINES:])
        return rows[-_MAX_LINES:]
    return rows


def _prune(keep: list[dict]) -> None:
    """Rewrite the file with only the newest lines. Atomic, so a crash
    mid-prune cannot leave a half-written log."""
    try:
        tmp = _FILE.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in keep:
                fh.write(json.dumps(r) + "\n")
        os.replace(tmp, _FILE)
        log.info("download_log_pruned", kept=len(keep))
    except Exception as exc:
        log.warning("download_log_prune_failed", error=str(exc))


def recent(limit: int = 200) -> list[dict]:
    """The newest downloads first."""
    limit = max(1, min(int(limit or 200), _MAX_ROWS))
    rows = _read()
    rows.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return rows[:limit]


def totals(days: int = 14) -> dict:
    """Headline numbers plus a per-day count for the last `days` days.

    `clips` is DISTINCT clips, not events: the same clip downloaded three
    times is one clip that somebody wanted, and conflating the two would make
    a single user re-downloading look like demand.
    """
    rows = _read()
    now = time.time()
    day = 86400.0
    per_day: dict[str, int] = {}
    for r in rows:
        ts = r.get("ts") or 0
        if now - ts <= days * day:
            k = time.strftime("%Y-%m-%d", time.localtime(ts))
            per_day[k] = per_day.get(k, 0) + 1
    return {
        "events": len(rows),
        "clips": len({r.get("clip_id") for r in rows if r.get("clip_id")}),
        "users": len({r.get("user_id") for r in rows if r.get("user_id")}),
        "bytes": sum(int(r.get("size") or 0) for r in rows),
        "last_24h": sum(1 for r in rows if now - (r.get("ts") or 0) <= day),
        "per_day": per_day,
    }


def delete_all_for_user(user_id: str) -> int:
    """Forget one account's downloads. Called when the account is deleted —
    an erasure request that left this log behind would not be one."""
    rows = _read()
    keep = [r for r in rows if r.get("user_id") != user_id]
    removed = len(rows) - len(keep)
    if removed:
        _prune(keep)
    return removed
