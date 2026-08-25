"""Who has already had a free trial — a record that outlives the account.

THE HOLE THIS CLOSES. The trial is granted in upsert_twitch_user, in the branch
that only runs when no account exists for that Twitch id. Signing in again
therefore does not restart the clock. But `DELETE /account` is a user-facing
endpoint: delete the account, sign in with the same Twitch account, and the
"no account exists" branch runs again and hands out another 7 days. Repeatable
indefinitely, for free, by anyone who notices.

Fixing it inside users.json is impossible by definition — the whole problem is
that the row is gone. So the record has to live somewhere deletion does not
reach, which is this file.

WHY HASHES AND NOT TWITCH IDS. This outlives the account, so it is the one
place holding an identifier for someone who has asked to be forgotten. A salted
hash answers the only question we need to ask ("have I seen this one before?")
while not being a list of the Twitch accounts that ever used the product. The
salt is generated once and kept beside the hashes; it is not the dashboard
secret, because that one is regenerated per process when unset and would
silently invalidate the whole ledger on restart.

NOT A FRAUD SYSTEM. Someone with a second Twitch account can have a second
trial, and that is fine — the bar is "you cannot farm trials by clicking delete",
not "you can never see this product twice".
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json, read_json

log = structlog.get_logger(__name__)

_LEDGER_FILE = Path(settings.local_storage_path) / "trials.json"


def _load() -> dict:
    data = read_json(_LEDGER_FILE, None)
    if not isinstance(data, dict) or not data.get("salt"):
        # First run, or a file damaged beyond use. A fresh salt means previously
        # recorded hashes no longer match, which fails OPEN (someone might get a
        # second trial) rather than locking out every future signup.
        return {"salt": secrets.token_hex(16), "seen": {}}
    data.setdefault("seen", {})
    return data


def _key(salt: str, platform: str, external_id: str) -> str:
    return hashlib.sha256(f"{salt}:{platform}:{external_id}".encode()).hexdigest()


def has_used_trial(platform: str, external_id: str) -> bool:
    """Has this platform identity already been given a self-serve trial?"""
    if not external_id:
        return False
    data = _load()
    return _key(data["salt"], platform, str(external_id)) in data["seen"]


def record_trial(platform: str, external_id: str) -> None:
    """Remember that this identity consumed its trial. Idempotent."""
    if not external_id:
        return
    data = _load()
    key = _key(data["salt"], platform, str(external_id))
    if key in data["seen"]:
        return
    data["seen"][key] = time.time()
    atomic_write_json(_LEDGER_FILE, data)
    log.info("trial_recorded", platform=platform, total=len(data["seen"]))


def count() -> int:
    return len(_load()["seen"])


def backfill_from_existing_accounts() -> int:
    """Record the PRE-CUTOVER accounts, which are the ones that had a free week
    just by signing up. Idempotent, runs at boot.

    Without this the ledger starts empty on a live install, so every legacy user
    could still delete-and-return for another free week — it would only protect
    people who signed up after it shipped.

    ONLY `pre_card_cutover` ACCOUNTS, AND THAT RESTRICTION IS THE WHOLE POINT.
    This used to record every account with a Twitch id, on the reasoning that
    "everyone here already has or had access, so recording them takes nothing
    away". That was true when signing up granted 7 days immediately. The card
    cutover made it false, and nothing here noticed:

        a new user signs up          -> offered 7 days, no ledger entry
        ANY deploy restarts the app  -> this backfill records them
        they come back and pick a plan-> has_used_trial() is now True
                                     -> _checkout_trial_days() returns 0
                                     -> Stripe Checkout asks for money TODAY

    So a signup that did not go straight through checkout lost its free week to
    the next deploy, and the person met a bill instead of the trial the site
    promised them. That is a customer stopping at the paywall for a reason that
    is entirely our fault.

    Post-cutover accounts get their entry from the webhook, at the moment Stripe
    actually starts a trial — which is the only event that means the week was
    really taken. A missing flag is treated as post-cutover: the flag is written
    by a migration that runs before this at boot, so an absent one means we
    cannot prove they had a trial, and the safe direction on a promise is to
    honour it.
    """
    from src.auth import users as user_store

    data = _load()
    added = 0
    for u in user_store.get_all():
        tid = u.get("twitch_id")
        if not tid:
            continue
        if u.get("pre_card_cutover") is not True:
            continue
        key = _key(data["salt"], "twitch", str(tid))
        if key in data["seen"]:
            continue
        # created_at, so the record reflects when they actually arrived rather
        # than when this migration happened to run.
        data["seen"][key] = u.get("created_at") or time.time()
        added += 1
    if added:
        atomic_write_json(_LEDGER_FILE, data)
        log.info("trial_ledger_backfilled", added=added, total=len(data["seen"]))
    return added


def prune_wrongly_burned_trials() -> int:
    """Give back the free weeks the old backfill took by mistake. Runs at boot.

    Fixing the backfill stops NEW signups losing their trial, and does nothing
    for the people it already happened to — their entry is sitting in the ledger
    on production right now, and every one of them meets a bill instead of a
    free week.

    Removes an entry only when all of these hold, which together mean the week
    provably was not taken:

      * the account is post-cutover, so signing up never granted it one;
      * it has no Stripe customer, so it has never been through Checkout and
        therefore cannot have started a Stripe trial;
      * it has no app-managed trial end date, so an admin never comped it.

    Anyone who really did use a week keeps their entry: a Stripe trial leaves a
    customer id behind, and a comp leaves a date.
    """
    from src.auth import users as user_store

    data = _load()
    removed = 0
    for u in user_store.get_all():
        tid = u.get("twitch_id")
        if not tid:
            continue
        if u.get("pre_card_cutover") is True:
            continue
        if u.get("stripe_customer_id") or u.get("trial_ends_at"):
            continue
        key = _key(data["salt"], "twitch", str(tid))
        if data["seen"].pop(key, None) is not None:
            removed += 1
    if removed:
        atomic_write_json(_LEDGER_FILE, data)
        log.warning("trial_ledger_pruned", restored=removed,
                    total=len(data["seen"]))
    return removed
