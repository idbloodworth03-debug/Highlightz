"""Reading a cancellation out of a Stripe subscription event.

Owner, 2026-11-18: "When users end their subscription make them take a
survey". Nobody cancels on this site: "Manage billing" opens Stripe's own
page and the cancel button is there. So the site hears about it from the
webhook, and this module is the one place that decides what a subscription
object says:

  * `cancel_at_period_end` (or a `cancel_at` date) on a subscription that is
    still active: they pressed cancel and keep the plan until the paid time
    runs out. THIS is the moment to ask. Stripe sends them straight back to
    the dashboard, where the question is waiting.
  * the same flag gone again: they changed their mind. Stop asking.
  * the subscription ending outright after being paid for, because the
    customer asked (not a failed card, not a dispute): ask, unless this same
    cancellation was already asked about.
  * `cancellation_details.feedback` / `.comment`: Stripe's page has a reason
    step of its own (off unless it is switched on in Stripe's settings, and
    skippable there). If they answered it, that IS the survey for this
    cancellation and they are not asked twice.

A failed card is not somebody leaving and is never asked "why did you
cancel": status past_due is handled elsewhere, and a subscription Stripe gave
up on carries reason `payment_failed`.

`token` names ONE cancellation: the subscription and the second it was
cancelled. Stripe repeats events and sends several for one cancel; the token
is what makes the question be asked once.
"""

from __future__ import annotations

# Stripe's own vocabulary for its reason step, in ours.
STRIPE_FEEDBACK = {
    "too_expensive": "too_expensive",
    "unused": "not_using",
    "low_quality": "clips_quality",
    "missing_features": "missing_feature",
    "too_complex": "hard_to_use",
    "switched_service": "switched",
    "customer_service": "other",
    "other": "other",
}


def read(sub: dict) -> dict:
    """What this subscription object says about being cancelled."""
    sub = sub or {}
    cd = sub.get("cancellation_details") or {}
    ends = sub.get("cancel_at") or sub.get("current_period_end") or 0
    if not ends:
        # Newer API versions carry the period on the item, not the subscription.
        items = ((sub.get("items") or {}).get("data") or [])
        ends = (items[0] or {}).get("current_period_end") if items else 0
    try:
        ends = float(ends or 0)
    except (TypeError, ValueError):
        ends = 0.0
    try:
        when = int(sub.get("canceled_at") or 0)
    except (TypeError, ValueError):
        when = 0
    feedback = str(cd.get("feedback") or "")
    comment = str(cd.get("comment") or "")
    return {
        "token": f"{sub.get('id') or ''}:{when}",
        "scheduled": bool(sub.get("cancel_at_period_end") or sub.get("cancel_at")),
        "why": str(cd.get("reason") or ""),
        "answered_on_stripe": bool(feedback or comment),
        "reason": STRIPE_FEEDBACK.get(feedback, "other") if (feedback or comment) else "",
        # "customer_service" has no answer of ours; keep Stripe's word with it.
        "detail": (comment or ("Customer service" if feedback == "customer_service" else "")),
        "ends_at": ends,
    }


def decide(info: dict, status: str, status_before: str) -> str:
    """What to do about the survey for this event: "record" (Stripe already
    has their answer), "ask", "unask" (the cancellation was undone), or "".

    `status` is ours (active / trialing / inactive / past_due ...), after the
    event; `status_before` is what the account held before it.
    """
    if info["answered_on_stripe"] and (info["scheduled"] or status == "inactive"):
        return "record"
    if status in ("active", "trialing"):
        return "ask" if info["scheduled"] else "unask"
    if status == "inactive" and status_before in ("active", "trialing") \
            and info["why"] in ("", "cancellation_requested"):
        return "ask"
    return ""
