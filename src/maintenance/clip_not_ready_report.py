"""
Twitch clips that never finished: which channels, which hours, whose, did
they finish late, and what did waiting for them cost everyone else?

    venv/bin/python -m src.maintenance.clip_not_ready_report [--hours 6] [--no-twitch]

Read-only. Built for the alert of 2026-10-23: `twitch_clip_not_ready` x21 in
76 minutes, the example on jynxzi, against about 15 a day (2.6% of clips)
measured on 2026-10-20. Create Clip answered 202 each time; Get Clips never
showed a playable clip within the ~50s poll, so the moment was dropped.

ONE CLIP AT A TIME. `run_clip_processor` is a single loop over one queue for
every account, channel and platform, and `get_clip` polls inside it. So a
clip Twitch never makes holds every other job for ~56s (post-roll, Create
Clip, 20 polls 2.5s apart), and a job older than MAX_CLIP_JOB_AGE_SECS (90s)
is dropped as stale. The queue section measures that cost. It also means two
of our Create Clip calls are never in flight at once, and that requests
spacing out to ~56s apart is a run of failures, not a detector.

The sections:
  per hour     Twitch requests, ready, not ready, and minutes the queue spent
               waiting on clips that never came
  the queue    every clip job (Twitch and Kick): how long each waited to
               start, and how many were dropped as stale or skipped as full
  per channel  Twitch requests by channel
  per account  requested / not ready, per hour. Both accounts failing in the
               same hours points at the channel or Twitch at that time; one
               account failing while another succeeds points at that account
  Twitch now   the not-ready slugs asked of Get Clips: finished late (we
               stopped waiting too soon), ghost (url, no video) or never made

Joins, from the journal (`journalctl -u highlightz`):
  processing_clip_job      clip_id, channel, user_id, platform, age_s
  creating_twitch_clip     channel, broadcaster_id, user_id
  twitch_clip_requested    slug, broadcaster_id (the 202). Its account and
                           job start are the latest creating_twitch_clip and
                           processing_clip_job, exact because jobs run one
                           at a time
  twitch_clip_ready_after  slug, seconds
  twitch_clip_not_ready    slug, channel
  clip_job_stale_dropped, clip_skipped_queue_full
It writes nothing; Get Clips is one call per 100 slugs with the app token.
"""

import asyncio
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

_FAILS = ("twitch_clip_create_failed", "twitch_clip_rate_limited",
          "twitch_clip_not_authorized", "twitch_clip_classification_failed")


