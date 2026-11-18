"""What each account costs, free against paying, and what is left: the margins.

    cd /opt/highlightz && venv/bin/python scripts/unit_costs.py
    cd /opt/highlightz && venv/bin/python scripts/unit_costs.py --server=112 --other=20

Owner, 2026-11-18: "figure out my per person cost currently and do it free vs
paying users so i can see my margins".

WHAT COSTS MONEY HERE. One server, a card processor, and little else. The
server exists to watch live channels: memory, processor and the app's loop
all grow with how many are live at once (scripts/stream_cost.py measures
that), and an account with nothing live costs next to nothing. So the bill is
shared out BY LIVE CHANNELS: an account's cost is its share of the channels
live right now, times the bill.

WHERE THE NUMBERS COME FROM. Everything is read from this server's own files,
the same ones the site runs on:
  * who is paying, and how much: the accounts file, by the SAME rule the
    admin page's revenue figure uses (an active subscription with a Stripe
    customer that was not a gift), priced at what Stripe actually charges
    (plans.billed_monthly);
  * who is live right now: the note the site writes every 30 seconds of the
    channels holding a live slot (headroom.json). streams.json is NOT used
    for this: its statuses are only as fresh as the last save;
  * clips caught in the last 30 days (the ledger) and video held (the clip
    files), shown beside it as a second opinion on the same split.

WHAT IT IS TOLD RATHER THAN READS, printed at the top every time:
  * the server bill. This cannot be read from the machine. The default is
    what the owner said DigitalOcean bills them on 2026-11-18 ("112$ bill
    digital ocean"; it is also the list price for that hardware: 8 vCPU
    Premium AMD, 16 GB, 320 GB). It goes stale the day the server is resized:
    `--server=` sets it, and the output always says where the figure is from;
  * `--other=`: anything else paid monthly to run the site (backups, email,
    the domain). Default 0. It is shared out the same way as the server;
  * the card processor's cut: 2.9% + 30 cents a charge, plus Stripe Billing's
    0.7% (`--stripe-pct=`, `--stripe-fixed=`).

WHAT IT IS NOT. One moment's split, not a month's: run it at a busy hour (the
server is sized for the busy hour, so that is the right moment) and again at
another to see how steady it is. It leaves out the owner's time, tax, refunds
and chargebacks.

Read-only. Prints counts, money and, for the heaviest accounts, the account's
NAME. Never an email address, an id or a Stripe id.
"""
import collections
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GB = 1073741824
SERVER_DEFAULT = 112.0          # the owner's DigitalOcean bill as given on 2026-11-18
SERVER_DEFAULT_AS_OF = "2026-11-18"
STRIPE_PCT_DEFAULT = 3.6        # 2.9% card + 0.7% Stripe Billing
STRIPE_FIXED_DEFAULT = 0.30
SNAPSHOT_MAX_AGE_S = 600.0      # the live note is written every 30 s

# Mutually exclusive, the same partition /admin/overview makes, plus staff.
SEGMENTS = ("paying", "free", "trialing", "comped", "grace", "staff")
LABEL = {"paying": "PAYING", "free": "FREE", "trialing": "On trial (not charged yet)",
         "comped": "Gifted a plan", "grace": "Card failing", "staff": "Staff (admins)"}


def segment_of(user: dict) -> str:
    """Which one group an account is in. THE SAME RULE AS THE ADMIN PAGE
    (api.admin_overview), so the revenue here is the revenue there: paying is
    an active subscription with a Stripe customer that was not granted."""
    from src.billing import plans
    if user.get("is_admin"):
        return "staff"
    status = user.get("subscription_status")
    if status == "trialing":
        return "trialing"
    if status in plans.GRACE_STATUSES:
        return "grace"
    if status != "active":
        return "free"
    if not user.get("stripe_customer_id") or user.get("plan_source") == "granted":
        return "comped"
    return "paying"


