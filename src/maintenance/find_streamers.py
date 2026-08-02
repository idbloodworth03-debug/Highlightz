"""Build an outreach shortlist of streamers in a target viewer band.

WHAT TWITCH WILL AND WILL NOT TELL YOU — read this before trusting the output.

  * There is NO country field anywhere in Helix. Not on streams, not on users,
    not on channels. "US-based" cannot be queried. The two honest proxies are
    `language` (necessary, nowhere near sufficient — en covers UK, CA, AU, IE,
    and plenty of EU streamers) and WHEN someone is live, which is a real
    signal but only if you sample around the clock. Both are reported; neither
    is treated as proof, and the tool refuses to imply otherwise.

  * `GET /streams` returns viewers RIGHT NOW. There is no average-viewers
    endpoint. A single snapshot of a 100-500 band is mostly people having an
    unusually good or bad night, which is the wrong list to do outreach
    against. So this samples over days and averages.

Hence two commands:

    python -m src.maintenance.find_streamers --sample     # run on a cron, several times a day
    python -m src.maintenance.find_streamers --report     # aggregate what has been collected

Sampling is cheap but not free, and Helix's budget is SHARED WITH LIVE
CLIPPING (800 points/min across the whole app). One pass costs roughly 20-40
requests, paced with a delay, and is hard-capped by --max-pages. Do not run it
on a tight loop.
"""

import argparse
import asyncio
import csv
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# 22:00-07:00 UTC is roughly 6pm-3am US Eastern / 3pm-midnight US Pacific: the
# window a US-audience streamer almost has to be live in. Being live here does
# not make someone American; being NEVER live here makes them very unlikely to
# be, which is the direction this filter is actually reliable in.
US_PRIME_UTC = set(list(range(22, 24)) + list(range(0, 7)))


def _store() -> Path:
    d = Path(settings.local_storage_path) / "prospects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _samples_file() -> Path:
    return _store() / "samples.jsonl"


# ---------------------------------------------------------------- sampling

async def _token(session: aiohttp.ClientSession) -> str:
    async with session.post(TOKEN_URL, params={
        "client_id": settings.twitch_client_id,
        "client_secret": settings.twitch_client_secret,
        "grant_type": "client_credentials",
    }) as r:
        r.raise_for_status()
        return (await r.json())["access_token"]


async def sample(language: str, floor: int, max_pages: int, delay: float) -> int:
    """One pass down the live-stream list, stopping once viewer counts fall
    below `floor`. Helix sorts /streams by viewer count descending, which is
    the only reason walking it like this terminates."""
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        print("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set.")
        return 1

    rows, cursor, pages = [], None, 0
    async with aiohttp.ClientSession() as session:
        tok = await _token(session)
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {tok}"}
        while pages < max_pages:
            params = {"first": 100, "type": "live"}
            if language:
                params["language"] = language
            if cursor:
                params["after"] = cursor
            async with session.get(f"{HELIX}/streams", headers=headers,
                                   params=params) as r:
                if r.status == 429:
                    print("Helix rate-limited us — stopping this pass early. "
                          "Clip creation shares this budget, so backing off.")
                    break
                r.raise_for_status()
                data = await r.json()

            batch = data.get("data") or []
            if not batch:
                break
            pages += 1
            for s in batch:
                rows.append({
                    "login": s.get("user_login"),
                    "name": s.get("user_name"),
                    "uid": s.get("user_id"),
                    "viewers": s.get("viewer_count") or 0,
                    "game": s.get("game_name") or "",
                    "lang": s.get("language") or "",
                    "title": (s.get("title") or "")[:120],
                })
            # Sorted descending, so once the tail of a page is under the floor
            # every later page is too.
            if (batch[-1].get("viewer_count") or 0) < floor:
                break
            cursor = (data.get("pagination") or {}).get("cursor")
            if not cursor:
                break
            await asyncio.sleep(delay)

    rows = [r for r in rows if r["viewers"] >= floor and r["login"]]
    ts = time.time()
    with _samples_file().open("a", encoding="utf-8") as fh:
        for r in rows:
            r["ts"] = ts
            fh.write(json.dumps(r) + "\n")

    hour = datetime.fromtimestamp(ts, timezone.utc).hour
    print(f"sampled {len(rows)} live channels >= {floor} viewers "
          f"in {pages} page(s), at {hour:02d}:00 UTC")
    print(f"appended to {_samples_file()}")
    return 0


# ---------------------------------------------------------------- reporting

