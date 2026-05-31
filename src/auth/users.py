"""
Lightweight user management — stored as JSON, no database required.
Passwords hashed with PBKDF2-SHA256 + per-user salt.
"""

import hashlib
import json
import secrets
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
    _USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def get_all() -> list[dict]:
    return [{k: v for k, v in u.items() if k not in ("password_hash", "salt")} for u in _load()]


def get_by_id(user_id: str) -> dict | None:
    return next((u for u in _load() if u["id"] == user_id), None)


def get_by_username(username: str) -> dict | None:
    return next((u for u in _load() if u["username"].lower() == username.lower()), None)


def verify(user: dict, password: str) -> bool:
    dk, _ = _hash_password(password, user["salt"])
    return secrets.compare_digest(dk, user["password_hash"])


def create(username: str, password: str, is_admin: bool = False) -> dict:
    users = _load()
    if any(u["username"].lower() == username.lower() for u in users):
        raise ValueError(f"Username '{username}' already exists")
    dk, salt = _hash_password(password)
    user: dict = {
        "id": secrets.token_urlsafe(16),
        "username": username,
        "password_hash": dk,
        "salt": salt,
        "is_admin": is_admin,
        "created_at": time.time(),
    }
    users.append(user)
    _save(users)
    return user


def ensure_admin_exists(admin_password: str) -> None:
    """On first boot, seed an admin account from the existing dashboard password."""
    if not _load():
        create("admin", admin_password, is_admin=True)
