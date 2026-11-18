"""X: OAuth 2.0 with PKCE, the v2 chunked media upload, then the post.

Owner, 2026-11-18: "lets get X active now too". Unlike the other three there
is no app review to wait for: a post goes out public the moment the keys and
the credit are in place. What X has instead is a BILL.

THE COST, AND WHAT GUARDS IT. Since 2026-02 the X API is pay-per-use: credit
bought up front in the developer console, about $0.015 for a post and about
$0.20 for a post whose text has a link in it (third-party write-ups of X's
price list, 2026-11; X's own pages could not be opened from the dev session).
Every other cost this product has is one fixed server, and Autopilot posts
every clip an account approves, so two things here keep one account from
running the bill up:

  * `X_DAILY_POSTS_PER_USER` (default 10): past it a post waits, as a
    retryable failure, and goes out by itself when the 24 hours roll on.
    The count rides on the connection (`extra["posts"]`), so tidying the
    queue does not reset it.
  * links are left out of the text unless `X_ALLOW_LINKS=true`, and the card
    says so. One link is the price of thirteen posts.

Out of credit is HTTP 402 from X. That is ours to fix, never the user's: the
card says so, it stays retryable, and it is logged as an ERROR so the alert
email reaches the owner (src/alerts).

THE CALLS (docs.x.com: OAuth 2.0 authorization code with PKCE; Media, chunked
upload; Posts). Written from those pages as search reported them and NOT yet
run against X: the first real post is the proof, and every refusal X makes is
shown on the card in X's own words so it can be read.

  authorize  https://x.com/i/oauth2/authorize  (S256 challenge, always)
  token      POST api.x.com/2/oauth2/token, client id + secret as HTTP Basic
  who        GET  api.x.com/2/users/me
  upload     POST /2/media/upload/initialize   {media_type,total_bytes,media_category}
             POST /2/media/upload/{id}/append   multipart: media, segment_index
             POST /2/media/upload/{id}/finalize
             GET  /2/media/upload?command=STATUS&media_id={id}   until succeeded
  post       POST /2/tweets  {text, media: {media_ids: [id]}}

REFRESH TOKENS ARE SINGLE USE. Each refresh returns a new one and kills the
old, so two refreshes at once strand the connection; providers.ensure_fresh
takes a lock per connection for that reason. The access token lasts two hours.
"""

from __future__ import annotations

import base64
import re
import time
import urllib.parse
import uuid

import structlog

from config.settings import settings

from . import ProviderError, PostResult, content_type, redirect_uri
from . import _http

log = structlog.get_logger(__name__)

_AUTH = "https://x.com/i/oauth2/authorize"
_TOKEN = "https://api.x.com/2/oauth2/token"
_REVOKE = "https://api.x.com/2/oauth2/revoke"
_ME = "https://api.x.com/2/users/me"
_MEDIA = "https://api.x.com/2/media/upload"
_POSTS = "https://api.x.com/2/tweets"
# users.read + tweet.read are what /2/users/me needs; offline.access is what
# makes X hand back a refresh token at all.
_SCOPES = "tweet.read tweet.write users.read media.write offline.access"

MB = 1024 * 1024
# X asks for segments of 5 MB or less (it refuses above 8).
CHUNK = 4 * MB
TEXT_MAX = 280
URL_WEIGHT = 23                    # every link counts as 23, whatever its length
STATUS_TRIES = 40                  # with X's own check_after_secs, a few minutes
STATUS_WAIT_MAX_S = 10.0
DAY_S = 24 * 3600

# A link, as X would turn one into a link: with a scheme, or a bare domain
# on an ending people actually type. Approximate on purpose: missing an odd
# one costs twenty cents, and matching "gg.wp" would eat a word.
_LINK = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|tv|gg|net|org|io|app|co|me|ly|be|live|xyz|link|bio)\b(?:/\S*)?",
    re.IGNORECASE)


def weighted_len(text: str) -> int:
    """Length the way X counts it (twitter-text v3): most Latin text is one
    each, everything else (emoji, CJK) is two, and a link is 23."""
    n, last = 0, 0
    for m in _LINK.finditer(text):
        n += _plain_len(text[last:m.start()]) + URL_WEIGHT
        last = m.end()
    return n + _plain_len(text[last:])


def _plain_len(s: str) -> int:
    n = 0
    for ch in s:
        c = ord(ch)
        n += 1 if (c <= 4351 or 8192 <= c <= 8205 or 8208 <= c <= 8223 or 8242 <= c <= 8247) else 2
    return n