def tier_of(user: dict) -> str:
    """For a paying account: the tier it is on, or "legacy" for the old price."""
    from config.settings import settings
    from src.billing import plans
    stored = user.get("plan")
    on_legacy = bool(settings.stripe_price_id
                     and user.get("stripe_price_id") == settings.stripe_price_id)
    return stored if stored in plans.PAID_PLANS and not on_legacy else "legacy"


def stripe_fee(price: float, pct: float, fixed: float) -> float:
    return round(price * pct / 100.0 + fixed, 4) if price > 0 else 0.0


def live_now(store: pathlib.Path, now: float) -> tuple[dict, float | None]:
    """({account id: channels live right now}, how old that note is in seconds).
    None when there is no note to read."""
    try:
        got = json.loads((store / "headroom.json").read_text())
        keys, at = got.get("keys") or [], float(got.get("at") or 0)
    except (OSError, ValueError, TypeError):
        return {}, None
    out: dict = collections.Counter()
    for key in keys:
        uid, sep, _channel = str(key).partition(":")
        if sep and uid:
            out[uid] += 1
    return dict(out), max(0.0, now - at)


def listed(store: pathlib.Path) -> tuple[dict, dict]:
    """({account id: channels listed and not paused}, {account id: paused})."""
    try:
        records = json.loads((store / "streams.json").read_text())
    except (OSError, ValueError):
        return {}, {}
    on, paused = collections.Counter(), collections.Counter()
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict) or not rec.get("user_id") or not rec.get("channel"):
            continue
        (paused if rec.get("status") == "paused" else on)[str(rec["user_id"])] += 1
    return dict(on), dict(paused)


