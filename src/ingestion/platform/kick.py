"""Kick platform — liveness, metadata and the chatroom id for a channel.

Two doors, because Kick keeps them apart (verified against Kick's public API
docs, September 2026):

  * **api.kick.com/public/v1** is the sanctioned one. It needs an APP access
    token (client-credentials grant against id.kick.com, no user involved) and
    answers whether a channel is live, its title, category and viewer count.
    It does NOT return the chatroom id, and it has no clips endpoint at all —
    which is why a Kick clip is a file Highlightz captures itself
    (src/ingestion/clip_recorder.py), never a Kick-hosted clip.
  * **kick.com/api/v2/channels/<slug>** is the site's own endpoint, sitting
    behind Cloudflare. It is the only place the chatroom id lives, and it
    also carries liveness when the public API is not configured. It is asked
    with browser-shaped headers; it may still answer 403 from a datacenter
    IP, which is why the chatroom id is CACHED ON DISK the first time it is
    learned — it never changes for a channel, so one success is enough for
    every session after it, including across restarts.

When the chatroom id cannot be learned the channel still runs: the worker
scores it from audio and viewer count and says so in the log
(`kick_chat_unavailable`). Chat is the best signal, not the only one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import aiohttp
import structlog
from tenacity import (retry, retry_if_not_exception_type, stop_after_attempt,
                      wait_exponential)

from config.settings import settings
from .base import BasePlatform, StreamInfo, ChannelOffline

log = structlog.get_logger(__name__)

KICK_API_BASE = "https://api.kick.com/public/v1"
KICK_TOKEN_URL = "https://id.kick.com/oauth/token"
KICK_SITE_API = "https://kick.com/api/v2/channels/{slug}"

# The site endpoint is meant for a browser; these are the headers a Chromium
# sends it. Nothing here is a secret or a login, it is what makes the request
# look like the page's own fetch rather than a script's.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://kick.com/",
    "sec-ch-ua": '"Not:A-Brand";v="24", "Chromium";v="128"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_CHATROOMS_FILE = Path(settings.local_storage_path) / "kick_chatrooms.json"
_chatrooms: dict[str, str] = {}
_chatrooms_loaded = False


def _load_chatrooms() -> None:
    global _chatrooms_loaded
    if _chatrooms_loaded:
        return
    _chatrooms_loaded = True
    try:
        if _CHATROOMS_FILE.exists():
            _chatrooms.update({str(k): str(v) for k, v in
                               json.loads(_CHATROOMS_FILE.read_text(encoding="utf-8")).items()})
    except (OSError, ValueError, AttributeError):
        log.warning("kick_chatroom_cache_unreadable", path=str(_CHATROOMS_FILE))


def _remember_chatroom(slug: str, chatroom_id: str) -> None:
    _load_chatrooms()
    if not chatroom_id or _chatrooms.get(slug) == chatroom_id:
        return
    _chatrooms[slug] = chatroom_id
    try:
        _CHATROOMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CHATROOMS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_chatrooms, indent=2), encoding="utf-8")
        tmp.replace(_CHATROOMS_FILE)
    except OSError as exc:
        log.warning("kick_chatroom_cache_write_failed", error=str(exc))


def cached_chatroom(slug: str) -> str:
    _load_chatrooms()
    return _chatrooms.get(slug.lower(), "")


def _ascii(s) -> str:
    return (s or "").encode("ascii", errors="ignore").decode()


class KickPlatform(BasePlatform):
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._app_token: str = ""
        self._app_token_exp: float = 0.0

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ── HTTP, in one place so tests can stand in front of it ─────────────
    async def _get_json(self, url: str, *, headers: dict | None = None,
                        params: dict | None = None) -> tuple[int, object]:
        session = await self._ensure_session()
        async with session.get(url, headers=headers, params=params,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body

    async def _post_form(self, url: str, data: dict) -> tuple[int, object]:
        session = await self._ensure_session()
        async with session.post(url, data=data,
                                timeout=aiohttp.ClientTimeout(total=20)) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body

    # ── the public API ───────────────────────────────────────────────────
    def configured(self) -> bool:
        return bool(settings.kick_client_id and settings.kick_client_secret)

    async def _get_app_token(self) -> str:
        """An app token (client credentials). Cached until a minute before it
        expires; a 401 downstream clears it so the next call mints a new one."""
        if self._app_token and time.time() < self._app_token_exp - 60:
            return self._app_token
        status, tok = await self._post_form(KICK_TOKEN_URL, {
            "grant_type": "client_credentials",
            "client_id": settings.kick_client_id,
            "client_secret": settings.kick_client_secret})
        if status != 200 or not isinstance(tok, dict) or not tok.get("access_token"):
            raise RuntimeError(f"Kick app token refused (HTTP {status})")
        self._app_token = str(tok["access_token"])
        self._app_token_exp = time.time() + float(tok.get("expires_in") or 3600)
        return self._app_token

    async def _public_channel(self, slug: str) -> dict | None:
        """The public API's row for a slug, or None when it is not usable
        (not configured, token refused, 5xx). ChannelOffline for a slug it
        does not know."""
        if not self.configured():
            return None
        token = await self._get_app_token()
        status, body = await self._get_json(
            f"{KICK_API_BASE}/channels", params={"slug": slug},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if status == 401:
            self._app_token = ""              # stale token; the retry mints another
            raise RuntimeError("Kick app token rejected")
        if status == 404:
            raise ChannelOffline(f"Channel '{slug}' not found on Kick")
        if status != 200 or not isinstance(body, dict):
            log.warning("kick_public_api_unusable", slug=slug, status=status)
            return None
        rows = body.get("data") or []
        if not rows:
            raise ChannelOffline(f"Channel '{slug}' not found on Kick")
        row = rows[0]
        if isinstance(row, dict) and str(row.get("slug") or slug).lower() != slug:
            # The API can answer with a different channel for a near-miss;
            # do not monitor someone the user did not ask for.
            raise ChannelOffline(f"Channel '{slug}' not found on Kick")
        return row if isinstance(row, dict) else None

    # ── the site endpoint (chatroom id, liveness fallback) ───────────────
    async def _site_channel(self, slug: str) -> dict | None:
        status, body = await self._get_json(KICK_SITE_API.format(slug=slug),
                                            headers=_BROWSER_HEADERS)
        if status == 404:
            raise ChannelOffline(f"Channel '{slug}' not found on Kick")
        if status != 200 or not isinstance(body, dict):
            log.info("kick_site_api_unusable", slug=slug, status=status)
            return None
        chatroom = (body.get("chatroom") or {})
        cid = str(chatroom.get("id") or "")
        if cid:
            _remember_chatroom(slug, cid)
        return body

    # "Not live" is an ANSWER, not a failure — see twitch.py for why it is
    # excluded from the retry.
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10),
           retry=retry_if_not_exception_type(ChannelOffline))
    async def get_stream_info(self, channel: str) -> StreamInfo:
        slug = channel.lower()
        live = False
        title = game = ""
        viewers = 0
        mature = False
        chatroom_id = cached_chatroom(slug)

        pub = await self._public_channel(slug)
        # The site endpoint is asked on EVERY session start, not only for the
        # chatroom id: it is the only source of `playback_url`, the channel's
        # HLS playlist, which is how the video is pulled (see below). Verified
        # on prod 2026-09-15: kick.com/api/v2/channels answers the droplet
        # with HTTP 200 and a playback_url; streamlink's own Kick plugin, on
        # the same box, found "No playable streams".
        site: dict | None = None
        try:
            site = await self._site_channel(slug)
        except ChannelOffline:
            raise
        except Exception as exc:
            log.info("kick_site_lookup_failed", slug=slug, error=str(exc))
        if pub is not None:
            stream = pub.get("stream") or {}
            live = bool(stream.get("is_live"))
            title = _ascii(pub.get("stream_title"))
            game = _ascii((pub.get("category") or {}).get("name"))
            viewers = int(stream.get("viewer_count") or 0)
            mature = bool(stream.get("is_mature"))
        else:
            # No app credentials (or the public API is down): the site
            # endpoint carries liveness too.
            if site is None:
                raise RuntimeError(f"Kick did not answer for '{slug}'")
            ls = site.get("livestream")
            live = bool(ls)
            if ls:
                title = _ascii(ls.get("session_title"))
                cats = ls.get("categories") or []
                game = _ascii(cats[0].get("name")) if cats and isinstance(cats[0], dict) else ""
                viewers = int(ls.get("viewer_count") or 0)
                mature = bool(ls.get("is_mature"))

        if site is not None and not chatroom_id:
            chatroom_id = str((site.get("chatroom") or {}).get("id") or "")
        if not live:
            raise ChannelOffline(f"Channel '{slug}' is not live on Kick")
        if not chatroom_id:
            log.warning("kick_chat_unavailable", slug=slug,
                        why="chatroom id not learned; scoring on audio and viewers")

        # The HLS playlist itself, handed to streamlink's generic HLS reader
        # (`hls://` forces that plugin), so nothing depends on the Kick plugin
        # or its Cloudflare browser challenge. The page URL is the fallback
        # when the site endpoint could not be read.
        playback = str((site or {}).get("playback_url") or "")
        stream_url = "hls://" + playback if playback.startswith("http") else f"https://kick.com/{slug}"

        return StreamInfo(
            channel=slug,
            platform="kick",
            stream_url=stream_url,
            chat_channel_id=chatroom_id,
            title=title,
            game=game,
            viewer_count=viewers,
            is_mature=mature,
        )

    async def is_live(self, channel: str) -> bool:
        try:
            await self.get_stream_info(channel)
            return True
        except ValueError:
            return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ── suggestions for the add-stream box ───────────────────────────────────────
#
# Same two rows the Twitch dropdown draws (src/output/twitch_clips.py
# search_channels / get_top_streams): {login, name, avatar, is_live, game} for
# a search hit and {login, name, game, viewers} for a popular channel. Kick's
# public API has "who is live, sorted by viewers" but no name search, so
# search goes to the site endpoint and falls back to an exact-slug lookup.

KICK_SITE_SEARCH = "https://kick.com/api/search"
KICK_SITE_LIVE = "https://kick.com/stream/livestreams/en"
SUGGEST_LIMIT = 24

_shared: KickPlatform | None = None


def _client() -> KickPlatform:
    global _shared
    if _shared is None:
        _shared = KickPlatform()
    return _shared


def _search_row(ch: dict) -> dict | None:
    user = ch.get("user") or {}
    slug = str(ch.get("slug") or user.get("username") or "").strip().lower()
    if not slug:
        return None
    return {"login": slug,
            "name": str(user.get("username") or ch.get("username") or slug),
            "avatar": str(user.get("profile_pic") or ch.get("profile_pic") or ""),
            "is_live": bool(ch.get("is_live") or ch.get("livestream")),
            "game": ""}


def _live_row(item: dict) -> dict | None:
    """One popular-channel row from either the public API's livestream shape
    or the site's, which nest the same facts differently."""
    ch = item.get("channel") or {}
    user = ch.get("user") or {}
    slug = str(item.get("slug") or ch.get("slug") or user.get("username") or "").strip().lower()
    if not slug:
        return None
    cat = item.get("category") or {}
    cats = item.get("categories") or []
    game = cat.get("name") if isinstance(cat, dict) else ""
    if not game and cats and isinstance(cats[0], dict):
        game = cats[0].get("name")
    return {"login": slug,
            "name": str(user.get("username") or item.get("username") or slug),
            "game": _ascii(game),
            "viewers": int(item.get("viewer_count") or item.get("viewers") or 0)}


