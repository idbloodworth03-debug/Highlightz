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

def test_a_card_being_retried_keeps_the_tier_it_is_being_billed_for():
    from src.billing.plans import get_plan
    assert get_plan({"subscription_status": "past_due", "plan": "starter"}) == "starter"
    assert get_plan({"subscription_status": "past_due", "plan": "pro"}) == "pro"


def test_a_legacy_subscriber_whose_card_fails_is_not_locked_out():
    """$15-era subscribers are active with NO stored plan. past_due cannot be
    reached without a successful charge first, so this is a real customer."""
    from src.billing.plans import get_plan
    assert get_plan({"subscription_status": "past_due"}) == "pro"


def test_an_unconfirmed_first_payment_grants_nothing_on_its_own():
    """`incomplete` means nobody has paid anything yet. Granting the legacy
    fallback here would hand Pro to anyone who opens a checkout and walks away,
    for the ~23 hours before Stripe expires it."""
    from src.billing.plans import get_plan
    assert get_plan({"subscription_status": "incomplete"}) == "locked"
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
        assert get_plan({"subscription_status": status, "plan": "pro"}) == "locked"


def test_grace_does_not_leak_into_the_paid_check():
    """is_paid drives upgrade prompts. Someone mid-dunning is on a paid tier."""
    from src.billing.plans import is_paid
    assert is_paid({"subscription_status": "past_due", "plan": "pro"}) is True
    assert is_paid({"subscription_status": "canceled", "plan": "pro"}) is False


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
    assert env["plan_now"]() == "locked"
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


def test_a_failing_card_does_not_stop_streams_or_fire_the_ended_toast(env):
    env["fire"]("customer.subscription.updated", "sub_A", "past_due")
    assert env["store"].get_by_id("u1")["subscription_status"] == "past_due"
    assert env["plan_now"]() == "starter", "locked out a customer mid-retry"
    assert env["sent"] == [], "told a customer being retried that they had ended"
    assert env["stopped"] == [], "stopped the streams of a customer mid-retry"


def test_buying_a_subscription_never_says_it_ended(env):
    """`incomplete` is the opening state of a 3DS/SCA card payment."""
    env["fire"]("customer.subscription.created", "sub_A", "incomplete")
    assert env["sent"] == [], "fired 'subscription ended' at somebody buying one"
    assert env["stopped"] == []


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
