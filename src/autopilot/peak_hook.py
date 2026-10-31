"""Find a clip's hook from its score peak — no human picking it.

Owner, 2026-10-31: build reels that "find these clips, specifically duxxion
for now, use the peak as hooks and create instagram reels". The hook is the
5-10s the plan-based edit opens on before playing the whole clip
(`plan.build`, "ONE CLIP, AND THE HOOK" in HANDOFF). Until now a hook only
existed if somebody picked one by hand; this places it automatically.

WHERE THE PEAK IS IN THE FILE. The score timeline is not stored per clip
(the engine keeps it in a rolling in-memory deque), but it does not need to
be: the engine fires at the CREST of the score (`_monitor_and_fire` holds a
3s settle to catch the top, never waits for decay), and the stream worker
cuts `[fired_at - pre_roll, fired_at + tail]` out of the capture buffer. So
in every captured file the score peak sits `tail` seconds before the END.
Measured from the end, not the start, because the start is the side that
moves: input seeking snaps it to a keyframe, and a buffer that did not reach
far enough back makes it shorter. The tail is always waited for.

    tail = the preset's post_roll      a caught clip (rules.py: 20-32s)
    tail = 10                          a manual Clip-it (main._force_clip)
    tail = 5                           a ~30s Twitch clip fetched because
                                       capture missed (ends a beat after
                                       the API call)

WHY THE AUDIO THEN DECIDES. The score is mostly CHAT, and chat lags the
moment: viewers see it a few seconds late, react, then type ("Chat peaks
~8s after the kill", rules.py). The streamer's own reaction — the yell —
is in the audio, in sync with the picture. So the score peak says WHERE to
look and the loudness inside the seconds before it says WHEN: the hook is
built around the loudest second of `[peak - LOOKBACK_S, peak + LOOKAHEAD_S]`.
If the audio is flat there (no clear spike), the score peak alone places it.

Nothing here writes anything. It reads the file's audio once, through
ffmpeg, and returns a window in the clip's own seconds.
"""

from __future__ import annotations

import asyncio
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

# The hook the edit opens on. plan.HOOK_MIN_S..HOOK_MAX_S is the rule; 8s is
# the length the old picker defaulted to and the one the owner rendered first.
HOOK_S = 8.0
# How much of the build-up the hook keeps before the loudest second. Short on
# purpose: the hook is bait, it has to land fast, but a yell with zero context
# is noise. ~3s of the play, then the reaction.
LEAD_S = 3.0

# Where to look for the moment, relative to the score peak.
LOOKBACK_S = 14.0     # chat crest lags the picture by up to ~10-12s
LOOKAHEAD_S = 2.0     # the 3s settle can put the fire just past the crest

# Loudness is read in 100ms steps and smoothed over 1s, so one click or a
# keyboard slam does not outrank a sustained yell.
STEP_S = 0.1
SMOOTH_S = 1.0
# A spike has to stand this far above the window's typical level to count as
# the moment. Below it the audio is flat and the score peak places the hook.
SPIKE_DB = 6.0
SILENCE_DB = -70.0    # astats says -inf for digital silence; floor it here

TWITCH_CLIP_MAX_S = 31.5   # Create Clip makes ~30s clips; capture cuts are longer
TWITCH_CLIP_TAIL_S = 5.0
MANUAL_TAIL_S = 10.0       # main._force_clip: pre_roll=60, post_roll=10

LOUDNESS_TIMEOUT_S = 60.0


@dataclass
class Hook:
    start: float
    end: float
    peak_at: float | None      # where we think the score peaked, file seconds
    moment_at: float           # the second the hook is built around
    source: str                # "audio" | "score" | "audio-only"

    def as_clip_hook(self) -> dict:
        """The shape `plan.hook_window` reads off a clip."""
        return {"start": round(self.start, 2), "end": round(self.end, 2)}

    def as_dict(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2),
                "peak_at": None if self.peak_at is None else round(self.peak_at, 2),
                "moment_at": round(self.moment_at, 2), "source": self.source}


