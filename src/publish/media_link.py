"""A public, time-limited link to one render, for platforms that fetch.

Instagram's publishing API does not take bytes: it is handed a URL and pulls
the video itself, unauthenticated, from a public address. Everything else in
the product sits behind the session, so this is the one door that has to
open without one — and it opens for exactly one file, for a bounded time, to
whoever holds a token nobody can forge.

The token is `<upload_id>.<expires>.<hmac>`; the MAC covers both so neither
the id nor the expiry can be changed without the key. Verification is
constant-time. The key is the token-encryption key when set (it is the
secret meant for exactly this kind of thing), else the session secret.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from config.settings import settings


def _key() -> bytes:
    return (settings.token_encryption_key or settings.dashboard_secret_key).encode()


def _mac(upload_id: str, expires: int) -> str:
    msg = f"{upload_id}.{expires}".encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()[:32]


def sign(upload_id: str, ttl_s: int | None = None, now: float | None = None) -> str:
    now = time.time() if now is None else now
    expires = int(now + (settings.publish_media_ttl_s if ttl_s is None else ttl_s))
    return f"{upload_id}.{expires}.{_mac(upload_id, expires)}"


def verify(token: str, now: float | None = None) -> str | None:
    """The upload id the token names, or None if it is forged or expired."""
    now = time.time() if now is None else now
    try:
        upload_id, exp_s, mac = token.split(".")
        expires = int(exp_s)
    except (ValueError, AttributeError):
        return None
    if now > expires:
        return None
    if not hmac.compare_digest(mac, _mac(upload_id, expires)):
        return None
    return upload_id


def public_url(upload_id: str) -> str:
    return settings.public_base_url.rstrip("/") + "/media/" + sign(upload_id)
