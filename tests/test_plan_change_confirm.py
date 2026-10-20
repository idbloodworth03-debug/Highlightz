"""A link must never move money (audit, 2026-10-20).

GET /billing/checkout?plan=pro used to change an active subscriber's price in
place, and Stripe bills the prorated difference the moment the price changes —
so any link to it (the paywall's own "Choose Pro", or one posted anywhere)
charged a Starter customer with nothing on screen saying so, and ?plan=starter
downgraded a Pro one. Now the link goes to /billing/change, a page that says
what the switch costs, and only its Confirm button (a POST carrying a one-time
token from that page) makes the change.
"""

import base64
import json
import re
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def world(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.billing import stripe_billing

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(api.settings, "stripe_price_id_starter", "price_starter")
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    now = time.time()
    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "u1", "username": "starter_user", "subscription_status": "active",
         "plan": "starter", "stripe_customer_id": "cus_A", "created_at": now},
        {"id": "u2", "username": "pro_user", "subscription_status": "active",
         "plan": "pro", "stripe_customer_id": "cus_B", "created_at": now},
        {"id": "u3", "username": "free_user", "subscription_status": "none",
         "plan": "free", "created_at": now},
    ]))
    changes, sent = [], []

    async def _change(customer_id, price_id):
        changes.append((customer_id, price_id))
        return "sub_123"
    monkeypatch.setattr(stripe_billing, "change_subscription_price", _change)

    async def _no_checkout(*a, **k):
        raise AssertionError("a Checkout session was opened for a subscriber")
    monkeypatch.setattr(stripe_billing, "create_checkout_url", _no_checkout)

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)

    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def as_user(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": False,
             "subscription_status": "active"}).encode())).decode())
        return c
    c.as_user, c.changes, c.sent = as_user, changes, sent
    c.plan_of = lambda uid: user_store.get_by_id(uid).get("plan")
    return c


def _token(page: str) -> str:
    return re.search(r'name="token" value="([^"]+)"', page).group(1)


# ── the link itself does nothing ─────────────────────────────────────────────

def test_the_upgrade_link_changes_nothing_and_goes_to_the_page(world):
    r = world.as_user("u1").get("/billing/checkout?plan=pro", follow_redirects=False)
    assert r.headers["location"] == "/billing/change?plan=pro"
    assert world.changes == [] and world.plan_of("u1") == "starter"


def test_a_downgrade_link_changes_nothing_either(world):
    r = world.as_user("u2").get("/billing/checkout?plan=starter", follow_redirects=False)
    assert r.headers["location"] == "/billing/change?plan=starter"
    assert world.changes == [] and world.plan_of("u2") == "pro"


def test_opening_the_page_changes_nothing(world):
    world.as_user("u1").get("/billing/change?plan=pro")
    assert world.changes == [] and world.plan_of("u1") == "starter"


# ── the page says what it costs ──────────────────────────────────────────────

def test_the_page_names_both_plans_their_prices_and_the_charge(world):
    from src.billing.plans import PLAN_LIMITS
    r = world.as_user("u1").get("/billing/change?plan=pro")
    page = r.text
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert f"Starter &middot; ${PLAN_LIMITS['starter']['price']}/month" in page
    assert f"Pro &middot; ${PLAN_LIMITS['pro']['price']}/month" in page
    assert "charges the card on your subscription today for the difference" in page
    assert '<form method="post" action="/billing/change">' in page
    assert "Confirm &mdash; switch to Pro" in page and '<a href="/">Not now</a>' in page
    assert 'name="robots" content="noindex"' in page


def test_a_downgrade_page_says_the_rest_becomes_credit(world):
    page = world.as_user("u2").get("/billing/change?plan=starter").text
    assert "becomes a credit on your account" in page
    assert "charges the card" not in page


# ── only the Confirm button on that page makes the change ───────────────────