def _load() -> list[dict]:
    f = _samples_file()
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def aggregate(rows: list[dict]) -> dict[str, dict]:
    """Per channel: average viewers across the passes it was LIVE for.

    Averaging only over live samples is deliberate — this is "what does their
    stream usually pull", not "how often do they stream". Counting offline
    passes as zero would rank a 400-viewer weekend streamer below a 120-viewer
    daily one, which is backwards for outreach.
    """
    by: dict[str, dict] = {}
    seen_hours: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        login = r.get("login")
        if not login:
            continue
        e = by.setdefault(login, {
            "login": login, "name": r.get("name") or login,
            "uid": r.get("uid") or "", "viewers": [], "games": defaultdict(int),
            "langs": set(), "titles": [],
        })
        e["viewers"].append(r.get("viewers") or 0)
        if r.get("game"):
            e["games"][r["game"]] += 1
        if r.get("lang"):
            e["langs"].add(r["lang"])
        if r.get("title"):
            e["titles"].append(r["title"])
        if r.get("ts"):
            seen_hours[login].append(
                datetime.fromtimestamp(r["ts"], timezone.utc).hour)

    for login, e in by.items():
        v = e["viewers"]
        hours = seen_hours[login]
        e["samples"] = len(v)
        e["avg"] = round(statistics.fmean(v), 1)
        e["median"] = round(statistics.median(v), 1)
        e["min"] = min(v)
        e["max"] = max(v)
        e["top_game"] = max(e["games"].items(), key=lambda kv: kv[1])[0] if e["games"] else ""
        e["lang"] = ",".join(sorted(e["langs"]))
        e["us_prime_pct"] = round(
            100 * sum(1 for h in hours if h in US_PRIME_UTC) / len(hours)) if hours else 0
        e["hours"] = sorted(set(hours))
    return by


def _hour_coverage(rows: list[dict]) -> set[int]:
    return {datetime.fromtimestamp(r["ts"], timezone.utc).hour
            for r in rows if r.get("ts")}


def report(lo: int, hi: int, min_samples: int, language: str, limit: int) -> int:
    rows = _load()
    if not rows:
        print(f"No samples yet at {_samples_file()}.\n"
              "Run --sample a few times across different times of day first.")
        return 1

    passes = sorted({round(r["ts"]) for r in rows if r.get("ts")})
    covered = _hour_coverage(rows)
    print(f"{len(rows)} observations from {len(passes)} sampling pass(es), "
          f"UTC hours covered: {sorted(covered)}")

    # These two warnings exist because both mistakes produce a confident-looking
    # list that is wrong, and neither is visible in the output itself.
    if len(passes) < 3:
        print("\n!! Only %d pass(es). An 'average' over this is a snapshot with "
              "extra steps — someone having one good night reads as a 400-viewer "
              "streamer. Sample across several days before acting on this."
              % len(passes))
    prime_only = covered and covered.issubset(US_PRIME_UTC)
    if prime_only:
        print("\n!! Every sample was taken during US prime hours, so the "
              "US-prime %% column is 100%% for everyone by construction and "
              "means nothing. Sample outside 22:00-07:00 UTC to make it real.")

    agg = aggregate(rows)
    picks = [e for e in agg.values()
             if lo <= e["avg"] <= hi
             and e["samples"] >= min_samples
             and (not language or language in e["lang"])]
    picks.sort(key=lambda e: (-e["us_prime_pct"], -e["samples"], -e["avg"]))

    if not picks:
        print(f"\nNothing matched {lo}-{hi} avg viewers with >= {min_samples} "
              f"samples. Widen the band or collect more passes.")
        return 1

    print(f"\n{len(picks)} channel(s) averaging {lo}-{hi} viewers "
          f"(showing {min(limit, len(picks))}):\n")
    print(f"{'channel':<22}{'avg':>7}{'med':>7}{'min':>6}{'max':>7}"
          f"{'n':>4}{'US?':>6}  game")
    print("-" * 88)
    for e in picks[:limit]:
        prime = "n/a" if prime_only else f"{e['us_prime_pct']}%"
        print(f"{e['login']:<22}{e['avg']:>7}{e['median']:>7}{e['min']:>6}"
              f"{e['max']:>7}{e['samples']:>4}{prime:>6}  {e['top_game'][:28]}")

    out = _store() / "prospects.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["channel", "display_name", "url", "avg_viewers",
                    "median_viewers", "min", "max", "samples",
                    "us_prime_pct", "languages", "top_game"])
        for e in picks:
            w.writerow([e["login"], e["name"], f"https://twitch.tv/{e['login']}",
                        e["avg"], e["median"], e["min"], e["max"], e["samples"],
                        e["us_prime_pct"], e["lang"], e["top_game"]])
    print(f"\nfull list ({len(picks)} rows) -> {out}")
    print("\nThe US column is an inference from when they stream, not a fact "
          "Twitch reports. Check the channel before you write to anyone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sample", action="store_true", help="collect one pass")
    p.add_argument("--report", action="store_true", help="aggregate collected passes")
    p.add_argument("--language", default="en", help="Helix language code (default en)")
    p.add_argument("--min", type=int, default=100, dest="lo")
    p.add_argument("--max", type=int, default=500, dest="hi")
    p.add_argument("--floor", type=int, default=60,
                   help="stop sampling below this many viewers. Deliberately "
                        "under --min so a target's quiet nights are still "
                        "recorded; cutting at --min would bias every average up.")
    p.add_argument("--min-samples", type=int, default=3)
    p.add_argument("--max-pages", type=int, default=60)
    p.add_argument("--delay", type=float, default=0.4,
                   help="seconds between Helix pages; this budget is shared "
                        "with live clip creation")
    p.add_argument("--limit", type=int, default=40)
    a = p.parse_args(argv)

    if a.sample:
        return asyncio.run(sample(a.language, a.floor, a.max_pages, a.delay))
    if a.report:
        return report(a.lo, a.hi, a.min_samples, a.language, a.limit)
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
