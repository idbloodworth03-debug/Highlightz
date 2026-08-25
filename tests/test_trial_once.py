"""One free week per Twitch account, and deleting the account does not reset it.

THE HOLE. `DELETE /account` is a user-facing endpoint. Delete the row, sign in
with the same Twitch account, and you look brand new again. It cannot be fixed
inside users.json, because the whole problem is that the row is gone — so the
record lives in a ledger that deletion does not reach.

WHAT MOVED. The free week used to be granted at signup, so the ledger was read
and written there. It is now Stripe's trial, claimed at checkout, so the ledger
gates `_checkout_trial_days` instead: a returning burner still reaches Checkout,
but Checkout bills them from day one. The guarantee is unchanged and the test
that matters is still the delete-and-return cycle — only the thing it asserts
against has moved from a signup status to the number of free days on offer.
"""

import time

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.auth import trial_ledger, users as user_store
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    return user_store, trial_ledger


def _signup(store, twitch_id="tw_1", login="nova", **kw):
    user_store, _ = store
    return user_store.upsert_twitch_user(
        twitch_id=twitch_id, login=login, username=login, **kw)


def _free_days(store, user):
    """What Checkout would offer this user — the new home of the guarantee."""
    from src.dashboard import api
    user_store, _ = store
    return api._checkout_trial_days(user_store.get_by_id(user["id"]))


# ── the normal path still works ──────────────────────────────────────────────

def test_a_first_time_signup_is_offered_the_full_free_week(store):
    from src.billing.plans import TRIAL_DAYS
    u = _signup(store)
    assert _free_days(store, u) == TRIAL_DAYS


def test_signing_up_alone_does_not_spend_the_week(store):
    """Connecting Twitch and looking around is not a trial. The week is spent
    when Stripe actually starts one."""
    _, tl = store
    _signup(store)
    assert tl.has_used_trial("twitch", "tw_1") is False


def test_signing_in_again_does_not_create_a_second_account(store):
    first = _signup(store)
    again = _signup(store)
    assert again["id"] == first["id"], "a second account was created"


# ── the hole ─────────────────────────────────────────────────────────────────

def test_deleting_the_account_and_returning_does_not_grant_a_second_week(store):
    """THE regression this file exists for. Free product, forever, one click."""
    user_store, tl = store
    first = _signup(store)
    tl.record_trial("twitch", "tw_1")             # Stripe started their trial

    user_store.delete(first["id"])
    assert user_store.get_by_id(first["id"]) is None

    second = _signup(store)
    assert second["id"] != first["id"], "expected a genuinely new account"
    assert _free_days(store, second) == 0, \
        "a deleted-and-recreated account was offered another free week"


def test_the_cycle_cannot_be_repeated(store):
    """Once is a bug; a loop is a business model for somebody else."""
    user_store, tl = store
    u = _signup(store)
    tl.record_trial("twitch", "tw_1")
    for _ in range(4):
        user_store.delete(u["id"])
        u = _signup(store)
        assert _free_days(store, u) == 0


def test_the_ledger_survives_the_users_file_being_wiped(store):
    """Deletion is the attack; a wiped users.json is the same shape."""
    user_store, tl = store
    _signup(store)
    tl.record_trial("twitch", "tw_1")
    (user_store._USERS_FILE).write_text("[]")
    assert tl.has_used_trial("twitch", "tw_1")
    assert _free_days(store, _signup(store)) == 0


# ── what it must not break ───────────────────────────────────────────────────

def test_a_different_twitch_account_still_gets_its_own_week(store):
    """The bar is 'you cannot farm trials by clicking delete', not 'nobody new
    may ever try the product'."""
    from src.billing.plans import TRIAL_DAYS
    _, tl = store
    a = _signup(store, "tw_1", "nova")
    tl.record_trial("twitch", "tw_1")
    b = _signup(store, "tw_2", "other")
    assert _free_days(store, a) == 0
    assert _free_days(store, b) == TRIAL_DAYS


