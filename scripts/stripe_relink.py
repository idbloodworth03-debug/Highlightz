"""Link live Stripe subscriptions back to the accounts that paid for them.

Every checkout minted a fresh Stripe customer because no customer id was ever
stored locally, so people paid and the app never knew. This walks Stripe's LIVE
subscriptions, resolves each to an account via the subscription's metadata
user_id, and writes back the customer id, status and tier.

DRY RUN BY DEFAULT — prints what it would change and touches nothing. Pass
--apply to write.

    /opt/highlightz/venv/bin/python scripts/stripe_relink.py
    /opt/highlightz/venv/bin/python scripts/stripe_relink.py --apply
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings          # noqa: E402
from src.auth import users as user_store      # noqa: E402
from src.billing import plans                 # noqa: E402

APPLY = "--apply" in sys.argv

if not settings.stripe_secret_key:
    sys.exit("STRIPE_SECRET_KEY is not set — nothing to do.")

import stripe                                  # noqa: E402

_raw = stripe.StripeClient(settings.stripe_secret_key)
client = getattr(_raw, "v1", _raw)

LIVE = ("active", "trialing", "past_due")


def g(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def as_id(x) -> str:
    if not x:
        return ""
    return x if isinstance(x, str) else str(g(x, "id", "") or "")


def as_list(x) -> list:
    if not x:
        return []
    return x if isinstance(x, list) else list(g(x, "data", []) or [])


def plan_for(price_id: str) -> str | None:
    if not price_id:
        return None
    if price_id == settings.stripe_price_id_starter:
        return "starter"
    if price_id in (settings.stripe_price_id_pro, settings.stripe_price_id):
        return "pro"
    return None


subs = as_list(client.subscriptions.list(params={"status": "all", "limit": 100}))
by_id = {u["id"]: u for u in user_store.get_all()}

print(f"{'DRY RUN — nothing will be written' if not APPLY else 'APPLYING CHANGES'}\n")

planned, skipped = [], []
for s in subs:
    status = str(g(s, "status", "") or "")
    if status not in LIVE:
        continue
    uid = str(g(g(s, "metadata"), "user_id", "") or "")
    cust = as_id(g(s, "customer"))
    items = as_list(g(s, "items"))
    price = as_id(g(items[0], "price")) if items else ""
    tier = plan_for(price)

    user = by_id.get(uid)
    if not user:
        skipped.append((as_id(s), f"metadata user_id {uid or '(none)'} matches no account"))
        continue
    existing = user.get("stripe_customer_id")
    if existing and existing != cust:
        # Do NOT overwrite: this account already points at a different customer,
        # and picking the wrong one silently moves their billing.
        skipped.append((as_id(s), f"{user['username']} already linked to {existing}"))
        continue

    changes = {}
    if not existing:
        changes["stripe_customer_id"] = cust
    if user.get("subscription_status") != "active":
        changes["subscription_status"] = ("active", user.get("subscription_status"))
    if tier and user.get("plan") != tier:
        changes["plan"] = (tier, user.get("plan"))
    # A paying customer must not stay filed as a comp, or the revenue figures
    # keep reporting them as a gift.
    if user.get("plan_source") == "granted":
        changes["plan_source"] = ("stripe", "granted")
    if not changes:
        continue
    planned.append((user, cust, tier, changes, as_id(s)))

for user, cust, tier, changes, sid in planned:
    before = plans.get_plan(user)
    print(f"  {user['username']}  ({sid})")
    for k, v in changes.items():
        if isinstance(v, tuple):
            print(f"      {k}: {v[1] or '(unset)'} -> {v[0]}")
        else:
            print(f"      {k}: (unset) -> {v}")
    print(f"      effective plan: {before} -> {tier or 'pro (legacy price)'}")

if skipped:
    print("\n  skipped:")
    for sid, why in skipped:
        print(f"      {sid}: {why}")

if not planned:
    print("  nothing to relink — every live subscription is already on its account")
    sys.exit(0)

if not APPLY:
    print(f"\n{len(planned)} account(s) would change. Re-run with --apply to write.")
    sys.exit(0)

for user, cust, tier, changes, sid in planned:
    uid = user["id"]
    user_store.update_subscription(uid, cust, "active")
    if tier:
        user_store.set_plan(uid, tier)
    if changes.get("plan_source"):
        users = user_store._load()
        for u in users:
            if u["id"] == uid:
                u["plan_source"] = "stripe"
                break
        user_store._save(users)
    print(f"  linked {user['username']} -> {cust}")

print(f"\n{len(planned)} account(s) updated. Restart is not required — the store "
      f"is read per request.")
