"""Instagram Reels: "Instagram API with Instagram Login" (no Facebook Page).

The cost the owner accepted: Instagram does not take bytes. It is handed a
URL and fetches the video itself, so the render has to be reachable on the
public internet for a few minutes — that is /media/<signed token>
(src/publish/media_link.py), unforgeable and expiring, one file per link.
The user's account must be a Professional (Business or Creator) account;
a personal one cannot be connected and the error says so. Meta app review
(instagram_business_content_publish) is needed before accounts other than
the app's own testers can connect.

Tokens: the code exchange gives a one-hour token which is immediately
swapped for a 60-day one; that one is refreshed (not re-consented) whenever
it has under a week left, which is what `refreshes_without_refresh_token`
means — Instagram has no separate refresh token, the long-lived token
refreshes itself.
"""

from __future__ import annotations

import time
import urllib.parse

from config.settings import settings

from . import ProviderError, PostResult, redirect_uri
from . import _http

_AUTH = "https://www.instagram.com/oauth/authorize"
_TOKEN = "https://api.instagram.com/oauth/access_token"
_GRAPH = "https://graph.instagram.com"
_V = "v21.0"
_SCOPES = "instagram_business_basic,instagram_business_content_publish"

CAPTION_MAX = 2200
STATUS_POLL_S = 5.0
STATUS_TRIES = 60                 # 5 minutes; Instagram transcodes first


class Instagram:
    id = "instagram"
    label = "Instagram"
    uses_pkce = False
    refresh_margin_s = 7 * 24 * 3600
    refreshes_without_refresh_token = True

    def configured(self) -> bool:
        return bool(settings.instagram_app_id and settings.instagram_app_secret)

    def authorization_url(self, state: str, code_challenge: str | None = None) -> str:
        q = {"client_id": settings.instagram_app_id, "redirect_uri": redirect_uri(self.id),
             "response_type": "code", "scope": _SCOPES, "state": state,
             "enable_fb_login": "0", "force_authentication": "1"}
        return _AUTH + "?" + urllib.parse.urlencode(q)

    async def exchange(self, code: str, code_verifier: str | None = None) -> dict:
        r = await _http.request("POST", _TOKEN, data={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(self.id), "code": code})
        short = r.json() or {}
        if not r.ok or not short.get("access_token"):
            raise ProviderError("Instagram did not accept the sign-in: "
                                + str(short.get("error_message") or short.get("error_type")
                                      or (short.get("error") or {}).get("message") or r.status))
        # Swap the one-hour token for the 60-day one straight away.
        r = await _http.request("GET", f"{_GRAPH}/access_token", params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_app_secret,
            "access_token": short["access_token"]})
        long = r.json() or {}
        token = long.get("access_token") or short["access_token"]
        expires_in = float(long.get("expires_in") or 3600)
        me = await self._me(token)
        return {"account_id": me["user_id"], "account_name": "@" + me["username"],
                "access_token": token, "refresh_token": "",
                "expires_at": time.time() + expires_in, "scopes": _SCOPES,
                "extra": {"ig_user_id": me["user_id"], "username": me["username"]}}

    async def refresh(self, conn) -> dict:
        r = await _http.request("GET", f"{_GRAPH}/refresh_access_token", params={
            "grant_type": "ig_refresh_token", "access_token": conn.access_token})
        tok = r.json() or {}
        if not r.ok or not tok.get("access_token"):
            raise ProviderError("Your Instagram connection has expired. Connect it again.",
                                reauth=True)
        return {"access_token": tok["access_token"],
                "expires_at": time.time() + float(tok.get("expires_in") or 5184000)}

    async def revoke(self, conn) -> None:
        # Instagram Login has no revoke endpoint for long-lived tokens; the
        # user removes the app from Instagram's settings. Forgetting the token
        # on our side is the whole of what we can do.
        return None

    async def _me(self, token: str) -> dict:
        r = await _http.request("GET", f"{_GRAPH}/{_V}/me", params={
            "fields": "user_id,username,account_type", "access_token": token})
        me = r.json() or {}
        if not r.ok or not (me.get("user_id") or me.get("id")):
            raise ProviderError("Instagram would not say which account signed in: "
                                + str((me.get("error") or {}).get("message") or r.status))
        if (me.get("account_type") or "").upper() not in ("", "BUSINESS", "MEDIA_CREATOR", "CREATOR"):
            raise ProviderError("Instagram only lets apps post to a Professional account "
                                "(Business or Creator). Switch the account type in "
                                "Instagram's settings, then connect again.")
        return {"user_id": str(me.get("user_id") or me.get("id")),
                "username": str(me.get("username") or "instagram")}

    async def post(self, conn, path, size: int, caption: str, fmt: str,
                   duration_s: float = 0.0) -> PostResult:
        from src.publish import media_link
        if (fmt or "").lower().lstrip(".") == "webm":
            raise ProviderError("Instagram does not accept WebM. Export this clip as MP4.")
        ig_user = (conn.extra or {}).get("ig_user_id") or conn.account_id
        token = conn.access_token
        # The render's on-disk name IS its upload id (uploads.library.path_for
        # builds `<id>.<ext>` and nothing else), so the stem is what the
        # signed public link needs.
        video_url = media_link.public_url(path.stem)
        r = await _http.request("POST", f"{_GRAPH}/{_V}/{ig_user}/media", data={
            "media_type": "REELS", "video_url": video_url,
            "caption": (caption or "")[:CAPTION_MAX], "share_to_feed": "true",
            "access_token": token})
        made = r.json() or {}
        self._check(r, made, "Instagram would not start the post")
        container = made.get("id")
        if not container:
            raise ProviderError("Instagram opened no media container.", retryable=True)

        for _ in range(STATUS_TRIES):
            r = await _http.request("GET", f"{_GRAPH}/{_V}/{container}", params={
                "fields": "status_code,status", "access_token": token})
            st = r.json() or {}
            code = st.get("status_code") or ""
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise ProviderError("Instagram could not process the video: "
                                    + str(st.get("status") or code))
            await _http.sleep(STATUS_POLL_S)
        else:
            raise ProviderError("Instagram is still processing the video. Try again "
                                "in a few minutes.", retryable=True)

        r = await _http.request("POST", f"{_GRAPH}/{_V}/{ig_user}/media_publish", data={
            "creation_id": container, "access_token": token})
        pub = r.json() or {}
        self._check(r, pub, "Instagram would not publish the post")
        media_id = str(pub.get("id") or "")
        url = ""
        if media_id:
            r = await _http.request("GET", f"{_GRAPH}/{_V}/{media_id}", params={
                "fields": "permalink", "access_token": token})
            url = str((r.json() or {}).get("permalink") or "")
        return PostResult(url=url, remote_id=media_id or container)

    @staticmethod
    def _check(r, payload: dict, what: str) -> None:
        if r.ok and not payload.get("error"):
            return
        err = payload.get("error") or {}
        code = int(err.get("code") or 0)
        msg = str(err.get("error_user_msg") or err.get("message") or r.status)
        if r.status == 401 or code in (190, 102):
            raise ProviderError("Your Instagram connection has expired. Connect it again.",
                                reauth=True)
        if code in (4, 17, 32, 613) or r.status >= 500:
            raise ProviderError(f"{what}: {msg}. Will retry.", retryable=True)
        raise ProviderError(f"{what}: {msg}")


PROVIDER = Instagram()
