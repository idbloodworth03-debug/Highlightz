"""Twitch OAuth2 helpers — authorization URL, code exchange, token refresh,
and authenticated user lookup via Helix.

Login scope is `clips:edit` so the same user token can later create clips on
the user's behalf (clips are hosted by Twitch and attributed to the user).
"""

import urllib.parse
import aiohttp

from config.settings import settings

_AUTH_URL  = "https://id.twitch.tv/oauth2/authorize"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_USERS_URL = "https://api.twitch.tv/helix/users"

# clips:edit  → create clips on the user's behalf
# user:read:email is NOT requested — we don't need it.
_SCOPES = "clips:edit"


def authorization_url(state: str) -> str:
    params = {
        "client_id":     settings.twitch_client_id,
        "redirect_uri":  settings.twitch_redirect_uri,
        "response_type": "code",
        "scope":         _SCOPES,
        "state":         state,
        "force_verify":  "false",
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for a token set.

    Returns {access_token, refresh_token, expires_in, scope, token_type}.
    """
    data = {
        "client_id":     settings.twitch_client_id,
        "client_secret": settings.twitch_client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  settings.twitch_redirect_uri,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            return await resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh token set."""
    data = {
        "client_id":     settings.twitch_client_id,
        "client_secret": settings.twitch_client_secret,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_user(access_token: str) -> dict:
    """Fetch the authenticated Twitch user via Helix Get Users."""
    headers = {
        "Client-Id":     settings.twitch_client_id,
        "Authorization": f"Bearer {access_token}",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(_USERS_URL, headers=headers) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    data = payload.get("data", [])
    if not data:
        raise ValueError("Twitch Get Users returned no data")
    u = data[0]
    return {
        "id":           u["id"],
        "login":        u["login"],
        "username":     u.get("display_name") or u["login"],
        "avatar_url":   u.get("profile_image_url", ""),
    }
