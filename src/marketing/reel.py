"""One marketing reel, in the owner's approved V6 format, from one captured clip.

Owner, 2026-10-31: "once a clip is captured, make a marketing reel with the
peak from the clip as a hook". The format is V6 from the "Campaign reel edit"
work (highlightz-reel-v6-twitch-clip-opener-v2.mp4), which the owner approved
after one change ("No music until after the clip. You can't hear the
stream"):

    HOOK        8s at 1x   the clip's peak (src/marketing/peak.py), stream
                           audio clear, "CLIP OF <CHANNEL> · TWITCH" tag
    FAST-FWD    5 beats    the clip from up to 20s before the moment, at ~7x:
                (2.34s)    motion blur, purple tint, Highlightz logo, a
                           progress bar counting the clip's own time; tape
                           sound only, no stream audio, no music
    1x          13 beats   back to normal speed just before the moment, so the
                (6.09s)    payoff and his reaction play again; "▶ 1×" tag
    AD          fixed      V6's ad section from its drop to the end card,
                           with its phonk — the music starts here and only here

The AD and the fast-forward's tape sound are ASSETS cut once from V6 (see
`assets_dir`), so the ad is exactly what the owner approved and changing it
is swapping a file, not editing code. Beats are V6's 128 BPM grid.

One ffmpeg run per reel, under the shared one-render slot (render._slot), so
a reel never runs beside an Autopilot encode on the 2-core box.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

W, H, FPS = 1080, 1920, 30
B = 0.46875                    # one beat at 128 BPM, V6's grid
FF_S = 5 * B                   # 2.34375: V6's fast-forward
REACT_S = 13 * B               # 6.09375: V6's back-to-1x section
REACT_LEAD_S = 2.0             # the 1x replay starts this long before the moment
FF_SRC_MAX_S = 20.0            # at most this much of the clip flashes by (~8.5x)
FF_SRC_MIN_S = 3.0             # less than this to skip and there is no fast-forward
MIN_CLIP_S = 12.0              # shorter clips are not worth a reel

# Asset names inside assets_dir(). ad.mp4 is required; the rest degrade.
AD = "ad.mp4"                  # V6 from the drop to the end, video + phonk
FF_SOUND = "ff.wav"            # V6's fast-forward bed: tape whirr + beat ticks
LOGO = "logo.png"              # the Highlightz mark, RGBA

# V6's fast-forward overlay, in its own 1080x1920 pixels (from its CSS).
LOGO_X, LOGO_Y, LOGO_W = 390, 640, 300
BAR_X, BAR_Y, BAR_W, BAR_H = 190, 1220, 700, 12
TIME_Y = 1252
CHIP_X, CHIP_Y = 60, 236
PURPLE, GOLD, INK = "0xB86ADC", "0xFFD08A", "0xF2EAF7"

ENCODE_X_REALTIME = 12.0       # timeout allowance, like auto_edit's RENDER_X_REALTIME


class ReelError(Exception):
    """A reel could not be made. The message is safe to show the owner."""


def assets_dir() -> Path:
    """Where the V6 assets live: MARKETING_REEL_ASSETS, else the copy shipped
    in the repo (marketing/assets, beside src/)."""
    if settings.marketing_reel_assets:
        return Path(settings.marketing_reel_assets)
    return Path(__file__).resolve().parent.parent.parent / "marketing" / "assets"


def assets_ready() -> bool:
    return (assets_dir() / AD).is_file()


# ── the timeline (pure, tested) ──────────────────────────────────────────────

@dataclass
class Timeline:
    hook: tuple[float, float]
    ff: tuple[float, float] | None
    react: tuple[float, float]

    @property
    def ff_speed(self) -> float:
        return (self.ff[1] - self.ff[0]) / FF_S if self.ff else 0.0

    def as_dict(self) -> dict:
        r = lambda w: None if w is None else [round(w[0], 2), round(w[1], 2)]
        return {"hook": r(self.hook), "ff": r(self.ff), "react": r(self.react),
                "ff_speed": round(self.ff_speed, 2)}


def timeline(duration: float, hook_start: float, hook_end: float, moment: float) -> Timeline:
    """Where each section comes from in the clip, in the clip's own seconds."""
    r0 = max(0.0, moment - REACT_LEAD_S)
    r1 = min(duration, r0 + REACT_S)
    r0 = max(0.0, r1 - REACT_S)                 # near the end: slide back
    f1 = r0
    f0 = max(0.0, f1 - FF_SRC_MAX_S)
    ff = (f0, f1) if f1 - f0 >= FF_SRC_MIN_S else None
    return Timeline(hook=(hook_start, hook_end), ff=ff, react=(r0, r1))


