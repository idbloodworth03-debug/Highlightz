"""Make a marketing reel by itself whenever a chosen channel's clip is captured.

Owner, 2026-10-31: "I WANT IT TO LINK TO THE SITE, ONCE A CLIP IS CAPTURED,
MAKE A MARKETING REEL WITH THE PEAK FROM THE CLIP AS A HOOK" — for Duxxion
first. Not a feature: nothing new for users, no screen, no setting. The site
does it in the background and the reel lands in the admin's own Library.

    api.on_clip_file_ready(clip_id)      every path that makes a clip's file
      └─ auto.consider(clip_id)          (in the background, never awaited)
           channel in MARKETING_REEL_CHANNELS?   the clip's account an admin?
           no reel yet for this clip?            under today's cap?
           └─ reel.make  →  runner.save_render  →  the admin's Library
                         →  a note in their bell (users.add_notices)

WHOSE CLIPS. Only a clip captured on an ADMIN's account, and the reel goes
to that same account. The Privacy Policy keeps a clip's file to the account
it belongs to; a clipper who monitors Duxxion on their own account is never
read. So: monitor Duxxion on your own (admin) account.

ONE AT A TIME. A module lock queues reels behind each other, and each render
also takes render._slot, so a burst of clips during a stream becomes a short
queue, never parallel encodes. A daily cap per account bounds the CPU.

RECORDED ON THE CLIP as `clip["marketing_reel"]` = {status, at, upload_id |
error, hook, seconds}: the dedupe, and the trail for "why didn't this one get
a reel". A render cut off by a restart is left "rendering" in the record
but never retried by itself; `scripts/marketing_reel.py --clip <id>` redoes
one by hand.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

RECORD_WAIT_S = 180.0          # the processor may write the record after the file
RECORD_POLL_S = 5.0

_lock = asyncio.Lock()
_inflight: set[str] = set()


def channels() -> set[str]:
    return {c.strip().lower() for c in (settings.marketing_reel_channels or "").split(",")
            if c.strip()}


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def made_today(clips, uid: str, now: float | None = None) -> int:
    """Reels started for this account today (UTC), whatever became of them."""
    today = _day(now or time.time())
    return sum(1 for c in clips
               if c.get("user_id") == uid
               and _day(float((c.get("marketing_reel") or {}).get("at") or 0)) == today)


def why_not(clip: dict | None, *, is_admin: bool, has_file: bool, made: int) -> str:
    """Why this clip does NOT get a reel, or "" when it does. Pure: tested."""
    if not clip:
        return "no_record"
    if str(clip.get("channel") or "").lower() not in channels():
        return "channel"
    if clip.get("marketing_reel"):
        return "already"
    if clip.get("status") == "rejected":
        return "rejected"
    if not is_admin:
        return "not_admin"
    if not has_file:
        return "no_file"
    if made >= max(0, settings.marketing_reel_daily_cap):
        return "daily_cap"
    return ""


def reel_name(clip: dict) -> str:
    from src.autopilot import runner
    title = str(clip.get("clip_title") or clip.get("stream_title") or clip.get("id", "")[:8])
    return runner._safe_name(f"{clip.get('channel') or 'clip'}-marketing-{title}") + "-9x16.mp4"


async def consider(clip_id: str) -> None:
    """Called for every clip whose file just became ready. Cheap and silent
    for every clip that is not a chosen channel's. Never raises."""
    try:
        if not channels():
            return
        from src.dashboard import api
        clip = api._clips.get(clip_id)
        waited = 0.0
        while clip is None and waited < RECORD_WAIT_S:
            await asyncio.sleep(RECORD_POLL_S)
            waited += RECORD_POLL_S
            clip = api._clips.get(clip_id)
        if clip is None or str(clip.get("channel") or "").lower() not in channels():
            return
        await make_for(clip)
    except Exception:
        log.exception("marketing_reel_crashed", clip_id=clip_id)


