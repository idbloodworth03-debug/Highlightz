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


def test_has_access_matches_active_statuses():
    assert sb.has_access("active", False) is True
    assert sb.has_access("trialing", False) is True
    assert sb.has_access("expired", False) is False
    assert sb.has_access("inactive", False) is False
    # Admins always have access regardless of subscription state.
    assert sb.has_access("none", True) is True
