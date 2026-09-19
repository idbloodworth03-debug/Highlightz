"""The five sound effects, as WAV files ffmpeg can mix in.

THE PROBLEM THIS SOLVES. The browser editor synthesizes whoosh, hit, pop,
riser and ding in WebAudio — oscillators, filtered noise and gain envelopes
(`SFX` in aurora_html.py). render.py's docstring says plainly that the
server reproduces none of it, so every automatic edit until now went out
silent apart from the clip's own audio. A clipper cannot post that as their
own content.

WHY FILES AND NOT A FILTERGRAPH. Every one of these could in principle be
built inside the render's own graph with aevalsrc and friends. That would
add five more generators and a dozen filters to a graph that already has
twenty, rebuilt for every clip, and each one would be a new way for the
whole render to fail. Generating them ONCE into WAVs and mixing the files is
a smaller idea: the render's graph gains one input per cue and nothing else,
and a broken effect is a missing file rather than a failed post.

HOW CLOSE THEY ARE. The tonal ones — hit, pop, ding — are exact: a linear
chirp has a closed-form phase and aevalsrc evaluates it directly. The noisy
ones — whoosh, riser — are approximations: WebAudio sweeps a bandpass across
the sound, and ffmpeg's filters take a fixed frequency. A static band plus an
envelope is what they get, which under a 0.5s transition is indistinguishable
to anybody not looking at a spectrogram. Said out loud here rather than
discovered later by someone comparing the two renderers.

GENERATED ON FIRST USE, not shipped: they are a few KB each, deterministic,
and a binary in the repo is a binary somebody has to trust.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

KINDS = ("whoosh", "hit", "pop", "riser", "ding")
RATE = 48000          # match the render's -ar, so amix never resamples


def _chirp(f0: float, f1: float, dur: float) -> str:
    """Phase for a linear sweep from f0 to f1 over `dur`.

    f(t) = f0 + (f1-f0)·t/T, and phase is its integral:
        2π · (f0·t + (f1-f0)·t² / 2T)
    Writing the frequency straight into sin() instead — sin(2π·f(t)·t) — is
    the classic mistake: it sweeps at twice the intended rate and lands an
    octave out.
    """
    k = (f1 - f0) / (2.0 * dur)
    return f"sin(2*PI*({f0:.4f}*t+{k:.4f}*t*t))"


# Each entry is the filter/source chain that produces one effect, at 48k mono.
# Kept as data so a test can assert the shape without running ffmpeg.
def _recipe(kind: str) -> tuple[str, float]:
    if kind == "hit":
        # 140 Hz down to 38 in 0.28s, with a short noise click on the front.
        return (f"aevalsrc='{_chirp(140, 38, 0.28)}':d=0.35:s={RATE},"
                f"afade=t=out:st=0.06:d=0.29:curve=exp,volume=0.9", 0.35)
    if kind == "pop":
        return (f"aevalsrc='{_chirp(760, 320, 0.07)}':d=0.12:s={RATE},"
                f"afade=t=out:st=0.01:d=0.11:curve=exp,volume=0.8", 0.12)
    if kind == "ding":
        # The browser's two partials, 1320 and 2640, at its own mix.
        return (f"aevalsrc='0.6*sin(2*PI*1320*t)+0.25*sin(2*PI*2640*t)':"
                f"d=0.7:s={RATE},afade=t=out:st=0.05:d=0.65:curve=exp,volume=0.8", 0.7)
    if kind == "whoosh":
        return (f"anoisesrc=d=0.45:c=white:r={RATE}:a=0.7,"
                f"highpass=f=320,lowpass=f=3200,"
                f"afade=t=in:st=0:d=0.12,afade=t=out:st=0.18:d=0.27,volume=1.4", 0.45)
    # riser: builds and stops dead, so the cut lands on silence
    return (f"anoisesrc=d=0.85:c=white:r={RATE}:a=0.7,"
            f"highpass=f=500,lowpass=f=4200,"
            f"afade=t=in:st=0:d=0.8:curve=exp,afade=t=out:st=0.8:d=0.05,volume=1.2", 0.85)


def root() -> Path:
    return Path(settings.local_storage_path) / "sfx"


def path_for(kind: str) -> Path:
    return root() / f"{kind}.wav"


def command(kind: str) -> list[str]:
    chain, _dur = _recipe(kind)
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-filter_complex", f"{chain}[a]", "-map", "[a]",
            "-c:a", "pcm_s16le", "-ar", str(RATE), "-ac", "1",
            str(path_for(kind))]


async def ensure() -> dict:
    """Every effect that exists on disk, generating any that do not.

    Returns {kind: path} for the ones that are really there — the caller
    drops cues it has no file for, so a generator that fails costs one whoosh
    rather than the post.
    """
    root().mkdir(parents=True, exist_ok=True)
    out: dict = {}
    for kind in KINDS:
        p = path_for(kind)
        if p.exists() and p.stat().st_size > 44:      # bigger than a WAV header
            out[kind] = p
            continue
        try:
            proc = await asyncio.create_subprocess_exec(
                *command(kind),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            _o, err = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0 and p.exists():
                out[kind] = p
                log.info("sfx_generated", kind=kind, path=str(p))
            else:
                log.warning("sfx_generate_failed", kind=kind,
                            error=(err or b"").decode()[:300])
        except Exception as exc:
            log.warning("sfx_generate_error", kind=kind, error=str(exc))
    return out
