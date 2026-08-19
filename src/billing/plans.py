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

# How long a self-serve trial lasts. New signups get this automatically, with no
# card — the single source of truth for the number, so the landing page, the
# paywall and the signup path cannot drift apart.
TRIAL_DAYS = 7

# The state a NEW account lands in once its trial runs out: no streams, no
# queue, nothing. Expressed as a plan with zero limits rather than as a new
# gate, because every access check in the product already asks limits_for()
# what this user may do — add_stream, the pending cap, the VOD gate and the
# Clip Editor gate all fail naturally against zeroes. A separate "locked"
# branch would have to be added to each of them and would be forgotten in one.
LOCKED_PLAN = "locked"

PLAN_LIMITS: dict[str, dict] = {
    "locked":  {"label": "Trial ended", "price": 0, "max_streams": 0,
                "max_pending": 0, "vod": False, "uploads": False},
    # LEGACY ONLY. Nothing new ever lands here: accounts that existed before the
    # trial cutover are marked `grandfathered` and keep this permanently, so
    # nobody who was already using the product loses it. New signups get a
    # 7-day trial and then `locked`.
    "free":    {"label": "Free", "price": 0, "max_streams": 1,
                "max_pending": 15, "vod": False, "uploads": False},
    "starter": {"label": "Starter", "price": 10, "max_streams": 3,
                "max_pending": 50, "vod": False, "uploads": False},
    "pro":     {"label": "Pro", "price": 25, "max_streams": 10,
                "max_pending": 200, "vod": True, "uploads": True},
}

PAID_PLANS = ("starter", "pro")
DEFAULT_PLAN = FREE_PLAN

# The pending-clip cap for admins: effectively none.
#
# A LARGE INT RATHER THAN math.inf, and that is not a style preference. This
# number is serialised into the /me payload, and json.dumps(float("inf"))
# emits the bare token `Infinity`, which is not valid JSON — the browser's
# JSON.parse throws on it, so every fetch of /me would fail and the dashboard
# would come up empty for exactly the account that has to be able to fix
# things. An int serialises cleanly and compares the same way against a queue
# nobody will ever fill.
UNLIMITED_PENDING = 1_000_000_000

# Statuses that mean "this person is currently paying us" (or has been granted
# the equivalent). Anything else — none, inactive, expired, cancelled, a typo
# from a future Stripe change — falls through to free rather than to paid,
# because failing open on billing is the expensive direction.
ACTIVE_STATUSES = ("active", "trialing")

# Stripe states that mean "we are still trying to collect", NOT "they left".
#
#   past_due   — a charge failed and Stripe is retrying. Smart Retries run for
#                up to about two weeks before giving up.
#   incomplete — the FIRST payment has not confirmed yet. This is the normal
#                opening state for 3DS/SCA cards: the customer clicks subscribe,
#                Stripe sends `incomplete`, then `active` seconds later.
#
# Both used to fall through to `locked` — zero streams — and fire a "your
# subscription has ended" toast. For past_due that cut off a customer we were
# still billing, for the whole retry window. For incomplete it told somebody
# their subscription had ended *while they were buying it*.
#
# This was not always so harsh, and nobody changed the billing code to make it
# so: before the trial cutover, a non-active status landed on `free` (one
# stream). Adding `locked` turned a soft landing into a total shutout for every
# status in this list.
#
# `unpaid` and `incomplete_expired` are deliberately NOT here — those are Stripe
# having given up, which is a real end.
GRACE_STATUSES = ("past_due", "incomplete")


def get_plan(user: dict | None) -> str:
    """Resolve the effective plan for a user dict (public or full)."""
    if not user:
        # No user in hand — a deleted account still holding a live session, or a
        # lookup that missed. The least access, not the free tier: both real
        # callers pass get_by_id(uid), which returns None exactly when the
        # account is gone, and handing that case a stream slot is failing open
        # on billing. (An empty dict is falsy and lands here too, which is the
        # correct answer for it.)
        return LOCKED_PLAN
    if user.get("is_admin") or user.get("is_labeler"):
        return "pro"

    status = user.get("subscription_status")
    if status == "trialing":
        # A trial granted with an explicit tier honours it, so an admin can comp
        # someone Starter for a month. Without one it stays Pro — that is the
        # original behaviour (a trial showcases the full product), and it is
        # what every trial granted before tiers were selectable resolves to.
        plan = user.get("plan")
        return plan if plan in PAID_PLANS else "pro"
    if status in GRACE_STATUSES:
        # Still a customer — Stripe is mid-collection. Keep them on the tier
        # they are being billed for rather than locking them out of a product
        # they have not stopped paying for. If the retries ultimately fail,
        # Stripe sends `unpaid`/`canceled` and they fall through below.
        plan = user.get("plan")
        if plan in PAID_PLANS:
            return plan
        # No stored tier, so the two grace statuses part company here, on what
        # they actually imply about money having changed hands:
        #
        #   past_due   — you cannot reach it without a successful charge first.
        #                This is a legacy $15-era subscriber (active, no stored
        #                plan) whose card has now failed. Grandfather them, same
        #                as the active branch below does.
        #   incomplete — the FIRST payment never confirmed, so nobody has paid
        #                anything. Granting full access here would hand Pro to
        #                anyone who opens a checkout and abandons it, for the
        #                ~23 hours Stripe waits before `incomplete_expired`.
        if status == "past_due":
            return LEGACY_PAID_PLAN
        return FREE_PLAN if user.get("grandfathered") else LOCKED_PLAN
    if status != "active":
        # Never subscribed, cancelled, lapsed, or an expired trial.
        #
        # GRANDFATHERED accounts — everyone who existed before the self-serve
        # trial replaced the free tier — keep the free plan indefinitely. That
        # flag is set once, by a migration at boot, and never on a new account.
        # It is an explicit mark rather than a date comparison or an inference
        # from `status` because a lapsed NEW subscriber must land on `locked`
        # while a legacy user on the identical status keeps free, and no amount
        # of reading created_at or subscription_status can tell those apart.
        return FREE_PLAN if user.get("grandfathered") else LOCKED_PLAN

    plan = user.get("plan")
    if plan in PAID_PLANS:
        return plan
    return LEGACY_PAID_PLAN     # grandfathered $15-era subscriber


def limits_for(user: dict | None) -> dict:
    """The limits actually in force for this user.

    ADMINS HAVE NO PENDING-CLIP CAP. Everything that enforces the cap —
    pending_room() before the Twitch clip is created, the second check in
    notify_clip_ready, the queue meter and the /me payload — reads it from
    here, so lifting it in this one place lifts it everywhere rather than
    leaving a path that still refuses at 200.

    Done here rather than by giving admins their own entry in PLAN_LIMITS: the
    plan NAME is load-bearing elsewhere (billing copy, upgrade prompts,
    is_paid, the Stripe reconciler all switch on it), and inventing an "admin"
    plan would make every one of those places grow a case for a plan nobody is
    billed for. get_plan() keeps saying "pro" and only the number changes.

    Returns a COPY. PLAN_LIMITS holds one shared dict per plan, so building the
    override by mutating the value would rewrite the cap for every pro user on
    the box, permanently and invisibly.
    """
    limits = PLAN_LIMITS[get_plan(user)]
    if user and user.get("is_admin"):
        return {**limits, "max_pending": UNLIMITED_PENDING}
    return limits


def is_paid(user: dict | None) -> bool:
    """Whether this user is on a paying tier. Use for upgrade prompts — NOT for
    access control, which should ask limits_for() what is actually allowed."""
    return get_plan(user) in PAID_PLANS
