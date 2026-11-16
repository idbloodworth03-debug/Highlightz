"""A subscription ending is not the same as a customer leaving.

Three separate ways the billing path used to take access away from somebody who
had not stopped paying. All three were reproduced against the real webhook
handler before being fixed (scratchpad/audit_repro.py):

  1. Our own duplicate sweep cancels the extra subscription; Stripe sends
     `customer.subscription.deleted` for it; the handler marked the customer
     inactive even though their real subscription was still running. The fix
     for double billing was itself locking people out.
  2. `past_due` — a card being retried — locked the customer out for the whole
     ~2-week Smart Retries window while Stripe was still trying to collect.
     REVERSED BY THE OWNER, 2026-11-16: "for the people whose cards are failing
     we need to do something about that so they don't have access". A failing
     card now lands on Free until the charge goes through. What survives from
     the original fix: it is never told its subscription ENDED, it is never
     sold a second subscription, and nothing of its setup is deleted (see
     tests/test_card_failing.py for the hold and the way back).
  3. `incomplete` — the opening state of a 3DS/SCA card payment — fired "Your
     subscription has ended" at somebody in the middle of buying it.

The opposite mistake is just as expensive, so every test here has a partner
that proves a REAL ending still lapses the account.
"""

import asyncio
import json
import time

import pytest


# ── plan resolution ──────────────────────────────────────────────────────────

def test_a_failing_card_is_on_free_whatever_tier_it_was_billed_for():
    """No payment, no paid plan (owner, 2026-11-16). It used to keep the tier
    through Stripe's whole retry window."""
    from src.billing.plans import get_plan
    for tier in ("starter", "pro", "studio"):
        assert get_plan({"subscription_status": "past_due", "plan": tier}) == "free", tier


def test_the_tier_is_kept_for_the_day_the_charge_goes_through():
    """Free is where they are, not what they are. The stored tier is untouched,
    so the same record reads as its tier again the moment it is active."""
    from src.billing.plans import get_plan
    u = {"subscription_status": "past_due", "plan": "studio"}
    assert get_plan(u) == "free" and u["plan"] == "studio"
    assert get_plan(dict(u, subscription_status="active")) == "studio"


def test_a_legacy_subscriber_whose_card_fails_is_on_free_too():
    """$15-era subscribers are active with NO stored plan. Failing, they are
    on Free like anybody else; paid again, they are grandfathered Pro again."""
    from src.billing.plans import get_plan
    assert get_plan({"subscription_status": "past_due"}) == "free"
    assert get_plan({"subscription_status": "active"}) == "pro"


def test_an_unconfirmed_first_payment_grants_nothing_on_its_own():
    """`incomplete` means nobody has paid anything yet. Granting the legacy
    fallback here would hand Pro to anyone who opens a checkout and walks away,
    for the ~23 hours before Stripe expires it."""
    from src.billing.plans import get_plan
    assert get_plan({"subscription_status": "incomplete"}) == "free"
    assert get_plan({"subscription_status": "incomplete", "grandfathered": True}) == "free"


def test_an_unconfirmed_payment_for_a_known_tier_grants_that_tier():
    from src.billing.plans import get_plan
    assert get_plan({"subscription_status": "incomplete", "plan": "starter"}) == "starter"


def test_stripe_giving_up_is_still_an_ending():
    """unpaid and incomplete_expired are Stripe having stopped trying. If these
    ever land in GRACE_STATUSES, a cancelled customer keeps the product."""
    from src.billing.plans import get_plan, GRACE_STATUSES
    for status in ("unpaid", "incomplete_expired", "canceled", "inactive", "expired"):
        assert status not in GRACE_STATUSES, f"{status} must not be a grace status"
        assert get_plan({"subscription_status": status, "plan": "pro"}) == "free"


