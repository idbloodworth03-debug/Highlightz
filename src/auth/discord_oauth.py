"""Discord OAuth2 helpers — authorization URL, code exchange, user fetch."""

import urllib.parse
import aiohttp
from config.settings import settings

_AUTH_URL  = "https://discord.com/oauth2/authorize"
_TOKEN_URL = "https://discord.com/api/oauth2/token"
_USER_URL  = "https://discord.com/api/users/@me"
_CDN       = "https://cdn.discordapp.com"


def authorization_url(state: str) -> str:
    params = {
        "client_id":     settings.discord_client_id,
        "redirect_uri":  settings.discord_redirect_uri,
        "response_type": "code",
        "scope":         "identify",
        "state":         state,
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code(code: str) -> str:
    """Exchange an authorization code for an access token."""
    data = {
        "client_id":     settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  settings.discord_redirect_uri,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            token_data = await resp.json()
    return token_data["access_token"]


async def get_user(access_token: str) -> dict:
    """Fetch the Discord user object for the given access token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(_USER_URL, headers=headers) as resp:
            resp.raise_for_status()
            user = await resp.json()
    avatar_hash = user.get("avatar")
    if avatar_hash:
        avatar_url = f"{_CDN}/avatars/{user['id']}/{avatar_hash}.png?size=128"
    else:
        default_idx = (int(user["id"]) >> 22) % 6
        avatar_url = f"{_CDN}/embed/avatars/{default_idx}.png"
    return {
        "id":         user["id"],
        "username":   user.get("global_name") or user["username"],
        "avatar_url": avatar_url,
    }
