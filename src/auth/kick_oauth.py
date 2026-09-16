"""Kick OAuth 2.1 helpers: authorization URL (PKCE), code exchange, and the
authenticated user lookup.

Sign-in only (owner, 2026-09-16: "make people able to sign up with kick now
too, either or"). The token is used ONCE, to ask Kick who just signed in, and
is then discarded: Kick monitoring runs on public endpoints and the app
token (src/ingestion/platform/kick.py), so nothing needs a user token and
nothing stores one. The legal pages say exactly that.

Endpoints per Kick's developer docs (id.kick.com / api.kick.com):
  authorize  GET  https://id.kick.com/oauth/authorize   (PKCE S256 required)
  token      POST https://id.kick.com/oauth/token
  me         GET  https://api.kick.com/public/v1/users  (no ids = the caller)
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

import aiohttp

from config.settings import settings

_AUTH_URL  = "https://id.kick.com/oauth/authorize"
_TOKEN_URL = "https://id.kick.com/oauth/token"
_USERS_URL = "https://api.kick.com/public/v1/users"

# user:read is what "who is this" needs. Nothing else is asked for, so the
# consent screen says only that.
_SCOPES = "user:read"


def configured() -> bool:
    return bool(settings.kick_client_id and settings.kick_client_secret)


def redirect_uri() -> str:
    return settings.kick_redirect_uri or (settings.public_base_url.rstrip("/") + "/auth/kick/callback")


def make_verifier() -> str:
    """A PKCE code_verifier: 43–128 URL-safe characters."""
    return secrets.token_urlsafe(48)


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(state: str, verifier: str) -> str:
    params = {
        "client_id":             settings.kick_client_id,
        "redirect_uri":          redirect_uri(),
        "response_type":         "code",
        "scope":                 _SCOPES,
        "state":                 state,
        "code_challenge":        challenge(verifier),
        "code_challenge_method": "S256",
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code(code: str, verifier: str) -> dict:
    """Exchange the authorization code for a token set.

    Returns {access_token, refresh_token, expires_in, token_type, scope}."""
    data = {
        "grant_type":    "authorization_code",
        "client_id":     settings.kick_client_id,
        "client_secret": settings.kick_client_secret,
        "redirect_uri":  redirect_uri(),
        "code":          code,
        "code_verifier": verifier,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data,
                                headers={"Accept": "application/json"}) as resp:
            resp.raise_for_status()
            return await resp.json()


def slug_for(name: str) -> str:
    """Kick's channel slug is the username lower-cased with spaces as
    hyphens; it is what kick.com/<slug> and the monitoring code key on."""
    return "-".join((name or "").strip().lower().split())


async def get_user(access_token: str) -> dict:
    """The user this token belongs to.

    Returns {"id", "username", "slug", "avatar_url", "email"}."""
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(_USERS_URL, headers=headers) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    data = payload.get("data") or []
    if not data:
        raise ValueError("Kick users endpoint returned no data")
    u = data[0]
    uid = u.get("user_id") or u.get("id")
    if uid in (None, ""):
        raise ValueError("Kick users endpoint returned no user id")
    name = u.get("name") or u.get("username") or ""
    return {
        "id":         str(uid),
        "username":   name,
        "slug":       slug_for(name),
        "avatar_url": u.get("profile_picture") or u.get("profile_pic") or "",
        "email":      (u.get("email") or "").strip().lower(),
    }
