"""Free up the server: what the admin "Clean up" tab would do, worked out.

Owner, 2026-10-31: "free up space on the server, go through users who have
been inactive for a week or more and clear out space, and also end streams for
people who have not accepted a clip in a week or more". Their choices: free
accounts only, clip videos only, remove the channels, an admin button with a
preview first.

TWO RULES, both for FREE accounts only (the effective plan is free and the
account is not an admin). Paying and granted accounts are never touched: Pro
promises unlimited clips kept, and Starter/Pro are sold as watching while you
are away.

  * Inactive a week (last activity = the latest of: an authenticated request
    or socket ping, a sign-in, the account's creation): the VIDEO FILES of
    their clips are cleared. The clip records stay, the cards keep their
    pictures, Twitch clips stay on Twitch and can be fetched again from the
    card; a Kick clip's video is gone for good (on Kick the file is the clip).
  * No clip approved for a week (or never, on an account at least a week old):
    their channels come off their list. That ends the streams, and once
    nobody lists a Kick channel its chat subscription is pruned too, which is
    most of what froze the site on 2026-10-29.

This module only DECIDES, from plain data, so the preview and the run cannot
disagree and the rules can be tested exactly. api.cleanup_preview and
api.cleanup_run gather the data and carry it out.
"""

from __future__ import annotations

from typing import Callable

from src.billing import plans

WEEK_S = 7 * 86400


def _ts(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _name(u: dict) -> str:
    # The same order as the admin page's other lists (api.admin_downloads).
    return str(u.get("twitch_login") or u.get("kick_slug") or u.get("username")
               or str(u.get("id") or "")[:8])


def is_free(u: dict) -> bool:
    """A free account: the effective plan is free and it is not an admin."""
    return not u.get("is_admin") and plans.get_plan(u) == "free"


def last_active(u: dict, activity: dict) -> float:
    return max(_ts(activity.get(u.get("id", ""))), _ts(u.get("last_login_at")),
               _ts(u.get("created_at")))


def last_approved(uid: str, clips) -> float:
    return max((_ts(c.get("approved_at") or c.get("created_at")) for c in clips
                if c.get("user_id") == uid and c.get("status") == "approved"), default=0.0)


def plan(users: list[dict], clips, streams: dict, activity: dict, now: float,
         file_size: Callable[[str], int]) -> dict:
    """What a clean-up would do right now.

    users     every account record
    clips     every clip record (an iterable of dicts)
    streams   the stream registry, key "<user id>:<channel>" -> record
    activity  user id -> last authenticated request or socket ping
    file_size clip id -> bytes of its video on disk (0 when there is none)

    Returns {"files": [...], "streams": [...], "totals": {...}}: per account,
    the clip videos to clear and the channels to remove, with what they cost.
    """
    clips = list(clips)
    free = {u["id"]: u for u in users if u.get("id") and is_free(u)}

    files = []
    for uid, u in free.items():
        seen = last_active(u, activity)
        if now - seen < WEEK_S:
            continue
        mine = []
        for c in clips:
            if c.get("user_id") != uid or not c.get("id"):
                continue
            size = file_size(c["id"])
            if size > 0:
                mine.append((c["id"], size))
        if mine:
            files.append({"user_id": uid, "name": _name(u), "last_active": seen,
                          "clips": [cid for cid, _ in mine],
                          "bytes": sum(size for _, size in mine)})

    by_user: dict[str, list[str]] = {}
    for key, rec in streams.items():
        uid = str(rec.get("user_id") or (key.split(":", 1)[0] if ":" in key else ""))
        channel = rec.get("channel") or (key.split(":", 1)[1] if ":" in key else key)
        if uid in free and channel:
            by_user.setdefault(uid, []).append(channel)
    ended = []
    for uid, channels in by_user.items():
        u = free[uid]
        if now - _ts(u.get("created_at")) < WEEK_S:
            continue                                   # a new account has not had a week
        approved = last_approved(uid, clips)
        if now - approved < WEEK_S:
            continue
        ended.append({"user_id": uid, "name": _name(u), "last_approved": approved,
                      "channels": sorted(channels)})

    files.sort(key=lambda r: -r["bytes"])
    ended.sort(key=lambda r: r["name"].lower())
    return {"files": files, "streams": ended,
            "totals": {"file_accounts": len(files),
                       "clip_files": sum(len(r["clips"]) for r in files),
                       "bytes": sum(r["bytes"] for r in files),
                       "stream_accounts": len(ended),
                       "channels": sum(len(r["channels"]) for r in ended)}}


def notice(cleared_files: int, channels: list[str]) -> dict | None:
    """The note left in the account's notifications bell, saying what was
    done and how to undo it. None when nothing was done to the account."""
    if not cleared_files and not channels:
        return None
    parts = []
    if channels:
        names = ", ".join(channels[:5]) + (f" and {len(channels) - 5} more" if len(channels) > 5 else "")
        parts.append(f"No clips were approved on your account for a week, so we stopped watching "
                     f"{names}. Add them back on Live Streams anytime; approving clips keeps "
                     f"them running.")
    if cleared_files:
        parts.append(f"After a week away, the video files of {cleared_files} saved "
                     f"clip{'s' if cleared_files != 1 else ''} were cleared to free space. Your "
                     f"clips are still listed, and Twitch clips can be downloaded again "
                     f"from their card.")
    title = ("We stopped watching your channels" if channels
             else "We cleared some clip videos")
    return {"title": title, "body": " ".join(parts)}
