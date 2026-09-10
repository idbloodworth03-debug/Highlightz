"""
Membership tiers.

  free    — $0, no card, no time limit: 1 monitored stream, 20 pending clips,
            5 suggested clips. No VOD scanner, no Clip Editor.
  starter — $10/month: 3 monitored streams, 50 pending clips
  pro     — $25/month: 10 monitored streams, 200 pending clips, VOD scanner,
            Clip Editor

THE 7-DAY TRIAL IS GONE (2026-08-26) AND FREE IS THE FRONT DOOR AGAIN.

The trial replaced the free tier because an app-managed free week with no card
converted badly. Card-up-front fixed the conversion problem and created a worse
one: the card IS the wall. Somebody who wants to find out whether the detector
works on their channel has to commit payment details first, and most will not —
so the top of the funnel was the thing being optimised away.

Free is now permanent and deliberately thin. It is not a sampler with a clock
on it; it is the smallest version of the product that still proves the product
works. One stream, twenty clips in the queue, and the crowd suggestions — which
are the part most likely to produce a moment worth paying for, so they get their
own budget rather than competing for the twenty.

WHAT THIS MEANS FOR EXISTING TRIALS: nothing. `trialing` still resolves to pro,
so anyone Stripe is currently running a trial for finishes it on the terms they
signed up under and converts or lapses exactly as before. Only NEW checkouts
stop carrying trial days.

WHY FREE IS DELIBERATELY THIN. Every monitored stream runs a streamlink+ffmpeg
audio meter on a single shared vCPU — that is the scarce resource in this whole
product, not disk or bandwidth. One stream per free user is what makes a large
free population survivable at all. Raising it is not a config tweak; read the
capacity note in HANDOFF first.

PLAN RESOLUTION (get_plan) — the ordering matters and each rule is load-bearing:
  1. Admins and labelers get 'pro'. The training team needs the full product
     without a subscription.
  2. A trial ('trialing') gets 'pro' — Stripe trials still in flight and
     admin-granted comps both land here.
  3. WITHOUT an active subscription you get 'free'. Not locked out: never
     subscribed, cancelled, lapsed and finished-trial all land on the same
     tier, and that is the point of reopening it. Silently keeping their stored
     `plan` would hand a former subscriber Pro forever, which is why the stored
     value is only consulted below this line.
  4. WITH an active subscription, the stored `plan` (set by the Stripe webhook
     from the subscription's price id) decides.
  5. An active subscription with NO stored plan is a legacy single-price
     ($15-era) subscriber. They are grandfathered as 'pro' — they paid for full
     access and a pricing change must never strip features from an existing
     customer. This is the ONLY case where a missing plan means paid, which is
     why it is checked last and requires status == 'active'.
"""

import time

FREE_PLAN = "free"
LEGACY_PAID_PLAN = "pro"      # what a pre-tiers subscriber is grandfathered to

# The zero-access plan. NOTHING RESOLVES TO IT ANY MORE except a missing user
# record — free is the floor for every real account now. It is kept because
# `not user` still has to mean "no access" (a deleted account holding a live
# session), and because expressing that as a plan with zero limits is what lets
# every existing check — add_stream, the pending cap, the VOD gate, the Clip
# Editor gate — refuse naturally by asking limits_for(), instead of each
# growing its own special case that one of them would forget.
LOCKED_PLAN = "locked"

# HOW MANY CROWD SUGGESTIONS MAY BE WAITING AT ONCE, and why this is a separate
# budget rather than a slice of max_pending.
#
# Suggestions are moments the DETECTOR MISSED, surfaced because viewers clipped
# them (src/trigger/suggested_clips.py). They used to share the pending queue,
# which meant they competed with the clips a user pays for — and a full queue
# drops the newest arrival, so a chatty channel's suggestions could be the
# reason a triggered clip never landed. That was held off with a 50% reserve.
#
# A separate budget makes the same guarantee structurally instead of by
# arithmetic: a suggestion can never occupy a slot a real clip wanted, because
# it is not drawing from the same pool at all. It also lets the free tier do
# what it is for — twenty of our clips AND five suggested ones, rather than
# five eating into the twenty.
#
# RAISED 2026-08-26, 3/15/50 -> 5/25/75. These are moments a human framed, so
# they hold up better than the detector's own picks and the product leans on
# them deliberately. Note which number actually throttles delivery: for anyone
# who reviews their queue it is MAX_PER_HOUR in suggested_clips.py, not this —
# raising this alone would only let more pile up unreviewed, so both moved.
# A LARGE INT RATHER THAN math.inf, and that is not a style preference. This
# number is serialised into the /me payload, and json.dumps(float("inf"))
# emits the bare token `Infinity`, which is not valid JSON — the browser's
# JSON.parse throws on it, so every fetch of /me would fail and the dashboard
# would come up empty for exactly the account that has to be able to fix
# things. An int serialises cleanly and compares the same way against a queue
# nobody will ever fill.
UNLIMITED_PENDING = 1_000_000_000

_SUGGESTED = "max_suggested"

