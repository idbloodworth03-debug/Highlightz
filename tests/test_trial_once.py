"""The trial ledger outlived the trial. What it must and must not still do.

WHAT THIS FILE USED TO GUARD. One free week per Twitch account, and deleting
the account did not reset it. `DELETE /account` is user-facing, so a burner
could delete and sign in again looking brand new; the fix was a ledger that
deletion does not reach, read by `_checkout_trial_days`.

WHAT CHANGED. There is no self-serve trial any more — free is the front door,
so a checkout carries no free days for anybody and there is nothing left to
double-claim. The abuse this file existed to prevent is no longer reachable
because the thing being abused no longer exists.

WHY THE LEDGER STAYS ANYWAY. It is still WRITTEN — by the Stripe webhook, when
a trialing subscription appears — so it remains an accurate record of who has
had a free week. That matters for two reasons: the trials still finishing are
in it, and if a trial is ever reintroduced the history has to be there or the
first thing the new offer does is hand a second week to everyone who already
had one. What is gone is the READ that gated anything.

So the tests below are the inverse of the originals: the ledger must keep
recording, and it must NOT be able to change what a checkout offers.
"""

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
    from src.dashboard import api
    user_store, _ = store
    return api._checkout_trial_days(user_store.get_by_id(user["id"]))


# ── the offer is gone, for everyone, whatever the ledger says ────────────────

def test_a_first_time_signup_is_offered_no_free_days():
    """Inverted from `..._is_offered_the_full_free_week`. Not a regression —
    the trial was retired deliberately, and free is how somebody tries the
    product now."""
    from src.billing import plans
    assert not hasattr(plans, "TRIAL_DAYS")


def test_a_clean_account_gets_no_trial_at_checkout(store):
    assert _free_days(store, _signup(store)) == 0


def test_a_ledger_entry_cannot_change_the_offer_either(store):
    """The ledger used to be the difference between 7 days and 0. Now both
    answers are 0, and this is what proves the gate is genuinely gone rather
    than merely unreachable in the happy path."""
    _, tl = store
    u = _signup(store)
    tl.record_trial("twitch", "tw_1")
    assert tl.has_used_trial("twitch", "tw_1") is True
    assert _free_days(store, u) == 0


def test_a_signup_still_does_not_spend_anybody_s_week(store):
    """Connecting Twitch and looking around was never a trial, and must not
    start writing ledger entries now that signing up grants real access."""
    _, tl = store
    _signup(store)
    assert tl.has_used_trial("twitch", "tw_1") is False


# ── the ledger itself still works, because it is still written ───────────────

def test_the_ledger_records_and_deduplicates(store):
    """Written by the Stripe webhook when a trialing subscription appears."""
    _, tl = store
    tl.record_trial("twitch", "tw_9")
    tl.record_trial("twitch", "tw_9")
    assert tl.has_used_trial("twitch", "tw_9") is True
    assert tl.has_used_trial("twitch", "tw_other") is False


def test_the_ledger_survives_the_account_being_deleted(store):
    """THE ORIGINAL POINT, and still true. The record lives outside users.json
    precisely because deletion reaches that file and not this one — so if a
    trial is ever reintroduced it does not hand a second week to everyone who
    deleted and came back."""
    user_store, tl = store
    u = _signup(store)
    tl.record_trial("twitch", "tw_1")
    users = user_store._load()
    user_store._save([x for x in users if x["id"] != u["id"]])
    assert user_store.get_by_id(u["id"]) is None
    assert tl.has_used_trial("twitch", "tw_1") is True, \
        "deleting the account wiped the trial history"


def test_signing_in_again_does_not_create_a_second_account(store):
    first = _signup(store)
    again = _signup(store)
    assert first["id"] == again["id"]