def _when(rec: dict) -> float:
    try:
        return datetime.fromisoformat(str(rec.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _utc(ts: float, fmt: str) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime(fmt)


def _hour(ts: float) -> str:
    return _utc(ts, "%m-%d %H:00") if ts else "?"


def collect(lines) -> dict:
    """Every Twitch clip request in the lines, keyed by slug, with its outcome,
    plus every clip job the processor started, dropped or skipped."""
    chan_of: dict[str, str] = {}
    slugs: dict[str, dict] = {}
    refusals: Counter = Counter()
    users: dict[str, set] = defaultdict(set)
    jobs: list[tuple[float, float, str]] = []          # (started, waited s, platform)
    stale: list[tuple[float, str]] = []                # (dropped at, channel)
    full: list[float] = []
    creator, job_start = "", 0.0
    for line in lines:
        i = line.find("{")
        if i < 0 or '"event"' not in line:
            continue
        try:
            rec = json.loads(line[i:])
        except ValueError:
            continue
        ev, ts = rec.get("event"), _when(rec)
        if ev == "processing_clip_job":
            jobs.append((ts, float(rec.get("age_s") or 0), rec.get("platform") or "?"))
            job_start = ts
        elif ev == "clip_job_stale_dropped":
            stale.append((ts, rec.get("channel") or "?"))
        elif ev == "clip_skipped_queue_full":
            full.append(ts)
        elif ev == "creating_twitch_clip":
            chan_of[str(rec.get("broadcaster_id"))] = rec.get("channel") or "?"
            users[rec.get("channel") or "?"].add(rec.get("user_id"))
            creator = rec.get("user_id") or ""
        elif ev == "twitch_clip_requested" and rec.get("slug"):
            slugs.setdefault(rec["slug"], {}).update(at=ts, bid=str(rec.get("broadcaster_id")), user=creator,
                                                     start=job_start or ts)
        elif ev == "twitch_clip_ready_after" and rec.get("slug"):
            slugs.setdefault(rec["slug"], {}).update(outcome="ready", seconds=float(rec.get("seconds") or 0),
                                                     done=ts)
        elif ev == "twitch_clip_not_ready" and rec.get("slug"):
            slugs.setdefault(rec["slug"], {}).update(outcome="not ready", channel=rec.get("channel"), done=ts)
        elif ev in _FAILS:
            refusals[(str(rec.get("broadcaster_id")), ev)] += 1
    for s in slugs.values():
        s["channel"] = s.get("channel") or chan_of.get(s.get("bid", ""), "?")
        s.setdefault("at", s.get("done", 0.0))
        s.setdefault("outcome", "no outcome yet")
        s.setdefault("user", "")
    return {"slugs": slugs, "users": users, "jobs": jobs, "stale": stale, "full": full,
            "refusals": {(chan_of.get(b, "?"), ev): n for (b, ev), n in refusals.items()}}


def _names() -> dict:
    try:
        from src.auth import users as user_store
        return {u.get("id"): u.get("username") or u.get("id") for u in user_store._load()}
    except Exception:
        return {}


async def ask_twitch(slugs: list[str]) -> dict[str, str] | None:
    """slug -> 'finished late' | 'ghost' | 'never made', or None if Twitch could not be asked."""
    import aiohttp
    from config.settings import settings
    from src.output import twitch_clips
    found: dict[str, dict] = {}
    try:
        async with aiohttp.ClientSession() as session:
            token = await twitch_clips._get_app_token(session)
            headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
            for i in range(0, len(slugs), 100):
                params = [("id", s) for s in slugs[i:i + 100]] + [("first", "100")]
                async with session.get(f"{twitch_clips.HELIX_BASE}/clips", headers=headers,
                                       params=params) as resp:
                    if resp.status != 200:
                        return None
                    for r in (await resp.json()).get("data", []):
                        found[r.get("id")] = r
    except Exception:
        return None
    out = {}
    for s in slugs:
        r = found.get(s)
        out[s] = ("never made" if r is None
                  else "finished late" if r.get("thumbnail_url") and float(r.get("duration") or 0) > 0
                  else "ghost")
    return out


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d):3d}%" if d else "   -"


def _q(xs: list, p: float) -> float:
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))]


