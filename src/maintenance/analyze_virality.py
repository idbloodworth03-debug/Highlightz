"""How accurate is virality_score? — read-only, run on the server.

    venv/bin/python -m src.maintenance.analyze_virality
    venv/bin/python -m src.maintenance.analyze_virality --exclude Bloodworthhh
    venv/bin/python -m src.maintenance.analyze_virality --exclude Bloodworthhh --csv /tmp/virality.csv

WHAT IT IS ASKING. virality_score is a formula with six hand-picked weights
(engine.py::_compute_virality_score). Nothing has ever checked whether the
number it produces ranks clips the way anything real does. This report scores
it against every independent judgement we hold:

  A. HUMANS, blind        human_scores.jsonl — a labeler rated virality 1-10
                          without seeing the bot's number. The most direct
                          answer, and the smallest sample.
  B. WHAT USERS KEPT      training_log.jsonl — approved / rejected / expired.
                          Bigger, noisier, and the one that reflects the
                          product actually being used.
  C. WHAT VIEWERS CLIPPED viewer_clips.jsonl — a stranger watching the stream
                          decided the moment was worth keeping. Unprompted and
                          free of our own influence.

WHY --exclude EXISTS. Approvals are only ground truth if the person approving
was judging clips rather than producing volume. The owner's own account spent a
period approving indiscriminately to move counters, so its outcomes are noise
with a label on them. Excluding an account is a statement about DATA QUALITY,
not a filter to make a number look better — every section prints its n before
and after so the cost of the exclusion is visible.

READ THE ORDER OF SECTIONS. Section 1 asks whether the score can discriminate
AT ALL. A score bunched into one band cannot rank anything, and no correlation
below it would be interpretable — if section 1 is flat, stop there and fix that
first.

VOD MOMENTS ARE EXCLUDED, AND THAT MATTERS MORE THAN IT SOUNDS. A live clip's
virality comes from the six-weight formula in engine.py. A VOD moment's comes
from vod/analyzer.py and is literally `trigger_score * 0.7` — a rescaled copy
of the trigger, not an independent estimate of anything. Mixed in, they would
drag every correlation toward whatever the TRIGGER predicts and make the
virality formula look like it tracks reality when what it is tracking is
itself. They are not marked in training_log.jsonl, so they are identified by
that exact 0.7 relationship and reported separately. --include-vod overrides.

CHANGING THESE WEIGHTS CANNOT CHANGE WHAT GETS CLIPPED. virality_score is
computed alongside the trigger score and then only carried and displayed —
nothing gates capture on it (verified: engine.py computes it, clip_processor
and stream_worker pass it through, the dashboard renders it, and no threshold
anywhere reads it). So unlike a trigger-weight change this one cannot cost
volume, and simulate_weights.py — which models clip volume — has nothing to
say about it. The risk here is only that the number shown to users, and the
sort built on it, become differently wrong.

Writes nothing. Never touches the live store.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from config.settings import settings
from src.maintenance.analyze_human_scores import _significant, spearman

STORE = Path(settings.local_storage_path)
HUMAN = STORE / "human_scores.jsonl"
TRAIN = STORE / "training_log.jsonl"
VIEWER = STORE / "viewer_clips.jsonl"
CLIPS = STORE / "clips.json"
USERS = STORE / "users.json"

# The live formula, so the report can compare itself to what is running rather
# than to a remembered version of it. Keep in sync with
# engine.py::_compute_virality_score.
CURRENT_VIRALITY_WEIGHTS = {
    "AUDIO_SPIKE":       28,
    "SILENCE_BURST":     20,
    "EMOTE_HOMOGENEITY": 18,
    "KEYWORD":            8,   # (keyword + sentiment) / 2 * 16
    "SENTIMENT":          8,
    "VIEWER_SPIKE":      12,
    "CHAT_VELOCITY":      6,
}
SIGNALS = list(CURRENT_VIRALITY_WEIGHTS)


# ── small stats, stdlib only (prod has no numpy) ─────────────────────────────

def mean(xs):
    return sum(xs) / len(xs) if xs else None


def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def stdev(xs):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Probability a random positive scores above a random negative.

    0.50 is a coin flip — the score carries no information about the outcome.
    Computed from ranks (Mann-Whitney), so ties count as half and it does not
    care about the scale or shape of the score, only its ordering.
    """
    if not pos or not neg:
        return None
    allv = pos + neg
    order = sorted(range(len(allv)), key=lambda i: allv[i])
    ranks = [0.0] * len(allv)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and allv[order[j + 1]] == allv[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rsum = sum(ranks[:len(pos)])
    n1, n2 = len(pos), len(neg)
    return (rsum - n1 * (n1 + 1) / 2) / (n1 * n2)


def _by(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def _fmt(v, nd=1, dash="—"):
    return dash if v is None else f"{v:.{nd}f}"


def _bar(frac, width=28):
    n = int(round(max(0.0, min(1.0, frac)) * width))
    return "█" * n + "·" * (width - n)


# ── loading ──────────────────────────────────────────────────────────────────

def _jsonl(path: Path, cutoff: float = 0) -> list[dict]:
    rows = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("ts", 0) >= cutoff:
                    rows.append(r)
    except FileNotFoundError:
        pass
    return rows


def _resolve_users(names: list[str]) -> tuple[set[str], list[str]]:
    """Usernames/ids -> user ids. Returns (ids, names that matched nothing)."""
    ids, missed = set(), []
    try:
        users = json.loads(USERS.read_text())
    except Exception:
        users = []
    for name in names:
        low = name.strip().lower()
        hit = [u for u in users
               if low in ((u.get("username") or "").lower(),
                          (u.get("twitch_login") or "").lower(),
                          (u.get("id") or "").lower())]
        if hit:
            ids.update(u["id"] for u in hit)
        else:
            missed.append(name)
    return ids, missed


def is_vod_shaped(row: dict) -> bool:
    """Does this record's virality look like vod/analyzer.py made it?

    A VOD moment sets virality_score = round(trigger_score * 0.7, 1) — so its
    virality carries no information the trigger does not already carry. Live
    clips satisfy that identity only by coincidence, and at 0.1 precision that
    coincidence is rare enough to be worth the trade: a handful of live clips
    dropped is cheaper than every correlation being quietly inflated by a
    rescaled copy of the trigger score.

    The clip store marks them (`is_vod_moment`), but training_log.jsonl does
    not record that field, so the identity is the only thing available on the
    dataset that matters most.
    """
    if row.get("is_vod_moment") or row.get("vod_id"):
        return True
    t, v = row.get("trigger_score"), row.get("virality_score")
    if not isinstance(t, (int, float)) or not isinstance(v, (int, float)):
        return False
    return abs(v - round(t * 0.7, 1)) < 0.05


def split_vod(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    live = [r for r in rows if not is_vod_shaped(r)]
    return live, [r for r in rows if is_vod_shaped(r)]


# ── 0. what is actually here ─────────────────────────────────────────────────

def section_data(human, train, viewer, clips, excluded_ids, missed):
    print("=" * 74)
    print("0. WHAT DATA EXISTS")
    print("=" * 74)
    for label, rows, path in (("human ratings   ", human, HUMAN),
                              ("clip outcomes   ", train, TRAIN),
                              ("viewer clips    ", viewer, VIEWER)):
        if not rows:
            print(f"  {label} : NONE at {path}")
            continue
        ts = [r.get("ts", 0) for r in rows if r.get("ts")]
        span = ""
        if ts:
            days = (max(ts) - min(ts)) / 86400
            span = (f"  spanning {days:.0f} days, newest "
                    f"{(time.time() - max(ts)) / 86400:.1f} days ago")
        print(f"  {label} : {len(rows):>6} records{span}")
    print(f"  live clips      : {len(clips):>6} in the store now")
    for label, rows in (("outcomes", train), ("stored clips", clips)):
        _, vod = split_vod(rows)
        if vod:
            print(f"  !! {len(vod)} of {len(rows)} {label} are VOD-shaped "
                  f"(virality == trigger x 0.7)")
    if any(is_vod_shaped(r) for r in list(train) + list(clips)):
        print("     Those are excluded: their virality is a rescaled trigger score,")
        print("     so including them measures the trigger, not this formula.")
        print("     --include-vod keeps them if you want to see the difference.")
    if excluded_ids:
        print(f"\n  excluding {len(excluded_ids)} account(s) from outcome data — see --exclude")
    if missed:
        print(f"  !! --exclude matched no account for: {', '.join(missed)}")
        print("     (check the spelling — an unmatched name silently excludes nobody)")
    print()


# ── 1. can the score discriminate at all? ────────────────────────────────────

def section_spread(clips, train):
    print("=" * 74)
    print("1. CAN virality_score SEPARATE ANYTHING?")
    print("=" * 74)
    print("A score that lands every clip in the same band cannot rank them, and")
    print("no correlation below is interpretable if this one is flat.\n")
    for label, vals in (("live clips", [c.get("virality_score") for c in clips]),
                        ("logged outcomes", [r.get("virality_score") for r in train])):
        vals = [float(v) for v in vals if isinstance(v, (int, float))]
        if len(vals) < 10:
            print(f"  {label}: only {len(vals)} scored clips — not enough to judge\n")
            continue
        s = sorted(vals)
        print(f"  {label}  n={len(vals)}")
        print(f"    mean {mean(vals):.1f}   median {median(vals):.1f}   sd {_fmt(stdev(vals))}")
        pct = lambda p: s[min(len(s) - 1, int(len(s) * p))]
        print(f"    p10 {pct(.10):.0f}   p25 {pct(.25):.0f}   p50 {pct(.50):.0f}   "
              f"p75 {pct(.75):.0f}   p90 {pct(.90):.0f}   max {s[-1]:.0f}")
        bands = defaultdict(int)
        for v in vals:
            bands[min(9, int(v // 10))] += 1
        print("    distribution:")
        for b in range(10):
            n = bands[b]
            print(f"      {b*10:>3}-{b*10+9:<3} {_bar(n / len(vals))} {n:>5}"
                  f"  {100*n/len(vals):>5.1f}%")
        at_zero = sum(1 for v in vals if v == 0)
        if at_zero:
            print(f"    NOTE {at_zero} clips ({100*at_zero/len(vals):.1f}%) score exactly 0 — "
                  f"check whether those are real or missing signals")
        print()


# ── 2. against blind human ratings ───────────────────────────────────────────

def section_humans(human, exclude_labelers):
    print("=" * 74)
    print("2. AGAINST BLIND HUMAN RATINGS  (the most direct answer)")
    print("=" * 74)
    rows = [r for r in human
            if (r.get("human") or {}).get("virality") is not None
            and isinstance(r.get("bot_virality_score"), (int, float))]
    if exclude_labelers:
        before = len(rows)
        rows = [r for r in rows if r.get("labeler_id") not in exclude_labelers]
        print(f"  excluded labelers: {before} -> {len(rows)} ratings")
    if len(rows) < 20:
        print(f"  only {len(rows)} paired ratings — score more clips in the Training")
        print("  Studio before drawing any conclusion from this section.\n")
        return rows
    hv = [float(r["human"]["virality"]) for r in rows]
    bv = [float(r["bot_virality_score"]) for r in rows]

    print(f"  n = {len(rows)} paired ratings, "
          f"{len({r.get('labeler_id') for r in rows})} labeler(s), "
          f"{len({r.get('channel') for r in rows})} channel(s)")
    spread = len({int(h) for h in hv})
    print(f"  humans used {spread} of 10 rating values, mean {mean(hv):.2f}, "
          f"sd {_fmt(stdev(hv), 2)}")
    if spread <= 3 or (stdev(hv) or 0) < 0.8:
        print("  !! the HUMANS barely discriminate here. Correlating against a")
        print("     near-constant rating measures nothing — fix the labelling first.")

    r_v = spearman(bv, hv)
    print(f"\n  virality_score vs human virality : {_fmt(r_v, 3, '—'):>7}"
          f"   {'(significant)' if _significant(r_v, len(rows)) else '(NOT significant)'}"
          f"   [pooled]")

    # WITHIN each labeler, then combined. Pooling ranks across raters who use
    # different parts of the scale mixes "this clip beat that clip" with "this
    # rater is harsher than that one", and the second is not a fact about the
    # bot. With means from 1.7 to 4.2 in this dataset that is not a rounding
    # concern — it is most of the variance.
    per_lab, weights = [], []
    for lid, group in _by(rows, lambda r: r.get("labeler_id")).items():
        if len(group) < 30:
            continue
        rr = spearman([float(r["bot_virality_score"]) for r in group],
                      [float(r["human"]["virality"]) for r in group])
        if rr is not None:
            per_lab.append((group[0].get("labeler") or lid, rr, len(group)))
            weights.append(len(group))
    if per_lab:
        combined = sum(r * n for _, r, n in per_lab) / sum(weights)
        print(f"  same, computed WITHIN each labeler : {combined:+.3f}"
              f"   [the one to believe]")
        for name, rr, n in sorted(per_lab, key=lambda x: -x[2]):
            flag = "" if _significant(rr, n) else "  (not significant)"
            print(f"      {str(name)[:22]:<22} n={n:<5} {rr:+.3f}{flag}")
    tr = [(float(r["bot_trigger_score"]), float(r["human"]["virality"])) for r in rows
          if isinstance(r.get("bot_trigger_score"), (int, float))]
    if len(tr) >= 20:
        r_t = spearman([a for a, _ in tr], [b for _, b in tr])
        print(f"  trigger_score  vs human virality : {_fmt(r_t, 3, '—'):>7}"
              f"   (baseline — is virality even better?)")
        if r_v is not None and r_t is not None and r_v <= r_t:
            print("  !! the dedicated virality formula does NOT beat the trigger score.")
            print("     Six extra weights are buying nothing over the number we already had.")

    print("\n  where it goes wrong — bot band vs what humans said:")
    buckets = defaultdict(list)
    for b, h in zip(bv, hv):
        buckets[min(4, int(b // 20))].append(h)
    print(f"    {'bot band':<10} {'n':>5}  {'mean human':>10}   spread")
    for k in range(5):
        vals = buckets[k]
        if not vals:
            continue
        print(f"    {k*20:>3}-{k*20+19:<6} {len(vals):>5}  {mean(vals):>10.2f}   "
              f"{_bar(mean(vals) / 10, 20)}")
    print("    (a working score climbs down this column; a flat column means the")
    print("     bands are interchangeable)")

    k = max(5, len(rows) // 10)
    top = [h for _, h in sorted(zip(bv, hv), reverse=True)[:k]]
    bot = [h for _, h in sorted(zip(bv, hv))[:k]]
    print(f"\n  bot's top {k} clips    : humans averaged {mean(top):.2f}/10")
    print(f"  bot's bottom {k} clips : humans averaged {mean(bot):.2f}/10")
    gap = (mean(top) or 0) - (mean(bot) or 0)
    print(f"  separation            : {gap:+.2f} points"
          f"{'  — effectively none' if abs(gap) < 0.7 else ''}")

    by_lab = defaultdict(list)
    for r in rows:
        by_lab[r.get("labeler") or r.get("labeler_id")].append(float(r["human"]["virality"]))
    if len(by_lab) > 1:
        print("\n  per labeler (a rater who parks the slider drags every correlation):")
        for name, vals in sorted(by_lab.items(), key=lambda kv: -len(kv[1])):
            print(f"    {str(name)[:22]:<22} n={len(vals):<5} mean {mean(vals):.2f}"
                  f"  sd {_fmt(stdev(vals), 2)}")

    # DO THE HUMANS EVEN AGREE WITH EACH OTHER?
    #
    # This is the ceiling on every number above it. "Correlates with human
    # virality" presumes there IS a stable human view to correlate with. If two
    # people watching the same clip do not rank it the same way, then no
    # formula can score well against the average of them, and a weak
    # correlation is evidence about the TARGET, not about the bot. Nothing else
    # in this report can be read properly without it.
    shared = {cid: g for cid, g in _by(rows, lambda r: r.get("clip_id")).items()
              if len({x.get("labeler_id") for x in g}) > 1}
    print(f"\n  clips rated by more than one person: {len(shared)}")
    if len(shared) >= 20:
        a_side, b_side, gaps = [], [], []
        for g in shared.values():
            seen, picked = set(), []
            for r in g:
                if r.get("labeler_id") not in seen:
                    seen.add(r.get("labeler_id"))
                    picked.append(float(r["human"]["virality"]))
                if len(picked) == 2:
                    break
            a_side.append(picked[0])
            b_side.append(picked[1])
            gaps.append(abs(picked[0] - picked[1]))
        agree = spearman(a_side, b_side)
        print(f"  rater-to-rater agreement          : {_fmt(agree, 3, '—'):>7}"
              f"   (n={len(shared)})")
        print(f"  median gap between two ratings    : {median(gaps):.1f} points of 10")
        if agree is not None and agree < 0.3:
            print("\n  !! THE HUMANS DO NOT AGREE WITH EACH OTHER. That caps everything")
            print("     above: there is no stable 'human virality' for a formula to")
            print("     match, so a flat correlation is a fact about the TARGET as")
            print("     much as about the bot. Fix the rating task — clearer")
            print("     instructions, or a forced comparison of two clips instead of")
            print("     an absolute 1-10 — before fitting any weights to this.")
    else:
        print("  too few doubly-rated clips to tell whether the humans agree —")
        print("  and without that, no correlation above can be read properly.")
    print()
    return rows


# ── 3. against what other people kept ────────────────────────────────────────

def section_outcomes(train, excluded_ids):
    print("=" * 74)
    print("3. AGAINST WHAT USERS ACTUALLY KEPT")
    print("=" * 74)
    before = len(train)
    rows = [r for r in train if r.get("user_id") not in excluded_ids]
    scored = [r for r in rows if isinstance(r.get("virality_score"), (int, float))]
    print(f"  {before} outcomes -> {len(rows)} after exclusions -> "
          f"{len(scored)} carry a virality score")
    if len(scored) < 30:
        print("  not enough to judge.\n")
        return scored

    by_label = defaultdict(list)
    for r in scored:
        by_label[r.get("label")].append(float(r["virality_score"]))
    print(f"\n  {'outcome':<20} {'n':>6} {'mean':>7} {'median':>7}")
    for label in ("approved", "rejected", "expired_unreviewed"):
        v = by_label.get(label) or []
        if v:
            print(f"  {label:<20} {len(v):>6} {mean(v):>7.1f} {median(v):>7.1f}")

    pos = by_label.get("approved") or []
    neg = (by_label.get("rejected") or []) + (by_label.get("expired_unreviewed") or [])
    a = auc(pos, neg)
    print(f"\n  AUC, virality_score -> approved : {_fmt(a, 3, '—'):>7}")
    print("    0.50 = the score tells you nothing about whether a clip gets kept.")
    print("    0.60 = weak but real.   0.70+ = genuinely useful.")
    if a is not None:
        if a < 0.55:
            print("    VERDICT: no better than a coin flip on this data.")
        elif a < 0.62:
            print("    VERDICT: a real but weak signal.")
        else:
            print("    VERDICT: carrying useful information.")

    tpos = [float(r["trigger_score"]) for r in scored
            if r.get("label") == "approved" and isinstance(r.get("trigger_score"), (int, float))]
    tneg = [float(r["trigger_score"]) for r in scored
            if r.get("label") != "approved" and isinstance(r.get("trigger_score"), (int, float))]
    ta = auc(tpos, tneg)
    if ta is not None:
        print(f"  AUC, trigger_score  -> approved : {_fmt(ta, 3, '—'):>7}   (baseline)")
        if a is not None and a <= ta:
            print("    !! virality is no better than the trigger score at predicting keeps.")

    by_user = defaultdict(lambda: defaultdict(list))
    for r in scored:
        by_user[r.get("user_id")][r.get("label")].append(float(r["virality_score"]))
    if len(by_user) > 1:
        print("\n  BUT THE POOLED NUMBER ABOVE MIXES ACCOUNTS. Different users")
        print("  approve at wildly different rates AND clip different channels,")
        print("  so pooling can manufacture separation that no single user's")
        print("  behaviour shows. Per account is the honest read:")
        print(f"    {'user':<26} {'kept':>5} {'not':>5} {'AUC':>7}")
        good = []
        for uid, labels in sorted(by_user.items(), key=lambda kv: -sum(len(v) for v in kv[1].values())):
            p = labels.get("approved") or []
            n = (labels.get("rejected") or []) + (labels.get("expired_unreviewed") or [])
            if len(p) + len(n) < 15:
                continue
            a_u = auc(p, n)
            note = ""
            if a_u is not None and len(p) >= 10:
                good.append((a_u, len(p)))
            elif a_u is not None:
                note = "   (too few keeps to trust)"
            print(f"    {str(uid)[:26]:<26} {len(p):>5} {len(n):>5} "
                  f"{_fmt(a_u, 3, '—'):>7}{note}")
        if good:
            w = sum(n for _, n in good)
            combined = sum(a_u * n for a_u, n in good) / w
            print(f"\n    weighted across accounts with >=10 keeps : {combined:.3f}")
            if a is not None and combined < a - 0.03:
                print(f"    The pooled {a:.3f} is inflated by between-account differences.")
                print(f"    {combined:.3f} is what the score does for an individual user.")
    print()
    return scored


# ── 4. against what strangers clipped ────────────────────────────────────────

def section_viewers(viewer, clips):
    print("=" * 74)
    print("4. AGAINST WHAT REAL VIEWERS CLIPPED")
    print("=" * 74)
    print("Unprompted ground truth: somebody watching the stream decided the")
    print("moment was worth keeping. RECALL ONLY — this says nothing about the")
    print("clips we took that no viewer clipped.\n")
    if not viewer:
        print("  no viewer_clips.jsonl yet.\n")
        return
    matched = [r for r in viewer
               if isinstance(r.get("our_peak"), (int, float))
               or isinstance(r.get("our_score"), (int, float))]
    print(f"  {len(viewer)} viewer clips logged, {len(matched)} with a score of ours")
    views = [(float(r.get("view_count") or 0), r) for r in viewer
             if r.get("view_count") is not None]
    if len(views) >= 20:
        vc = [v for v, _ in views]
        print(f"  view counts: median {median(vc):.0f}, max {max(vc):.0f}")
    have_virality = [r for r in viewer
                     if isinstance(r.get("our_virality_peak"), (int, float))]
    if have_virality:
        vv = [float(r["our_virality_peak"]) for r in have_virality]
        allv = [float(c["virality_score"]) for c in clips
                if isinstance(c.get("virality_score"), (int, float))]
        print(f"\n  moments a stranger clipped : n={len(vv)}, "
              f"our virality averaged {mean(vv):.1f}")
        if allv:
            print(f"  every clip we captured     : n={len(allv)}, "
                  f"averaged {mean(allv):.1f}")
            print(f"  difference                 : {(mean(vv) - mean(allv)):+.1f}")
            print("  (a working score rates the ones strangers wanted HIGHER)")
        return

    # THIS IS A SCHEMA GAP, NOT A DATA VOLUME PROBLEM, and saying "not enough
    # overlap" hid that. The engine's _score_history holds the TRIGGER score
    # only, so `our_peak` on every one of these records is a trigger score.
    # There has never been a virality number attached to a viewer clip, so no
    # amount of waiting would have made this section work.
    #
    # `clip_id` here is Twitch's slug for the VIEWER'S OWN clip. It is not our
    # clip id and can never join against clips.json — matching on it was always
    # going to return nothing.
    print(f"\n  {len(matched)} of these carry a score of ours — but it is the")
    print("  TRIGGER score. The engine only keeps trigger history, so no virality")
    print("  number has ever been attached to a viewer clip. This is a schema gap,")
    print("  not a shortage of data: the biggest, most honest dataset we have")
    print("  (~79k unprompted human judgements) cannot speak to this formula.")
    print("\n  Once the engine records virality alongside the trigger, this")
    print("  section starts answering the question within days at this volume.")
    print("  (analyze_viewer_clips.py already benchmarks the TRIGGER against it.)")
    print()


# ── 5. which signals actually predict virality ───────────────────────────────

def section_weights(human_rows, outcome_rows):
    print("=" * 74)
    print("5. WHICH SIGNALS PREDICT VIRALITY — AND WHAT THE WEIGHTS SHOULD BE")
    print("=" * 74)
    print("Correlation of each raw signal against each ground truth. The current")
    print("weights were hand-picked; this is what the data says about them.\n")

    corr_h, corr_o = {}, {}
    for sig in SIGNALS:
        pairs = [(float((r.get("bot_signals") or {}).get(sig)),
                  float(r["human"]["virality"]))
                 for r in human_rows
                 if (r.get("bot_signals") or {}).get(sig) is not None]
        if len(pairs) >= 20:
            corr_h[sig] = spearman([a for a, _ in pairs], [b for _, b in pairs])
        opairs = [(float((r.get("signals") or {}).get(sig)),
                   1.0 if r.get("label") == "approved" else 0.0)
                  for r in outcome_rows
                  if (r.get("signals") or {}).get(sig) is not None]
        if len(opairs) >= 30:
            corr_o[sig] = spearman([a for a, _ in opairs], [b for _, b in opairs])

    print(f"  {'signal':<20} {'weight now':>10} {'vs humans':>11} {'vs kept':>10}")
    for sig in sorted(SIGNALS, key=lambda s: -CURRENT_VIRALITY_WEIGHTS[s]):
        print(f"  {sig:<20} {CURRENT_VIRALITY_WEIGHTS[sig]:>10} "
              f"{_fmt(corr_h.get(sig), 3, '—'):>11} {_fmt(corr_o.get(sig), 3, '—'):>10}")

    usable = {s: v for s, v in corr_h.items() if v is not None and v > 0}
    if len(usable) >= 3:
        total = sum(usable.values())
        pool = sum(CURRENT_VIRALITY_WEIGHTS.values())
        print(f"\n  If the weights simply followed the human correlations "
              f"(same {pool}-point pool):")
        print(f"    {'signal':<20} {'now':>5} {'proposed':>9} {'move':>7}")
        for sig in sorted(usable, key=lambda s: -usable[s]):
            prop = round(pool * usable[sig] / total)
            now = CURRENT_VIRALITY_WEIGHTS[sig]
            print(f"    {sig:<20} {now:>5} {prop:>9} {prop - now:>+7}")
        dropped = [s for s in SIGNALS if s not in usable]
        if dropped:
            print(f"    zero or negative against humans: {', '.join(dropped)}")
        print("\n  THIS IS A HYPOTHESIS, NOT A PATCH. It is fitted on the same")
        print("  data it would be judged by, and the correlations above may not")
        print("  be significant — check the n and the flags before believing a")
        print("  row. Re-run on held-out data (--days) before acting on it.")
        print("\n  What it CANNOT break: nothing gates a clip on virality_score,")
        print("  so changing these weights cannot cost clip volume. That is a")
        print("  trigger-weight risk and simulate_weights.py is the tool for it;")
        print("  it has nothing to say about this formula. What it CAN break is")
        print("  the number users see and the Virality sort built on it.")
    else:
        print("\n  not enough signal-level data to propose weights yet.")
    print()


# ── 6. the ceiling ───────────────────────────────────────────────────────────

def _solve(A, b, ridge=1e-3):
    """Least squares by Gaussian elimination on the normal equations.

    Ridge term because the signals are correlated with each other; without it a
    near-singular matrix produces enormous weights that fit the sample and
    predict nothing.
    """
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        M[i][i] += ridge
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-12:
            return None
        M[i], M[piv] = M[piv], M[i]
        for r in range(i + 1, n):
            f = M[r][i] / M[i][i]
            for c in range(i, n + 1):
                M[r][c] -= f * M[i][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][c] * x[c] for c in range(i + 1, n))) / M[i][i]
    return x


def _fit(rows, get_sig, get_target):
    """Best linear combination of the signals for this target. Returns weights."""
    X = [[float(get_sig(r).get(sg, 0.0)) for sg in SIGNALS] for r in rows]
    y = [float(get_target(r)) for r in rows]
    k = len(SIGNALS)
    A = [[sum(X[i][a] * X[i][b] for i in range(len(X))) for b in range(k)]
         for a in range(k)]
    v = [sum(X[i][a] * y[i] for i in range(len(X))) for a in range(k)]
    return _solve(A, v)


def _apply(w, sig):
    return sum(w[i] * float(sig.get(sg, 0.0)) for i, sg in enumerate(SIGNALS))


def section_ceiling(human_rows, outcome_rows):
    print("=" * 74)
    print("6. THE CEILING — IS THERE ANY SET OF WEIGHTS THAT WORKS?")
    print("=" * 74)
    print("Section 5 proposes weights. This asks the prior question: fit the BEST")
    print("possible linear combination of these signals on half the data, then")
    print("score the half it has never seen. If even that cannot beat the")
    print("current formula by much, the problem is the SIGNALS, not the weights,")
    print("and no reweighting will fix it.\n")

    for label, rows, get_sig, get_target in (
        ("human rating", [r for r in human_rows if r.get("bot_signals")],
         lambda r: r.get("bot_signals") or {},
         lambda r: r["human"]["virality"]),
        ("kept vs not", [r for r in outcome_rows if r.get("signals")],
         lambda r: r.get("signals") or {},
         lambda r: 1.0 if r.get("label") == "approved" else 0.0),
    ):
        if len(rows) < 200:
            print(f"  {label}: only {len(rows)} rows — need 200+ to split.\n")
            continue
        half = len(rows) // 2
        train_r, test_r = rows[:half], rows[half:]
        w = _fit(train_r, get_sig, get_target)
        if w is None:
            print(f"  {label}: signals are collinear, no stable fit.\n")
            continue
        fitted = [_apply(w, get_sig(r)) for r in test_r]
        actual = [float(get_target(r)) for r in test_r]
        r_out = spearman(fitted, actual)
        cur = [float(r.get("bot_virality_score") or r.get("virality_score") or 0)
               for r in test_r]
        r_cur = spearman(cur, actual)
        print(f"  vs {label}   (fitted on {len(train_r)}, scored on {len(test_r)} unseen)")
        print(f"    best fitted weights : {_fmt(r_out, 3, '—'):>7}")
        print(f"    formula running now : {_fmt(r_cur, 3, '—'):>7}")
        if r_out is None:
            print("    -> the fitted score could not be ranked either.")
        elif abs(r_out) < 0.10:
            print("    -> Even the BEST possible weighting of these signals is")
            print("       near zero on data it has not seen. Reweighting cannot")
            print("       fix this. The signals do not carry the answer.")
        elif r_cur is None:
            # A constant or missing current score has no rank order, so
            # comparing against it returns None. Reading that as "no
            # improvement" is exactly backwards — an unrankable score is the
            # easiest thing in the world to beat.
            print(f"    -> the formula running now cannot be ranked here at all")
            print(f"       (constant or missing), so {r_out:+.3f} is pure gain.")
        elif r_out > r_cur + 0.05:
            print(f"    -> A reweight is worth {r_out - r_cur:+.3f} here. Real, and")
            print("       the size of the prize is now known rather than hoped for.")
        else:
            print("    -> No better than what is already deployed.")
        print()


def section_channels(human_rows, outcome_rows):
    print("=" * 74)
    print("7. DOES IT WORK ANYWHERE? — per channel")
    print("=" * 74)
    print("An average of zero can hide a score that works on loud channels and")
    print("inverts on quiet ones. If so, the fix is per-channel, not global.\n")
    shown = 0
    print(f"  {'channel':<24} {'n':>5} {'vs human':>9}")
    for ch, g in sorted(_by(human_rows, lambda r: r.get("channel")).items(),
                        key=lambda kv: -len(kv[1])):
        g = [r for r in g if isinstance(r.get("bot_virality_score"), (int, float))]
        if len(g) < 40:
            continue
        rr = spearman([float(r["bot_virality_score"]) for r in g],
                      [float(r["human"]["virality"]) for r in g])
        flag = "" if _significant(rr, len(g)) else "  (ns)"
        print(f"  {str(ch)[:24]:<24} {len(g):>5} {_fmt(rr, 3, '—'):>9}{flag}")
        shown += 1
    if not shown:
        print("  no channel has 40+ rated clips yet.")
    print()


# ── csv ──────────────────────────────────────────────────────────────────────

def dump_csv(path: Path, human_rows, outcome_rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "clip_id", "channel", "who", "label_or_rating",
                    "bot_virality", "bot_trigger"] + SIGNALS)
        for r in human_rows:
            sig = r.get("bot_signals") or {}
            w.writerow(["human", r.get("clip_id"), r.get("channel"),
                        r.get("labeler"), (r.get("human") or {}).get("virality"),
                        r.get("bot_virality_score"), r.get("bot_trigger_score")]
                       + [sig.get(s, "") for s in SIGNALS])
        for r in outcome_rows:
            sig = r.get("signals") or {}
            w.writerow(["outcome", r.get("clip_id"), r.get("channel"),
                        r.get("user_id"), r.get("label"),
                        r.get("virality_score"), r.get("trigger_score")]
                       + [sig.get(s, "") for s in SIGNALS])
    print(f"wrote {path}  — send this back for deeper analysis\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exclude", action="append", default=[],
                    help="username or user id whose OUTCOMES to ignore (repeatable)")
    ap.add_argument("--exclude-labeler", action="append", default=[],
                    help="also ignore this account's training ratings")
    ap.add_argument("--include-vod", action="store_true",
                    help="keep VOD moments in, whose virality is trigger*0.7 by "
                         "construction and will flatter the formula")
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--csv", type=Path, default=None)
    a = ap.parse_args()

    cutoff = time.time() - a.days * 86400 if a.days else 0
    human = _jsonl(HUMAN, cutoff)
    train_all = _jsonl(TRAIN, cutoff)
    viewer = _jsonl(VIEWER, cutoff)
    try:
        clips_all = list(json.loads(CLIPS.read_text()))
    except Exception:
        clips_all = []
    train, clips = train_all, clips_all

    if not a.include_vod:
        train = [r for r in train if not is_vod_shaped(r)]
        clips = [c for c in clips if not is_vod_shaped(c)]

    excluded, missed = _resolve_users(a.exclude)
    ex_lab, missed_lab = _resolve_users(a.exclude_labeler)

    scope = f"last {a.days:g} days" if a.days else "all time"
    print(f"\nVIRALITY SCORE ACCURACY — {scope}\n")
    # Section 0 reports on everything, including what the next line drops.
    section_data(human, train_all, viewer, clips_all, excluded, missed + missed_lab)
    section_spread(clips, train)
    human_rows = section_humans(human, ex_lab)
    outcome_rows = section_outcomes(train, excluded)
    section_viewers(viewer, clips)
    section_weights(human_rows, outcome_rows)
    section_ceiling(human_rows, outcome_rows)
    section_channels(human_rows, outcome_rows)

    if a.csv:
        dump_csv(a.csv, human_rows, outcome_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
