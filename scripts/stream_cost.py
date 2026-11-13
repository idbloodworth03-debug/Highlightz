"""What one LIVE channel actually costs this box, measured not guessed.

The live ceiling (config/settings.py, cores x 6) is the one number CLAUDE.md
and the handoff say not to raise casually. "Raise it" is a guess until somebody
knows what the channels already live cost. This measures the box as it is
running right now and says what there is room for.

    /opt/highlightz/venv/bin/python scripts/stream_cost.py
    /opt/highlightz/venv/bin/python scripts/stream_cost.py --sample=20

Stdlib and /proc only: no psutil, because a deploy never runs pip install and a
new runtime dependency crash-loops the service. Read-only. It never prints a
command line (a stream URL carries a signed token), only counts.

THREE THINGS CAN RUN OUT, and only two of them grow with a resize:

  1. Memory. Each live channel is four small processes.
  2. CPU across every core. Those four processes are separate, so the kernel
     spreads them over all the cores: more cores carry more channels.
  3. THE APP'S ONE LOOP. Chat scoring, every Kick webhook, every dashboard
     request and every socket run on ONE thread of ONE Python process, which
     can use one core and no more, however many the box has. A resize does not
     move this limit at all, so on a big box it is the one that binds first,
     and it is measured here separately for that reason.

REWRITTEN 2026-11-13 for the resize. The first version counted LIVE channels as
"how many streamlink processes are running". That was right when a live channel
ran one (the audio meter). Since video capture, each live channel runs TWO (the
meter's and the recorder's), so it reported twice the channels at half the cost
each and a ceiling twice what the box could take. Processes are now told apart
by what they were started to do.
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PAGE = os.sysconf("SC_PAGE_SIZE")
TICKS = os.sysconf("SC_CLK_TCK")
MB = 1048576
GB = 1073741824

# The kinds that exist ONLY because a channel is live: one of each per channel.
PER_LIVE = ("meter_streamlink", "meter_ffmpeg", "recorder_streamlink", "recorder_ffmpeg")

# How much of the limits this is willing to plan to fill. Not 100%: a box at
# 100% stops answering instead of slowing down, and the busiest night is
# busier than the one that was measured.
CPU_BUDGET = 0.75        # of every core together
LOOP_BUDGET = 70.0       # percent of the ONE core the app's loop can use
THIN = 5                 # fewer live channels than this is not a measurement


def meminfo() -> dict:
    out = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        out[k] = int(v.split()[0]) * 1024        # kB -> bytes
    return out


def _stat(path) -> tuple[str, int, int]:
    """(name, rss_bytes, cpu_ticks) from a /proc stat file."""
    stat = pathlib.Path(path).read_text()
    # comm is parenthesised and may contain spaces, so split on the LAST ')'
    # rather than on whitespace: "(ffmpeg -i x)" would otherwise shift every
    # field after it and silently mis-read cpu and rss.
    close = stat.rindex(")")
    name = stat[stat.index("(") + 1:close]
    fields = stat[close + 2:].split()
    return name, int(fields[21]) * PAGE, int(fields[11]) + int(fields[12])


def classify(name: str, argv: list[str]) -> str:
    """What a process is for. `argv` may be empty (not ours to read)."""
    n = (name or "").lower()
    heads = [os.path.basename(a).lower() for a in argv[:3]]
    if "streamlink" in n or "streamlink" in heads:
        # The meter asks for the audio-only rendition; the recorder asks for
        # video (settings.clip_capture_quality).
        return "meter_streamlink" if "audio_only,worst" in argv else "recorder_streamlink"
    if "ffmpeg" in n or "ffmpeg" in heads:
        if "segment" in argv:                    # -f segment: the rolling buffer
            return "recorder_ffmpeg"
        if "-vn" in argv and "s16le" in argv:    # video dropped, raw PCM out
            return "meter_ffmpeg"
        return "other_ffmpeg"                    # a cut, a render, a thumbnail
    if "python" in n or (heads and "python" in heads[0]):
        return "app" if "src.main" in argv else "other_python"
    return "other"


def procs() -> dict:
    """{pid: (kind, rss_bytes, cpu_ticks, main_thread_ticks)} for every process
    this user can read."""
    out = {}
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            name, rss, ticks = _stat(p / "stat")
            try:
                argv = (p / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
            except OSError:
                argv = []
            kind = classify(name, argv)
            main = 0
            if kind == "app":
                # The thread whose id is the process id is the one the event
                # loop runs on. Worker threads (a transcription, a file hash)
                # are in the process total and not in this.
                main = _stat(p / "task" / p.name / "stat")[2]
            out[int(p.name)] = (kind, rss, ticks, main)
        except (OSError, ValueError, IndexError):
            continue      # process exited mid-read, or not ours to see
    return out


def totals(a: dict, b: dict, seconds: float) -> tuple[dict, float]:
    """Two readings `seconds` apart -> ({kind: (procs, rss, cpu%)}, loop%).

    cpu% is percent of ONE core, so a group can exceed 100. loop% is the app's
    main thread alone. A process that started or ended between the readings is
    left out: half a sample of it is not a rate."""
    tot, loop = {}, 0.0
    for pid, (kind, rss, ticks, main) in b.items():
        if pid not in a or a[pid][0] != kind:
            continue
        cpu = (ticks - a[pid][2]) / TICKS / seconds * 100
        n, r, c = tot.get(kind, (0, 0.0, 0.0))
        tot[kind] = (n + 1, r + rss, c + cpu)
        if kind == "app":
            loop += (main - a[pid][3]) / TICKS / seconds * 100
    return tot, loop


def live_channels(tot: dict) -> int:
    """Channels being watched live, from the processes that exist only for one.

    The meter's streamlink is one per live channel by construction. The
    recorder's is the fallback for a box running capture without the meter.
    NEVER the two added together: that is the double count this file had."""
    return tot.get("meter_streamlink", (0,))[0] or tot.get("recorder_streamlink", (0,))[0]


def per_live(tot: dict, live: int) -> tuple[float, float]:
    """(bytes, cpu%) one live channel's own processes cost."""
    if live <= 0:
        return 0.0, 0.0
    rss = sum(tot[k][1] for k in PER_LIVE if k in tot)
    cpu = sum(tot[k][2] for k in PER_LIVE if k in tot)
    return rss / live, cpu / live


