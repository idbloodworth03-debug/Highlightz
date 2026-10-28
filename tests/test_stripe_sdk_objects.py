"""Stripe's own objects, not dicts (2026-10-13).

Found from one support ticket. A user who had just paid for Pro had no plan
stored, and the logs held no Stripe events at all for a week. Production runs
stripe-python 15.2.0, where a StripeObject is NOT a dict:

  * the webhook handed construct_event's Event (no .get) to code written
    against dicts, so every delivery raised "AttributeError: get" and answered
    500 — ten in a week on prod, zero subscription events processed;
  * the hourly sweep read a subscription item's `price` as "a dict or a str",
    and a Price object is neither, so it returned plan=None for every live
    subscription — a status could be corrected, a plan never.

Every earlier test fed these paths hand-built dicts, which is exactly the
shape the real SDK stopped producing — so they all passed while production
failed. These drive the REAL library: a correctly signed payload through the
real endpoint, and real Subscription objects through the real sweep. Skipped
only where stripe is not installed (CI installs the production lock).
"""

import asyncio
import hashlib
import hmac
import json
import time

import pytest

stripe = pytest.importorskip("stripe")

from src.billing import stripe_billing as sb  # noqa: E402

SECRET = "whsec_test_secret"


def _signed(payload: str, secret: str = SECRET) -> str:
    """The Stripe-Signature header Stripe would send for this body."""
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _sub_event(user_id: str, price: str = "price_pro", *, status: str = "active",
               etype: str = "customer.subscription.created", cust: str = "cus_sdk",
               eid: str = "evt_sdk_1") -> dict:
    """A subscription event in the shape Stripe actually sends."""
    return {"id": eid, "object": "event", "type": etype,
            "data": {"object": {
                "id": "sub_sdk", "object": "subscription", "customer": cust,
                "status": status, "metadata": {"user_id": user_id},
                "items": {"object": "list", "data": [{
                    "id": "si_sdk", "object": "subscription_item",
                    "price": {"id": price, "object": "price"}}]}}}}


# ── the webhook ──────────────────────────────────────────────────────────────

