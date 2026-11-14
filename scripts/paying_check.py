"""Why does the admin page's Paying number differ from Stripe's?

The admin page counts an ACCOUNT as paying when the app's own record says so
(api._counts_as_paying: subscription marked active, a Stripe customer attached,
not granted by an admin). Stripe counts CUSTOMERS it is charging. Those are two
different lists, and nothing compares them by name. This does: every account
the admin page counts, next to what Stripe says about that account's customer,
then everybody Stripe is charging that the admin page does not count.

    /opt/highlightz/venv/bin/python scripts/paying_check.py

Read-only: it asks Stripe and reads users.json; it changes neither. It prints
account names, never an email address, and only the last six characters of a
Stripe id.

WHAT IT CAN FIND, each of which makes the two numbers disagree:
  GONE         the account's customer does not exist in this Stripe account
               (deleted in the dashboard, or made in test mode). The hourly
               sweep cannot ask about a customer that is not there, so it
               leaves the account as it was: "active", for ever.
  NOT ACTIVE   Stripe has the customer but no live subscription for it.
  FREE         the subscription is active but the last invoice was $0 (a 100%
               off code, a $0 price): a subscriber Stripe is not charging.
  PAUSED       active with collection paused.
  SHARED       two accounts point at the same Stripe customer: two here, one
               there.
  UNCOUNTED    Stripe is charging somebody the admin page leaves out (staff,
               a granted plan, a missed webhook, no linked account).
"""
import pathlib
import sys
import time
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

LIVE = ("active", "trialing", "past_due", "incomplete")
RANK = {s: len(LIVE) - i for i, s in enumerate(LIVE)}


def tail(ident: str) -> str:
    """The end of a Stripe id: enough to find it in the dashboard, no more."""
    ident = str(ident or "")
    return ("…" + ident[-6:]) if len(ident) > 6 else (ident or "-")


def best_sub(subs: list[dict]) -> dict | None:
    """The subscription that decides a customer's state: a live one over an
    ended one, and the most committed of the live ones (the app's own order,
    stripe_billing.authoritative_subscription)."""
    best, rank = None, -1
    for s in subs:
        r = RANK.get(s.get("status") or "", 0)
        if r > rank:
            best, rank = s, r
    return best


def verdict(customer: str, subs: list[dict]) -> tuple[str, str]:
    """(code, plain words) for one customer, from what Stripe returned.

    `customer` is "ok", "deleted", "missing" (Stripe says there is no such
    customer) or "error" (Stripe could not be asked)."""
    if customer == "error":
        return "UNKNOWN", "Stripe could not be asked about this customer just now"
    if customer in ("deleted", "missing"):
        why = "was deleted in Stripe" if customer == "deleted" else "does not exist in this Stripe account"
        return "GONE", f"the customer {why}, so nothing is being charged"
    s = best_sub(subs)
    if s is None:
        return "NOT ACTIVE", "the customer has no subscription at all"
    st = s.get("status") or "?"
    if st == "trialing":
        return "TRIAL", "on a free trial: not charged yet"
    if st != "active":
        return "NOT ACTIVE", f"the subscription is {st.replace('_', ' ')}"
    if s.get("paused"):
        return "PAUSED", "active, but collection is paused: not being charged"
    last = s.get("last_total")
    if last is not None and last <= 0:
        return "FREE", f"active, but the last invoice was ${last:.2f}: not being charged"
    words = "active"
    if last is not None:
        words += f", last invoice ${last:.2f}"
    if s.get("cancel_at_period_end"):
        words += ", set to cancel at the end of the period"
    return "PAYING", words


def compare(paying_accounts: list[dict], other_accounts: list[dict], stripe: dict) -> dict:
    """Line the admin page's list up against Stripe's.

    paying_accounts: [{"name", "customer", "plan", "monthly"}] the admin counts.
    other_accounts:  [{"name", "customer", "why"}] accounts with a customer the
                     admin does NOT count, and why not.
    stripe: {"customers": {cust: "ok"|"deleted"|"missing"|"error"},
             "subs": {cust: [sub, ...]}}
    """
    holders = defaultdict(list)
    for a in paying_accounts:
        holders[a["customer"]].append(a["name"])
    rows = []
    for a in paying_accounts:
        code, words = verdict(stripe["customers"].get(a["customer"], "error"),
                              stripe["subs"].get(a["customer"], []))
        shared = [n for n in holders[a["customer"]] if n != a["name"]]
        rows.append({**a, "code": code, "words": words, "shared_with": shared})

    charged = {c for c, subs in stripe["subs"].items()
               if verdict(stripe["customers"].get(c, "ok"), subs)[0] == "PAYING"}
    counted = {a["customer"] for a in paying_accounts}
    others = {a["customer"]: a for a in other_accounts}
    uncounted = []
    for c in sorted(charged - counted):
        o = others.get(c)
        uncounted.append({"customer": c, "name": o["name"] if o else "",
                          "why": o["why"] if o else "no account is linked to this customer"})

    # Each account that is on the admin's list and should not be on a list of
    # "people Stripe is charging", once: a second account on a shared customer
    # is extra even though the customer itself is paying.
    extra, seen = [], set()
    for r in rows:
        if r["code"] != "PAYING":
            extra.append((r["name"], r["code"]))
        elif r["customer"] in seen:
            extra.append((r["name"], "SHARED"))
        seen.add(r["customer"])
    return {"rows": rows, "uncounted": uncounted, "extra": extra,
            "admin": len(paying_accounts), "charged": len(charged),
            "active_subs": sum(1 for subs in stripe["subs"].values() for s in subs if s.get("status") == "active"),
            "active_customers": sum(1 for subs in stripe["subs"].values()
                                    if any(s.get("status") == "active" for s in subs))}


