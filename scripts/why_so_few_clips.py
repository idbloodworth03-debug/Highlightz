"""Is clipping broken, or just throttled? Answers it from real data.

"I only have 30 clips" has several very different causes and they are not
distinguishable by looking at the clip count:

  * the trigger never fires        -> thresholds too high, or chat too quiet
  * it fires and cooldown eats it  -> working exactly as designed, just capped
  * it fires and the clip fails    -> a real bug, or a channel that blocks clips
  * nothing was ever live          -> no streams monitored, nothing to clip

This reads the stats ledger, the stored clips and the journal, and says which.

    /opt/highlightz/venv/bin/python scripts/why_so_few_clips.py
    /opt/highlightz/venv/bin/python scripts/why_so_few_clips.py --days=30

Read-only.
"""
import collections
import json
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings          # noqa: E402

DAYS = 7.0
for a in sys.argv[1:]:
    if a.startswith("--days="):
        DAYS = float(a.split("=", 1)[1])
SINCE = time.time() - DAYS * 86400


def hr(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ── what actually got made ───────────────────────────────────────────────────
hr(f"CLIPS ON RECORD  (last {DAYS:.0f} days)")

clips_file = pathlib.Path(settings.local_storage_path) / "clips.json"
clips = []
try:
    raw = json.loads(clips_file.read_text())
    clips = list(raw.values()) if isinstance(raw, dict) else list(raw)
except Exception as exc:
    print(f"  could not read {clips_file}: {exc}")

recent = [c for c in clips if (c.get("created_at") or 0) >= SINCE]
print(f"  clips stored, all time : {len(clips)}")
print(f"  clips in window        : {len(recent)}")
if recent:
    per_day = collections.Counter(
        time.strftime("%Y-%m-%d", time.localtime(c["created_at"])) for c in recent)
    for day in sorted(per_day):
        print(f"      {day}  {per_day[day]:>4}")
    by_ch = collections.Counter(c.get("channel", "?") for c in recent)
    print("  by channel:")
    for ch, n in by_ch.most_common(10):
        print(f"      {ch:<24}{n:>4}")

# ── the journal: what the engine actually did ────────────────────────────────
hr(f"WHAT THE ENGINE DID  (journal, last {DAYS:.0f} days)")

EVENTS = ["trigger_fired", "trigger_suppressed_cooldown", "twitch_clip_ready",
          "clip_processor_error", "clip_job_stale_dropped",
          "clip_skipped_queue_full", "clip_channel_not_clippable",
          "clip_token_expired", "worker_spawned", "stream_offline",
          "job_enqueued"]
counts = {}
try:
    out = subprocess.run(
        ["journalctl", "-u", "highlightz", "--since", f"{DAYS:.0f} days ago",
         "--no-pager", "-o", "cat"],
        capture_output=True, timeout=180).stdout.decode(errors="replace")
    for ev in EVENTS:
        counts[ev] = out.count(f'"event": "{ev}"') or out.count(ev)
    lines = out.splitlines()
except Exception as exc:
    print(f"  could not read the journal: {exc}")
    lines, out = [], ""

if out:
    for ev in EVENTS:
        print(f"  {ev:<32}{counts.get(ev, 0):>7}")

# ── the verdict ──────────────────────────────────────────────────────────────
hr("WHAT THIS MEANS")

fired      = counts.get("trigger_fired", 0)
suppressed = counts.get("trigger_suppressed_cooldown", 0)
made       = counts.get("twitch_clip_ready", 0)
errors     = counts.get("clip_processor_error", 0)
stale      = counts.get("clip_job_stale_dropped", 0)
full       = counts.get("clip_skipped_queue_full", 0)
blocked    = counts.get("clip_channel_not_clippable", 0)
spawned    = counts.get("worker_spawned", 0)

if not out:
    print("  No journal read — rerun on the server, or with sudo.")
elif spawned == 0:
    print("  NOTHING WAS MONITORED. No worker started in this window, so there")
    print("  was never a live stream to clip from. Check that streams are added")
    print("  and that those channels actually went live.")
elif fired == 0 and suppressed == 0:
    print("  THE TRIGGER NEVER FIRED. Workers ran, but no moment ever crossed")
    print("  the threshold. That points at the score never getting high enough —")
    print("  quiet chat, a threshold set too high, or the audio meter not")
    print("  feeding in. Not a clipping bug; a scoring one.")
elif made == 0 and fired > 0:
    print(f"  THE TRIGGER FIRED {fired} TIMES AND NOTHING WAS MADE. That is a real")
    print("  failure in the clip path, not throttling. See the error counts above.")
elif suppressed > fired * 3:
    print(f"  IT IS THE COOLDOWN, NOT A BUG. The score crossed the threshold and")
    print(f"  was held back {suppressed:,} times against {fired:,} actual fires.")
    print("  The engine is finding plenty of moments and deliberately spacing")
    print("  them out. Cooldowns are 120s on most presets and 480s on irl, so")
    print("  one channel caps at roughly 30 clips per hour of LIVE time — fewer")
    print("  on a slow preset. If you want more clips, lower the cooldown for")
    print("  that preset rather than the threshold.")
else:
    print(f"  Fired {fired}, made {made}. Losses: {errors} errors, {stale} stale,")
    print(f"  {full} queue-full, {blocked} channel-blocks-clipping.")

if blocked:
    print()
    print(f"  NOTE: {blocked} attempt(s) hit a channel with clipping restricted.")
    print("  Those never succeed and the stream is stopped automatically.")
if stale:
    print()
    print(f"  NOTE: {stale} clip(s) were dropped for arriving too late to capture.")

# ── how much live time was there even ────────────────────────────────────────
hr("HOW MUCH LIVE TIME")
print("  Clips can only happen while a monitored channel is actually live.")
print(f"  workers started in window : {spawned}")
if lines:
    live = len([l for l in lines if "trigger_score" in l])
    print(f"  seconds of scoring seen   : ~{live:,}"
          f"   ({live / 3600:.1f} hours of live monitoring)")
    if live and made:
        print(f"  one clip per ~{live / max(made, 1) / 60:.1f} minutes of live stream")