def test_an_admin_signup_is_untouched(store):
    u = _signup(store, "tw_admin", "boss", is_admin=True)
    assert u["subscription_status"] == "active"
    assert u["trial_ends_at"] == 0


def test_an_admin_can_still_comp_someone_who_used_their_week(store):
    """The ledger stops self-serve farming. An admin deciding to give somebody
    another look is a deliberate act and must keep working — and it stays
    app-managed, with no card and no Stripe involvement."""
    user_store, tl = store
    first = _signup(store)
    tl.record_trial("twitch", "tw_1")
    user_store.delete(first["id"])
    burned = _signup(store)
    assert _free_days(store, burned) == 0

    granted = user_store.grant_trial(burned["id"], days=14)
    assert granted is not None
    assert granted["subscription_status"] == "trialing"
    assert granted["trial_ends_at"] > time.time()


def test_a_comped_trial_still_expires_on_its_own_date(store):
    """The access gate now requires trial_ends_at > 0 before expiring anyone.
    An admin comp always sets a real date, so it is still governed."""
    from src.billing.plans import get_plan
    user_store, _ = store
    u = _signup(store)
    granted = user_store.grant_trial(u["id"], days=14)
    assert granted["trial_ends_at"] > 0
    assert get_plan(user_store.get_by_id(u["id"])) == "pro"


# ── the ledger itself ────────────────────────────────────────────────────────

def test_the_ledger_does_not_store_raw_twitch_ids(store):
    """It outlives the account, so it is the one place holding an identifier
    for someone who asked to be forgotten. It answers 'seen before?' without
    being a list of who used the product."""
    _, tl = store
    tl.record_trial("twitch", "tw_SECRET123")
    raw = tl._LEDGER_FILE.read_text()
    assert "tw_SECRET123" not in raw


def test_recording_the_same_identity_twice_is_idempotent(store):
    _, tl = store
    tl.record_trial("twitch", "tw_9")
    tl.record_trial("twitch", "tw_9")
    assert tl.count() == 1


def test_an_unreadable_ledger_fails_open(store):
    """A corrupt ledger must not deny every future signup its free week.
    Failing open costs a few free weeks; failing closed makes the offer on the
    landing page a lie for everyone."""
    from src.billing.plans import TRIAL_DAYS
    _, tl = store
    tl._LEDGER_FILE.write_text("{ not json")
    assert tl.has_used_trial("twitch", "tw_1") is False
    assert _free_days(store, _signup(store)) == TRIAL_DAYS


def test_the_ledger_is_written_privately(store):
    import os
    _, tl = store
    tl.record_trial("twitch", "tw_1")
    assert oct(os.stat(tl._LEDGER_FILE).st_mode)[-3:] == "600"


# ── the accounts that predate the ledger ─────────────────────────────────────

def _make_legacy(store, *ids):
    """Mark accounts as predating the card cutover — the population that got a
    free week just by signing up, and the only one the backfill is for."""
    user_store, _ = store
    rows = user_store._load()
    for r in rows:
        if r.get("twitch_id") in ids:
            r["pre_card_cutover"] = True
    user_store._save(rows)


def test_legacy_accounts_are_backfilled_so_they_cannot_farm_either(store):
    """The ledger ships empty on a live install. Without a backfill it protects
    only people who signed up after it existed — which, on the day it ships, is
    nobody.

    PRE-CUTOVER ACCOUNTS ONLY. Those are the ones that got a week just by
    signing up, so recording them takes nothing away."""
    user_store, tl = store
    first = _signup(store, "tw_old", "veteran")
    _make_legacy(store, "tw_old")
    assert tl.has_used_trial("twitch", "tw_old") is False

    assert tl.backfill_from_existing_accounts() == 1
    user_store.delete(first["id"])
    assert _free_days(store, _signup(store, "tw_old", "veteran")) == 0


