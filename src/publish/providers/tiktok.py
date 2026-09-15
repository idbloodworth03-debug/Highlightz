"""TikTok: Login Kit OAuth + the Content Posting API, direct post.

The cost the owner accepted: until TikTok audits the app, every post it
makes is forced to SELF_ONLY — the video lands on the user's profile as
private, and they flip it public in the app. creator_info tells us which
privacy levels the account is allowed, so the post carries the most public
one on offer and the card says plainly when that was "private". Once the
audit passes nothing here changes; PUBLIC_TO_EVERYONE simply starts
appearing in the options.

Upload is chunked by TikTok's rules: one chunk for anything up to 64 MB,
otherwise 32 MB chunks with the remainder folded into the last one, each
sent with a Content-Range. A ≤64 MB render is the common case (a 30 s clip
at 1080×1920).
"""

from __future__ import annotations

import time
import urllib.parse

from config.settings import settings

from . import ProviderError, PostResult, content_type, redirect_uri
from . import _http

_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
_REVOKE = "https://open.tiktokapis.com/v2/oauth/revoke/"
_USER = "https://open.tiktokapis.com/v2/user/info/"
_CREATOR = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
_SCOPES = "user.info.basic,video.publish"

MB = 1024 * 1024
SINGLE_CHUNK_MAX = 64 * MB
CHUNK = 32 * MB
TITLE_MAX = 2200
# How long to wait for TikTok to finish processing after the bytes land.
STATUS_POLL_S = 5.0
STATUS_TRIES = 36                  # 3 minutes

_PRIVACY_ORDER = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR",
                  "SELF_ONLY")


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's rules."""
    if size <= SINGLE_CHUNK_MAX:
        return size, 1
    return CHUNK, size // CHUNK


