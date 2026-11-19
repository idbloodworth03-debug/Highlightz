"""How fast are sign-ups growing, and where does that pace lead? Read-only.

    cd /opt/highlightz && venv/bin/python scripts/growth.py
    cd /opt/highlightz && venv/bin/python scripts/growth.py --tz=America/Chicago --days=30

Owner, 2026-11-19: "we grew so much in the past few days if we grow at this
rate how big can we get in a few weeks. Take into account the fact that we
grew more and more each day", and then, of a first answer worked out from one
screenshot of the admin page: "i got more then this use accurate numbers".
The accurate numbers are in the accounts file on the server. This reads them.

WHAT IT PRINTS
  1. Sign-ups for each of the last days, in the owner's time zone, counted
     the way the admin page's Users tile counts them (staff are not counted;
     a day is 12:00 AM to 12:00 AM). Today is marked as not finished, with
     how it compares to yesterday BY THE SAME TIME OF DAY, which is the one
     fair comparison before the day is over.
  2. The pace: this week against the week before, and by how much each day
     has been multiplying the one before it (fitted to the last full days,
     so one odd day does not decide it).
  3. Where that leads in 7, 14 and 21 days, three ways, each saying what it
     assumes: it holds where it is; it keeps growing for one more week, by
     less each day, and then holds; it multiplies at the last week's pace
     every day to the end. THE THIRD IS ARITHMETIC, NOT A FORECAST: nothing
     keeps multiplying every day for weeks, and the output says so when the
     number is past believing.
  4. Where the new accounts say they came from, the last three days against
     the three before. One source carrying a spike says how long it lasts.
  5. What the server carries: channels live now against accounts, and at how
     many accounts the live limit is reached if that share holds.

Counts only. Never an account's name, an address, an id or anything typed
into a form. "Where they came from" is the fixed list the sign-up question
offers, not the free text beside it.
"""
import collections
import json
import math
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OWNER_TZ = "America/New_York"       # where the owner reads the admin page (docs/HANDOFF.md)
FIT_DAYS = 7                        # full days the daily multiple is fitted to
HORIZONS = (7, 14, 21)
FADE_DAYS = 7                       # scenario B: how long the growth takes to run out
EARLY = 0.25                        # before this much of the day has gone, today says little
BEYOND_BELIEF = 1_000_000           # past this the output stops pretending
LIVE_NOTE_FRESH_S = 180.0


