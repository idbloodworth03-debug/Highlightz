"""Kick OAuth 2.1 helpers — PKCE authorization, code exchange, token refresh,
and authenticated user lookup via Kick public API.

OAuth server: https://id.kick.com
API server:   https://api.kick.com/public/v1
"""

import base64
import hashlib
import json
import logging
import os
import urllib.parse

import aiohttp

from config.settings import settings

log = logging.getLogger(__name__)

_AUTH_URL  = "https://id.kick.com/oauth/authorize"
_TOKEN_URL = "https://id.kick.com/oauth/token"
_USER_URL  = "https://kick.com/api/v1/user"

_SCOPES = "openid user:read channel:read events:subscribe"


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


_USER_ENDPOINTS = [
    "https://id.kick.com/oauth/userinfo",       # OIDC — needs openid scope
    "https://id.kick.com/userinfo",
    "https://api.kick.com/public/v1/users/me",  # may not exist yet
    "https://api.kick.com/public/v1/user",      # singular variant
    "https://api.kick.com/v1/user",             # without /public prefix
    "https://kick.com/api/v1/user",
    "https://kick.com/api/v2/user",
]

# Mimic a browser to avoid Cloudflare WAF blocking server-to-server requests
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _parse_user(payload: dict | list) -> dict:
    """Normalise a Kick user payload from any endpoint variant."""
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        u = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    elif isinstance(payload, list):
        u = payload[0] if payload else {}
    else:
        u = payload
    return {
        "id":         str(u.get("user_id", u.get("id", ""))),
        "username":   u.get("username", ""),
        "avatar_url": u.get("profile_pic", "") or u.get("avatar_url", ""),
        "slug":       u.get("slug", "") or u.get("username", ""),
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
    """Fetch the authenticated Kick user, trying multiple endpoint variants.

    Returns {"id": str, "username": str, "avatar_url": str, "slug": str}.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json",
    }
    errors = []
    async with aiohttp.ClientSession() as session:
        for url in _USER_ENDPOINTS:
            try:
                async with session.get(url, headers=headers) as resp:
                    body = await resp.text()
                    summary = f"{url} → {resp.status}: {body[:200]}"
                    log.info("kick_userinfo_attempt", url=url, status=resp.status, body=body[:200])
                    if resp.status == 200:
                        payload = json.loads(body)
                        user = _parse_user(payload)
                        if user["id"]:
                            return user
                        errors.append(f"{url} → 200 no id")
                    else:
                        errors.append(f"{url} → {resp.status}")
            except Exception as exc:
                log.info("kick_userinfo_attempt", url=url, error=str(exc))
                errors.append(f"{url} → {exc}")
    # Last resort: decode JWT payload to extract user claims
    user = _decode_jwt_user(access_token)
    if user and user["id"]:
        return user
    short = " | ".join(errors)
    raise ValueError(f"All Kick user endpoints failed: {short}")
