"""The 7-day self-serve trial that replaced the free tier.

A new account gets the whole product for a week with no card, and then locks.
Every account that existed BEFORE the cutover keeps the free tier permanently —
that is the grandfather flag, and it is the only thing separating a legacy free
user from a new user whose trial ran out. The two sit on identical
subscription_status values, so nothing else can tell them apart.

The failure modes here are expensive in both directions: lock out a paying-
adjacent user who was promised free access, or hand the full product away
forever to anyone who signs up.
"""

import time

import pytest

from src.billing.plans import PLAN_LIMITS, TRIAL_DAYS, get_plan, limits_for


# ── the trial ────────────────────────────────────────────────────────────────

def test_a_brand_new_account_starts_a_seven_day_trial(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")

    before = time.time()
    u = users.upsert_twitch_user("tw1", "nova", "nova")
    full = users.get_by_id(u["id"])

    assert full["subscription_status"] == "trialing"
    # Tolerance on BOTH sides: `before` is read before the call, so the delta is
    # a hair over 7 days, not under it.
    days = (full["trial_ends_at"] - before) / 86400
    assert abs(days - TRIAL_DAYS) < 0.01, f"trial is {days:.4f} days, not {TRIAL_DAYS}"


def test_the_trial_grants_the_whole_product():
    """A trial that only unlocked the free tier would prove nothing about the
    thing being sold — the point is to let someone find out whether the detector
    works on THEIR channels, which needs more than one stream."""
    trial = {"subscription_status": "trialing"}
    assert get_plan(trial) == "pro"
    assert limits_for(trial)["max_streams"] == PLAN_LIMITS["pro"]["max_streams"]
    assert limits_for(trial)["vod"] is True


def test_signing_in_again_does_not_restart_the_clock(tmp_path, monkeypatch):
    """The trial is granted in the branch that CREATES the account. If it were
    granted on every login, anyone could have an unlimited free product by
    signing out and back in."""
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")

    u = users.upsert_twitch_user("tw1", "nova", "nova")
    first_end = users.get_by_id(u["id"])["trial_ends_at"]
    users.upsert_twitch_user("tw1", "nova", "nova")          # sign in again
    assert users.get_by_id(u["id"])["trial_ends_at"] == first_end


def test_an_expired_trial_locks_rather_than_dropping_to_free():
    """The whole point of removing the free tier. An expired trial that fell
    back to free would be a free tier with extra steps."""
    assert get_plan({"subscription_status": "expired"}) == "locked"
    lim = limits_for({"subscription_status": "expired"})
    assert lim["max_streams"] == 0 and lim["max_pending"] == 0
    assert lim["vod"] is False and lim["uploads"] is False


def test_locked_is_expressed_as_zero_limits_not_as_a_new_gate():
    """Every access check in the product already asks limits_for() what this
    user may do. A separate `if locked:` branch would have to be added to
    add_stream, the pending cap, the VOD gate and the Clip Editor gate — and
    would be forgotten in one of them. Zeroes fail all four for free."""
    for key, zero in (("max_streams", 0), ("max_pending", 0)):
        assert PLAN_LIMITS["locked"][key] == zero
    assert PLAN_LIMITS["locked"]["vod"] is False
    assert PLAN_LIMITS["locked"]["uploads"] is False


def test_a_locked_user_is_still_offered_a_way_out():
    """An upgrade prompt that vanishes for exactly the person the paywall exists
    to convert. This regressed the moment `locked` was added and was caught by
    an existing test."""
    from src.dashboard import api
    nxt = api._next_tier({"subscription_status": "expired"})
    assert nxt is not None, "a locked user gets no upgrade path"
    assert nxt["plan"] == "starter"


# ── the grandfather ──────────────────────────────────────────────────────────

def test_an_account_that_predates_the_cutover_keeps_free_forever():
    legacy = {"subscription_status": "none", "grandfathered": True}
    assert get_plan(legacy) == "free"
    assert limits_for(legacy)["max_streams"] == 1


def test_the_flag_is_what_separates_two_identical_looking_users():
    """This is the reason it is a stored flag and not a date or a status check:
    both users below are cancelled subscribers with the same stored plan."""
    same = {"subscription_status": "canceled", "plan": "pro"}
    assert get_plan({**same, "grandfathered": True}) == "free"
    assert get_plan(same) == "locked"


def test_the_migration_marks_every_existing_account_and_is_idempotent(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    users._save([{"id": "a", "username": "a"}, {"id": "b", "username": "b"}])

    assert users.grandfather_existing_accounts() == 2
    assert all(u["grandfathered"] for u in users._load())
    # Running twice must not re-mark, or a later new account could be swept up.
    assert users.grandfather_existing_accounts() == 0


def test_a_new_signup_is_never_swept_up_by_a_later_migration_run(tmp_path, monkeypatch):
    """The migration marks accounts MISSING the key. New accounts are created
    with it explicitly False, so a second run cannot hand a new user the legacy
    free tier once their trial ends."""
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")

    u = users.upsert_twitch_user("tw9", "new", "new")
    users.grandfather_existing_accounts()
    assert users.get_by_id(u["id"])["grandfathered"] is False


def test_the_migration_runs_before_any_request_is_served():
    """A legacy user told their trial ended, because the flag had not been
    written yet, is the exact bug this ordering prevents."""
    import inspect
    from src import main
    src = inspect.getsource(main.main)
    assert "grandfather_existing_accounts()" in src
    assert src.index("grandfather_existing_accounts()") < src.index("run_dashboard()")


# ── what the page promises ───────────────────────────────────────────────────

def test_the_page_and_the_code_agree_on_the_number_of_days():
    """The landing page, the paywall and the signup path all quote 7. If
    TRIAL_DAYS moves and the copy does not, the product lies about its offer."""
    from src.dashboard.api import LANDING_HTML
    assert f"{TRIAL_DAYS} days free" in LANDING_HTML
    import inspect
    from src.auth import users
    assert "TRIAL_DAYS" in inspect.getsource(users.upsert_twitch_user)


def test_nothing_still_sells_a_free_forever_tier():
    from src.dashboard.api import LANDING_HTML as html
    assert "/forever" not in html
    assert "free plan" not in html.lower()
