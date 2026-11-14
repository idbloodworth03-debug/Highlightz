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
    # The first pass is soon after a start and the rest are hourly; here the
    # two are the same tick, so sweep() below is exactly one pass.
    monkeypatch.setattr(api, "_RECONCILE_FIRST", api._RECONCILE_INTERVAL)

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

    def stripe_unreachable():
        class _Boom:
            def __init__(self): self.subscriptions = self
            def list(self, params=None): raise RuntimeError("stripe is down")
        monkeypatch.setattr(stripe_billing, "_client", lambda: _Boom())

    return {"users": users, "stripe_says": stripe_says, "sweep": sweep,
            "state": state, "sent": sent, "stopped": stopped, "store": user_store,
            "stripe_unreachable": stripe_unreachable}


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


def test_customerless_accounts_cost_one_call_for_all_of_them(env):
    """Free and trial accounts are most of the table, and a per-account lookup
    would be a Stripe call each, every hour, on a 1vCPU box.

    Adoption deliberately spends ONE global scan instead — that is what finds a
    paying account we hold no customer id for. So the rule is not "never call
    Stripe", it is "never call it per customerless account"."""
    seen = []
    env["users"](
        {"id": "u1", "username": "u1", "subscription_status": "expired",
         "created_at": time.time()},
        {"id": "u2", "username": "u2", "subscription_status": "none",
         "created_at": time.time()},
        {"id": "u3", "username": "u3", "subscription_status": "expired",
         "created_at": time.time()})
    class _R:
        def __init__(self, d): self.data = d
    class _C:
        def __init__(self): self.subscriptions = self
        def list(self, params=None):
            seen.append(params or {}); return _R([])
    from src.billing import stripe_billing
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(stripe_billing, "_client", lambda: _C())
    try:
        env["sweep"]()
    finally:
        mp.undo()
    per_customer = [c for c in seen if c.get("customer")]
    assert per_customer == [], "looked up customerless accounts one by one"
    assert len(seen) == 1, f"three idle accounts cost {len(seen)} Stripe calls"


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


# ── surviving a webhook that never fires ─────────────────────────────────────
# The production root cause: the Stripe endpoint existed, was enabled, had the
# right URL, and had NO customer.subscription.* events selected. It returned 200
# to everything it was sent, so nothing looked wrong, and not one customer id
# was ever written. Checkout then minted a new Stripe customer per purchase and
# the same people were billed two and three times.

