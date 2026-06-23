"""Kick OAuth 2.1 helpers — PKCE authorization, code exchange, token refresh,
and authenticated user lookup via Kick public API.

OAuth server: https://id.kick.com
API server:   https://api.kick.com/public/v1
"""

import base64
import hashlib
import json
import os
import urllib.parse

import aiohttp
import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_AUTH_URL  = "https://id.kick.com/oauth/authorize"
_TOKEN_URL = "https://id.kick.com/oauth/token"

# Only scopes that actually exist in Kick's OAuth server may be requested —
# id.kick.com/oauth/authorize rejects the WHOLE request with invalid_scope if any
# unknown scope is present. Kick's published scope list (KickEngineering/KickDevDocs)
# is: user:read, channel:read, channel:write, channel:rewards:read/write, chat:write,
# streamkey:read, events:subscribe, moderation:*, kicks:read. There is NO clips scope
# (Kick has no public clip-creation API), so requesting clips:write here previously
# broke account linking entirely. Keep only the scopes we use.
_SCOPES = "user:read channel:read channel:write events:subscribe"


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


_INTROSPECT_URL = "https://id.kick.com/oauth/introspect"

# Kick's /public/v1 does not have a /users/me endpoint yet.
# /public/v1/channels (no params, with auth) may return the current user's channel.
_CHANNEL_ENDPOINTS = [
    "https://api.kick.com/public/v1/channels",
    "https://api.kick.com/public/v1/channels/me",
]

# Mimic a browser to reduce Cloudflare WAF rejection on kick.com endpoints
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _unwrap_first(payload: dict | list) -> dict:
    """Extract the first item from any Kick API envelope shape."""
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _parse_user(payload: dict | list) -> dict:
    u = _unwrap_first(payload)
    return {
        "id":         str(u.get("user_id", u.get("id", ""))),
        "username":   u.get("username", ""),
        "avatar_url": u.get("profile_pic", "") or u.get("avatar_url", ""),
        "slug":       u.get("slug", "") or u.get("username", ""),
    }


def _parse_channel_user(payload: dict | list) -> dict:
    ch = _unwrap_first(payload)
    u   = ch.get("user", ch)
    uid  = str(ch.get("broadcaster_user_id", u.get("user_id", u.get("id", ""))))
    name = ch.get("broadcaster_user_login", u.get("username", u.get("slug", "")))
    return {
        "id":         uid,
        "username":   name,
        "avatar_url": u.get("profile_pic", "") or u.get("avatar_url", ""),
        "slug":       ch.get("slug", name),
    }


def _decode_jwt_user(token: str) -> dict | None:
    """Try to extract user info from a JWT access token payload.

    Returns None if the token is not a JWT or doesn't contain usable claims.
    No signature verification — we already trust the token came from Kick.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padding = 4 - len(parts[1]) % 4
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * padding))
        uid  = str(payload.get("sub", payload.get("user_id", payload.get("id", ""))))
        name = payload.get("username", payload.get("preferred_username", payload.get("name", "")))
        return {
            "id":         uid,
            "username":   name,
            "avatar_url": payload.get("avatar_url", payload.get("picture", "")),
            "slug":       payload.get("slug", name),
        }
    except Exception:
        return None


async def get_user(access_token: str) -> dict:
    """Fetch the authenticated Kick user.

    Strategy (in order):
    1. Token introspection (RFC 7662) — works without a /me endpoint
    2. GET /public/v1/channels — channel:read scope may return own channel
    3. JWT decode fallback (only if Kick ever issues JWTs)

    Returns {"id": str, "username": str, "avatar_url": str, "slug": str}.
    """
    auth_headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json",
    }
    errors = []

    async with aiohttp.ClientSession() as session:
        # 1. Token introspection
        try:
            intr_data = {
                "token":         access_token,
                "client_id":     settings.kick_client_id,
                "client_secret": settings.kick_client_secret,
            }
            async with session.post(
                _INTROSPECT_URL,
                data=intr_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                body = await resp.text()
                log.info("kick_introspect", status=resp.status, body=body[:300])
                if resp.status == 200:
                    payload = json.loads(body)
                    if payload.get("active"):
                        uid  = str(payload.get("sub", payload.get("user_id", "")))
                        name = payload.get("username", payload.get("preferred_username", ""))
                        if uid:
                            return {"id": uid, "username": name, "avatar_url": "", "slug": name}
                        errors.append(f"introspect → active but no sub/user_id: {body[:120]}")
                    else:
                        errors.append(f"introspect → inactive/error: {body[:120]}")
                else:
                    errors.append(f"introspect → {resp.status}: {body[:120]}")
        except Exception as exc:
            log.info("kick_introspect_exc", error=str(exc))
            errors.append(f"introspect → {exc}")

        # 2. Channel endpoints (channel:read scope)
        for url in _CHANNEL_ENDPOINTS:
            try:
                async with session.get(url, headers=auth_headers) as resp:
                    body = await resp.text()
                    log.info("kick_channel_attempt", url=url, status=resp.status, body=body[:300])
                    if resp.status == 200:
                        payload = json.loads(body)
                        user = _parse_channel_user(payload)
                        if user["id"]:
                            return user
                        errors.append(f"{url} → 200 no id: {body[:80]}")
                    else:
                        errors.append(f"{url} → {resp.status}")
            except Exception as exc:
                log.info("kick_channel_exc", url=url, error=str(exc))
                errors.append(f"{url} → {exc}")

    # 3. JWT decode (Kick tokens are currently opaque, but kept as safety net)
    user = _decode_jwt_user(access_token)
    if user and user["id"]:
        return user

    short = " | ".join(errors)
    raise ValueError(f"All Kick user endpoints failed: {short}")
