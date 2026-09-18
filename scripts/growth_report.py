"""Who your users are and where they stop, from the real stores. Read-only.

    /opt/highlightz/venv/bin/python scripts/growth_report.py

WHY THIS EXISTS. Growth advice that is not built on the actual accounts is
guesswork, and the numbers that decide what to do next are spread over four
files: users.json (who and what they pay), streams.json (what they monitor),
clips.json (what the product delivered), funnel.json (where visitors stop).

AGGREGATES ONLY. No email, no Twitch login, no channel name belonging to a
single account leaves this script — the point is the shape of the base, and a
report you can paste somewhere is worth more than one you cannot.

THE QUESTION IT IS REALLY ANSWERING: are these people clipping THEMSELVES or
clipping OTHER channels? The whole positioning turns on it, and the answer is
in whether a user's monitored channels match their own Twitch login.
"""
import collections
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                      # noqa: E402

ROOT = pathlib.Path(settings.local_storage_path)
NOW = time.time()
DAY = 86400.0


def load(name, default):
    try:
        return json.loads((ROOT / name).read_text())
    except Exception:
        return default


def pct(a, b):
    return f"{(100.0 * a / b):.0f}%" if b else "n/a"


def ago(ts):
    if not ts:
        return "never"
    d = (NOW - float(ts)) / DAY
    return f"{d:.0f}d ago" if d >= 1 else "today"


users = load("users.json", [])
clips = load("clips.json", [])
streams = load("streams.json", {})
funnel = load("funnel.json", {})

if isinstance(streams, dict):
    streams = list(streams.values())

print("=" * 62)
print("HIGHLIGHTZ GROWTH REPORT")
print("=" * 62)

# ── accounts ────────────────────────────────────────────────────────────────
admins = [u for u in users if u.get("is_admin")]
real = [u for u in users if not u.get("is_admin")]
paying = [u for u in real if u.get("subscription_status") in ("active", "trialing")]
by_plan = collections.Counter(u.get("plan") or "free" for u in paying)
by_status = collections.Counter(u.get("subscription_status") or "none" for u in real)

print(f"\nACCOUNTS ({len(real)} real, {len(admins)} admin)")
for s, n in by_status.most_common():
    print(f"  {s:<12} {n}")
print(f"  paying       {len(paying)}  ({', '.join(f'{k} {v}' for k, v in by_plan.most_common()) or 'none'})")

mrr = 0
for u in paying:
    p = (u.get("plan") or "").lower()
    mrr += 25 if p == "pro" else 10 if p == "starter" else 15   # legacy = 15
print(f"  MRR          ${mrr}")

# ── signup and activity over time ───────────────────────────────────────────
def within(days, key):
    return sum(1 for u in real if (NOW - float(u.get(key) or 0)) <= days * DAY)


print("\nSIGNUPS")
for d in (7, 30, 90):
    print(f"  last {d:>2}d     {within(d, 'created_at')}")

print("\nSTILL AROUND (last completed sign-in)")
for d in (7, 30):
    print(f"  last {d:>2}d     {within(d, 'last_login_at')}")
never = sum(1 for u in real if not u.get("last_login_at"))
print(f"  never since   {never}")

# ── activation: did the product ever do anything for them? ──────────────────
with_stream = {s.get("user_id") for s in streams if s.get("user_id")}
clips_by_user = collections.Counter(c.get("user_id") for c in clips if c.get("user_id"))
approved_by_user = collections.Counter(
    c.get("user_id") for c in clips if c.get("user_id") and c.get("status") == "approved")

print(f"\nACTIVATION (of {len(real)} real accounts)")
print(f"  added a channel   {len(with_stream & {u['id'] for u in real}):>4}  {pct(len(with_stream & {u['id'] for u in real}), len(real))}")
got = sum(1 for u in real if clips_by_user.get(u['id']))
kept = sum(1 for u in real if approved_by_user.get(u['id']))
print(f"  got a clip        {got:>4}  {pct(got, len(real))}")
print(f"  kept a clip       {kept:>4}  {pct(kept, len(real))}")

