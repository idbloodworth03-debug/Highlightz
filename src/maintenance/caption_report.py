"""What do the captions on disk actually cover?

"Captions stop after the first few words" has two completely different causes
and they need opposite fixes:

  * the TRANSCRIPT stops early — Whisper only produced text for the start of
    the clip, so there is nothing to show later. A frontend change cannot fix
    this.
  * the transcript covers the clip but the CUES are wrong — bad timings, or
    everything collapsed into one entry.

Guessing between them costs a deploy per guess. This prints the answer:
cue count, the span the cues cover, the clip's real duration, and coverage.

    python -m src.maintenance.caption_report            # every captioned upload
    python -m src.maintenance.caption_report <upload_id>
"""

import json
import subprocess
import sys
from pathlib import Path

from config.settings import settings


def _ffprobe_path() -> str:
    ff = settings.ffmpeg_path
    return ff[:-6] + "ffprobe" if ff.endswith("ffmpeg") else "ffprobe"


def media_duration(path: Path) -> float | None:
    try:
        p = subprocess.run(
            [_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(p.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def report(cap_file: Path) -> None:
    video = Path(str(cap_file)[: -len(".captions.json")])
    try:
        data = json.loads(cap_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{cap_file.name}: unreadable ({exc})")
        return

    segs = data.get("segments") or []
    dur = media_duration(video) if video.exists() else None

    print(f"\n=== {video.parent.name}/{video.name}")
    print(f"    model={data.get('model')} lang={data.get('language')!r} "
          f"took={data.get('took_s')}s")
    if not segs:
        print("    NO CUES — transcription produced nothing.")
        return

    first, last = segs[0], segs[-1]
    words = sum(len(str(s.get('text', '')).split()) for s in segs)
    print(f"    cues={len(segs)}  words={words}")
    print(f"    cues span {first['start']:.2f}s -> {last['end']:.2f}s")
    if dur:
        cov = last["end"] / dur * 100
        print(f"    clip duration {dur:.2f}s  ->  COVERAGE {cov:.0f}%")
        if cov < 80:
            print("    *** TRANSCRIPT STOPS EARLY — this is the bug. Whisper "
                  "produced no text past the point above.")
    else:
        print("    clip duration unknown (ffprobe failed or file gone)")

    longest = max(segs, key=lambda s: s["end"] - s["start"])
    print(f"    longest cue {longest['end'] - longest['start']:.2f}s: "
          f"{str(longest.get('text',''))[:70]!r}")
    if len(segs) == 1:
        print("    *** ONE CUE for the whole clip — word timings are missing, "
              "so cue shaping fell back to keeping the segment whole.")

    print("    first 5:")
    for s in segs[:5]:
        print(f"      {s['start']:7.2f} - {s['end']:7.2f}  {s.get('text','')!r}")
    if len(segs) > 5:
        print("    last 3:")
        for s in segs[-3:]:
            print(f"      {s['start']:7.2f} - {s['end']:7.2f}  {s.get('text','')!r}")


def main(argv: list[str]) -> int:
    root = Path(settings.local_storage_path) / "uploads"
    if not root.exists():
        print(f"no uploads directory at {root}")
        return 1

    files = sorted(root.glob("*/*.captions.json"))
    if argv:
        want = argv[0]
        files = [f for f in files if want in f.name]
        if not files:
            print(f"no caption file matching {want!r}")
            return 1
    if not files:
        print(f"no caption files under {root} — nothing has been captioned yet")
        return 1

    print(f"{len(files)} caption file(s) under {root}")
    for f in files:
        report(f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