# ── The weekly library allowance ──────────────────────────────────────────────
# max_pending caps the REVIEW QUEUE — how many undecided clips may be waiting.
# This caps the LIBRARY — how many clips you may KEEP in a rolling seven days.
# They are different questions and a user can hit either one first.
#
# ROLLING SEVEN DAYS, not a calendar week. A Monday reset means everyone who
# joins on a Saturday gets a two-day first "week", and it produces a stampede
# every Monday morning. A rolling window treats every account the same on the
# day it signs up.
#
# COUNTED FROM WHAT IS IN THE LIBRARY, so deleting a clip gives the slot back.
# The alternative — counting approvals from the append-only ledger — is a truer
# rate limit and cannot be worked around, but it means a user with five clips
# in an empty library is told they are out of room, which reads as a bug. The
# cap is on what you STORE, so it counts what is stored.
_LIB_WEEK = "max_library_week"

# WHETHER A HIGHLIGHT MAY TAKE A SLOT OFF A TRIGGERED CLIP.
#
# Free only, on purpose. Highlight clips are moments a human framed and they
# hold up better than the detector's own picks, so on the one tier where the
# queue is genuinely tight they are worth more than the weakest triggered clip
# sitting next to them. Every paid tier has room for both and never needs the
# trade — Starter holds fifty triggered clips and twenty-five Highlights, and
# a queue that full is a review backlog rather than a capacity problem.
#
# This is a NARROW re-introduction of eviction, which was removed in 2026-08-03
# because a full queue silently destroying the user's existing clips is
# hostile. What makes it defensible here: it only ever displaces a triggered
# clip, only for something rated higher, only on free, only the weakest one,
# and it tells the tab it happened. See notify_clip_ready.
_HL_PRIORITY = "highlight_priority"

PLAN_LIMITS: dict[str, dict] = {
    # "Trial ended" was accurate while every account began with a free week.
    # It is not any more, and with free reopened nothing lands here at all —
    # the label survives for the one caller that can still reach it.
    "locked":  {"label": "Not subscribed", "price": 0, "max_streams": 0,
                "max_pending": 0, _SUGGESTED: 0, _LIB_WEEK: 0,
                "vod": False, "uploads": False, _HL_PRIORITY: False},
    # THE FRONT DOOR. No card, no clock. Deliberately the smallest version of
    # the product that still proves it works: one channel, twenty clips in the
    # queue, and five suggested clips on top of those — see _SUGGESTED.
    "free":    {"label": "Free", "price": 0, "max_streams": 1,
                "max_pending": 20, _SUGGESTED: 5, _LIB_WEEK: 30,
                "vod": False, "uploads": False, _HL_PRIORITY: True},
    "starter": {"label": "Starter", "price": 10, "max_streams": 3,
                "max_pending": 50, _SUGGESTED: 25, _LIB_WEEK: 100,
                "vod": False, "uploads": False, _HL_PRIORITY: False},
    # Pro is the tier with no ceiling on what you keep, which is most of why
    # somebody moves up from Starter.
    "pro":     {"label": "Pro", "price": 25, "max_streams": 10,
                "max_pending": 200, _SUGGESTED: 75, _LIB_WEEK: UNLIMITED_PENDING,
                "vod": True, "uploads": True, _HL_PRIORITY: False},
}

PAID_PLANS = ("starter", "pro")
DEFAULT_PLAN = FREE_PLAN

# The pending-clip cap for admins: effectively none.
#
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


# Where an account has got to on the way from "connected Twitch" to "paying",
# newest stage last. Lives here rather than in the admin panel because the
# diagnostic script and the panel must never disagree about what stage somebody
# is at — that is precisely the confusion this exists to end.
#
# STAGE IS NOT THE SAME QUESTION AS PLAN. get_plan answers "what may they do";
# this answers "how far did they get, and is that where they meant to stop".
FUNNEL_STAGES = (
    "staff",             # admin/trainer — never in the funnel
    "legacy",            # pre-cutover account on its own terms
    "signed_up",         # connected Twitch, on the free tier
    "checkout_started",  # reached Stripe's card form, no subscription yet
    "checkout_dropped",  # a Stripe customer exists but no subscription
    "trialing",          # card on file, not yet charged
    "paying",            # charged
    "past_due",          # card failing, Stripe retrying
    "lapsed",            # had access, does not now
)

FUNNEL_LABELS = {
    "staff":            "Staff",
    "legacy":           "Legacy account",
    # NOT "never opened checkout". That wording is left over from when free
    # was closed and a signup with no payment really was an incomplete
    # journey. Free is a real tier now, so this is where a lot of people are
    # MEANT to end up — describing it as a failure to pay is both wrong and
    # the reason the admin table was flagging healthy accounts.
    "signed_up":        "On the free tier",
    "checkout_started": "Left at the card form",
    "checkout_dropped": "Checkout abandoned",
    "trialing":         "On trial",
    "paying":           "Paying",
    "past_due":         "Card failing",
    "lapsed":           "Lapsed",
}


