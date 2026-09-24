"""Autopilot's worker: approved clip in, scheduled post out.

Two ways in, both from api.py: `maybe_run(clip)` right after an approval,
and `maybe_run_by_id(clip_id)` when a clip's file lands (the file can arrive
after the approval — the cut waits for the moment's tail). Both are safe to
call more than once: the clip carries its own `autopilot` record and a clip
that is already rendering, scheduled or posted is left alone.

What is written where, in order, so a restart mid-run loses at most one
step and never double-posts:

    clip["autopilot"] = {status: "rendering"}        → tab shows the badge
    ffmpeg render → uploads library (source="render") → the file exists
    schedule item (source="autopilot")                → the calendar shows it
    clip["autopilot"] = {status: "scheduled", item_id, due_at}

A failure at any step lands as {status: "failed", error} on the clip, with
the reason, and the card says so. Retries are the user's: "Run Autopilot
on my approved clips" clears failed records and tries again.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import structlog

from config.settings import settings
from src.autopilot import caption_for, next_due, title_for
from src.autopilot import render as ap_render

log = structlog.get_logger(__name__)


def _plan_seconds(plan) -> float:
    from src.autopilot.plan import plan_duration
    return round(plan_duration(plan), 2)

_inflight: set[str] = set()
# Clips approved this long ago or less are eligible for the bulk "run now".
RUN_NOW_WINDOW_S = 7 * 24 * 3600
RUN_NOW_MAX = 10


def eligible(clip: dict, cfg: dict) -> bool:
    """Approved, on Autopilot, and not already handled."""
    if not cfg.get("enabled") or clip.get("status") != "approved":
        return False
    st = (clip.get("autopilot") or {}).get("status")
    return st in (None, "", "waiting_file")


def _safe_name(raw: str) -> str:
    return (re.sub(r"[^A-Za-z0-9 ._-]", "", raw)[:60].strip(" .") or "clip")


async def _captions_for(path: Path) -> list:
    """[(start, end, text)] from the server's Whisper pass, cached on disk
    beside the file the way the editor's captions are. Any failure is an
    empty list: captions are a bonus, never the reason a post is missed."""
    from src.captions import transcribe as cap
    try:
        payload = cap.load(path)
        if payload is None:
            payload = await cap.transcribe(path)
            cap.save(path, payload)
        return [(s["start"], s["end"], s["text"]) for s in (payload.get("segments") or [])
                if s.get("text")]
    except Exception as exc:
        log.warning("autopilot_captions_skipped", error=str(exc))
        return []


def render_name(clip: dict) -> str:
    return _safe_name(f"{clip.get('channel', 'clip')}-{clip.get('clip_title') or clip.get('stream_title') or 'highlight'}") + "-9x16.mp4"


async def save_render(uid: str, clip: dict, dst: Path, name: str | None = None):
    """Move a finished render into the user's library, then delete the
    working copy either way. Shared by Autopilot and the admin test button,
    so both land a render in the same place with the same name."""
    from src.uploads import library as upload_lib

    async def _chunks():
        with dst.open("rb") as fh:
            while True:
                b = fh.read(1024 * 1024)
                if not b:
                    break
                yield b
    try:
        return await upload_lib.save_stream(uid, name or render_name(clip), _chunks(), source="render")
    finally:
        dst.unlink(missing_ok=True)


async def process_clip(clip: dict, cfg: dict, notify, *, connected: set[str]) -> dict:
    """Render, save, schedule. Returns the clip's autopilot record."""
    from src.dashboard import api
    from src.clips import files as clip_files
    from src.uploads import library as upload_lib
    from src.publish import schedule as sched

    cid, uid = clip["id"], clip["user_id"]
    if cid in _inflight:
        return clip.get("autopilot") or {}
    _inflight.add(cid)

    async def mark(rec: dict) -> None:
        clip["autopilot"] = rec
        api._save_clips()
        await notify({"event": "clip_updated", "clip": api._clip_out(clip)}, uid)

    try:
        src = clip_files.path_for(cid)
        if not src or not src.exists():
            await mark({"status": "waiting_file", "at": time.time()})
            return clip["autopilot"]
        await mark({"status": "rendering", "at": time.time()})

        duration = float(clip.get("duration_seconds") or 0.0)
        out_dir = Path(settings.local_storage_path) / "autopilot"
        dst = out_dir / f"{cid}.mp4"
        from src.auth import users as user_store
        from src.autopilot import auto_edit
        if auto_edit.uses_new_edit(user_store.get_by_id(uid)):
            # ADMINS: the plan-based edit (blur frame, slides + whoosh, the
            # user's hook if they picked one), owner 2026-09-23: "implemented
            # to the admins right now so we can test it out".
            plan = await auto_edit.make(clip, dst, captions=bool(cfg.get("captions")),
                                        mode=cfg.get("mode") or "clipper")
            duration = _plan_seconds(plan)
        else:
            captions = []
            if cfg.get("captions") and settings.captions_enabled:
                captions = await _captions_for(src)
            await ap_render.render(src, dst, cfg["template"], title=title_for(cfg, clip),
                                   captions=captions, duration=duration)

        up = await save_render(uid, clip, dst)

        platforms = [p for p in cfg["platforms"] if p in connected]
        due = next_due(cfg, time.time(), sched.last_due_from(uid, "autopilot"))
        item = sched.add(uid, up.id, up.filename, caption_for(cfg, clip), platforms, due,
                         duration_s=duration, ratio="9:16", fmt="mp4", source="autopilot")
        await notify({"event": "schedule_added", "item": item.public()}, uid)
        await notify({"event": "upload_added", "upload": up.public(),
                      "quota": upload_lib.quota(uid)}, uid)
        await mark({"status": "scheduled", "at": time.time(), "item_id": item.id,
                    "due_at": due, "platforms": platforms})
        log.info("autopilot_scheduled", clip_id=cid, user_id=uid, item=item.id,
                 due_at=due, platforms=platforms)
        return clip["autopilot"]
    except ap_render.RenderError as exc:
        await mark({"status": "failed", "at": time.time(), "error": exc.args[0]})
    except Exception as exc:
        log.exception("autopilot_crashed", clip_id=cid)
        await mark({"status": "failed", "at": time.time(),
                    "error": f"Unexpected error: {exc}"[:200]})
    finally:
        _inflight.discard(cid)
    return clip.get("autopilot") or {}


