"""Stripe subscription billing helpers."""

import stripe
import structlog
from config.settings import settings

log = structlog.get_logger(__name__)

# Statuses that mean the user has paid access
ACTIVE_STATUSES = {"active", "trialing"}


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(settings.stripe_secret_key)


def has_access(subscription_status: str, is_admin: bool) -> bool:
    """Return True if this user may access the dashboard."""
    return is_admin or subscription_status in ACTIVE_STATUSES


async def create_checkout_url(user_id: str, username: str) -> str:
    """Create a Stripe Checkout session and return its URL."""
    client = _client()
    base = settings.discord_redirect_uri.rsplit("/", 2)[0]  # derive base URL
    session = client.checkout.sessions.create(params={
        "mode":                 "subscription",
        "payment_method_types": ["card"],
        "line_items":           [{"price": settings.stripe_price_id, "quantity": 1}],
        "success_url":          f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url":           f"{base}/billing/cancel",
        "metadata":             {"user_id": user_id, "username": username},
        "subscription_data":    {"metadata": {"user_id": user_id}},
    })
    return session.url


async def create_portal_url(customer_id: str) -> str:
    """Create a Stripe Customer Portal session and return its URL."""
    client = _client()
    base = settings.discord_redirect_uri.rsplit("/", 2)[0]
    session = client.billing_portal.sessions.create(params={
        "customer":   customer_id,
        "return_url": f"{base}/",
    })
    return session.url


def handle_webhook_event(payload: bytes, sig_header: str) -> dict:
    """Verify and parse a Stripe webhook event. Raises on invalid signature."""
    return stripe.Webhook.construct_event(
        payload, sig_header, settings.stripe_webhook_secret
    )


def sync_subscription_event(event: dict) -> tuple[str | None, str | None, str]:
    """
    Extract (customer_id, user_id, status) from a subscription lifecycle event.
    Returns (None, None, '') if the event is not subscription-related.
    """
    etype = event.get("type", "")
    if not etype.startswith("customer.subscription"):
        return None, None, ""

    sub      = event["data"]["object"]
    cust_id  = sub.get("customer")
    raw_status = sub.get("status", "")
    user_id  = sub.get("metadata", {}).get("user_id")

    # Map Stripe statuses to our simple model
    if raw_status in ACTIVE_STATUSES:
        status = "active"
    elif raw_status in ("canceled", "unpaid", "incomplete_expired"):
        status = "inactive"
    else:
        status = raw_status

    return cust_id, user_id, status
