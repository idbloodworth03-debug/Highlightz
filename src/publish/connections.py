"""The user's connected posting accounts: one YouTube, TikTok or Instagram
login per user per platform, with the OAuth tokens that let the server post.

This is the first place the product holds a credential for somewhere other
than Twitch, so the rules are the ones users.py already lives by:

  * Tokens are encrypted at rest with the same Fernet key as the Twitch
    tokens (users._encrypt / _decrypt), and the file goes through
    atomic_write_json so it is 0600 and never half-written.
  * Everything is scoped by user_id at the store level. Another user's
    connection reads as missing, never as forbidden.
  * `public()` never carries a token. The dashboard needs to know WHICH
    account is connected and whether it is healthy, and nothing else.

`last_error` is how a dead connection shows up on screen: a refresh that
fails, a revoked grant, a platform saying no. The poster records it, the card
shows it, and the fix is always the same — Connect again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json, read_json

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "publish_connections.json"

PLATFORMS = ("youtube", "tiktok", "instagram")


@dataclass
class Connection:
    user_id: str
    platform: str
    account_id: str = ""
    account_name: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0          # epoch seconds; 0 = no known expiry
    scopes: str = ""
    connected_at: float = field(default_factory=time.time)
    last_error: str = ""
    # Per-platform odds and ends that are not worth a column each: the
    # Instagram user id, TikTok's open_id, the privacy level it will post at.
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        """What the dashboard sees. No token, in either form."""
        return {"platform": self.platform, "account_id": self.account_id,
                "account_name": self.account_name, "connected_at": self.connected_at,
                "expires_at": self.expires_at, "last_error": self.last_error,
                "extra": {k: v for k, v in self.extra.items()
                          if k in ("privacy_level", "note")}}


_conns: dict[tuple[str, str], Connection] = {}
_loaded = False


def _enc(v: str) -> str:
    from src.auth.users import _encrypt
    return _encrypt(v)


def _dec(v: str) -> str:
    from src.auth.users import _decrypt
    return _decrypt(v)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for r in read_json(_INDEX, []):
        try:
            c = Connection(**r)
        except TypeError:
            continue                    # a shape change must not drop every account
        c.access_token = _dec(c.access_token)
        c.refresh_token = _dec(c.refresh_token)
        _conns[(c.user_id, c.platform)] = c


def _save() -> None:
    rows = []
    for c in _conns.values():
        d = asdict(c)
        d["access_token"] = _enc(c.access_token)
        d["refresh_token"] = _enc(c.refresh_token)
        rows.append(d)
    atomic_write_json(_INDEX, rows)


def get(user_id: str, platform: str) -> Connection | None:
    _load()
    return _conns.get((user_id, platform))


def for_user(user_id: str) -> list[Connection]:
    _load()
    return [c for (u, _), c in _conns.items() if u == user_id]


def connected_platforms(user_id: str) -> set[str]:
    """Platforms this user can post to right now: connected and not marked
    broken. A connection with a last_error is still listed by for_user (the
    card shows the error) but it is not one the poster should try."""
    return {c.platform for c in for_user(user_id) if not c.last_error}


def save(conn: Connection) -> Connection:
    _load()
    if conn.platform not in PLATFORMS:
        raise ValueError(f"Unknown platform {conn.platform!r}")
    _conns[(conn.user_id, conn.platform)] = conn
    _save()
    return conn


def update_tokens(user_id: str, platform: str, *, access_token: str,
                  refresh_token: str | None = None, expires_at: float | None = None) -> None:
    c = get(user_id, platform)
    if not c:
        return
    c.access_token = access_token
    if refresh_token:
        c.refresh_token = refresh_token
    if expires_at is not None:
        c.expires_at = float(expires_at)
    c.last_error = ""
    _save()


def set_error(user_id: str, platform: str, message: str) -> None:
    c = get(user_id, platform)
    if not c:
        return
    c.last_error = (message or "")[:300]
    _save()


def remove(user_id: str, platform: str) -> Connection | None:
    _load()
    c = _conns.pop((user_id, platform), None)
    if c:
        _save()
    return c


def delete_all_for_user(user_id: str) -> int:
    """Account deletion has to take the posting credentials with it."""
    _load()
    gone = [k for k in _conns if k[0] == user_id]
    for k in gone:
        _conns.pop(k, None)
    if gone:
        _save()
    return len(gone)