def post_text(caption: str) -> tuple[str, bool]:
    """(the text X gets, whether a link was left out).

    One caption is written for every platform and may run to 2,200
    characters; X takes 280, counted its own way, and refuses the whole post
    over that. So it is cut at a word, with an ellipsis, rather than failed.
    """
    text = (caption or "").strip()
    dropped = False
    if not settings.x_allow_links and _LINK.search(text):
        text = _LINK.sub("", text)
        dropped = True
    # Tidy what cutting left behind: doubled spaces, a line that is now empty.
    text = "\n".join(ln for ln in (re.sub(r"[ \t]{2,}", " ", l).strip() for l in text.splitlines()) if ln)
    if weighted_len(text) <= TEXT_MAX:
        return text, dropped
    cut = text
    while cut and weighted_len(cut) > TEXT_MAX - 2:           # the ellipsis counts two
        cut = cut[:-1]
    space = cut.rfind(" ")
    if space > len(cut) * 0.6:                                # do not lose a third of it to a word
        cut = cut[:space]
    return cut.rstrip(" ,;:-\n") + "…", dropped


def _recent(conn, now: float | None = None) -> list[float]:
    now = time.time() if now is None else now
    return [t for t in ((conn.extra or {}).get("posts") or []) if isinstance(t, (int, float)) and now - t < DAY_S]