def room(*, live: int, cores: int, mem_available: float, mem_total: float,
         mem_each: float, cpu_each: float, app_cpu: float, loop_cpu: float) -> dict:
    """How many live channels each limit supports, and which binds.

    Every figure scales one sample linearly, and each is rounded the careful
    way: the app's whole CPU is charged to the live channels (some of it is the
    dashboard and would not grow), so the true room is this or more."""
    out = {"by_mem": None, "by_cpu": None, "by_loop": None, "safe": None, "binding": None}
    if live <= 0 or mem_each <= 0 or cpu_each <= 0:
        return out
    # Kept back for the OS, a caption model and a VOD scan. On a box with no
    # swap, running out of memory is a kill, not a slowdown.
    reserve = max(400 * MB, 0.10 * mem_total)
    out["reserve"] = reserve
    out["by_mem"] = int(live + max(0.0, mem_available - reserve) / mem_each)
    out["by_cpu"] = int(cores * 100 * CPU_BUDGET / (cpu_each + app_cpu / live))
    # Under 2% the loop is idle and one sample of it is noise, not a rate.
    if loop_cpu >= 2.0:
        out["by_loop"] = int(live * LOOP_BUDGET / loop_cpu)
    limits = {k: v for k, v in (("memory", out["by_mem"]), ("CPU", out["by_cpu"]),
                                ("the app's one loop", out["by_loop"])) if v is not None}
    out["binding"] = min(limits, key=limits.get)
    out["safe"] = max(1, limits[out["binding"]])
    return out