# ── where the score peaked ───────────────────────────────────────────────────

def tail_for(clip: dict, duration: float, preset: str = "default") -> float | None:
    """Seconds between the score peak and the end of this clip's file, or
    None when the file was not cut around a score peak at all (a crowd
    suggestion: the moment came from viewers' clips, not our score)."""
    if clip.get("suggested"):
        return None
    if any(str(s.get("type", "")).upper().endswith("MANUAL")
           for s in (clip.get("trigger_signals") or []) if isinstance(s, dict)):
        return MANUAL_TAIL_S
    if (clip.get("platform") or "twitch") == "twitch" and 0 < duration <= TWITCH_CLIP_MAX_S:
        # Capture missed and the file is Twitch's own ~30s clip (clips/fetch.py),
        # which ends a few seconds after the call that made it.
        return TWITCH_CLIP_TAIL_S
    from src.trigger.rules import get_rules
    return float(get_rules(str(clip.get("channel") or ""), preset or "default").post_roll)


def score_peak_at(clip: dict, duration: float, preset: str = "default") -> float | None:
    """Where in the file (seconds from its start) the score crested."""
    if duration <= 0:
        return None
    tail = tail_for(clip, duration, preset)
    if tail is None:
        return None
    return max(0.0, min(duration, duration - tail))


# ── loudness ─────────────────────────────────────────────────────────────────

