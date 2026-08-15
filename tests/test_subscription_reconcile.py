"""Stripe webhooks are the only writer of subscription state.

That makes a missed delivery permanent AND invisible — the endpoint is down
through a deploy, or an event errors past Stripe's retries, and the account is
wrong forever with nothing to notice. This sweep is what makes billing drift
self-correcting rather than a support ticket.

The dangerous direction is obvious: a sweep that gets "no subscription" wrong
cancels paying customers en masse, on a timer, with no human in the loop. So
the tests below are weighted towards what it must NOT touch.
"""

import asyncio
import json
import time

import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):
    from src.auth import users as user_store
    from src.billing import stripe_billing
    from src.dashboard import api
    from config.settings import settings

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    monkeypatch.setattr(settings, "stripe_price_id_starter", "price_starter")
    monkeypatch.setattr(settings, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(api, "_RECONCILE_GAP", 0)

    sent, stopped = [], []
    async def _bc(msg, **kw): sent.append((msg.get("event"), kw.get("user_id")))
    async def _enforce(uid): stopped.append(uid)
    monkeypatch.setattr(api, "broadcast", _bc)
    monkeypatch.setattr(api, "_enforce_stream_limit", _enforce)

    # One pass, not an infinite loop: the first sleep is what the task starts
    # with, so raising on the SECOND call lets exactly one sweep run.
    real_sleep = asyncio.sleep
    calls = {"n": 0}
    class _Stop(Exception): pass
    async def _sleep(secs, *a, **k):
        if secs == api._RECONCILE_INTERVAL:
            calls["n"] += 1
            if calls["n"] > 1:
                raise _Stop()
            return
        return await real_sleep(0)
    monkeypatch.setattr(api.asyncio, "sleep", _sleep)

    def users(*records):
        (tmp_path / "users.json").write_text(json.dumps(list(records)))

    def stripe_says(by_customer):
        """by_customer: {customer_id: [subscription dicts]} or an Exception."""
        class _R:
            def __init__(self, d): self.data = d
        class _C:
            subscriptions = None
            def __init__(self): self.subscriptions = self
            def list(self, params=None):
                v = by_customer.get(params.get("customer"))
                if isinstance(v, Exception):
                    raise v
                return _R(v or [])
        monkeypatch.setattr(stripe_billing, "_client", lambda: _C())

    def sweep():
        async def _go():
            try:
                await api.subscription_reconcile_task()
            except _Stop:
                pass
        asyncio.run(_go())

    def state(uid):
        u = user_store.get_by_id(uid)
        return u.get("subscription_status"), u.get("plan")

    return {"users": users, "stripe_says": stripe_says, "sweep": sweep,
            "state": state, "sent": sent, "stopped": stopped, "store": user_store}


def _u(uid, **over):
    rec = {"id": uid, "username": uid, "stripe_customer_id": "cus_" + uid,
           "subscription_status": "active", "plan": "starter",
           "created_at": time.time()}
    rec.update(over)
    return rec


# ── the drift it exists to fix ───────────────────────────────────────────────

def test_a_missed_activation_webhook_is_repaired(env):
    """They paid, the webhook never landed, and today nothing would ever fix
    it. This is the case that turns into a refund request."""
    env["users"](_u("u1", subscription_status="none", plan=None))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "active",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["state"]("u1") == ("active", "pro")
    assert ("subscription_active", "u1") in env["sent"]


def test_a_missed_cancellation_webhook_is_also_repaired(env):
    """Both directions, or the sweep is just a way to give away the product."""
    env["users"](_u("u1", subscription_status="active", plan="pro"))
    env["stripe_says"]({"cus_u1": [{"id": "sub_A", "status": "canceled"}]})
    env["sweep"]()
    assert env["state"]("u1")[0] == "inactive"
    assert ("subscription_expired", "u1") in env["sent"]
    assert env["stopped"] == ["u1"]


def test_a_missed_upgrade_lands_the_tier(env):
    env["users"](_u("u1", plan="starter"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "active",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["state"]("u1") == ("active", "pro")


def test_a_customer_with_no_subscriptions_at_all_is_lapsed(env):
    env["users"](_u("u1"))
    env["stripe_says"]({"cus_u1": []})
    env["sweep"]()
    assert env["state"]("u1")[0] == "inactive"


# ── what it must never touch ─────────────────────────────────────────────────

def test_stripe_being_unreachable_cancels_nobody(env):
    """The nightmare: a Stripe outage silently revoking every paying customer,
    on a timer, with no human in the loop."""
    env["users"](_u("u1"), _u("u2"))
    env["stripe_says"]({"cus_u1": RuntimeError("stripe is down"),
                        "cus_u2": RuntimeError("stripe is down")})
    env["sweep"]()
    assert env["state"]("u1") == ("active", "starter")
    assert env["state"]("u2") == ("active", "starter")
    assert env["sent"] == [] and env["stopped"] == []


def test_an_admin_granted_trial_is_left_alone(env):
    """App-managed access has no Stripe subscription behind it — reconciling it
    would revoke every comp and trial the moment Stripe said 'none', which is
    always, because there never was one."""
    env["users"](_u("u1", subscription_status="trialing",
                    trial_ends_at=time.time() + 86400))
    env["stripe_says"]({"cus_u1": []})
    env["sweep"]()
    assert env["state"]("u1")[0] == "trialing"
    assert env["sent"] == []


def test_admins_and_trainers_are_skipped(env):
    env["users"](_u("boss", is_admin=True), _u("lab", is_labeler=True))
    env["stripe_says"]({"cus_boss": [], "cus_lab": []})
    env["sweep"]()
    assert env["state"]("boss")[0] == "active"
    assert env["state"]("lab")[0] == "active"


def test_users_with_no_stripe_customer_are_never_looked_up(env):
    """Free and trial accounts are most of the table. Calling Stripe once per
    account per hour for them is wasted rate limit on a 1vCPU box."""
    seen = []
    env["users"]({"id": "u1", "username": "u1", "subscription_status": "expired",
                  "created_at": time.time()})
    class _C:
        def __init__(self): self.subscriptions = self
        def list(self, params=None):
            seen.append(params); raise AssertionError("looked up a customerless user")
    from src.billing import stripe_billing
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(stripe_billing, "_client", lambda: _C())
    try:
        env["sweep"]()
    finally:
        mp.undo()
    assert seen == []


def test_a_healthy_account_is_not_rewritten_or_broadcast(env):
    """No drift means no write and no toast — otherwise every open tab gets a
    'your plan is active' popup once an hour, forever."""
    env["users"](_u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "active",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["state"]("u1") == ("active", "pro")
    assert env["sent"] == [], "broadcast a change to an account that did not change"


def test_a_dead_duplicate_does_not_outrank_the_live_subscription(env):
    """A cancelled duplicate can be NEWER than the live one. Picking by recency
    is what admin_stripe_sync got wrong (limit 1, no status filter)."""
    env["users"](_u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_dead", "status": "canceled", "created": 999,
         "items": {"data": [{"price": {"id": "price_starter"}}]}},
        {"id": "sub_live", "status": "active", "created": 1,
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["state"]("u1") == ("active", "pro"), "read a dead duplicate as the truth"
    assert env["sent"] == []


def test_a_card_in_retry_is_reported_as_past_due_not_cancelled(env):
    """Reconcile must not undo the grace handling — past_due keeps the tier."""
    from src.billing.plans import get_plan
    env["users"](_u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "past_due",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["state"]("u1") == ("past_due", "pro")
    assert get_plan(env["store"].get_by_id("u1")) == "pro"
    assert env["stopped"] == [], "stopped the streams of a customer mid-retry"


def test_one_bad_account_does_not_abort_the_whole_sweep(env):
    """A single malformed record must not stop everyone else being reconciled."""
    env["users"](_u("u1"), _u("u2", subscription_status="none", plan=None))
    env["stripe_says"]({"cus_u1": RuntimeError("boom"),
                        "cus_u2": [{"id": "sub_B", "status": "active",
                                    "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["state"]("u2") == ("active", "pro"), "the sweep gave up after one failure"
