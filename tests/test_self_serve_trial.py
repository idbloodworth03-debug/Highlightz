"""The self-serve trial is retired and free is the front door again.

WHAT THIS FILE USED TO BE. It guarded the opposite arrangement: new signups got
7 days of full Pro with a card up front, free was legacy-only, and an account
with no subscription landed on `locked` — zero streams, zero queue. Every
assertion here has been inverted rather than deleted, because the mechanisms it
was protecting are still the ones that can break; only the intended answer
moved.

WHY IT MOVED. Card-up-front fixed the conversion problem the old app-managed
free week had and created a worse one: the card became the wall. Somebody who
wants to find out whether the detector works on their channel had to hand over
payment details first, so the top of the funnel was what got optimised away.

THE PART THAT MUST NOT BREAK. Trials already running are Stripe's, not ours,
and people are mid-trial with cards on file. `trialing` still has to resolve to
pro, and an in-flight trial has to convert or lapse exactly as it would have.
Retiring an offer must never reach backwards into the accounts that took it.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner

from src.billing.plans import PLAN_LIMITS, get_plan, limits_for


# ── the offer itself ─────────────────────────────────────────────────────────

def test_a_new_checkout_carries_no_free_days():
    """THE RETIREMENT, in one assertion. A checkout is now unambiguously
    'start paying' — the way to try the product without paying is to sign up
    and use it."""
    from src.dashboard import api
    for user in [
        {"id": "u", "twitch_id": "1"},
        {"id": "u", "twitch_id": "1", "pre_card_cutover": True},
        {"id": "u", "twitch_id": "1", "grandfathered": True},
        {},
        None,
    ]:
        assert api._checkout_trial_days(user) == 0, user


def test_signing_up_lands_on_free_not_locked():
    """The whole point. A brand-new account with no subscription is a working
    account — one stream, a real queue — rather than an empty shell behind a
    paywall."""
    fresh = {"id": "u1", "subscription_status": "none"}
    assert get_plan(fresh) == "free"
    limits = limits_for(fresh)
    assert limits["max_streams"] == 1
    assert limits["max_pending"] == PLAN_LIMITS["free"]["max_pending"] == 20
    assert limits["vod"] is False, "the free tier was given the VOD scanner"
    assert limits["uploads"] is False


def test_the_free_tier_has_no_clock_on_it():
    """A trial is a tier with an expiry. Free is not: nothing in the plan
    carries a duration, so there is no date on which access can quietly stop."""
    free = PLAN_LIMITS["free"]
    assert not any("day" in str(k).lower() or "trial" in str(k).lower() for k in free)
    import src.billing.plans as plans
    assert not hasattr(plans, "TRIAL_DAYS"), \
        "TRIAL_DAYS is back — the self-serve trial has returned without its copy"


@pytest.mark.parametrize("status", ["none", "canceled", "inactive", "expired", ""])
def test_every_way_of_not_paying_lands_on_free(status):
    """Never subscribed, cancelled, lapsed and finished-trial were four routes
    to `locked`. They are one route to `free` now, and the ones that differ
    only in wording must not diverge again."""
    assert get_plan({"id": "u", "subscription_status": status}) == "free"


def test_grandfathering_no_longer_decides_anything():
    """It used to be the flag that separated a legacy free user from a locked
    new one. With free reopened both are just free — and leaving the branch in
    would mean two classes of free user that nothing distinguishes."""
    with_flag    = {"id": "a", "subscription_status": "none", "grandfathered": True}
    without_flag = {"id": "b", "subscription_status": "none", "grandfathered": False}
    assert get_plan(with_flag) == get_plan(without_flag) == "free"
    assert limits_for(with_flag) == limits_for(without_flag)


# ── what retiring it must NOT touch ──────────────────────────────────────────

def test_a_trial_already_running_is_still_the_full_product():
    """People are mid-trial with cards on file. Stripe keeps billing them on
    the terms they signed up under, so the app has to keep honouring those
    terms — retiring an offer cannot reach backwards into accounts that took
    it."""
    assert get_plan({"id": "u", "subscription_status": "trialing"}) == "pro"
    assert limits_for({"id": "u", "subscription_status": "trialing"})["vod"] is True


def test_an_admin_granted_trial_still_works():
    """Separate mechanism, separate lifetime. Comping somebody a month is not
    the self-serve offer and does not retire with it."""
    assert get_plan({"id": "u", "subscription_status": "trialing",
                     "plan": "starter"}) == "starter"


def test_a_paying_subscriber_is_untouched():
    for plan in ("starter", "pro"):
        u = {"id": "u", "subscription_status": "active", "plan": plan}
        assert get_plan(u) == plan


def test_a_legacy_fifteen_dollar_subscriber_is_still_grandfathered_to_pro():
    """Checked last and requires status == active: the only case where a
    missing plan means paid."""
    assert get_plan({"id": "u", "subscription_status": "active"}) == "pro"


def test_a_deleted_account_still_gets_nothing():
    """`not user` is the one caller that can still reach the locked plan, and
    it has to: a live session for a row that is gone must not be handed a
    stream slot. Failing open on billing is the expensive direction."""
    assert get_plan(None) == "locked"
    assert limits_for(None)["max_streams"] == 0


# ── the access gate end to end ───────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    people = {
        "fresh": {"id": "fresh", "username": "fresh", "subscription_status": "none"},
        "onTrial": {"id": "onTrial", "username": "onTrial",
                    "subscription_status": "trialing",
                    "trial_ends_at": time.time() + 3 * 86400},
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": False,
             "subscription_status": people[uid]["subscription_status"],
             "trial_ends_at": people[uid].get("trial_ends_at", 0)}
        ).encode())).decode())
        return c

    c.login = login
    yield c


def test_a_brand_new_account_is_not_redirected_to_a_paywall(client):
    """AuthMiddleware must stay out of the way. It used to redirect every
    non-subscriber, so a signup without a card saw no product at all."""
    r = client.login("fresh").get("/me")
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "free"


def test_the_free_plan_reports_its_real_limits_to_the_dashboard(client):
    me = client.login("fresh").get("/me").json()
    assert me["plan_limits"]["max_streams"] == 1
    assert me["plan_limits"]["max_pending"] == PLAN_LIMITS["free"]["max_pending"]
    # Derived: what matters is that /me reports the SAME number the backend
    # enforces, not what that number currently is.
    assert me["plan_limits"]["max_suggested"] == PLAN_LIMITS["free"]["max_suggested"]
    assert me["plan_limits"]["max_suggested"] >= 1
    assert me["plan_limits"]["vod"] is False


def test_a_free_user_is_refused_the_vod_scanner(client):
    """Backend-side, not just hidden in the UI. A UI-only gate still lets a
    direct POST through."""
    # A VALID body on purpose. FastAPI validates before the handler runs, so a
    # malformed payload returns 422 without ever reaching the plan gate — and
    # the test would pass with no gate at all. (The first draft of this sent
    # {"url": ...} and did exactly that.)
    r = client.login("fresh").post(
        "/vod/analyze", json={"vod_url": "https://www.twitch.tv/videos/123456789"})
    assert r.status_code == 403, r.status_code


def test_someone_mid_trial_still_has_the_vod_scanner(client):
    """Their trial has not ended; nothing about retiring the offer should
    change what they can do today."""
    me = client.login("onTrial").get("/me").json()
    assert me["plan"] == "pro"
    assert me["plan_limits"]["vod"] is True
