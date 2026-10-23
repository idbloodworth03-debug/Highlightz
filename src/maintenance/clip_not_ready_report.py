"""
Twitch clips that never finished: which channels, which hours, and did they
finish late?

    venv/bin/python -m src.maintenance.clip_not_ready_report [--hours 6] [--no-twitch]

Read-only. Built for the alert of 2026-10-23: `twitch_clip_not_ready` x21 in
76 minutes, the example on jynxzi, against about 15 a day (2.6% of clips)
measured on 2026-10-20. Create Clip answered 202 each time; Get Clips never
showed a playable clip within the ~50s poll, so the moment was dropped.

Four explanations predict different numbers, and this prints them:

  all of Twitch    many channels fail in the same hours (Twitch, or our Get
                   Clips call), and almost nothing is ready anywhere
  one channel      one channel fails while others in the same hours are fine
  crowding         each account runs its own detector and creates its own
                   clip with its own token, so a big channel can get several
                   Create Clip calls within seconds. If that is what fails,
                   requests close to another one fail more than lone ones
  slow, not lost   Twitch finished them after we stopped waiting: they are
                   playable now

It reads the journal for the window (`journalctl -u highlightz`) and joins
each request to its outcome:
  creating_twitch_clip     channel, broadcaster_id, user_id
  twitch_clip_requested    slug, broadcaster_id (the 202)
  twitch_clip_ready_after  slug, seconds
  twitch_clip_not_ready    slug, channel
then asks Twitch's Get Clips (app token, 100 ids a call) what became of the
not-ready ones. It writes nothing.
"""

import asyncio
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

CROWD_S = 10.0          # another request on the same channel this close = crowded
_FAILS = ("twitch_clip_create_failed", "twitch_clip_rate_limited",
          "twitch_clip_not_authorized", "twitch_clip_classification_failed")


def _when(rec: dict) -> float:
    try:
        return datetime.fromisoformat(str(rec.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def collect(lines) -> dict:
    """Every Twitch clip request in the lines, keyed by slug, with its outcome."""
    chan_of: dict[str, str] = {}
    slugs: dict[str, dict] = {}
    refusals: Counter = Counter()
    users: dict[str, set] = defaultdict(set)
    for line in lines:
        i = line.find("{")
        if i < 0 or '"event"' not in line:
            continue
        try:
            rec = json.loads(line[i:])
        except ValueError:
            continue
        ev, ts = rec.get("event"), _when(rec)
        if ev == "creating_twitch_clip":
            chan_of[str(rec.get("broadcaster_id"))] = rec.get("channel") or "?"
            users[rec.get("channel") or "?"].add(rec.get("user_id"))
        elif ev == "twitch_clip_requested" and rec.get("slug"):
            slugs.setdefault(rec["slug"], {}).update(at=ts, bid=str(rec.get("broadcaster_id")))
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
    by_channel: dict[str, list] = defaultdict(list)
    for slug, s in slugs.items():
        by_channel[s["channel"]].append((s["at"], slug))
    for reqs in by_channel.values():
        reqs.sort()
        for k, (at, slug) in enumerate(reqs):
            near = [a for j, (a, _) in enumerate(reqs) if j != k and abs(a - at) <= CROWD_S]
            slugs[slug]["crowded"] = bool(near)
    return {"slugs": slugs, "users": users,
            "refusals": {(chan_of.get(b, "?"), ev): n for (b, ev), n in refusals.items()}}


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


def _utc(ts: float, fmt: str) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime(fmt)


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d):3d}%" if d else "   -"


def report(lines, out=print, twitch=ask_twitch) -> int:
    data = collect(lines)
    slugs, users = data["slugs"], data["users"]
    if not slugs:
        out("No Twitch clip requests in this window.")
        return 0

    out("== per hour (UTC): Twitch clip requests and what became of them ==")
    out("   hour          requested  ready  not ready  rate   slowest ready")
    hours: dict[str, Counter] = defaultdict(Counter)
    slow: dict[str, list] = defaultdict(list)
    for s in slugs.values():
        h = _utc(s["at"], "%m-%d %H:00") if s["at"] else "?"
        hours[h]["requested"] += 1
        hours[h][s["outcome"]] += 1
        if s["outcome"] == "ready":
            slow[h].append(s["seconds"])
    for h in sorted(hours):
        c = hours[h]
        top = f"{max(slow[h]):.1f} s" if slow[h] else "-"
        out(f"   {h}   {c['requested']:9d} {c['ready']:6d} {c['not ready']:10d}  {_pct(c['not ready'], c['requested'])}   {top}")

    out("\n== per channel ==")
    out("   channel              requested  ready  not ready  rate  accounts   not-ready rate: crowded | alone")
    per: dict[str, list] = defaultdict(list)
    for s in slugs.values():
        per[s["channel"]].append(s)
    for ch in sorted(per, key=lambda c: (-sum(x["outcome"] == "not ready" for x in per[c]), c)):
        xs = per[ch]
        bad = sum(x["outcome"] == "not ready" for x in xs)
        crowd = [x for x in xs if x.get("crowded")]
        alone = [x for x in xs if not x.get("crowded")]
        cb = sum(x["outcome"] == "not ready" for x in crowd)
        ab = sum(x["outcome"] == "not ready" for x in alone)
        out(f"   {ch[:20]:20} {len(xs):9d} {sum(x['outcome'] == 'ready' for x in xs):6d} {bad:10d} "
            f"{_pct(bad, len(xs))} {len(users.get(ch, ())):9d}   "
            f"{_pct(cb, len(crowd))} of {len(crowd):3d} | {_pct(ab, len(alone))} of {len(alone):3d}")
    for (ch, ev), n in sorted(data["refusals"].items()):
        out(f"   also: {ch} {ev} x{n}")

    ready = [s["seconds"] for s in slugs.values() if s["outcome"] == "ready"]
    if ready:
        q = sorted(ready)
        out(f"\n   ready times: median {statistics.median(q):.1f} s, 90th {q[int(0.9 * (len(q) - 1))]:.1f} s, "
            f"slowest {q[-1]:.1f} s (we stop waiting at ~50 s)")

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
                out(f"   {_utc(s['at'], '%m-%d %H:%M:%S')}  "
                    f"{s['channel'][:18]:18} {got.get(k, '?'):13}  {k}")
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
