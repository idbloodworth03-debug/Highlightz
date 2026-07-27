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
# Candidate weight sets, all summing to the same 110 pool. Volume is NOT
# guaranteed by an equal pool alone: a channel whose clips lean on the signals
# being cut loses points that a rarely-present signal (VIEWER_SPIKE) cannot
# give back. That is exactly what the first attempt hit — one channel
# (yaboyyywill) absorbed the entire -6.6%. So we evaluate several strengths of
# the same lean and pick the strongest one that holds volume.
CANDIDATES = {
    # name:            CHAT AUDIO VIEW SILENCE EMOTE SENT KEY
    "A first try":     (36,  22,   20,  10,     11,   5,   6),
    "B gentle":        (36,  23,   18,  12,     11,   5,   5),
    "C SHIPPED":       (38,  22,   19,  11,     10,   5,   5),
    "D minimal":       (36,  23,   17,  13,     11,   5,   5),
}
_ORDER = ("CHAT_VELOCITY", "AUDIO_SPIKE", "VIEWER_SPIKE", "SILENCE_BURST",
          "EMOTE_HOMOGENEITY", "SENTIMENT", "KEYWORD")


def _wset(t: tuple) -> dict:
    return dict(zip(_ORDER, t))


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
        rows.append((ch, thresholds.get(ch, 60.0), sigs, mults.get(ch, {}), c))
    if not rows:
        print("no clips carry signal vectors — nothing to simulate")
        return 0

    print(f"=== weight what-if — {len(rows)} clips with signal vectors ===\n")
    base_scores = [_score(sg, OLD, m) for _, _, sg, m, _ in rows]
    base_fire = sum(1 for (ch, thr, _, _, _), sc in zip(rows, base_scores) if sc >= thr)
    print(f"baseline (weights before this retune): {base_fire} clips clear their bar, "
          f"mean score {sum(base_scores)/len(base_scores):.2f}\n")

    print(f"  {'candidate':14s} {'mean':>7s} {'fires':>6s} {'delta':>7s} {'volume':>8s}   verdict")
    results = {}
    for name, tup in CANDIDATES.items():
        w = _wset(tup)
        assert sum(w.values()) == 110, f"{name} pool != 110"
        sc = [_score(sg, w, m) for _, _, sg, m, _ in rows]
        fire = sum(1 for (ch, thr, _, _, _), v in zip(rows, sc) if v >= thr)
        pct = 100.0 * (fire - base_fire) / base_fire if base_fire else 0.0
        results[name] = (fire, pct, w)
        verdict = ("OK" if pct >= -1.0 else
                   "acceptable" if pct >= -3.0 else
                   "TOO LOSSY")
        print(f"  {name:14s} {sum(sc)/len(sc):7.2f} {fire:6d} {fire-base_fire:+7d} "
              f"{pct:+7.1f}%   {verdict}")

    # Pick the strongest lean that still holds volume (>= -1%).
    safe = [(n, r) for n, r in results.items() if r[1] >= -1.0]
    print()
    if safe:
        # "Strongest lean" = furthest from OLD on the two evidence-backed moves.
        best = max(safe, key=lambda kv: kv[1][2]["VIEWER_SPIKE"] - kv[1][2]["SILENCE_BURST"])
        print(f"  RECOMMEND: {best[0]}  (volume {best[1][1]:+.1f}%, "
              f"VIEWER_SPIKE {best[1][2]['VIEWER_SPIKE']}, SILENCE_BURST {best[1][2]['SILENCE_BURST']})")
    else:
        print("  NONE hold volume — the lean must be softened further.")

    # Per-channel for every candidate: shows whether losses concentrate again.
    print("\n-- per-channel fires (top 10 channels by clip count) --")
    per_tot = defaultdict(int)
    for ch, _, _, _, _ in rows:
        per_tot[ch] += 1
    tops = [c for c, _ in sorted(per_tot.items(), key=lambda kv: -kv[1])[:10]]
    hdr = "  " + f"{'channel':18s} {'clips':>6s} {'base':>6s}" + "".join(
        f" {n.split()[0]:>6s}" for n in CANDIDATES)
    print(hdr)
    for ch in tops:
        idx = [i for i, r in enumerate(rows) if r[0] == ch]
        b = sum(1 for i in idx if base_scores[i] >= rows[i][1])
        cells = ""
        for name, tup in CANDIDATES.items():
            w = _wset(tup)
            f = sum(1 for i in idx if _score(rows[i][2], w, rows[i][3]) >= rows[i][1])
            cells += f" {f:6d}"
        print(f"  {ch[:18]:18s} {len(idx):6d} {b:6d}{cells}")
    print("\n  (sub/raid +15 flat bonus is not reproducible after the fact and is")
    print("   ignored on every side, so it cannot bias the comparison.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
