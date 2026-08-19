"""What one monitored stream actually costs this box, measured not guessed.

MAX_CONCURRENT_STREAMS is the one number CLAUDE.md says not to raise casually,
because every worker holds a chat socket, an evaluation loop and — with audio
detection on — a streamlink and an ffmpeg, all on one vCPU. "Raise it to 30"
is a guess until somebody knows what 20 already costs. This measures the box
as it is running right now and says what there is room for.

    /opt/highlightz/venv/bin/python scripts/stream_cost.py
    /opt/highlightz/venv/bin/python scripts/stream_cost.py --sample=20

Stdlib and /proc only: no psutil, because a deploy never runs pip install and a
new runtime dependency crash-loops the service. Read-only.
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                      # noqa: E402
from src.dashboard import api                             # noqa: E402

SAMPLE = 10.0
for a in sys.argv[1:]:
    if a.startswith("--sample="):
        SAMPLE = float(a.split("=", 1)[1])

PAGE = os.sysconf("SC_PAGE_SIZE")
TICKS = os.sysconf("SC_CLK_TCK")


def meminfo() -> dict:
    out = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        out[k] = int(v.split()[0]) * 1024        # kB -> bytes
    return out


def procs() -> dict:
    """{pid: (name, rss_bytes, cpu_ticks)} for everything we can read."""
    out = {}
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text()
            # comm is parenthesised and may contain spaces, so split on the LAST
            # ')' rather than on whitespace — "(ffmpeg -i x)" would otherwise
            # shift every field after it and silently mis-read cpu and rss.
            close = stat.rindex(")")
            name = stat[stat.index("(") + 1:close]
            fields = stat[close + 2:].split()
            utime, stime = int(fields[11]), int(fields[12])
            rss = int(fields[21]) * PAGE
            out[int(p.name)] = (name, rss, utime + stime)
        except (OSError, ValueError, IndexError):
            continue      # process exited mid-read, or not ours to see
    return out


def group(name: str) -> str:
    n = name.lower()
    if "ffmpeg" in n:
        return "ffmpeg"
    if "streamlink" in n:
        return "streamlink"
    if "python" in n:
        return "python"
    return "other"


def fmt(b: float) -> str:
    return f"{b / 1048576:.0f}MB" if b < 1073741824 else f"{b / 1073741824:.2f}GB"


cores = os.cpu_count() or 1
mem = meminfo()
cap = max(1, settings.max_concurrent_streams)
streams = len(api._streams)

print("=" * 70)
print("THE BOX")
print("=" * 70)
print(f"  cores                  : {cores}")
print(f"  memory total           : {fmt(mem['MemTotal'])}")
print(f"  memory available       : {fmt(mem['MemAvailable'])}"
      f"   ({mem['MemAvailable'] / mem['MemTotal'] * 100:.0f}% free)")
if "SwapTotal" in mem and mem["SwapTotal"]:
    used = mem["SwapTotal"] - mem.get("SwapFree", 0)
    print(f"  swap                   : {fmt(used)} of {fmt(mem['SwapTotal'])} used"
          + ("   <- swapping means the box is already over its memory" if used > 50 * 1048576 else ""))
else:
    print("  swap                   : none configured"
          "   <- an OOM kills the service outright rather than slowing it")
load = pathlib.Path("/proc/loadavg").read_text().split()[:3]
print(f"  load 1/5/15min         : {' / '.join(load)}"
      f"   ({float(load[1]) / cores:.2f} per core over 5min)")
print(f"  VOD_AUDIO_ENABLED      : {settings.vod_audio_enabled}"
      + ("   <- each scan adds a streamlink + ffmpeg" if settings.vod_audio_enabled else ""))

print()
print("=" * 70)
print(f"WHAT IS RUNNING  (sampling CPU for {SAMPLE:.0f}s)")
print("=" * 70)

a = procs()
time.sleep(SAMPLE)
b = procs()

tot = {}
for pid, (name, rss, ticks) in b.items():
    if pid not in a:
        continue
    g = group(name)
    cpu = (ticks - a[pid][2]) / TICKS / SAMPLE * 100
    n, r, c = tot.get(g, (0, 0.0, 0.0))
    tot[g] = (n + 1, r + rss, c + cpu)

print(f"  {'group':<14}{'procs':>7}{'RSS':>12}{'CPU%':>10}")
for g in ("python", "streamlink", "ffmpeg", "other"):
    if g not in tot:
        continue
    n, r, c = tot[g]
    print(f"  {g:<14}{n:>7}{fmt(r):>12}{c:>9.1f}%")
hz_rss = sum(r for g, (n, r, c) in tot.items() if g != "other")
hz_cpu = sum(c for g, (n, r, c) in tot.items() if g != "other")
print(f"  {'-> highlightz':<14}{'':>7}{fmt(hz_rss):>12}{hz_cpu:>9.1f}%")

print()
print("=" * 70)
print("PER STREAM")
print("=" * 70)
print(f"  MAX_CONCURRENT_STREAMS : {cap}")
print(f"  streams running now    : {streams}")

if streams <= 0:
    print()
    print("  NOTHING IS RUNNING, so there is no per-stream cost to measure and")
    print("  no basis for a new ceiling. Run this while your usual number of")
    print("  channels are live — the whole point is to measure the real load.")
    sys.exit(0)

# SEPARATE THE FIXED COST FROM THE MARGINAL ONE, because dividing everything
# by the stream count charges the whole baseline — the dashboard, the app, the
# interpreter — to the streams, and with a handful running that inflates the
# per-stream figure enough to make the ceiling meaningless.
#
# streamlink and ffmpeg are unambiguously per-stream: one pair per audio-enabled
# worker, nothing else starts them. The python process is both at once — it
# holds the app AND every worker's evaluation loop as asyncio tasks — and one
# sample cannot split it. So it is reported as what it is rather than divided.
sub_rss = sum(r for g, (n, r, c) in tot.items() if g in ("streamlink", "ffmpeg"))
sub_cpu = sum(c for g, (n, r, c) in tot.items() if g in ("streamlink", "ffmpeg"))
py_rss  = tot.get("python", (0, 0.0, 0.0))[1]
py_cpu  = tot.get("python", (0, 0.0, 0.0))[2]

print(f"  python (app + workers) : {fmt(py_rss)} RSS, {py_cpu:.1f}% CPU"
      "   <- fixed baseline AND per-stream, not separable from one sample")
print(f"  streamlink + ffmpeg    : {fmt(sub_rss)} RSS, {sub_cpu:.1f}% CPU"
      "   <- purely per-stream")
mem_each = sub_rss / streams
cpu_each = sub_cpu / streams
print(f"  marginal per stream    : ~{fmt(mem_each)}, ~{cpu_each:.1f}% of one core")
if sub_rss == 0:
    print("      (no streamlink/ffmpeg running — either audio detection is off")
    print("       or no worker has started one yet, so the marginal cost here is")
    print("       the cheap case, not the expensive one)")

print()
print("=" * 70)
print("HOW HIGH CAN THE CEILING GO")
print("=" * 70)
# Keep a real reserve: the OS, the dashboard itself and a VOD scan all need
# room, and on a swapless box running out of memory is a kill, not a slowdown.
RESERVE = 400 * 1048576
room = max(0, mem["MemAvailable"] - RESERVE)
by_mem = int(streams + (room / mem_each)) if mem_each > 0 else None
by_cpu = int((cores * 100 * 0.75 - py_cpu) / cpu_each) if cpu_each > 0 else None
if by_mem is None or by_cpu is None:
    print("  CANNOT SAY. No per-stream subprocess cost was measurable, so there is")
    print("  nothing to extrapolate from. Run this while streams are live on a box")
    print("  with audio detection on.")
    safe = None
else:
    print(f"  by memory  : ~{by_mem} streams"
          f"   (keeping {fmt(RESERVE)} back for the OS, dashboard and VOD scans)")
    print(f"  by CPU     : ~{by_cpu} streams"
          f"   (total CPU under 75% of {cores} core(s), python's share subtracted)")
    safe = max(1, min(by_mem, by_cpu))
    print(f"  -> the binding constraint is "
          f"{'memory' if by_mem < by_cpu else 'CPU'}, so about {safe} streams")

# THE HONESTY GUARD. Every number above is one sample extrapolated linearly,
# and the two things that break that assumption are both common: too few
# streams to measure from, and the python process growing per-worker in a way
# this cannot separate. A confident ceiling from three streams on a quiet
# afternoon is exactly the guess CLAUDE.md says not to make, so it is refused
# rather than dressed up with a caveat nobody reads.
THIN = 5
print()
if not str(pathlib.Path(__file__).resolve()).startswith("/opt/highlightz"):
    print("  NOT PRODUCTION. This is not /opt/highlightz, so these numbers describe")
    print("  a different machine with different cores and memory. They say nothing")
    print("  about what the droplet can take. Re-run there.")
elif streams < THIN:
    print(f"  TOO THIN TO DECIDE. Only {streams} stream(s) were running, and the")
    print("  estimate scales one sample linearly. Worse, python's per-stream")
    print("  growth is inside the baseline here, so the real marginal cost is")
    print("  HIGHER than the figure above. Re-run at your busiest before moving")
    print("  the ceiling.")
elif safe is not None and safe > cap:
    print(f"  RAISING IT LOOKS SUPPORTED. {cap} -> {safe} fits the measured cost.")
    print("  Move it in ONE step, not to the computed maximum, and re-run this")
    print("  with the new streams actually live before going further — thin chat")
    print("  is far cheaper than a 40k-viewer channel, and the ceiling has to")
    print("  hold on the worst night, not the night you measured.")
else:
    print(f"  DO NOT RAISE IT. The measured cost supports about {safe} streams and")
    print(f"  the ceiling is already {cap}. Raising it would let the box accept work")
    print("  it cannot run; with no swap that ends as an OOM kill of the whole")
    print("  service, not as slowness. Resize the droplet first.")
