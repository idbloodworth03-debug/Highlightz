"""
Read-only analysis of clips/training_log.jsonl — the labeled clip-outcome
dataset. Answers, from evidence instead of intuition:

  1. How is the data distributed (labels × time × channel)? The log spans
     formula eras (pre/post feedback-loop fix, threshold retunes), so recent
     months matter more than the total.
  2. Which signals SEPARATE approved from rejected/expired clips? Reported as
     per-label means and a rank AUC per signal (0.5 = no separation, 1.0 =
     perfect). A signal with AUC ≈ 0.5 is dead weight at any base weight.
  3. Which dominant (argmax) signal leads good vs bad clips?
  4. What-if threshold sweep: for each score cutoff, how many rejected clips
     would never have fired vs how many approved ones would have been lost.

This script never writes anything. Run on the server:
  venv/bin/python -m src.maintenance.analyze_training_log [--days N] [--channel X]
"""

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from config.settings import settings

_LOG_FILE = Path(settings.local_storage_path) / "training_log.jsonl"

POS = "approved"
NEGS = ("rejected", "expired_unreviewed")


def _load(days: float | None, channel: str | None) -> list[dict]:
    cutoff = time.time() - days * 86400 if days else 0
    rows = []
    for line in _LOG_FILE.open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ts", 0) < cutoff:
            continue
        if channel and r.get("channel", "").lower() != channel.lower():
            continue
        if r.get("label") not in (POS,) + tuple(NEGS):
            continue
        rows.append(r)
    return rows


def _auc(pos_vals: list[float], neg_vals: list[float]) -> float | None:
    """Rank AUC via pairwise comparison (fine at this dataset size)."""
    if not pos_vals or not neg_vals:
        return None
    wins = ties = 0
    for p in pos_vals:
        for n in neg_vals:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + ties / 2) / (len(pos_vals) * len(neg_vals))


def _fmt_month(ts: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def report(rows: list[dict]) -> None:
    if not rows:
        print("No matching records.")
        return

    pos = [r for r in rows if r["label"] == POS]
    neg = [r for r in rows if r["label"] in NEGS]

    print(f"records: {len(rows)}  (approved {len(pos)}, "
          f"rejected {sum(1 for r in rows if r['label']=='rejected')}, "
          f"expired {sum(1 for r in rows if r['label']=='expired_unreviewed')})")

    # ── Label mix per month (era segmentation) ────────────────────────────
    print("\n── by month (approval rate per era) ──")
    months: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        months[_fmt_month(r.get("ts", 0))][r["label"]] += 1
    for m in sorted(months):
        c = months[m]
        total = sum(c.values())
        ap = c[POS] / total * 100 if total else 0
        print(f"  {m}: {total:4d} clips | approved {c[POS]:3d} ({ap:4.1f}%) "
              f"| rejected {c['rejected']:4d} | expired {c['expired_unreviewed']:3d}")

    # ── Per-channel mix (top by volume) ───────────────────────────────────
    print("\n── by channel (top 10 by volume) ──")
    chans: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        chans[r.get("channel", "?")][r["label"]] += 1
    for ch, c in sorted(chans.items(), key=lambda kv: -sum(kv[1].values()))[:10]:
        total = sum(c.values())
        ap = c[POS] / total * 100 if total else 0
        print(f"  {ch:20s} {total:4d} clips | approved {c[POS]:3d} ({ap:4.1f}%)")

    # ── Signal separation ─────────────────────────────────────────────────
    print("\n── signal separation: approved vs rejected+expired ──")
    print("  (AUC 0.5 = signal tells us nothing; >0.6 useful; <0.4 inversely useful)")
    all_keys = sorted({k for r in rows for k in (r.get("signals") or {})})
    print(f"  {'signal':20s} {'mean(appr)':>10s} {'mean(neg)':>10s} {'AUC':>6s}  n(appr)/n(neg)")
    for key in all_keys:
        pv = [r["signals"][key] for r in pos if key in (r.get("signals") or {})]
        nv = [r["signals"][key] for r in neg if key in (r.get("signals") or {})]
        if not pv and not nv:
            continue
        mean_p = sum(pv) / len(pv) if pv else float("nan")
        mean_n = sum(nv) / len(nv) if nv else float("nan")
        auc = _auc(pv, nv)
        auc_s = f"{auc:.2f}" if auc is not None else "  —"
        print(f"  {key:20s} {mean_p:10.3f} {mean_n:10.3f} {auc_s:>6s}  {len(pv)}/{len(nv)}")

    # trigger_score / virality separation
    for field in ("trigger_score", "virality_score"):
        pv = [r[field] for r in pos if isinstance(r.get(field), (int, float))]
        nv = [r[field] for r in neg if isinstance(r.get(field), (int, float))]
        if pv and nv:
            auc = _auc(pv, nv)
            print(f"  {field:20s} {sum(pv)/len(pv):10.1f} {sum(nv)/len(nv):10.1f} "
                  f"{auc:6.2f}  {len(pv)}/{len(nv)}")

    # ── Dominant-signal analysis ──────────────────────────────────────────
    print("\n── dominant (argmax) signal per clip ──")
    dom: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        sigs = r.get("signals") or {}
        if not sigs:
            continue
        lead = max(sigs.items(), key=lambda kv: kv[1])[0]
        dom[lead][r["label"]] += 1
    print(f"  {'led by':20s} {'clips':>6s} {'approved':>9s} {'appr %':>7s}")
    for lead, c in sorted(dom.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(c.values())
        ap = c[POS] / total * 100 if total else 0
        print(f"  {lead:20s} {total:6d} {c[POS]:9d} {ap:6.1f}%")

    # ── What-if threshold sweep ───────────────────────────────────────────
    print("\n── what-if: raise the floor to T (clips with trigger_score < T never fire) ──")
    print("  keeps = approved retained; junk cut = negatives suppressed")
    scored = [(r.get("trigger_score") or 0, r["label"]) for r in rows
              if isinstance(r.get("trigger_score"), (int, float))]
    n_pos = sum(1 for _, l in scored if l == POS)
    n_neg = len(scored) - n_pos
    for t in (50, 55, 60, 65, 70, 75, 80, 85, 90):
        keep_p = sum(1 for s, l in scored if l == POS and s >= t)
        cut_n = sum(1 for s, l in scored if l != POS and s < t)
        fired = sum(1 for s, _ in scored if s >= t)
        prec = (keep_p / fired * 100) if fired else 0
        print(f"  T={t:2d}: keeps {keep_p:3d}/{n_pos} approved | cuts {cut_n:4d}/{n_neg} junk "
              f"| precision of what fires {prec:5.1f}%")


def main() -> int:
    days = channel = None
    args = sys.argv[1:]
    if "--days" in args:
        days = float(args[args.index("--days") + 1])
    if "--channel" in args:
        channel = args[args.index("--channel") + 1]
    if not _LOG_FILE.exists():
        print(f"no log at {_LOG_FILE}")
        return 1
    scope = f"last {days:g} days" if days else "all time"
    if channel:
        scope += f", channel={channel}"
    print(f"training log analysis — {scope}\n")
    report(_load(days, channel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