def test_the_backfill_is_idempotent(store):
    _, tl = store
    _signup(store, "tw_a", "a")
    _signup(store, "tw_b", "b")
    _make_legacy(store, "tw_a", "tw_b")
    assert tl.backfill_from_existing_accounts() == 2
    assert tl.backfill_from_existing_accounts() == 0
    assert tl.count() == 2


# ── the bug that sent two people to a bill instead of a free week ────────────

def test_a_deploy_does_not_burn_a_new_signups_free_week(store):
    """THE REGRESSION THIS FILE NOW EXISTS TO STOP.

    The backfill used to record EVERY account with a Twitch id, on the
    reasoning that everyone it touched had already had access. True when
    signing up granted 7 days; false the moment the card cutover made signup
    grant nothing. So:

        sign up (offered 7 days) -> any deploy -> pick a plan -> billed TODAY

    Anyone who did not go straight through checkout lost their trial to the
    next restart and met a bill instead of the week the site had promised."""
    from src.billing.plans import TRIAL_DAYS
    _, tl = store
    u = _signup(store, "tw_new", "nova")
    assert _free_days(store, u) == TRIAL_DAYS

    for _ in range(3):                       # three deploys
        tl.backfill_from_existing_accounts()

    assert _free_days(store, u) == TRIAL_DAYS, \
        "a deploy took away a signup's free week"
    assert tl.has_used_trial("twitch", "tw_new") is False


def test_the_repair_gives_back_a_week_the_old_backfill_took(store):
    """Fixing the backfill does nothing for the people it already happened to —
    their entry is sitting in the ledger on production."""
    from src.billing.plans import TRIAL_DAYS
    _, tl = store
    u = _signup(store, "tw_victim", "nova")
    tl.record_trial("twitch", "tw_victim")           # what the old backfill did
    assert _free_days(store, u) == 0                 # met a bill at the paywall

    assert tl.prune_wrongly_burned_trials() == 1
    assert _free_days(store, u) == TRIAL_DAYS
    # And it does not keep "restoring" the same account for ever.
    assert tl.prune_wrongly_burned_trials() == 0


@pytest.mark.parametrize("field,value", [
    ("pre_card_cutover", True),      # had a no-card week at signup
    ("stripe_customer_id", "cus_1"),  # been through Checkout, so could have had a Stripe trial
    ("trial_ends_at", 9e9),          # an admin comped them
])
def test_the_repair_does_not_hand_a_second_week_to_someone_who_had_one(store, field, value):
    """The repair has to be narrow. Each of these is evidence the week really
    was taken, and giving it back would make the ledger pointless."""
    user_store, tl = store
    u = _signup(store, "tw_had", "nova")
    rows = user_store._load()
    for r in rows:
        r[field] = value
    user_store._save(rows)
    tl.record_trial("twitch", "tw_had")

    assert tl.prune_wrongly_burned_trials() == 0
    assert tl.has_used_trial("twitch", "tw_had") is True
    assert _free_days(store, u) == 0


def test_the_repair_runs_at_boot_after_the_flag_it_depends_on():
    """It reads pre_card_cutover to tell the two groups apart, so it cannot run
    before the migration that writes it — and both must run before any request
    is served, or a signup reaching checkout first gets the wrong answer."""
    import inspect
    from src import main
    src = inspect.getsource(main.main)
    assert src.index("mark_pre_card_cutover_accounts()") \
        < src.index("prune_wrongly_burned_trials()") \
        < src.index("run_dashboard()")


def test_the_backfill_takes_nothing_away_from_current_users(store):
    """They keep whatever their account says. It only bites if they delete it."""
    user_store, tl = store
    u = _signup(store, "tw_live", "live")
    before = dict(user_store.get_by_id(u["id"]))
    tl.backfill_from_existing_accounts()
    assert user_store.get_by_id(u["id"]) == before


def test_accounts_with_no_twitch_id_are_skipped(store):
    """Kick-only and password accounts have no Twitch identity to record."""
    user_store, tl = store
    user_store.create("localonly", "hunter2hunter2")
    assert tl.backfill_from_existing_accounts() == 0
