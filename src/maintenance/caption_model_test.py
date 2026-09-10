"""Compare Whisper models and decoding settings on a REAL clip.

WHY THIS EXISTS RATHER THAN A PATCH. "Captions keep picking up incorrect
words" has one dominant cause and it is not a bug: `tiny.en` is the smallest
Whisper model there is, and Twitch clips are its worst case — game audio and
music under shouted, slangy speech. It was chosen deliberately, because this is
a 1 vCPU box that is also running a streamlink+ffmpeg audio meter per monitored
channel, and clip detection has to win the core.

So the fix is a TRADE, not a correction: accuracy costs CPU that live detection
may want. Nobody can settle that from a dev container — the weights host is
unreachable there and the CPU is not the production CPU. This runs the
candidates back to back on a clip you actually have and prints what each one
produced and what each one cost.

    python -m src.maintenance.caption_model_test <upload_id_or_filename_fragment>
    python -m src.maintenance.caption_model_test <fragment> --models=tiny.en,base.en
    python -m src.maintenance.caption_model_test <fragment> --beams=1,5
    python -m src.maintenance.caption_model_test <fragment> --prompt="Live gameplay commentary from a Twitch stream."

**RUN IT WHEN THE BOX IS QUIET**, and for the same reason caption_vad_test
says so: this is a SEPARATE PROCESS, so the semaphore that serialises
captioning inside the service is a different object here. Nothing coordinates
the two. Started during a stream you care about, this competes with the audio
meters clip detection depends on. Downloading a model it has not seen before
also costs bandwidth and disk under `<storage>/models`.

READ THE OUTPUT FOR WORDS, NOT FOR THE SCORE. There is no reference transcript
to measure against, so nothing here can tell you which run is "right". It puts
them side by side so a human who knows what was said can see it. The timings
are the half that IS objective.
"""

import subprocess
import sys
import time
from pathlib import Path

from config.settings import settings
from src.captions import transcribe as cap

# Sizes worth considering on a box like this one. Anything above small.en is
# not a realistic candidate for a single core and is left out rather than
# offered and then regretted.
DEFAULT_MODELS = ("tiny.en", "base.en")


def _find(fragment: str) -> Path | None:
    """Locate an upload by id or filename fragment — same lookup the VAD tool
    uses, so both take the same argument."""
    root = Path(settings.local_storage_path) / "uploads"
    for p in sorted(root.glob("*/*")):
        if p.suffix == ".json" or p.name.endswith(".wav"):
            continue
        if fragment in p.name or fragment in p.parent.name:
            return p
    # Captured clips live in their own store and are just as good a test case.
    for p in sorted((Path(settings.local_storage_path) / "clipfiles").glob("*.mp4")):
        if fragment in p.name:
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


def _transcript(segs) -> str:
    return " ".join(s.text.strip() for s in segs).strip()


def _run(video: Path, models, beams, prompt: str | None) -> int:
    dur = _duration(video)
    print(f"\nclip: {video.name}"
          + (f"  ({dur:.1f}s)" if dur else "")
          + f"\nvad: {settings.captions_vad}   (unchanged by this tool)")
    print(f"prompt: {prompt!r}" if prompt else "prompt: none")

    wav = video.with_suffix(".modeltest.wav")
    try:
        t0 = time.time()
        cap.extract_audio(video, wav)
        print(f"audio extracted in {time.time() - t0:.1f}s\n")

        results = []
        for name in models:
            try:
                from faster_whisper import WhisperModel
                t0 = time.time()
                model = WhisperModel(
                    name, device="cpu", compute_type="int8", cpu_threads=1,
                    download_root=str(Path(settings.local_storage_path) / "models"),
                )
                load_s = time.time() - t0
            except Exception as exc:
                print(f"=== {name}: COULD NOT LOAD — {type(exc).__name__}: {exc}")
                print("    If this is a download error, the box cannot reach the "
                      "weights host.\n")
                continue

            for beam in beams:
                t0 = time.time()
                try:
                    segs, lang = cap._run_whisper(wav, model=model, beam=beam,
                                                  prompt=prompt)
                except Exception as exc:
                    print(f"=== {name} beam={beam}: FAILED — "
                          f"{type(exc).__name__}: {exc}\n")
                    continue
                took = time.time() - t0
                text = _transcript(segs)
                results.append((name, beam, took, len(segs), text))
                rt = f"{took / dur:.2f}x realtime" if dur else ""
                print(f"=== {name}  beam={beam}   {took:.1f}s  {rt}"
                      f"   (load {load_s:.1f}s, {len(segs)} cues)")
                print(f"    {text or '(nothing transcribed)'}\n")
            del model
    finally:
        wav.unlink(missing_ok=True)

    if len(results) > 1:
        print("─" * 72)
        print("WHAT TO DO WITH THIS. Read the transcripts against what was "
              "actually said —\nnothing here knows that, so nothing here can "
              "score them. Then weigh the time:")
        base = results[0]
        for name, beam, took, _n, _t in results[1:]:
            mult = took / base[2] if base[2] else 0
            print(f"  {name} beam={beam} costs {mult:.1f}x "
                  f"{base[0]} beam={base[1]}  ({took:.1f}s vs {base[2]:.1f}s)")
        print("\nThat multiple is CPU taken from live clip detection while a "
              "caption runs.\nCaptioning is serialised to one clip at a time, so "
              "it is one core's worth,\nnot per user — but it is the same core "
              "the audio meters need.")
        print("\nTo adopt one, set in .env and restart:")
        print("  CAPTIONS_MODEL=base.en")
        print("  CAPTIONS_BEAM_SIZE=5")
        if prompt:
            print(f'  CAPTIONS_INITIAL_PROMPT="{prompt}"')
    return 0


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    models = DEFAULT_MODELS
    beams = (1,)
    prompt = None
    for a in argv:
        if a.startswith("--models="):
            models = tuple(x.strip() for x in a.split("=", 1)[1].split(",") if x.strip())
        elif a.startswith("--beams="):
            beams = tuple(int(x) for x in a.split("=", 1)[1].split(",") if x.strip())
        elif a.startswith("--prompt="):
            prompt = a.split("=", 1)[1]

    video = _find(args[0])
    if not video:
        print(f"No clip matching {args[0]!r} under "
              f"{Path(settings.local_storage_path) / 'uploads'} or clipfiles/.")
        return 1
    return _run(video, models, beams, prompt)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
