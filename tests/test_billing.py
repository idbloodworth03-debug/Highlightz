"""
Tests for the Stripe billing mapping that drives the trial→paid transition.

sync_subscription_event is the single point that interprets Stripe webhooks into
our (customer_id, user_id, status) model. The webhook handler then links the
customer and flips the user's subscription_status, so getting this mapping right
is what makes a trial convert to paid (and a lapse revoke access).
"""

import asyncio
from unittest.mock import MagicMock, patch

from src.billing import stripe_billing as sb


def _sub_event(etype: str, status: str, *, cust="cus_123", user="u_42"):
    return {
        "id": "evt_1",
        "type": etype,
        "data": {"object": {
            "customer": cust,
            "status": status,
            "metadata": {"user_id": user},
        }},
    }


def test_subscription_created_active_maps_to_active():
    # The trial→paid event: checkout creates the subscription, Stripe fires
    # customer.subscription.created with status=active and our user_id in metadata.
    cust, user, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.created", "active"))
    assert (cust, user, status) == ("cus_123", "u_42", "active")


def test_trialing_maps_to_active_access():
    cust, user, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.updated", "trialing"))
    assert status == "active"  # trialing still grants access


def test_canceled_maps_to_inactive():
    _, _, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.deleted", "canceled"))
    assert status == "inactive"


def test_unpaid_and_incomplete_expired_map_to_inactive():
    for raw in ("unpaid", "incomplete_expired"):
        _, _, status = sb.sync_subscription_event(
            _sub_event("customer.subscription.updated", raw))
        assert status == "inactive", raw


def test_past_due_passes_through_for_gating():
    # past_due is not active → the middleware gate treats it as no-access, but we
    # keep the raw status so the user can be recovered when payment succeeds.
    _, _, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.updated", "past_due"))
    assert status == "past_due"


def test_non_subscription_event_is_ignored():
    # checkout.session.completed and friends are not subscription lifecycle events;
    # the handler must no-op on them (returns empties → no status write).
    cust, user, status = sb.sync_subscription_event({
        "id": "evt_2", "type": "checkout.session.completed",
        "data": {"object": {"customer": "cus_x"}},
    })
    assert (cust, user, status) == (None, None, "")


def test_checkout_enables_promotion_codes():
    # The 50%-off-first-month promo is a Stripe Coupon + Promotion Code; checkout
    # must opt in to the code box or users can never enter it. Lock the flag so a
    # future edit can't silently drop it.
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        url = asyncio.run(sb.create_checkout_url("u_1", "alice", "price_pro"))
    assert url == "https://checkout"
    params = fake_client.checkout.sessions.create.call_args.kwargs["params"]
    assert params["allow_promotion_codes"] is True
    # The subscription must still carry user_id so the webhook can attribute it.
    assert params["subscription_data"]["metadata"]["user_id"] == "u_1"


def _checkout_params(price_id="price_pro"):
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", price_id))
    return fake_client.checkout.sessions.create.call_args.kwargs["params"]


def test_checkout_has_no_free_trial_and_charges_immediately():
    # There is no self-serve trial: checkout must never set trial_period_days —
    # billing starts at subscribe time. Free access exists only as an
    # admin-granted app-managed trial, which never touches Stripe.
    p = _checkout_params()
    assert "trial_period_days" not in p["subscription_data"]
    assert p["mode"] == "subscription"
    assert p["payment_method_types"] == ["card"]
    # Checkout charges exactly the tier price it was asked for.
    assert p["line_items"] == [{"price": "price_pro", "quantity": 1}]


def test_checkout_reuses_existing_stripe_customer():
    # A re-subscribe must land on the SAME Stripe customer — a fresh customer
    # per checkout orphans the old subscription (invisible, unkillable billing).
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", "price_pro", customer_id="cus_A"))
    params = fake_client.checkout.sessions.create.call_args.kwargs["params"]
    assert params["customer"] == "cus_A"
    # And without a known customer, Stripe creates one (param absent).
    fake_client.reset_mock()
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", "price_pro"))
    assert "customer" not in fake_client.checkout.sessions.create.call_args.kwargs["params"]


