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
from src.billing.plans import TRIAL_DAYS
import shutil
import tempfile
import time
from pathlib import Path

from config.settings import settings

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
                    _ulog.warning("users_loaded_from_backup", path=str(path))
                return data
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as exc:
            _ulog.error("users_json_corrupt", path=str(path), error=str(exc))
            continue
    return []


def _save(users: list[dict]) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp file first, then atomically replace
    fd, tmp = tempfile.mkstemp(dir=_USERS_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)
        # Keep a one-step-behind backup before overwriting
        if _USERS_FILE.exists():
            try:
                # Write to a temp file first so the backup is never world-readable
                fd2, tmp_bak = tempfile.mkstemp(dir=_USERS_FILE.parent, suffix=".bak.tmp")
                os.close(fd2)
                shutil.copyfile(_USERS_FILE, tmp_bak)
                os.chmod(tmp_bak, 0o600)
                os.replace(tmp_bak, _BACKUP_FILE)
            except OSError:
                pass
        # Whose file is it? Captured BEFORE the replace, because the temp file
        # is owned by whoever is running us — and that is not always the
        # service. An admin script run from a root shell would otherwise leave
        # users.json as 600 root:root, and the service (which does not run as
        # root) then cannot read its own user database. That is not theoretical:
        # it took the site down, because ensure_admin_exists() reads this file
        # at startup and a PermissionError there kills the process.
        prev_owner = None
        try:
            if _USERS_FILE.exists():
                st = os.stat(_USERS_FILE)
                prev_owner = (st.st_uid, st.st_gid)
        except OSError:
            pass

        os.replace(tmp, _USERS_FILE)
        try:
            os.chmod(_USERS_FILE, 0o600)
        except OSError:
            pass
        # Hand it back to the original owner. Only root can do this, which is
        # exactly the case that needs it — a non-root writer already owns the
        # file and the chown would be a no-op anyway.
        if prev_owner and os.geteuid() == 0:
            for target in (_USERS_FILE, _BACKUP_FILE):
                try:
                    if target.exists() and (os.stat(target).st_uid,
                                            os.stat(target).st_gid) != prev_owner:
                        os.chown(target, *prev_owner)
                except OSError as exc:
                    _ulog.warning("users_chown_failed", path=str(target), error=str(exc))
        _ulog.debug("users_saved", count=len(users))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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

    # Brand-new account: starts a 7-day self-serve trial, no card. This is the
    # only place a trial is granted automatically; grant_trial stays for admin
    # comps. It is tied to the Twitch id and this branch only runs when no
    # account exists for that id, so signing in again does not restart the
    # clock — re-trialling would need a whole new Twitch account.
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
        "subscription_status":  "active" if is_admin else "trialing",
        "trial_ends_at":        0 if is_admin else now + TRIAL_DAYS * 86400,
        # Explicitly NOT grandfathered: this account never had the free tier, so
        # when its trial runs out it locks rather than falling back to free.
        "grandfathered":        False,
        "created_at":           now,
    }
    users.append(user)
    _save(users)
    return _public(user)


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


async def get_valid_twitch_token(user_id: str) -> str | None:
    """Return a currently-valid Twitch access token for the user, refreshing
    it via the stored refresh token if it has expired. Returns None if the user
    has no linked Twitch account or the refresh fails."""
    user = get_by_id(user_id)
    if not user or not user.get("tw_access"):
        return None

    if time.time() < user.get("tw_expires_at", 0):
        return _decrypt(user["tw_access"])

    refresh = _decrypt(user.get("tw_refresh", ""))
    if not refresh:
        return None
    from src.auth import twitch_oauth
    try:
        tokens = await twitch_oauth.refresh_access_token(refresh)
    except Exception:
        return None
    access = tokens.get("access_token", "")
    new_refresh = tokens.get("refresh_token", refresh)
    _store_refreshed_tokens(user_id, access, new_refresh, tokens.get("expires_in", 0))
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


def update_subscription(user_id: str, customer_id: str | None, status: str) -> None:
    """Called by Stripe webhook to sync subscription state by user ID."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if customer_id:
                u["stripe_customer_id"] = customer_id
            u["subscription_status"] = status
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


def set_email(user_id: str, email: str) -> None:
    """Record the billing email (from the Stripe customer at activation) —
    powers the duplicate-signup guard and gives support a contact address."""
    if not email:
        return
    email = email.strip().lower()
    users = _load()
    for u in users:
        if u["id"] == user_id:
            if u.get("email") != email:
                u["email"] = email
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


def update_subscription_by_customer(customer_id: str, status: str) -> str | None:
    """Update subscription status when only the Stripe customer ID is known.
    Returns the affected user's ID, or None if not found."""
    users = _load()
    found_id = None
    for u in users:
        if u.get("stripe_customer_id") == customer_id:
            u["subscription_status"] = status
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


