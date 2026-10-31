"""Peak reels: a channel's best caught moments as vertical reels, each one
opening on its own score peak. Admin-only while it is tested, like the rest
of the plan-based edit.

Owner, 2026-10-31: "can you build me a mod that finds these clips,
specifically duxxion for now, uses the peak as hooks and creates instagram
reels?" — decided in that conversation: built INTO Highlightz, from the clips
the app already captured (no Twitch downloads for this), exporting MP4s to
review and post by hand (nothing is posted automatically).

    pick a channel's top clips that have a file
      → peak_hook.find  (score peak in the file, refined on the audio)
      → auto_edit.make_from with that hook  ([hook] then the whole clip, 9:16)
      → the account's Library, named <channel>-reel-…-9x16.mp4

It is the existing Auto Edit render with an automatic hook. The account's
Auto Edit settings (template, captions, title) apply unchanged, and renders
queue behind the same one-at-a-time slot as Autopilot (render._slot), so a
batch can never run two encodes at once on the 2-core box.

PRIVACY. Only the requesting account's own clips, exactly like
`_own_clip_for_admin`: the Privacy Policy keeps a clip's file to the account
it belongs to, and rendering one is reading it.

STATE. One job per account, in memory. Every change is broadcast as
`peak_reels` to that account's tabs (CLAUDE.md realtime contract). A finished
reel is ALSO written onto its clip as `clip["peak_reel"]`, so a reel made
survives a restart and a second batch does not redo it; a job cut off by a
restart is simply gone (`GET /reels/peak` then shows no job, the reels it
finished are in the Library and in `recent`).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

DEFAULT_COUNT = 5
MAX_COUNT = 10
KEEP_STATUSES = ("approved", "published", "pending")   # never a rejected clip

# uid -> job dict. The dict IS what the browser gets, so keep it JSON-plain.
_jobs: dict[str, dict] = {}
# uids with a batch running, and those asked to stop after the current clip.
_running: set[str] = set()
_stop: set[str] = set()


# ── choosing clips (pure, tested) ────────────────────────────────────────────

def _chan(c: dict) -> str:
    return str(c.get("channel") or "").lower()


def channels(clips, uid: str, have_ids) -> list[dict]:
    """The account's channels that have clips a reel can be made from, most
    first: [{"channel", "clips", "made"}]."""
    rows: dict[str, dict] = {}
    for c in clips:
        if c.get("user_id") != uid or c.get("id") not in have_ids:
            continue
        if c.get("status") not in KEEP_STATUSES:
            continue
        r = rows.setdefault(_chan(c), {"channel": _chan(c), "clips": 0, "made": 0})
        r["clips"] += 1
        if (c.get("peak_reel") or {}).get("upload_id"):
            r["made"] += 1
    return sorted((r for r in rows.values() if r["channel"]),
                  key=lambda r: (-r["clips"], r["channel"]))


def pick(clips, uid: str, channel: str, count: int, have_ids, *,
         skip_made: bool = True) -> list[dict]:
    """The clips a batch takes, best first.

    Approved clips before pending ones — somebody already watched those and
    said yes — and within each group the Autopilot ranking (`plan.rank`:
    crowd highlights, then virality, then score, then newest). Rejected clips
    and clips without a file are never taken; clips that already have a peak
    reel are skipped unless `skip_made` is off.
    """
    from src.autopilot import plan as P
    channel = (channel or "").lower()
    pool = [c for c in clips
            if c.get("user_id") == uid and _chan(c) == channel
            and c.get("id") in have_ids
            and c.get("status") in KEEP_STATUSES
            and not (skip_made and (c.get("peak_reel") or {}).get("upload_id"))]
    approved = [c for c in pool if c.get("status") in ("approved", "published")]
    pending = [c for c in pool if c.get("status") == "pending"]
    return (P.rank(approved) + P.rank(pending))[:max(1, min(MAX_COUNT, int(count)))]


def reel_name(clip: dict) -> str:
    """`duxxion-reel-<title>-9x16.mp4`, through the same filename scrub as
    every other render in the Library."""
    from src.autopilot import runner
    title = str(clip.get("clip_title") or clip.get("title") or clip.get("id", "")[:8])
    return runner._safe_name(f"{_chan(clip) or 'clip'}-reel-{title}") + "-9x16.mp4"


def preset_for(uid: str, channel: str) -> str:
    """The rules preset this account scores the channel under, read from its
    profile file (read-only — the server owns writing it). 'default' when
    there is none or it cannot be read; the audio covers a wrong guess."""
    import json
    from src.trigger.rules import PRESETS
    root = (Path(settings.local_storage_path) / "profiles" / uid).resolve()
    path = (root / f"{channel.lower()}.json").resolve()
    if not path.is_relative_to(root):
        return "default"
    try:
        preset = json.loads(path.read_text(encoding="utf-8")).get("preset") or "default"
    except Exception:
        return "default"
    return preset if preset in PRESETS else "default"


# ── the job ──────────────────────────────────────────────────────────────────

def job_for(uid: str) -> dict | None:
    return _jobs.get(uid)


def running(uid: str) -> bool:
    return uid in _running


def request_stop(uid: str) -> bool:
    if uid not in _running:
        return False
    _stop.add(uid)
    job = _jobs.get(uid)
    if job:
        job["stopping"] = True
    return True


def recent(clips, uid: str, limit: int = 20) -> list[dict]:
    """Reels this account already has, newest first — what the panel shows
    when no job is running (and after a restart)."""
    rows = []
    for c in clips:
        r = c.get("peak_reel") or {}
        if c.get("user_id") == uid and r.get("upload_id"):
            rows.append({"clip_id": c["id"], "channel": _chan(c),
                         "title": c.get("clip_title") or "", **r})
    rows.sort(key=lambda r: -float(r.get("at") or 0))
    return rows[:limit]


def new_job(uid: str, channel: str, picked: list[dict]) -> dict:
    job = {
        "id": uuid.uuid4().hex[:12],
        "channel": channel.lower(),
        "status": "running",          # running | done | stopped
        "started_at": time.time(),
        "finished_at": None,
        "stopping": False,
        "items": [{"clip_id": c["id"],
                   "title": c.get("clip_title") or "",
                   "status": "waiting",    # waiting|finding|rendering|ready|failed|skipped
                   "hook": None, "upload_id": None, "seconds": None,
                   "error": None} for c in picked],
    }
    _jobs[uid] = job
    _running.add(uid)
    _stop.discard(uid)
    return job


async def run(uid: str, job: dict, notify) -> None:
    """Make every reel in `job`, one at a time. `notify(event)` broadcasts to
    the account; the API passes a closure over `broadcast(..., user_id=uid)`.
    Never raises: every clip ends ready, failed or skipped, and the job ends
    done or stopped, whatever happens."""
    from src.autopilot import auto_edit, peak_hook, runner
    from src.autopilot import title_for
    from src.autopilot.plan import plan_duration
    from src.autopilot.render import RenderError
    from src.auth import users as _users
    from src.clips import files as clip_files
    from src.dashboard import api
    from src.uploads import library as upload_lib
    from src import language as _language

    async def push() -> None:
        try:
            await notify({"event": "peak_reels", "job": job})
        except Exception as exc:
            log.warning("peak_reels_notify_failed", error=str(exc))

    preset = preset_for(uid, job["channel"])
    try:
        await push()
        for item in job["items"]:
            if uid in _stop:
                item["status"] = "skipped"
                continue
            cid = item["clip_id"]
            clip = api._clips.get(cid)
            src = clip_files.path_for(cid)
            if not clip or clip.get("user_id") != uid or not src or not src.is_file():
                item.update(status="failed", error="The clip or its video file is gone.")
                await push()
                continue
            dst = Path(settings.local_storage_path) / "autoedit" / f"reel-{cid}.mp4"
            try:
                item["status"] = "finding"
                await push()
                duration = await auto_edit.probe_duration(src)
                if duration <= 0:
                    duration = float(clip.get("duration_seconds") or 0.0)
                hook = await peak_hook.find(clip, src, duration, preset)
                item["hook"] = hook.as_dict() if hook else None
                item["status"] = "rendering"
                await push()

                cfg = _users.autopilot_for(uid)
                lang = _language.for_user_id(uid)
                rec = {k: v for k, v in clip.items() if k != "hook"}
                if hook:
                    rec["hook"] = hook.as_clip_hook()
                plan = await auto_edit.make_from(
                    src, rec, dst, captions=bool(cfg["edit_captions"]),
                    template=cfg["edit_template"],
                    title=title_for({**cfg, "title": cfg["edit_title"]}, clip, lang),
                    language=lang)
                up = await runner.save_render(uid, clip, dst, name=reel_name(clip))
                await notify({"event": "upload_added", "upload": up.public(),
                              "quota": upload_lib.quota(uid)})
                # plan.hook is True only when the renderer actually opened on
                # it (plan.hook_window can still refuse one); say so if not.
                if hook and not plan.hook:
                    item["hook"] = None
                item.update(status="ready", upload_id=up.id,
                            seconds=round(plan_duration(plan), 1))
                clip["peak_reel"] = {"upload_id": up.id, "at": time.time(),
                                     "seconds": item["seconds"], "hook": item["hook"]}
                api._save_clips()
                await notify({"event": "clip_updated", "clip": api._clip_out(clip)})
            except RenderError as exc:
                item.update(status="failed", error=exc.args[0])
            except Exception as exc:          # never leave a row on "rendering"
                log.exception("peak_reel_crashed", clip_id=cid)
                item.update(status="failed", error=f"Unexpected error: {exc}"[:200])
            finally:
                dst.unlink(missing_ok=True)
            log.info("peak_reel", clip_id=cid, channel=job["channel"],
                     status=item["status"], hook=item["hook"], error=item["error"])
            await push()
    finally:
        job["status"] = "stopped" if uid in _stop else "done"
        job["stopping"] = False
        job["finished_at"] = time.time()
        _running.discard(uid)
        _stop.discard(uid)
        await push()
