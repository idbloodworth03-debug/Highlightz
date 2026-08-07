"""Comping a SPECIFIC membership, not just "access".

Before this, /admin/users/{id}/grant set subscription_status='active' and
nothing else. get_plan() then fell through its last rule — an active
subscription with no stored plan is a grandfathered $15-era subscriber — and
returned 'pro'. So every grant was a Pro grant, by accident rather than by
design, and there was no way to hand anyone Starter.

The trial path had the same shape: 'trialing' returned 'pro' unconditionally.

The trap in fixing it: once a granted plan is STORED, the stored plan stops
being proof of payment. A comped Starter looks byte-identical to a paying one
apart from having no Stripe customer behind it, so revenue has to key off that
rather than off the tier.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A REAL user store on a temp file — these endpoints write, so mocking
    get_by_id would test nothing about whether the grant persisted."""
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats as ss

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_CLIP_COUNTER_FILE", tmp_path / "counter.json")

    now = time.time()
    seed = [
        {"id": "boss", "username": "boss", "is_admin": True,
         "subscription_status": "active", "created_at": now},
        {"id": "nobody", "username": "nobody", "subscription_status": "none",
         "created_at": now},
        {"id": "payer", "username": "payer", "subscription_status": "active",
         "plan": "pro", "stripe_customer_id": "cus_real", "created_at": now},
    ]
    (tmp_path / "users.json").write_text(_j.dumps(seed))

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)
    api._clips.clear(); api._streams.clear()

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": uid == "boss", "subscription_status": "active"}).encode())).decode())
        return c

    c.login = login
    c.store = user_store
    c.api = api
    yield c
    api._clips.clear(); api._streams.clear()


# ── granting a specific tier ─────────────────────────────────────────────────

@pytest.mark.parametrize("plan", ["starter", "pro"])
def test_an_admin_can_comp_either_tier(client, plan):
    from src.billing import plans
    r = client.login("boss").post(f"/admin/users/nobody/grant?plan={plan}")
    assert r.status_code == 200, r.text
    u = client.store.get_by_id("nobody")
    assert u["subscription_status"] == "active"
    assert u["plan"] == plan
    assert plans.get_plan(u) == plan, "the granted tier is not what the user resolves to"


def test_granting_starter_really_means_starter_and_not_pro(client):
    """The whole point. Before this, every grant resolved to Pro because a
    stored plan of None hits get_plan's grandfather rule."""
    from src.billing import plans
    client.login("boss").post("/admin/users/nobody/grant?plan=starter")
    u = client.store.get_by_id("nobody")
    assert plans.get_plan(u) == "starter"
    limits = plans.limits_for(u)
    assert limits["max_streams"] == 3 and limits["max_pending"] == 50
    assert limits["vod"] is False, "a comped Starter is being given the Pro VOD scanner"


def test_the_default_is_still_pro_so_an_older_caller_is_unchanged(client):
    """The endpoint took no plan before. A bookmarked call or an open older tab
    must keep doing exactly what it did rather than quietly downgrade someone."""
    from src.billing import plans
    client.login("boss").post("/admin/users/nobody/grant")
    assert plans.get_plan(client.store.get_by_id("nobody")) == "pro"


def test_an_unknown_plan_is_refused(client):
    r = client.login("boss").post("/admin/users/nobody/grant?plan=platinum")
    assert r.status_code == 400
    assert "platinum" in r.text
    assert client.store.get_by_id("nobody")["subscription_status"] == "none", \
        "a rejected grant still changed the user"


def test_a_permanent_comp_clears_any_trial_window(client):
    """A leftover trial_ends_at would have the auth middleware expire a grant
    that was meant to have no end date."""
    client.login("boss").post("/admin/users/nobody/grant-trial",
                              json={"days": 3, "plan": "starter"})
    assert client.store.get_by_id("nobody").get("trial_ends_at")
    client.login("boss").post("/admin/users/nobody/grant?plan=pro")
    assert not client.store.get_by_id("nobody").get("trial_ends_at"), \
        "a permanent comp kept a trial expiry and will be revoked by the middleware"


