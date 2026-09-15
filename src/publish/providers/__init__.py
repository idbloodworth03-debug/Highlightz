"""Posting providers: the three places a finished clip can be sent, each
behind the same small interface so the poster and the API never know which
one they are talking to.

Owner's decision, 2026-09-15, reversing 2026-08-02: the Scheduler POSTS.
The cost paragraph in HANDOFF (platform review, YouTube's quota, Instagram
fetching from a public URL) is still true; the owner chose to pay it. Each
provider documents its own version of that cost at the top of its file.

The contract every provider satisfies:

    configured()                       operator has registered an app
    authorization_url(state, chal)     where to send the user
    exchange(code, verifier) -> dict   tokens + who the account is
    refresh(conn) -> dict              a fresh access token
    revoke(conn)                       best effort, never raises
    post(conn, path, size, caption, fmt, duration_s) -> PostResult

`post` raises ProviderError with a message safe to show the user. `reauth`
means the connection is dead (revoked, expired past refresh) and the fix is
to connect again; `retryable` means the same request may work later (a
quota, a 5xx) and the item should be left retryable rather than marked as
the user's problem.
"""

from __future__ import annotations

import time
from typing import NamedTuple

from config.settings import settings


class ProviderError(Exception):
    def __init__(self, message: str, *, reauth: bool = False, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.reauth = reauth
        self.retryable = retryable


class PostResult(NamedTuple):
    url: str
    remote_id: str
    note: str = ""


def redirect_uri(platform: str) -> str:
    return settings.public_base_url.rstrip("/") + f"/publish/connect/{platform}/callback"


def caption_title(caption: str, fallback: str, limit: int) -> str:
    """The first line of the caption, as a title. YouTube and TikTok both
    want a title and a creator writes one caption; the first line is what
    they would have typed into a title box."""
    first = next((ln.strip() for ln in (caption or "").splitlines() if ln.strip()), "")
    return (first or fallback or "Clip")[:limit].rstrip()


def content_type(fmt: str) -> str:
    return {"webm": "video/webm", "mov": "video/quicktime"}.get(
        (fmt or "").lower().lstrip("."), "video/mp4")


def _registry():
    from . import youtube, tiktok, instagram
    return {p.id: p for p in (youtube.PROVIDER, tiktok.PROVIDER, instagram.PROVIDER)}


def get(platform: str):
    return _registry().get(platform)


def all_providers():
    return list(_registry().values())


async def ensure_fresh(provider, conn) -> None:
    """Refresh the access token if it is about to expire, and persist it.

    Called before every post. A provider whose tokens do not expire (or that
    hands out none) leaves expires_at at 0 and this is a no-op.
    """
    from src.publish import connections
    if not conn.expires_at:
        return
    if conn.expires_at - time.time() > provider.refresh_margin_s:
        return
    if not conn.refresh_token and not provider.refreshes_without_refresh_token:
        raise ProviderError(f"Your {provider.label} connection has expired. Connect it again.",
                            reauth=True)
    try:
        fresh = await provider.refresh(conn)
    except ProviderError:
        raise
    except Exception as exc:                      # network, malformed reply
        raise ProviderError(f"Could not refresh your {provider.label} login: {exc}",
                            retryable=True)
    conn.access_token = fresh["access_token"]
    if fresh.get("refresh_token"):
        conn.refresh_token = fresh["refresh_token"]
    conn.expires_at = float(fresh.get("expires_at") or 0)
    connections.update_tokens(conn.user_id, conn.platform,
                              access_token=conn.access_token,
                              refresh_token=conn.refresh_token,
                              expires_at=conn.expires_at)
