"""
Membership tiers.

  free    — $0: 1 monitored stream, 15 pending clips. No VOD scanner, no Clip
            Editor. Exists so someone can use the actual product before paying;
            a cold visitor will not hand over $10 to find out whether the
            detector works on their channel.
  starter — $10/month: 3 monitored streams, 50 pending clips
  pro     — $25/month: 10 monitored streams, 200 pending clips, VOD scanner,
            Clip Editor

WHY FREE IS DELIBERATELY THIN. Every monitored stream runs a streamlink+ffmpeg
audio meter on a single shared vCPU — that is the scarce resource in this whole
product, not disk or bandwidth. One stream per free user is what makes a large
free population survivable at all. Raising it is not a config tweak; read the
capacity note in HANDOFF first.

PLAN RESOLUTION (get_plan) — the ordering matters and each rule is load-bearing:
  1. Admins and labelers get 'pro'. The training team needs the full product
     without a subscription.
  2. An admin-granted trial ('trialing') gets 'pro' — that is the point of it.
  3. WITHOUT an active subscription you get 'free'. Not locked out: a cancelled
     or lapsed subscriber keeps using the product on the free tier. Before the
     free tier existed, lapsing meant a paywall and nothing else, and silently
     keeping their stored `plan` would hand a former subscriber Pro forever.
  4. WITH an active subscription, the stored `plan` (set by the Stripe webhook
     from the subscription's price id) decides.
  5. An active subscription with NO stored plan is a legacy single-price
     ($15-era) subscriber. They are grandfathered as 'pro' — they paid for full
     access and a pricing change must never strip features from an existing
     customer. This is the ONLY case where a missing plan means paid, which is
     why it is checked last and requires status == 'active'.
"""

FREE_PLAN = "free"
LEGACY_PAID_PLAN = "pro"      # what a pre-tiers subscriber is grandfathered to

PLAN_LIMITS: dict[str, dict] = {
    "free":    {"label": "Free", "price": 0, "max_streams": 1,
                "max_pending": 15, "vod": False, "uploads": False},
    "starter": {"label": "Starter", "price": 10, "max_streams": 3,
                "max_pending": 50, "vod": False, "uploads": False},
    "pro":     {"label": "Pro", "price": 25, "max_streams": 10,
                "max_pending": 200, "vod": True, "uploads": True},
}

PAID_PLANS = ("starter", "pro")
DEFAULT_PLAN = FREE_PLAN

# Statuses that mean "this person is currently paying us" (or has been granted
# the equivalent). Anything else — none, inactive, expired, cancelled, a typo
# from a future Stripe change — falls through to free rather than to paid,
# because failing open on billing is the expensive direction.
ACTIVE_STATUSES = ("active", "trialing")


def get_plan(user: dict | None) -> str:
    """Resolve the effective plan for a user dict (public or full)."""
    if not user:
        return FREE_PLAN
    if user.get("is_admin") or user.get("is_labeler"):
        return "pro"

    status = user.get("subscription_status")
    if status == "trialing":
        return "pro"            # admin-granted trials showcase the full product
    if status != "active":
        return FREE_PLAN        # never subscribed, cancelled, or lapsed

    plan = user.get("plan")
    if plan in PAID_PLANS:
        return plan
    return LEGACY_PAID_PLAN     # grandfathered $15-era subscriber


def limits_for(user: dict | None) -> dict:
    return PLAN_LIMITS[get_plan(user)]


def is_paid(user: dict | None) -> bool:
    """Whether this user is on a paying tier. Use for upgrade prompts — NOT for
    access control, which should ask limits_for() what is actually allowed."""
    return get_plan(user) in PAID_PLANS