def test_a_verified_webhook_comes_back_as_plain_dicts(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_webhook_secret", SECRET)
    payload = json.dumps(_sub_event("u1"))
    ev = sb.handle_webhook_event(payload.encode(), _signed(payload))
    assert type(ev) is dict and ev.get("type") == "customer.subscription.created"
    assert type(ev["data"]["object"]) is dict
    # The functions downstream, on what the endpoint now hands them.
    assert sb.extract_price_id(ev) == "price_pro"
    assert sb.sync_subscription_event(ev) == ("cus_sdk", "u1", "active")


def test_a_bad_signature_is_still_refused(monkeypatch):
    """Parsing the body ourselves must not skip the check that matters."""
    monkeypatch.setattr(sb.settings, "stripe_webhook_secret", SECRET)
    payload = json.dumps(_sub_event("u1"))
    with pytest.raises(Exception):
        sb.handle_webhook_event(payload.encode(), _signed(payload, secret="whsec_wrong"))
    with pytest.raises(Exception):
        sb.handle_webhook_event(payload.encode(), "t=1,v1=deadbeef")


@pytest.fixture
def webhook(tmp_path, monkeypatch):
    """The real endpoint, with only the outside world stubbed."""
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store, trial_ledger

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    monkeypatch.setattr(api, "_stripe_processed", {})
    for k, v in (("stripe_webhook_secret", SECRET), ("stripe_price_id_pro", "price_pro"),
                 ("stripe_price_id_starter", "price_starter")):
        monkeypatch.setattr(sb.settings, k, v)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(api, "_enforce_stream_limit", _noop, raising=False)
    monkeypatch.setattr(sb, "customer_email", _noop)
    monkeypatch.setattr(sb, "cancel_duplicate_subscriptions", _noop)

    u = user_store.upsert_twitch_user("tw_sdk", "luclips", "luclips")
    user_store.update_subscription(u["id"], "cus_sdk", "none")
    client = TestClient(api.app, base_url="https://testserver")

    def post(event: dict):
        body = json.dumps(event)
        return client.post("/billing/webhook", content=body,
                           headers={"stripe-signature": _signed(body),
                                    "content-type": "application/json"})
    return post, user_store, u


@pytest.mark.parametrize("price, plan", [("price_pro", "pro"), ("price_starter", "starter")])
def test_a_real_subscription_event_activates_and_stores_the_plan(webhook, price, plan):
    """The whole point: a paying user gets their tier stored, so Starter is
    Starter and not the legacy-Pro fallback a missing plan resolves to."""
    post, store, u = webhook
    r = post(_sub_event(u["id"], price))
    assert r.status_code == 200, r.text
    after = store.get_by_id(u["id"])
    assert after["subscription_status"] == "active"
    assert after["plan"] == plan


def test_a_tampered_body_is_rejected_by_the_endpoint(webhook):
    post, store, u = webhook
    from fastapi.testclient import TestClient
    from src.dashboard import api
    body = json.dumps(_sub_event(u["id"]))
    sig = _signed(body)
    r = TestClient(api.app, base_url="https://testserver").post(
        "/billing/webhook", content=body.replace("price_pro", "price_starter"),
        headers={"stripe-signature": sig, "content-type": "application/json"})
    assert r.status_code == 400
    assert store.get_by_id(u["id"])["subscription_status"] == "none"


# ── the hourly sweep ─────────────────────────────────────────────────────────

def _real_subscription(price: str, status: str = "active"):
    return stripe.Subscription.construct_from({
        "id": "sub_sdk", "object": "subscription", "customer": "cus_sdk",
        "status": status, "trial_end": None,
        "items": {"object": "list", "data": [{
            "id": "si_sdk", "object": "subscription_item",
            "price": {"id": price, "object": "price"}}]}}, "sk_test_x")


@pytest.mark.parametrize("price, plan", [("price_pro", "pro"), ("price_starter", "starter")])
def test_the_sweep_reads_the_plan_off_real_sdk_objects(monkeypatch, price, plan):
    for k, v in (("stripe_secret_key", "sk_test_x"), ("stripe_price_id_pro", "price_pro"),
                 ("stripe_price_id_starter", "price_starter")):
        monkeypatch.setattr(sb.settings, k, v)
    listing = stripe.ListObject.construct_from(
        {"object": "list", "data": [_real_subscription(price)]}, "sk_test_x")

    class _Subs:
        def list(self, params=None):
            return listing

    class _Client:
        subscriptions = _Subs()
    monkeypatch.setattr(sb, "_client", lambda: _Client())
    truth = asyncio.run(sb.authoritative_subscription("cus_sdk"))
    assert truth["status"] == "active"
    assert truth["plan"] == plan, "the sweep cannot read a plan off a real Price object"


def test_a_legacy_price_event_keeps_pro_and_records_what_it_charges(webhook, monkeypatch):
    """The $15-era price grandfathers to Pro (features) while the record keeps
    the $15 it charges (MRR). Before 2026-10-28 only the tier was stored, so
    the admin MRR priced every such subscriber at Pro's $25."""
    post, store, u = webhook
    monkeypatch.setattr(sb.settings, "stripe_price_id", "price_legacy15")
    event = _sub_event(u["id"], "price_legacy15")
    event["data"]["object"]["items"]["data"][0]["price"].update(
        {"unit_amount": 1500, "currency": "usd",
         "recurring": {"interval": "month", "interval_count": 1}})
    r = post(event)
    assert r.status_code == 200, r.text
    after = store.get_by_id(u["id"])
    assert after["plan"] == "pro"
    assert after["stripe_price_id"] == "price_legacy15"
    assert after["monthly_price"] == 15.0