def caught(store: pathlib.Path, days: float, now: float) -> dict:
    """{account id: clips the detector caught in the last `days`}."""
    out: dict = collections.Counter()
    since = now - days * 86400
    try:
        with open(store / "stream_stats.jsonl", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"caught"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("event") == "caught" and float(r.get("ts") or 0) >= since and r.get("user_id"):
                    out[str(r["user_id"])] += 1
    except OSError:
        pass
    return dict(out)


def video_held(store: pathlib.Path) -> dict:
    """{account id: bytes of clip video held for it}."""
    try:
        clips = json.loads((store / "clips.json").read_text())
    except (OSError, ValueError):
        return {}
    owner = {}
    for c in (clips.values() if isinstance(clips, dict) else clips if isinstance(clips, list) else []):
        if isinstance(c, dict) and c.get("id") and c.get("user_id"):
            owner[str(c["id"])] = str(c["user_id"])
    out: dict = collections.Counter()
    try:
        with os.scandir(store / "clipfiles") as it:
            for e in it:
                if e.name.endswith(".mp4") and e.is_file():
                    uid = owner.get(e.name[:-4])
                    if uid:
                        out[uid] += e.stat().st_size
    except OSError:
        pass
    return dict(out)


def build(users: list, live: dict, on: dict, paused: dict, clips: dict, video: dict,
          *, pct: float, fixed: float) -> dict:
    """One row per account, then one per group. No money is shared out here."""
    from src.billing import plans
    rows = []
    for u in users:
        uid = str(u.get("id") or "")
        seg = segment_of(u)
        price = float(plans.billed_monthly(u)) if seg == "paying" else 0.0
        rows.append({"name": str(u.get("username") or "?")[:28], "segment": seg,
                     "tier": tier_of(u) if seg == "paying" else "",
                     "price": price, "fee": stripe_fee(price, pct, fixed),
                     "live": int(live.get(uid, 0)), "listed": int(on.get(uid, 0)),
                     "paused": int(paused.get(uid, 0)), "clips": int(clips.get(uid, 0)),
                     "video": int(video.get(uid, 0))})
    groups = {}
    for seg in SEGMENTS:
        mine = [r for r in rows if r["segment"] == seg]
        groups[seg] = {
            "accounts": len(mine),
            "using": sum(1 for r in mine if r["live"] or r["listed"]),
            "live_accounts": sum(1 for r in mine if r["live"]),
            "live": sum(r["live"] for r in mine), "listed": sum(r["listed"] for r in mine),
            "paused": sum(r["paused"] for r in mine), "clips": sum(r["clips"] for r in mine),
            "video": sum(r["video"] for r in mine),
            "revenue": sum(r["price"] for r in mine), "fees": sum(r["fee"] for r in mine)}
    return {"rows": rows, "groups": groups}


def share_out(data: dict, infra: float) -> str:
    """Give every account and group its part of `infra`, by channels live now.
    With nothing live the split falls back to channels listed (said in the
    output), and with nothing listed either there is nothing to share.
    Returns what the split was made by."""
    rows, groups = data["rows"], data["groups"]
    basis = "live"
    total = sum(r["live"] for r in rows)
    if total <= 0:
        basis, total = "listed", sum(r["listed"] for r in rows)
    for r in rows:
        r["share"] = (r[basis] / total) if total > 0 else 0.0
        r["cost"] = r["share"] * infra
    for seg, g in groups.items():
        g["share"] = sum(r["share"] for r in rows if r["segment"] == seg)
        g["cost"] = g["share"] * infra
    data["basis"], data["basis_total"] = basis, total
    return basis


def money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < -0.004 else f"${max(0.0, x):,.2f}"


def pct_of(part: float, whole: float) -> str:
    return f"{100.0 * part / whole:.0f}%" if whole > 0 else "n/a"


def report(data: dict, *, server: float, other: float, pct: float, fixed: float,
           server_given: bool, age: float | None, days: float, top: int = 8) -> list[str]:
    rows, groups, basis = data["rows"], data["groups"], data["basis"]
    infra = server + other
    pay, free = groups["paying"], groups["free"]
    out = ["=" * 74, "WHAT THIS IS WORKED OUT FROM", "=" * 74]
    out.append(f"  server bill            : {money(server)} a month"
               + ("" if server_given else
                  f"   <- your DigitalOcean bill as you gave it on {SERVER_DEFAULT_AS_OF}."))
    if not server_given:
        out.append("                           If the server has been resized since, tell me the new figure.")
    out.append(f"  other monthly costs    : {money(other)}"
               + ("" if other else "   <- none given (backups, email, the domain would go here)"))
    out.append(f"  card processing        : {pct:g}% + {money(fixed)} of each charge")
    if basis == "live":
        out.append(f"  shared out by          : channels live right now ({data['basis_total']})"
                   + (f", noted {age:.0f}s ago" if age is not None else ""))
    else:
        out.append("  shared out by          : channels LISTED, because nothing is live right now"
                   if data["basis_total"] else
                   "  shared out by          : nothing: no channel is live or listed")
        out.append("                           (a weaker measure; run this again while streams are live)")
    if age is not None and age > SNAPSHOT_MAX_AGE_S:
        out.append(f"  THE LIVE NOTE IS {age / 60:.0f} MINUTES OLD: the site is not writing it. The split")
        out.append("  below is from then, not now.")

    out += ["", "=" * 74, "WHO IS ON THE SITE, AND WHAT THEY USE", "=" * 74,
            f"  {'':<28}{'accounts':>9}{'with a':>9}{'live':>6}{'share':>9}{'clips':>8}{'video':>8}",
            f"  {'':<28}{'':>9}{'channel':>9}{'now':>6}{'of load':>9}{f'{days:.0f}d':>8}{'held':>8}"]
    all_clips = sum(g["clips"] for g in groups.values())
    for seg in SEGMENTS:
        g = groups[seg]
        if not g["accounts"]:
            continue
        out.append(f"  {LABEL[seg]:<28}{g['accounts']:>9,}{g['using']:>9,}{g['live']:>6,}"
                   f"{pct_of(g['share'], 1.0):>9}{g['clips']:>8,}{g['video'] / GB:>7.1f}G")
    out.append(f"  (clips: paying caught {pct_of(pay['clips'], all_clips)} of them and free "
               f"{pct_of(free['clips'], all_clips)}; if that is far from the share of load,")
    out.append("   this moment is not typical and it is worth running again at another hour)")

    def per(amount: float, n: int) -> str:
        return money(amount / n) if n else "n/a"

    out += ["", "=" * 74, "FREE AGAINST PAYING", "=" * 74,
            f"  {'':<34}{'FREE':>16}{'PAYING':>16}",
            f"  {'accounts':<34}{free['accounts']:>16,}{pay['accounts']:>16,}",
            f"  {'  of them with a channel live now':<34}{free['live_accounts']:>16,}{pay['live_accounts']:>16,}",
            f"  {'their share of the bill, a month':<34}{money(free['cost']):>16}{money(pay['cost']):>16}",
            f"  {'card processing, a month':<34}{money(0):>16}{money(pay['fees']):>16}",
            f"  {'COST PER ACCOUNT':<34}{per(free['cost'], free['accounts']):>16}"
            f"{per(pay['cost'] + pay['fees'], pay['accounts']):>16}",
            f"  {'cost per account that is live now':<34}{per(free['cost'], free['live_accounts']):>16}"
            f"{per(pay['cost'] + pay['fees'], pay['live_accounts']):>16}",
            f"  {'what they pay, a month':<34}{money(0):>16}{money(pay['revenue']):>16}",
            f"  {'PAID PER ACCOUNT':<34}{money(0):>16}{per(pay['revenue'], pay['accounts']):>16}"]
    margin = pay["revenue"] - pay["fees"] - pay["cost"]
    out.append(f"  {'MARGIN':<34}{money(-free['cost']):>16}{money(margin):>16}"
               + (f"   ({pct_of(margin, pay['revenue'])} of what they pay)" if pay["revenue"] > 0 else ""))
    if pay["accounts"]:
        out.append(f"  Each paying account also carries {money(free['cost'] / pay['accounts'])} a month "
                   "of the free accounts' cost.")

    by_tier = collections.OrderedDict()
    for t in ("studio", "pro", "starter", "legacy"):
        mine = [r for r in rows if r["segment"] == "paying" and r["tier"] == t]
        if mine:
            by_tier[t] = mine
    if by_tier:
        out += ["", "=" * 74, "THE PAYING ACCOUNTS, BY PLAN  (per account, a month)", "=" * 74,
                f"  {'plan':<10}{'accounts':>9}{'live now':>10}{'pays':>10}{'costs':>10}{'margin':>10}{'':>3}"]
        for t, mine in by_tier.items():
            n = len(mine)
            rev = sum(r["price"] for r in mine)
            cost = sum(r["cost"] + r["fee"] for r in mine)
            out.append(f"  {t.capitalize():<10}{n:>9,}{sum(r['live'] for r in mine) / n:>10.1f}"
                       f"{money(rev / n):>10}{money(cost / n):>10}{money((rev - cost) / n):>10}"
                       f"   {pct_of(rev - cost, rev)}")

    total_rev = pay["revenue"]
    net = total_rev - pay["fees"] - infra
    out += ["", "=" * 74, "THE WHOLE SITE, A MONTH", "=" * 74,
            f"  coming in (paying accounts)       : {money(total_rev)}",
            f"  card processing                   : {money(-pay['fees'])}",
            f"  server and other costs            : {money(-infra)}"]
    for seg in SEGMENTS:
        g = groups[seg]
        if g["cost"] > 0.004:
            out.append(f"      of which {LABEL[seg].lower():<28}: {money(g['cost'])}  ({pct_of(g['share'], 1.0)})")
    out.append(f"  LEFT OVER                         : {money(net)}"
               + (f"   ({pct_of(net, total_rev)} of what comes in)" if total_rev > 0 else ""))
    if data["basis"] == "live" and data["basis_total"]:
        each = infra / data["basis_total"]
        out.append(f"  One channel live costs about {money(each)} a month of server at today's load,")
        out.append("  whoever's it is. That is the number to hold a plan's price against.")
        # The worst an account can do to its plan: every channel it is allowed,
        # live at once, all month. Today's cost per channel, so it moves with
        # the load; it is a guard on the prices, not a forecast.
        from src.billing import plans
        out += ["", "=" * 74,
                "IF AN ACCOUNT USED EVERYTHING ITS PLAN ALLOWS  (every channel live, all month)",
                "=" * 74,
                f"  {'plan':<10}{'channels':>9}{'pays':>10}{'would cost':>12}{'margin':>10}"]
        for name in ("free", "starter", "pro", "studio"):
            lim = plans.PLAN_LIMITS.get(name) or {}
            n, price = int(lim.get("max_streams") or 0), float(lim.get("price") or 0)
            cost = n * each + stripe_fee(price, pct, fixed)
            out.append(f"  {name.capitalize():<10}{n:>9}{money(price):>10}{money(cost):>12}"
                       f"{money(price - cost):>10}")

    heavy = sorted((r for r in rows if r["cost"] > 0), key=lambda r: -(r["cost"] + r["fee"]))[:top]
    if heavy:
        out += ["", "=" * 74, "THE ACCOUNTS THAT COST THE MOST RIGHT NOW  (names only)", "=" * 74,
                f"  {'account':<28}{'group':<14}{'live':>5}{'costs':>10}{'pays':>10}{'margin':>10}"]
        for r in heavy:
            what = (r["tier"].capitalize() if r["segment"] == "paying" else
                    {"free": "Free", "trialing": "Trial", "comped": "Gifted", "grace": "Card failing",
                     "staff": "Staff"}[r["segment"]])
            cost = r["cost"] + r["fee"]
            out.append(f"  {r['name']:<28}{what:<14}{r['live']:>5}{money(cost):>10}"
                       f"{money(r['price']):>10}{money(r['price'] - cost):>10}")
    out += ["", "Not in here: your own time, tax, refunds and chargebacks. One moment's",
            "split: the server is sized for the busy hour, so a busy hour is the one to read."]
    return out


def main(argv: list[str], store: pathlib.Path | None = None, users: list | None = None,
         now: float | None = None) -> int:
    server, other = SERVER_DEFAULT, 0.0
    pct, fixed, days = STRIPE_PCT_DEFAULT, STRIPE_FIXED_DEFAULT, 30.0
    server_given = False
    for a in argv:
        key, _, value = a.partition("=")
        try:
            num = float(value)
        except ValueError:
            print(f"{a}: that is not a number. NOTHING WAS WORKED OUT.")
            return 1
        if num < 0:
            print(f"{a}: a cost cannot be negative. NOTHING WAS WORKED OUT.")
            return 1
        if key == "--server":
            server, server_given = num, True
        elif key == "--other":
            other = num
        elif key == "--stripe-pct":
            pct = num
        elif key == "--stripe-fixed":
            fixed = num
        elif key == "--days":
            days = max(1.0, num)
        else:
            print(f"{key}: not something this takes (--server= --other= --stripe-pct= "
                  "--stripe-fixed= --days=). NOTHING WAS WORKED OUT.")
            return 1
    now = time.time() if now is None else now
    if store is None:
        from config.settings import settings
        store = pathlib.Path(settings.local_storage_path)
        if not store.is_absolute():
            store = ROOT / store
    if users is None:
        from src.auth import users as user_store
        users = user_store.get_all()
    if not users:
        print("No accounts could be read from here. Run this in /opt/highlightz, as root.")
        return 1
    live, age = live_now(store, now)
    on, paused = listed(store)
    data = build(users, live, on, paused, caught(store, days, now), video_held(store),
                 pct=pct, fixed=fixed)
    share_out(data, server + other)
    for line in report(data, server=server, other=other, pct=pct, fixed=fixed,
                       server_given=server_given, age=age, days=days):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
