"""Stripe set up for campaign payments (owner, 2026-10-23: "i need to set it up
with stripe now").

What these defend:
  1. the check reads the account and the webhook and says what is missing,
     never printing a key;
  2. --add-events adds only the two checkout events to the existing
     endpoint, keeping every event it had (same endpoint, same secret);
  3. the Checkout a streamer pays: amounts from the stored quote, no $0 line,
     a receipt to the contact email, metadata on session and PaymentIntent,
     and a stale saved customer that cannot block the payment;
  4. turning checkout.session.completed on cannot disturb subscriptions: a
     subscription's own checkout event is answered and changes nothing.
"""

import json
from types import SimpleNamespace

import pytest
import stripe

from src.maintenance import stripe_campaign_check as chk

LIVE = "sk_live_" + "x" * 24
SUB_EVENTS = ["customer.subscription.created", "customer.subscription.updated",
              "customer.subscription.deleted"]


class FakeStripe:
    def __init__(self, endpoints, account=None, list_error=None):
        self.updates = []

        def _list(params=None):
            if list_error:
                raise list_error
            return SimpleNamespace(data=endpoints)

        def _update(eid, params=None):
            self.updates.append((eid, params))
            return {"id": eid, **(params or {})}
        acct = account if account is not None else {
            "charges_enabled": True, "payouts_enabled": True, "default_currency": "usd",
            "business_profile": {"name": "ANTI Technology LLC"},
            "settings": {"payments": {"statement_descriptor": "HIGHLIGHTZ"}}}
        self.v1 = SimpleNamespace(
            accounts=SimpleNamespace(retrieve_current=lambda: acct),
            webhook_endpoints=SimpleNamespace(list=_list, update=_update))


def endpoint(events, status="enabled", url="https://highlightz.app/billing/webhook", eid="we_1"):
    return {"id": eid, "url": url, "status": status, "enabled_events": list(events)}


@pytest.fixture
def keys(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "stripe_secret_key", LIVE)
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_" + "y" * 24)
    monkeypatch.setattr(settings, "campaign_requests_enabled", False)
    monkeypatch.setattr(settings, "campaigns_enabled", True)
    return settings


def run(client, add=False):
    lines = []
    code = chk.check(client, add_events=add, out=lines.append)
    return code, "\n".join(lines)


def test_the_key_mode_is_read_from_the_prefix_only():
    assert chk.key_mode(LIVE) == "live"
    assert chk.key_mode("sk_test_abc") == "test"
    assert chk.key_mode("rk_live_abc") == "restricted live"
    assert chk.key_mode("") == "" and chk.key_mode("nonsense") == "unknown"


def test_no_key_says_so_and_asks_stripe_nothing(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    code, text = run(None)
    assert code == 1 and "STRIPE_SECRET_KEY is not set" in text


def test_a_subscription_only_endpoint_is_reported_and_left_alone(keys):
    s = FakeStripe([endpoint(SUB_EVENTS)])
    code, text = run(s)
    assert code == 1
    assert "not sent: checkout.session.completed, checkout.session.async_payment_succeeded" in text
    assert "--add-events" in text and s.updates == [], "it changed Stripe without being asked"
    assert LIVE not in text and "whsec_" + "y" * 24 not in text, "a key was printed"
    assert "live mode" in text and "card payments on" in text and '"HIGHLIGHTZ"' in text


def test_add_events_keeps_every_event_and_the_same_endpoint(keys):
    s = FakeStripe([endpoint(SUB_EVENTS + ["invoice.paid"])])
    code, text = run(s, add=True)
    assert code == 0, text
    [(eid, params)] = s.updates
    assert eid == "we_1"
    assert set(params["enabled_events"]) == set(SUB_EVENTS) | {"invoice.paid"} | set(chk.NEEDED)
    assert "ADDED" in text and "Ready" in text


def test_an_endpoint_on_every_event_needs_nothing(keys):
    s = FakeStripe([endpoint(["*"])])
    code, text = run(s, add=True)
    assert code == 0 and s.updates == [] and "ok       sends" in text


def test_a_missing_or_disabled_endpoint_is_named(keys):
    code, text = run(FakeStripe([endpoint(["*"], url="https://example.com/hook")]))
    assert code == 1 and "no endpoint points at https://highlightz.app/billing/webhook" in text
    assert "other    https://example.com/hook" in text
    code, text = run(FakeStripe([endpoint(["*"], status="disabled")]))
    assert code == 1 and "it is disabled" in text


def test_an_account_that_cannot_take_cards_is_a_problem(keys):
    s = FakeStripe([endpoint(["*"])], account={"charges_enabled": False, "payouts_enabled": False})
    code, text = run(s)
    assert code == 1 and "card payments OFF" in text


def test_a_key_that_cannot_list_endpoints_is_reported(keys):
    s = FakeStripe([], list_error=stripe.PermissionError("not allowed"))
    code, text = run(s)
    assert code == 1 and "could not list webhook endpoints" in text


def test_requests_on_with_the_marketplace_off_is_a_problem(keys):
    keys.campaign_requests_enabled, keys.campaigns_enabled = True, False
    code, text = run(FakeStripe([endpoint(["*"])]))
    assert code == 1 and "turn on CAMPAIGNS_ENABLED" in text


# ── the Checkout itself ──────────────────────────────────────────────────────

def _req(**kw):
    q = {"budget_cents": 100000, "flat_fee_cents": 0, "percent_fee_cents": 5000,
         "total_cents": 105000, "fee_pct": 5.0}
    r = {"id": "r" * 32, "user_id": "u1", "title": "Clip my week", "quote": q,
         "contact_email": "streamer@example.com"}
    r.update(kw)
    return r


def test_the_checkout_charges_the_stored_quote_with_a_receipt(monkeypatch):
    from src.billing import stripe_billing as sb
    sent = []
    fake = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(
        create=lambda params: sent.append(params) or {"url": "https://checkout.stripe.com/x", "id": "cs_1"}))))
    monkeypatch.setattr(sb, "_client", lambda: fake)
    url, sid = sb.create_campaign_checkout(_req(), {"id": "u1"})
    assert (url, sid) == ("https://checkout.stripe.com/x", "cs_1")
    [p] = sent
    assert p["mode"] == "payment"
    amounts = [(li["price_data"]["product_data"]["name"], li["price_data"]["unit_amount"]) for li in p["line_items"]]
    assert amounts == [("Campaign prize pool: Clip my week", 100000),
                       ("Highlightz platform fee (5%)", 5000)], "a $0 line, or the wrong amounts"
    assert sum(a for _, a in amounts) == 105000
    assert p["payment_intent_data"]["receipt_email"] == "streamer@example.com"
    assert p["metadata"] == p["payment_intent_data"]["metadata"] == \
        {"kind": "campaign_request", "request_id": "r" * 32, "user_id": "u1"}
    assert p["customer_email"] == "streamer@example.com" and "customer" not in p


