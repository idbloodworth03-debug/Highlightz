"""Where do sign-outs come from? Read-only; reads nginx's access logs.

    python3 scripts/signout_check.py [log directory, default /var/log/nginx]

Prints counts only: never an address, a name, or a path with a token in it.

Owner, 2026-11-21: "Users are reporting they keep getting signed out". The
first look (the audit that day) found that the site TOLD signed-in people they
were signed out (/login showed them the form; the public pages said "Sign in")
and that a sign-in lasted only 7 days. Both are fixed. It also found most
"signed out" moments came within 10 minutes of a dashboard load, which that
first look could not explain, because three different things look alike in a
log:

  * really signed out: the next dashboard comes only after a sign-in;
  * not this person at all, or another tab or app on the same phone: the same
    address and browser load the dashboard again without signing in (iPhones
    on one version all send the same browser string, and a phone network puts
    many people behind one address);
  * a dashboard download cut short (the app switched away mid-load): nginx
    logs a 200 with however many bytes it got out, which is neither page's size.

So each signed-out-looking moment is sorted by what it was (the landing page
by its size, the sign-in page, or a cut-off download) and by what happened
next on the same address and browser.
"""

from __future__ import annotations

import glob
import gzip
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

LINE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) (\d+) "[^"]*" "([^"]*)"')
DASH_MIN = 150_000        # "/" this big is the dashboard; the landing page is far smaller
NEAR = 4_000              # bytes either side of the landing page's usual size
LOOK_AHEAD = 1800         # seconds: what happened next
GAPS = [(600, "under 10 min"), (3600, "10 min - 1 h"), (86400, "1 h - 1 day"),
        (6 * 86400, "1 - 6 days"), (8 * 86400, "6 - 8 days"), (10 ** 12, "over 8 days")]
NEXT = ("dashboard again, no sign-in", "signed in again", "did not come back")
KIND_NAME = {"landing": "landing page", "login": "sign-in page", "cut": "cut-off download"}


def family(ua: str) -> str | None:
    u = ua.lower()
    if any(k in u for k in ("bytedance", "musical_ly", "tiktok")): return "TikTok app"
    if "instagram" in u: return "Instagram app"
    if "fban" in u or "fbav" in u or "fb_iab" in u: return "Facebook app"
    if any(k in u for k in ("snapchat", "twitter", "discord", "reddit", "linkedinapp")): return "other app"
    if "iphone" in u or "ipad" in u:
        return "iPhone Chrome" if "crios" in u else "iPhone Safari"
    if "android" in u: return "Android"
    if any(k in u for k in ("bot", "crawl", "spider", "curl", "python", "monitor", "wget", "http")): return None
    return "computer"


def _first_time(f: str) -> float:
    op = gzip.open if f.endswith(".gz") else open
    try:
        with op(f, "rt", errors="replace") as fh:
            for ln in fh:
                m = LINE.match(ln)
                if m:
                    return datetime.strptime(m.group(2), "%d/%b/%Y:%H:%M:%S %z").timestamp()
    except (OSError, ValueError):
        pass
    return float("inf")


def read(logdir: str):
    """The log lines that matter here, oldest file first, one line at a time.

    STREAMED, AND FILTERED BEFORE PARSING. Production's logs hold millions of
    lines, almost all bots and media, on the box that runs the site with no
    swap: holding them all would take gigabytes. Only the few addresses this
    looks at are parsed at all."""
    files = sorted(glob.glob(logdir.rstrip("/") + "/access.log*"), key=_first_time)
    for f in files:
        op = gzip.open if f.endswith(".gz") else open
        try:
            with op(f, "rt", errors="replace") as fh:
                for ln in fh:
                    if not any(k in ln for k in _WANTED):
                        yield None
                        continue
                    m = LINE.match(ln)
                    if not m:
                        continue
                    ip, ts, meth, path, st, size, ua = m.groups()
                    try:
                        t = datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z").timestamp()
                    except ValueError:
                        continue
                    yield (t, ip + "|" + ua, meth, path, int(st), int(size), ua)
        except OSError:
            continue


_WANTED = ('"GET / ', "/login", "/logout", "/auth/")


def main(logdir: str = "/var/log/nginx") -> int:
    err, oauth, outs = Counter(), Counter(), Counter()
    events = defaultdict(list)            # key -> [(t, what, family, size)]
    small, total, first, last = [], 0, None, None
    for row in read(logdir):
        total += 1
        if row is None:
            continue
        t, k, meth, path, st, size, ua = row
        first = t if first is None else min(first, t)
        last = t if last is None else max(last, t)
        p = path.split("?")[0]
        if p == "/login" and "error=" in path:
            err[re.sub(r".*error=([a-z_]+).*", r"\1", path)] += 1
        if p == "/logout":
            outs[meth] += 1
        if p in ("/auth/twitch", "/auth/kick"):
            oauth[p + " started"] += 1
        if p in ("/auth/twitch/callback", "/auth/kick/callback"):
            oauth[p[:-len("/callback")] + " came back"] += 1
        fam = family(ua)
        if fam is None:
            continue
        what = None
        if p == "/logout":
            what = "logout"
        elif p in ("/auth/twitch/callback", "/auth/kick/callback"):
            what = "back"
        elif meth == "GET" and st == 200 and p == "/":
            what = "dash" if size >= DASH_MIN else "small"
            if 1000 < size < DASH_MIN:
                small.append(size)
        elif meth == "GET" and st == 200 and p == "/login" and "error=" not in path:
            what = "login"
        if what:
            events[k].append((t, what, fam, size))
    if first is None:
        print("no log lines found in " + logdir)
        return 1
    print(f"== nginx logs cover {(last - first) / 86400:.1f} days, {total} requests")
    # The landing page's usual size: most small "/" answers are it, so the
    # middle one is. Anything else small is a dashboard download cut short.
    landing = statistics.median(small) if small else 0
    for k, ev in events.items():
        ev.sort(key=lambda e: e[0])
        events[k] = [(t, ("landing" if abs(size - landing) <= NEAR else "cut") if w == "small" else w, fam)
                     for t, w, fam, size in ev]

    print("\n== sign-in page shown with an error (why someone was sent there)")
    for name, n in err.most_common():
        print(f"   {n:6d}  {name}")
    print(f"\n== sign outs on purpose: POST /logout {outs['POST']}, GET /logout {outs['GET']}")
    print("== sign-in round trips: " + ", ".join(f"{k} {v}" for k, v in sorted(oauth.items())))

    gaps = defaultdict(Counter)
    after = defaultdict(Counter)          # (kind) -> what came next, for gaps under an hour
    dash_by, people = Counter(), defaultdict(set)
    for k, ev in events.items():
        last_dash, quit_ = None, False
        for i, (t, what, fam) in enumerate(ev):
            if what == "logout":
                quit_ = True
            elif what == "dash":
                last_dash, quit_ = t, False
                dash_by[fam] += 1
                people[fam].add(k)
            elif what in KIND_NAME and last_dash is not None and not quit_:
                gap = t - last_dash
                last_dash = None
                gaps[fam][next(name for lim, name in GAPS if gap < lim)] += 1
                if gap >= 3600:
                    continue
                nxt, signed = "did not come back", False
                for t2, w2, _ in ev[i + 1:]:
                    if t2 - t > LOOK_AHEAD or w2 == "logout":
                        break
                    if w2 == "back":
                        signed = True
                    elif w2 == "dash":
                        nxt = "signed in again" if signed else "dashboard again, no sign-in"
                        break
                after[KIND_NAME[what]][nxt] += 1

    print("\n== dashboard loads by browser (people = address + browser pairs)")
    for fam, n in dash_by.most_common():
        print(f"   {n:6d} loads  {len(people[fam]):5d} people  {fam}")
    print("\n== signed in, then shown the landing or sign-in page later WITHOUT signing out,")
    print("   by how long after their last dashboard load (same address + same browser)")
    print("   " + "browser".ljust(15) + "".join(name.rjust(14) for _, name in GAPS))
    for fam in sorted(gaps, key=lambda f: -sum(gaps[f].values())):
        print("   " + fam.ljust(15) + "".join(str(gaps[fam][name]).rjust(14) for _, name in GAPS))
    print(f"\n== the ones within an hour: what they were, and what happened in the next {LOOK_AHEAD // 60} min")
    print(f"   (landing page = a '/' of about {int(landing)} bytes; cut-off download = any other size)")
    print("   " + "".ljust(18) + "".join(n.rjust(30) for n in NEXT))
    for kind in ("landing page", "sign-in page", "cut-off download"):
        print("   " + kind.ljust(18) + "".join(str(after[kind][n]).rjust(30) for n in NEXT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/var/log/nginx"))
