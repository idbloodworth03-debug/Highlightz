"""A/B Whisper's voice-activity filter on a REAL clip.

The VAD filter discards audio it judges non-speech before transcription. When
it is right it saves CPU; when it is wrong the speech is gone and no later
stage can get it back. A 30s clip on prod came back captioned only to 8.07s,
cut mid-sentence — which points at VAD, but "points at" is not proof, and the
dev container cannot run Whisper at all.

So this runs the same clip twice, once each way, and prints what each produced.
The numbers decide, not the argument.

    python -m src.maintenance.caption_vad_test <upload_id_or_filename_fragment>

Costs two transcriptions. It takes the same single slot the API uses, so it
cannot run alongside a user's caption job or steal a core from clip detection.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

from config.settings import settings
from src.captions import transcribe as cap


def _find(fragment: str) -> Path | None:
    root = Path(settings.local_storage_path) / "uploads"
    for p in sorted(root.glob("*/*")):
        if p.suffix == ".json" or p.name.endswith(".wav"):
            continue
        if fragment in p.name or fragment in p.parent.name:
            return p
    return None


def _duration(path: Path) -> float | None:
    ff = settings.ffmpeg_path
    probe = ff[:-6] + "ffprobe" if ff.endswith("ffmpeg") else "ffprobe"
    try:
        p = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, timeout=30)
        return float(p.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


async def _run(video: Path) -> int:
    dur = _duration(video)
    print(f"clip: {video.name}")
    print(f"duration: {dur:.2f}s" if dur else "duration: unknown")

    wav = video.with_suffix(".vadtest.wav")
    try:
        cap.extract_audio(video, wav)
        audio_s = _duration(wav)
        print(f"extracted audio: {audio_s:.2f}s" if audio_s else "extracted audio: unknown")
        if dur and audio_s and audio_s < dur * 0.9:
            print("  *** The AUDIO TRACK itself is short — VAD is not the problem, "
                  "the clip's audio is. Stop here.")

        for vad in (True, False):
            # Same slot the API uses, so this cannot double up with a real job.
            async with cap._slot:
                t0 = time.time()
                segs, lang = await asyncio.get_running_loop().run_in_executor(
                    None, cap._run_whisper, wav, vad)
            took = time.time() - t0
            end = segs[-1].end if segs else 0.0
            cov = (end / dur * 100) if dur else 0.0
            words = sum(len(s.text.split()) for s in segs)
            print(f"\n--- vad_filter={vad}")
            print(f"    cues={len(segs)} words={words} lang={lang!r} took={took:.1f}s")
            print(f"    covers 0.00s -> {end:.2f}s  ({cov:.0f}% of the clip)")
            for s in segs[:3]:
                print(f"      {s.start:7.2f} - {s.end:7.2f}  {s.text!r}")
            if len(segs) > 3:
                print(f"      ... {len(segs) - 3} more, last ends {end:.2f}s")
            if len(segs) == 1 and words > 6:
                print("      *** one cue for many words — word timings did not "
                      "come back; word_timestamps is not taking effect.")
    finally:
        wav.unlink(missing_ok=True)

    print("\nRead it like this: if vad_filter=False covers much more of the clip, "
          "VAD was eating speech and CAPTIONS_VAD should stay false (the default). "
          "If both cover the same, VAD is innocent and the truncation is elsewhere.")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    video = _find(argv[0])
    if not video:
        print(f"no upload matching {argv[0]!r} under "
              f"{Path(settings.local_storage_path) / 'uploads'}")
        return 1
    return asyncio.run(_run(video))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
