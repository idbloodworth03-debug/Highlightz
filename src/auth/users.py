"""
Lightweight user management — stored as JSON, no database required.
Passwords hashed with PBKDF2-SHA256 + per-user salt.
Twitch OAuth users have no password — identified by twitch_id.
Their Twitch access/refresh tokens are encrypted at rest.
"""

import base64
import hashlib
import json
import logging
import os
import secrets

# The trial length lives with the plans, so the signup path and the landing
# page cannot disagree about how many days a new user gets.
import shutil
import tempfile
import time
from pathlib import Path

from config.settings import settings
from src.auth._jsonstore import atomic_write_json

_USERS_FILE = Path(settings.local_storage_path) / "users.json"


# ── Token encryption ───────────────────────────────────────────────────────
# Twitch OAuth tokens are encrypted at rest with a key derived from either
# TOKEN_ENCRYPTION_KEY (dedicated, preferred) or the session secret (legacy).
# If `cryptography` is unavailable, falls back to storing the raw value
# (the users file is already chmod 0600).
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except Exception:
        _ulog.warning("cryptography_unavailable: tokens stored unencrypted — pip install cryptography")
        return None
    if not settings.token_encryption_key:
        _ulog.warning(
            "TOKEN_ENCRYPTION_KEY_not_set: falling back to session secret for token encryption"
        )
    secret = settings.token_encryption_key or settings.dashboard_secret_key
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    if not value:
        return ""
    f = _fernet()
    if f is None:
        return value
    return f.encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    if not value:
        return ""
    f = _fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception as exc:
        _ulog.warning("token_decrypt_failed (possible key rotation): %s", exc)
        return ""


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return dk.hex(), salt


_ulog = logging.getLogger(__name__)

_BACKUP_FILE = Path(settings.local_storage_path) / "users.json.bak"