async def make_for(clip: dict, *, force: bool = False) -> dict:
    """Make this clip's reel now (queued behind any other). Returns the record
    written onto the clip. `force` skips the dedupe and the daily cap — the
    by-hand script uses it — but never the admin or file checks."""
    from src.auth import users as user_store
    from src.autopilot import runner
    from src.clips import files as clip_files
    from src.dashboard import api
    from src.marketing import reel
    from src.uploads import library as upload_lib

    cid, uid = clip["id"], str(clip.get("user_id") or "")
    user = user_store.get_by_id(uid) or {}
    src = clip_files.path_for(cid)
    has_file = bool(src and src.is_file())
    made = made_today(api._clips.values(), uid)
    probe = {**clip, "marketing_reel": None} if force else clip
    why = why_not(probe, is_admin=bool(user.get("is_admin")), has_file=has_file,
                  made=0 if force else made)
    if why:
        log.info("marketing_reel_skipped", clip_id=cid, channel=clip.get("channel"), why=why)
        return clip.get("marketing_reel") or {}
    if cid in _inflight:
        return clip.get("marketing_reel") or {}
    if not reel.assets_ready():
        log.warning("marketing_reel_skipped", clip_id=cid, why="no_assets",
                    assets=str(reel.assets_dir()))
        return {}

    _inflight.add(cid)
    clip["marketing_reel"] = {"status": "queued", "at": time.time()}
    api._save_clips()
    up = made_info = None
    dst = Path(settings.local_storage_path) / "marketing" / f"reel-{cid}.mp4"
    try:
        async with _lock:
            clip["marketing_reel"] = {"status": "rendering", "at": time.time()}
            api._save_clips()
            made_info = await reel.make(clip, src, dst, preset=_preset_for(uid, clip))
            up = await runner.save_render(uid, clip, dst, name=reel_name(clip))
        rec = {"status": "ready", "at": time.time(), "upload_id": up.id,
               "hook": made_info["hook"], "timeline": made_info["timeline"],
               "seconds": made_info["seconds"]}
        log.info("marketing_reel_ready", clip_id=cid, channel=clip.get("channel"),
                 upload_id=up.id, seconds=made_info["seconds"], hook=made_info["hook"])
    except reel.ReelError as exc:
        rec = {"status": "failed", "at": time.time(), "error": exc.args[0]}
        log.warning("marketing_reel_failed", clip_id=cid, error=exc.args[0])
    except Exception as exc:
        log.exception("marketing_reel_crashed", clip_id=cid)
        rec = {"status": "failed", "at": time.time(), "error": f"Unexpected error: {exc}"[:200]}
    finally:
        _inflight.discard(cid)
        dst.unlink(missing_ok=True)
    clip["marketing_reel"] = rec
    api._save_clips()

    # Telling the owner. A failure here is logged and never turns a reel that
    # is already in the Library into a "failed" one.
    try:
        await api.broadcast({"event": "clip_updated", "clip": api._clip_out(clip)}, user_id=uid)
        if up is not None:
            await api.broadcast({"event": "upload_added", "upload": up.public(),
                                 "quota": upload_lib.quota(uid)}, user_id=uid)
            h = made_info["hook"]
            user_store.add_notices({uid: {
                "title": f"Marketing reel ready: {clip.get('channel')}",
                "body": (f"{clip.get('clip_title') or 'A new clip'} is in your Library as "
                         f"{reel_name(clip)}. It opens on {_mmss(h['start'])}–{_mmss(h['end'])} "
                         f"of the clip, then the fast-forward and the ad. "
                         f"{made_info['seconds']:.0f}s, ready to post."),
            }})
            await api.broadcast({"event": "inbox_changed"}, user_id=uid)
    except Exception as exc:
        log.warning("marketing_reel_notify_failed", clip_id=cid, error=str(exc))
    return rec


def _preset_for(uid: str, clip: dict) -> str:
    """The rules preset the account scores this channel under (profile file,
    read-only). 'default' if unknown; the audio covers a wrong guess."""
    import json
    from src.trigger.rules import PRESETS
    channel = str(clip.get("channel") or "").lower()
    root = (Path(settings.local_storage_path) / "profiles" / uid).resolve()
    path = (root / f"{channel}.json").resolve()
    if not channel or not path.is_relative_to(root):
        return "default"
    try:
        preset = json.loads(path.read_text(encoding="utf-8")).get("preset") or "default"
    except Exception:
        return "default"
    return preset if preset in PRESETS else "default"


def _mmss(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"
