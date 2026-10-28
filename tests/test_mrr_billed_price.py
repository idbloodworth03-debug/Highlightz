"""MRR counts what Stripe charges, not the tier a subscriber is entitled to.

THE BUG (2026-10-28, owner: "The MRR tracker is inaccurate ... unkn0wnballer
was grandfathered into a 15$ pro subscription"). Production's admin overview
read MRR $100 = 4 paying x $25, with no one on the legacy rate. unkn0wnballer
pays $15 on the pre-tiers price, but their record said plan 'pro': the webhook
maps the legacy price id to 'pro' (so a grandfathered subscriber keeps every
Pro feature) and stores it. The MRR priced each payer at their STORED TIER's
list price, so from the first subscription event after the tiers launched a
$15 subscriber counted as $25. Only a subscriber with no stored tier at all
was priced at plans.LEGACY_PRICE.

Entitlement and price are different questions. The webhook and the hourly
sweep now record what Stripe actually charges (stripe_billing.monthly_amount:
price x quantity per month, less a discount that is still running and is not
a one-off), and the MRR and the admin table's price read that. Before anything
is recorded, the old rule is the fallback.
"""

import asyncio
import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner

stripe = pytest.importorskip("stripe")

LEGACY = "price_legacy15"
NOW = time.time()


# ── what Stripe charges, read off Stripe's own objects ───────────────────────

def _sub(price: dict, *, quantity=1, discount=None, status="active"):
    """A real stripe-python Subscription, the shape production gets back."""
    body = {"id": "sub_x", "object": "subscription", "customer": "cus_x",
            "status": status, "trial_end": None,
            "items": {"object": "list", "data": [{
                "id": "si_x", "object": "subscription_item", "quantity": quantity,
                "price": {"object": "price", **price}}]}}
    if discount is not None:
        body["discount"] = discount
    return stripe.Subscription.construct_from(body, "sk_test_x")


def _monthly(price, **kw):
    from src.billing.stripe_billing import monthly_amount
    return monthly_amount(_sub(price, **kw), now=NOW)


MONTHLY = {"interval": "month", "interval_count": 1}


def test_the_legacy_price_is_fifteen_dollars_whatever_tier_it_maps_to():
    assert _monthly({"id": LEGACY, "unit_amount": 1500, "recurring": MONTHLY}) == 15.0


def test_list_prices_and_quantities_and_intervals_come_to_a_month():
    assert _monthly({"id": "p", "unit_amount": 2500, "recurring": MONTHLY}) == 25.0
    assert _monthly({"id": "p", "unit_amount": 1000, "recurring": MONTHLY}, quantity=2) == 20.0
    assert _monthly({"id": "p", "unit_amount": 24000,
                     "recurring": {"interval": "year", "interval_count": 1}}) == 20.0
    assert _monthly({"id": "p", "unit_amount": 3000,
                     "recurring": {"interval": "month", "interval_count": 3}}) == 10.0


def test_a_discount_that_never_ends_is_part_of_what_they_pay():
    """Grandfathering can be a coupon on the new price rather than the old
    price; either way $15 is what arrives."""
    forever = {"object": "discount", "end": None,
               "coupon": {"id": "GRANDFATHER", "object": "coupon",
                          "duration": "forever", "percent_off": 40}}
    assert _monthly({"id": "p", "unit_amount": 2500, "recurring": MONTHLY},
                    discount=forever) == 15.0
    flat = {"object": "discount", "end": None,
            "coupon": {"id": "TENOFF", "object": "coupon",
                       "duration": "forever", "amount_off": 1000}}
    assert _monthly({"id": "p", "unit_amount": 2500, "recurring": MONTHLY},
                    discount=flat) == 15.0