def verdict(*, live: int, cap: int, safe: int, production: bool) -> list[str]:
    """What to do about the limit, in words.

    THE HONESTY GUARD comes first. Every number is one sample scaled linearly,
    and a confident limit from three channels on a quiet afternoon is exactly
    the guess CLAUDE.md says not to make, so it is refused rather than dressed
    up with a caveat nobody reads."""
    if not production:
        return ["NOT PRODUCTION. This is not /opt/highlightz, so these numbers describe",
                "a different machine with different cores and memory. They say nothing",
                "about what the droplet can take. Re-run there."]
    if live < THIN:
        return [f"TOO THIN TO DECIDE. Only {live} channel(s) were live, and the estimate",
                "scales one sample linearly. Re-run at your busiest before moving the",
                "limit."]
    if safe > cap:
        return [f"RAISING IT LOOKS SUPPORTED. {cap} -> {safe} fits the measured cost.",
                "Move it in ONE step, not to the computed maximum, and re-run this",
                "with the new channels actually live before going further: thin chat",
                "is far cheaper than a 40k-viewer channel, and the limit has to hold",
                "on the worst night, not the night you measured."]
    if safe < cap:
        out = [f"THE LIMIT IS HIGHER THAN THIS SAMPLE SUPPORTS. The site allows {cap}",
               f"live and the measured cost supports about {safe}."]
        if live > safe:
            out.append(f"{live} ARE LIVE NOW, which is already past it: expect a slow site.")
        return out + [f"Pin MAX_CONCURRENT_STREAMS={safe} in .env and restart, so the box is",
                      "not asked for more than it can run."]
    return [f"THE LIMIT FITS. The measured cost supports about {safe} live and the",
            f"limit is {cap}. Leave it."]


def fmt(b: float) -> str:
    return f"{b / MB:.0f}MB" if b < GB else f"{b / GB:.2f}GB"


