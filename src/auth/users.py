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
import shutil
import tempfile
import time
from pathlib import Path

from config.settings import settings

_USERS_FILE = Path(settings.local_storage_path) / "users.json"

TRIAL_DAYS = 7
_TRIAL_SECONDS = TRIAL_DAYS * 86400


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

# ── Trial ledger ────────────────────────────────────────────────────────────
# A persistent record of every Twitch ID that has EVER started a free trial.
# This deliberately survives account deletion so a user can't reset their trial
# by deleting and re-creating their account (infinite-trial abuse).
_TRIAL_LEDGER_FILE = Path(settings.local_storage_path) / "trial_claims.json"


def _load_trial_ledger() -> set[str]:
    try:
        data = json.loads(_TRIAL_LEDGER_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as exc:
        _ulog.error("trial_ledger_corrupt", error=str(exc))
    return set()


def has_claimed_trial(twitch_id: str) -> bool:
    """True if this Twitch ID has ever been granted a free trial."""
    return bool(twitch_id) and twitch_id in _load_trial_ledger()


def _record_trial_claim(twitch_id: str) -> None:
    """Persist that this Twitch ID has claimed its one-time trial."""
    if not twitch_id:
        return
    ledger = _load_trial_ledger()
    if twitch_id in ledger:
        return
    ledger.add(twitch_id)
    _TRIAL_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_TRIAL_LEDGER_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sorted(ledger), f)
        os.replace(tmp, _TRIAL_LEDGER_FILE)
        try:
            os.chmod(_TRIAL_LEDGER_FILE, 0o600)
        except OSError:
            pass
    except Exception as exc:
        _ulog.error("trial_ledger_save_failed", error=str(exc))
        try:
            os.unlink(tmp)
        except OSError:
            pass


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
        os.replace(tmp, _USERS_FILE)
        try:
            os.chmod(_USERS_FILE, 0o600)
        except OSError:
            pass
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
        existing["is_admin"]    = is_admin
        if access_token:
            existing["tw_access"]     = enc_access
            existing["tw_refresh"]    = enc_refresh
            existing["tw_expires_at"] = expires_at
        if is_admin:
            existing["subscription_status"] = "active"
        elif (existing.get("subscription_status") == "none"
              and not existing.get("trial_ends_at")
              and not has_claimed_trial(twitch_id)):
            # Account existed before trials were introduced (or was admin-created)
            # and has never claimed a trial. Grant the one-time 7-day trial.
            existing["subscription_status"] = "trialing"
            existing["trial_ends_at"]       = now + _TRIAL_SECONDS
            _record_trial_claim(twitch_id)
        _save(users)
        return _public(existing)

    # Brand-new account. Grant a trial only if this Twitch ID has never had one
    # — prevents resetting the trial via delete-and-recreate.
    grant_trial = not is_admin and not has_claimed_trial(twitch_id)
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
        # New users get a one-time 7-day free trial (no card required).
        # Returning users who already used their trial must subscribe.
        "subscription_status":  "active" if is_admin else ("trialing" if grant_trial else "none"),
        "trial_ends_at":        (now + _TRIAL_SECONDS) if grant_trial else 0,
        "created_at":           now,
    }
    users.append(user)
    _save(users)
    if grant_trial:
        _record_trial_claim(twitch_id)
    return _public(user)


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

    # Trial: use a prefixed key so Kick trials are tracked separately from Twitch trials
    prefixed_id = f"kick:{kick_id}"
    grant_trial = not has_claimed_trial(prefixed_id)
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
        "subscription_status":  "trialing" if grant_trial else "none",
        "trial_ends_at":        (now + _TRIAL_SECONDS) if grant_trial else 0,
        "created_at":           now,
    }
    users.append(user)
    _save(users)
    if grant_trial:
        _record_trial_claim(prefixed_id)
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
