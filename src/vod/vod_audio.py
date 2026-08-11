"""Per-second loudness for a finished broadcast, so a VOD scan can hear it.

WHY THIS EXISTS. The VOD scanner scored chat and nothing else. That left it
blind to the single heaviest non-chat signal in the live engine — AUDIO_SPIKE,
22 of the live pool's 110 points — so a moment where the streamer screams but
chat only murmurs scored well live and was invisible to a scan of the same
stream. Measured side by side, the live bot produced more and better clips than
a scan of its own VOD, and this was most of the quality half of that gap.

HOW IT WORKS. Exactly the pipeline AudioMeter already uses for live streams —
streamlink pulls an audio-only rendition, FFmpeg decodes it to raw 8 kHz mono
PCM, and RMS is computed straight off the samples. No file is written and no
video is ever downloaded or re-hosted, so this stays inside the same compliance
line as everything else: we measure loudness, we do not keep audio.

The one difference from live is direction of time. Live reads a socket at
whatever rate reality supplies; here the whole VOD is decoded as fast as the
network allows, then replayed second by second through the SAME baseline and
peak-decay maths the live engine uses (see score_timeline), so a given moment
scores the same whether it was caught live or found afterwards.

FAILS SOFT, ALWAYS. Missing streamlink, missing FFmpeg, a subscriber-only VOD,
a network stall — every one of them returns an empty timeline and the scan
continues chat-only, exactly as it behaved before this module existed. A scan
that finds fewer moments is a worse scan; a scan that raises is a broken
feature.
"""

from __future__ import annotations

import asyncio
import os

import structlog

from config.settings import settings
from src.ingestion.audio_meter import _rms_db, _SILENCE_DB

log = structlog.get_logger(__name__)

# 8 kHz mono s16le → 16000 bytes is exactly one second, which makes the chunk
# index the offset in seconds with no bookkeeping.
_SAMPLE_RATE   = 8000
_BYTES_PER_SEC = _SAMPLE_RATE * 2

# A scan must not hang a worker forever on a stalled download. Generous, because
# a long VOD over a slow link is legitimate; the job reports progress meanwhile.
_DEFAULT_TIMEOUT_S = 45 * 60


async def extract_db_timeline(
    vod_url: str,
    on_progress=None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict[int, float]:
    """{offset_second: dBFS} for the whole VOD, or {} if audio is unavailable.

    `on_progress(seconds_decoded)` is called about once a second of decoded
    audio so the job can show movement — without it a multi-minute decode looks
    identical to a hung scan.
    """
    r_fd, w_fd = os.pipe()
    sl = ff = None
    timeline: dict[int, float] = {}
    try:
        sl = await asyncio.create_subprocess_exec(
            settings.streamlink_path, "--stdout", "--loglevel", "error",
            vod_url, "audio_only,worst",
            stdout=w_fd, stderr=asyncio.subprocess.DEVNULL,
        )
        os.close(w_fd); w_fd = -1

        ff = await asyncio.create_subprocess_exec(
            settings.ffmpeg_path, "-hide_banner", "-nostats",
            "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(_SAMPLE_RATE),
            "-f", "s16le", "pipe:1",
            stdin=r_fd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        os.close(r_fd); r_fd = -1

        async def _read() -> None:
            sec = 0
            while True:
                # readexactly, not read: a short read would silently shift every
                # subsequent second's offset, and a scan whose timestamps drift
                # is worse than one with no audio at all.
                try:
                    chunk = await ff.stdout.readexactly(_BYTES_PER_SEC)
                except asyncio.IncompleteReadError as exc:
                    if len(exc.partial) >= _BYTES_PER_SEC // 2:
                        timeline[sec] = _rms_db(exc.partial)
                    return
                timeline[sec] = _rms_db(chunk)
                sec += 1
                if on_progress and sec % 30 == 0:
                    on_progress(sec)

        await asyncio.wait_for(_read(), timeout=timeout_s)
        log.info("vod_audio_decoded", seconds=len(timeline), url=vod_url)
        return timeline

    except asyncio.TimeoutError:
        log.warning("vod_audio_timeout", url=vod_url, decoded=len(timeline))
        return timeline          # partial audio still beats none
    except FileNotFoundError as exc:
        log.warning("vod_audio_binary_missing", error=str(exc))
        return {}
    except Exception as exc:
        log.warning("vod_audio_failed", url=vod_url, error=str(exc))
        return {}
    finally:
        for fd in (r_fd, w_fd):
            if fd >= 0:
                try: os.close(fd)
                except OSError: pass
        for proc in (ff, sl):
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass


# ── replay of the live engine's audio maths ──────────────────────────────────
# Lifted deliberately from TriggerEngine.evaluate() rather than reinvented: a
# VOD moment has to score the same as the live one it corresponds to, and two
# separate implementations of "how loud is loud" would drift the first time
# either is tuned. Constants mirror engine.py.

_WARMUP        = 30      # samples before a spike can register (~30s)
_SPIKE_RANGE_DB = 15.0   # dB above baseline that maps to a full 1.0
_PEAK_DECAY_DB  = 1.5    # per second, once the peak stops being renewed


def score_timeline(db_by_sec: dict[int, float]) -> dict[int, float]:
    """{offset_second: audio_score 0-1} from a dB timeline.

    Runs forward through the VOD one second at a time, so the baseline warms up
    and decays exactly as it does live. Deliberately NOT a global normalisation
    over the whole file: that would let a single loud moment at hour three
    change how hour one is scored, which the live engine can never do.
    """
    if not db_by_sec:
        return {}
    out: dict[int, float] = {}
    baseline = peak = 0.0
    samples = 0
    for sec in range(max(db_by_sec) + 1):
        db = db_by_sec.get(sec, _SILENCE_DB)
        if db <= _SILENCE_DB:
            out[sec] = 0.0
            continue
        if samples < _WARMUP:
            baseline = db if samples == 0 else 0.7 * baseline + 0.3 * db
            peak = baseline
            samples += 1
            out[sec] = 0.0
            continue
        baseline = 0.983 * baseline + 0.017 * db
        peak = db if db >= peak else max(baseline, peak - _PEAK_DECAY_DB)
        samples += 1
        out[sec] = min(max((peak - baseline) / _SPIKE_RANGE_DB, 0.0), 1.0)
    return out