def test_a_one_off_or_finished_discount_is_not_the_run_rate():
    """The 50%-off first month is a one-off: MRR is what they pay from then on."""
    once = {"object": "discount", "end": None,
            "coupon": {"id": "HALF", "object": "coupon", "duration": "once",
                       "percent_off": 50}}
    assert _monthly({"id": "p", "unit_amount": 2500, "recurring": MONTHLY},
                    discount=once) == 25.0
    ended = {"object": "discount", "end": NOW - 60,
             "coupon": {"id": "Q", "object": "coupon", "duration": "repeating",
                        "percent_off": 50}}
    assert _monthly({"id": "p", "unit_amount": 2500, "recurring": MONTHLY},
                    discount=ended) == 25.0


def test_a_price_it_cannot_read_is_not_guessed():
    from src.billing.stripe_billing import monthly_amount
    assert monthly_amount(_sub({"id": "p"})) is None              # no amount on it
    assert monthly_amount({"items": {"data": [{"price": "price_only_an_id"}]}}) is None
    assert monthly_amount({}) is None


def test_the_sweep_lookup_returns_the_charge_with_the_plan(monkeypatch):
    from src.billing import stripe_billing as sb
    for k, v in (("stripe_secret_key", "sk_test_x"), ("stripe_price_id", LEGACY),
                 ("stripe_price_id_pro", "price_pro"),
                 ("stripe_price_id_starter", "price_starter")):
        monkeypatch.setattr(sb.settings, k, v)
    listing = stripe.ListObject.construct_from({"object": "list", "data": [
        _sub({"id": LEGACY, "unit_amount": 1500, "recurring": MONTHLY})]}, "sk_test_x")

    class _Client:
        class subscriptions:
            @staticmethod
            def list(params=None):
                return listing
    monkeypatch.setattr(sb, "_client", lambda: _Client())
    truth = asyncio.run(sb.authoritative_subscription("cus_x"))
    assert truth["plan"] == "pro", "the legacy price must still grandfather to Pro"
    assert truth["price_id"] == LEGACY
    assert truth["monthly"] == 15.0


# ── the sweep and the webhook record it ──────────────────────────────────────

@pytest.fixture
def store(monkeypatch, tmp_path):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    return user_store


def test_the_sweep_records_the_charge_without_calling_it_drift(store, monkeypatch):
    """unkn0wnballer's shape: active, stored as Pro, nothing recorded. The sweep
    asks Stripe and writes down $15, quietly: nothing the user sees changed, so
    there is no correction to broadcast."""
    from src.dashboard import api
    from src.billing import stripe_billing as sb
    u = store.upsert_twitch_user("tw_ub", "unkn0wnballer", "unkn0wnballer")
    store.update_subscription(u["id"], "cus_ub", "active")
    store.set_plan(u["id"], "pro")

    async def _truth(cust):
        return {"status": "active", "plan": "pro", "raw": "active", "trial_end": 0,
                "subscription": "sub_ub", "price_id": LEGACY, "monthly": 15.0}
    sent = []
    async def _bc(msg, **kw):
        sent.append(msg)
    monkeypatch.setattr(sb, "authoritative_subscription", _truth)
    monkeypatch.setattr(api, "broadcast", _bc)
    result = asyncio.run(api.reconcile_one_user(store.get_by_id(u["id"])))
    assert result["ok"] and result["drift"] == []
    assert sent == [], "recording a price is not an access change"
    after = store.get_by_id(u["id"])
    assert after["monthly_price"] == 15.0 and after["stripe_price_id"] == LEGACY
    assert after["plan"] == "pro", "their features must not change"


def test_the_sweep_does_not_record_a_price_for_an_ended_subscription(store, monkeypatch):
    from src.dashboard import api
    from src.billing import stripe_billing as sb
    u = store.upsert_twitch_user("tw_gone", "gone", "gone")
    store.update_subscription(u["id"], "cus_gone", "inactive")

    async def _truth(cust):
        return {"status": "inactive", "plan": None, "raw": "canceled", "trial_end": 0,
                "subscription": "sub_gone", "price_id": "price_pro", "monthly": 25.0}
    async def _bc(msg, **kw):
        return None
    monkeypatch.setattr(sb, "authoritative_subscription", _truth)
    monkeypatch.setattr(api, "broadcast", _bc)
    asyncio.run(api.reconcile_one_user(store.get_by_id(u["id"])))
    assert "monthly_price" not in store.get_by_id(u["id"])


