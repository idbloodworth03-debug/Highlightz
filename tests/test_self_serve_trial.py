"""The 7-day trial, now that it requires a card.

WHAT CHANGED. Signing up used to hand out `trialing` + a trial_ends_at with no
card on file. It now grants nothing: the user goes to Stripe Checkout, puts a
card down, and Stripe bills nothing until day 7. The free week still exists —
it just belongs to Stripe, so it converts by itself instead of asking someone
to come back and pay.

WHAT MUST NOT CHANGE. Two populations are frozen and neither may be disturbed
by any of this:

  * `grandfathered` — accounts from before the trial replaced the free tier.
    They keep the free plan permanently.
  * `pre_card_cutover` — accounts from before a card was required. They keep
    the terms they signed up under, in both directions: nothing is taken away,
    and a subscription they start bills immediately as it always has.

The failure modes are expensive in both directions: lock out someone who was
promised free access, or hand the product away to anyone who signs up.
"""

import time

import pytest

from src.billing.plans import PLAN_LIMITS, TRIAL_DAYS, get_plan, limits_for


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    return users


# ── signing up no longer grants anything ─────────────────────────────────────

def test_a_brand_new_account_gets_no_access_until_a_card_is_entered(store):
    """The cutover, in one assertion. This used to be seven free days."""
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    full = store.get_by_id(u["id"])
    assert full["subscription_status"] == "none"
    assert full["trial_ends_at"] == 0
    assert get_plan(full) == "locked"


def test_a_new_signup_is_not_told_their_trial_ended(store):
    """`none` and `expired` resolve to the same access, so the status could
    have been either — but the paywall reads it to pick its headline, and
    `expired` selects "Your free trial has ended". Telling that to somebody who
    has not started one is the reason this is its own status."""
    from src.dashboard import api
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    full = store.get_by_id(u["id"])
    assert full["subscription_status"] != "expired"
    copy = api._paywall_copy("new")
    assert "has ended" not in copy["headline"]


def test_signing_up_does_not_burn_the_free_week(store, tmp_path, monkeypatch):
    """A signup is not a trial any more. Someone who connects Twitch, looks
    around and never enters a card must still have their one free week waiting
    when they come back."""
    from src.auth import trial_ledger
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    store.upsert_twitch_user("tw1", "nova", "nova")
    assert trial_ledger.has_used_trial("twitch", "tw1") is False


def test_an_admin_signup_still_gets_straight_in(store):
    from config.settings import settings
    import src.auth.users as users_mod
    u = store.upsert_twitch_user(str(settings.admin_twitch_id or "tw_admin"),
                                 "owner", "owner", is_admin=True)
    assert store.get_by_id(u["id"])["subscription_status"] == "active"


# ── the trial itself, wherever it is run from ────────────────────────────────

def test_the_trial_grants_the_whole_product():
    """A trial that only unlocked the free tier would prove nothing about the
    thing being sold — the point is to let someone find out whether the detector
    works on THEIR channels, which needs more than one stream."""
    trial = {"subscription_status": "trialing"}
    assert get_plan(trial) == "pro"
    assert limits_for(trial)["max_streams"] == PLAN_LIMITS["pro"]["max_streams"]
    assert limits_for(trial)["vod"] is True


def test_a_stripe_trial_is_not_instantly_expired_by_the_access_gate(store, monkeypatch):
    """THE TRAP THIS CUTOVER CREATED. The gate reads

        status == 'trialing' and time.time() >= trial_ends_at

    and a Stripe-run trial can arrive with no trial_ends_at stored yet — so
    without the `> 0` guard the comparison is `time.time() >= 0`, which is true
    always. The customer would be expired, their streams stopped and a "your
    free trial has ended" toast fired at the instant they paid.

    DRIVEN THROUGH THE REAL MIDDLEWARE, not grepped for. The first version of
    this test asserted `"trial_ends_at > 0" in inspect.getsource(...)` and
    mutation testing walked straight past it: the guard was deleted from the
    CODE and the string still matched, because the comment above it quotes the
    expression. A source-string test cannot tell code from prose about code.
    """
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    from src.dashboard import api

    u = store.upsert_twitch_user("tw_stripe", "nova", "nova")
    # Exactly the shape the webhook has not caught up with yet: Stripe says
    # trialing, we have no end date of our own.
    store.update_subscription(u["id"], "cus_x", "trialing", 0)

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": u["id"], "username": "nova",
         "is_admin": False, "subscription_status": "trialing"}).encode())).decode())

    me = c.get("/me").json()
    assert me["subscription_status"] == "trialing", \
        "a trial with no stored end date was expired on sight"
    assert me["plan"] == "pro", "the paying customer was locked out"
    assert store.get_by_id(u["id"])["subscription_status"] == "trialing", \
        "the expiry was even persisted"


def test_an_app_managed_trial_still_expires_on_its_date(store, monkeypatch):
    """The other side of the same guard: making it tolerant of a MISSING end
    date must not make it tolerant of a PASSED one, or comped trials never end.
    """
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    from src.dashboard import api

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(api, "_stop_user_streams_now", _noop)

    u = store.upsert_twitch_user("tw_comp", "old", "old")
    store.grant_trial(u["id"], days=7)
    # Wind the clock past the end.
    users = store._load()
    for row in users:
        if row["id"] == u["id"]:
            row["trial_ends_at"] = time.time() - 60
    store._save(users)

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": u["id"], "username": "old",
         "is_admin": False, "subscription_status": "trialing"}).encode())).decode())

    me = c.get("/me").json()
    assert me["subscription_status"] == "expired", "an ended trial kept working"
    assert me["plan"] == "locked"


