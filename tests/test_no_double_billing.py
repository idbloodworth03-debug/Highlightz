"""Two live money bugs, one checkout endpoint.

1. UPGRADES SILENTLY DID NOTHING. /billing/checkout bounced any already-active
   subscriber to "/" — so a Starter customer clicked Go Pro, landed back on the
   dashboard, and stayed on Starter. No error, no charge, no upgrade. The guard
   existed to stop a second subscription being minted, and it did, by stopping
   the upgrade entirely.

2. WHEN THAT GUARD MISSED, THE CUSTOMER PAID TWICE. Checkout in mode=subscription
   ALWAYS creates a new subscription; reusing the customer only means both land
   on the same customer. The guard's live check deliberately fails OPEN on a
   Stripe error, and two tabs can both pass it inside the webhook-latency
   window — either way the old subscription keeps billing alongside the new one.

The fix is one subscription, always: change the price in place, and sweep any
duplicate the moment Stripe says one was created.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(api.settings, "stripe_price_id_starter", "price_starter")
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "u1", "username": "starter_user", "subscription_status": "active",
         "plan": "starter", "stripe_customer_id": "cus_A", "created_at": now},
    ]))
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "u1", "username": "starter_user",
         "is_admin": False, "subscription_status": "active"}).encode())).decode())
    c.api = api
    return c


def _stub_change(monkeypatch, result):
    from src.billing import stripe_billing
    calls = []

    async def _change(customer_id, price_id):
        calls.append((customer_id, price_id))
        return result
    monkeypatch.setattr(stripe_billing, "change_subscription_price", _change)
    return calls


def _no_checkout(monkeypatch):
    """Explode if anything opens a Checkout session — that is the double-charge."""
    from src.billing import stripe_billing

    async def _boom(*a, **k):
        raise AssertionError("a second Checkout session was opened — this double-bills")
    monkeypatch.setattr(stripe_billing, "create_checkout_url", _boom)


# ── the upgrade actually happens ─────────────────────────────────────────────

def test_upgrading_changes_the_price_in_place(client, monkeypatch):
    calls = _stub_change(monkeypatch, "sub_123")
    _no_checkout(monkeypatch)
    r = client.get("/billing/checkout?plan=pro", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert calls == [("cus_A", "price_pro")], "the subscription price was never changed"


def test_the_stored_plan_follows_the_upgrade(client, monkeypatch):
    """The bug the user actually saw: they pay, and the app still says Starter."""
    _stub_change(monkeypatch, "sub_123")
    _no_checkout(monkeypatch)
    client.get("/billing/checkout?plan=pro", follow_redirects=False)
    from src.auth import users as user_store
    assert user_store.get_by_id("u1")["plan"] == "pro"


def test_an_upgrade_never_opens_a_second_checkout(client, monkeypatch):
    """mode=subscription always creates a NEW subscription. For someone who
    already has one, that is the double charge."""
    _stub_change(monkeypatch, "sub_123")
    _no_checkout(monkeypatch)          # asserts inside if it is ever called
    client.get("/billing/checkout?plan=pro", follow_redirects=False)


def test_a_failed_upgrade_does_not_claim_success(client, monkeypatch):
    """Marking them Pro locally when Stripe refused would give away the tier for
    free and hide the failure."""
    _stub_change(monkeypatch, None)    # Stripe unreachable / nothing to change
    _no_checkout(monkeypatch)
    r = client.get("/billing/checkout?plan=pro", follow_redirects=False)
    assert "/billing/portal" in r.headers.get("location", ""), \
        "a failed upgrade should route to the portal, not back to a stale dashboard"
    from src.auth import users as user_store
    assert user_store.get_by_id("u1")["plan"] == "starter", "plan changed despite a failed upgrade"


def test_choosing_the_plan_you_already_have_is_a_no_op(client, monkeypatch):
    calls = _stub_change(monkeypatch, "sub_123")
    _no_checkout(monkeypatch)
    client.get("/billing/checkout?plan=starter", follow_redirects=False)
    assert calls == [], "Stripe was asked to change a price to the one already set"


# ── the duplicate sweeper ────────────────────────────────────────────────────

def test_a_duplicate_subscription_is_cancelled_as_soon_as_stripe_reports_it():
    """The last line of defence. Runs on customer.subscription.created so a
    duplicate dies seconds after it appears, not at the next billing date."""
    import inspect
    from src.dashboard import api
    # The route just verifies the signature and delegates; the handling
    # lives in _process_stripe_event.
    src = inspect.getsource(api._process_stripe_event)
    # The CALL, not the name — the import line alone satisfies a presence
    # check while the call is replaced by a constant.
    assert "await cancel_duplicate_subscriptions(cust_id, new_sub_id)" in src, \
        "the duplicate sweep is imported but never invoked"
    i = src.index("await cancel_duplicate_subscriptions")
    guard = src[max(0, i - 500):i]
    assert 'customer.subscription.created' in guard, \
        "the sweep is not scoped to newly-created subscriptions"


def test_the_sweeper_keeps_the_new_subscription_and_kills_the_rest():
    import inspect
    from src.billing import stripe_billing
    src = inspect.getsource(stripe_billing.cancel_duplicate_subscriptions)
    assert "if sub_id == keep_id:" in src and "continue" in src, \
        "the sweeper does not protect the subscription it was told to keep"


def test_the_price_change_prorates_immediately():
    """The customer asked for the upgrade and expects it now. Deferring the
    charge to the next cycle gives away the difference."""
    import inspect
    from src.billing import stripe_billing
    src = inspect.getsource(stripe_billing.change_subscription_price)
    assert '"proration_behavior": "always_invoice"' in src


def test_the_price_change_reports_failure_rather_than_guessing():
    import inspect
    from src.billing import stripe_billing
    src = inspect.getsource(stripe_billing.change_subscription_price)
    assert "return None" in src and "-> str | None" in src
