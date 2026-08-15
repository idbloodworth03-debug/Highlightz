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


async def create_checkout_url(user_id: str, username: str, price_id: str,
                              customer_id: str | None = None) -> str:
    """Create a Stripe Checkout session for the given recurring price and
    return its URL.

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
        "line_items":           [{"price": price_id, "quantity": 1}],
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


# Portal configuration id resolved at runtime; cached so we don't re-list /
# re-create it on every "Manage billing" click.
_portal_config_id: str | None = None


def _ensure_portal_configuration(client: stripe.StripeClient) -> str:
    """Return a usable Customer Portal configuration id, creating one via the
    API if the account has none.

    Stripe refuses to open ANY portal session until a configuration exists —
    normally saved once by hand in the dashboard. If that step was never done,
    every "Manage billing" click 500s. Building the configuration here makes
    the portal work with zero dashboard setup, and gets cancel / invoices /
    card-update (plus plan switching when both tier prices are configured).
    """
    existing = client.billing_portal.configurations.list(
        params={"active": True, "limit": 10})
    for cfg in (existing.data if hasattr(existing, "data") else []):
        cfg_id = cfg.get("id") if isinstance(cfg, dict) else cfg.id
        if cfg_id:
            return cfg_id

    features: dict = {
        "customer_update":       {"enabled": True, "allowed_updates": ["email"]},
        "invoice_history":       {"enabled": True},
        "payment_method_update": {"enabled": True},
        "subscription_cancel":   {"enabled": True, "mode": "at_period_end"},
    }
    # Plan switching (Starter <-> Pro). subscription_update needs product ids,
    # which we don't store — resolve them from the configured prices. Skipped
    # (not fatal) if prices are unset or lookup fails: the portal still opens.
    price_ids = [p for p in (settings.stripe_price_id_starter,
                             settings.stripe_price_id_pro) if p]
    if len(price_ids) == 2:
        try:
            products = []
            for pid in price_ids:
                price = client.prices.retrieve(pid)
                product = price.get("product") if isinstance(price, dict) else price.product
                products.append({"product": product, "prices": [pid]})
            features["subscription_update"] = {
                "enabled":                 True,
                "default_allowed_updates": ["price"],
                "products":                products,
                "proration_behavior":      "create_prorations",
            }
        except Exception as exc:
            log.warning("stripe_portal_plan_switch_unavailable", error=str(exc))

    cfg = client.billing_portal.configurations.create(params={
        "business_profile": {
            "privacy_policy_url":   "https://highlightz.app/privacy",
            "terms_of_service_url": "https://highlightz.app/tos",
        },
        "features": features,
    })
    cfg_id = cfg.get("id") if isinstance(cfg, dict) else cfg.id
    log.info("stripe_portal_configuration_created", configuration=cfg_id)
    return cfg_id


async def create_portal_url(customer_id: str) -> str:
    """Create a Stripe Customer Portal session and return its URL.

    Self-healing: if the account has no saved portal configuration (the usual
    reason this endpoint dies), build one via the API and retry once.
    """
    global _portal_config_id
    client = _client()
    base = "https://highlightz.app"
    params: dict = {"customer": customer_id, "return_url": f"{base}/"}
    if _portal_config_id:
        params["configuration"] = _portal_config_id
    try:
        session = client.billing_portal.sessions.create(params=params)
    except Exception as exc:
        if "configuration" not in str(exc).lower():
            raise
        log.warning("stripe_portal_missing_config", error=str(exc))
        _portal_config_id = _ensure_portal_configuration(client)
        params["configuration"] = _portal_config_id
        session = client.billing_portal.sessions.create(params=params)
    return session.url


async def cancel_customer_subscriptions(customer_id: str) -> int | None:
    """Cancel all active Stripe subscriptions for a customer.

    Returns the number cancelled, or None if Stripe could not be reached.

    That distinction is the point. This used to return 0 for BOTH "there was
    nothing to cancel" and "the API call blew up", which are opposite facts: one
    means the customer is not being charged, the other means they are and we do
    not know it. An admin revoking access needs to be told the difference, or
    they walk away believing billing stopped when it did not.
    """
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
        return None          # NOT 0 — see the docstring; this one means "unknown"


def extract_price_id(event: dict) -> str | None:
    """The recurring price id on a subscription lifecycle event — this is what
    tells us which membership tier the subscription is for."""
    if not event.get("type", "").startswith("customer.subscription"):
        return None
    sub = event["data"]["object"]
    items = ((sub.get("items") or {}).get("data")) or []
    for item in items:
        price = item.get("price") if isinstance(item, dict) else None
        if isinstance(price, dict) and price.get("id"):
            return price["id"]
        if isinstance(price, str) and price:
            return price
    # Some event shapes carry a top-level plan (legacy API versions)
    plan = sub.get("plan")
    if isinstance(plan, dict) and plan.get("id"):
        return plan["id"]
    return None


def plan_for_price(price_id: str | None) -> str | None:
    """Map a Stripe price id to a membership plan. The legacy single-price id
    ($15 era) maps to 'pro' — existing subscribers are grandfathered with full
    access. Unknown/missing prices return None (caller keeps the stored plan)."""
    if not price_id:
        return None
    if price_id == settings.stripe_price_id_starter:
        return "starter"
    if price_id == settings.stripe_price_id_pro:
        return "pro"
    if price_id == settings.stripe_price_id:
        return "pro"   # grandfathered legacy $15 subscribers
    return None


async def customer_email(customer_id: str) -> str | None:
    """Billing email for a Stripe customer. Best-effort: None on any failure —
    the duplicate-email guard must fail OPEN (never touch a subscription on
    flaky data)."""
    if not (settings.stripe_secret_key and customer_id):
        return None
    try:
        client = _client()
        cust = client.customers.retrieve(customer_id)
        email = cust.get("email") if isinstance(cust, dict) else cust.email
        return email.strip().lower() if email else None
    except Exception as exc:
        log.warning("stripe_customer_email_failed", customer=customer_id, error=str(exc))
        return None


async def refund_and_cancel_subscription(sub: dict) -> bool:
    """Duplicate-signup remediation: refund the subscription's initial payment,
    then cancel it. REFUND FIRST — if the refund fails we abort and leave the
    subscription (and the user's access) intact for manual handling, because
    the one unacceptable outcome is a customer who paid AND lost access.
    Returns True only when both steps succeeded."""
    if not settings.stripe_secret_key:
        return False
    sub_id = sub.get("id")
    if not sub_id:
        return False
    try:
        client = _client()
        # Find the payment behind the first invoice.
        inv_id = sub.get("latest_invoice")
        if isinstance(inv_id, dict):
            invoice = inv_id
        elif inv_id:
            invoice = client.invoices.retrieve(inv_id)
        else:
            invoice = None
        pi = charge = None
        if invoice is not None:
            get = invoice.get if isinstance(invoice, dict) else lambda k, d=None: getattr(invoice, k, d)
            pi = get("payment_intent")
            charge = get("charge")
            if isinstance(pi, dict):
                pi = pi.get("id")
            if isinstance(charge, dict):
                charge = charge.get("id")
            amount_paid = get("amount_paid") or 0
        else:
            amount_paid = 0
        if amount_paid > 0:
            if pi:
                client.refunds.create(params={"payment_intent": pi})
            elif charge:
                client.refunds.create(params={"charge": charge})
            else:
                log.error("duplicate_refund_no_payment_ref", subscription=sub_id)
                return False
        client.subscriptions.cancel(sub_id)
        log.info("duplicate_subscription_refunded_and_cancelled",
                 subscription=sub_id, refunded_cents=amount_paid)
        return True
    except Exception as exc:
        log.error("duplicate_remediation_failed", subscription=sub_id, error=str(exc))
        return False


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