# ── timed grants ─────────────────────────────────────────────────────────────

def test_a_trial_can_grant_a_specific_tier(client):
    from src.billing import plans
    r = client.login("boss").post("/admin/users/nobody/grant-trial",
                                  json={"days": 30, "plan": "starter"})
    assert r.status_code == 200, r.text
    u = client.store.get_by_id("nobody")
    assert u["subscription_status"] == "trialing"
    assert plans.get_plan(u) == "starter"
    assert u["trial_ends_at"] > time.time()


def test_a_trial_with_no_tier_still_showcases_the_full_product(client):
    """The recorded reason trials exist. Every trial granted before a tier could
    be chosen has no stored plan, and must keep resolving to Pro."""
    from src.billing import plans
    client.login("boss").post("/admin/users/nobody/grant-trial", json={"days": 7})
    assert plans.get_plan(client.store.get_by_id("nobody")) == "pro"


def test_a_trial_with_an_unknown_tier_is_refused(client):
    r = client.login("boss").post("/admin/users/nobody/grant-trial",
                                  json={"days": 7, "plan": "gold"})
    assert r.status_code == 400
    assert client.store.get_by_id("nobody")["subscription_status"] == "none"


# ── the trap: a gift is not revenue ──────────────────────────────────────────

def test_a_comped_tier_is_never_counted_as_money(client):
    """The regression this change invites. Storing plan='starter' on a comped
    account makes it look identical to a paying Starter subscriber — and the
    MRR sum used to price anything with a stored paid plan. A Stripe customer,
    not the tier, is what proves someone is paying.
    """
    c = client.login("boss")
    c.post("/admin/users/nobody/grant?plan=starter")
    d = c.get("/admin/overview").json()
    assert d["mrr"] == 25, f"a comped Starter leaked into MRR: {d['mrr']}"
    assert d["users"]["comped"] == 1
    assert d["users"]["paying"] == 1, "the comped account is being counted as a customer"

    rows = {u["id"]: u for u in c.get("/admin/users").json()}
    assert rows["nobody"]["is_paying"] is False
    assert rows["nobody"]["plan_source"] == "granted"
    assert rows["payer"]["is_paying"] is True, "the real subscriber stopped counting"


def test_a_comp_never_invents_a_stripe_customer(client):
    """Any billing path that later keys off stripe_customer_id — the portal,
    cancel-on-delete, admin sync — would act on a customer that does not exist.
    """
    client.login("boss").post("/admin/users/nobody/grant?plan=pro")
    assert not client.store.get_by_id("nobody").get("stripe_customer_id")


def test_a_real_subscribers_record_is_untouched_by_the_new_field(client):
    """plan_source is derived for anyone who predates it, so an existing paying
    account does not need a migration to keep counting."""
    rows = {u["id"]: u for u in client.login("boss").get("/admin/users").json()}
    assert rows["payer"]["plan_source"] == "stripe"
    assert rows["payer"]["is_paying"] is True


# ── access ───────────────────────────────────────────────────────────────────

def test_granting_a_membership_is_admin_only(client):
    assert client.login("payer").post(
        "/admin/users/nobody/grant?plan=pro").status_code == 403
    assert client.login("payer").post(
        "/admin/users/nobody/grant-trial", json={"days": 7}).status_code == 403
    assert client.store.get_by_id("nobody")["subscription_status"] == "none"


def test_the_admin_panel_offers_both_tiers_in_both_places():
    """A picker that only lists Pro would leave the endpoint's new ability
    unreachable, which is indistinguishable from not having built it."""
    from src.dashboard.api import ADMIN_HTML as html
    assert "PLAN_OPTIONS" in html
    assert "['starter', 'Starter']" in html and "['pro', 'Pro']" in html
    assert "dr-plan" in html and "dr-days" in html, "the drawer has no tier/duration picker"
    assert "grant?plan=" in html, "the panel never sends a plan"
    assert 'JSON.stringify({days, plan})' in html, "the timed grant never sends a plan"
