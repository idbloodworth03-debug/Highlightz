"""
Read-only analysis of clips/human_scores.jsonl — the blind human-vs-bot
calibration dataset from the Training Studio.

Each record pairs a human's 1-10 rating of a clip with the bot's hidden signal
values for the same clip. This report answers, in order of what actually
drives a formula change:

  1. Volume & coverage — enough data, spread across enough channels/labelers?
  2. Are the humans discriminating, or parking every slider at 5?
  3. Does the bot's OVERALL score rank clips the way humans do? (headline)
  4. Which individual signals predict human-judged virality? (drives weights)
  5. A data-derived weight proposal vs the weights running today.
  6. Inter-rater agreement, and the clips worth rewatching.

Never writes anything. Run on the server:
  venv/bin/python -m src.maintenance.analyze_human_scores [--days N]
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from config.settings import settings
from src.trigger import scoring

_FILE = Path(settings.local_storage_path) / "human_scores.jsonl"

# human slider key -> bot signal key. Sliders for chat_velocity/keyword were
# retired (humans can't judge message rates from a 30s clip); historical
# records still carry them and are still read here.
DIMS = {
    "chat_velocity": "CHAT_VELOCITY",
    "keyword":       "KEYWORD",
    "sentiment":     "SENTIMENT",
    "audio":         "AUDIO_SPIKE",
}

# Weights running in production. Chat-side comes from scoring.CHAT_WEIGHTS;
# the three live-only signals mirror engine.py's base_weights (keep in sync).
CURRENT_WEIGHTS = {
    "CHAT_VELOCITY":     scoring.CHAT_WEIGHTS["CHAT_VELOCITY"],
    "KEYWORD":           scoring.CHAT_WEIGHTS["KEYWORD"],
    "SENTIMENT":         scoring.CHAT_WEIGHTS["SENTIMENT"],
    "EMOTE_HOMOGENEITY": scoring.CHAT_WEIGHTS["EMOTE_HOMOGENEITY"],
    "AUDIO_SPIKE":       24,
    "VIEWER_SPIKE":      15,
    "SILENCE_BURST":     14,
}


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _significant(r: float | None, n: int) -> bool:
    """Rough 95% two-tailed test: |r| beyond ~1.96/sqrt(n-1) is unlikely noise."""
    if r is None or n < 10:
        return False
    return abs(r) > 1.96 / ((n - 1) ** 0.5)


def _pairs_for_signal(rows: list[dict], sig: str, human_key: str) -> list[tuple[float, float]]:
    out = []
    for r in rows:
        h = (r.get("human") or {}).get(human_key)
        b = (r.get("bot_signals") or {}).get(sig)
        if h is not None and b is not None:
            out.append((float(h), float(b)))
    return out


def main() -> int:
    days = None
    args = sys.argv[1:]
    if "--days" in args:
        days = float(args[args.index("--days") + 1])
    cutoff = time.time() - days * 86400 if days else 0

    if not _FILE.exists():
        print(f"no data yet at {_FILE}")
        return 1
    rows = []
    for line in _FILE.open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ts", 0) >= cutoff and r.get("human") and r.get("bot_signals") is not None:
            rows.append(r)
    scope = f"last {days:g} days" if days else "all time"
    print(f"=== human-vs-bot calibration - {scope} ===\n")
    if not rows:
        print("No matching records.")
        return 0

    # -- 1. volume & coverage ------------------------------------------------
    per_lab = defaultdict(int)
    per_chan = defaultdict(int)
    for r in rows:
        per_lab[r.get("labeler") or r.get("labeler_id") or "?"] += 1
        per_chan[r.get("channel") or "?"] += 1
    ts = [r.get("ts", 0) for r in rows if r.get("ts")]
    span = ""
    if ts:
        span = (f"  {time.strftime('%Y-%m-%d', time.localtime(min(ts)))}"
                f" -> {time.strftime('%Y-%m-%d', time.localtime(max(ts)))}")
    print(f"-- 1. volume --{span}")
    print(f"  records: {len(rows)}   channels: {len(per_chan)}   labelers: {len(per_lab)}")
    print("  by labeler: " + ", ".join(f"{k} {v}" for k, v in
                                       sorted(per_lab.items(), key=lambda kv: -kv[1])))
    top = sorted(per_chan.items(), key=lambda kv: -kv[1])[:6]
    print("  top channels: " + ", ".join(f"{k} {v}" for k, v in top))
    if len(per_chan) < 3:
        print("  WARNING thin channel spread - weights fitted here may overfit one streamer.")

    # -- 2. are humans discriminating? ---------------------------------------
    print("\n-- 2. human score distribution (are trainers using the range?) --")
    print(f"  {'slider':12s} {'n':>5s} {'mean':>6s} {'sd':>5s}  histogram 1->10")
    all_h = defaultdict(list)
    for r in rows:
        for k, v in (r.get("human") or {}).items():
            if v is not None:
                all_h[k].append(float(v))
    for k, vals in sorted(all_h.items(), key=lambda kv: -len(kv[1])):
        n = len(vals)
        mean = sum(vals) / n
        sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
        buckets = [0] * 10
        for v in vals:
            buckets[min(9, max(0, int(v) - 1))] += 1
        peak = max(buckets) or 1
        bar = "".join(" .:-=+*#%@"[min(9, int(9 * b / peak))] for b in buckets)
        flag = "  WARNING low spread" if sd < 1.2 else ""
        print(f"  {k:12s} {n:5d} {mean:6.2f} {sd:5.2f}  {bar}{flag}")

    # -- 3. headline: does the overall bot score match human judgment? -------
    print("\n-- 3. HEADLINE - bot's overall score vs human virality --")
    hv = [(float(r["human"]["virality"]), float(r["bot_trigger_score"]))
          for r in rows
          if (r.get("human") or {}).get("virality") is not None
          and isinstance(r.get("bot_trigger_score"), (int, float))]
    if len(hv) >= 3:
        r_all = spearman([a for a, _ in hv], [b for _, b in hv])
        sig = "significant" if _significant(r_all, len(hv)) else "NOT significant"
        print(f"  trigger_score  vs human virality: {r_all:+.3f}  (n={len(hv)}, {sig})")
        vv = [(float(r["human"]["virality"]), float(r["bot_virality_score"]))
              for r in rows
              if (r.get("human") or {}).get("virality") is not None
              and isinstance(r.get("bot_virality_score"), (int, float))]
        if len(vv) >= 3:
            r_v = spearman([a for a, _ in vv], [b for _, b in vv])
            print(f"  virality_score vs human virality: {r_v:+.3f}  (n={len(vv)})")
        hv_sorted = sorted(hv, key=lambda t: -t[1])
        k = max(1, len(hv_sorted) // 10)
        top_m = sum(a for a, _ in hv_sorted[:k]) / k
        bot_m = sum(a for a, _ in hv_sorted[-k:]) / k
        print(f"  human score on bot's top 10%: {top_m:.2f}   bottom 10%: {bot_m:.2f}"
              f"   gap: {top_m - bot_m:+.2f}")
        if abs(r_all or 0) < 0.15:
            print("  WARNING the overall score barely tracks human judgment.")
    else:
        print("  not enough virality ratings yet.")

    # -- 4. which signals predict human virality? (drives weights) -----------
    print("\n-- 4. each signal vs HUMAN VIRALITY (this is what sets weights) --")
    print(f"  {'signal':20s} {'corr':>7s} {'n':>5s} {'weight':>7s}  verdict")
    sig_corr: dict[str, tuple[float, int]] = {}
    for sig in sorted(CURRENT_WEIGHTS, key=lambda s: -CURRENT_WEIGHTS[s]):
        pairs = _pairs_for_signal(rows, sig, "virality")
        if len(pairs) < 10:
            print(f"  {sig:20s} {'-':>7s} {len(pairs):5d} {CURRENT_WEIGHTS[sig]:7d}  too little data")
            continue
        r_s = spearman([a for a, _ in pairs], [b for _, b in pairs])
        sig_corr[sig] = (r_s or 0.0, len(pairs))
        if not _significant(r_s, len(pairs)):
            verdict = "noise - no evidence it predicts"
        elif (r_s or 0) < 0:
            verdict = "INVERTED - fires on clips humans dislike"
        elif (r_s or 0) < 0.15:
            verdict = "weak"
        elif (r_s or 0) < 0.30:
            verdict = "moderate"
        else:
            verdict = "STRONG"
        print(f"  {sig:20s} {r_s:+7.3f} {len(pairs):5d} {CURRENT_WEIGHTS[sig]:7d}  {verdict}")

    # Also report the retired sliders against their own signal, for history.
    print("\n  (sanity: each retained slider vs its own matching signal)")
    for hk, bk in DIMS.items():
        pairs = _pairs_for_signal(rows, bk, hk)
        if len(pairs) >= 10:
            r_s = spearman([a for a, _ in pairs], [b for _, b in pairs])
            mark = "" if _significant(r_s, len(pairs)) else "  (not significant)"
            print(f"  {hk:20s} {r_s:+7.3f} {len(pairs):5d}{mark}")

    # -- 5. data-derived weight proposal -------------------------------------
    print("\n-- 5. weight proposal (correlation-derived, same total pool) --")
    usable = {s: c for s, (c, n) in sig_corr.items() if _significant(c, n) and c > 0}
    if not usable:
        print("  No signal clears significance - do NOT retune on this data yet.")
    else:
        pool = sum(CURRENT_WEIGHTS.values())
        total_c = sum(usable.values())
        # Cap any single signal at 40% of the pool. Letting one correlation
        # take the whole budget builds a single-signal formula: when that
        # signal is quiet (or its source breaks) NOTHING clips. Whatever the
        # cap frees up stays with the non-predictive signals, which keep their
        # relative shape — they're unproven, not proven harmful.
        cap = 0.40 * pool
        prop: dict[str, float] = {}
        for s, c in usable.items():
            prop[s] = min(cap, pool * c / total_c)
        rest = [s for s in CURRENT_WEIGHTS if s not in usable]
        left = max(0.0, pool - sum(prop.values()))
        rest_now = sum(CURRENT_WEIGHTS[s] for s in rest) or 1
        for s in rest:
            prop[s] = max(2.0, left * CURRENT_WEIGHTS[s] / rest_now)
        if any(pool * c / total_c > cap for c in usable.values()):
            print(f"  (capped: no single signal may exceed 40% of pool = {cap:.0f})")
        print(f"  {'signal':20s} {'now':>5s} {'proposed':>9s} {'change':>8s}")
        for sig in sorted(CURRENT_WEIGHTS, key=lambda s: -CURRENT_WEIGHTS[s]):
            now, new = CURRENT_WEIGHTS[sig], round(prop[sig])
            print(f"  {sig:20s} {now:5d} {new:9d} {new - now:+8d}")
        print("  NOTE: a proposal is not a decision - sanity-check each move against")
        print("  what it does on a healthy stream before applying (see CLAUDE.md).")

    # -- 6. agreement between labelers ---------------------------------------
    print("\n-- 6. inter-rater agreement --")
    by_clip: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_clip[r.get("clip_id") or "?"].append(r)
    multi = {c: rs for c, rs in by_clip.items() if len(rs) > 1}
    print(f"  clips scored by 2+ labelers: {len(multi)}")
    if multi:
        for dim in ("virality", "sentiment", "audio"):
            a, b = [], []
            for rs in multi.values():
                vals = [(x.get("human") or {}).get(dim) for x in rs]
                vals = [v for v in vals if v is not None]
                if len(vals) >= 2:
                    a.append(float(vals[0]))
                    b.append(float(vals[1]))
            if len(a) >= 3:
                r_ir = spearman(a, b)
                note = "" if (r_ir or 0) > 0.4 else "  WARNING humans disagree with each other"
                print(f"  {dim:10s} rater-vs-rater: {r_ir:+.3f} (n={len(a)}){note}")
    else:
        print("  none - without overlap there's no way to measure human consistency.")

    # -- 7. biggest disagreements --------------------------------------------
    print("\n-- 7. biggest bot-vs-human disagreements (rewatch these) --")
    scored = []
    for r in rows:
        h = (r.get("human") or {}).get("virality")
        b = r.get("bot_trigger_score")
        if h is None or not isinstance(b, (int, float)):
            continue
        scored.append((abs((float(h) - 1) / 9 - float(b) / 100.0), r, float(h), float(b)))
    scored.sort(key=lambda t: -t[0])
    for gap, r, h, b in scored[:10]:
        print(f"  gap {gap:.2f}  {(r.get('channel') or '?')[:16]:16s} "
              f"human {h:.0f}/10 vs bot {b:.0f}/100  clip {str(r.get('clip_id'))[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
