"""The Clip Editor's filmstrip, cut on the server.

Owner, 2026-09-24: "The bottom bar on the clip editor is just a black screen.
Nothing renders I need this fixed." The strip was built in the BROWSER: a
hidden <video> seeked to sixteen points and drawn to a canvas after each
`seeked`. Reproduced in Chromium it works — but only for codecs that browser
software-decodes; the uploads here are H.264, which a user's Chrome decodes
in HARDWARE, and a hardware-decoded frame from a video that is not on the
page can come back to drawImage as black. The code also painted black first
and drew after a 1.5s timeout whether or not a frame had arrived, so "no
frame yet" and "black frame" looked the same.

The server has ffmpeg and the file on local disk, so it cuts the sixteen
frames itself: one short ffmpeg run per frame, SEQUENTIAL on purpose — one
process with sixteen inputs would hold sixteen 1080p decoders at once on a
2 GB box. Each run seeks by keyframe (-ss before -i) and decodes a frame or
two, so the whole strip is a few seconds, once, then cached beside the file.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

THUMB_N = 16                 # must match THUMB_N in aurora_html.py
TW, TH = 128, 72             # the same size the browser strip draws at
_locks: dict[str, asyncio.Lock] = {}


def cache_path(src: Path) -> Path:
    return src.with_name(src.name + ".thumbs.json")


async def _duration(src: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(src),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    try:
        return float((out or b"0").decode().strip() or 0)
    except ValueError:
        return 0.0


def command(src: Path, t: float) -> list[str]:
    """One frame at `t`, cover-cropped to the strip's tile, as a JPEG on stdout."""
    return ["ffmpeg", "-v", "error", "-threads", "1",
            "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
            "-vf", f"scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH}",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5", "-"]


def times(duration: float) -> list[float]:
    """The same sixteen points the browser strip used: each tile's middle."""
    return [min(duration - 0.05, (i + 0.5) / THUMB_N * duration) for i in range(THUMB_N)]


async def strip(src: Path) -> list[str]:
    """Sixteen data: URLs, "" for any frame that could not be cut. Cached
    beside the file and reused until the file changes."""
    cache = cache_path(src)
    try:
        if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
            return json.loads(cache.read_text())["thumbs"]
    except Exception:
        pass
    lock = _locks.setdefault(str(src), asyncio.Lock())
    async with lock:
        if cache.exists():
            try:
                return json.loads(cache.read_text())["thumbs"]
            except Exception:
                pass
        dur = await _duration(src)
        if dur <= 0:
            return []
        out: list[str] = []
        for t in times(dur):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command(src, t), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
                jpg, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
                out.append("data:image/jpeg;base64," + base64.b64encode(jpg).decode()
                           if proc.returncode == 0 and jpg else "")
            except Exception:
                out.append("")
        if any(out):
            try:
                cache.write_text(json.dumps({"thumbs": out}))
            except OSError:
                log.warning("thumbs_cache_write_failed", path=str(cache))
        return out