def test_a_failing_card_is_not_on_a_paid_tier_for_the_prompts_either():
    """is_paid drives upgrade prompts. Someone whose card is failing is on
    Free, and the prompt they follow lands in the billing portal (the checkout
    guard sends past_due there), never in a second checkout."""
    from src.billing.plans import is_paid
    assert is_paid({"subscription_status": "past_due", "plan": "pro"}) is False
    assert is_paid({"subscription_status": "canceled", "plan": "pro"}) is False
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api)
    at = src.index('if live == "past_due":')
    assert 'RedirectResponse("/billing/portal")' in src[at:at + 400]


# ── the webhook ──────────────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch, tmp_path):
    from src.auth import users as user_store
    from src.billing import stripe_billing
    from src.dashboard import api
    from config.settings import settings

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")

    sent = []
    async def _bc(msg, **kw): sent.append((msg.get("event"), kw.get("user_id")))
    monkeypatch.setattr(api, "broadcast", _bc)

    stopped = []
    async def _enforce(uid): stopped.append(uid)
    monkeypatch.setattr(api, "_enforce_stream_limit", _enforce)

    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "u1", "username": "nova", "stripe_customer_id": "cus_1",
         "subscription_status": "active", "plan": "starter",
         "created_at": time.time()}]))

    def stripe_says(subs):
        class _R:
            def __init__(self, d): self.data = d
        class _C:
            def __init__(self, d): self.subscriptions = self; self._d = d
            def list(self, params=None): return _R(self._d)
        monkeypatch.setattr(stripe_billing, "_client", lambda: _C(subs))

    def fire(etype, sub_id, status):
        api._stripe_processed.clear()
        ev = {"id": "evt_" + sub_id + etype, "type": etype, "data": {"object": {
            "id": sub_id, "customer": "cus_1", "status": status,
            "metadata": {"user_id": "u1"}, "items": {"data": []}}}}
        return asyncio.run(api._process_stripe_event(ev, time.time(), ev["id"]))

    def plan_now():
        from src.billing.plans import get_plan
        return get_plan(user_store.get_by_id("u1"))

    def stripe_unreachable():
        class _Boom:
            def __init__(self): self.subscriptions = self
            def list(self, params=None): raise RuntimeError("stripe is down")
        monkeypatch.setattr(stripe_billing, "_client", lambda: _Boom())

    return {"stripe_says": stripe_says, "fire": fire, "sent": sent,
            "stopped": stopped, "plan_now": plan_now, "store": user_store,
            "stripe_unreachable": stripe_unreachable, "mp": monkeypatch}


def test_cancelling_a_duplicate_does_not_touch_the_live_subscription(env):
    """THE regression this file exists for. sub_B is the duplicate our own
    sweep just killed; sub_A is what the customer actually pays for."""
    env["stripe_says"]([{"id": "sub_A", "status": "active"},
                        {"id": "sub_B", "status": "canceled"}])
    env["fire"]("customer.subscription.deleted", "sub_B", "canceled")
    assert env["store"].get_by_id("u1")["subscription_status"] == "active"
    assert env["plan_now"]() == "starter", "a paying customer was locked out"
    assert env["sent"] == [], "told a paying customer their subscription ended"
    assert env["stopped"] == [], "stopped a paying customer's streams"


def test_the_last_subscription_ending_still_lapses_the_account(env):
    """The partner test. Without it, the guard above could be 'never lapse
    anybody' and still pass."""
    env["stripe_says"]([{"id": "sub_A", "status": "canceled"}])
    env["fire"]("customer.subscription.deleted", "sub_A", "canceled")
    assert env["store"].get_by_id("u1")["subscription_status"] == "inactive"
    assert env["plan_now"]() == "free"
    assert ("subscription_expired", "u1") in env["sent"]
    assert env["stopped"] == ["u1"]


def test_a_still_dunning_sibling_subscription_counts_as_live(env):
    """past_due on the OTHER subscription means Stripe is still collecting on
    it. Treating only 'active' as live would lapse them mid-retry."""
    env["stripe_says"]([{"id": "sub_A", "status": "past_due"},
                        {"id": "sub_B", "status": "canceled"}])
    env["fire"]("customer.subscription.deleted", "sub_B", "canceled")
    assert env["store"].get_by_id("u1")["subscription_status"] == "active"


