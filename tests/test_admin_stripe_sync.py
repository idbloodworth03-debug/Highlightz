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
