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

# clips:edit       → create clips on the user's behalf
# user:read:email  → the account's email, returned by Helix Get Users
#
# THIS ONLY WORKS GOING FORWARD, and that is a property of OAuth rather than of
# this code. A token's scopes are fixed at the moment it is issued, and
# refreshing returns the same set — so every token already stored was minted
# under `clips:edit` alone and will never return an email, however many times we
# ask. Existing users hand theirs over the next time they sign in and approve
# the new consent screen, and not before. scripts/backfill_emails.py reports
# exactly who that leaves.
#
# It also changes what the consent screen says at the moment somebody decides
# whether to connect — from "create clips" to "create clips and see your email
# address". That is a real cost on the highest-stakes step in the funnel, paid
# for knowing how to reach the people who sign up and never subscribe.
_SCOPES = "clips:edit user:read:email"


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
        # Present only when the token carries user:read:email. Absent — not
        # empty — on every token minted before that scope was requested, which
        # is why this reads with a default instead of indexing. An empty string
        # here must never overwrite an email we already learned from Stripe.
        "email":        (u.get("email") or "").strip().lower(),
    }


_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


async def token_scopes(access_token: str) -> list[str] | None:
    """What this token is ACTUALLY allowed to do, straight from Twitch.

    Exists so the email backfill can report the truth instead of assuming it.
    Scopes are fixed when a token is issued and a refresh returns the same set,
    so a token minted before user:read:email was requested will never return an
    email — but that is a claim about OAuth, and the honest way to make it about
    a particular user is to ask.

    None means we could not ask (no token, Twitch unreachable, token already
    dead). The caller must not read None as "no scopes", which would report a
    working account as unreachable.
    """
    if not access_token:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    _VALIDATE_URL,
                    headers={"Authorization": f"OAuth {access_token}"}) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
        scopes = payload.get("scopes")
        return list(scopes) if isinstance(scopes, list) else []
    except Exception:
        return None
