"""Kick OAuth 2.1 helpers — PKCE authorization, code exchange, token refresh,
and authenticated user lookup via Kick public API.

OAuth server: https://id.kick.com
API server:   https://api.kick.com/public/v1
"""

import base64
import hashlib
import os
import urllib.parse

import aiohttp

from config.settings import settings

_AUTH_URL  = "https://id.kick.com/oauth/authorize"
_TOKEN_URL = "https://id.kick.com/oauth/token"
_USER_URL  = "https://api.kick.com/public/v1/users/me"

_SCOPES = "user:read channel:read events:subscribe"


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(state: str) -> tuple[str, str]:
    """Return (authorization_url, code_verifier).

    The caller must store code_verifier in the session so it can be passed
    to exchange_code() in the callback.
    """
    verifier, challenge = _pkce_pair()
    params = {
        "client_id":             settings.kick_client_id,
        "redirect_uri":          settings.kick_redirect_uri,
        "response_type":         "code",
        "scope":                 _SCOPES,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params), verifier


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange an authorization code + PKCE verifier for tokens.

    Returns {access_token, refresh_token, expires_in, token_type, scope}.
    """
    data = {
        "client_id":     settings.kick_client_id,
        "client_secret": settings.kick_client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  settings.kick_redirect_uri,
        "code_verifier": code_verifier,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh token set."""
    data = {
        "client_id":     settings.kick_client_id,
        "client_secret": settings.kick_client_secret,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_user(access_token: str) -> dict:
    """Fetch the authenticated Kick user via the public API.

    Returns {"id": str, "username": str, "avatar_url": str, "slug": str}.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(_USER_URL, headers=headers) as resp:
            resp.raise_for_status()
            payload = await resp.json()

    # Public API wraps data in {"data": [...]}
    if isinstance(payload, dict) and "data" in payload:
        users = payload["data"]
        u = users[0] if users else {}
    elif isinstance(payload, list):
        u = payload[0] if payload else {}
    else:
        u = payload

    if not u:
        raise ValueError("Kick user endpoint returned no data")

    return {
        "id":         str(u.get("user_id", u.get("id", ""))),
        "username":   u.get("username", ""),
        "avatar_url": u.get("profile_pic", "") or u.get("avatar_url", ""),
        "slug":       u.get("slug", "") or u.get("username", ""),
    }