def report(r: dict, mode: str) -> list[str]:
    out = [f"Stripe key: {mode} mode", ""]
    out.append(f"THE ADMIN PAGE COUNTS {r['admin']} AS PAYING. What Stripe says about each:")
    out.append(f"  {'account':<22}{'plan':<9}{'app $/mo':>9}  {'Stripe':<11}detail")
    for row in sorted(r["rows"], key=lambda x: (x["code"] == "PAYING", x["name"].lower())):
        more = f"  [same customer as {', '.join(row['shared_with'])}]" if row["shared_with"] else ""
        out.append(f"  {row['name'][:21]:<22}{(row['plan'] or '-')[:8]:<9}{row['monthly']:>9.2f}  "
                   f"{row['code']:<11}{row['words']} (customer {tail(row['customer'])}){more}")
    out += ["", f"STRIPE: {r['active_subs']} active subscription(s) held by {r['active_customers']} customer(s); "
                f"{r['charged']} of those customers are being charged."]
    out.append("  Charged in Stripe but NOT counted on the admin page:")
    if r["uncounted"]:
        for u in r["uncounted"]:
            out.append(f"    customer {tail(u['customer'])}  {u['name'] or '(no account)':<22}{u['why']}")
    else:
        out.append("    nobody")
    out += ["", "THE DIFFERENCE"]
    out.append(f"  admin page {r['admin']}  -  not really being charged {len(r['extra'])}"
               f"  +  charged but uncounted {len(r['uncounted'])}  =  {r['admin'] - len(r['extra']) + len(r['uncounted'])}"
               f"   (Stripe is charging {r['charged']})")
    for name, code in r["extra"]:
        out.append(f"    on the admin page, not charged in Stripe: {name}  ({code})")
    if not r["extra"] and not r["uncounted"]:
        out.append("  The two lists are the same people. If Stripe's dashboard still shows a")
        out.append("  different number, it is counting something else (its totals lag by up")
        out.append("  to a day, and some of its tiles count subscriptions, not customers).")
    return out


# ── reading the app and Stripe (everything above is arithmetic on plain data) ─

def _g(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _aslist(x) -> list:
    if not x:
        return []
    return x if isinstance(x, list) else list(_g(x, "data", []) or [])


def _id(x) -> str:
    return x if isinstance(x, str) else str(_g(x, "id", "") or "")


def read_stripe(client, wanted: set[str]) -> dict:
    """Every subscription Stripe holds, by customer, and whether each customer
    in `wanted` exists."""
    import stripe
    subs, cursor = defaultdict(list), None
    while True:
        params = {"status": "all", "limit": 100, "expand": ["data.latest_invoice"]}
        if cursor:
            params["starting_after"] = cursor
        page = client.subscriptions.list(params=params)
        data = _aslist(page)
        for s in data:
            inv = _g(s, "latest_invoice")
            total = _g(inv, "total") if inv is not None and not isinstance(inv, str) else None
            subs[_id(_g(s, "customer"))].append({
                "id": _id(s), "status": str(_g(s, "status", "") or ""),
                "paused": bool(_g(s, "pause_collection")),
                "cancel_at_period_end": bool(_g(s, "cancel_at_period_end")),
                "last_total": (float(total) / 100) if isinstance(total, (int, float)) else None,
            })
        if not (_g(page, "has_more") and data):
            break
        cursor = _id(data[-1])
    customers = {}
    for c in wanted:
        if c in subs:
            customers[c] = "ok"         # it holds subscriptions, so it exists
            continue
        try:
            obj = client.customers.retrieve(c)
            customers[c] = "deleted" if _g(obj, "deleted") else "ok"
        except stripe.InvalidRequestError:
            customers[c] = "missing"
        except Exception:
            customers[c] = "error"
        time.sleep(0.05)
    return {"customers": customers, "subs": dict(subs)}


def read_accounts() -> tuple[list[dict], list[dict]]:
    """(the accounts the admin page counts as paying, the other accounts that
    have a Stripe customer and why each is not counted)."""
    from src.auth import users as user_store
    from src.billing import plans
    from src.dashboard import api

    def name(u):
        return u.get("twitch_login") or u.get("username") or u.get("id") or "?"

    paying, others = [], []
    for u in user_store.get_all():
        cust = u.get("stripe_customer_id")
        if not cust:
            continue
        if not u.get("is_admin") and api._counts_as_paying(u):
            paying.append({"name": name(u), "customer": cust, "plan": u.get("plan") or "legacy",
                           "monthly": plans.billed_monthly(u)})
            continue
        if u.get("is_admin"):
            why = "a staff account: never counted"
        elif u.get("plan_source") == "granted":
            why = "its plan was granted by an admin, so it is counted as comped"
        else:
            why = f"the app has it as '{u.get('subscription_status') or 'none'}', not active"
        others.append({"name": name(u), "customer": cust, "why": why})
    return paying, others


def main() -> int:
    from config.settings import settings
    if not settings.stripe_secret_key:
        print("STRIPE_SECRET_KEY is not set: nothing to compare against.")
        return 1
    import stripe
    raw = stripe.StripeClient(settings.stripe_secret_key)
    client = getattr(raw, "v1", raw)
    paying, others = read_accounts()
    wanted = {a["customer"] for a in paying} | {a["customer"] for a in others}
    try:
        state = read_stripe(client, wanted)
    except Exception as exc:
        print(f"Stripe could not be read ({type(exc).__name__}). Nothing was changed; run it again.")
        return 1
    mode = "LIVE" if settings.stripe_secret_key.startswith(("sk_live", "rk_live")) else "TEST"
    print("\n".join(report(compare(paying, others, state), mode)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
