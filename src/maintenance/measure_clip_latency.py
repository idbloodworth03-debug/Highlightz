"""
PHASE 0 of the viewer-clip trigger: measure, before changing any formula.

Viewers clipping a moment is the strongest highlight evidence available — a
human decided it mattered, live, in the moment. But whether we can ACT on it
depends on one number nobody documents: how long after a viewer creates a clip
does it become visible in Helix Get Clips?

Why that number decides the design:

  Twitch's Create Clip captures roughly the last 60s of broadcast at call time.
  If we learn about a viewer's clip L seconds late and then create our own, our
  capture window has already slid forward by L.

    L < ~20s   -> the moment is still well inside our window. Viewer clips can
                  drive a TRIGGER: clip the instant the crowd clips.
    L ~ 20-45s -> marginal; we'd catch the tail/reaction, not the payoff.
                  Usable as a strong scoring signal, risky as a trigger.
    L > ~45s   -> too late to re-clip. Still valuable as a LEARNING signal
                  (did we score that moment highly? should this channel's
                  threshold move?) but not as a live trigger.

This script also measures the two things that size the feature:
  * baseline clip RATE per channel (clips/min) — the signal has to be a spike
    against the channel's own normal, exactly like every other signal here.
  * how many clips are OURS (Highlightz-created). Those must never count:
    our clips are real Twitch clips and appear in this same API, so counting
    them would make the bot detect its own clip and re-trigger, forever.

Read-only. Run on the server against a channel that is LIVE and busy:

  venv/bin/python -m src.maintenance.measure_clip_latency jynxzi --minutes 20
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from config.settings import settings
from src.output.twitch_clips import HELIX_BASE, _get_app_token, resolve_broadcaster_id

# Poll interval. Deliberately tighter than we would run in production: the
# measurement resolution of the lag cannot be better than this.
_POLL_SECS = 5.0
# Query window. Sent as BOTH started_at and ended_at — passing started_at alone
# makes Twitch default the window to a WEEK, which would return a channel's
# entire clip history every poll.
_WINDOW_MINS = 5


def _ours() -> tuple[set[str], set[str]]:
    """(twitch user ids of our users, slugs of clips we created).

    Both are needed. creator_id catches clips made by a Highlightz user's
    account; the slug set catches anything the id check misses.
    """
    base = Path(settings.local_storage_path)
    uids: set[str] = set()
    slugs: set[str] = set()
    try:
        users = json.loads((base / "users.json").read_text())
        users = users if isinstance(users, list) else users.get("users", [])
        uids = {str(u.get("twitch_id")) for u in users if u.get("twitch_id")}
    except (OSError, json.JSONDecodeError):
        pass
    try:
        clips = json.loads((base / "clips.json").read_text())
        slugs = {str(c.get("twitch_clip_id")) for c in clips if c.get("twitch_clip_id")}
    except (OSError, json.JSONDecodeError):
        pass
    return uids, slugs


def _parse_ts(s: str) -> float:
    """RFC3339 -> epoch seconds. Returns 0.0 when unparseable."""
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    o = sorted(values)
    return o[min(len(o) - 1, max(0, int(round(p * (len(o) - 1)))))]


async def _poll(session, headers, bid: str) -> list[dict]:
    now = time.time()
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {
        "broadcaster_id": bid,
        "first": 100,
        # Seconds are ignored by Twitch, so pad the window generously on both
        # sides rather than trusting minute alignment.
        "started_at": datetime.fromtimestamp(now - _WINDOW_MINS * 60, timezone.utc).strftime(fmt),
        "ended_at":   datetime.fromtimestamp(now + 120, timezone.utc).strftime(fmt),
    }
    async with session.get(f"{HELIX_BASE}/clips", headers=headers, params=params) as r:
        if r.status != 200:
            print(f"  ! poll failed HTTP {r.status}")
            return []
        return (await r.json()).get("data", [])


async def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    channel = args[0].lstrip("@").lower()
    minutes = float(args[args.index("--minutes") + 1]) if "--minutes" in args else 15.0

    bid = await resolve_broadcaster_id(channel)
    if not bid:
        print(f"could not resolve channel '{channel}'")
        return 1

    our_uids, our_slugs = _ours()
    print(f"=== viewer-clip latency probe — {channel} (id {bid}) ===")
    print(f"polling every {_POLL_SECS:g}s for {minutes:g} min, "
          f"{_WINDOW_MINS}-minute lookback window")
    print(f"excluding {len(our_uids)} Highlightz account(s) and "
          f"{len(our_slugs)} clip(s) we created\n")

    seen: dict[str, float] = {}      # slug -> first time WE saw it
    created: dict[str, float] = {}   # slug -> Twitch's created_at
    lags: list[float] = []
    ours_filtered = 0
    minute_buckets: dict[int, int] = {}
    deadline = time.time() + minutes * 60

    async with aiohttp.ClientSession() as session:
        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {token}"}
        while time.time() < deadline:
            now = time.time()
            for c in await _poll(session, headers, bid):
                slug = c.get("id")
                if not slug or slug in seen:
                    continue
                if str(c.get("creator_id")) in our_uids or slug in our_slugs:
                    ours_filtered += 1
                    continue
                ts = _parse_ts(c.get("created_at", ""))
                seen[slug] = now
                created[slug] = ts
                if ts:
                    lag = now - ts
                    # A clip that already existed when we started shows a huge
                    # apparent lag; only count ones born during the probe.
                    if 0 <= lag <= _WINDOW_MINS * 60:
                        lags.append(lag)
                        minute_buckets[int(ts // 60)] = minute_buckets.get(int(ts // 60), 0) + 1
                        print(f"  +{lag:5.1f}s  {c.get('creator_name','?')[:18]:18s} "
                              f"{(c.get('title') or '')[:44]}")
            await asyncio.sleep(_POLL_SECS)

    print(f"\n=== results over {minutes:g} minutes ===")
    print(f"  new viewer clips seen : {len(lags)}")
    print(f"  our clips filtered out: {ours_filtered}")
    if not lags:
        print("\n  No viewer clips appeared. Either the channel is quiet, its")
        print("  audience does not clip, or it went offline. Re-run on a busy")
        print("  channel during a hype moment before concluding anything.")
        return 0

    print(f"\n  -- LATENCY (created_at -> visible in API) --")
    print(f"     median {_pct(lags,0.5):6.1f}s     p90 {_pct(lags,0.9):6.1f}s"
          f"     max {max(lags):6.1f}s     min {min(lags):5.1f}s")
    med = _pct(lags, 0.5)
    print()
    if med < 20:
        print("  VERDICT: fast enough to TRIGGER on. Build the consensus tripwire —")
        print("  the moment is still inside our 60s capture window at this lag.")
    elif med < 45:
        print("  VERDICT: marginal. A clip fired now would catch the reaction, not")
        print("  the payoff. Use it as a strong SCORING signal; only trigger when")
        print("  several viewers clip at once (consensus outweighs the timing cost).")
    else:
        print("  VERDICT: too slow to re-clip. Use it as a LEARNING signal only —")
        print("  score the moments viewers clipped, and auto-tune this channel's")
        print("  threshold toward them. Do NOT wire it to a trigger.")

    if minute_buckets:
        counts = list(minute_buckets.values())
        span = max(1, len(minute_buckets))
        print(f"\n  -- CLIP RATE (sets the spike baseline) --")
        print(f"     mean {sum(counts)/span:5.2f} clips/min   busiest minute {max(counts)}")
        print(f"     a trigger should fire on a SPIKE against this baseline,")
        print(f"     not on an absolute count — same normalisation as every")
        print(f"     other signal (a channel that always gets 3 clips/min is")
        print(f"     not interesting at 3 clips/min).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
