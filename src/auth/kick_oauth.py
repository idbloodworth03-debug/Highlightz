"""Kick OAuth2 helpers — authorization URL, code exchange, token refresh,
and authenticated user lookup via Kick API.

Scopes requested: user:read channel:read channel:write chat:write events:subscribe
"""

import urllib.parse
import aiohttp

from config.settings import settings

_AUTH_URL  = "https://kick.com/oauth2/authorize"
_TOKEN_URL = "https://kick.com/oauth2/token"
_USER_URL  = "https://kick.com/api/v1/user"

_SCOPES = "user:read channel:read channel:write chat:write events:subscribe"


def authorization_url(state: str) -> str:
    params = {
        "client_id":     settings.kick_client_id,
        "redirect_uri":  settings.kick_redirect_uri,
        "response_type": "code",
        "scope":         _SCOPES,
        "state":         state,
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for a token set.

    Returns {access_token, refresh_token, expires_in, token_type, ...}.
    """
    data = {
        "client_id":     settings.kick_client_id,
        "client_secret": settings.kick_client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  settings.kick_redirect_uri,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data) as resp:
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
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_user(access_token: str) -> dict:
    """Fetch the authenticated Kick user.

    Returns {"id": str, "username": str, "avatar_url": str, "slug": str}.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(_USER_URL, headers=headers) as resp:
            resp.raise_for_status()
            u = await resp.json()
    if not u:
        raise ValueError("Kick user endpoint returned no data")
    return {
        "id":         str(u.get("id", "")),
        "username":   u.get("username", ""),
        "avatar_url": u.get("profile_pic", "") or u.get("avatar_url", ""),
        "slug":       u.get("slug", "") or u.get("username", ""),
    }
