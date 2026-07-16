"""
Membership tiers. Two paid plans:

  starter — $10/month: 3 monitored streams, 50 pending clips, no VOD scanner
  pro     — $25/month: 10 monitored streams, 200 pending clips, VOD scanner

Plan resolution rules (get_plan):
  - Admins and admin-granted trials get the full 'pro' experience.
  - A user's stored `plan` (set by the Stripe webhook from the subscription's
    price id) wins otherwise.
  - Legacy subscribers from the single-price $15 era have no `plan` field —
    they're grandfathered as 'pro' (they paid for full access; a pricing
    change must never strip features from existing customers).

Enforcement is backend-side (add-stream limit, pending-clip cap, VOD gate);
the dashboard mirrors the limits for display only.
"""

PLAN_LIMITS: dict[str, dict] = {
    "starter": {"label": "Starter", "price": 10, "max_streams": 3,
                "max_pending": 50, "vod": False},
    "pro":     {"label": "Pro", "price": 25, "max_streams": 10,
                "max_pending": 200, "vod": True},
}

DEFAULT_PLAN = "pro"   # legacy/grandfathered accounts


def get_plan(user: dict | None) -> str:
    """Resolve the effective plan for a user dict (public or full)."""
    if not user:
        return DEFAULT_PLAN
    if user.get("is_admin"):
        return "pro"
    if user.get("subscription_status") == "trialing":
        return "pro"   # admin-granted trials showcase the full product
    plan = user.get("plan")
    return plan if plan in PLAN_LIMITS else DEFAULT_PLAN


def limits_for(user: dict | None) -> dict:
    return PLAN_LIMITS[get_plan(user)]