def report(lines, out=print, twitch=ask_twitch, names=_names) -> int:
    data = collect(lines)
    slugs, users = data["slugs"], data["users"]
    if not slugs and not data["jobs"]:
        out("No clip jobs in this window.")
        return 0

    out("== per hour (UTC): Twitch clip requests and what became of them ==")
    out("   hour          requested  ready  not ready  rate   slowest ready   queue held by not-ready")
    hours: dict[str, Counter] = defaultdict(Counter)
    slow: dict[str, list] = defaultdict(list)
    held: dict[str, float] = defaultdict(float)
    for s in slugs.values():
        h = _hour(s["at"])
        hours[h]["requested"] += 1
        hours[h][s["outcome"]] += 1
        if s["outcome"] == "ready":
            slow[h].append(s["seconds"])
        elif s["outcome"] == "not ready" and s.get("done"):
            held[h] += max(0.0, s["done"] - s.get("start", s["at"]))
    for h in sorted(hours):
        c = hours[h]
        top = f"{max(slow[h]):.1f} s" if slow[h] else "-"
        out(f"   {h}   {c['requested']:9d} {c['ready']:6d} {c['not ready']:10d}  "
            f"{_pct(c['not ready'], c['requested'])}   {top:>13}   {held[h] / 60:5.1f} min")

    out("\n== the queue: every clip job (Twitch and Kick), one at a time ==")
    out("   hour          started  waited: median   90th    max   dropped stale  skipped full")
    started: dict[str, list] = defaultdict(list)
    for ts, waited, _ in data["jobs"]:
        started[_hour(ts)].append(waited)
    stale_h = Counter(_hour(ts) for ts, _ in data["stale"])
    full_h = Counter(_hour(ts) for ts in data["full"])
    for h in sorted(set(started) | set(stale_h) | set(full_h)):
        w = started[h]
        waits = (f"{statistics.median(w):6.0f} s {_q(w, 0.9):5.0f} s {max(w):5.0f} s" if w
                 else f"{'-':>8} {'-':>7} {'-':>7}")
        out(f"   {h}   {len(w):7d}         {waits}   {stale_h[h]:12d}  {full_h[h]:12d}")
    if data["stale"]:
        by = Counter(ch for _, ch in data["stale"])
        out("   dropped stale, by channel: " + ", ".join(f"{c} {n}" for c, n in by.most_common(6)))

    if not slugs:
        return 0
    out("\n== per channel (Twitch) ==")
    out("   channel              requested  ready  not ready  rate  accounts")
    per: dict[str, list] = defaultdict(list)
    for s in slugs.values():
        per[s["channel"]].append(s)
    for ch in sorted(per, key=lambda c: (-sum(x["outcome"] == "not ready" for x in per[c]), c)):
        xs = per[ch]
        bad = sum(x["outcome"] == "not ready" for x in xs)
        out(f"   {ch[:20]:20} {len(xs):9d} {sum(x['outcome'] == 'ready' for x in xs):6d} {bad:10d} "
            f"{_pct(bad, len(xs))} {len(users.get(ch, ())):9d}")
    for (ch, ev), n in sorted(data["refusals"].items()):
        out(f"   also: {ch} {ev} x{n}")

    out("\n== per account (Twitch): requested/not ready, per hour ==")
    who = names()
    cols = sorted(hours)
    out("   account          " + "".join(f"{h[-5:]:>10}" for h in cols))
    by_user: dict[str, list] = defaultdict(list)
    for s in slugs.values():
        by_user[s["user"]].append(s)
    for uid in sorted(by_user, key=lambda u: (-sum(x["outcome"] == "not ready" for x in by_user[u]), u)):
        row = ""
        for h in cols:
            xs = [x for x in by_user[uid] if _hour(x["at"]) == h]
            bad = sum(x["outcome"] == "not ready" for x in xs)
            cell = f"{len(xs)}/{bad}" if xs else "-"
            row += f"{cell:>10}"
        out(f"   {str(who.get(uid) or uid or '?')[:16]:16} {row}")

    ready = [s["seconds"] for s in slugs.values() if s["outcome"] == "ready"]
    if ready:
        out(f"\n   ready times: median {statistics.median(ready):.1f} s, 90th {_q(ready, 0.9):.1f} s, "
            f"slowest {max(ready):.1f} s (we stop waiting at ~50 s; Twitch's docs say 15)")

    lost = [k for k, s in sorted(slugs.items(), key=lambda kv: -kv[1]["at"]) if s["outcome"] == "not ready"]
    if lost and twitch:
        out(f"\n== the {len(lost)} not-ready clips, asked of Twitch now ==")
        got = asyncio.run(twitch(lost))
        if got is None:
            out("   Twitch could not be asked (token or network); run again later.")
        else:
            out("   " + ", ".join(f"{k} {n}" for k, n in sorted(Counter(got.values()).items())))
            for k in lost[:10]:
                s = slugs[k]
                out(f"   {_utc(s['at'], '%m-%d %H:%M:%S')}  {s['channel'][:18]:18} "
                    f"{str(who.get(s['user']) or s['user'] or '?')[:14]:14} {got.get(k, '?'):13}  {k}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    hours = float(args[args.index("--hours") + 1]) if "--hours" in args else 6
    p = subprocess.run(["journalctl", "-u", "highlightz", "--since", f"{hours:g} hours ago",
                        "-o", "cat", "--no-pager"], capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stderr.strip() or "journalctl failed")
        return 2
    return report(p.stdout.splitlines(), twitch=None if "--no-twitch" in args else ask_twitch)


if __name__ == "__main__":
    raise SystemExit(main())
