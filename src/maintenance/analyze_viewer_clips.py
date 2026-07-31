"""
Viewer-clip analysis — did the bot catch what real viewers cared about?

    venv/bin/python -m src.maintenance.analyze_viewer_clips
    venv/bin/python -m src.maintenance.analyze_viewer_clips --channel jynxzi
    venv/bin/python -m src.maintenance.analyze_viewer_clips --misses 40

Read-only. Reads clips/viewer_clips.jsonl (the learning watcher) and
clips/clips.json (what we actually captured). Writes nothing, changes nothing.

WHY THIS IS THE MOST HONEST BENCHMARK WE HAVE
Every signal in this project is a proxy for "a human would want this clipped".
The 1001 hand-scored clips said the proxies are weak — trigger_score correlates
just +0.081 with human-judged virality. A viewer clip is not a proxy: someone
watched the stream and decided the moment was worth keeping. That is free,
unprompted ground truth at volume.

READ THE CAVEATS. They are not boilerplate; three of them can invert a
conclusion:

1. RECALL ONLY, NEVER PRECISION. This dataset says which viewer-clipped
   moments we missed. It says NOTHING about whether the clips we DID take were
   good — no viewer clipping a moment is not evidence the moment was bad
   (small channels, sleepy chat, nobody watching at 4am). Do not read a low
   hit rate as "the bot is broken" or a high one as "the bot is good".

2. LOWERING A THRESHOLD IS NOT FREE. The sweep shows what a lower bar would
   have CAUGHT. It cannot show what that bar would have ALSO caught — this
   file only contains moments viewers clipped, so there is no sample of the
   ordinary stream to count new false positives against. A bar low enough to
   catch every viewer clip may also fire constantly. Validate any change with
   simulate_weights.py before deploying, as always.

3. old records are biased LOW. Records written before the peak-window change
   only carry `our_score`, a point sample at the clip's created_at — i.e. the
   aftermath, seconds after the spike. Those understate the bot. Records with
   `our_peak` are the trustworthy ones; the report says which it used and
   refuses to mix them silently.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from config.settings import settings

VIEWER_LOG = Path(settings.local_storage_path) / "viewer_clips.jsonl"
CLIPS_FILE = Path(settings.local_storage_path) / "clips.json"

# How close one of our clips must be to a viewer's for us to call it the same
# moment. Our clip fires at the peak; the viewer's created_at lands after it.
MATCH_BEFORE = 90.0
MATCH_AFTER  = 45.0

# Viewer clips within this many seconds of each other are the same moment.
CONSENSUS_GAP = 45.0

BAR = "─" * 72


def _pct(x: float, n: int) -> str:
    return f"{100.0 * x / n:5.1f}%" if n else "    —"


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[i]


def load_viewer_clips(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn last line from a kill -9 mid-write
    return rows


def load_our_clips(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def effective_score(r: dict) -> tuple[float | None, str]:
    """Our score for this moment, and which measure it came from.

    Prefers the window peak. Falls back to the point sample so older records
    still count, but the caller is told, because mixing them silently would
    understate the bot by an unknown amount.
    """
    if r.get("our_peak") is not None:
        return float(r["our_peak"]), "peak"
    if r.get("our_score") is not None:
        return float(r["our_score"]), "point"
    return None, "none"


def cluster_moments(rows: list[dict], gap: float = CONSENSUS_GAP) -> list[list[dict]]:
    """Group a channel's viewer clips into distinct MOMENTS.

    Ten people clipping one play is one highlight, not ten. Counting clips
    rather than moments would let a single viral moment dominate every number
    in this report.
    """
    out: list[list[dict]] = []
    for r in sorted(rows, key=lambda x: x.get("ts", 0)):
        if out and r.get("ts", 0) - out[-1][-1].get("ts", 0) <= gap:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def our_clip_near(ours: list[dict], channel: str, ts: float) -> dict | None:
    """Did WE capture a clip covering this moment? Truer than comparing scores
    to a threshold, because it accounts for cooldown and post-roll — a moment
    can beat the bar and still not produce a clip."""
    best, best_gap = None, None
    for c in ours:
        if (c.get("channel") or "").lower() != channel.lower():
            continue
        ct = c.get("created_at") or 0
        if ts - MATCH_BEFORE <= ct <= ts + MATCH_AFTER:
            gap = abs(ct - ts)
            if best_gap is None or gap < best_gap:
                best, best_gap = c, gap
    return best


def report(rows: list[dict], ours: list[dict], only_channel: str = "",
           show_misses: int = 25) -> None:
    if only_channel:
        rows = [r for r in rows if (r.get("channel") or "").lower() == only_channel.lower()]

    print(BAR)
    print("VIEWER-CLIP ANALYSIS — what the crowd clipped, and what we caught")
    print(BAR)

    if not rows:
        print("\nNo viewer clips recorded yet.")
        print("The watcher runs on the 60s viewer-poll loop while a stream is")
        print("monitored, and only records clips made by OTHER people. If this")
        print("is empty, either nothing was monitored or nobody clipped.")
        return

    # ── 1. Coverage ───────────────────────────────────────────────────────
    t0 = min(r.get("ts", 0) for r in rows)
    t1 = max(r.get("ts", 0) for r in rows)
    span_h = (t1 - t0) / 3600.0
    by_chan: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_chan[r.get("channel") or "?"].append(r)

    paired = [r for r in rows if effective_score(r)[0] is not None]
    kinds = {k for r in rows for k in [effective_score(r)[1]] if k != "none"}

    print(f"\n1. COVERAGE")
    print(f"   {len(rows)} viewer clips over {span_h:.1f}h across {len(by_chan)} channel(s)")
    print(f"   {time.strftime('%Y-%m-%d %H:%M', time.localtime(t0))}"
          f"  →  {time.strftime('%Y-%m-%d %H:%M', time.localtime(t1))}")
    print(f"   paired with one of our scores: {len(paired)}/{len(rows)}"
          f"  ({_pct(len(paired), len(rows)).strip()})")
    if len(paired) < len(rows):
        print(f"   {len(rows) - len(paired)} unpaired — we weren't watching that channel")
        print(f"     at that moment, or it fell outside our ~20min score history.")
    if kinds == {"point"}:
        print("\n   ⚠  ALL records are point samples (`our_score`), taken at the")
        print("      clip's created_at — i.e. AFTER the moment, when chat has")
        print("      calmed. Every number below UNDERSTATES the bot. Redeploy")
        print("      picks up `our_peak`; re-run once fresh data accumulates.")
    elif kinds == {"peak", "point"}:
        n_peak = sum(1 for r in paired if effective_score(r)[1] == "peak")
        print(f"\n   ⚠  Mixed measures: {n_peak} window peaks, {len(paired)-n_peak} older")
        print("      point samples. The point-sample rows understate the bot.")
        print("      Consider --peak-only once enough fresh data exists.")

    if not paired:
        print("\nNothing to compare — no record carries one of our scores.")
        return

    # ── 2. The headline ───────────────────────────────────────────────────
    print(f"\n2. DID WE SEE WHAT THEY SAW?")
    print(f"   Grouping clips into distinct moments (viewers within"
          f" {CONSENSUS_GAP:.0f}s = one moment).\n")
    print(f"   {'channel':<20} {'moments':>8} {'we scored ≥ bar':>16} {'we clipped it':>15}")
    print(f"   {'-'*20} {'-'*8} {'-'*16} {'-'*15}")

    tot_moments = tot_over = tot_clipped = 0
    per_chan_scores: dict[str, list[float]] = {}
    all_misses: list[tuple] = []

    for chan, crows in sorted(by_chan.items()):
        cpaired = [r for r in crows if effective_score(r)[0] is not None]
        if not cpaired:
            continue
        moments = cluster_moments(cpaired)
        scores = []
        over = clipped = 0
        for m in moments:
            # The moment's best evidence: our highest reading across it.
            best = max(effective_score(r)[0] for r in m)
            thr = next((r["threshold"] for r in m if r.get("threshold") is not None), None)
            scores.append(best)
            hit = thr is not None and best >= thr
            over += 1 if hit else 0
            mt = max(r.get("ts", 0) for r in m)
            got = our_clip_near(ours, chan, mt)
            clipped += 1 if got else 0
            if not got:
                all_misses.append((len(m), best, thr, chan, m))
        per_chan_scores[chan] = scores
        tot_moments += len(moments); tot_over += over; tot_clipped += clipped
        print(f"   {chan[:20]:<20} {len(moments):>8} "
              f"{_pct(over, len(moments)):>16} {_pct(clipped, len(moments)):>15}")

    print(f"   {'-'*20} {'-'*8} {'-'*16} {'-'*15}")
    print(f"   {'ALL':<20} {tot_moments:>8} "
          f"{_pct(tot_over, tot_moments):>16} {_pct(tot_clipped, tot_moments):>15}")
    print()
    print("   'we scored ≥ bar' = our score crossed the channel's threshold.")
    print("   'we clipped it'   = a real clip of ours lands within the window.")
    print("   The second is lower when cooldown or post-roll swallowed a")
    print("   moment that did beat the bar — that gap is a fixable bug, not a")
    print("   scoring problem.")

    # ── 3. Where our scores actually sat ──────────────────────────────────
    print(f"\n3. OUR SCORE AT MOMENTS VIEWERS CLIPPED")
    print(f"   {'channel':<20} {'bar':>6} {'p10':>7} {'p50':>7} {'p90':>7} {'max':>7}")
    print(f"   {'-'*20} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for chan, scores in sorted(per_chan_scores.items()):
        thr = next((r["threshold"] for r in by_chan[chan]
                    if r.get("threshold") is not None), None)
        print(f"   {chan[:20]:<20} {(f'{thr:.0f}' if thr else '?'):>6} "
              f"{_percentile(scores,.10):>7.1f} {_percentile(scores,.50):>7.1f} "
              f"{_percentile(scores,.90):>7.1f} {max(scores):>7.1f}")
    print()
    print("   If p50 sits far BELOW the bar, one of two things is true and this")
    print("   data cannot tell them apart: the bar is too high for that channel,")
    print("   OR our signals simply don't light up on what viewers care about.")
    print("   The threshold sweep below is only worth acting on in the first case.")

    # ── 4. Threshold sweep ────────────────────────────────────────────────
    print(f"\n4. WHAT A LOWER BAR WOULD HAVE CAUGHT")
    print(f"   {'channel':<20} {'bar':>6}" + "".join(f"{int(b):>7}" for b in (30,35,40,45,50,55,60,70)))
    print(f"   {'-'*20} {'-'*6}" + "-" * 56)
    for chan, scores in sorted(per_chan_scores.items()):
        thr = next((r["threshold"] for r in by_chan[chan]
                    if r.get("threshold") is not None), None)
        row = "".join(f"{100.0*sum(1 for s in scores if s >= b)/len(scores):>6.0f}%"
                      for b in (30,35,40,45,50,55,60,70))
        print(f"   {chan[:20]:<20} {(f'{thr:.0f}' if thr else '?'):>6}{row}")
    print()
    print("   ⚠  This shows only what a bar would have CAUGHT, never what it")
    print("      would ALSO have fired on. This file contains only clipped")
    print("      moments — there is no sample of ordinary stream here to count")
    print("      new false positives against. Run simulate_weights.py before")
    print("      changing anything.")

    # ── 5. Consensus ──────────────────────────────────────────────────────
    multi = [(n, s, t, c, m) for (n, s, t, c, m) in all_misses if n >= 2]
    print(f"\n5. CONSENSUS MOMENTS WE MISSED")
    print("   Multiple DISTINCT viewers clipping the same moment is the")
    print("   strongest evidence in this dataset. One person is taste; three")
    print("   is a highlight.")
    if not multi:
        print("\n   None — every multi-viewer moment produced a clip. That is the")
        print("   result you want.")
    else:
        print(f"\n   {len(multi)} multi-viewer moment(s) produced no clip of ours:\n")
        for n, s, thr, chan, m in sorted(multi, key=lambda x: -x[0])[:show_misses]:
            uniq = len({r.get("creator_id") for r in m})
            when = time.strftime("%m-%d %H:%M", time.localtime(max(r["ts"] for r in m)))
            gap = f"{s:.0f} vs bar {thr:.0f}" if thr else f"{s:.0f}"
            title = next((r.get("title") for r in m if r.get("title")), "")
            print(f"   {when}  {chan[:16]:<16} {uniq} viewers  we scored {gap}")
            if title:
                print(f"      “{title[:66]}”")
            print(f"      https://clips.twitch.tv/{m[0].get('clip_id','')}")

    # ── 6. The biggest single misses ──────────────────────────────────────
    print(f"\n6. BIGGEST MISSES — highest-scoring moments that still produced no clip")
    near = sorted([x for x in all_misses if x[2] is not None],
                  key=lambda x: -(x[1] / x[2] if x[2] else 0))[:show_misses]
    if not near:
        print("\n   None.")
    else:
        print("   These beat, or nearly beat, the bar and still yielded nothing —")
        print("   check cooldown and post-roll before touching any weights.\n")
        for n, s, thr, chan, m in near:
            when = time.strftime("%m-%d %H:%M", time.localtime(max(r["ts"] for r in m)))
            flag = "OVER BAR" if s >= thr else f"{100*s/thr:.0f}% of bar"
            print(f"   {when}  {chan[:16]:<16} {s:>5.1f}/{thr:<5.0f} {flag:>12}"
                  f"   https://clips.twitch.tv/{m[0].get('clip_id','')}")

    print(f"\n{BAR}")
    print("Recall only. This says nothing about whether the clips we DID take")
    print("were good — a moment nobody clipped is not a moment nobody wanted.")
    print(BAR)


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze viewer-clip learning data")
    ap.add_argument("--channel", default="", help="restrict to one channel")
    ap.add_argument("--misses", type=int, default=25, help="how many misses to list")
    ap.add_argument("--peak-only", action="store_true",
                    help="drop older point-sample records, which understate the bot")
    args = ap.parse_args()

    rows = load_viewer_clips(VIEWER_LOG)
    if args.peak_only:
        rows = [r for r in rows if r.get("our_peak") is not None]
    report(rows, load_our_clips(CLIPS_FILE), args.channel, args.misses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