# ── the admin MRR and price column read it ───────────────────────────────────

def _person(uid, **kw):
    return {"id": uid, "username": uid, "subscription_status": "active",
            "stripe_customer_id": "cus_" + uid, "created_at": NOW - 90 * 86400, **kw}


@pytest.fixture
def admin(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats as ss

    people = {}
    monkeypatch.setattr(user_store, "get_all", lambda: [dict(u) for u in people.values()])
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_CLIP_COUNTER_FILE", tmp_path / "counter.json")
    monkeypatch.setattr(api.settings, "stripe_price_id", LEGACY)
    people["boss"] = {"id": "boss", "username": "boss", "is_admin": True,
                      "subscription_status": "active", "created_at": NOW - 900 * 86400}
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "boss", "username": "boss", "is_admin": True,
         "subscription_status": "active"}).encode())).decode())
    c.people = people
    return c


def test_a_fifteen_dollar_subscriber_stored_as_pro_counts_as_fifteen(admin):
    """The production shape that read $100 for $90 of revenue."""
    admin.people["unkn0wnballer"] = _person("unkn0wnballer", plan="pro", grandfathered=True,
                                            stripe_price_id=LEGACY, monthly_price=15.0)
    admin.people["nova"] = _person("nova", plan="pro", stripe_price_id="price_pro",
                                   monthly_price=25.0)
    admin.people["kat"] = _person("kat", plan="starter", stripe_price_id="price_starter",
                                  monthly_price=10.0)
    d = admin.get("/admin/overview").json()
    assert d["mrr"] == 50, d["mrr"]
    assert d["mrr"] != 60, "the $15 subscriber is being priced at the Pro rate again"
    assert d["users"]["paying_by_tier"] == {"pro": 1, "starter": 1, "legacy": 1}
    assert d["mrr_unknown"] == 1
    assert d["users"]["by_plan"]["pro"] == 2, "a grandfathered subscriber keeps Pro"
    row = next(u for u in admin.get("/admin/users").json() if u["username"] == "unkn0wnballer")
    assert row["plan_price"] == 15, "the users table must agree with the MRR"
    assert row["plan"] == "pro"


def test_a_running_discount_is_counted_as_charged(admin):
    admin.people["deal"] = _person("deal", plan="pro", stripe_price_id="price_pro",
                                   monthly_price=18.75)
    d = admin.get("/admin/overview").json()
    assert d["mrr"] == 18.75
    assert d["users"]["paying_by_tier"]["pro"] == 1


def test_until_stripe_has_been_asked_the_old_rule_still_applies(admin):
    """Nothing recorded yet: the tier's list price, or LEGACY_PRICE with no tier.
    The hourly sweep (or Sync from Stripe) replaces both with the real charge."""
    from src.billing import plans
    admin.people["nova"] = _person("nova", plan="pro")
    admin.people["old"] = _person("old")
    d = admin.get("/admin/overview").json()
    assert d["mrr"] == 25 + plans.LEGACY_PRICE
    assert d["users"]["paying_by_tier"] == {"pro": 1, "starter": 0, "legacy": 1}


def test_comps_and_lapsed_subscribers_are_still_not_revenue(admin):
    admin.people["gift"] = _person("gift", plan="pro", plan_source="granted",
                                   monthly_price=25.0)
    admin.people["nocard"] = {**_person("nocard", plan="pro"), "stripe_customer_id": None}
    admin.people["lapsed"] = _person("lapsed", plan="pro", monthly_price=25.0,
                                     subscription_status="canceled")
    d = admin.get("/admin/overview").json()
    assert d["mrr"] == 0
    assert d["users"]["paying"] == 0
