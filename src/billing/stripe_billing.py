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


async def create_checkout_url(user_id: str, username: str,
                              customer_id: str | None = None) -> str:
    """Create a Stripe Checkout session and return its URL.

    Billing starts immediately — there is no self-serve free trial. (Free access
    is granted only by an admin through the dashboard, app-managed via
    subscription_status='trialing' + trial_ends_at, with no Stripe involvement.)

    When the user already has a Stripe customer, pass customer_id so the new
    subscription lands on the SAME customer — otherwise every checkout mints a
    fresh customer, and a re-subscribe orphans the old one (portal/cancel/sync
    all key off the single stored id).
    """
    client = _client()
    base = "https://highlightz.app"
    params: dict = {
        "mode":                 "subscription",
        "payment_method_types": ["card"],
        "line_items":           [{"price": settings.stripe_price_id, "quantity": 1}],
        "success_url":          f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url":           f"{base}/billing/cancel",
        "metadata":             {"user_id": user_id, "username": username},
        "subscription_data":    {"metadata": {"user_id": user_id}},
        # Show the "Add promotion code" box on the hosted checkout page. The
        # actual discount (50% off first month) is a Coupon + Promotion Code
        # created in the Stripe dashboard (duration: once). Stripe validates the
        # code, enforces redemption limits/expiry, and applies it — no discount
        # logic lives here.
        "allow_promotion_codes": True,
    }
    if customer_id:
        params["customer"] = customer_id
    session = client.checkout.sessions.create(params=params)
    return session.url


async def live_subscription_status(customer_id: str) -> str | None:
    """Best-effort LIVE check of a customer's subscription state, for the
    checkout guard: right after a payment there's a window where Stripe knows
    the subscription exists but our webhook hasn't landed yet — opening another
    checkout in that window mints a duplicate subscription. Returns the most
    relevant status ('active' > 'trialing' > 'past_due') or None when the
    customer has none / on any error (fail-open: never block checkout on a
    Stripe hiccup)."""
    if not (settings.stripe_secret_key and customer_id):
        return None
    try:
        client = _client()
        subs = client.subscriptions.list(params={"customer": customer_id,
                                                 "status": "all", "limit": 10})
        items = subs.data if hasattr(subs, "data") else []
        statuses = {(s.get("status") if isinstance(s, dict) else s.status) for s in items}
        for pref in ("active", "trialing", "past_due"):
            if pref in statuses:
                return pref
        return None
    except Exception as exc:
        log.warning("stripe_live_status_failed", customer=customer_id, error=str(exc))
        return None


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


def extract_promo_id(event: dict) -> str | None:
    """Promotion-code id (promo_...) from a subscription lifecycle event, if a
    promo code was applied at checkout. Handles both the legacy single
    `discount` field and the newer `discounts` list; each may carry the
    promotion_code as an id string or an expanded object."""
    if not event.get("type", "").startswith("customer.subscription"):
        return None
    sub = event["data"]["object"]
    candidates = []
    if sub.get("discount"):
        candidates.append(sub["discount"])
    candidates.extend(d for d in (sub.get("discounts") or []) if isinstance(d, dict))
    for disc in candidates:
        promo = disc.get("promotion_code")
        if isinstance(promo, dict):
            promo = promo.get("id")
        if promo:
            return promo
    return None


# promo_id -> human code text ("NOVA50"); ids are immutable so cache forever.
_promo_code_cache: dict[str, str] = {}


async def resolve_promo_code(promo_id: str) -> str | None:
    """Human-readable code text for a promotion-code id, for per-streamer
    signup attribution. Best-effort: returns None on any error — attribution
    is bookkeeping and must never break webhook processing."""
    if promo_id in _promo_code_cache:
        return _promo_code_cache[promo_id]
    if not settings.stripe_secret_key:
        return None
    try:
        client = _client()
        obj = client.promotion_codes.retrieve(promo_id)
        code = obj.get("code") if isinstance(obj, dict) else obj.code
        if code:
            _promo_code_cache[promo_id] = code
        return code
    except Exception as exc:
        log.warning("promo_code_resolve_failed", promo=promo_id, error=str(exc))
        return None


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
