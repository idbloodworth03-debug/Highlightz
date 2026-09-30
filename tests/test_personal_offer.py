"""A personal discount an admin gives one account (owner, 2026-09-30).

"I want to offer a specific user a code … next time he signs in he can claim a
discount code that brings him straight to the checkout page. The discount code
is CLIPPER and it offers him 25% off his first month."

What these defend:
  1. only an admin sets or removes an offer, and the user's tab hears about it
     (`offer_changed`, scoped to them);
  2. /me carries the offer until they dismiss it or are already paying;
  3. Checkout pre-applies the code ONLY for the account's own offer: a stranger
     adding ?offer=1 gets an ordinary checkout;
  4. Stripe gets `discounts` with the code's id and no promotion-code box
     (Stripe refuses both at once); a code missing from Stripe falls back to
     the box instead of failing;
  5. the modal, its handler and the admin controls are wired.
"""

import base64
import json

import pytest
from itsdangerous import TimestampSigner

from src.auth import users as user_store

PEOPLE = {}


def _fresh():
    PEOPLE.clear()
    PEOPLE.update({
        "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
        "fan":  {"id": "fan", "username": "fan", "subscription_status": "none"},
        "other": {"id": "other", "username": "other", "subscription_status": "none"},
    })


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    _fresh()
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        p = PEOPLE[uid]
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
             "subscription_status": p.get("subscription_status", "none")}).encode())).decode())
        return c
    c.login, c.sent = login, sent
    return c


def _offer(c, uid="fan", code="clipper"):
    return c.login("boss").post(f"/admin/users/{uid}/offer",
                                json={"code": code, "plan": "pro", "headline": "25% off your first month"})


def test_an_admin_sets_an_offer_and_the_user_hears_it_live(client):
    r = _offer(client)
    assert r.status_code == 200 and r.json()["offer"]["code"] == "CLIPPER"
    assert ("offer_changed", "fan") in client.sent
    me = client.login("fan").get("/me").json()
    assert me["offer"] == {"code": "CLIPPER", "plan": "pro", "headline": "25% off your first month"}


def test_only_an_admin_can_set_or_remove_one(client):
    r = client.login("fan").post("/admin/users/fan/offer", json={"code": "FREE"})
    assert r.status_code in (401, 403)
    assert "offer" not in PEOPLE["fan"]
    _offer(client)
    r = client.login("other").delete("/admin/users/fan/offer")
    assert r.status_code in (401, 403) and PEOPLE["fan"]["offer"]


def test_a_bad_code_is_refused(client):
    assert _offer(client, code="no spaces!").status_code == 400


def test_not_now_hides_it_and_resending_brings_it_back(client):
    _offer(client)
    client.sent.clear()
    assert client.login("fan").post("/offer/dismiss").status_code == 200
    assert ("offer_changed", "fan") in client.sent
    assert client.login("fan").get("/me").json()["offer"] is None
    _offer(client)
    assert client.login("fan").get("/me").json()["offer"]["code"] == "CLIPPER"


def test_it_is_not_shown_to_someone_already_paying():
    _fresh()
    u = dict(PEOPLE["fan"], offer={"code": "CLIPPER", "plan": "pro"}, subscription_status="active")
    assert user_store.offer_for(u) is None


def test_removing_it_clears_it(client):
    _offer(client)
    assert client.login("boss").delete("/admin/users/fan/offer").status_code == 200
    assert client.login("fan").get("/me").json()["offer"] is None


# ── checkout ─────────────────────────────────────────────────────────────────

@pytest.fixture
def checkout(client, monkeypatch):
    from src.dashboard import api
    from src.billing import stripe_billing
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    calls = []

    async def fake_create(uid, username, price_id, customer_id=None, trial_days=0, promo_code=None):
        calls.append({"uid": uid, "promo": promo_code})
        return "https://checkout.stripe.test/s"
    monkeypatch.setattr(stripe_billing, "create_checkout_url", fake_create)
    client.calls = calls
    return client


def test_claim_opens_checkout_with_the_code_applied(checkout):
    _offer(checkout)
    r = checkout.login("fan").get("/billing/checkout?plan=pro&offer=1", follow_redirects=False)
    assert r.status_code in (302, 307) and "checkout.stripe.test" in r.headers["location"]
    assert checkout.calls[-1] == {"uid": "fan", "promo": "CLIPPER"}
    assert PEOPLE["fan"]["offer"]["claimed_at"] > 0


def test_offer_1_does_nothing_for_an_account_without_an_offer(checkout):
    _offer(checkout)                                   # fan has one; other does not
    checkout.login("other").get("/billing/checkout?plan=pro&offer=1", follow_redirects=False)
    assert checkout.calls[-1] == {"uid": "other", "promo": None}


def _stripe(monkeypatch, found):
    from src.billing import stripe_billing
    made = {}

    class Promos:
        def list(self, params):
            assert params["code"] == "CLIPPER" and params["active"] is True
            return {"data": [{"id": "promo_123"}] if found else []}

    class Sessions:
        def create(self, params):
            made.update(params)
            return type("S", (), {"url": "https://x"})()

    class Client:
        promotion_codes = Promos()
        checkout = type("C", (), {"sessions": Sessions()})()
    monkeypatch.setattr(stripe_billing, "_client", lambda: Client())
    return made


def test_stripe_gets_the_discount_and_no_code_box(monkeypatch):
    import asyncio
    from src.billing import stripe_billing
    made = _stripe(monkeypatch, found=True)
    asyncio.run(stripe_billing.create_checkout_url("fan", "fan", "price_pro", promo_code="CLIPPER"))
    assert made["discounts"] == [{"promotion_code": "promo_123"}]
    assert "allow_promotion_codes" not in made


def test_a_code_missing_from_stripe_falls_back_to_the_box(monkeypatch):
    import asyncio
    from src.billing import stripe_billing
    made = _stripe(monkeypatch, found=False)
    asyncio.run(stripe_billing.create_checkout_url("fan", "fan", "price_pro", promo_code="CLIPPER"))
    assert "discounts" not in made and made["allow_promotion_codes"] is True


# ── the pages ────────────────────────────────────────────────────────────────

def test_the_modal_and_its_live_handler_are_wired():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "function OfferModal(" in page
    assert "<OfferModal offer={me.offer}" in page
    assert "'/billing/checkout?plan=' + (offer.plan || 'pro') + '&offer=1'" in page
    assert "msg.event==='offer_changed'" in page
    assert "fetch('/offer/dismiss',{method:'POST'})" in page


def test_the_admin_drawer_can_offer_and_remove():
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert "offerBlock(u)" in html and "dr-offer" in html and "dr-unoffer" in html
    assert ".dr-offer, .dr-unoffer'" in html