def _load() -> list[dict]:
    for path in (_USERS_FILE, _BACKUP_FILE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                if path == _BACKUP_FILE:
                    _ulog.warning("users_loaded_from_backup path=%s", path)
                return data
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as exc:
            _ulog.error("users_json_corrupt path=%s error=%s", path, exc)
            continue
    return []


def _save(users: list[dict]) -> None:
    """Atomic, 0600, previous owner preserved — see src/auth/_jsonstore.py for
    why that last part matters (a root-run admin script once made this file
    unreadable to the service and took the whole site down)."""
    atomic_write_json(_USERS_FILE, users, backup_path=_BACKUP_FILE)
    _ulog.debug("users_saved count=%d", len(users))


# The two kick_* entries are deliberate even though nothing writes them any
# more: records created before the Kick OAuth flow was removed still carry the
# encrypted values until the purge script has run, and _public() is what keeps
# them out of every API response in the meantime. Drop them from this tuple only
# once no user record contains either field.
_SECRET_FIELDS = ("password_hash", "salt", "tw_access", "tw_refresh", "kick_access", "kick_refresh")


def _public(user: dict) -> dict:
    return {k: v for k, v in user.items() if k not in _SECRET_FIELDS}


def get_all() -> list[dict]:
    return [_public(u) for u in _load()]


def get_by_id(user_id: str) -> dict | None:
    return next((u for u in _load() if u["id"] == user_id), None)


def get_by_username(username: str) -> dict | None:
    return next((u for u in _load() if u["username"].lower() == username.lower()), None)


def get_by_twitch_id(twitch_id: str) -> dict | None:
    return next((u for u in _load() if u.get("twitch_id") == twitch_id), None)


def verify(user: dict, password: str) -> bool:
    if not user.get("password_hash") or not user.get("salt"):
        return False
    dk, _ = _hash_password(password, user["salt"])
    return secrets.compare_digest(dk, user["password_hash"])


def create(username: str, password: str, is_admin: bool = False) -> dict:
    users = _load()
    if any(u["username"].lower() == username.lower() for u in users):
        raise ValueError(f"Username '{username}' already exists")
    dk, salt = _hash_password(password)
    user: dict = {
        "id":                   secrets.token_urlsafe(16),
        "username":             username,
        "password_hash":        dk,
        "salt":                 salt,
        "is_admin":             is_admin,
        "avatar_url":           "",
        "stripe_customer_id":   None,
        "subscription_status":  "active" if is_admin else "none",
        "created_at":           time.time(),
    }
    users.append(user)
    _save(users)
    return user


def upsert_twitch_user(
    twitch_id: str,
    login: str,
    username: str,
    avatar_url: str = "",
    access_token: str = "",
    refresh_token: str = "",
    expires_in: int = 0,
    is_admin: bool = False,
) -> dict:
    """Find or create a user by Twitch ID, storing encrypted OAuth tokens.

    Returns the public user dict (no secrets)."""
    users = _load()
    expires_at = time.time() + max(int(expires_in) - 60, 0)  # refresh 60s early
    enc_access  = _encrypt(access_token)
    enc_refresh = _encrypt(refresh_token)

    now      = time.time()
    existing = next((u for u in users if u.get("twitch_id") == twitch_id), None)
    if existing:
        existing["username"]    = username
        existing["twitch_login"] = login
        existing["avatar_url"]  = avatar_url
        # Only elevate to admin — never downgrade. This prevents ADMIN_TWITCH_ID
        # being unset (or wrong) from silently stripping admin on every Twitch login.
        if is_admin:
            existing["is_admin"] = True
        if access_token:
            existing["tw_access"]     = enc_access
            existing["tw_refresh"]    = enc_refresh
            existing["tw_expires_at"] = expires_at
        if is_admin:
            existing["subscription_status"] = "active"
        # No access is ever granted on login — subscriptions come from Stripe
        # Checkout, and free trials only from an explicit admin grant.
        _save(users)
        return _public(existing)

    # Brand-new account. NO ACCESS IS GRANTED HERE ANY MORE.
    #
    # The 7 free days still exist, but they are Stripe's now, not ours: the user
    # goes through Checkout, enters a card, and Stripe bills nothing until day
    # 7. This branch used to hand out `trialing` + a trial_ends_at with no card
    # on file, so a trial ending meant asking someone to come back and pay.
    #
    # `none`, deliberately, NOT `expired`. get_plan sends both to `locked`, so
    # access is identical — but the paywall reads this string to choose its
    # copy, and `expired` selects "Your free trial has ended", which is a lie
    # told to somebody who has not started one. `none` falls through to the
    # welcome variant.
    #
    # The trial ledger is no longer written here. A signup is not a trial any
    # more; the free week is claimed at checkout and burned in the webhook when
    # a trialing subscription actually appears. Signing up and never entering a
    # card must not spend somebody's one free week.
    user: dict = {
        "id":                   secrets.token_urlsafe(16),
        "username":             username,
        "password_hash":        None,
        "salt":                 None,
        "is_admin":             is_admin,
        "twitch_id":            twitch_id,
        "twitch_login":         login,
        "avatar_url":           avatar_url,
        "tw_access":            enc_access,
        "tw_refresh":           enc_refresh,
        "tw_expires_at":        expires_at,
        "stripe_customer_id":   None,
        "subscription_status":  "active" if is_admin else "none",
        "trial_ends_at":        0,
        # Explicitly NOT grandfathered: this account never had the free tier, so
        # with no subscription it locks rather than falling back to free.
        "grandfathered":        False,
        # Explicitly NOT pre-cutover: this account signed up under card-required
        # terms and is subject to them. See mark_pre_card_cutover_accounts.
        "pre_card_cutover":     False,
        "created_at":           now,
    }
    users.append(user)
    _save(users)
    return _public(user)


def mark_pre_card_cutover_accounts() -> int:
    """Mark every account that predates card-required signup.

    Signing up used to grant 7 free days with no card. It now sends you to
    Stripe Checkout to put a card on file. The instruction for everyone already
    here is that nothing changes, and this flag is what makes that true — it is
    read wherever the new terms would otherwise reach backwards:

      * _checkout_trial_days gives them 0, so a subscription they start bills
        immediately, exactly as it does today. Not a punishment: they already
        had their free access, and handing them a second free week would be a
        change to their terms too.
      * Their in-flight app-managed trials are untouched. This function does not
        look at subscription_status and does not clear trial_ends_at, so a user
        three days into a no-card trial keeps all seven and needs no card.

    Runs once at boot and is idempotent: an account already carrying the flag is
    skipped, and new accounts are created with it explicitly False, so a second
    run can never grandfather somebody who signed up after the cutover.

    Deliberately NOT a date comparison, for the same reason grandfather_existing_
    accounts is not one: created_at cannot separate a pre-cutover account from a
    post-cutover one once the boundary moment has passed and the process has
    restarted. Only a mark written once, at the boundary, can.
    """
    users = _load()
    marked = 0
    for u in users:
        if "pre_card_cutover" not in u:
            u["pre_card_cutover"] = True
            marked += 1
    if marked:
        _save(users)
        _ulog.info("marked %d accounts as predating card-required signup", marked)
    return marked


def grandfather_existing_accounts() -> int:
    """Mark every account that predates the self-serve trial as grandfathered.

    The free tier was replaced by a 7-day trial. Without this, `get_plan` would
    drop every non-paying account that already existed onto the locked plan the
    moment this deploys — including people mid-session and, worse, subscribers
    who had merely lapsed, who up to now kept using the product on free.

    Runs once at boot and is idempotent: an account that already carries the
    flag is skipped, and new accounts are created with it explicitly False, so
    a second run can never hand a new user the legacy free tier.

    Deliberately NOT a date comparison. A lapsed NEW subscriber and a legacy
    free user can sit on the identical subscription_status, and created_at
    cannot separate them once the cutover moment has passed — only an explicit
    mark, written once, can.
    """
    users = _load()
    marked = 0
    for u in users:
        if "grandfathered" not in u:
            u["grandfathered"] = True
            marked += 1
    if marked:
        _save(users)
        _ulog.info("grandfathered %d existing accounts onto the legacy free tier", marked)
    return marked


def _store_refreshed_tokens(user_id: str, access_token: str, refresh_token: str, expires_in: int) -> None:
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["tw_access"]     = _encrypt(access_token)
            u["tw_refresh"]    = _encrypt(refresh_token)
            u["tw_expires_at"] = time.time() + max(int(expires_in) - 60, 0)
            break
    _save(users)


class TwitchTokenTransientError(RuntimeError):
    """The refresh could not be completed, but nothing says the token is bad.

    Separated from "returns None" because the two mean opposite things to the
    caller and only one of them is the user's problem. None means Twitch told
    us the refresh token is dead and the user genuinely has to sign in again,
    which stops their streams. This means we could not reach Twitch, or Twitch
    had a bad minute — the token may well be fine, and the right response is to
    let the next moment try again rather than to stop every stream the user has
    and tell them to re-login.
    """


async def get_valid_twitch_token(user_id: str) -> str | None:
    """Return a currently-valid Twitch access token for the user, refreshing it
    via the stored refresh token if it has expired.

    Returns None when the user has no linked Twitch account, or when Twitch
    says the refresh token itself is no longer good — both mean re-login.
    Raises TwitchTokenTransientError when the refresh merely failed to happen.
    """
    user = get_by_id(user_id)
    if not user or not user.get("tw_access"):
        return None

    if time.time() < user.get("tw_expires_at", 0):
        return _decrypt(user["tw_access"])

    refresh = _decrypt(user.get("tw_refresh", ""))
    if not refresh:
        _ulog.warning("twitch_refresh_missing user_id=%s "
                      "detail=token_expired_and_no_refresh_token_stored", user_id)
        return None
    from src.auth import twitch_oauth
    try:
        tokens = await twitch_oauth.refresh_access_token(refresh)
    except Exception as exc:
        # WAS `except Exception: return None`, WITH NO LOG. That is how six
        # stream stoppages reached production with no recorded cause: every
        # failure — a revoked token, a DNS blip, a 500 from Twitch — produced
        # the identical silent None, and the caller turned all of them into
        # "your Twitch connection has expired, monitoring stopped".
        status = getattr(exc, "status", None)
        # 400/401 is Twitch answering, and the answer is that this refresh
        # token is finished — revoked, already rotated, or the app was
        # disconnected. That one really is a re-login.
        if status in (400, 401):
            _ulog.warning("twitch_refresh_rejected user_id=%s status=%s error=%s "
                          "detail=refresh_token_no_longer_valid_user_must_reauthorise",
                          user_id, status, str(exc)[:200])
            return None
        _ulog.warning("twitch_refresh_failed_transient user_id=%s status=%s "
                      "error_type=%s error=%s "
                      "detail=could_not_reach_twitch_token_may_still_be_fine",
                      user_id, status, type(exc).__name__, str(exc)[:200])
        raise TwitchTokenTransientError(
            f"Twitch token refresh failed for {user_id}: {type(exc).__name__}") from exc
    access = tokens.get("access_token", "")
    new_refresh = tokens.get("refresh_token", refresh)
    _store_refreshed_tokens(user_id, access, new_refresh, tokens.get("expires_in", 0))
    if not access:
        _ulog.warning("twitch_refresh_empty user_id=%s "
                      "detail=twitch_returned_200_with_no_access_token", user_id)
    return access or None


def set_miss_notice_dismissed(user_id: str, when: float) -> None:
    """Remember that the user closed the queue-full notice.

    Persisted rather than kept in the tab, so dismissing it once dismisses it
    everywhere and it does not reappear on the next page load — which is what
    made the notice feel broken.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["miss_notice_dismissed_at"] = when
            _save(users)
            return


# The two places a refusal is shown, and they are tracked SEPARATELY on
# purpose. An operator closing the banner on their own dashboard — where it
# appears because they monitor that channel themselves — must not also strike
# it off the control room's list, and acknowledging it in the control room must
# not silence the account-level warning. Same mechanism, two independent keys.
_REFUSAL_SCOPES = {"user": "refusals_dismissed", "admin": "refusals_acked"}


def set_refusal_dismissed(user_id: str, channel: str, when: float,
                          scope: str = "user") -> None:
    """Remember that this person closed the notice about one refusing channel.

    Per channel, not one flag for the whole notice: dismissing "kaicenat has
    clipping restricted" must not also hide a different channel breaking
    tomorrow.

    Stored as a timestamp rather than a boolean, so the notice can come back
    when there is something new to say — the reader compares it against the
    channel's last refusal. Same shape, and the same reason, as
    `miss_notice_dismissed_at`.
    """
    key = _REFUSAL_SCOPES.get(scope)
    if not channel or not key:
        return
    users = _load()
    for u in users:
        if u["id"] == user_id:
            d = dict(u.get(key) or {})
            d[channel.lower()] = when
            # Bounded: a channel dismissed long ago is deleted from the tally
            # the moment it clips again, so its entry here would otherwise sit
            # forever. Keep the most recent handful — anything older cannot
            # still be suppressing a live row.
            if len(d) > 50:
                d = dict(sorted(d.items(), key=lambda kv: kv[1])[-50:])
            u[key] = d
            _save(users)
            return


def refusal_dismissed_at(user: dict, channel: str, scope: str = "user") -> float:
    """When this person last closed the notice for `channel`, or 0."""
    key = _REFUSAL_SCOPES.get(scope)
    if not user or not channel or not key:
        return 0.0
    try:
        return float((user.get(key) or {}).get(channel.lower()) or 0)
    except (TypeError, ValueError):
        return 0.0


def set_ref_once(user_id: str, ref: str) -> bool:
    """Attribute a user to a referrer, FIRST TOUCH ONLY.

    Never overwrites. Someone who arrives through Tommy's link, returns a week
    later through Ian's and subscribes still counts as Tommy's — otherwise the
    person who posted most recently harvests everyone else's work and the
    weekly table stops telling you which lane actually produces users.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if u.get("ref"):
                return False
            u["ref"] = ref
            u["ref_at"] = time.time()
            _save(users)
            return True
    return False


def set_review_prompt_state(user_id: str, state: dict) -> None:
    """Persist when we last asked this user for a review, and whether they told
    us to stop. Lives on the user rather than in the reviews file because it
    exists even for people who never write one."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["review_prompt"] = state
            _save(users)
            return


def update_subscription(user_id: str, customer_id: str | None, status: str,
                        trial_ends_at: float | None = None) -> None:
    """Called by Stripe webhook to sync subscription state by user ID.

    `trial_ends_at` is Stripe's own trial_end, passed only for a card-up-front
    trial. Stored so the countdown the dashboard shows is the date the card is
    actually charged rather than a second clock of our own.

    None means "leave whatever is there alone" — most callers know nothing
    about trials and must not blank an app-managed trial's end date on an
    unrelated status change. Passing 0 explicitly does clear it, which is what
    leaving a trial for a real subscription should do.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if customer_id:
                u["stripe_customer_id"] = customer_id
            u["subscription_status"] = status
            if trial_ends_at is not None:
                u["trial_ends_at"] = trial_ends_at
            break
    _save(users)


def set_labeler(user_id: str, on: bool) -> bool:
    """Grant/revoke the trainer role: access to the blind clip-scoring studio
    (and nothing else — labelers are not admins). Returns True if user found."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if bool(u.get("is_labeler")) != on:
                u["is_labeler"] = on
                _save(users)
            return True
    return False


def set_admin(user_id: str, on: bool) -> bool:
    """Grant/revoke full admin: the admin portal, user management, and the
    permanent billing bypass. Returns True if user found. The caller is
    responsible for never letting the last admin revoke themselves."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if bool(u.get("is_admin")) != on:
                u["is_admin"] = on
                _save(users)
            return True
    return False


def set_affiliate_code(user_id: str, code: str | None) -> bool:
    """Give this account an affiliate code, or clear it with None.

    Validation and the uniqueness check live in src/auth/affiliates.py — this
    is only the write. Returns True if the account was found.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if code:
                u["affiliate_code"] = code
            else:
                u.pop("affiliate_code", None)
            _save(users)
            return True
    return False


def set_email(user_id: str, email: str, source: str = "stripe") -> None:
    """Record an email for this account, and where it came from.

    Two sources, and the difference matters when you look at the list:

      stripe — the billing address on their Stripe customer, learned at
               activation. Only exists for people who have paid.
      twitch — the account email, returned by Helix when the token carries
               user:read:email. Exists from sign-in, so it covers the people
               who never paid — which is the entire reason for asking.

    STRIPE WINS ON CONFLICT. A billing address is one somebody typed to receive
    receipts about money; a Twitch account email may be years old and unread.
    When they differ, the one that has already been used successfully is the
    better contact, so an incoming twitch address never overwrites a stored
    stripe one. The reverse does overwrite: learning the billing address is
    strictly newer information.

    An empty email is ignored rather than stored. Twitch returns nothing at all
    for a token without the scope, and writing that through would blank an
    address we already had.
    """
    if not email:
        return
    email = email.strip().lower()
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if source == "twitch" and u.get("email") \
                    and u.get("email_source") == "stripe":
                return
            if u.get("email") != email or u.get("email_source") != source:
                u["email"] = email
                u["email_source"] = source
                _save(users)
            return


def find_other_active_with_email(email: str, exclude_user_id: str) -> dict | None:
    """Another account with the same billing email AND a live paid
    subscription — the duplicate-signup case. Only 'active' counts: an
    app-managed trial on the other account isn't a payment, and past_due
    isn't a live sub."""
    if not email:
        return None
    email = email.strip().lower()
    for u in _load():
        if (u["id"] != exclude_user_id
                and (u.get("email") or "").strip().lower() == email
                and u.get("subscription_status") == "active"):
            return _public(u)
    return None


def mark_login(user_id: str) -> None:
    """Stamp the moment this account last completed Twitch OAuth.

    A DIFFERENT FACT FROM LAST SEEN, and the difference is the point. Last seen
    moves on every authenticated request, so it tells you whether somebody is
    still using the product. This moves only when they go through Twitch again
    and re-approve — which is what a session expiring, a sign-out, or a new
    permission being requested forces.

    That last case is why this exists now: user:read:email cannot reach anybody
    whose grant predates it, so "who has re-authorised since" is the question
    that says when their email will arrive, and nothing was recording it.

    Overwrites every time, unlike checkout_started_at. First-touch would answer
    "did they ever log in", which created_at already answers.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["last_login_at"] = time.time()
            _save(users)
            return


def mark_checkout_started(user_id: str) -> None:
    """Stamp the moment this account was sent to Stripe Checkout.

    THE MISSING SIGNAL. Without it, "signed up and never paid" and "went to the
    card form and backed out" are the same row in the database — no local state
    changes when somebody clicks through to Stripe, so the two most important
    drop-off points in the funnel are indistinguishable. That mattered less
    when signup itself granted a trial; now checkout IS the funnel.

    First touch wins. This answers "did they ever reach the card form", not
    "how many times did they open it", and overwriting on every click would
    turn the timestamp into a last-seen field that cannot answer either.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if not u.get("checkout_started_at"):
                u["checkout_started_at"] = time.time()
                _save(users)
            return


def set_plan(user_id: str, plan: str) -> None:
    """Record the membership tier ('starter'/'pro'), set by the Stripe webhook
    from the subscription's price id. Unlike promo attribution this always
    updates — upgrades/downgrades through the billing portal must take effect."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if u.get("plan") != plan:
                u["plan"] = plan
                _save(users)
            return


def set_promo_code(user_id: str, code: str) -> None:
    """Attribute the promo code used at signup — first attribution wins, so a
    later re-subscribe with a different code can't rewrite who referred the
    user (payouts key off this)."""
    if not code:
        return
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if not u.get("promo_code"):
                u["promo_code"] = code
                _save(users)
            return


def grant_trial(user_id: str, days: int, plan: str | None = None) -> dict | None:
    """Admin-granted timed trial: access until trial_ends_at, app-managed with
    no Stripe subscription behind it. Expiry is enforced by the auth middleware
    and the idle reaper, which flip the user to 'expired' and stop their streams
    once the clock runs out. Granting again extends/replaces the window
    (trial_ends_at is measured from now).

    `plan` picks WHICH membership the trial grants. Omitted keeps the original
    behaviour — the trial showcases the full product — so every trial granted
    before this argument existed still resolves to Pro.

    Returns the public user dict, or None if the user doesn't exist."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["subscription_status"] = "trialing"
            u["trial_ends_at"]       = time.time() + days * 86400
            if plan:
                u["plan"] = plan
                # NEVER touch stripe_customer_id here. That field is what tells
                # revenue apart from generosity: a granted membership has no
                # customer behind it, and pricing one would inflate MRR.
                u["plan_source"] = "granted"
            _save(users)
            return _public(u)
    return None


def grant_plan(user_id: str, plan: str) -> dict | None:
    """Comp a user a specific membership, with no end date and no Stripe.

    Separate from update_subscription() because that one is the WEBHOOK's entry
    point: it is called with a real Stripe customer and must not be taught to
    invent plans. This is the admin's entry point, and it records plan_source so
    the panel and the revenue figures can tell a comped Pro from a paying one.
    """
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["subscription_status"] = "active"
            u["plan"]                = plan
            u["plan_source"]         = "granted"
            # A comp replaces any previous trial window; leaving trial_ends_at
            # set would have the middleware expire a permanent grant.
            u.pop("trial_ends_at", None)
            _save(users)
            return _public(u)
    return None


def update_subscription_by_customer(customer_id: str, status: str,
                                    trial_ends_at: float | None = None) -> str | None:
    """Update subscription status when only the Stripe customer ID is known.
    Returns the affected user's ID, or None if not found."""
    users = _load()
    found_id = None
    for u in users:
        if u.get("stripe_customer_id") == customer_id:
            u["subscription_status"] = status
            if trial_ends_at is not None:
                u["trial_ends_at"] = trial_ends_at
            found_id = u["id"]
            break
    _save(users)
    return found_id


def delete(user_id: str) -> bool:
    """Remove a user by ID. Returns True if found and deleted."""
    users = _load()
    filtered = [u for u in users if u["id"] != user_id]
    if len(filtered) == len(users):
        return False
    _save(filtered)
    return True


# ── Kick OAuth helpers ─────────────────────────────────────────────────────────

def get_by_kick_id(kick_id: str) -> dict | None:
    return next((u for u in _load() if u.get("kick_id") == kick_id), None)


# ── Kick account linking: REMOVED 2026-08-27 ──────────────────────────────────
# link_kick_to_user, upsert_kick_user, _store_refreshed_kick_tokens and
# get_kick_token lived here and wrote `kick_access` / `kick_refresh` /
# `kick_expires_at` onto the user record. Kick monitoring is switched off and
# both legal pages state that no Kick credentials are requested or stored, so
# the flow that collected them is gone rather than the sentence that promised
# it would not. `get_by_kick_id` above is kept: it only reads an id.
#
# Existing records may still carry the encrypted fields. scripts/purge_kick_credentials.py
# strips them; run it once on production after deploying.


def ensure_admin_exists(admin_password: str) -> None:
    """On first boot or if no admin exists, seed an admin account.

    Refuses to seed an admin while the dashboard password is still the
    insecure default — otherwise anyone could log in as admin/highlightz.
    """
    from config.settings import _DEFAULT_PASSWORD
    import logging
    if admin_password == _DEFAULT_PASSWORD:
        logging.getLogger(__name__).critical(
            "SECURITY: refusing to seed admin account because DASHBOARD_PASSWORD "
            "is the default value. Set a strong DASHBOARD_PASSWORD in .env, then "
            "restart so the admin account can be created."
        )
        return
    users = _load()
    if not users or not any(u.get("is_admin") for u in users):
        if not any(u["username"].lower() == "admin" for u in users):
            create("admin", admin_password, is_admin=True)