# ── the positioning question ────────────────────────────────────────────────
own, other, mixed, unknown = 0, 0, 0, 0
for u in real:
    login = (u.get("twitch_login") or u.get("kick_slug") or "").lower()
    mine = {(s.get("channel") or "").lower() for s in streams if s.get("user_id") == u["id"]}
    if not mine:
        continue
    if not login:
        unknown += 1
    elif mine == {login}:
        own += 1
    elif login in mine:
        mixed += 1
    else:
        other += 1
print("\nWHO ARE THEY? (accounts that monitor at least one channel)")
print(f"  only their own channel      {own}")
print(f"  their own + others          {mixed}")
print(f"  only OTHER channels         {other}   <- clippers")
print(f"  could not tell              {unknown}")

# ── clips ───────────────────────────────────────────────────────────────────
st = collections.Counter(c.get("status") or "?" for c in clips)
judged = st.get("approved", 0) + st.get("rejected", 0)
vod = sum(1 for c in clips if c.get("is_vod_moment"))
kick = sum(1 for c in clips if c.get("platform") == "kick")
print(f"\nCLIPS ({len(clips)} held)")
for s, n in st.most_common():
    print(f"  {s:<12} {n}")
print(f"  keep rate     {pct(st.get('approved', 0), judged)} of {judged} judged")
print(f"  vod moments   {vod}   kick {kick}")

if clips_by_user:
    top = clips_by_user.most_common(5)
    print("  clips per account (top 5): " + ", ".join(str(n) for _, n in top))

# ── monitored channels right now ────────────────────────────────────────────
print(f"\nMONITORED CHANNELS ({len(streams)} registered)")
plat = collections.Counter(s.get("platform") or "twitch" for s in streams)
print("  " + ", ".join(f"{k} {v}" for k, v in plat.most_common()))
live_cap = max(1, settings.max_concurrent_streams)
print(f"  live slots on this box: {live_cap}")
per_user = collections.Counter(s.get("user_id") for s in streams)
if per_user:
    print(f"  busiest account has {per_user.most_common(1)[0][1]} channels registered")

# ── the funnel ──────────────────────────────────────────────────────────────
print("\nFUNNEL (all time)")
try:
    from src.dashboard.funnel import STEPS
    # {"days": {"YYYY-MM-DD": {step: count}}, "once": {step: [user ids]}}
    totals = {k: 0 for k, _l, _h in STEPS}
    for _day, row in ((funnel or {}).get("days") or {}).items():
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if k in totals and isinstance(v, (int, float)):
                totals[k] += int(v)
    # The two "first ever" steps also keep the set of accounts credited, which
    # survives the 180-day count window. Prefer it where it is bigger.
    for k, ids in ((funnel or {}).get("once") or {}).items():
        if k in totals and isinstance(ids, list):
            totals[k] = max(totals[k], len(ids))
    prev = None
    for k, label, _h in STEPS:
        n = totals.get(k, 0)
        drop = ""
        # Only when it reads as a real conversion. Steps were added to the
        # tracker at different times, so an early one can legitimately hold a
        # smaller number than the step after it, and printing "3550%" there
        # would be worse than printing nothing.
        if prev is not None and prev[1] and n <= prev[1]:
            drop = f"   {pct(n, prev[1])} of {prev[0]}"
        print(f"  {label:<20} {n:>6}{drop}")
        if k != "returning":
            prev = (label, n)
    print("  (counts start when each step was added to the tracker, so the")
    print("   early rows under-report; the last two are per account, all time)")
except Exception as exc:
    print(f"  (could not read: {exc})")

print("\n" + "=" * 62)
print("Paste this back. Nothing here identifies a single user.")
print("=" * 62)
