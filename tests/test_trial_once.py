"""One free trial per Twitch account, and deleting the account does not reset it.

THE HOLE. The trial is granted in the branch of upsert_twitch_user that runs
only when no account exists for that Twitch id, so signing in again never
restarts the clock. But `DELETE /account` is a user-facing endpoint. Delete the
row, sign in with the same Twitch account, and that branch is reachable again —
another 7 days, repeatable indefinitely, by anyone who notices.

It cannot be fixed inside users.json, because the whole problem is that the row
is gone. So the record lives in a ledger that deletion does not reach, and the
test that matters is the delete-and-return cycle rather than anything about a
single signup.
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


# ── the normal path still works ──────────────────────────────────────────────

def test_a_first_time_signup_gets_the_full_trial(store):
    from src.billing.plans import TRIAL_DAYS, get_plan
    u = _signup(store)
    assert u["subscription_status"] == "trialing"
    days_left = (u["trial_ends_at"] - time.time()) / 86400
    assert TRIAL_DAYS - 0.01 < days_left <= TRIAL_DAYS
    assert get_plan(u) == "pro", "the trial is supposed to showcase the full product"


def test_signing_in_again_does_not_extend_the_trial(store):
    user_store, _ = store
    first = _signup(store)
    time.sleep(0.01)
    again = _signup(store)
    assert again["id"] == first["id"], "a second account was created"
    assert again["trial_ends_at"] == first["trial_ends_at"], "the clock restarted"


# ── the hole ─────────────────────────────────────────────────────────────────

def test_deleting_the_account_and_returning_does_not_grant_a_second_trial(store):
    """THE regression this file exists for. Free product, forever, one click."""
    from src.billing.plans import get_plan
    user_store, _ = store
    first = _signup(store)
    assert first["subscription_status"] == "trialing"

    user_store.delete(first["id"])
    assert user_store.get_by_id(first["id"]) is None

    second = _signup(store)
    assert second["id"] != first["id"], "expected a genuinely new account"
    assert second["subscription_status"] == "expired", \
        "a deleted-and-recreated account got another free trial"
    assert second["trial_ends_at"] == 0
    assert get_plan(second) == "locked", "the second trial still grants access"


def test_the_cycle_cannot_be_repeated(store):
    """Once is a bug; a loop is a business model for somebody else."""
    user_store, _ = store
    u = _signup(store)
    for _ in range(4):
        user_store.delete(u["id"])
        u = _signup(store)
        assert u["subscription_status"] == "expired"


def test_the_ledger_survives_the_users_file_being_wiped(store):
    """Deletion is the attack; a wiped users.json is the same shape."""
    user_store, tl = store
    _signup(store)
    (user_store._USERS_FILE).write_text("[]")
    assert tl.has_used_trial("twitch", "tw_1")
    assert _signup(store)["subscription_status"] == "expired"


# ── what it must not break ───────────────────────────────────────────────────

def test_a_different_twitch_account_still_gets_its_own_trial(store):
    """The bar is 'you cannot farm trials by clicking delete', not 'nobody new
    may ever try the product'."""
    assert _signup(store, "tw_1", "nova")["subscription_status"] == "trialing"
    assert _signup(store, "tw_2", "other")["subscription_status"] == "trialing"


def test_an_admin_signup_is_untouched(store):
    u = _signup(store, "tw_admin", "boss", is_admin=True)
    assert u["subscription_status"] == "active"
    assert u["trial_ends_at"] == 0


def test_an_admin_can_still_comp_someone_who_used_their_trial(store):
    """The ledger stops self-serve farming. An admin deciding to give somebody
    another look is a deliberate act and must keep working."""
    user_store, _ = store
    first = _signup(store)
    user_store.delete(first["id"])
    burned = _signup(store)
    assert burned["subscription_status"] == "expired"

    granted = user_store.grant_trial(burned["id"], days=14)
    assert granted is not None
    assert granted["subscription_status"] == "trialing"
    assert granted["trial_ends_at"] > time.time()


def test_a_failed_signup_does_not_burn_the_trial(store):
    """The ledger is written after the account is safely saved. Recording first
    would spend somebody's trial on a signup that never completed."""
    import inspect
    from src.auth import users as user_store
    src = inspect.getsource(user_store.upsert_twitch_user)
    saved_at = src.rindex("_save(users)")
    recorded_at = src.index('trial_ledger.record_trial("twitch"')
    assert saved_at < recorded_at, "the trial is recorded before the account exists"


# ── the ledger itself ────────────────────────────────────────────────────────

def test_the_ledger_does_not_store_raw_twitch_ids(store):
    """It outlives the account, so it is the one place holding an identifier
    for someone who asked to be forgotten. It answers 'seen before?' without
    being a list of who used the product."""
    _, tl = store
    _signup(store, "tw_SECRET123", "nova")
    raw = tl._LEDGER_FILE.read_text()
    assert "tw_SECRET123" not in raw
    assert "nova" not in raw


def test_recording_the_same_identity_twice_is_idempotent(store):
    _, tl = store
    tl.record_trial("twitch", "tw_9")
    tl.record_trial("twitch", "tw_9")
    assert tl.count() == 1


def test_an_unreadable_ledger_fails_open(store):
    """A corrupt ledger must not lock every future signup out of the trial.
    Failing open costs a few free weeks; failing closed breaks signup."""
    _, tl = store
    tl._LEDGER_FILE.write_text("{ not json")
    assert tl.has_used_trial("twitch", "tw_1") is False
    assert _signup(store)["subscription_status"] == "trialing"


def test_the_ledger_is_written_privately(store):
    import os
    _, tl = store
    _signup(store)
    assert oct(os.stat(tl._LEDGER_FILE).st_mode)[-3:] == "600"


# ── the accounts that predate the ledger ─────────────────────────────────────

def test_existing_accounts_are_backfilled_so_they_cannot_farm_either(store):
    """The ledger ships empty on a live install. Without a backfill it protects
    only people who signed up after it existed — which, on the day it ships, is
    nobody."""
    user_store, tl = store
    first = _signup(store, "tw_old", "veteran")
    tl._LEDGER_FILE.unlink()                      # as if the ledger never existed

    assert tl.backfill_from_existing_accounts() == 1
    user_store.delete(first["id"])
    assert _signup(store, "tw_old", "veteran")["subscription_status"] == "expired"


def test_the_backfill_is_idempotent(store):
    _, tl = store
    _signup(store, "tw_a", "a")
    _signup(store, "tw_b", "b")
    tl._LEDGER_FILE.unlink()
    assert tl.backfill_from_existing_accounts() == 2
    assert tl.backfill_from_existing_accounts() == 0
    assert tl.count() == 2


def test_the_backfill_takes_nothing_away_from_current_users(store):
    """They keep whatever their account says. It only bites if they delete it."""
    user_store, tl = store
    u = _signup(store, "tw_live", "live")
    tl.backfill_from_existing_accounts()
    after = user_store.get_by_id(u["id"])
    assert after["subscription_status"] == "trialing"
    assert after["trial_ends_at"] == u["trial_ends_at"]


def test_accounts_with_no_twitch_id_are_skipped(store):
    """Kick-only and password accounts have no Twitch identity to record."""
    user_store, tl = store
    user_store.create("localonly", "hunter2hunter2")
    assert tl.backfill_from_existing_accounts() == 0