def test_an_expired_trial_locks_rather_than_dropping_to_free():
    """An expired trial that fell back to free would be a free tier with extra
    steps."""
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
    to convert."""
    from src.dashboard import api
    nxt = api._next_tier({"subscription_status": "expired"})
    assert nxt is not None, "a locked user gets no upgrade path"
    assert nxt["plan"] == "starter"


# ── who checkout offers free days to ─────────────────────────────────────────

def test_a_new_user_gets_the_free_week_at_checkout(store, tmp_path, monkeypatch):
    from src.auth import trial_ledger
    from src.dashboard import api
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    assert api._checkout_trial_days(store.get_by_id(u["id"])) == TRIAL_DAYS


def test_someone_who_already_had_a_free_week_pays_from_day_one(store, tmp_path,
                                                              monkeypatch):
    from src.auth import trial_ledger
    from src.dashboard import api
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    trial_ledger.record_trial("twitch", "tw1")
    assert api._checkout_trial_days(store.get_by_id(u["id"])) == 0


def test_a_pre_cutover_account_keeps_its_own_terms(store, tmp_path, monkeypatch):
    """Frozen means frozen in both directions. They already had their free
    access; a subscription they start bills immediately, exactly as it does
    today, rather than opening with a second free week."""
    from src.auth import trial_ledger
    from src.dashboard import api
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    legacy = {"id": "old", "twitch_id": "tw_old", "pre_card_cutover": True}
    assert api._checkout_trial_days(legacy) == 0


def test_no_user_record_bills_rather_than_giving_the_product_away():
    """Failing open on billing is the expensive direction."""
    from src.dashboard import api
    assert api._checkout_trial_days(None) == 0


def test_checkout_always_collects_a_card_even_on_a_trial():
    """Stripe's default for a trialing subscription in Checkout is
    payment_method_collection='if_required', which creates the trial with NO
    card when nothing is due today — silently restoring the exact situation
    this change exists to end, and only on trial checkouts."""
    import inspect
    from src.billing import stripe_billing
    src = inspect.getsource(stripe_billing.create_checkout_url)
    assert '"payment_method_collection": "always"' in src


def test_the_free_week_is_burned_when_stripe_starts_it_not_at_signup():
    """An abandoned checkout costs nothing and must not spend the one free
    week. The ledger entry belongs where the week is actually taken."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api._process_stripe_event)
    assert "record_trial" in src
    assert 'status == "trialing"' in src


# ── the grandfather (free tier) ──────────────────────────────────────────────

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


def test_the_migration_marks_every_existing_account_and_is_idempotent(store):
    store._save([{"id": "a", "username": "a"}, {"id": "b", "username": "b"}])
    assert store.grandfather_existing_accounts() == 2
    assert all(u["grandfathered"] for u in store._load())
    # Running twice must not re-mark, or a later new account could be swept up.
    assert store.grandfather_existing_accounts() == 0


def test_a_new_signup_is_never_swept_up_by_a_later_migration_run(store):
    """The migration marks accounts MISSING the key. New accounts are created
    with it explicitly False, so a second run cannot hand a new user the legacy
    free tier."""
    u = store.upsert_twitch_user("tw9", "new", "new")
    store.grandfather_existing_accounts()
    assert store.get_by_id(u["id"])["grandfathered"] is False


# ── the grandfather (card-required cutover) ──────────────────────────────────

def test_the_card_cutover_migration_marks_everyone_and_is_idempotent(store):
    store._save([{"id": "a", "username": "a"}, {"id": "b", "username": "b"}])
    assert store.mark_pre_card_cutover_accounts() == 2
    assert all(u["pre_card_cutover"] for u in store._load())
    assert store.mark_pre_card_cutover_accounts() == 0


def test_a_post_cutover_signup_is_never_swept_up_by_a_later_run(store):
    u = store.upsert_twitch_user("tw9", "new", "new")
    store.mark_pre_card_cutover_accounts()
    assert store.get_by_id(u["id"])["pre_card_cutover"] is False


def test_the_cutover_migration_does_not_touch_an_in_flight_trial(store):
    """A user three days into a no-card trial keeps all seven days and never
    needs a card. The migration adds a flag and reads neither
    subscription_status nor trial_ends_at."""
    ends = time.time() + 4 * 86400
    store._save([{"id": "mid", "username": "mid",
                  "subscription_status": "trialing", "trial_ends_at": ends}])
    store.mark_pre_card_cutover_accounts()
    u = store._load()[0]
    assert u["subscription_status"] == "trialing"
    assert u["trial_ends_at"] == ends
    assert get_plan(u) == "pro"


def test_both_migrations_run_before_any_request_is_served():
    """A legacy user told their trial ended, or a pre-cutover account handed
    the new terms because its flag had not been written yet, is the exact bug
    this ordering prevents."""
    import inspect
    from src import main
    src = inspect.getsource(main.main)
    for fn in ("grandfather_existing_accounts()", "mark_pre_card_cutover_accounts()"):
        assert fn in src
        assert src.index(fn) < src.index("run_dashboard()")


# ── what the page promises ───────────────────────────────────────────────────

def test_the_page_and_the_code_agree_on_the_number_of_days():
    """The landing page, the paywall and checkout all quote the same number. If
    TRIAL_DAYS moves and the copy does not, the product lies about its offer."""
    from src.dashboard.api import LANDING_HTML
    assert f"{TRIAL_DAYS} days free" in LANDING_HTML


def test_nothing_still_sells_a_free_forever_tier():
    from src.dashboard.api import LANDING_HTML as html
    assert "/forever" not in html
    assert "free plan" not in html.lower()
