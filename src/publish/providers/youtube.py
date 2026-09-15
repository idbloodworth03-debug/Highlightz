"""YouTube: Google OAuth 2.0 + the Data API v3 resumable upload.

The cost the owner accepted: the default quota is 10,000 units a day and
`videos.insert` costs 1,600, so the WHOLE app gets six uploads a day until
Google grants an increase (Cloud console → YouTube Data API → Quotas →
request). A quota refusal surfaces on the card as exactly that, retryable,
rather than as the user's mistake. Until the OAuth consent screen is verified
Google also shows an "unverified app" interstitial and caps test users at
100; the Connect flow still works through it.

Two scopes: youtube.upload to post, youtube.readonly to learn which channel
was connected so the card can say "Connected as <channel>". access_type=
offline + prompt=consent is what makes Google return a refresh token; without
both it only does so on the very first consent and a reconnect gets none.
"""

from __future__ import annotations

import time
import urllib.parse

from config.settings import settings

from . import ProviderError, PostResult, caption_title, content_type, redirect_uri
from . import _http

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"
_REVOKE = "https://oauth2.googleapis.com/revoke"
_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
_SCOPES = ("https://www.googleapis.com/auth/youtube.upload "
           "https://www.googleapis.com/auth/youtube.readonly")

TITLE_MAX = 100
DESCRIPTION_MAX = 5000


class YouTube:
    id = "youtube"
    label = "YouTube"
    uses_pkce = False
    refresh_margin_s = 120
    refreshes_without_refresh_token = False

    def configured(self) -> bool:
        return bool(settings.google_client_id and settings.google_client_secret)

    def authorization_url(self, state: str, code_challenge: str | None = None) -> str:
        q = {"client_id": settings.google_client_id,
             "redirect_uri": redirect_uri(self.id),
             "response_type": "code", "scope": _SCOPES, "state": state,
             "access_type": "offline", "prompt": "consent",
             "include_granted_scopes": "true"}
        return _AUTH + "?" + urllib.parse.urlencode(q)

    async def exchange(self, code: str, code_verifier: str | None = None) -> dict:
        r = await _http.request("POST", _TOKEN, data={
            "code": code, "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri(self.id), "grant_type": "authorization_code"})
        tok = r.json()
        if not r.ok or not tok.get("access_token"):
            raise ProviderError("Google did not accept the sign-in: "
                                + str(tok.get("error_description") or tok.get("error") or r.status))
        me = await self._channel(tok["access_token"])
        return {"account_id": me["id"], "account_name": me["title"],
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", ""),
                "expires_at": time.time() + float(tok.get("expires_in") or 3600),
                "scopes": tok.get("scope", ""), "extra": {}}

    async def refresh(self, conn) -> dict:
        r = await _http.request("POST", _TOKEN, data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": conn.refresh_token, "grant_type": "refresh_token"})
        tok = r.json()
        if not r.ok or not tok.get("access_token"):
            err = str(tok.get("error") or r.status)
            # invalid_grant is Google for "the user revoked this" — only a
            # new consent fixes it.
            raise ProviderError("Your YouTube connection was revoked. Connect it again.",
                                reauth=(err == "invalid_grant"), retryable=(err != "invalid_grant"))
        return {"access_token": tok["access_token"],
                "expires_at": time.time() + float(tok.get("expires_in") or 3600)}

    async def revoke(self, conn) -> None:
        try:
            await _http.request("POST", _REVOKE,
                                params={"token": conn.refresh_token or conn.access_token})
        except Exception:
            pass

    async def _channel(self, access_token: str) -> dict:
        r = await _http.request("GET", _CHANNELS,
                                params={"part": "snippet", "mine": "true"},
                                headers={"Authorization": "Bearer " + access_token})
        items = (r.json() or {}).get("items") or []
        if not r.ok or not items:
            raise ProviderError("That Google account has no YouTube channel. "
                                "Create one on YouTube first, then connect again.")
        return {"id": items[0]["id"],
                "title": (items[0].get("snippet") or {}).get("title") or "YouTube"}

    async def post(self, conn, path, size: int, caption: str, fmt: str,
                   duration_s: float = 0.0) -> PostResult:
        auth = {"Authorization": "Bearer " + conn.access_token}
        body = {"snippet": {"title": caption_title(caption, path.stem, TITLE_MAX),
                            "description": (caption or "")[:DESCRIPTION_MAX],
                            "categoryId": "20"},          # Gaming
                "status": {"privacyStatus": "public",
                           "selfDeclaredMadeForKids": False}}
        ctype = content_type(fmt)
        r = await _http.request(
            "POST", _UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
            headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": ctype},
            json=body)
        if not r.ok:
            self._raise(r)
        location = r.headers.get("location") or r.headers.get("Location")
        if not location:
            raise ProviderError("YouTube did not open an upload session.", retryable=True)
        r = await _http.request(
            "PUT", location,
            headers={**auth, "Content-Type": ctype, "Content-Length": str(size)},
            content=_http.file_chunks(path, 0, size - 1), timeout=_http.UPLOAD_TIMEOUT)
        if not r.ok:
            self._raise(r)
        vid = (r.json() or {}).get("id")
        if not vid:
            raise ProviderError("YouTube accepted the upload but returned no video id.",
                                retryable=True)
        return PostResult(url=f"https://www.youtube.com/watch?v={vid}", remote_id=vid)

    @staticmethod
    def _raise(r) -> None:
        err = ((r.json() or {}).get("error") or {})
        errors = err.get("errors") or [{}]
        reason = str(errors[0].get("reason") or "")
        message = str(err.get("message") or f"HTTP {r.status}")
        if r.status == 401 or reason in ("authError", "invalid_grant"):
            raise ProviderError("Your YouTube connection has expired. Connect it again.",
                                reauth=True)
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise ProviderError("YouTube's daily upload quota for Highlightz is used up. "
                                "It resets at midnight Pacific time; the post will be "
                                "retried then.", retryable=True)
        if reason == "uploadLimitExceeded":
            raise ProviderError("YouTube says this channel has hit its upload limit "
                                "for today.", retryable=True)
        if r.status >= 500:
            raise ProviderError(f"YouTube is having trouble ({r.status}). Will retry.",
                                retryable=True)
        raise ProviderError("YouTube refused the upload: " + message)


PROVIDER = YouTube()
