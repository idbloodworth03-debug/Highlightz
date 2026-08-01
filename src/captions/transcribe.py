"""
Auto-captions — Whisper on the droplet.

THE CONSTRAINT THAT SHAPES EVERYTHING HERE: this runs on the same 1 vCPU that
is already running a streamlink+ffmpeg audio meter for every monitored channel.
Clip detection is the product; captioning is a convenience. If the two ever
compete, detection has to win, because a missed highlight is a broken promise
and a slow caption is only a slow caption.

So:
  * ONE transcription at a time, process-wide (`_slot`). Not per user — per
    process. Two concurrent runs on one core makes both slow AND starves the
    meters.
  * `cpu_threads=1`. Left to itself CTranslate2 grabs every core it can see,
    which on a single-core box means it grabs the only one.
  * The model is loaded LAZILY and only once. Loading costs seconds and a few
    hundred MB of a 2 GB box; a user who never asks for captions should never
    pay for it.
  * A hard timeout, so a pathological file cannot pin the core indefinitely.

The Whisper call itself is isolated in `_run_whisper` so everything around it
— queueing, storage, the API, the concurrency limit — is testable without the
model. The model could not be downloaded in the dev container (the egress
proxy blocks the weights host), so **that one function is unverified here and
must be confirmed on prod**; `python -m src.captions.transcribe --selftest`
does exactly that.
"""

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

# Process-wide: only one clip is ever transcribed at a time. See module docs.
_slot = asyncio.Semaphore(1)
_model = None
_model_lock = asyncio.Lock()


@dataclass
class Segment:
    start: float
    end: float
    text: str


def captions_path(video_path: Path) -> Path:
    """Captions live beside the video they describe, so deleting the upload's
    directory takes them with it and nothing is orphaned."""
    return video_path.with_suffix(video_path.suffix + ".captions.json")


def extract_audio(video: Path, out_wav: Path, timeout: float = 60.0) -> None:
    """Video -> 16 kHz mono WAV, which is exactly what Whisper wants.

    Downmixing here rather than letting Whisper do it keeps the expensive
    process fed with the smallest possible input.
    """
    cmd = [settings.ffmpeg_path, "-nostdin", "-y", "-i", str(video),
           "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out_wav)]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size == 0:
        raise RuntimeError(
            "Could not read audio from that clip"
            + (f" ({proc.stderr.decode(errors='replace')[-160:]})" if proc.stderr else ""))


def _load_model():
    """Import and construct the model. Kept separate so tests can replace it
    without faster-whisper being installed at all."""
    from faster_whisper import WhisperModel
    return WhisperModel(
        settings.captions_model,
        device="cpu",
        compute_type="int8",        # 4x smaller and faster than float32 on CPU
        cpu_threads=1,              # never take the only core
        download_root=str(Path(settings.local_storage_path) / "models"),
    )


def _run_whisper(wav: Path) -> tuple[list[Segment], str]:
    """The only part that touches Whisper. Synchronous and CPU-bound — callers
    run it in an executor.

    UNVERIFIED IN DEV: the weights host is unreachable from the dev container,
    so this path has never actually executed there. Confirm on prod with
    `python -m src.captions.transcribe --selftest` before trusting it.
    """
    global _model
    if _model is None:
        _model = _load_model()
    segs, info = _model.transcribe(
        str(wav),
        beam_size=1,                 # greedy: markedly cheaper, fine for captions
        vad_filter=True,             # skip silence instead of hallucinating over it
        condition_on_previous_text=False,   # stops one bad guess cascading
    )
    out = [Segment(round(s.start, 2), round(s.end, 2), (s.text or "").strip())
           for s in segs if (s.text or "").strip()]
    return out, getattr(info, "language", "") or ""


async def transcribe(video: Path, on_progress=None) -> dict:
    """Caption one clip. Returns the payload that gets stored and served.

    Serialised process-wide: a second caller waits rather than competing for
    the core with the first one AND with live clip detection.
    """
    if not video.exists():
        raise RuntimeError("That clip is no longer on disk.")

    async with _slot:
        started = time.time()
        wav = video.with_suffix(".caption.wav")
        try:
            if on_progress:
                await on_progress(10, "extracting audio")
            await asyncio.get_running_loop().run_in_executor(
                None, extract_audio, video, wav)

            if on_progress:
                await on_progress(30, "transcribing")
            segs, lang = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, _run_whisper, wav),
                timeout=settings.captions_timeout_s,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Captioning took longer than {settings.captions_timeout_s}s and was "
                "stopped so it couldn't slow down clip detection.")
        finally:
            wav.unlink(missing_ok=True)

        payload = {
            "segments": [asdict(s) for s in segs],
            "language": lang,
            "model": settings.captions_model,
            "created_at": time.time(),
            "took_s": round(time.time() - started, 1),
        }
        log.info("captions_done", video=video.name, segments=len(segs),
                 took_s=payload["took_s"], language=lang)
        return payload


def save(video: Path, payload: dict) -> None:
    p = captions_path(video)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(p)          # atomic: never serve a half-written caption file


def load(video: Path) -> dict | None:
    p = captions_path(video)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _selftest() -> int:
    """Prove on PROD that the model loads and transcribes — the one thing the
    dev container cannot check."""
    import wave, math, struct, tempfile
    print(f"model={settings.captions_model} timeout={settings.captions_timeout_s}s")
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "tone.wav"
        with wave.open(str(wav), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 12.0)))
                                   for i in range(16000 * 2)))
        t0 = time.time()
        try:
            segs, lang = _run_whisper(wav)
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            print("If this is a download error, the droplet cannot reach the "
                  "weights host; pre-fetch the model or check egress.")
            return 1
    print(f"OK — model loaded and ran in {time.time() - t0:.1f}s "
          f"({len(segs)} segments from a 2s test tone, lang={lang!r})")
    print("A tone has no speech, so 0 segments here is the correct result.")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_selftest() if "--selftest" in sys.argv else
                     print("usage: python -m src.captions.transcribe --selftest") or 2)