async def search_channels(query: str) -> list[dict]:
    p = _client()
    q = (query or "").strip()
    if not q:
        return []
    rows: list[dict] = []
    try:
        status, body = await p._get_json(KICK_SITE_SEARCH, params={"searched_word": q},
                                         headers=_BROWSER_HEADERS)
        if status == 200 and isinstance(body, dict):
            for ch in (body.get("channels") or [])[:SUGGEST_LIMIT]:
                row = _search_row(ch) if isinstance(ch, dict) else None
                if row:
                    rows.append(row)
    except Exception as exc:
        log.info("kick_search_failed", query=q, error=str(exc))
    if rows:
        return rows
    # No search result (or no search): the public API knows exact slugs.
    try:
        pub = await p._public_channel(q.lower())
    except ChannelOffline:
        return []
    except Exception as exc:
        log.info("kick_slug_lookup_failed", query=q, error=str(exc))
        return []
    if not pub:
        return []
    stream = pub.get("stream") or {}
    return [{"login": q.lower(), "name": str(pub.get("slug") or q),
             "avatar": str(pub.get("banner_picture") or ""),
             "is_live": bool(stream.get("is_live")),
             "game": _ascii((pub.get("category") or {}).get("name"))}]


async def top_streams() -> list[dict]:
    """The most-watched live Kick channels right now."""
    p = _client()
    if p.configured():
        try:
            token = await p._get_app_token()
            status, body = await p._get_json(
                f"{KICK_API_BASE}/livestreams",
                params={"limit": SUGGEST_LIMIT, "sort": "viewer_count"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            if status == 200 and isinstance(body, dict):
                rows = [r for r in (_live_row(i) for i in (body.get("data") or [])
                                    if isinstance(i, dict)) if r]
                if rows:
                    return rows
            log.info("kick_public_livestreams_unusable", status=status)
        except Exception as exc:
            log.info("kick_public_livestreams_failed", error=str(exc))
    try:
        status, body = await p._get_json(KICK_SITE_LIVE,
                                         params={"page": 1, "limit": SUGGEST_LIMIT, "sort": "desc"},
                                         headers=_BROWSER_HEADERS)
        if status == 200 and isinstance(body, dict):
            return [r for r in (_live_row(i) for i in (body.get("data") or [])
                                if isinstance(i, dict)) if r]
        log.info("kick_site_livestreams_unusable", status=status)
    except Exception as exc:
        log.info("kick_site_livestreams_failed", error=str(exc))
    return []