def test_a_stale_saved_customer_cannot_block_the_payment(monkeypatch):
    from src.billing import stripe_billing as sb
    sent = []

    def create(params):
        sent.append(dict(params))
        if "customer" in params:
            raise stripe.InvalidRequestError("No such customer: 'cus_gone'", param="customer")
        return {"url": "https://checkout.stripe.com/y", "id": "cs_2"}
    fake = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create))))
    monkeypatch.setattr(sb, "_client", lambda: fake)
    assert sb.create_campaign_checkout(_req(), {"id": "u1", "stripe_customer_id": "cus_gone"})[1] == "cs_2"
    assert sent[0]["customer"] == "cus_gone" and "customer" not in sent[1]
    assert sent[1]["customer_email"] == "streamer@example.com"


def test_any_other_stripe_refusal_is_not_retried(monkeypatch):
    from src.billing import stripe_billing as sb
    calls = []

    def create(params):
        calls.append(1)
        raise stripe.InvalidRequestError("Amount must be at least 50 cents", param="line_items")
    fake = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create))))
    monkeypatch.setattr(sb, "_client", lambda: fake)
    with pytest.raises(stripe.InvalidRequestError):
        sb.create_campaign_checkout(_req(), {"id": "u1", "stripe_customer_id": "cus_ok"})
    assert len(calls) == 1


# ── switching the event on cannot touch subscriptions ───────────────────────

def test_a_subscription_checkout_event_is_answered_and_changes_nothing(monkeypatch):
    """Once checkout.session.completed is on, every subscription checkout sends
    it too. It must get a 200 (or Stripe retries and then disables the
    endpoint) and must not reach the campaign code or any account."""
    from fastapi.testclient import TestClient
    from src.billing import stripe_billing as sb
    from src.dashboard import api
    monkeypatch.setattr(api.settings, "stripe_webhook_secret", "whsec_test")
    monkeypatch.setattr(sb, "handle_webhook_event", lambda payload, sig: json.loads(payload))
    touched = []

    async def _paid(*a, **k):
        touched.append("campaign")
    monkeypatch.setattr(api, "_campaign_paid", _paid)
    monkeypatch.setattr(api, "apply_subscription_event", lambda *a, **k: touched.append("account"))
    event = {"id": "evt_sub_checkout", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_sub", "mode": "subscription", "payment_status": "paid",
                                 "customer": "cus_1", "metadata": {"user_id": "u1", "username": "x"}}}}
    r = TestClient(api.app).post("/billing/webhook", content=json.dumps(event),
                                 headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 200 and r.json() == {"received": True}
    assert touched == [], "a subscription's checkout event reached campaign or account code"