def test_confirming_makes_the_change_once_and_says_so_live(world):
    c = world.as_user("u1")
    tok = _token(c.get("/billing/change?plan=pro").text)
    r = c.post("/billing/change", data={"plan": "pro", "token": tok}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?plan=pro"
    assert world.changes == [("cus_A", "price_pro")] and world.plan_of("u1") == "pro"
    assert ("subscription_active", "u1") in world.sent
    assert all(uid == "u1" for _, uid in world.sent)


def test_a_post_without_the_pages_token_changes_nothing(world):
    """A form on another site, or a forged request: no token from our page."""
    c = world.as_user("u1")
    c.get("/billing/change?plan=pro")
    for data in ({"plan": "pro"}, {"plan": "pro", "token": "guessed"}):
        r = c.post("/billing/change", data=data, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/billing/change?plan=pro"
    assert world.changes == [] and world.plan_of("u1") == "starter"


def test_a_token_works_once(world):
    c = world.as_user("u1")
    tok = _token(c.get("/billing/change?plan=pro").text)
    c.post("/billing/change", data={"plan": "pro", "token": tok}, follow_redirects=False)
    again = c.post("/billing/change", data={"plan": "pro", "token": tok}, follow_redirects=False)
    # Refused as unconfirmed — not merely "already on Pro", which would also
    # let a kept token through after a downgrade in between.
    assert again.headers["location"] == "/billing/change?plan=pro"
    assert world.changes == [("cus_A", "price_pro")]


def test_a_token_for_one_plan_cannot_confirm_the_other(world):
    c = world.as_user("u2")
    tok = _token(c.get("/billing/change?plan=starter").text)
    r = c.post("/billing/change", data={"plan": "pro", "token": tok}, follow_redirects=False)
    assert world.changes == [] and r.headers["location"] == "/billing/change?plan=pro"


def test_an_old_token_changes_nothing(world, monkeypatch):
    from src.dashboard import api
    c = world.as_user("u1")
    tok = _token(c.get("/billing/change?plan=pro").text)
    monkeypatch.setattr(api, "_PLAN_CHANGE_TTL_S", -1)
    c.post("/billing/change", data={"plan": "pro", "token": tok}, follow_redirects=False)
    assert world.changes == []


def test_every_redirect_after_the_post_is_a_303(world):
    """307 would make the browser send the form again to wherever it lands."""
    c = world.as_user("u1")
    assert c.post("/billing/change", data={"plan": "pro"}, follow_redirects=False).status_code == 303
    tok = _token(c.get("/billing/change?plan=pro").text)
    assert c.post("/billing/change", data={"plan": "pro", "token": tok},
                  follow_redirects=False).status_code == 303


# ── everyone else ────────────────────────────────────────────────────────────

def test_signed_out_is_sent_to_sign_in_and_nothing_changes(world):
    world.cookies.clear()
    assert world.get("/billing/change?plan=pro", follow_redirects=False).headers["location"] == "/login"
    r = world.post("/billing/change", data={"plan": "pro", "token": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert world.changes == []


def test_someone_not_subscribed_goes_to_checkout_not_an_in_place_change(world):
    c = world.as_user("u3")
    r = c.get("/billing/change?plan=pro", follow_redirects=False)
    assert r.headers["location"] == "/billing/checkout?plan=pro"
    assert world.changes == []


def test_the_plan_you_already_have_is_a_no_op(world):
    c = world.as_user("u1")
    assert c.get("/billing/change?plan=starter", follow_redirects=False).headers["location"] == "/?plan=unchanged"
    assert world.changes == []


def test_an_unknown_plan_is_refused(world):
    assert world.as_user("u1").get("/billing/change?plan=enterprise").status_code == 400


def test_the_paywall_cards_still_point_at_checkout():
    """Unchanged links; it is the endpoint behind them that now asks first."""
    from src.dashboard import api
    assert 'href="/billing/checkout?plan=pro"' in api.PAYWALL_HTML
    assert 'href="/billing/checkout?plan=starter"' in api.PAYWALL_HTML