def link_kick_to_user(
    user_id: str,
    kick_id: str,
    username: str,
    slug: str,
    avatar_url: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
) -> dict:
    """Link a Kick account to an existing Highlightz user (already logged in via Twitch).

    Updates the user record with Kick OAuth fields and returns the public user dict.
    """
    users = _load()
    expires_at = time.time() + max(int(expires_in) - 60, 0)
    enc_access  = _encrypt(access_token)
    enc_refresh = _encrypt(refresh_token)

    for u in users:
        if u["id"] == user_id:
            u["kick_id"]         = kick_id
            u["kick_slug"]       = slug
            u["kick_username"]   = username
            if avatar_url and not u.get("avatar_url"):
                u["avatar_url"]  = avatar_url
            if access_token:
                u["kick_access"]     = enc_access
                u["kick_refresh"]    = enc_refresh
                u["kick_expires_at"] = expires_at
            _save(users)
            return _public(u)

    raise ValueError(f"User '{user_id}' not found")


def upsert_kick_user(
    kick_id: str,
    username: str,
    slug: str,
    avatar_url: str = "",
    access_token: str = "",
    refresh_token: str = "",
    expires_in: int = 0,
) -> dict:
    """Find or create a Highlightz account identified solely by Kick ID.

    Used when a user signs in with Kick without a pre-existing Twitch account.
    Returns the public user dict (no secrets).
    """
    users = _load()
    expires_at = time.time() + max(int(expires_in) - 60, 0)
    enc_access  = _encrypt(access_token)
    enc_refresh = _encrypt(refresh_token)
    now = time.time()

    existing = next((u for u in users if u.get("kick_id") == kick_id), None)
    if existing:
        existing["kick_slug"]     = slug
        existing["kick_username"] = username
        if avatar_url:
            existing["avatar_url"] = avatar_url
        if access_token:
            existing["kick_access"]     = enc_access
            existing["kick_refresh"]    = enc_refresh
            existing["kick_expires_at"] = expires_at
        _save(users)
        return _public(existing)

    # A new account begins with no access and goes through the paywall →
    # checkout; free access exists only as an admin-granted timed trial.
    user: dict = {
        "id":                   secrets.token_urlsafe(16),
        "username":             username,
        "password_hash":        None,
        "salt":                 None,
        "is_admin":             False,
        "kick_id":              kick_id,
        "kick_slug":            slug,
        "kick_username":        username,
        "avatar_url":           avatar_url,
        "kick_access":          enc_access,
        "kick_refresh":         enc_refresh,
        "kick_expires_at":      expires_at,
        "stripe_customer_id":   None,
        "subscription_status":  "none",
        "trial_ends_at":        0,
        "created_at":           now,
    }
    users.append(user)
    _save(users)
    return _public(user)


def _store_refreshed_kick_tokens(user_id: str, access_token: str, refresh_token: str, expires_in: int) -> None:
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["kick_access"]     = _encrypt(access_token)
            u["kick_refresh"]    = _encrypt(refresh_token)
            u["kick_expires_at"] = time.time() + max(int(expires_in) - 60, 0)
            break
    _save(users)


async def get_kick_token(user_id: str) -> str | None:
    """Return a currently-valid Kick access token for the user, refreshing
    it via the stored refresh token if it has expired. Returns None if the user
    has no linked Kick account or the refresh fails."""
    user = get_by_id(user_id)
    if not user or not user.get("kick_access"):
        return None

    if time.time() < user.get("kick_expires_at", 0):
        return _decrypt(user["kick_access"])

    refresh = _decrypt(user.get("kick_refresh", ""))
    if not refresh:
        return None
    from src.auth import kick_oauth
    try:
        tokens = await kick_oauth.refresh_access_token(refresh)
    except Exception:
        return None
    access = tokens.get("access_token", "")
    new_refresh = tokens.get("refresh_token", refresh)
    _store_refreshed_kick_tokens(user_id, access, new_refresh, tokens.get("expires_in", 0))
    return access or None


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
