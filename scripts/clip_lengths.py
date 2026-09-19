"""How long the clips actually are, per platform. Read-only, run on PROD.

    /opt/highlightz/venv/bin/python scripts/clip_lengths.py

WHY THIS EXISTS. Owner, 2026-09-19: "why is twitch still being clipped for
only 30 seconds and why is kick only 20-30 seconds". Two different mechanisms
are in play and only one of them is ours:

  TWITCH LINK   Create Clip (POST /helix/clips). TWITCH decides the length and
                hands back `duration`; there is no length parameter to send.
                That number is what clips.twitch.tv plays.
  THE FILE      Our own cut from the live capture buffer, pre_roll + post_roll
                wide (35-66s depending on preset). Kick has ONLY this, because
                Kick has no clip API.

So a Twitch clip has two lengths at once and a Kick clip has one. Reading the
file off disk is the only way to know which number the owner is looking at,
and whether the cut is coming up short of what the preset asked for.

Prints no channel or account — durations and counts only.
"""
import collections
import json
import pathlib
import statistics
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                      # noqa: E402
from src.clips import files as clip_files                 # noqa: E402
from src.trigger.rules import PRESETS                     # noqa: E402

ROOT = pathlib.Path(settings.local_storage_path)


def probe(path) -> float:
    """Real duration of the container, from ffprobe. 0.0 if it cannot say."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def spread(name, vals):
    if not vals:
        print(f"  {name:<22} none")
        return
    vals = sorted(vals)
    print(f"  {name:<22} n={len(vals):<4} min {vals[0]:>5.1f}s   "
          f"median {statistics.median(vals):>5.1f}s   max {vals[-1]:>5.1f}s")


print("=" * 66)
print("CLIP LENGTHS")
print("=" * 66)

print("\nWHAT THE PRESETS ASK THE CUT FOR")
for k, v in PRESETS.items():
    print(f"  {k:<10} pre {v.pre_roll:>3}s + post {v.post_roll:>3}s = {v.pre_roll + v.post_roll:>3}s")

print("\nCAPTURE SETTINGS ON THIS BOX")
print(f"  clip_capture_enabled   : {settings.clip_capture_enabled}"
      f"{'   <- Kick cannot clip at all without this' if not settings.clip_capture_enabled else ''}")
print(f"  clip_capture_buffer_s  : {settings.clip_capture_buffer_s}"
      f"   (a cut cannot reach further back than this)")
print(f"  clip_capture_segment_s : {settings.clip_capture_segment_s}")
print(f"  clip_capture_quality   : {settings.clip_capture_quality}")

try:
    clips = json.loads((ROOT / "clips.json").read_text())
except Exception as exc:
    print(f"\ncould not read clips.json: {exc}")
    raise SystemExit(1)

# Twitch's own number, straight off the record. This is the clips.twitch.tv
# length and nothing in this repo chooses it.
said = collections.defaultdict(list)
for c in clips:
    d = float(c.get("duration_seconds") or 0)
    if d:
        said[c.get("platform") or "twitch"].append(d)

print("\nWHAT THE RECORD SAYS (Twitch: Twitch's own duration; Kick: pre+post)")
for plat, vals in sorted(said.items()):
    spread(plat, vals)

# The files we cut ourselves.
print("\nWHAT THE FILE ON DISK ACTUALLY IS (ffprobe)")
have = clip_files.existing_ids()
real = collections.defaultdict(list)
short = collections.Counter()
checked = 0
for c in clips:
    if c.get("id") not in have:
        continue
    p = clip_files.path_for(c["id"])
    if p is None or not p.exists():
        continue
    d = probe(p)
    checked += 1
    if not d:
        continue
    plat = c.get("platform") or "twitch"
    real[plat].append(d)
    # Did the cut come up short of what the preset asked for? The buffer only
    # holds so much, and a moment early in a stream cannot reach back before
    # the recorder started.
    want = float(c.get("duration_seconds") or 0)
    if want and d < want - 3:
        short[plat] += 1

for plat, vals in sorted(real.items()):
    spread(plat, vals)
print(f"  ({checked} files probed)")

if short:
    print("\n  CUTS THAT CAME UP SHORT (file more than 3s under what was asked)")
    for plat, n in short.most_common():
        print(f"    {plat:<10} {n} of {len(real.get(plat, []))}")
    print("    Usually the buffer: a moment near the start of a stream cannot")
    print("    reach back before the recorder started, and the cut keeps only")
    print("    what the buffer really holds.")

print("\n" + "=" * 66)
print("Paste this back. No channel or account name appears above.")
print("=" * 66)
