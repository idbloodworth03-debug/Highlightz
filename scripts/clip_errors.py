"""The actual reason clipping is failing, with the traceback.

run_clip_processor logs every failure as clip_processor_error WITH a full
traceback, and the three failures that stop a stream outright each log their
own event first. Those are the only things that say what is really wrong; the
toast the user sees is deliberately vague and identical for most causes.

    /opt/highlightz/venv/bin/python scripts/clip_errors.py
    /opt/highlightz/venv/bin/python scripts/clip_errors.py --hours=6

Read-only.
"""
import collections
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

HOURS = 3.0
for a in sys.argv[1:]:
    if a.startswith("--hours="):
        HOURS = float(a.split("=", 1)[1])

# Ordered worst-first: the top three each STOP the stream, so if one of them
# fired that is the whole story and the generic errors below are noise.
TERMINAL = ["clip_token_expired", "clip_channel_not_clippable"]
OTHER = ["clip_processor_error", "clip_skipped_queue_full", "clip_job_stale_dropped",
         "twitch_clip_ready", "processing_clip_job", "trigger_fired",
         "clip_dropped_queue_full", "stream_worker_error", "worker_spawned",
         # The Helix layer logs WHY a create failed, with the status and body.
         # Without these the generic "Twitch clip creation failed for 'x'" is
         # all you see, which names the channel and not the cause.
         "twitch_clip_create_failed", "twitch_clip_rate_limited",
         "twitch_clip_ccl_retry", "twitch_clip_not_authorized",
         # And the refresh path, which used to fail silently.
         "twitch_refresh_rejected", "twitch_refresh_failed_transient",
         "twitch_refresh_missing", "twitch_refresh_empty"]

try:
    out = subprocess.run(
        ["journalctl", "-u", "highlightz", "--since", f"{HOURS:.0f} hours ago",
         "--no-pager", "-o", "cat"],
        capture_output=True, timeout=300).stdout.decode(errors="replace")
except Exception as exc:
    sys.exit(f"could not read the journal: {exc}")

if not out.strip():
    sys.exit(f"The journal returned nothing for the last {HOURS:.0f}h. "
             f"Try a longer window: --hours=24")

lines = out.splitlines()
counts = collections.Counter()
for ev in TERMINAL + OTHER:
    counts[ev] = out.count(ev)

print("=" * 72)
print(f"CLIP EVENTS  (last {HOURS:.0f}h)")
print("=" * 72)
for ev in TERMINAL:
    if counts[ev]:
        print(f"  {ev:<32}{counts[ev]:>6}   <- THIS STOPS THE STREAM")
    else:
        print(f"  {ev:<32}{counts[ev]:>6}")
for ev in OTHER:
    print(f"  {ev:<32}{counts[ev]:>6}")

# ── the terminal causes, in full ─────────────────────────────────────────────
for ev in TERMINAL:
    hits = [l for l in lines if ev in l]
    if not hits:
        continue
    print()
    print("=" * 72)
    print(f"{ev}  ({len(hits)})")
    print("=" * 72)
    for l in hits[-8:]:
        print("  " + l.strip()[:200])
    if ev == "clip_token_expired":
        print()
        print("  MEANING: the user's Twitch OAuth token no longer works, so no clip")
        print("  can be created for them. Monitoring was stopped on purpose — it")
        print("  cannot succeed until they sign out and back in to reconnect.")
    else:
        print()
        print("  MEANING: that broadcaster has clipping restricted on Twitch.")
        print("  It can never succeed and the stream was stopped deliberately.")

# ── tracebacks ───────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("TRACEBACKS")
print("=" * 72)
idx = [i for i, l in enumerate(lines) if "clip_processor_error" in l]
if not idx:
    print("  None. No unexpected exception was raised in the clip processor,")
    print("  so the failure is one of the named causes above rather than a crash.")
else:
    print(f"  {len(idx)} clip_processor_error entries. Last {min(3, len(idx))}:")
    for i in idx[-3:]:
        print()
        print("  " + "-" * 68)
        for l in lines[i:i + 30]:
            print("  " + l.rstrip()[:200])

# ── what the errors say, grouped ─────────────────────────────────────────────
errs = collections.Counter()
for l in lines:
    if "clip_processor_error" in l:
        m = re.search(r"error=([^\s]+(?:\s+[^\s=]+)*)", l)
        if m:
            errs[m.group(1)[:90]] += 1
if errs:
    print()
    print("=" * 72)
    print("DISTINCT ERRORS")
    print("=" * 72)
    for msg, n in errs.most_common(10):
        print(f"  {n:>4}x  {msg}")

# ── why Helix refused, which is the cause behind the generic message ─────────
fails = [l for l in lines if "twitch_clip_create_failed" in l]
if fails:
    print()
    print("=" * 72)
    print(f"WHY THE CLIP CALL FAILED  ({len(fails)})")
    print("=" * 72)
    by_status = collections.Counter()
    bodies = collections.Counter()
    for l in fails:
        m = re.search(r'"status":\s*(\d+)', l)
        by_status[m.group(1) if m else "?"] += 1
        b = re.search(r'"body":\s*"(.{0,120})', l)
        if b:
            bodies[b.group(1)[:110]] += 1
    print("  by HTTP status:")
    for st, n in by_status.most_common():
        note = {"401": "  <- the USER TOKEN is rejected; this is an auth problem",
                "403": "  <- forbidden; channel restricts clipping",
                "404": "  <- broadcaster or stream not found (went offline?)",
                "429": "  <- rate limited (the Helix clip limit is global)",
                "500": "  <- Twitch server error, transient",
                "503": "  <- Twitch unavailable, transient"}.get(st, "")
        print(f"      {st:<6}{n:>6}{note}")
    print("  distinct bodies:")
    for b, n in bodies.most_common(6):
        print(f"      {n:>4}x  {b}")

print()
print("=" * 72)
print("READ IT LIKE THIS")
print("=" * 72)
print("  clip_token_expired          -> the user must reconnect Twitch (sign out/in)")
print("  clip_channel_not_clippable  -> that channel blocks clips; nothing to fix")
print("  clip_skipped_queue_full     -> their review queue is full, not an error")
print("  clip_job_stale_dropped      -> the box was too slow to capture in time")
print("  clip_processor_error        -> a real exception; the traceback above is the cause")
print("  trigger_fired but no        -> scoring works, the clip call is what fails")
print("      twitch_clip_ready")