def test_a_paying_account_is_adopted_without_the_webhook(env):
    """Stripe knows who the subscription belongs to — create_checkout_url puts
    user_id in its metadata. Reading that back is what makes billing survive a
    webhook that is misconfigured, deleted, or silently returning 200."""
    env["users"](_u("u1", subscription_status="none", plan=None,
                    stripe_customer_id=None))
    env["stripe_says"]({None: [
        {"id": "sub_A", "status": "active", "customer": "cus_NEW",
         "metadata": {"user_id": "u1"},
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    u = env["store"].get_by_id("u1")
    assert u["stripe_customer_id"] == "cus_NEW", "the paying account was never linked"
    assert u["subscription_status"] == "active"
    assert u["plan"] == "pro"
    assert ("subscription_active", "u1") in env["sent"]


def test_adoption_never_moves_an_account_already_pointing_elsewhere(env):
    """Overwriting a stored customer would silently move somebody's billing."""
    env["users"](_u("u1", stripe_customer_id="cus_THEIRS", subscription_status="active"))
    env["stripe_says"]({None: [
        {"id": "sub_B", "status": "active", "customer": "cus_DIFFERENT",
         "metadata": {"user_id": "u1"},
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["store"].get_by_id("u1")["stripe_customer_id"] == "cus_THEIRS"


def test_a_subscription_for_a_deleted_account_is_left_alone(env):
    env["users"](_u("u1"))
    env["stripe_says"]({None: [
        {"id": "sub_G", "status": "active", "customer": "cus_GHOST",
         "metadata": {"user_id": "gone"},
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert all(u.get("stripe_customer_id") != "cus_GHOST"
               for u in env["store"].get_all())


def test_a_cancelled_subscription_is_not_adopted(env):
    """Only live subscriptions grant access. Adopting a cancelled one would
    hand the product back to somebody who stopped paying."""
    env["users"](_u("u1", subscription_status="inactive", stripe_customer_id=None))
    env["stripe_says"]({None: [
        {"id": "sub_X", "status": "canceled", "customer": "cus_OLD",
         "metadata": {"user_id": "u1"},
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["sweep"]()
    assert env["store"].get_by_id("u1")["subscription_status"] == "inactive"


def test_stripe_unreachable_adopts_nobody(env):
    env["users"](_u("u1", subscription_status="none", stripe_customer_id=None))
    env["stripe_unreachable"]()
    env["sweep"]()
    assert env["store"].get_by_id("u1")["subscription_status"] == "none"


# ── a failing card must stay "past due", not flip back to "active" ───────────
# Production, 2026-11-14: the admin page counted 7 paying and Stripe 5. The two
# extra (kipcrap, unkn0wnballer) were PAST DUE in Stripe and "active" here,
# with no sweep errors. The sweep did record past_due, and then the adoption
# pass that follows it in the same run set the account back to "active": it
# lists every live subscription (past_due is live), finds the account "not
# active", and "adopts" it as active. Every hour, both steps, in that order, so
# the record read "active" all but a few seconds of the day.
#
# The past_due test above never saw it because its Stripe returned the
# subscription only when asked by customer. Real Stripe returns the same
# subscription in the unfiltered list too, with the account in its metadata.

def _both_listings(env, by_customer):
    """Stripe as it really answers: each subscription both under its customer
    and in the list of everything."""
    everything = [s for subs in by_customer.values() for s in subs]
    env["stripe_says"]({**by_customer, None: everything})


def _sub(sid, status, cust, uid, price="price_pro"):
    return {"id": sid, "status": status, "customer": cust, "metadata": {"user_id": uid},
            "items": {"data": [{"price": {"id": price}}]}}


def test_a_past_due_subscriber_is_not_adopted_back_to_active(env):
    env["users"](_u("u1", plan="pro"))
    _both_listings(env, {"cus_u1": [_sub("sub_A", "past_due", "cus_u1", "u1")]})
    env["sweep"]()
    assert env["state"]("u1") == ("past_due", "pro"), "the adoption pass undid the sweep"


def test_it_stays_past_due_sweep_after_sweep(env):
    """The flip was hourly. Two more passes must change nothing and send nothing."""
    env["users"](_u("u1", plan="pro", subscription_status="past_due"))
    _both_listings(env, {"cus_u1": [_sub("sub_A", "past_due", "cus_u1", "u1")]})
    for _ in range(2):
        env["sweep"]()
        assert env["state"]("u1") == ("past_due", "pro")
    assert env["sent"] == [], "a subscriber whose state did not change was told it had"


def test_the_paying_tile_stops_counting_them(env):
    """What the owner saw: the tile is api._counts_as_paying."""
    from src.dashboard import api
    env["users"](_u("payer", plan="pro"), _u("failing", plan="pro"))
    _both_listings(env, {"cus_payer": [_sub("sub_P", "active", "cus_payer", "payer")],
                         "cus_failing": [_sub("sub_F", "past_due", "cus_failing", "failing")]})
    env["sweep"]()
    counted = sorted(u["id"] for u in env["store"].get_all() if api._counts_as_paying(u))
    assert counted == ["payer"]


def test_a_card_that_recovers_goes_back_to_active(env):
    """The other direction still works: Stripe collects, the account is active."""
    env["users"](_u("u1", plan="pro", subscription_status="past_due"))
    _both_listings(env, {"cus_u1": [_sub("sub_A", "active", "cus_u1", "u1")]})
    env["sweep"]()
    assert env["state"]("u1") == ("active", "pro")


def test_an_unlinked_account_is_adopted_in_the_state_stripe_reports(env):
    """Adoption links the account; it does not get to invent "active". A
    first charge that failed is past_due here too, and a trial is a trial."""
    env["users"](_u("late", subscription_status="none", plan=None, stripe_customer_id=None),
                 _u("trial", subscription_status="none", plan=None, stripe_customer_id=None))
    env["stripe_says"]({None: [_sub("sub_L", "past_due", "cus_L", "late"),
                               _sub("sub_T", "trialing", "cus_T", "trial")]})
    env["sweep"]()
    late, trial = env["store"].get_by_id("late"), env["store"].get_by_id("trial")
    assert (late["stripe_customer_id"], late["subscription_status"], late["plan"]) == ("cus_L", "past_due", "pro")
    assert (trial["stripe_customer_id"], trial["subscription_status"]) == ("cus_T", "trialing")


def test_a_live_duplicate_does_not_drag_a_paying_account_down(env):
    """One customer, an active subscription and a past_due duplicate: the
    account is paying, whichever order Stripe lists them in."""
    for order in ((0, 1), (1, 0)):
        subs = [_sub("sub_OK", "active", "cus_u1", "u1"), _sub("sub_DUP", "past_due", "cus_u1", "u1")]
        env["users"](_u("u1", plan="pro"))
        _both_listings(env, {"cus_u1": [subs[order[0]], subs[order[1]]]})
        env["sweep"]()
        assert env["state"]("u1") == ("active", "pro"), order


def test_a_card_starting_to_fail_reaches_the_open_tab_quietly(env):
    """The Account screen shows the status. It changed, so an open tab has to
    hear (realtime contract), but nothing was taken away: no "expired" toast,
    no "active" toast, no streams stopped."""
    env["users"](_u("u1", plan="pro"))
    _both_listings(env, {"cus_u1": [_sub("sub_A", "past_due", "cus_u1", "u1")]})
    env["sweep"]()
    assert env["sent"] == [("billing_changed", "u1")]
    assert env["stopped"] == []


def test_the_quiet_event_is_handled_and_stays_quiet():
    import pathlib
    src = pathlib.Path("src/dashboard/aurora_html.py").read_text()
    at = src.index("msg.event==='billing_changed'")
    block = src[at:src.index("else if(msg.event===", at + 10)]
    assert "fetch('/me')" in block and "setMe" in block
    assert "flash(" not in block, "a failing card is not announced with a toast"
    api_src = pathlib.Path("src/dashboard/api.py").read_text()
    assert api_src.count('{"event": "billing_changed"}') == 2      # the sweep and the webhook


def test_the_first_pass_is_minutes_after_a_start_not_an_hour(monkeypatch):
    """Every deploy restarts the clock. With the first pass a full hour out, a
    billing fix took an hour to show and a day of hourly deploys never
    reconciled at all."""
    from src.dashboard import api
    assert api._RECONCILE_FIRST <= 300 < api._RECONCILE_INTERVAL
    slept = []

    class _Stop(Exception):
        pass

    async def _sleep(secs, *a, **k):
        slept.append(secs)
        if len(slept) == 3:
            raise _Stop()

    monkeypatch.setattr(api.asyncio, "sleep", _sleep)
    monkeypatch.setattr(api.settings, "stripe_secret_key", "")      # nothing to ask: only the timing runs
    with pytest.raises(_Stop):
        asyncio.run(api.subscription_reconcile_task())
    assert slept == [api._RECONCILE_FIRST, api._RECONCILE_INTERVAL, api._RECONCILE_INTERVAL]
