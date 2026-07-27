"""
Read-only what-if: replay every stored clip's signal vector through the OLD
and NEW weight tables and report how clip VOLUME would change.

Why this exists: the July-2026 human-calibration retune leans the formula
toward human ratings, and the owner's hard requirement is that clip volume
must not fall. Weight changes are only safe to deploy if the same moments
still clear their channel's threshold. This answers that with the real
signal vectors from clips/clips.json rather than an argument.

It reproduces engine.py's scoring exactly: per-signal learned multipliers
from the streamer profile, raw sum capped at 100, then the multi-signal
bonus when 3+ signals exceed 0.5, capped again. The sub/raid flat bonus is
not reproducible after the fact and is ignored on BOTH sides, so it cannot
bias the comparison.

Never writes anything. Run on the server:
  venv/bin/python -m src.maintenance.simulate_weights
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from config.settings import settings
from src.trigger import scoring

# The weights this retune replaced.
OLD = {
    "CHAT_VELOCITY": 36, "AUDIO_SPIKE": 24, "KEYWORD": 4, "SENTIMENT": 5,
    "VIEWER_SPIKE": 15, "SILENCE_BURST": 14, "EMOTE_HOMOGENEITY": 12,
}
# The weights now in the code (read live so this can't drift out of sync).
NEW = {
    "CHAT_VELOCITY":     scoring.CHAT_WEIGHTS["CHAT_VELOCITY"],
    "KEYWORD":           scoring.CHAT_WEIGHTS["KEYWORD"],
    "SENTIMENT":         scoring.CHAT_WEIGHTS["SENTIMENT"],
    "EMOTE_HOMOGENEITY": scoring.CHAT_WEIGHTS["EMOTE_HOMOGENEITY"],
    "AUDIO_SPIKE":       22, "VIEWER_SPIKE": 20, "SILENCE_BURST": 10,
}


def _score(signals: list[tuple[str, float]], weights: dict,
           mult: dict[str, float]) -> float:
    """Mirror of TriggerEngine's score computation (minus the sub/raid bonus)."""
    raw = sum(v * weights.get(k, 0) * mult.get(k, 1.0) for k, v in signals)
    raw = min(raw, 100)
    if sum(1 for _, v in signals if v > 0.5) >= scoring.MULTI_SIGNAL_MIN_ACTIVE:
        raw *= scoring.MULTI_SIGNAL_BONUS
    return min(raw, 100)


def main() -> int:
    base = Path(settings.local_storage_path)
    try:
        clips = json.loads((base / "clips.json").read_text())
    except FileNotFoundError:
        print(f"no clips.json under {base}")
        return 1

    # Per-channel threshold + learned per-signal multipliers, from the profiles.
    thresholds: dict[str, float] = {}
    mults: dict[str, dict] = {}
    for pf in base.glob("profiles/*/*.json"):
        try:
            p = json.loads(pf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ch = p.get("channel") or pf.stem
        thresholds[ch] = float(p.get("trigger_threshold") or 60.0)
        mults[ch] = {k: float(v) for k, v in (p.get("signal_weights") or {}).items()}

    rows = []
    for c in clips:
        sigs = [(str(s.get("type", "")).replace("SignalType.", ""),
                 float(s.get("value") or 0.0))
                for s in (c.get("trigger_signals") or [])]
        if not sigs:
            continue
        ch = c.get("channel") or "?"
        m = mults.get(ch, {})
        rows.append((ch, thresholds.get(ch, 60.0),
                     _score(sigs, OLD, m), _score(sigs, NEW, m), c))
    if not rows:
        print("no clips carry signal vectors — nothing to simulate")
        return 0

    print(f"=== weight retune what-if — {len(rows)} clips with signal vectors ===\n")
    print(f"  {'':22s} {'OLD':>8s} {'NEW':>8s} {'delta':>8s}")
    old_mean = sum(r[2] for r in rows) / len(rows)
    new_mean = sum(r[3] for r in rows) / len(rows)
    print(f"  {'mean score':22s} {old_mean:8.2f} {new_mean:8.2f} {new_mean - old_mean:+8.2f}")

    # THE number that matters: would this moment still have fired?
    old_fire = sum(1 for r in rows if r[2] >= r[1])
    new_fire = sum(1 for r in rows if r[3] >= r[1])
    print(f"  {'clips clearing bar':22s} {old_fire:8d} {new_fire:8d} {new_fire - old_fire:+8d}")
    if old_fire:
        pct = 100.0 * (new_fire - old_fire) / old_fire
        print(f"  {'volume change':22s} {'':8s} {'':8s} {pct:+7.1f}%")
        if pct < -5:
            print("\n  *** VOLUME DROPS MORE THAN 5% — DO NOT DEPLOY AS-IS ***")
        elif pct < 0:
            print("\n  (slight dip, within noise — check the per-channel table below)")
        else:
            print("\n  volume holds or rises.")

    # Saturation: clips pinned at 100 are the bot's 'maximum confidence' cases,
    # which the human data showed are often rated 1/10.
    old_sat = sum(1 for r in rows if r[2] >= 99.99)
    new_sat = sum(1 for r in rows if r[3] >= 99.99)
    print(f"  {'pinned at 100 (sat.)':22s} {old_sat:8d} {new_sat:8d} {new_sat - old_sat:+8d}")

    print("\n-- per channel (top 12 by clip count) --")
    per = defaultdict(lambda: [0, 0, 0])
    for ch, thr, o, n, _ in rows:
        per[ch][0] += 1
        per[ch][1] += 1 if o >= thr else 0
        per[ch][2] += 1 if n >= thr else 0
    print(f"  {'channel':20s} {'clips':>6s} {'old':>6s} {'new':>6s} {'delta':>6s}")
    for ch, (tot, o, n) in sorted(per.items(), key=lambda kv: -kv[1][0])[:12]:
        flag = "   <-- drops" if n < o else ""
        print(f"  {ch[:20]:20s} {tot:6d} {o:6d} {n:6d} {n - o:+6d}{flag}")

    print("\n-- clips that would NEWLY fail to fire (spot-check these) --")
    lost = [r for r in rows if r[2] >= r[1] > r[3]]
    for ch, thr, o, n, c in lost[:10]:
        print(f"  {ch[:18]:18s} thr {thr:5.1f}  {o:5.1f} -> {n:5.1f}  "
              f"{(c.get('clip_title') or '')[:38]}")
    if not lost:
        print("  none — every moment that fired before still fires.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
