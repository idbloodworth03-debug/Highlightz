"""
Lightweight user management — stored as JSON, no database required.
Passwords hashed with PBKDF2-SHA256 + per-user salt.
Discord OAuth users have no password — identified by discord_id.
"""

import hashlib
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from config.settings import settings

_USERS_FILE = Path(settings.local_storage_path) / "users.json"


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return dk.hex(), salt


def _load() -> list[dict]:
    try:
        return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _save(users: list[dict]) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_USERS_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)
        os.replace(tmp, _USERS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_all() -> list[dict]:
    return [{k: v for k, v in u.items() if k not in ("password_hash", "salt")} for u in _load()]


def get_by_id(user_id: str) -> dict | None:
    return next((u for u in _load() if u["id"] == user_id), None)


def get_by_username(username: str) -> dict | None:
    return next((u for u in _load() if u["username"].lower() == username.lower()), None)


def get_by_discord_id(discord_id: str) -> dict | None:
    return next((u for u in _load() if u.get("discord_id") == discord_id), None)


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
        "discord_id":           None,
        "avatar_url":           "",
        "stripe_customer_id":   None,
        "subscription_status":  "active" if is_admin else "none",
        "created_at":           time.time(),
    }
    users.append(user)
    _save(users)
    return user


def upsert_discord_user(discord_id: str, username: str, avatar_url: str = "") -> dict:
    """Find or create a user by Discord ID. Returns the public user dict (no secrets)."""
    users = _load()
    existing = next((u for u in users if u.get("discord_id") == discord_id), None)
    if existing:
        existing["username"]   = username
        existing["avatar_url"] = avatar_url
        _save(users)
        return {k: v for k, v in existing.items() if k not in ("password_hash", "salt")}

    user: dict = {
        "id":                   secrets.token_urlsafe(16),
        "username":             username,
        "password_hash":        None,
        "salt":                 None,
        "is_admin":             False,
        "discord_id":           discord_id,
        "avatar_url":           avatar_url,
        "stripe_customer_id":   None,
        "subscription_status":  "none",
        "created_at":           time.time(),
    }
    users.append(user)
    _save(users)
    return {k: v for k, v in user.items() if k not in ("password_hash", "salt")}


def update_subscription(user_id: str, customer_id: str, status: str) -> None:
    """Called by Stripe webhook to sync subscription state by user ID."""
    users = _load()
    for u in users:
        if u["id"] == user_id:
            u["stripe_customer_id"]  = customer_id
            u["subscription_status"] = status
            break
    _save(users)


def update_subscription_by_customer(customer_id: str, status: str) -> None:
    """Update subscription status when only the Stripe customer ID is known."""
    users = _load()
    for u in users:
        if u.get("stripe_customer_id") == customer_id:
            u["subscription_status"] = status
            break
    _save(users)


def delete(user_id: str) -> bool:
    """Remove a user by ID. Returns True if found and deleted."""
    users = _load()
    filtered = [u for u in users if u["id"] != user_id]
    if len(filtered) == len(users):
        return False
    _save(filtered)
    return True


def ensure_admin_exists(admin_password: str) -> None:
    """On first boot, seed an admin account from the existing dashboard password."""
    if not _load():
        create("admin", admin_password, is_admin=True)
