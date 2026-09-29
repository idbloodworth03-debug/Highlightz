"""Render the plan-based auto-edit for one clip, from inside the server.

Until 2026-09-23 the plan/graph renderer only ran from the command line
(`scripts/edit_preview.py`). Owner, having watched the result on prod: "I
like the changes I want this to be implemented to the admins right now so we
can test it out … we need that option to add the intro hook bait thing
also". So this is the preview script's pipeline as a function the app calls:

    probe the file → builder.build (hook honoured) → captions → one ffmpeg run

Two callers, both ADMIN-ONLY for now (see `uses_new_edit`):
  * `POST /clips/{id}/auto-edit` — render into the library and post nothing,
    so an admin can watch the edit before trusting Autopilot with it;
  * `runner.process_clip` — an admin's own Autopilot renders this edit
    instead of the old `render.py` one. Everybody else is untouched.

ONE RENDER AT A TIME, shared with render.py's `_slot`: this box also scores
live streams, and two libx264 encodes at once is how the earlier preview got
OOM-killed (exit -9).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from config.settings import settings
from src.autopilot import graph as G
from src.autopilot import plan as P
from src.autopilot import render as ap_render
from src.autopilot import sfx as S
from src.autopilot.render import RenderError

log = structlog.get_logger(__name__)

# Measured on prod 2026-09-23: 228s to render a 42.5s edit with captions on
# the 1-vCPU box — about 5.4x real time. Autopilot's flat 300s would kill a
# 60s clip with a 10s hook, so the allowance scales with the video.
RENDER_X_REALTIME = 10.0


def uses_new_edit(user: dict | None) -> bool:
    """Who gets the plan-based edit. Admins only, while it is being tested."""
    return bool(user and user.get("is_admin"))


async def probe_duration(path: Path) -> float:
    """The file's real length. The record's `duration_seconds` is what was
    asked for at the cut, not necessarily what landed, and a segment asked
    to read past the end of its file skews every offset after it."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return float((out or b"0").decode().strip() or 0)
    except Exception:
        return 0.0


# The caption step gets its own ceiling on top of Whisper's: waiting for the
# one transcription slot has no timeout of its own, so a slot held by something
# else (the editor's Auto-captions, another render) would leave an Autopilot
# clip sitting on "Editing…" with nothing said. Past this, the edit carries on
# without captions AND says why.
CAPTION_STEP_EXTRA_S = 90.0


async def _transcript(path: Path) -> tuple[list, str]:
    """Whisper's cues for the clip, cached beside the file, and — when there
    are none — the REASON, in words the person can act on. Never raises: a
    missing caption must not cost the edit, but it must not be silent either
    ("The auto captions are being held up and it is stuck. No words are
    coming out", owner 2026-09-28)."""
    from src.captions import transcribe as cap
    if not settings.captions_enabled:
        return [], ("Captions are switched off on the server (CAPTIONS_ENABLED is not set), "
                    "so this edit has none.")

    async def work() -> list:
        payload = cap.load(path)
        if payload is None:
            payload = await cap.transcribe(path)
            cap.save(path, payload)
        return payload.get("segments") or []

    try:
        segs = await asyncio.wait_for(work(), timeout=settings.captions_timeout_s + CAPTION_STEP_EXTRA_S)
    except asyncio.TimeoutError:
        log.warning("auto_edit_captions_timeout", path=path.name)
        return [], "Captions took too long (another caption job may have been running), so this edit has none."
    except Exception as exc:
        log.warning("auto_edit_captions_skipped", error=str(exc))
        return [], f"Captions could not be made: {str(exc)[:140]}"
    if not segs:
        return [], "No speech was found in this clip, so it has no captions."
    return segs, ""


def apply_template(plan: P.EditPlan, name: str) -> P.EditPlan:
    """Shape a finished plan into one of the user's Auto Edit templates.

    Only the plan's own knobs are turned — framing, and the slide in/out with
    its whoosh — so every template is something the renderer already does and
    `P.valid` still has the last word. `suggested` changes nothing: it is the
    formula as designed.
    """
    if name == "fill":
        for seg in plan.segments:
            seg.framing = "fill"
    elif name == "clean":
        plan.slide_in = False
        plan.slide_out = False
        plan.sfx = []
    return plan


