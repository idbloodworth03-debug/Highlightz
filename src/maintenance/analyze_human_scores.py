"""
Read-only analysis of clips/human_scores.jsonl — the blind human-vs-bot
calibration dataset from the Training Studio.

Each record pairs a human's 1-10 rating of a clip on four dimensions with the
bot's hidden signal values for the same clip. This report answers:

  1. How much data exists (total, per labeler)?
  2. Per dimension: does the bot's signal AGREE with human perception?
     Reported as Spearman rank correlation (1.0 = perfect agreement,
     0 = unrelated, negative = inverted).
  3. Where do bot and human disagree hardest (the clips worth rewatching)?
  4. Inter-labeler note: same-clip scores from different labelers, when any.

Never writes anything. Run on the server:
  venv/bin/python -m src.maintenance.analyze_human_scores [--days N]
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from config.settings import settings

_FILE = Path(settings.local_storage_path) / "human_scores.jsonl"

# human slider key -> bot signal key
DIMS = {
    "chat_velocity": "CHAT_VELOCITY",
    "keyword":       "KEYWORD",
    "sentiment":     "SENTIMENT",
    "audio":         "AUDIO_SPIKE",
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
    print(f"human-vs-bot calibration — {scope}\n")
    if not rows:
        print("No matching records.")
        return 0

    per = defaultdict(int)
    for r in rows:
        per[r.get("labeler") or r.get("labeler_id") or "?"] += 1
    print(f"records: {len(rows)}  by labeler: " +
          ", ".join(f"{k} {v}" for k, v in sorted(per.items(), key=lambda kv: -kv[1])))

    print("\n── agreement per dimension (Spearman rank correlation) ──")
    print("  1.0 = bot sees what humans see · 0 = unrelated · negative = inverted")
    print(f"  {'dimension':16s} {'corr':>6s} {'n':>5s}   mean(human) when bot high / low")
    for hk, bk in DIMS.items():
        pairs = [(r["human"].get(hk), (r["bot_signals"] or {}).get(bk))
                 for r in rows]
        pairs = [(h, b) for h, b in pairs if h is not None and b is not None]
        if not pairs:
            print(f"  {hk:16s}      —     0")
            continue
        hs = [float(h) for h, _ in pairs]
        bs = [float(b) for _, b in pairs]
        corr = spearman(hs, bs)
        hi = [h for h, b in zip(hs, bs) if b >= 0.5]
        lo = [h for h, b in zip(hs, bs) if b < 0.5]
        hi_m = f"{sum(hi)/len(hi):.1f}" if hi else " —"
        lo_m = f"{sum(lo)/len(lo):.1f}" if lo else " —"
        corr_s = f"{corr:+.2f}" if corr is not None else "    —"
        print(f"  {hk:16s} {corr_s:>6s} {len(pairs):5d}   {hi_m} / {lo_m}")

    print("\n── biggest disagreements (rewatch these) ──")
    scored = []
    for r in rows:
        gaps = []
        for hk, bk in DIMS.items():
            h = r["human"].get(hk)
            b = (r["bot_signals"] or {}).get(bk)
            if h is not None and b is not None:
                gaps.append(abs((float(h) - 1) / 9 - float(b)))   # both → 0..1
        if gaps:
            scored.append((sum(gaps) / len(gaps), r))
    scored.sort(key=lambda t: -t[0])
    for gap, r in scored[:8]:
        print(f"  gap {gap:.2f}  {r.get('channel','?'):18s} clip {str(r.get('clip_id'))[:8]}  "
              f"human {r['human']}  bot {r.get('bot_signals')}")

    dup: dict[str, int] = defaultdict(int)
    for r in rows:
        dup[r.get("clip_id") or "?"] += 1
    multi = sum(1 for v in dup.values() if v > 1)
    print(f"\nclips scored by 2+ labelers: {multi} "
          "(more of these = measurable inter-rater consistency)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
