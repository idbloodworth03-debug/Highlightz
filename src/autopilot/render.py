"""The server-side edit: one ffmpeg pass from the captured clip to a 1080×1920
MP4 in one of the editor's looks.

The browser editor paints every frame on a canvas; the server has no canvas
and no browser, so this is the same idea in ffmpeg filters, deliberately
limited to what a filter graph does well:

    full   centred 9:16 crop of the picture (the editor's Full Frame)
    punch  the same, zoomed 1.25× (Punch In)
    blur   the whole 16:9 picture over a blurred, filled copy (Blur Bars)
    hook   full, with a bold title across the top (Hook Title)

plus a title on any template, burned-in captions from the server's own
Whisper pass (one drawtext per cue), and a short fade in and out. No
Cam + Game: that needs a human to point at the camera. Sound effects are
synthesized in the browser and are not reproduced here.

Fonts: drawtext needs a real TTF. DejaVu Sans Bold ships with Ubuntu and
is the default; when the file is missing the title and captions are skipped
and the log says so, rather than the whole render failing over a font.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

W, H = 1080, 1920
FADE_S = 0.4
MAX_CAPTION_CUES = 120


class RenderError(RuntimeError):
    """Safe to show the user."""


def _esc(text: str) -> str:
    """Escape for a drawtext `text=` value inside a filter graph. Order
    matters: the backslash first, then the characters that end a value."""
    out = text.replace("\\", "\\\\")
    for ch in ("'", ":", ",", ";", "[", "]", "%"):
        out = out.replace(ch, "\\" + ch)
    return out


def font_path() -> str:
    f = settings.autopilot_font
    return f if f and os.path.exists(f) else ""


def _drawtext(font: str, text: str, size: int, y: str, box: bool, enable: str = "") -> str:
    parts = [f"fontfile={font}", f"text='{_esc(text)}'", f"fontsize={size}",
             "fontcolor=white", "x=(w-text_w)/2", f"y={y}"]
    if box:
        parts += ["box=1", "boxcolor=black@0.55", "boxborderw=18"]
    else:
        parts += ["borderw=6", "bordercolor=black@0.85"]
    if enable:
        parts.append(f"enable='{enable}'")
    return "drawtext=" + ":".join(parts)


def video_filter(template: str, *, title: str = "", captions: list | None = None,
                 duration: float = 0.0, font: str = "") -> str:
    """The -vf / -filter_complex text for one render."""
    if template == "blur":
        base = ("[0:v]split=2[bg][fg];"
                f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                "boxblur=24:6[bgb];"
                f"[fg]scale={W}:-2[fgs];"
                "[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    elif template == "punch":
        base = f"[0:v]scale=-2:{int(H * 1.25)},crop={W}:{H}"
    else:                                   # full, hook
        base = f"[0:v]scale=-2:{H},crop={W}:{H}"
    chain = [base]
    if font and title:
        top = template == "hook" or True     # a title always sits at the top
        chain.append(_drawtext(font, title, 76 if template == "hook" else 64,
                               f"h*0.12-text_h/2", box=template == "hook"))
    if font and captions:
        for cue in captions[:MAX_CAPTION_CUES]:
            s, e, t = float(cue[0]), float(cue[1]), str(cue[2]).strip()
            if not t or e <= s:
                continue
            chain.append(_drawtext(font, t.upper() if len(t) < 40 else t, 58,
                                   "h*0.78-text_h/2", box=True,
                                   enable=f"between(t\\,{s:.2f}\\,{e:.2f})"))
    chain.append(f"fade=t=in:st=0:d={FADE_S}")
    if duration and duration > FADE_S * 3:
        chain.append(f"fade=t=out:st={duration - FADE_S:.2f}:d={FADE_S}")
    chain.append("format=yuv420p")
    return ",".join(chain) + "[v]"


def build_command(src: Path, dst: Path, template: str, *, title: str = "",
                  captions: list | None = None, duration: float = 0.0,
                  font: str = "") -> list[str]:
    vf = video_filter(template, title=title, captions=captions, duration=duration, font=font)
    af = f"afade=t=in:st=0:d={FADE_S}"
    if duration and duration > FADE_S * 3:
        af += f",afade=t=out:st={duration - FADE_S:.2f}:d={FADE_S}"
    return [settings.ffmpeg_path, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "error",
            "-y", "-i", str(src),
            "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
            "-af", af,
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(dst)]


# One render at a time: this is a 1-vCPU box that is also scoring live
# streams, and ffmpeg with libx264 will take every core it is given.
_slot = asyncio.Semaphore(1)


async def render(src: Path, dst: Path, template: str, *, title: str = "",
                 captions: list | None = None, duration: float = 0.0) -> Path:
    font = font_path()
    if (title or captions) and not font:
        log.warning("autopilot_font_missing", path=settings.autopilot_font,
                    note="title and captions skipped; set AUTOPILOT_FONT to a .ttf")
    cmd = build_command(src, dst, template, title=title, captions=captions,
                        duration=duration, font=font)
    dst.parent.mkdir(parents=True, exist_ok=True)
    async with _slot:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=settings.autopilot_render_timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            dst.unlink(missing_ok=True)
            raise RenderError(f"Rendering took longer than {settings.autopilot_render_timeout_s}s and was stopped.")
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        tail = (err or b"").decode(errors="replace").strip()[-240:]
        log.warning("autopilot_render_failed", rc=proc.returncode, error=tail)
        raise RenderError("ffmpeg could not render this clip" + (f": {tail}" if tail else "."))
    return dst