def test_stripe_being_unreachable_does_not_silently_swallow_cancellations(env):
    """Fail-open direction, chosen deliberately: 'unknown' must NOT be read as
    'another one is live', or a Stripe outage means no cancellation ever
    applies and everybody keeps the product for free."""
    env["stripe_unreachable"]()
    env["fire"]("customer.subscription.deleted", "sub_A", "canceled")
    assert env["store"].get_by_id("u1")["subscription_status"] == "inactive"


def test_a_failing_card_drops_to_free_and_is_told_why_not_that_it_ended(env):
    """The webhook for a renewal charge that bounced (owner, 2026-11-16)."""
    env["stripe_says"]([{"id": "sub_A", "status": "past_due"}])
    env["fire"]("customer.subscription.updated", "sub_A", "past_due")
    u = env["store"].get_by_id("u1")
    assert u["subscription_status"] == "past_due" and u["plan"] == "starter", "the stored tier was thrown away"
    assert env["plan_now"]() == "free"
    assert env["stopped"] == ["u1"], "the account was not trimmed to what Free allows"
    assert env["sent"] == [("subscription_expired", "u1"), ("inbox_changed", "u1")]
    note = u["notices"][-1]
    assert note["title"] == "Your payment did not go through" and "paused, not removed" in note["body"]


def test_stripes_retries_of_the_same_failure_are_not_announced_again(env):
    """Stripe sends subscription.updated for each retry. One toast, one note."""
    env["stripe_says"]([{"id": "sub_A", "status": "past_due"}])
    env["fire"]("customer.subscription.updated", "sub_A", "past_due")
    env["sent"].clear()
    env["fire"]("customer.subscription.updated", "sub_A", "past_due")
    assert env["sent"] == [("billing_changed", "u1")]
    assert len(env["store"].get_by_id("u1")["notices"]) == 1


def test_a_duplicate_going_past_due_does_not_touch_a_paying_customer(env):
    """The partner of the duplicate test at the top: now that past_due costs
    the plan, a second subscription failing while the real one is being paid
    must change nothing."""
    env["stripe_says"]([{"id": "sub_A", "status": "active"}, {"id": "sub_DUP", "status": "past_due"}])
    env["fire"]("customer.subscription.updated", "sub_DUP", "past_due")
    assert env["store"].get_by_id("u1")["subscription_status"] == "active"
    assert env["plan_now"]() == "starter" and env["sent"] == [] and env["stopped"] == []


def test_the_failed_card_message_says_how_to_get_the_plan_back(env):
    from src.dashboard import api
    msg = api._lapse_message("free", "past_due")
    assert "did not go through" in msg and "Update your card" in msg and "come straight back" in msg
    assert "ended" not in msg, "a failing card is not an ending"
    assert "ended" in api._lapse_message("free"), "a real ending no longer says so"


def test_buying_a_subscription_never_says_it_ended(env):
    """`incomplete` is the opening state of a 3DS/SCA card payment."""
    env["fire"]("customer.subscription.created", "sub_A", "incomplete")
    assert env["sent"] == [("billing_changed", "u1")], "fired 'subscription ended' at somebody buying one"
    assert env["stopped"] == []
    assert env["plan_now"]() == "starter", "somebody mid-purchase lost the tier they already held"


def test_the_lapse_path_does_not_call_stripe_on_a_healthy_renewal(env):
    """The guard must not add a Stripe round-trip to every ordinary renewal —
    this runs inside the webhook, on a 1vCPU box."""
    calls = []
    from src.billing import stripe_billing
    async def _spy(cust, exclude):
        calls.append(cust); return False
    env["mp"].setattr(stripe_billing, "has_other_live_subscription", _spy)
    env["fire"]("customer.subscription.updated", "sub_A", "active")
    assert calls == [], "an active renewal made a needless Stripe call"