async def maybe_run(clip: dict) -> None:
    """The approval hook. Quiet when Autopilot is off, the plan is not Pro,
    or the clip is already handled."""
    from src.auth import users as user_store
    from src.billing.plans import limits_for
    from src.dashboard import api
    from src.publish import connections
    user = user_store.get_by_id(clip.get("user_id") or "")
    if not user or not limits_for(user)["uploads"]:
        return
    cfg = user_store.autopilot_for(user["id"])
    if not eligible(clip, cfg):
        return
    await process_clip(clip, cfg, api.broadcast,
                       connected=connections.connected_platforms(user["id"]))


async def maybe_run_by_id(clip_id: str) -> None:
    """The file-arrived hook."""
    from src.dashboard import api
    clip = api._clips.get(clip_id)
    if clip:
        await maybe_run(clip)


def kick(coro) -> None:
    """Fire-and-forget from a request handler."""
    asyncio.create_task(coro)


async def run_now(uid: str) -> int:
    """The bulk button: every recently approved clip with a file that Autopilot
    has not scheduled (including ones that failed), oldest first, capped."""
    from src.auth import users as user_store
    from src.dashboard import api
    from src.clips import files as clip_files
    from src.publish import connections
    cfg = user_store.autopilot_for(uid)
    now = time.time()
    todo = []
    for c in api._clips.values():
        if c.get("user_id") != uid or c.get("status") != "approved":
            continue
        if now - float(c.get("approved_at") or c.get("created_at") or 0) > RUN_NOW_WINDOW_S:
            continue
        st = (c.get("autopilot") or {}).get("status")
        if st in ("rendering", "scheduled"):
            continue
        p = clip_files.path_for(c["id"])
        if not p or not p.exists():
            continue
        todo.append(c)
    todo.sort(key=lambda c: float(c.get("approved_at") or 0))
    todo = todo[:RUN_NOW_MAX]
    connected = connections.connected_platforms(uid)
    for c in todo:
        c["autopilot"] = {}                         # a failed one gets another go
    for c in todo:
        await process_clip(c, {**cfg, "enabled": True}, api.broadcast, connected=connected)
    return len(todo)