# The stages that mean somebody set out to pay and did not arrive. These are
# the rows worth a second line in the admin table and the ones the "Stalled"
# chip is for.
#
# `signed_up` IS DELIBERATELY NOT HERE. It was, while free was closed: an
# account that had signed up and not paid had nothing, so it was a stall. Now
# it is the free tier working as intended, and leaving it in meant every
# healthy free user carried a "never opened checkout" note and flooded the one
# list you go to when somebody says they paid and have no access.
FUNNEL_STALLED = ("checkout_started", "checkout_dropped")


def funnel_stage(user: dict | None) -> str:
    """How far this account got. See FUNNEL_STAGES.

    Reads only local state, so it is cheap enough for a table of every user and
    truthful about what the APP believes. When the app and Stripe disagree —
    the paid-but-locked-out case — this reports the app's side, which is the
    side the customer is experiencing. scripts/why_no_access.py is the tool
    that compares the two.
    """
    if not user:
        return "signed_up"
    if user.get("is_admin") or user.get("is_labeler"):
        return "staff"
    status = user.get("subscription_status") or "none"
    if status == "trialing" and trial_expired(user):
        # A finished trial is not a trial in flight. Left as "trialing" it
        # inflates the trialing count in the funnel with people who are no
        # longer converting, and hides them from the lapsed bucket where the
        # follow-up actually belongs.
        status = "expired"
    if status == "trialing":
        return "trialing"
    if status == "active":
        return "paying"
    if status in GRACE_STATUSES:
        # incomplete means the FIRST payment never confirmed — they are still
        # inside checkout, not a customer whose card started failing later.
        return "past_due" if status == "past_due" else "checkout_dropped"
    # No access. Which kind of no-access is the interesting part.
    if user.get("grandfathered") or user.get("pre_card_cutover"):
        # Their terms are frozen; "lapsed" would read as something we should
        # chase, and they may be sitting exactly where they intend to.
        return "legacy"
    if user.get("stripe_customer_id"):
        return "lapsed" if status in ("canceled", "inactive", "expired") \
            else "checkout_dropped"
    if user.get("checkout_started_at"):
        return "checkout_started"
    return "signed_up"


def trial_expired(user: dict | None, now: float | None = None) -> bool:
    """Has this user's trial run out?

    THE ONE PLACE THAT DECIDES. This condition used to live only inside the
    dashboard auth middleware, which runs on the REQUESTING user — so it only
    ever fired when the trialing user themselves made a request. Somebody whose
    trial ended and who never came back was exactly the person it never ran
    for, and their stored status stayed `trialing` indefinitely: the admin table
    read "Trial ends" against a date already in the past, and get_plan below
    still resolved them to pro.

    `trial_ends_at > 0` IS LOAD-BEARING, not a tidy-up — the same trap the
    middleware documents. `_ends_at` is 0 for "we do not know when this ends",
    and a bare `ends_at < now` reads 0 as "expired in 1970", so it would expire
    every trial with no stored end date. That shape is not hypothetical: a
    card-up-front Stripe trial whose webhook has not landed yet, or landed
    without a trial_end, has exactly it. Expiring those would revoke a customer
    at the instant they paid.

    Failing open is right here regardless. A trialing status with no end date
    means we do not know when it ends; Stripe does, and it holds their card.
    """
    if not user or user.get("subscription_status") != "trialing":
        return False
    ends_at = float(user.get("trial_ends_at") or 0)
    if ends_at <= 0:
        return False
    return (time.time() if now is None else now) >= ends_at


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
    if status == "trialing" and trial_expired(user):
        # The trial is over. Before this line the answer here was "pro" until
        # the user next made a request and the middleware rewrote their stored
        # status — so anything that reads limits WITHOUT them making a request
        # (the admin table, the background per-plan caps) kept treating a
        # finished trial as Pro.
        status = "expired"
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
        return FREE_PLAN
    if status != "active":
        # Never subscribed, cancelled, lapsed, or a finished trial — all four
        # land on free now, which is the whole point of reopening it.
        #
        # This used to read `FREE_PLAN if user.get("grandfathered") else
        # LOCKED_PLAN`, because free was legacy-only and a new account that had
        # not paid was meant to have nothing. With free as the front door the
        # distinction is gone: `grandfathered` no longer changes what anybody
        # may do. The flag is left on the accounts that carry it — funnel_stage
        # still reads it to avoid chasing legacy users as "lapsed" — but no
        # access decision depends on it any more.
        return FREE_PLAN

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
        # Both ceilings, not just the queue. An admin who could not keep a clip
        # because of a weekly library cap would be blocked from the exact thing
        # staff do most — working through a real queue to check the product.
        return {**limits, "max_pending": UNLIMITED_PENDING,
                _LIB_WEEK: UNLIMITED_PENDING}
    return limits


def is_paid(user: dict | None) -> bool:
    """Whether this user is on a paying tier. Use for upgrade prompts — NOT for
    access control, which should ask limits_for() what is actually allowed."""
    return get_plan(user) in PAID_PLANS
