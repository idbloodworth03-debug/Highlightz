"""What does STRIPE think is going on, and does it match our records?

Read-only. Lists every subscription Stripe holds, flags customers carrying more
than one (the double-charge cases), and cross-references against users.json to
find subscriptions that were never linked to the account that paid for them.

Run on the box that has the live key:
    /opt/highlightz/venv/bin/python /opt/highlightz/scripts/stripe_audit.py
"""
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings          # noqa: E402
from src.auth import users as user_store      # noqa: E402

if not settings.stripe_secret_key:
    sys.exit("STRIPE_SECRET_KEY is not set — nothing to audit.")

import stripe                                  # noqa: E402

client = stripe.StripeClient(settings.stripe_secret_key)

LIVE = ("active", "trialing", "past_due", "incomplete")


def g(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


# ── everything Stripe holds ──────────────────────────────────────────────────
subs, cursor = [], None
while True:
    params = {"status": "all", "limit": 100}
    if cursor:
        params["starting_after"] = cursor
    page = client.subscriptions.list(params=params)
    data = page.data if hasattr(page, "data") else []
    subs.extend(data)
    if not (g(page, "has_more") and data):
        break
    cursor = g(data[-1], "id")

print(f"key: {'LIVE' if settings.stripe_secret_key.startswith('sk_live') else 'TEST'} mode")
print(f"{len(subs)} subscription(s) in Stripe\n")

price_name = {settings.stripe_price_id_starter: "starter",
              settings.stripe_price_id_pro: "pro",
              settings.stripe_price_id: "legacy"}

by_customer = defaultdict(list)
rows = []
for s in subs:
    sid    = g(s, "id", "")
    status = g(s, "status", "")
    cust   = g(s, "customer", "")
    if isinstance(cust, dict):
        cust = cust.get("id", "")
    meta   = g(s, "metadata") or {}
    uid    = (meta.get("user_id") if isinstance(meta, dict) else None) or ""
    items  = g(s, "items") or {}
    data   = (items.get("data") if isinstance(items, dict) else g(items, "data")) or []
    price  = ""
    if data:
        p = g(data[0], "price")
        price = (p.get("id") if isinstance(p, dict) else p) or ""
    rows.append({"id": sid, "status": status, "customer": cust, "user_id": uid,
                 "tier": price_name.get(price, price[:18] or "?")})
    if status in LIVE:
        by_customer[cust].append(sid)

hdr = f"{'subscription':<30}{'status':<12}{'customer':<22}{'tier':<10}{'metadata user_id'}"
print(hdr); print("-" * len(hdr))
for r in sorted(rows, key=lambda r: (r["status"] != "active", r["customer"])):
    print(f"{r['id']:<30}{r['status']:<12}{r['customer']:<22}{r['tier']:<10}"
          f"{r['user_id'] or '*** MISSING ***'}")

# ── the two failure modes ────────────────────────────────────────────────────
dupes = {c: ids for c, ids in by_customer.items() if len(ids) > 1}
print(f"\n{'=' * 72}\nCUSTOMERS BEING BILLED MORE THAN ONCE\n{'=' * 72}")
if dupes:
    for cust, ids in dupes.items():
        print(f"  {cust}: {len(ids)} live subscriptions -> {', '.join(ids)}")
    print("\n  These need cancelling and refunding BY HAND in the Stripe dashboard.")
else:
    print("  none")

users = user_store.get_all()
by_id = {u["id"]: u for u in users}
linked = {u.get("stripe_customer_id") for u in users if u.get("stripe_customer_id")}

print(f"\n{'=' * 72}\nLIVE SUBSCRIPTIONS NOT LINKED TO ANY ACCOUNT\n{'=' * 72}")
print("  (a paying customer the app does not know is paying — this is what a")
print("   missed webhook looks like, and why checkout keeps minting new ones)\n")
orphans = 0
for r in rows:
    if r["status"] not in LIVE:
        continue
    if r["customer"] in linked:
        continue
    orphans += 1
    who = by_id.get(r["user_id"])
    email = ""
    try:
        c = client.customers.retrieve(r["customer"])
        email = g(c, "email") or ""
    except Exception as exc:
        email = f"(lookup failed: {exc})"
    print(f"  {r['id']}  {r['status']:<10} {r['customer']}")
    print(f"      tier={r['tier']}  email={email}")
    print(f"      metadata user_id={r['user_id'] or '(none)'}"
          f"  -> account: {who['username'] if who else 'NOT FOUND in users.json'}")
if not orphans:
    print("  none — every live subscription is linked to an account")

print(f"\n{'=' * 72}")
print(f"Stripe live subscriptions : {sum(1 for r in rows if r['status'] in LIVE)}")
print(f"Accounts with a customer  : {len(linked)}")
print(f"Unlinked (orphaned)       : {orphans}")