def main(args: list[str]) -> int:
    from config import settings as cfg
    from config.settings import settings
    from src.dashboard import api
    from src.clips import files as clip_files
    from src.ingestion import clip_recorder
    import shutil

    sample = 10.0
    for a in args:
        if a.startswith("--sample="):
            sample = float(a.split("=", 1)[1])

    cores = cfg._usable_cpus()
    mem = meminfo()
    cap = max(1, settings.max_concurrent_streams)
    listed_cap = max(1, settings.max_registered_streams)
    pinned = "max_concurrent_streams" in settings.model_fields_set
    listed = len(api._counted_stream_keys())

    print("=" * 70)
    print("THE BOX")
    print("=" * 70)
    print(f"  cores                  : {cores}")
    print(f"  memory total           : {fmt(mem['MemTotal'])}")
    print(f"  memory available       : {fmt(mem['MemAvailable'])}"
          f"   ({mem['MemAvailable'] / mem['MemTotal'] * 100:.0f}% free)")
    if mem.get("SwapTotal"):
        used = mem["SwapTotal"] - mem.get("SwapFree", 0)
        print(f"  swap                   : {fmt(used)} of {fmt(mem['SwapTotal'])} used"
              + ("   <- swapping means the box is already over its memory" if used > 50 * MB else ""))
    else:
        print("  swap                   : none configured"
              "   <- an OOM kills the service outright rather than slowing it")
    load = pathlib.Path("/proc/loadavg").read_text().split()[:3]
    print(f"  load 1/5/15min         : {' / '.join(load)}"
          f"   ({float(load[1]) / cores:.2f} per core over 5min)")
    try:
        du = shutil.disk_usage(settings.local_storage_path)
        print(f"  disk                   : {fmt(du.used)} used of {fmt(du.total)}"
              f"   ({du.free / du.total * 100:.0f}% free)")
    except OSError as exc:
        print(f"  disk                   : could not be read ({exc.strerror})")

    print()
    print("=" * 70)
    print("THE LIMITS THE SITE IS RUNNING WITH")
    print("=" * 70)
    print(f"  live at once           : {cap}"
          + ("   <- pinned by MAX_CONCURRENT_STREAMS in .env" if pinned
             else f"   <- {cores} cores x {cfg._STREAMS_PER_CPU}, derived at startup"))
    print(f"  channels listed        : {listed} of {listed_cap}")
    buf_cap = settings.clip_capture_max_total_mb * MB
    buf_now = clip_recorder.total_bytes()
    store_cap = settings.clip_file_max_total_mb * MB
    store_now = clip_files.total_bytes()
    print(f"  rolling video buffers  : {fmt(buf_now)} of {fmt(buf_cap)}"
          + ("   <- capture is OFF" if not settings.clip_capture_enabled else ""))
    print(f"  cut clips on disk      : {fmt(store_now)} of {fmt(store_cap)}")

    print()
    print("=" * 70)
    print(f"WHAT IS RUNNING  (sampling CPU for {sample:.0f}s; CPU% is of ONE core)")
    print("=" * 70)
    a = procs()
    time.sleep(sample)
    tot, loop_cpu = totals(a, procs(), sample)

    labels = (("app", "the app"), ("meter_streamlink", "audio: streamlink"),
              ("meter_ffmpeg", "audio: ffmpeg"), ("recorder_streamlink", "video: streamlink"),
              ("recorder_ffmpeg", "video: ffmpeg"), ("other_ffmpeg", "other ffmpeg"),
              ("other_python", "other python"))
    print(f"  {'what':<20}{'procs':>7}{'RSS':>12}{'CPU%':>10}")
    for kind, label in labels:
        if kind in tot:
            n, r, c = tot[kind]
            print(f"  {label:<20}{n:>7}{fmt(r):>12}{c:>9.1f}%")
    ours = [v for k, v in tot.items() if k != "other"]
    print(f"  {'-> highlightz':<20}{'':>7}{fmt(sum(v[1] for v in ours)):>12}"
          f"{sum(v[2] for v in ours):>9.1f}%   of {cores * 100}% on this box")

    live = live_channels(tot)
    mem_each, cpu_each = per_live(tot, live)
    app_rss, app_cpu = tot.get("app", (0, 0.0, 0.0))[1:]

    print()
    print("=" * 70)
    print("PER LIVE CHANNEL")
    print("=" * 70)
    print(f"  live right now         : {live}   (of {cap} allowed, {listed} listed)")
    if "app" not in tot:
        print("  THE APP IS NOT RUNNING (no `python -m src.main` found), so there is")
        print("  nothing to measure. Start the service and run this again.")
        return 0
    if live <= 0:
        print()
        print("  NOTHING IS LIVE, so there is no per-channel cost to measure and no")
        print("  basis for a new limit. Run this while your usual number of channels")
        print("  are live: the whole point is to measure the real load.")
        return 0
    print(f"  its own processes      : ~{fmt(mem_each)}, ~{cpu_each:.1f}% of one core"
          "   (audio + video, streamlink + ffmpeg)")
    print(f"  the app, all threads   : {fmt(app_rss)}, {app_cpu:.1f}% of one core")
    print(f"  the app's ONE loop     : {loop_cpu:.1f}% of the one core it can use"
          f"   (~{loop_cpu / live:.1f}% per live channel)")
    if settings.clip_capture_enabled and tot.get("recorder_streamlink"):
        rings = tot["recorder_streamlink"][0]
        print(f"  rolling buffer each    : ~{fmt(buf_now / rings)}"
              f"   (the budget allows {cfg._CAPTURE_MB_PER_LIVE}MB each)")

    print()
    print("=" * 70)
    print("HOW MANY LIVE CHANNELS THIS BOX CAN CARRY")
    print("=" * 70)
    r = room(live=live, cores=cores, mem_available=mem["MemAvailable"],
             mem_total=mem["MemTotal"], mem_each=mem_each, cpu_each=cpu_each,
             app_cpu=app_cpu, loop_cpu=loop_cpu)
    safe = r["safe"]
    if safe is None:
        print("  CANNOT SAY. The live channels' own processes showed no cost in this")
        print("  sample, so there is nothing to scale from. Run it again.")
        return 0
    print(f"  by memory              : ~{r['by_mem']}"
          f"   (keeping {fmt(r['reserve'])} back for the OS, captions and VOD scans)")
    print(f"  by CPU, all {cores} cores   : ~{r['by_cpu']}"
          f"   (staying under {CPU_BUDGET * 100:.0f}%)")
    if r["by_loop"] is None:
        print("  by the app's one loop  : not the limit today (it is nearly idle)")
    else:
        print(f"  by the app's one loop  : ~{r['by_loop']}"
              f"   (staying under {LOOP_BUDGET:.0f}% of its one core; MORE CORES DO NOT RAISE THIS)")
    print(f"  -> {r['binding']} runs out first: about {safe} live,"
          f" which is {safe / cores:.1f} per core (the code uses {cfg._STREAMS_PER_CPU})")

    print()
    here = str(pathlib.Path(__file__).resolve()).startswith("/opt/highlightz")
    for line in verdict(live=live, cap=cap, safe=safe, production=here):
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