class TikTok:
    id = "tiktok"
    label = "TikTok"
    uses_pkce = True
    refresh_margin_s = 300
    refreshes_without_refresh_token = False

    def configured(self) -> bool:
        return bool(settings.tiktok_client_key and settings.tiktok_client_secret)

    def authorization_url(self, state: str, code_challenge: str | None = None) -> str:
        q = {"client_key": settings.tiktok_client_key, "scope": _SCOPES,
             "response_type": "code", "redirect_uri": redirect_uri(self.id),
             "state": state}
        if code_challenge:
            q["code_challenge"] = code_challenge
            q["code_challenge_method"] = "S256"
        return _AUTH + "?" + urllib.parse.urlencode(q)

    async def exchange(self, code: str, code_verifier: str | None = None) -> dict:
        data = {"client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code, "grant_type": "authorization_code",
                "redirect_uri": redirect_uri(self.id)}
        if code_verifier:
            data["code_verifier"] = code_verifier
        r = await _http.request("POST", _TOKEN, data=data,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        tok = r.json()
        if not r.ok or not tok.get("access_token"):
            raise ProviderError("TikTok did not accept the sign-in: "
                                + str(tok.get("error_description") or tok.get("error") or r.status))
        who = await self._user(tok["access_token"])
        return {"account_id": tok.get("open_id") or who.get("open_id", ""),
                "account_name": who.get("display_name") or "TikTok",
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", ""),
                "expires_at": time.time() + float(tok.get("expires_in") or 86400),
                "scopes": tok.get("scope", ""),
                "extra": {"username": who.get("username", "")}}

    async def refresh(self, conn) -> dict:
        r = await _http.request("POST", _TOKEN, data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "grant_type": "refresh_token", "refresh_token": conn.refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        tok = r.json()
        if not r.ok or not tok.get("access_token"):
            raise ProviderError("Your TikTok connection has expired. Connect it again.",
                                reauth=True)
        return {"access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token") or conn.refresh_token,
                "expires_at": time.time() + float(tok.get("expires_in") or 86400)}

    async def revoke(self, conn) -> None:
        try:
            await _http.request("POST", _REVOKE, data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "token": conn.access_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception:
            pass

    async def _user(self, access_token: str) -> dict:
        r = await _http.request("GET", _USER,
                                params={"fields": "open_id,display_name,username"},
                                headers={"Authorization": "Bearer " + access_token})
        return ((r.json() or {}).get("data") or {}).get("user") or {}

    async def post(self, conn, path, size: int, caption: str, fmt: str,
                   duration_s: float = 0.0) -> PostResult:
        auth = {"Authorization": "Bearer " + conn.access_token,
                "Content-Type": "application/json; charset=UTF-8"}
        r = await _http.request("POST", _CREATOR, headers=auth, json={})
        info = (r.json() or {})
        self._check(r, info, "TikTok would not say what this account may post")
        d = info.get("data") or {}
        options = d.get("privacy_level_options") or []
        privacy = next((p for p in _PRIVACY_ORDER if p in options), options[0] if options else "SELF_ONLY")
        max_s = float(d.get("max_video_post_duration_sec") or 0)
        if max_s and duration_s and duration_s > max_s:
            raise ProviderError(f"TikTok limits this account to {max_s:.0f}s videos; "
                                f"this one is {duration_s:.0f}s.")

        chunk, count = chunk_plan(size)
        r = await _http.request("POST", _INIT, headers=auth, json={
            "post_info": {"title": (caption or "")[:TITLE_MAX], "privacy_level": privacy,
                          "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False, "video_cover_timestamp_ms": 1000},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": chunk, "total_chunk_count": count}})
        init = r.json() or {}
        self._check(r, init, "TikTok would not open an upload")
        publish_id = (init.get("data") or {}).get("publish_id")
        upload_url = (init.get("data") or {}).get("upload_url")
        if not publish_id or not upload_url:
            raise ProviderError("TikTok opened no upload session.", retryable=True)

        ctype = content_type(fmt)
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else (i + 1) * chunk - 1
            r = await _http.request(
                "PUT", upload_url,
                headers={"Content-Type": ctype,
                         "Content-Length": str(end - start + 1),
                         "Content-Range": f"bytes {start}-{end}/{size}"},
                content=_http.file_chunks(path, start, end), timeout=_http.UPLOAD_TIMEOUT)
            if r.status not in (200, 201, 206):
                raise ProviderError(f"TikTok rejected the video bytes (HTTP {r.status}).",
                                    retryable=r.status >= 500)

        post_id = ""
        for _ in range(STATUS_TRIES):
            r = await _http.request("POST", _STATUS, headers=auth,
                                    json={"publish_id": publish_id})
            st = ((r.json() or {}).get("data") or {})
            status = st.get("status") or ""
            if status == "PUBLISH_COMPLETE":
                ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
                post_id = str(ids[0]) if ids else ""
                break
            if status == "FAILED":
                raise ProviderError("TikTok could not publish it: "
                                    + str(st.get("fail_reason") or "unknown reason"))
            await _http.sleep(STATUS_POLL_S)
        else:
            raise ProviderError("TikTok is still processing the video. Check your "
                                "TikTok inbox; it usually appears within a few minutes.",
                                retryable=False)

        username = (conn.extra or {}).get("username") or ""
        url = f"https://www.tiktok.com/@{username}/video/{post_id}" if username and post_id else \
              (f"https://www.tiktok.com/@{username}" if username else "")
        note = ""
        if privacy != "PUBLIC_TO_EVERYONE":
            note = ("Posted as private (visible only to you): TikTok only allows "
                    "public posts once the Highlightz app passes its audit. Open it "
                    "in TikTok and set it public.")
        return PostResult(url=url, remote_id=post_id or publish_id, note=note)

    @staticmethod
    def _check(r, payload: dict, what: str) -> None:
        err = payload.get("error") or {}
        code = str(err.get("code") or "")
        if r.ok and code in ("", "ok"):
            return
        if r.status == 401 or code in ("access_token_invalid", "token_expired",
                                       "scope_not_authorized"):
            raise ProviderError("Your TikTok connection has expired. Connect it again.",
                                reauth=True)
        if code in ("rate_limit_exceeded", "spam_risk_too_many_posts",
                    "spam_risk_too_many_pending_share") or r.status >= 500:
            raise ProviderError(f"{what}: {err.get('message') or code}. Will retry.",
                                retryable=True)
        raise ProviderError(f"{what}: {err.get('message') or code or r.status}")


PROVIDER = TikTok()
