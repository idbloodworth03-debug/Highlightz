"""Per-channel, per-session clip counts: how many we caught, how many you kept.

WHY THIS IS ITS OWN LEDGER rather than a query over existing data. Neither
existing source can answer the question:

  * `_clips` cannot. Rejecting a clip DELETES it (`del _clips[clip_id]`), and
    clips evicted at the pending cap are deleted too. Counting from there would
    report only what survived, so a channel where the user rejected 30 of 40
    would read as "10 clips caught" — the opposite of the point.
  * `training_log.jsonl` cannot. It deliberately skips any clip with no signal
    vector (VOD moments, legacy clips), so it is a training dataset, not a
    census. Counting rejections from it silently undercounts.

So every outcome is appended here at the moment it happens, exactly once, and
nothing is ever deleted. This number is meant to be shown to a streamer as
evidence, which is precisely the situation where a quietly-undercounting
number is worse than no number.

SESSIONS are inferred from gaps, not from stream start/stop — the app does not
persist broadcast boundaries, and inventing one would be a bigger change than
this is worth. Clips on the same channel more than SESSION_GAP_S apart belong
to different sessions. That matches how people actually stream (a block of
hours, then a day off) and is honest about being an inference: the report calls
them sessions by date, not by broadcast id.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_LOG_FILE = Path(settings.local_storage_path) / "stream_stats.jsonl"

CAUGHT   = "caught"      # the bot made a clip
APPROVED = "approved"    # the user kept it
REJECTED = "rejected"    # the user threw it away
EXPIRED  = "expired"     # aged out of the queue unreviewed
# A moment we did NOT clip because the queue was full. Deliberately its own
# event: it is not a "caught" (no clip exists) and it is not a rejection
# (the user never saw it). Folding it into either would corrupt the keep
# rate that gets shown to streamers.
MISSED   = "missed"

# A gap this long on one channel starts a new session. Four hours is longer
# than any break inside a single broadcast and shorter than the gap between
# broadcasts on consecutive days.
SESSION_GAP_S = 4 * 3600


def record(event: str, clip: dict) -> None:
    """Append one outcome. Best-effort and never raises — this is telemetry and
    must not be able to break the clip pipeline."""
    try:
        uid = clip.get("user_id")
        channel = clip.get("channel")
        if not uid or not channel:
            return
        line = {"ts": round(time.time(), 1), "event": event,
                "user_id": uid, "channel": channel,
                "clip_id": clip.get("id"),
                # created_at, not ts: a clip approved days later still belongs
                # to the session it was CAUGHT in, which is the whole point.
                "clip_at": round(float(clip.get("created_at") or 0), 1)}
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except Exception as exc:
        log.warning("stream_stats_write_failed", error=str(exc))


def _read(user_id: str) -> list[dict]:
    if not _LOG_FILE.exists():
        return []
    out = []
    try:
        with _LOG_FILE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue          # one bad line must not lose the rest
                if r.get("user_id") == user_id:
                    out.append(r)
    except OSError as exc:
        log.warning("stream_stats_read_failed", error=str(exc))
    return out


def _sessions(events: list[dict]) -> list[dict]:
    """Group one channel's events into sessions by the clip's CATCH time."""
    if not events:
        return []
    events = sorted(events, key=lambda r: r.get("clip_at") or r.get("ts") or 0)

    sessions: list[dict] = []
    cur: dict | None = None
    last_at = None
    for r in events:
        at = r.get("clip_at") or r.get("ts") or 0
        if cur is None or (last_at is not None and at - last_at > SESSION_GAP_S):
            cur = {"started_at": at, "ended_at": at,
                   "caught": 0, "approved": 0, "rejected": 0, "expired": 0,
                   "missed": 0}
            sessions.append(cur)
        cur["ended_at"] = max(cur["ended_at"], at)
        last_at = at
        ev = r.get("event")
        if ev == MISSED:
            cur["missed"] = cur.get("missed", 0) + 1
            continue        # never a caught, never a rejection
        if ev in cur:
            cur[ev] += 1
    for s in sessions:
        s["kept_pct"] = round(100 * s["approved"] / s["caught"]) if s["caught"] else 0
        s["reviewed"] = s["approved"] + s["rejected"]
    return sessions


def _summarise(user_id: str, channel: str, events: list[dict]) -> dict:
    """One channel's totals. Shared by for_user and all_rows so the per-user
    view and the admin table can never disagree about the same numbers."""
    sessions = _sessions(events)
    caught = sum(s["caught"] for s in sessions)
    approved = sum(s["approved"] for s in sessions)
    rejected = sum(s["rejected"] for s in sessions)
    expired = sum(s["expired"] for s in sessions)
    missed = sum(s.get("missed", 0) for s in sessions)
    return {
        "user_id": user_id,
        "channel": channel,
        "caught": caught,
        "approved": approved,
        "rejected": rejected,
        "expired": expired,
        # Moments the queue was too full to clip. NOT part of caught/kept — a
        # clip that was never made cannot be one the user kept or rejected.
        "missed": missed,
        # Of the ones actually LOOKED AT. Counting un-reviewed clips as
        # rejections would understate the hit rate on a channel whose queue
        # the user has not worked through yet.
        "kept_pct": round(100 * approved / caught) if caught else 0,
        "kept_of_reviewed_pct": (round(100 * approved / (approved + rejected))
                                 if (approved + rejected) else 0),
        "sessions": sorted(sessions, key=lambda s: -s["started_at"]),
        "last_at": max((s["ended_at"] for s in sessions), default=0),
    }


def for_user(user_id: str) -> list[dict]:
    """Per channel: totals plus a session breakdown, newest channel first."""
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for r in _read(user_id):
        by_channel[r["channel"]].append(r)

    out = [_summarise(user_id, channel, events)
           for channel, events in by_channel.items()]
    return sorted(out, key=lambda c: -c["last_at"])


def missed_since(user_id: str, since_ts: float) -> int:
    """Moments not clipped at all because the queue was full."""
    return sum(1 for r in _read(user_id)
               if r.get("event") == MISSED and (r.get("ts") or 0) >= since_ts)


def evictions_since(user_id: str, since_ts: float) -> int:
    """How many clips this user lost to the pending cap since `since_ts`.

    Cap evictions are already logged here as EXPIRED, so this needs no new
    storage — and it survives a restart, which an in-memory counter would not.
    Used for the "your queue is full" notice, so the number a user is shown is
    the same one the ledger holds.
    """
    return sum(1 for r in _read(user_id)
               if r.get("event") == EXPIRED and (r.get("ts") or 0) >= since_ts)


def all_rows() -> list[dict]:
    """Every (user, channel) pair with its totals — the admin view.

    Reads the file once and buckets by user rather than calling for_user() per
    user, which would re-read the whole log for each of them. At 1,000 users
    that is the difference between one pass and a thousand.
    """
    if not _LOG_FILE.exists():
        return []
    by_user: dict[str, list[dict]] = defaultdict(list)
    try:
        with _LOG_FILE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("user_id") and r.get("channel"):
                    by_user[r["user_id"]].append(r)
    except OSError as exc:
        log.warning("stream_stats_read_failed", error=str(exc))
        return []

    rows = []
    for uid, events in by_user.items():
        by_channel: dict[str, list[dict]] = defaultdict(list)
        for r in events:
            by_channel[r["channel"]].append(r)
        for channel, evs in by_channel.items():
            rows.append(_summarise(uid, channel, evs))
    return rows


def for_channel(user_id: str, channel: str) -> dict | None:
    for c in for_user(user_id):
        if c["channel"].lower() == channel.lower():
            return c
    return None


def delete_all_for_user(user_id: str) -> int:
    """Drop a user's rows when their account goes. Rewrites the file without
    them rather than appending a tombstone — this is the one case where the
    append-only rule gives way, because 'delete my account' has to mean it."""
    if not _LOG_FILE.exists():
        return 0
    kept, dropped = [], 0
    try:
        with _LOG_FILE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("user_id") == user_id:
                        dropped += 1
                        continue
                except json.JSONDecodeError:
                    pass          # unparseable lines are kept, not silently lost
                kept.append(line)
        if dropped:
            tmp = _LOG_FILE.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(kept), encoding="utf-8")
            tmp.replace(_LOG_FILE)
    except OSError as exc:
        log.warning("stream_stats_purge_failed", error=str(exc))
    return dropped