def zone_for(name: str):
    """(zone, its name); UTC when the name is unknown."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:
        return timezone.utc, "UTC"


def customers(users) -> list[dict]:
    """The accounts the admin page counts: every account that is not staff."""
    return [u for u in users if isinstance(u, dict) and u.get("id") and not u.get("is_admin")]


def _created(u: dict) -> float:
    try:
        return float(u.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def daily(users: list[dict], now: float, zone, days: int) -> list[tuple]:
    """[(date, sign-ups that calendar day)], oldest first, ending today."""
    today = datetime.fromtimestamp(now, zone).date()
    counts = collections.Counter(datetime.fromtimestamp(t, zone).date()
                                 for t in map(_created, users) if 0 < t <= now)
    return [(d, counts.get(d, 0)) for d in (today - timedelta(days=i) for i in range(days - 1, -1, -1))]


def by_this_time(users: list[dict], now: float, zone) -> tuple[int, int, float]:
    """(sign-ups today so far, sign-ups yesterday by the same clock time, how
    much of today has gone, 0 to 1). The fair way to compare a day that is
    not over with the one before it."""
    here = datetime.fromtimestamp(now, zone)
    start = datetime(here.year, here.month, here.day, tzinfo=zone)
    y = start.date() - timedelta(days=1)
    y_start = datetime(y.year, y.month, y.day, tzinfo=zone)
    gone = (here - start).total_seconds()
    today = sum(1 for t in map(_created, users) if start.timestamp() <= t <= now)
    yday = sum(1 for t in map(_created, users)
               if y_start.timestamp() <= t <= y_start.timestamp() + gone)
    return today, yday, max(0.0, min(1.0, gone / 86400.0))


def daily_multiple(counts: list[int]) -> float | None:
    """By how much each day multiplied the one before, fitted through all of
    `counts` (a straight line through their logarithms). 1.0 is flat, 2.0 is
    doubling every day. None with fewer than three days or nothing in them."""
    if len(counts) < 3 or not any(counts):
        return None
    # A day with nobody is counted as half a sign-up, so its logarithm exists;
    # days with somebody are used exactly as they are.
    ys = [math.log(c if c > 0 else 0.5) for c in counts]
    n = len(ys)
    mx, my = (n - 1) / 2.0, sum(ys) / n
    den = sum((i - mx) ** 2 for i in range(n))
    slope = sum((i - mx) * (y - my) for i, y in enumerate(ys)) / den
    return math.exp(slope)


def path(level: float, multiple: float, days: int, fade_days: int = 0) -> list[float]:
    """Sign-ups added by the end of each of the next `days` days, starting
    from `level` a day. ONE path, read off at any day, so 7, 14 and 21 days
    are points on the same line.

    fade_days=0: every day is `multiple` times the day before, all the way.
    fade_days=n: the growth runs out evenly over n days (`multiple` on the
    first, a little less each day, none from day n on) and the daily level
    then holds: still more each day for a while, by less."""
    out, added, per_day = [], 0.0, float(level)
    for i in range(days):
        if fade_days:
            m = 1.0 + (multiple - 1.0) * max(0.0, 1.0 - i / float(fade_days))
        else:
            m = multiple
        per_day *= m
        added = min(added + per_day, 1e18)
        per_day = min(per_day, 1e18)
        out.append(added)
    return out


def sources(users: list[dict], now: float, zone, span: int = 3) -> tuple[collections.Counter, collections.Counter]:
    """(where the last `span` days' accounts said they came from, the same for
    the `span` days before). Only the fixed answers the question offers."""
    today = datetime.fromtimestamp(now, zone).date()

    def window(first_back: int, last_back: int) -> collections.Counter:
        got = collections.Counter()
        for u in users:
            t = _created(u)
            if t <= 0 or t > now:
                continue
            back = (today - datetime.fromtimestamp(t, zone).date()).days
            if last_back <= back <= first_back:
                said = str(u.get("found_via") or "").strip().lower()
                got[said if said.isidentifier() and len(said) <= 20 else "(not said)"] += 1
        return got
    return window(span - 1, 0), window(2 * span - 1, span)


def live_now(store: pathlib.Path, now: float) -> int | None:
    """Channels holding a live slot, from the note the site writes every 30
    seconds. None when there is no fresh note."""
    try:
        got = json.loads((store / "headroom.json").read_text())
        if now - float(got.get("at") or 0) > LIVE_NOTE_FRESH_S:
            return None
        return len(got.get("keys") or [])
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _n(x: float) -> str:
    return "beyond counting" if x > 1e12 else f"{x:,.0f}"


def report(users: list[dict], now: float, tz_name: str = OWNER_TZ, days: int = 21,
           live: int | None = None, live_limit: int | None = None) -> list[str]:
    zone, used = zone_for(tz_name)
    people = customers(users)
    series = daily(people, now, zone, max(days, FIT_DAYS + 8))
    shown = series[-days:]
    today_n, yday_same, gone = by_this_time(people, now, zone)
    full = [n for _d, n in series[:-1]]                 # every finished day
    out = ["=" * 70, f"SIGN-UPS BY DAY  ({used} time; staff not counted)", "=" * 70]
    top = max((n for _d, n in shown), default=0) or 1
    for d, n in shown:
        mark = "   <- today, not finished" if d == series[-1][0] else ""
        out.append(f"  {d:%a %b %d}  {n:>6,}  {'#' * round(40 * n / top)}{mark}")
    out.append(f"  accounts in all: {len(people):,}")

    out += ["", "=" * 70, "THE PACE", "=" * 70]
    yday_total = full[-1] if full else 0
    out.append(f"  today so far ({gone * 24:.0f} h in)          : {today_n:,}")
    out.append(f"  yesterday by the same time       : {yday_same:,}"
               + (f"   -> today is running at {today_n / yday_same:.1f}x yesterday" if yday_same else ""))
    est_today = today_n * yday_total / yday_same if yday_same and gone < 1 else float(today_n)
    est_today = max(est_today, float(today_n))
    out.append(f"  yesterday, the whole day         : {yday_total:,}")
    early = gone < EARLY
    if yday_same and not early:
        out.append(f"  today, if it keeps yesterday's shape: about {est_today:,.0f} by midnight (an estimate)")
    elif early:
        out.append("  (too early in the day for today to say much; yesterday is used below)")
    if len(full) >= 14:
        wk, before = sum(full[-7:]), sum(full[-14:-7])
        out.append(f"  last 7 full days / the 7 before  : {wk:,} / {before:,}"
                   + (f"   -> {wk / before:.1f}x" if before else ""))
    m7 = daily_multiple(full[-FIT_DAYS:])
    m3 = daily_multiple(full[-3:])
    if m7:
        out.append(f"  each day against the one before  : x{m7:.2f} over the last {FIT_DAYS} full days"
                   + (f", x{m3:.2f} over the last 3" if m3 else ""))

    # The daily level everything below starts from: today's estimate once
    # enough of the day has gone to trust it, yesterday's total before that.
    level = est_today if (yday_same and not early) else float(max(today_n, yday_total))
    out += ["", "=" * 70, "WHERE THAT LEADS  (accounts in all; each line says what it assumes)", "=" * 70]
    out.append(f"  starting from {len(people):,} accounts and about {level:,.0f} new a day")
    out.append("  " + " " * 50 + "".join(f"{f'in {h} days':>14}" for h in HORIZONS))

    def line(label: str, mult: float, fade_days: int = 0) -> str:
        added = path(level, mult, HORIZONS[-1], fade_days)
        return f"  {label:<50}" + "".join(f"{_n(len(people) + added[h - 1]):>14}" for h in HORIZONS)
    out.append(line("A. it holds: every day like today", 1.0))
    if m7 and m7 > 1.0:
        out.append(line(f"B. grows one more week, by less each day, holds", m7, FADE_DAYS))
        out.append(line(f"C. x{m7:.2f} a day, every day, to the end", m7))
        out.append(f"  B starts at the last week's x{m7:.2f} a day and runs out evenly over {FADE_DAYS} days.")
        if len(people) + path(level, m7, HORIZONS[-1])[-1] > BEYOND_BELIEF:
            out.append("  C IS ARITHMETIC, NOT A FORECAST. Nothing multiplies every day for three")
            out.append("  weeks: a spike comes from somewhere, and that somewhere runs out.")
        else:
            out.append("  C is the last week's pace never slowing, which no spike has done for long.")
        out.append("  Read A as where it lands if the new level simply holds, and B as growth that")
        out.append("  keeps coming for a while. Which one it is shows in the next two or three days.")
    elif m7:
        out.append(f"  The last {FIT_DAYS} full days were not growing day on day (x{m7:.2f}), so A is the line to read.")
    if yday_same and not early and yday_total and est_today < yday_total * 0.6:
        out.append("  TODAY IS RUNNING WELL BELOW YESTERDAY: the spike may already be passing.")

    last3, prior3 = sources(people, now, zone)
    if last3 or prior3:
        out += ["", "=" * 70, "WHERE THEY SAY THEY CAME FROM  (last 3 days / the 3 before)", "=" * 70]
        # Biggest now first; then any that were there before and are not now.
        names = [n for n, _c in last3.most_common()] + [n for n, _c in prior3.most_common() if n not in last3]
        for name in names[:10]:
            out.append(f"  {name:<20} {last3.get(name, 0):>6,} / {prior3.get(name, 0):,}")

    if live is not None and live_limit:
        out += ["", "=" * 70, "WHAT THE SERVER CARRIES", "=" * 70]
        out.append(f"  channels live right now          : {live:,} of a limit of {live_limit:,}")
        if live and people:
            per = live / len(people)
            full_at = live_limit / per
            out.append(f"  that is one live channel for every {1 / per:.1f} accounts; if that share holds, the")
            out.append(f"  limit is reached at about {full_at:,.0f} accounts"
                       + (f", which at {level:,.0f} a day is about "
                          f"{max(0.0, (full_at - len(people)) / level):.0f} day(s) away."
                          if level > 0 and full_at > len(people) else " (it is there now)."))
            out.append("  Past it, channels wait for a slot (Pro and Studio first). Run this at a busy")
            out.append("  hour: the share of accounts that are live moves through the day.")
    return out


def main(argv: list[str]) -> int:
    from config.settings import settings
    tz_name, days = OWNER_TZ, 21
    for a in argv:
        if a.startswith("--tz="):
            tz_name = a.split("=", 1)[1]
        elif a.startswith("--days="):
            try:
                days = max(3, min(120, int(a.split("=", 1)[1])))
            except ValueError:
                pass
    store = pathlib.Path(settings.local_storage_path)
    if not store.is_absolute():
        store = ROOT / store
    try:
        users = json.loads((store / "users.json").read_text())
    except (OSError, ValueError) as exc:
        print(f"The accounts file could not be read ({type(exc).__name__}). Run this as root, in /opt/highlightz.")
        return 1
    now = time.time()
    limit = int(getattr(settings, "max_concurrent_streams", 0) or 0)
    for row in report(users if isinstance(users, list) else [], now, tz_name, days,
                      live=live_now(store, now), live_limit=limit or None):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