def test_live_subscription_status_prefers_active_and_fails_open(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    fake_client = MagicMock()
    fake_client.subscriptions.list.return_value = MagicMock(
        data=[{"status": "canceled"}, {"status": "past_due"}, {"status": "active"}])
    with patch.object(sb, "_client", return_value=fake_client):
        assert asyncio.run(sb.live_subscription_status("cus_A")) == "active"
    fake_client.subscriptions.list.return_value = MagicMock(data=[{"status": "canceled"}])
    with patch.object(sb, "_client", return_value=fake_client):
        assert asyncio.run(sb.live_subscription_status("cus_A")) is None
    # Errors must fail OPEN (None) — a Stripe hiccup must never block checkout.
    with patch.object(sb, "_client", side_effect=RuntimeError("boom")):
        assert asyncio.run(sb.live_subscription_status("cus_A")) is None
    assert asyncio.run(sb.live_subscription_status("")) is None


def test_apply_subscription_event_mismatch_targets_customer_owner(monkeypatch):
    # Stale-customer cancellation: metadata names user X, but X's stored customer
    # is B != A. The event must NOT touch X (their B-subscription is healthy);
    # it applies by customer and affects whoever owns A — here, nobody.
    from src.dashboard import api
    from src.auth import users as user_store
    calls = {}
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "stripe_customer_id": "cus_B"})
    monkeypatch.setattr(user_store, "update_subscription",
                        lambda *a: calls.setdefault("by_user", []).append(a))
    monkeypatch.setattr(user_store, "update_subscription_by_customer",
                        lambda cust, status: calls.setdefault("by_cust", []).append((cust, status)) or None)
    affected = api.apply_subscription_event("user_X", "cus_A", "inactive")
    assert affected is None                    # nobody owns cus_A anymore
    assert "by_user" not in calls              # user_X untouched
    assert calls["by_cust"] == [("cus_A", "inactive")]
    # Matching customer → normal per-user update, affected user returned.
    calls.clear()
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "stripe_customer_id": "cus_A"})
    assert api.apply_subscription_event("user_X", "cus_A", "active") == "user_X"
    assert calls["by_user"] == [("user_X", "cus_A", "active")]


def test_paywall_copy_never_promises_free_days():
    from src.dashboard.api import _paywall_copy
    variants = {k: _paywall_copy(k) for k in ("new", "returning", "trial_ended")}
    for kind, c in variants.items():
        joined = " ".join(c.values()).lower()
        # No self-serve trial exists — promising free days is a chargeback
        # waiting to happen. (Mentioning that a granted trial *ended* is fine.)
        assert "free trial" not in c["headline"].lower() or kind == "trial_ended"
        assert "days free" not in joined and "7-day" not in joined
        assert "from $10/month" in c["subline"]
        # No variant leaves template placeholders behind.
        assert all("{" not in v for v in c.values())
    assert "trial has ended" in variants["trial_ended"]["headline"].lower() or \
           "trial" in variants["trial_ended"]["headline"].lower()
    assert "welcome back" in variants["returning"]["subline"].lower()


def test_grant_trial_sets_status_and_expiry(tmp_path, monkeypatch):
    # Admin-granted timed trial: app-managed 'trialing' + trial_ends_at, no
    # Stripe involvement. The middleware/reaper expire it, so the two fields
    # are the entire contract.
    import time
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    u = users.create("bob", "hunter2hunter2")
    granted = users.grant_trial(u["id"], 30)
    assert granted["subscription_status"] == "trialing"
    assert abs(granted["trial_ends_at"] - (time.time() + 30 * 86400)) < 5
    assert sb.has_access("trialing", False) is True   # gate honours the grant
    # Re-granting extends/replaces the window from now.
    again = users.grant_trial(u["id"], 7)
    assert abs(again["trial_ends_at"] - (time.time() + 7 * 86400)) < 5
    # Unknown user → None, nothing written.
    assert users.grant_trial("missing", 7) is None


def test_has_access_matches_active_statuses():
    assert sb.has_access("active", False) is True
    assert sb.has_access("trialing", False) is True
    assert sb.has_access("expired", False) is False
    assert sb.has_access("inactive", False) is False
    # Admins always have access regardless of subscription state.
    assert sb.has_access("none", True) is True