async def build_plan(clip: dict, src: Path, *, captions: bool,
                     mode: str = "clipper", template: str = "suggested",
                     title: str = "", on_stage=None) -> tuple[P.EditPlan, dict]:
    """The plan for this one clip — with its hook, if it has one."""
    from src.autopilot import builder, llm_common
    duration = await probe_duration(src)
    if duration <= 0:
        duration = float(clip.get("duration_seconds") or 0.0)
    if duration < P.MIN_SEGMENT_S:
        raise RenderError("This clip is too short to edit.")
    sources = {clip["id"]: (src, duration)}
    transcripts = {}
    caption_note = ""
    if captions:
        if on_stage:
            await on_stage("captions")
        segs, caption_note = await _transcript(src)
        if segs:
            transcripts = {clip["id"]: segs}
    plan, meta = await builder.build([clip], sources, transcripts=transcripts, mode=mode)
    if captions and transcripts and not plan.captions:
        plan.captions = llm_common.captions_for_plan(plan, transcripts)
    apply_template(plan, template)
    if title:
        plan.title = title
    if captions and not plan.captions and not caption_note:
        caption_note = "No captions were placed on this edit."
    meta["caption_note"] = caption_note
    ok, why = P.valid(plan)
    if not ok:
        raise RenderError(f"The edit could not be planned: {why}")
    return plan, meta


async def render_plan(plan: P.EditPlan, dst: Path) -> Path:
    """One ffmpeg run for a plan, under the shared one-render slot."""
    sfx_paths = await S.ensure()
    font = ap_render.font_path()
    cmd = G.build_command(plan, dst, sfx_paths, font=font)
    dst.parent.mkdir(parents=True, exist_ok=True)
    timeout = max(settings.autopilot_render_timeout_s,
                  RENDER_X_REALTIME * P.plan_duration(plan))
    async with ap_render._slot:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            dst.unlink(missing_ok=True)
            raise RenderError(f"Rendering took longer than {int(timeout)}s and was stopped.")
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        # -9 is the kernel's OOM killer on this box, not an ffmpeg complaint;
        # say so, or the message reads as a bug in the edit.
        if proc.returncode == -9:
            raise RenderError("The server ran out of memory rendering this edit.")
        tail = (err or b"").decode(errors="replace").strip()[-240:]
        log.warning("auto_edit_render_failed", rc=proc.returncode, error=tail)
        raise RenderError("ffmpeg could not render this edit" + (f": {tail}" if tail else "."))
    return dst


async def make(clip: dict, dst: Path, *, captions: bool, mode: str = "clipper",
                template: str = "suggested", title: str = "", on_stage=None) -> P.EditPlan:
    """Plan and render one CAUGHT clip's edit to `dst`. Raises RenderError
    with a message safe to show the user.

    FULLY AUTOMATIC (owner, 2026-09-28: "I want it to be fully auto after a
    user accepts a clip"). A hook somebody saved on this clip while the picker
    existed is NOT used: nothing in the app asks for one any more, and an
    edit that opens on a stale 8 seconds nobody remembers choosing would be a
    bug that looks like the feature.
    """
    from src.clips import files as clip_files
    src = clip_files.path_for(clip["id"])
    if not src or not src.exists():
        raise RenderError("This clip has no video file to edit.")
    rec = {k: v for k, v in clip.items() if k != "hook"}
    return await make_from(src, rec, dst, captions=captions, mode=mode,
                           template=template, title=title, on_stage=on_stage)


async def make_from(src: Path, rec: dict, dst: Path, *, captions: bool,
                    mode: str = "clipper", template: str = "suggested",
                    title: str = "", on_stage=None) -> P.EditPlan:
    """Plan and render the edit of ANY video file — a caught clip or a file
    in the library (the Clip Editor's Auto Edit style). `rec` carries what
    the builder reads: an `id`, a `channel` for the log, and the `hook`."""
    if not src or not Path(src).exists():
        raise RenderError("There is no video file to edit.")
    clip = rec
    plan, meta = await build_plan(clip, Path(src), captions=captions, mode=mode,
                                  template=template, title=title, on_stage=on_stage)
    # Why there are no captions, when there are none. An attribute rather than a
    # new return type: every caller (and every test double) returns the plan.
    plan.caption_note = meta.get("caption_note", "")
    log.info("auto_edit_rendering", clip_id=clip["id"], hook=plan.hook,
             seconds=round(P.plan_duration(plan), 2), captions=len(plan.captions),
             source=meta.get("source"))
    if on_stage:
        await on_stage("render")
    await render_plan(plan, dst)
    return plan