# ── the ffmpeg command ───────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """The Auto Edit's drawtext escaping (both filtergraph passes; see
    graph._esc for the two live failures it was written against)."""
    from src.autopilot.graph import _esc as esc
    return esc(text)


def _chip(font: str, text: str, enable: str = "") -> str:
    """A V6-style tag at the top left: light text on a dark translucent box."""
    parts = [f"fontfile={_esc(font)}", "expansion=none", f"text={_esc(text)}",
             "fontsize=34", f"fontcolor={INK}", f"x={CHIP_X}", f"y={CHIP_Y}",
             "box=1", "boxcolor=0x0A0A0C@0.72", "boxborderw=16"]
    if enable:
        parts.append(f"enable='{enable}'")
    return "drawtext=" + ":".join(parts)


def _fit(src: str, out: str, framing: str) -> str:
    """The 16:9 clip into the 1080x1920 frame. `blur` (the default) keeps the
    whole picture over a blurred blow-up of itself; `fill` crops to fill."""
    if framing == "fill":
        return (f"[{src}]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1[{out}]")
    return (f"[{src}]split=2[{out}b][{out}f];"
            f"[{out}b]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=24:6[{out}bb];"
            f"[{out}f]scale={W}:-2[{out}fs];"
            f"[{out}bb][{out}fs]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


def _clock(secs_expr: str) -> str:
    """drawtext expansion for m:ss of an expression (already escaped)."""
    return (f"%{{eif\\\\:floor(({secs_expr})/60)\\\\:d}}\\\\:"
            f"%{{eif\\\\:mod(floor({secs_expr})\\,60)\\\\:d\\\\:2}}")


def build_command(src: Path, dst: Path, tl: Timeline, *, channel: str,
                  platform: str = "twitch", font: str, framing: str = "blur",
                  assets: Path | None = None) -> list[str]:
    """The whole reel as one ffmpeg command."""
    assets = assets or assets_dir()
    logo = assets / LOGO
    ff_sound = assets / FF_SOUND
    ad = assets / AD
    have_logo = logo.is_file()
    have_ff_sound = ff_sound.is_file()

    cmd = [settings.ffmpeg_path, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
           "-i", str(src)]
    idx = 1
    i_logo = i_ff = None
    if tl.ff and have_logo:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(logo)]
        i_logo, idx = idx, idx + 1
    if tl.ff and have_ff_sound:
        cmd += ["-i", str(ff_sound)]
        i_ff, idx = idx, idx + 1
    cmd += ["-i", str(ad)]
    i_ad = idx

    tag = f"CLIP OF {channel.upper()} · {('KICK' if platform == 'kick' else 'TWITCH')}"
    norm = ("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "loudnorm=I=-15:TP=-1.5:LRA=11,aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    n_v = 3 if tl.ff else 2
    g: list[str] = [f"[0:v]split={n_v}" + "".join(f"[s{k}]" for k in range(n_v)),
                    "[0:a]asplit=2[sa0][sa1]"]

    # Every section's picture is padded with its last frame and cut to the
    # exact length of its sound, so concat cannot drift picture from sound.
    def exact(length: float) -> str:
        return f"tpad=stop_mode=clone:stop_duration=1,trim=duration={length:.5f}"

    def seg(s_in: str, out: str, w: tuple[float, float], extra: str = "") -> None:
        g.append(f"[{s_in}]trim=start={w[0]:.3f}:end={w[1]:.3f},setpts=PTS-STARTPTS,"
                 f"fps={FPS}[{out}r]")
        g.append(_fit(f"{out}r", f"{out}f", framing))
        g.append(f"[{out}f]{extra or 'null'},{exact(w[1] - w[0])},format=yuv420p[{out}]")

    def aseg(s_in: str, out: str, w: tuple[float, float]) -> None:
        length = w[1] - w[0]
        g.append(f"[{s_in}]atrim=start={w[0]:.3f}:end={w[1]:.3f},asetpts=PTS-STARTPTS,"
                 f"{norm},afade=t=in:d=0.02,afade=t=out:st={max(0.0, length - 0.04):.3f}:d=0.04,"
                 f"apad=whole_dur={length:.3f},atrim=end={length:.3f}[{out}]")

    # 1. HOOK
    seg("s0", "vh", tl.hook, _chip(font, tag))
    aseg("sa0", "ah", tl.hook)

    # 2. FAST-FORWARD
    if tl.ff:
        speed = tl.ff_speed
        g.append(f"[s1]trim=start={tl.ff[0]:.3f}:end={tl.ff[1]:.3f},"
                 f"setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS}[ffr]")
        g.append(_fit("ffr", "fff", framing))
        # motion blur, then V6's purple cast
        fx = ["tmix=frames=4", "colorchannelmixer=rr=.78:gg=.62:bb=1.06"]
        g.append(f"[fff]{','.join(fx)},{exact(FF_S)}[ffx]")
        # progress bar: a fixed track, the fill slides in from the left
        g.append(f"color=c=white@0.15:s={BAR_W}x{BAR_H}:r={FPS}:d={FF_S:.5f},format=rgba[trk]")
        g.append(f"color=c={PURPLE}:s={BAR_W}x{BAR_H}:r={FPS}:d={FF_S:.5f},format=rgba[fil]")
        g.append(f"[trk][fil]overlay=x='-{BAR_W}+{BAR_W}*t/{FF_S:.5f}':y=0:shortest=1[bar]")
        g.append(f"[ffx][bar]overlay={BAR_X}:{BAR_Y}:shortest=1[ffb]")
        last = "ffb"
        if i_logo is not None:
            g.append(f"[{i_logo}:v]scale={LOGO_W}:-1,format=rgba,"
                     f"trim=duration={FF_S:.5f}[lg]")
            g.append(f"[{last}][lg]overlay={LOGO_X}:{LOGO_Y}:shortest=1[ffl]")
            last = "ffl"
        f0, f1 = tl.ff
        txt = [
            f"drawtext=fontfile={_esc(font)}:expansion=none:text={_esc('FAST-FORWARD ▶▶')}"
            f":fontsize=56:fontcolor=white:borderw=4:bordercolor=black@0.6"
            f":x=(w-text_w)/2:y={LOGO_Y - 110}",
            f"drawtext=fontfile={_esc(font)}:expansion=none:text={_esc(_mmss(f0))}"
            f":fontsize=30:fontcolor={INK}:x={BAR_X}:y={TIME_Y}",
            f"drawtext=fontfile={_esc(font)}:expansion=none:text={_esc(_mmss(f1))}"
            f":fontsize=30:fontcolor={INK}:x={BAR_X + BAR_W}-text_w:y={TIME_Y}",
            f"drawtext=fontfile={_esc(font)}:expansion=normal"
            f":text={_clock(f'{f0:.3f}+t*{speed:.5f}')}"
            f":fontsize=30:fontcolor={GOLD}:x=(w-text_w)/2:y={TIME_Y}",
        ]
        g.append(f"[{last}]{','.join(txt)},format=yuv420p[vf]")
        if i_ff is not None:
            g.append(f"[{i_ff}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                     f"apad=whole_dur={FF_S:.5f},atrim=end={FF_S:.5f}[af]")
        else:
            g.append(f"anullsrc=r=48000:cl=stereo,atrim=end={FF_S:.5f}[af]")

    # 3. BACK TO 1x
    seg(f"s{n_v - 1}", "vr", tl.react, _chip(font, "▶ 1×", "lt(t,1.2)"))
    aseg("sa1", "ar", tl.react)

    # 4. THE AD (as approved: its own picture and phonk)
    g.append(f"[{i_ad}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[vad]")
    g.append(f"[{i_ad}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aad]")

    parts = ["[vh][ah]"] + (["[vf][af]"] if tl.ff else []) + ["[vr][ar]", "[vad][aad]"]
    g.append("".join(parts) + f"concat=n={len(parts)}:v=1:a=1[v][a]")

    cmd += ["-filter_complex", ";".join(g), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(dst)]
    return cmd


def _mmss(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


# ── running it ───────────────────────────────────────────────────────────────

async def probe(path: Path) -> dict:
    """ffprobe's duration, and whether there is an audio stream."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        d = json.loads(out or b"{}")
    except Exception:
        return {"duration": 0.0, "audio": False}
    try:
        dur = float(d.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"duration": dur,
            "audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


async def make(clip: dict, src: Path, dst: Path, *, preset: str = "default") -> dict:
    """Find the peak, build the reel, render it to `dst`. Returns what was made
    (hook, sections, seconds). Raises ReelError with a readable message."""
    from src.autopilot import render as ap_render
    from src.marketing import peak

    if not assets_ready():
        raise ReelError(f"The ad section is missing ({assets_dir() / AD}).")
    font = ap_render.font_path()
    if not font:
        raise ReelError("The server has no font for the reel's text (AUTOPILOT_FONT).")
    info = await probe(src)
    duration = info["duration"]
    if duration < MIN_CLIP_S:
        raise ReelError(f"The clip is {duration:.1f}s; a reel needs at least {MIN_CLIP_S:g}s.")
    if not info["audio"]:
        raise ReelError("The clip has no audio, and the hook is his reaction.")
    hook = await peak.find(clip, src, duration, preset)
    if hook is None:
        raise ReelError("No hook could be placed in this clip.")
    tl = timeline(duration, hook.start, hook.end, hook.moment_at)
    ad_info = await probe(assets_dir() / AD)
    total = (tl.hook[1] - tl.hook[0]) + (FF_S if tl.ff else 0) + \
            (tl.react[1] - tl.react[0]) + ad_info["duration"]

    cmd = build_command(src, dst, tl, channel=str(clip.get("channel") or "streamer"),
                        platform=str(clip.get("platform") or "twitch"), font=font,
                        framing=settings.marketing_reel_framing)
    dst.parent.mkdir(parents=True, exist_ok=True)
    timeout = max(300.0, ENCODE_X_REALTIME * total)
    async with ap_render._slot:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            dst.unlink(missing_ok=True)
            raise ReelError(f"Rendering took longer than {int(timeout)}s and was stopped.")
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        if proc.returncode == -9:
            raise ReelError("The server ran out of memory rendering the reel.")
        tail = (err or b"").decode(errors="replace").strip()[-300:]
        log.warning("marketing_reel_render_failed", rc=proc.returncode, error=tail)
        raise ReelError("ffmpeg could not render the reel" + (f": {tail}" if tail else "."))
    return {"hook": hook.as_dict(), "timeline": tl.as_dict(), "seconds": round(total, 1)}
