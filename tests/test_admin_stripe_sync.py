"""The admin's "sync from Stripe" button.

It was its own implementation of reconciliation, and had four bugs the hourly
sweep did not:

  * `limit: 1` with no status filter — it read whichever subscription Stripe
    returned first as the truth. A cancelled duplicate can be newer than the
    live one, so syncing a healthy customer could cancel them.
  * A customer with no subscriptions returned "no subscriptions found" and
    changed nothing — so syncing a lapsed account left it active forever, which
    is the exact case an admin reaches for this button to fix.
  * It never synced the plan, only the status.
  * It changed access without telling the user's open tab.

It now runs the same reconcile_one_user() the sweep does. These tests pin the
endpoint's own behaviour — auth, refusals, the response shape — and prove the
four bugs are gone.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.billing import stripe_billing
    from src.dashboard import api
    from config.settings import settings

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    monkeypatch.setattr(settings, "stripe_price_id_starter", "price_starter")
    monkeypatch.setattr(settings, "stripe_price_id_pro", "price_pro")

    sent, stopped = [], []
    async def _bc(msg, **kw): sent.append((msg.get("event"), kw.get("user_id")))
    async def _enforce(uid): stopped.append(uid)
    monkeypatch.setattr(api, "broadcast", _bc)
    monkeypatch.setattr(api, "_enforce_stream_limit", _enforce)

    def users(*records):
        (tmp_path / "users.json").write_text(_j.dumps(list(records)))

    def stripe_says(by_customer):
        class _R:
            def __init__(self, d): self.data = d
        class _C:
            def __init__(self): self.subscriptions = self
            def list(self, params=None):
                v = by_customer.get(params.get("customer"))
                if isinstance(v, Exception):
                    raise v
                return _R(v or [])
        monkeypatch.setattr(stripe_billing, "_client", lambda: _C())

    def client_for(uid, admin):
        c = TestClient(api.app)
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": admin,
             "subscription_status": "active"}).encode())).decode())
        return c

    return {"users": users, "stripe_says": stripe_says, "sent": sent,
            "stopped": stopped, "store": user_store,
            "admin": client_for("boss", True), "user": client_for("u1", False)}


def _u(uid, **over):
    rec = {"id": uid, "username": uid, "stripe_customer_id": "cus_" + uid,
           "subscription_status": "active", "plan": "starter",
           "created_at": time.time()}
    rec.update(over)
    return rec


def _admin(uid="boss"):
    return {"id": uid, "username": uid, "is_admin": True,
            "subscription_status": "active", "created_at": time.time()}


# ── access ───────────────────────────────────────────────────────────────────

def test_only_an_admin_can_sync(env):
    env["users"](_admin(), _u("u1"))
    r = env["user"].post("/admin/users/u1/stripe-sync")
    assert r.status_code in (401, 403)


def test_syncing_someone_who_does_not_exist_is_a_404(env):
    env["users"](_admin())
    assert env["admin"].post("/admin/users/ghost/stripe-sync").status_code == 404


# ── the four bugs ────────────────────────────────────────────────────────────

def test_a_cancelled_customer_is_actually_lapsed(env):
    """Was: "No subscriptions found", nothing changed, account stayed active —
    the exact case this button gets reached for."""
    env["users"](_admin(), _u("u1", subscription_status="active", plan="pro"))
    env["stripe_says"]({"cus_u1": []})
    r = env["admin"].post("/admin/users/u1/stripe-sync")
    assert r.status_code == 200
    assert env["store"].get_by_id("u1")["subscription_status"] == "inactive"
    assert "status" in r.json()["changed"]


def test_a_dead_duplicate_does_not_cancel_a_healthy_customer(env):
    """Was: limit 1, no status filter — Stripe returns newest first, so a newer
    cancelled duplicate was read as the truth and cancelled a paying customer."""
    env["users"](_admin(), _u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_dead", "status": "canceled", "created": 999,
         "items": {"data": [{"price": {"id": "price_starter"}}]}},
        {"id": "sub_live", "status": "active", "created": 1,
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    r = env["admin"].post("/admin/users/u1/stripe-sync")
    assert env["store"].get_by_id("u1")["subscription_status"] == "active"
    assert r.json()["changed"] == [], "cancelled a paying customer"


def test_the_plan_is_synced_not_just_the_status(env):
    """Was: only the status was written, so a drifted tier stayed drifted."""
    env["users"](_admin(), _u("u1", plan="starter"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "active",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    r = env["admin"].post("/admin/users/u1/stripe-sync")
    assert env["store"].get_by_id("u1")["plan"] == "pro"
    assert r.json()["plan"] == "pro"
    assert "plan" in r.json()["changed"]


def test_a_sync_that_removes_access_reaches_the_users_open_tab(env):
    """Realtime contract. An admin changing someone's access from the admin
    panel must not require that person to refresh to find out."""
    env["users"](_admin(), _u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": []})
    env["admin"].post("/admin/users/u1/stripe-sync")
    assert ("subscription_expired", "u1") in env["sent"]
    assert env["stopped"] == ["u1"]


def test_a_sync_that_restores_access_also_broadcasts(env):
    env["users"](_admin(), _u("u1", subscription_status="none", plan=None))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "active",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["admin"].post("/admin/users/u1/stripe-sync")
    assert ("subscription_active", "u1") in env["sent"]


# ── refusals ─────────────────────────────────────────────────────────────────

def test_syncing_a_comped_account_is_refused_not_applied(env):
    """"Sync from Stripe" on an in-app trial would otherwise revoke the comp the
    admin themselves granted — Stripe has no subscription to report for it."""
    env["users"](_admin(), _u("u1", subscription_status="trialing",
                              trial_ends_at=time.time() + 86400))
    env["stripe_says"]({"cus_u1": []})
    r = env["admin"].post("/admin/users/u1/stripe-sync")
    assert r.status_code == 400
    assert "trial" in r.json()["detail"].lower()
    assert env["store"].get_by_id("u1")["subscription_status"] == "trialing"
    assert env["sent"] == []


def test_syncing_an_account_with_no_stripe_customer_is_refused(env):
    env["users"](_admin(), {"id": "u1", "username": "u1",
                            "subscription_status": "expired", "created_at": time.time()})
    r = env["admin"].post("/admin/users/u1/stripe-sync")
    assert r.status_code == 400


def test_stripe_being_unreachable_changes_nothing_and_says_so(env):
    """A 200 with stale data would tell the admin the account is fine when
    nobody actually asked Stripe."""
    env["users"](_admin(), _u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": RuntimeError("stripe is down")})
    r = env["admin"].post("/admin/users/u1/stripe-sync")
    assert r.status_code == 502
    assert env["store"].get_by_id("u1")["subscription_status"] == "active"
    assert env["sent"] == []


def test_a_card_in_retry_is_not_reported_as_cancelled(env):
    env["users"](_admin(), _u("u1", plan="pro"))
    env["stripe_says"]({"cus_u1": [
        {"id": "sub_A", "status": "past_due",
         "items": {"data": [{"price": {"id": "price_pro"}}]}}]})
    env["admin"].post("/admin/users/u1/stripe-sync")
    from src.billing.plans import get_plan
    assert get_plan(env["store"].get_by_id("u1")) == "pro"
    assert env["stopped"] == [], "stopped the streams of a customer mid-retry"


# ── the two callers stay one implementation ──────────────────────────────────

def test_the_button_and_the_sweep_share_one_reconciler(env):
    """They were separate implementations and the manual one carried bugs the
    sweep did not. Assert the shared entry points rather than trusting it."""
    import inspect
    from src.dashboard import api
    for fn in (api.admin_stripe_sync, api.subscription_reconcile_task):
        src = inspect.getsource(fn)
        assert "reconcile_one_user" in src, f"{fn.__name__} reconciles by itself again"
        assert "reconcile_skip_reason" in src, f"{fn.__name__} has its own skip rules"
    assert "subscriptions.list" not in inspect.getsource(api.admin_stripe_sync), \
        "the admin endpoint is calling Stripe directly again"


def test_the_admin_ui_reports_what_actually_changed(env):
    """The old toast said "No subscription found" for a cancelled customer —
    i.e. it reported nothing happening in the case where it did the most."""
    from src.dashboard.api import ADMIN_HTML
    # Strip // comments first — the comment explaining why the old string went
    # away contains the old string, and would satisfy the assertion by itself.
    code = "\n".join(l for l in ADMIN_HTML.splitlines()
                     if not l.lstrip().startswith("//"))
    assert "No subscription found" not in code
    assert "Already in sync" in code
    assert "r.changed" in code, "the toast ignores what the sync reported"


# ── card-up-front trials must be reconciled, comps must not ──────────────────
# The sweep used to skip EVERY trialing account, on the reasoning that trialing
# could only mean an app-managed comp. It cannot any more: a card-up-front trial
# is the opening state of every new paying subscription, so the old rule would
# have excluded the bulk of new customers from the only sweep that catches
# drift — precisely the people whose access depends on a webhook having landed.

def test_a_stripe_trial_is_no_longer_skipped_by_the_sweep():
    from src.dashboard.api import reconcile_skip_reason
    stripe_trial = {"id": "u1", "subscription_status": "trialing",
                    "stripe_customer_id": "cus_1"}
    assert reconcile_skip_reason(stripe_trial) is None, \
        "card-up-front trials are excluded from reconciliation"


def test_a_comp_is_still_skipped_because_it_has_no_stripe_customer():
    from src.dashboard.api import reconcile_skip_reason
    comp = {"id": "u2", "subscription_status": "trialing"}
    assert reconcile_skip_reason(comp) is not None


def test_a_comp_that_once_subscribed_is_not_downgraded_by_the_sweep(monkeypatch):
    """The case the old blanket skip was really protecting: a comped user
    holding a stale customer id from an old subscription. Stripe honestly
    reports nothing live, and revoking on that would undo a comp an admin
    deliberately granted."""
    import asyncio
    from src.dashboard import api
    from src.billing import stripe_billing

    async def _truth(cust):
        return {"status": "inactive", "plan": None, "raw": "none",
                "trial_end": 0, "subscription": ""}
    monkeypatch.setattr(stripe_billing, "authoritative_subscription", _truth)

    comp = {"id": "u3", "subscription_status": "trialing",
            "stripe_customer_id": "cus_old",
            "trial_ends_at": time.time() + 86400}
    result = asyncio.run(api.reconcile_one_user(comp))
    assert result["ok"] is True
    assert result["drift"] == [], "the comp was revoked by reconciliation"
    assert "app-managed" in result["reason"]


def test_a_real_trial_that_stripe_says_is_live_is_still_applied(monkeypatch):
    """Only the DOWNGRADE is refused. Real state from Stripe still lands, or
    the protection above would freeze every trialing account forever."""
    import asyncio
    from src.dashboard import api
    from src.billing import stripe_billing
    from src.auth import users as user_store

    writes = []
    monkeypatch.setattr(user_store, "update_subscription",
                        lambda *a, **k: writes.append(a))
    monkeypatch.setattr(user_store, "set_plan", lambda *a: None)
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "subscription_status": "active",
                                     "plan": "pro"})

    async def _truth(cust):
        return {"status": "active", "plan": "pro", "raw": "active",
                "trial_end": 0, "subscription": "sub_1"}
    monkeypatch.setattr(stripe_billing, "authoritative_subscription", _truth)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)

    user = {"id": "u4", "subscription_status": "trialing",
            "stripe_customer_id": "cus_1", "plan": "pro"}
    result = asyncio.run(api.reconcile_one_user(user))
    assert ("status", "trialing", "active") in result["drift"]
    assert writes, "the trial→paid transition was not written"


def test_reconciling_a_trial_carries_stripes_trial_end_across(monkeypatch):
    """Without it a reconciled trial lands with no date for the dashboard to
    count down to — and, before the access gate learned to require `> 0`, would
    have been expired outright."""
    import asyncio
    from src.dashboard import api
    from src.billing import stripe_billing
    from src.auth import users as user_store

    ends = int(time.time()) + 5 * 86400
    writes = []
    monkeypatch.setattr(user_store, "update_subscription",
                        lambda *a, **k: writes.append(a))
    monkeypatch.setattr(user_store, "set_plan", lambda *a: None)
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "subscription_status": "trialing",
                                     "plan": "pro"})

    async def _truth(cust):
        return {"status": "trialing", "plan": "pro", "raw": "trialing",
                "trial_end": ends, "subscription": "sub_1"}
    monkeypatch.setattr(stripe_billing, "authoritative_subscription", _truth)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)

    user = {"id": "u5", "subscription_status": "none",
            "stripe_customer_id": "cus_1"}
    asyncio.run(api.reconcile_one_user(user))
    assert writes and writes[0][3] == ends, \
        f"the trial end was not carried across: {writes}"


def test_stripe_and_the_app_agree_on_what_trialing_is_called(monkeypatch):
    """authoritative_subscription used to fold trialing into active, same as
    the webhook did. If the two mappings drift, the hourly sweep 'corrects'
    every trialing customer into looking like they are being charged."""
    from src.billing import stripe_billing as sb2
    import inspect
    src = inspect.getsource(sb2.authoritative_subscription)
    assert 'if raw == "trialing"' in src, \
        "reconciliation still collapses trialing into active"
