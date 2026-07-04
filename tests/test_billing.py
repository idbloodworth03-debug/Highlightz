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
        url = asyncio.run(sb.create_checkout_url("u_1", "alice"))
    assert url == "https://checkout"
    params = fake_client.checkout.sessions.create.call_args.kwargs["params"]
    assert params["allow_promotion_codes"] is True
    # The subscription must still carry user_id so the webhook can attribute it.
    assert params["subscription_data"]["metadata"]["user_id"] == "u_1"


def _checkout_params(trial_days):
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", trial_days=trial_days))
    return fake_client.checkout.sessions.create.call_args.kwargs["params"]


def test_checkout_with_trial_sets_trial_period_and_requires_card():
    p = _checkout_params(7)
    assert p["subscription_data"]["trial_period_days"] == 7
    # A card is always collected, even when starting a free trial.
    assert p["payment_method_collection"] == "always"
    assert p["payment_method_types"] == ["card"]


def test_checkout_without_trial_charges_immediately():
    # Returning users (already used their one trial) get no trial_period_days.
    p = _checkout_params(0)
    assert "trial_period_days" not in p["subscription_data"]
    assert p["payment_method_collection"] == "always"


def test_checkout_reuses_existing_stripe_customer():
    # A re-subscribe must land on the SAME Stripe customer — a fresh customer
    # per checkout orphans the old subscription (invisible, unkillable billing).
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", customer_id="cus_A"))
    params = fake_client.checkout.sessions.create.call_args.kwargs["params"]
    assert params["customer"] == "cus_A"
    # And without a known customer, Stripe creates one (param absent).
    fake_client.reset_mock()
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice"))
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


def test_paywall_copy_is_honest_about_trial_eligibility():
    from src.dashboard.api import _paywall_copy
    fresh = _paywall_copy(True)
    assert "7-day free trial" in fresh["headline"] and "won't be charged" in fresh["subline"]
    returning = _paywall_copy(False)
    assert "free trial" not in returning["cta"].lower()
    assert "$15/month" in returning["cta"]
    assert "already been used" in returning["subline"]
    # Neither variant leaves template placeholders behind.
    for c in (fresh, returning):
        assert all("{" not in v for v in c.values())


def test_trial_claim_id_prefers_twitch_then_kick():
    from src.auth import users
    assert users.trial_claim_id({"twitch_id": "123"}) == "123"
    assert users.trial_claim_id({"kick_id": "9"}) == "kick:9"
    assert users.trial_claim_id({}) == ""


def test_has_access_matches_active_statuses():
    assert sb.has_access("active", False) is True
    assert sb.has_access("trialing", False) is True
    assert sb.has_access("expired", False) is False
    assert sb.has_access("inactive", False) is False
    # Admins always have access regardless of subscription state.
    assert sb.has_access("none", True) is True
