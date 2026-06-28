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


async def create_checkout_url(user_id: str, username: str, trial_days: int = 0) -> str:
    """Create a Stripe Checkout session and return its URL.

    A card is ALWAYS collected. When trial_days > 0 the subscription starts with
    that many days free (Stripe charges the card automatically when the trial
    ends unless the user cancels). trial_days == 0 charges immediately — used for
    returning users who have already used their one free trial.
    """
    client = _client()
    base = "https://highlightz.app"
    sub_data: dict = {"metadata": {"user_id": user_id}}
    if trial_days > 0:
        sub_data["trial_period_days"] = trial_days
    session = client.checkout.sessions.create(params={
        "mode":                 "subscription",
        "payment_method_types": ["card"],
        "line_items":           [{"price": settings.stripe_price_id, "quantity": 1}],
        "success_url":          f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url":           f"{base}/billing/cancel",
        # Always require a card, even when starting a free trial.
        "payment_method_collection": "always",
        "metadata":             {"user_id": user_id, "username": username},
        "subscription_data":    sub_data,
        # Show the "Add promotion code" box on the hosted checkout page. The
        # actual discount (50% off first month) is a Coupon + Promotion Code
        # created in the Stripe dashboard (duration: once). Stripe validates the
        # code, enforces redemption limits/expiry, and applies it — no discount
        # logic lives here.
        "allow_promotion_codes": True,
    })
    return session.url


async def create_portal_url(customer_id: str) -> str:
    """Create a Stripe Customer Portal session and return its URL."""
    client = _client()
    base = "https://highlightz.app"
    session = client.billing_portal.sessions.create(params={
        "customer":   customer_id,
        "return_url": f"{base}/",
    })
    return session.url


async def cancel_customer_subscriptions(customer_id: str) -> int:
    """Cancel all active Stripe subscriptions for a customer. Returns count cancelled."""
    if not settings.stripe_secret_key or not customer_id:
        return 0
    try:
        client = _client()
        subs = client.subscriptions.list(params={"customer": customer_id, "status": "active", "limit": 10})
        items = subs.data if hasattr(subs, "data") else []
        cancelled = 0
        for sub in items:
            sub_id = sub.get("id") if isinstance(sub, dict) else sub.id
            client.subscriptions.cancel(sub_id)
            cancelled += 1
        if cancelled:
            log.info("stripe_subscriptions_cancelled", customer=customer_id, count=cancelled)
        return cancelled
    except Exception as exc:
        log.error("stripe_cancel_failed", customer=customer_id, error=str(exc))
        return 0


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
