"""A picture for a clip that arrives without one (owner, 2026-10-15: "Fix the
thumbnails for Kick I need those to not be the ugly brown images").

A Twitch clip comes with Twitch's own thumbnail. A Kick clip has nothing: Kick
has no clip API, the clip IS the file Highlightz cut from the live capture, so
every Kick card fell back to a gradient hashed from the channel name — dark
brown for a good share of names. The video is on our disk, so the picture is
one frame of it.

THE SAME RECIPE AS THE EDITOR'S FILMSTRIP (src/uploads/thumbs.py): -ss before
-i (keyframe seek), one thread, one frame, MJPEG on stdout. 640 px wide — a
card is ~320 CSS px, twice that on a 2x screen — about 25 KB. From the middle
of the clip, where the moment is; from the first frame if that fails.

MADE WHEN THE VIDEO LANDS, KEPT WHEN IT GOES. dashboard.on_clip_file_ready
asks for it as soon as a cut or a fetch finishes, and GET /clips/{id}/thumb
makes it on first view for clips that predate this. The JPEG sits beside the
MP4 and survives the size cap that evicts the MP4 (on prod the store's horizon
is about a day), so a clip reviewed the next morning still shows the moment.
It goes with the clip — see files.delete and files.sweep.

BUT NOT PAST THE RETENTION CEILING. A frame is something saved from the
broadcast, and the Terms and Privacy Policy say such a file is gone within
`clip_file_max_age_days` (the number of days they print). So the picture is
stamped with its video's mtime when it is made, and files.sweep ages pictures
on that clock exactly as it ages videos.

TWO AT A TIME, ONE PER CLIP. A Library of fifty Kick clips nobody has opened
since this shipped asks for fifty frames at once; the gate keeps that to two
ffmpeg processes on the one-vCPU box, and the in-flight map means the eager
request and the browser's request for the same clip share one run.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import structlog

from config.settings import settings
from src.clips import files as clip_files

log = structlog.get_logger(__name__)

WIDTH = 640
_TIMEOUT_S = 20
_CONCURRENCY = 2

_gate: asyncio.Semaphore | None = None
_gate_loop = None
_inflight: dict[str, asyncio.Task] = {}
_background: set[asyncio.Task] = set()


def command(src: Path, t: float) -> list[str]:
    # settings.ffmpeg_path, as the capture uses: the binary that cut the clip.
    return [settings.ffmpeg_path, "-v", "error", "-threads", "1",
            "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
            "-vf", f"scale={WIDTH}:-2",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5", "-"]


def _semaphore() -> asyncio.Semaphore:
    # One per event loop: a Semaphore remembers the loop it first waited on,
    # and the test suite runs many loops in one process.
    global _gate, _gate_loop
    loop = asyncio.get_running_loop()
    if _gate is None or _gate_loop is not loop:
        _gate, _gate_loop = asyncio.Semaphore(_CONCURRENCY), loop
    return _gate


async def _frame(src: Path, t: float) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *command(src, t), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        jpg, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return b""
    return jpg if proc.returncode == 0 and jpg else b""


async def _make(clip_id: str, at: float) -> Path | None:
    src, dst = clip_files.path_for(clip_id), clip_files.thumb_path_for(clip_id)
    if not src or not dst:
        return None
    async with _semaphore():
        if clip_files.thumb_exists(clip_id):
            return dst
        if not clip_files.exists(clip_id):
            return None
        try:
            # The picture takes its video's age, not today's: files.sweep ages
            # pictures on the video's clock, so a frame of a recording made 29
            # days ago is 29 days old the moment it exists.
            born = src.stat().st_mtime
        except OSError:
            return None
        jpg = b""
        for t in ((at, 0.0) if at > 0 else (0.0,)):
            jpg = await _frame(src, t)
            if jpg:
                break
        if not jpg:
            log.info("clip_thumb_failed", clip_id=clip_id)
            return None
        tmp = dst.with_name(dst.name + ".tmp")
        try:
            tmp.write_bytes(jpg)
            os.utime(tmp, (born, born))
            tmp.replace(dst)
        except OSError as exc:
            log.warning("clip_thumb_write_failed", clip_id=clip_id, error=str(exc))
            try:
                tmp.unlink()
            except OSError:
                pass
            return None
    clip_files._forget_scan()
    return dst


async def ensure(clip_id: str, at: float = 0.0) -> Path | None:
    """The clip's picture, made now if it can be. None when there is no video
    to take one from and none was made before. `at` is the second to take it
    from — the middle of the clip, by the callers."""
    if clip_files.thumb_exists(clip_id):
        return clip_files.thumb_path_for(clip_id)
    loop = asyncio.get_running_loop()
    task = _inflight.get(clip_id)
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_make(clip_id, at))
        _inflight[clip_id] = task
        task.add_done_callback(
            lambda t, cid=clip_id: _inflight.pop(cid, None) if _inflight.get(cid) is t else None)
    try:
        # Shielded: a browser that gives up on the request must not cancel
        # the run another request (or the eager call) is waiting on.
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("clip_thumb_error", clip_id=clip_id, error=str(exc))
        return None


def make_soon(clip_id: str, at: float = 0.0) -> None:
    """Start making it in the background — for the moment a video lands."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(ensure(clip_id, at))
    _background.add(task)
    task.add_done_callback(_background.discard)
