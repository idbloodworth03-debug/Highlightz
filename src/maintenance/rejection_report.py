"""
Are more clips being rejected, since when, by whom, and which kind?

    venv/bin/python -m src.maintenance.rejection_report [--days 7] [--before 14]

Read-only. Owner, 2026-10-22: "we are getting way too many clips rejected
recently". Before changing the detector, this says whether that is true and
where it comes from, from the two records that hold every verdict:

  clips/stream_stats.jsonl   one row per outcome (caught, approved, rejected,
                             cleared, expired, undone). Rejecting deletes the
                             clip record, so this is the only complete count.
  clips/training_log.jsonl   approved/rejected/aged-out clips WITH their
                             platform, trigger score and signal vector.

Sections, each comparing the last --days against the --before days ahead of
them:
  1. per day: caught, kept, rejected, cleared, aged out, and the keep rate of
     what was actually judged (kept / (kept + rejected)). A queue cleared
     with Clear queue is not a rejection and is counted apart.
  2. who: the channels with the most rejections, with each one's keep rate
     before and after, and how soon after capture the rejects came. Seconds
     means a clear-out; hours means a verdict.
  3. what: keep rate by platform, the trigger score of kept vs rejected clips,
     which signal led them, and how many had no audio signal at all (a dead
     audio meter, as on 2026-10-22, or silence).
For the per-signal separation, run analyze_training_log over the same window.
"""

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings

_ROOT = Path(settings.local_storage_path)
_EVENTS = ("caught", "approved", "rejected", "cleared", "expired", "undone")


def _rows(path: Path, since: float):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if float(r.get("ts") or 0) >= since:
                yield r


def _keep(c: Counter) -> str:
    judged = c["approved"] + c["rejected"]
    return f"{round(100 * c['approved'] / judged):3d}%" if judged else "   -"


def _names() -> dict:
    try:
        from src.auth import users as user_store
        return {u.get("id"): u.get("username") or u.get("id") for u in user_store._load()}
    except Exception:
        return {}


def report(days: float = 7, before: float = 14, now: float | None = None, out=print) -> None:
    now = time.time() if now is None else now
    cut = now - days * 86400
    start = cut - before * 86400
    ledger = list(_rows(_ROOT / "stream_stats.jsonl", start))
    training = list(_rows(_ROOT / "training_log.jsonl", start))
    names = _names()

    out(f"== 1. per day, UTC (last {days:g} days marked *) ==")
    out("   day     caught   kept  rejected  cleared  aged out  undone   keep rate")
    by_day: dict = defaultdict(Counter)        # date -> counts; dates sort across a new year
    for r in ledger:
        if r.get("event") in _EVENTS:
            by_day[datetime.fromtimestamp(r["ts"], timezone.utc).date()][r["event"]] += 1
    cut_day = datetime.fromtimestamp(cut, timezone.utc).date()
    for d in sorted(by_day):
        c = by_day[d]
        out(f" {'*' if d >= cut_day else ' '} {d:%m-%d}  {c['caught']:7d} {c['approved']:6d} {c['rejected']:9d} "
            f"{c['cleared']:8d} {c['expired']:9d} {c['undone']:7d}   {_keep(c)}")
    recent, earlier = Counter(), Counter()
    for r in ledger:
        (recent if r["ts"] >= cut else earlier)[r.get("event")] += 1
    out(f"   last {days:g} days: keep {_keep(recent).strip()} of {recent['approved'] + recent['rejected']} judged, "
        f"{recent['rejected']} rejected;  the {before:g} days before: keep {_keep(earlier).strip()} of "
        f"{earlier['approved'] + earlier['rejected']} judged, {earlier['rejected']} rejected")

    out(f"\n== 2. who: most rejections in the last {days:g} days ==")
    out("   account            channel               rejected  kept   keep now  keep before   median capture->reject")
    now_c: dict[tuple, Counter] = defaultdict(Counter)
    then_c: dict[tuple, Counter] = defaultdict(Counter)
    delay: dict[tuple, list] = defaultdict(list)
    for r in ledger:
        key = (r.get("user_id"), str(r.get("channel") or "").lower())
        (now_c if r["ts"] >= cut else then_c)[key][r.get("event")] += 1
        if r["ts"] >= cut and r.get("event") == "rejected" and r.get("clip_at"):
            delay[key].append(max(0.0, r["ts"] - float(r["clip_at"])))
    for key in sorted(now_c, key=lambda k: -now_c[k]["rejected"])[:15]:
        c = now_c[key]
        if not c["rejected"]:
            break
        d = statistics.median(delay[key]) if delay[key] else None
        when = ("-" if d is None else f"{d / 60:.0f} min" if d < 7200 else f"{d / 3600:.1f} h")
        out(f"   {str(names.get(key[0], key[0]))[:18]:18} {key[1][:20]:20} {c['rejected']:9d} {c['approved']:5d} "
            f"   {_keep(c)}      {_keep(then_c.get(key, Counter()))}        {when}")

    out(f"\n== 3. what: clips with a signal record (training log) ==")
    by: dict[str, dict[str, Counter]] = {"recent": defaultdict(Counter), "before": defaultdict(Counter)}
    score: dict[str, list] = defaultdict(list)
    lead: dict[str, Counter] = {"approved": Counter(), "rejected": Counter()}
    silent = Counter()
    for r in training:
        label = r.get("label")
        if label not in ("approved", "rejected"):
            continue
        span = "recent" if float(r.get("ts") or 0) >= cut else "before"
        by[span][r.get("platform") or "?"][label] += 1
        if span != "recent":
            continue
        if r.get("trigger_score") is not None:
            score[label].append(float(r["trigger_score"]))
        sigs = r.get("signals") or {}
        if sigs:
            lead[label][max(sigs.items(), key=lambda kv: kv[1])[0]] += 1
        if float(sigs.get("AUDIO_SPIKE", 0) or 0) == 0:
            silent[label] += 1
    for plat in sorted(set(by["recent"]) | set(by["before"])):
        out(f"   {plat:7} keep now {_keep(by['recent'][plat])} of {sum(by['recent'][plat].values()):4d} judged;"
            f"  before {_keep(by['before'][plat])} of {sum(by['before'][plat].values()):4d}")
    for label in ("approved", "rejected"):
        s = score[label]
        n = sum(lead[label].values())
        top = ", ".join(f"{k} {round(100 * v / n)}%" for k, v in lead[label].most_common(4)) if n else "-"
        out(f"   {label:9} n={n:4d}  median trigger score {statistics.median(s) if s else 0:5.1f}  "
            f"no audio {round(100 * silent[label] / n) if n else 0:3d}%  led by: {top}")
    out(f"\nPer-signal separation over the same window:\n"
        f"  venv/bin/python -m src.maintenance.analyze_training_log --days {days:g}")


def main() -> int:
    args = sys.argv[1:]
    days = float(args[args.index("--days") + 1]) if "--days" in args else 7
    before = float(args[args.index("--before") + 1]) if "--before" in args else 14
    report(days, before)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