def loudness_command(src: Path, out_txt: Path) -> list[str]:
    """ffmpeg: the first audio stream's RMS level every 100ms, printed to a
    file. Audio only (`-vn`), resampled to 16k mono first — the level is all
    we want, and a 60s clip decodes in well under a second this way."""
    n = int(round(16000 * STEP_S))
    af = (f"aresample=16000,aformat=channel_layouts=mono,"
          f"asetnsamples=n={n}:p=0,"
          f"astats=metadata=1:reset=1,"
          f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file={out_txt.as_posix()}")
    return [settings.ffmpeg_path, "-hide_banner", "-nostats", "-loglevel", "error",
            "-i", str(src), "-vn", "-map", "0:a:0", "-af", af, "-f", "null", "-"]


def parse_loudness(text: str) -> list[tuple[float, float]]:
    """[(t, dB)] from ametadata's print format:

        frame:0    pts:0       pts_time:0
        lavfi.astats.Overall.RMS_level=-23.4
    """
    out: list[tuple[float, float]] = []
    t: float | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            t = None
            for part in line.split():
                if part.startswith("pts_time:"):
                    try:
                        t = float(part.split(":", 1)[1])
                    except ValueError:
                        t = None
        elif line.startswith("lavfi.astats.Overall.RMS_level=") and t is not None:
            raw = line.split("=", 1)[1]
            try:
                db = float(raw)
            except ValueError:
                db = SILENCE_DB
            if not math.isfinite(db):
                db = SILENCE_DB
            out.append((t, max(SILENCE_DB, db)))
    return out


async def loudness(src: Path) -> list[tuple[float, float]]:
    """The clip's loudness curve. Empty on any failure (no audio stream, ffmpeg
    error, timeout): the caller then places the hook from the score alone."""
    fd, name = tempfile.mkstemp(prefix="hz-loud-", suffix=".txt")
    os.close(fd)
    out_txt = Path(name)
    try:
        proc = await asyncio.create_subprocess_exec(
            *loudness_command(Path(src), out_txt),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=LOUDNESS_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            log.warning("peak_hook_loudness_timeout", src=str(src))
            return []
        if proc.returncode != 0:
            log.info("peak_hook_no_audio", src=str(src),
                     error=(err or b"").decode(errors="replace")[-200:])
            return []
        return parse_loudness(out_txt.read_text(errors="replace"))
    except Exception as exc:
        log.warning("peak_hook_loudness_failed", src=str(src), error=str(exc))
        return []
    finally:
        out_txt.unlink(missing_ok=True)


def smooth(curve: list[tuple[float, float]], width_s: float = SMOOTH_S) -> list[tuple[float, float]]:
    """Centred moving average over `width_s`, in dB."""
    if not curve:
        return []
    half = max(1, int(round(width_s / STEP_S / 2)))
    vals = [db for _, db in curve]
    out = []
    for i, (t, _) in enumerate(curve):
        lo, hi = max(0, i - half), min(len(vals), i + half + 1)
        out.append((t, sum(vals[lo:hi]) / (hi - lo)))
    return out


# ── placing the hook ─────────────────────────────────────────────────────────

def _window_around(moment: float, duration: float,
                   length: float = HOOK_S, lead: float = LEAD_S) -> tuple[float, float] | None:
    """`length` seconds with `moment` `lead` seconds in, slid to stay inside
    the clip. None when the clip is shorter than the shortest hook."""
    from src.autopilot import plan as P
    if duration < P.HOOK_MIN_S:
        return None
    length = min(length, P.HOOK_MAX_S, duration)
    start = moment - lead
    start = max(0.0, min(start, duration - length))
    end = start + length
    if end - start < P.HOOK_MIN_S - 1e-6:
        return None
    return start, end


def pick(curve: list[tuple[float, float]], duration: float,
         peak_at: float | None) -> Hook | None:
    """The hook, from the loudness curve and the score peak (either may be
    missing). Pure: the tests drive it with made-up curves."""
    if duration <= 0:
        return None
    sm = smooth(curve)

    if peak_at is not None:
        lo, hi = peak_at - LOOKBACK_S, peak_at + LOOKAHEAD_S
    else:
        # No score peak to anchor on (a crowd suggestion): the whole clip,
        # minus the first and last second where a cut's edges live.
        lo, hi = 1.0, duration - 1.0
    lo, hi = max(0.0, lo), min(duration, hi)

    region = [(t, db) for t, db in sm if lo <= t <= hi]
    if region:
        t_best, db_best = max(region, key=lambda p: p[1])
        # "Typical" level of the region: its median, so a long quiet stretch
        # and a long loud one are both judged fairly.
        levels = sorted(db for _, db in region)
        typical = levels[len(levels) // 2]
        if db_best > SILENCE_DB + 1 and db_best - typical >= SPIKE_DB:
            w = _window_around(t_best, duration)
            if w:
                return Hook(w[0], w[1], peak_at, t_best,
                            "audio" if peak_at is not None else "audio-only")

    if peak_at is None:
        if not region:
            return None
        # Flat audio and no score: the loudest second is still the best guess.
        t_best = max(region, key=lambda p: p[1])[0]
        w = _window_around(t_best, duration)
        return Hook(w[0], w[1], None, t_best, "audio-only") if w else None

    # Flat audio: the moment is a few seconds BEFORE the chat crest. Put the
    # hook's end just past the peak so it holds the play and the first of the
    # reaction.
    moment = max(0.0, peak_at - (HOOK_S - LEAD_S - LOOKAHEAD_S))
    w = _window_around(moment, duration)
    return Hook(w[0], w[1], peak_at, moment, "score") if w else None


async def find(clip: dict, src: Path, duration: float, preset: str = "default") -> Hook | None:
    """The hook for one clip's file. None only when the clip is too short to
    have one; every other failure degrades to the score-peak placement."""
    peak = score_peak_at(clip, duration, preset)
    curve = await loudness(Path(src))
    hook = pick(curve, duration, peak)
    log.info("peak_hook", clip_id=clip.get("id"), channel=clip.get("channel"),
             duration=round(duration, 2), preset=preset,
             peak_at=None if peak is None else round(peak, 2),
             samples=len(curve), hook=hook.as_dict() if hook else None)
    return hook