def _multipart(fields: dict, chunk: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body with the text fields and one binary part
    called `media`. Built here because _http.request takes bytes, and a
    4 MB chunk is small enough to hold."""
    b = "hz" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append((f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode())
    out.append((f'--{b}\r\nContent-Disposition: form-data; name="media"; filename="chunk"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode())
    out.append(chunk)
    out.append(f"\r\n--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


class X:
    id = "x"
    label = "X"
    uses_pkce = True
    refresh_margin_s = 300
    refreshes_without_refresh_token = False

    def configured(self) -> bool:
        return bool(settings.x_client_id and settings.x_client_secret)

    @staticmethod
    def _basic() -> dict:
        raw = f"{settings.x_client_id}:{settings.x_client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(),
                "Content-Type": "application/x-www-form-urlencoded"}

    def authorization_url(self, state: str, code_challenge: str | None = None) -> str:
        q = {"response_type": "code", "client_id": settings.x_client_id,
             "redirect_uri": redirect_uri(self.id), "scope": _SCOPES, "state": state,
             "code_challenge": code_challenge or "", "code_challenge_method": "S256"}
        # %20 between the scopes, not "+": that is how X's own examples
        # write the space-separated list.
        return _AUTH + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)

    async def exchange(self, code: str, code_verifier: str | None = None) -> dict:
        r = await _http.request("POST", _TOKEN, headers=self._basic(), data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri(self.id), "client_id": settings.x_client_id,
            "code_verifier": code_verifier or ""})
        tok = r.json() or {}
        if not r.ok or not tok.get("access_token"):
            raise ProviderError("X did not accept the sign-in: " + _why(r))
        who = await self._me(tok["access_token"])
        return {"account_id": who["id"], "account_name": "@" + who["username"],
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", ""),
                "expires_at": time.time() + float(tok.get("expires_in") or 7200),
                "scopes": tok.get("scope", ""), "extra": {"username": who["username"]}}

    async def refresh(self, conn) -> dict:
        r = await _http.request("POST", _TOKEN, headers=self._basic(), data={
            "grant_type": "refresh_token", "refresh_token": conn.refresh_token,
            "client_id": settings.x_client_id})
        tok = r.json() or {}
        if not r.ok or not tok.get("access_token"):
            if r.status in (400, 401):
                raise ProviderError("Your X connection has expired. Connect it again.", reauth=True)
            raise ProviderError("X did not refresh the login: " + _why(r), retryable=True)
        # The refresh token is REPLACED each time; ensure_fresh stores it.
        return {"access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", ""),
                "expires_at": time.time() + float(tok.get("expires_in") or 7200)}

    async def revoke(self, conn) -> None:
        try:
            await _http.request("POST", _REVOKE, headers=self._basic(), data={
                "token": conn.access_token, "token_type_hint": "access_token",
                "client_id": settings.x_client_id})
        except Exception:
            pass

    async def _me(self, access_token: str) -> dict:
        r = await _http.request("GET", _ME, headers={"Authorization": "Bearer " + access_token})
        d = (r.json() or {}).get("data") or {}
        if not r.ok or not d.get("id"):
            if r.status == 402:
                _out_of_credit("connect")
                raise ProviderError("X posting is not available right now. Try again later.")
            raise ProviderError("X did not say which account this is: " + _why(r))
        return {"id": str(d["id"]), "username": str(d.get("username") or d.get("name") or "x")}

    async def post(self, conn, path, size: int, caption: str, fmt: str,
                   duration_s: float = 0.0) -> PostResult:
        from src.publish import connections
        cap = max(0, int(settings.x_daily_posts_per_user or 0))
        if cap and len(_recent(conn)) >= cap:
            raise ProviderError(
                f"Today's {cap} posts to X from Highlightz are used. This one goes out "
                "by itself when the next slot opens.", retryable=True)
        text, dropped = post_text(caption)
        auth = {"Authorization": "Bearer " + conn.access_token}

        r = await _http.request("POST", _MEDIA + "/initialize", headers=auth, json={
            "media_type": content_type(fmt), "total_bytes": int(size),
            "media_category": "tweet_video"})
        if not r.ok:
            self._raise(r, "start the upload")
        d = (r.json() or {}).get("data") or {}
        media_id = str(d.get("id") or d.get("media_id") or d.get("media_id_string") or "")
        if not media_id:
            raise ProviderError("X did not open an upload session.", retryable=True)

        index = 0
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                body, ctype = _multipart({"segment_index": index}, chunk)
                r = await _http.request("POST", f"{_MEDIA}/{media_id}/append",
                                        headers={**auth, "Content-Type": ctype},
                                        content=body, timeout=_http.UPLOAD_TIMEOUT)
                if not r.ok:
                    self._raise(r, "upload the video")
                index += 1

        r = await _http.request("POST", f"{_MEDIA}/{media_id}/finalize", headers=auth)
        if not r.ok:
            self._raise(r, "finish the upload")
        info = ((r.json() or {}).get("data") or {}).get("processing_info")
        # A video is processed after the bytes land, and X refuses a post
        # whose media is not ready. Ask at least once even when finalize
        # says nothing: "nothing" has meant both "ready" and "not started".
        tries = 0
        while True:
            state = (info or {}).get("state", "")
            if state == "succeeded":
                break
            if state == "failed":
                err = (info or {}).get("error") or {}
                raise ProviderError("X could not process the video: "
                                    + str(err.get("message") or err.get("name") or "no reason given"))
            if info is None and tries:
                break                                   # asked, and X has no processing to report
            tries += 1
            if tries > STATUS_TRIES:
                raise ProviderError("X is still processing the video. It will be tried again.",
                                    retryable=True)
            await _http.sleep(min(STATUS_WAIT_MAX_S, float((info or {}).get("check_after_secs") or 2)))
            r = await _http.request("GET", _MEDIA, headers=auth,
                                    params={"command": "STATUS", "media_id": media_id})
            if not r.ok:
                self._raise(r, "check the upload")
            info = ((r.json() or {}).get("data") or {}).get("processing_info")

        body = {"media": {"media_ids": [media_id]}}
        if text:
            body["text"] = text
        r = await _http.request("POST", _POSTS, headers=auth, json=body)
        if not r.ok:
            self._raise(r, "publish the post")
        post_id = str(((r.json() or {}).get("data") or {}).get("id") or "")
        if not post_id:
            raise ProviderError("X accepted the post but returned no id.", retryable=True)

        # Counted on the connection, after the post exists: this is the bill.
        conn.extra = {**(conn.extra or {}), "posts": _recent(conn) + [time.time()]}
        connections.save(conn)
        username = (conn.extra or {}).get("username") or "i"
        note = ("Links were left out of the X post." if dropped else "")
        return PostResult(url=f"https://x.com/{username}/status/{post_id}",
                          remote_id=post_id, note=note)

    @staticmethod
    def _raise(r, doing: str) -> None:
        if r.status == 401:
            raise ProviderError("Your X connection has expired. Connect it again.", reauth=True)
        if r.status == 402:
            _out_of_credit(doing)
            raise ProviderError("Highlightz's posting credit with X has run out. That is ours "
                                "to fix, not yours; the post will be retried.", retryable=True)
        if r.status == 429:
            raise ProviderError("X is limiting how fast this account can post. It will be "
                                "tried again shortly.", retryable=True)
        if r.status >= 500:
            raise ProviderError(f"X is having trouble ({r.status}). Will retry.", retryable=True)
        raise ProviderError(f"X refused to {doing}: " + _why(r))


def _out_of_credit(doing: str) -> None:
    # An ERROR on purpose: it is what puts this in the owner's alert email.
    log.error("x_credits_depleted", doing=doing,
              fix="buy X API credit in the developer console (console.x.com, Billing)")


def _why(r) -> str:
    """X's own words for a refusal, from whichever of its error shapes this is."""
    d = r.json() or {}
    errs = d.get("errors") or []
    first = errs[0] if errs and isinstance(errs[0], dict) else {}
    msg = (d.get("detail") or first.get("detail") or first.get("message")
           or d.get("error_description") or d.get("title") or d.get("error") or "")
    return (str(msg)[:240] or f"HTTP {r.status}")


PROVIDER = X()
