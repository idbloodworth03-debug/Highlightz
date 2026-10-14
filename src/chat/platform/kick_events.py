"""Kick chat, delivered by Kick: the public API's event webhooks (2026-10-14).

WHY THIS EXISTS. Kick chat used to be read the way kick.com reads it itself —
a Pusher websocket on the site's public app key, subscribed to
`chatrooms.<id>.v2`. Kick retired that: both app keys the site has used are
refused on every Pusher cluster with 4001 "App key … not in this cluster"
(checked from the production box 2026-10-14, all 18 key/cluster pairs). Every
Kick channel had been scored on audio and viewer count alone since — one
channel logged 459 reconnect errors in a day at an average chat velocity of
0.0, and a Pro customer asked why a busy stream made no clips.

THE SANCTIONED ROUTE, per Kick's developer docs (KickDevDocs, events/*):
  * an APP access token (client credentials — the one kick.py already mints
    for liveness) lets you "subscribe to events from any channel given you
    supply the user ID of that channel": POST /public/v1/events/subscriptions
    with {broadcaster_user_id, events: [chat.message.sent v1], method: webhook};
  * Kick POSTs every chat message to the ONE webhook URL set on the app in the
    Kick Developer tab (not per subscription) — /kick/webhook here;
  * every delivery is signed: RSA PKCS#1 v1.5 + SHA-256 over
    "<Kick-Event-Message-Id>.<Kick-Event-Message-Timestamp>.<raw body>",
    base64 in Kick-Event-Signature, checked against Kick's public key from
    /public/v1/public-key — fetched, never pasted in: the docs say it rotates.

BUILT AROUND KICK'S KNOWN FAULTS (KickDevDocs issues, autumn 2026):
  * #419 — a subscription can be created (200 + id) and then be missing from
    the listing, so a listing is never a reason to subscribe again; the record
    of what was created is ours, on disk (kick_event_subscriptions.json);
  * #300 — deliveries can stall, so a live channel whose chat goes quiet is
    re-subscribed (rate-limited; harmless when the chat really is quiet);
  * Kick unsubscribes a webhook that keeps failing for over a day, so a
    delivery whose signature checks out is ALWAYS answered 200 — listener or
    not, an event we use or not — and the signature is checked against the
    RAW body, never a re-serialised one (the cause of one public report of
    exactly that unsubscribe).

One subscription per channel however many users monitor it; each live session
listens on its own queue (kick_chat.KickChatMonitor), so every user's engine
sees every message. A subscription is kept while anyone has the channel in
their list, offline included, and deleted by `prune` once nobody does.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from typing import Callable, Mapping

import aiohttp
import structlog
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config.settings import settings
from src.ingestion.platform import kick as kick_platform

log = structlog.get_logger(__name__)

SUBSCRIPTIONS_URL = f"{kick_platform.KICK_API_BASE}/events/subscriptions"
PUBLIC_KEY_URL = f"{kick_platform.KICK_API_BASE}/public-key"
CHAT_EVENT = "chat.message.sent"
CHAT_EVENT_VERSION = 1

# A chat delivery is a couple of kilobytes; nothing genuine comes near this.
MAX_BODY = 64 * 1024


class KickRefused(RuntimeError):
    """Kick (or the missing app credentials) said no to a subscription call.
    `status` is Kick's HTTP status when there was one."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _snippet(body) -> str:
    try:
        s = body if isinstance(body, str) else json.dumps(body)
    except (TypeError, ValueError):
        s = str(body)
    return s[:300]


def _obj(x) -> dict:
    return x if isinstance(x, dict) else {}


# ── HTTP, in one place so tests can stand in front of it ─────────────────────

_session: aiohttp.ClientSession | None = None


async def _http(method: str, url: str, *, headers: dict | None = None,
                params=None, json_body=None) -> tuple[int, object]:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    async with _session.request(method, url, headers=headers, params=params,
                                json=json_body,
                                timeout=aiohttp.ClientTimeout(total=20)) as resp:
        text = await resp.text()
        try:
            body = json.loads(text) if text else None
        except ValueError:
            body = text
        return resp.status, body


async def _app_token() -> str:
    client = kick_platform._client()
    if not client.configured():
        raise KickRefused("KICK_CLIENT_ID / KICK_CLIENT_SECRET are not set")
    try:
        return await client._get_app_token()
    except Exception as exc:
        raise KickRefused(str(exc)) from exc


def _forget_token() -> None:
    kick_platform._client()._app_token = ""


async def _authed(method: str, url: str, *, params=None, json_body=None) -> tuple[int, object]:
    """A public-API call with the app token; a 401 mints a new token once."""
    for attempt in (0, 1):
        token = await _app_token()
        try:
            status, body = await _http(
                method, url, params=params, json_body=json_body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        except Exception as exc:
            raise KickRefused(f"{type(exc).__name__}: {exc}") from exc
        if status == 401 and attempt == 0:
            _forget_token()
            continue
        return status, body
    raise KickRefused("unreachable")


# ── Kick's public key ────────────────────────────────────────────────────────

_key = None                    # a loaded cryptography public key, or None
_key_tried = 0.0
_KEY_RETRY_S = 30.0            # no key yet: how soon to ask again
_KEY_REFRESH_S = 60.0          # the key stopped verifying: how soon to re-fetch

_PEM = re.compile(r"-----BEGIN ((?:RSA )?PUBLIC KEY)-----(.*?)-----END \1-----", re.S)
_B64 = re.compile(r"[A-Za-z0-9+/]+=*")


def _key_text(body) -> str:
    """The key's text in whatever shape the endpoint answers with. Its schema
    is not in Kick's docs (the page points at an OpenAPI file), so this finds
    a PEM block at any depth, or a bare base64 key under a key-ish name, or
    the PEM as the whole plain-text body."""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "ignore")
    if isinstance(body, str):
        return body if "PUBLIC KEY-----" in body.replace("\\n", "\n") else ""
    items = (list(body.items()) if isinstance(body, dict)
             else list(enumerate(body)) if isinstance(body, list) else [])
    for _, v in items:
        found = _key_text(v)
        if found:
            return found
    for k, v in items:
        if isinstance(k, str) and "key" in k.lower() and isinstance(v, str):
            bare = re.sub(r"\s+", "", v)
            if len(bare) >= 200 and _B64.fullmatch(bare):
                return bare
    return ""


def _parse_key(body):
    """A public key object, or None. The base64 inside the armour is decoded
    and read as DER, so line wrapping (one line, escaped newlines) and the
    SPKI-vs-PKCS#1 label cannot make a good key fail to load."""
    text = _key_text(body).replace("\\n", "\n")
    if not text:
        return None
    m = _PEM.search(text)
    b64 = re.sub(r"\s+", "", m.group(2) if m else text)
    try:
        return serialization.load_der_public_key(base64.b64decode(b64))
    except (ValueError, TypeError):
        return None


async def _public_key(refresh: bool = False):
    """The cached key; fetched on first need, re-fetched when a signature
    stops verifying (rotation). Both are rate-limited, so a stream of forged
    requests cannot turn into a stream of requests to Kick."""
    global _key, _key_tried
    if _key is not None and not refresh:
        return _key
    now = time.time()
    if now - _key_tried < (_KEY_REFRESH_S if _key is not None else _KEY_RETRY_S):
        return _key
    _key_tried = now
    try:
        status, body = await _http("GET", PUBLIC_KEY_URL, headers={"Accept": "application/json"})
        if status in (401, 403) and kick_platform._client().configured():
            status, body = await _authed("GET", PUBLIC_KEY_URL)
    except Exception as exc:
        log.warning("kick_public_key_unavailable", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return _key
    key = _parse_key(body) if status == 200 else None
    if key is None:
        log.warning("kick_public_key_unavailable", status=status, answer=_snippet(body))
        return _key
    log.info("kick_public_key_loaded", refreshed=_key is not None)
    _key = key
    return _key


def _signed_by(key, message_id: str, timestamp: str, body: bytes, signature: str) -> bool:
    try:
        key.verify(base64.b64decode(signature),
                   f"{message_id}.{timestamp}.".encode() + body,
                   padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


# ── who is listening ─────────────────────────────────────────────────────────

QUEUE_MAX = 5000
_listeners: dict[str, set[asyncio.Queue]] = {}


def listen(broadcaster_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _listeners.setdefault(str(broadcaster_id), set()).add(q)
    return q


def unlisten(broadcaster_id: str, q: asyncio.Queue) -> None:
    qs = _listeners.get(str(broadcaster_id))
    if qs is not None:
        qs.discard(q)
        if not qs:
            _listeners.pop(str(broadcaster_id), None)


def _dispatch(broadcaster_id: str, author: str, content: str) -> int:
    n = 0
    for q in list(_listeners.get(broadcaster_id, ())):
        try:
            q.put_nowait((author, content))
            n += 1
        except asyncio.QueueFull:
            pass    # a stalled session drops chat; it never holds up the webhook
    return n


# ── a delivery ───────────────────────────────────────────────────────────────

# The same chat message can arrive twice: Kick retries, and a renewed
# subscription can overlap its predecessor for a moment. Keyed on the CHAT
# message id (identical across both), not the delivery id (which is not).
_seen: dict[str, float] = {}
_SEEN_TTL_S = 600.0
_SEEN_MAX = 50_000


def _first_sighting(message_id: str) -> bool:
    now = time.time()
    if message_id in _seen:
        return False
    _seen[message_id] = now
    while _seen:
        oldest = next(iter(_seen))
        if len(_seen) > _SEEN_MAX or now - _seen[oldest] > _SEEN_TTL_S:
            del _seen[oldest]
        else:
            break
    return True


_EMOTE = re.compile(r"\[emote:\d+:([^\]\s]+)\]")


def plain_text(content: str) -> str:
    """Kick writes an emote into the text as [emote:<id>:<name>]; Twitch chat
    carries the bare name. The scorer counts a one-word hype emote (KEKW,
    POGGERS …) at 2.5x and measures crowdspeak on one-word messages
    (src/chat/metrics.py), and a bracketed tag is never one word — so the tag
    is read as its name, the way Twitch chat already arrives."""
    return _EMOTE.sub(r"\1", content)


_stats = {"received": 0, "rejected": 0, "duplicate": 0, "delivered": 0,
          "unclaimed": 0, "other_events": 0}
_stats_since = time.time()
_STATS_EVERY_S = 600.0
_reject_logged = 0.0
_REJECT_LOG_EVERY_S = 300.0


def _tick_stats() -> None:
    global _stats_since
    now = time.time()
    if now - _stats_since < _STATS_EVERY_S:
        return
    if any(_stats.values()):
        log.info("kick_webhook_stats", window_s=round(now - _stats_since), **_stats)
    for k in _stats:
        _stats[k] = 0
    _stats_since = now


def _reject(why: str) -> None:
    global _reject_logged
    _stats["rejected"] += 1
    now = time.time()
    if now - _reject_logged >= _REJECT_LOG_EVERY_S:
        _reject_logged = now
        log.warning("kick_webhook_rejected", why=why, rejected_in_window=_stats["rejected"])


async def handle_delivery(headers: Mapping[str, str], body: bytes) -> int:
    """One POST from Kick → the status to answer it with.

    400 without Kick's headers, 401 for a signature that does not verify
    (after one rate-limited key re-fetch, for rotation), 503 while there is no
    key to check with. Everything signed is 200, including events nobody
    listens to — see the module docstring on Kick unsubscribing failures."""
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    _stats["received"] += 1
    _tick_stats()
    mid = h.get("kick-event-message-id", "")
    ts = h.get("kick-event-message-timestamp", "")
    sig = h.get("kick-event-signature", "")
    if not (mid and ts and sig):
        _reject("missing Kick-Event headers")
        return 400
    key = await _public_key()
    if key is None:
        _reject("no public key to verify with")
        return 503
    if not _signed_by(key, mid, ts, body, sig):
        fresh = await _public_key(refresh=True)
        if fresh is None or fresh is key or not _signed_by(fresh, mid, ts, body, sig):
            _reject("signature does not verify")
            return 401
    if h.get("kick-event-type", "") != CHAT_EVENT:
        _stats["other_events"] += 1
        return 200
    try:
        payload = json.loads(body)
    except ValueError:
        return 200
    payload = _obj(payload)
    if not _first_sighting(str(payload.get("message_id") or mid)):
        _stats["duplicate"] += 1
        return 200
    bid = str(_obj(payload.get("broadcaster")).get("user_id") or "")
    author = str(_obj(payload.get("sender")).get("username") or "")
    content = plain_text(str(payload.get("content") or ""))
    if bid and author and content.strip():
        if _dispatch(bid, author, content):
            _stats["delivered"] += 1
        else:
            _stats["unclaimed"] += 1
    return 200


# ── subscriptions ────────────────────────────────────────────────────────────

_SUBS_FILE = Path(settings.local_storage_path) / "kick_event_subscriptions.json"
_subs: dict[str, dict] = {}            # broadcaster id -> {"id", "slug", "at"}
_subs_loaded = False
_inflight: set[str] = set()
_attempted: dict[str, float] = {}
RETRY_GAP_S = 120.0     # no subscription yet: how soon a failed attempt is retried
RENEW_GAP_S = 600.0     # a quiet one: how soon it may be renewed again


def _load_subs() -> None:
    global _subs_loaded
    if _subs_loaded:
        return
    _subs_loaded = True
    try:
        if _SUBS_FILE.exists():
            raw = json.loads(_SUBS_FILE.read_text(encoding="utf-8"))
            _subs.update({str(k): v for k, v in raw.items() if isinstance(v, dict)})
    except (OSError, ValueError, AttributeError):
        log.warning("kick_events_record_unreadable", path=str(_SUBS_FILE))


def _save_subs() -> None:
    try:
        _SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SUBS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_subs, indent=2), encoding="utf-8")
        tmp.replace(_SUBS_FILE)
    except OSError as exc:
        log.warning("kick_events_record_write_failed", error=str(exc))


def _created_id(body) -> tuple[str, list[str]]:
    """The subscription id out of a create answer, and any per-event errors.
    Kick answers per requested event; tolerant of a dict or a list."""
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, dict):
        rows = [rows]
    sub_id, errors = "", []
    for r in rows if isinstance(rows, list) else []:
        r = _obj(r)
        if r.get("error"):
            errors.append(str(r["error"]))
        sid = r.get("subscription_id") or r.get("id")
        if sid and not sub_id:
            sub_id = str(sid)
    return sub_id, errors


async def _create(broadcaster_id: str) -> tuple[str, object]:
    status, body = await _authed("POST", SUBSCRIPTIONS_URL, json_body={
        "broadcaster_user_id": int(broadcaster_id) if broadcaster_id.isdigit() else broadcaster_id,
        "events": [{"name": CHAT_EVENT, "version": CHAT_EVENT_VERSION}],
        "method": "webhook"})
    if status not in (200, 201):
        raise KickRefused(f"HTTP {status}: {_snippet(body)}", status)
    sub_id, errors = _created_id(body)
    if errors and not sub_id:
        raise KickRefused("; ".join(errors)[:300], status)
    return sub_id, body


async def _delete(ids: list[str]) -> bool:
    ids = [i for i in ids if i]
    if not ids:
        return True
    try:
        status, body = await _authed("DELETE", SUBSCRIPTIONS_URL,
                                     params=[("id", i) for i in ids])
    except KickRefused as exc:
        log.warning("kick_events_unsubscribe_failed", ids=ids, error=str(exc)[:200])
        return False
    if status in (200, 202, 204, 404):
        return True
    log.warning("kick_events_unsubscribe_failed", ids=ids, status=status, answer=_snippet(body))
    return False


async def ensure_subscribed(broadcaster_id: str, slug: str = "", *, renew: bool = False) -> bool:
    """Make sure Kick is sending this channel's chat. True when a subscription
    is believed to be in place.

    The record on disk is the truth (Kick's own listing can omit live ones,
    #419), so a channel with a record is not asked about again — unless
    `renew`, which the monitor asks for when a live chat has gone quiet: a
    fresh subscription is created first and the old one deleted after, so
    there is no gap (overlap is deduped). Kick's docs do not say whether it
    accepts a second subscription for the same channel and event, so if the
    create is refused the old one is deleted and the create tried again.
    A renewal at most every RENEW_GAP_S; a failed first attempt is retried
    after RETRY_GAP_S."""
    bid = str(broadcaster_id or "")
    if not bid:
        return False
    slug = (slug or "").lower()
    _load_subs()
    rec = _subs.get(bid)
    if rec and not renew:
        if slug and rec.get("slug") != slug:
            rec["slug"] = slug
            _save_subs()
        return True
    if bid in _inflight:
        return bool(rec)
    now = time.time()
    if now - _attempted.get(bid, 0.0) < (RENEW_GAP_S if rec else RETRY_GAP_S):
        return bool(rec)
    _attempted[bid] = now
    _inflight.add(bid)
    try:
        # Warm the key before the first delivery can arrive, so it verifies.
        await _public_key()
        old = (rec or {}).get("id", "")
        try:
            try:
                sub_id, answer = await _create(bid)
            except KickRefused as exc:
                # Only a refusal that can mean "already subscribed" — never
                # an auth failure or a rate limit, which a delete would not
                # fix and which would cost a working subscription.
                if not (old and exc.status in (200, 201, 400, 409, 422)):
                    raise
                log.info("kick_events_renew_replacing", slug=slug, broadcaster_id=bid,
                         error=str(exc)[:200])
                if await _delete([old]):
                    _subs.pop(bid, None)
                    _save_subs()
                    rec, old = None, ""
                sub_id, answer = await _create(bid)
        except KickRefused as exc:
            log.warning("kick_events_subscribe_failed", slug=slug, broadcaster_id=bid,
                        renew=bool(rec), error=str(exc)[:300])
            return bool(rec)
        _subs[bid] = {"id": sub_id, "slug": slug or (rec or {}).get("slug", ""), "at": int(now)}
        _save_subs()
        log.info("kick_events_renewed" if rec else "kick_events_subscribed",
                 slug=slug, broadcaster_id=bid, subscription_id=sub_id,
                 **({} if sub_id else {"answer": _snippet(answer)}))
        if old and old != sub_id:
            await _delete([old])
        return True
    finally:
        _inflight.discard(bid)


async def prune(monitored: set[str]) -> int:
    """Delete the subscription of every channel nobody monitors any more.
    Kept while ANY user lists the channel, live or not, or a session is
    listening; a delete that fails is retried on the next pass."""
    _load_subs()
    dropped = 0
    for bid, rec in list(_subs.items()):
        if rec.get("slug") in monitored or _listeners.get(bid) or bid in _inflight:
            continue
        if await _delete([str(rec.get("id") or "")]) and _subs.get(bid) is rec:
            _subs.pop(bid, None)
            dropped += 1
            log.info("kick_events_unsubscribed", slug=rec.get("slug"), broadcaster_id=bid)
    if dropped:
        _save_subs()
    return dropped


PRUNE_FIRST_S = 300.0
PRUNE_EVERY_S = 1800.0


async def prune_task(monitored: Callable[[], set[str]]) -> None:
    """Started once with the server. Never raises: it runs in the same gather
    as the dashboard, where an exception would stop the whole app."""
    try:
        _load_subs()
        if _subs:
            await _public_key()     # deliveries resume the moment we are back up
    except Exception as exc:
        log.warning("kick_events_warmup_failed", error=str(exc)[:200])
    await asyncio.sleep(PRUNE_FIRST_S)
    while True:
        try:
            await prune({str(s).lower() for s in monitored()})
        except Exception as exc:
            log.warning("kick_events_prune_failed", error=str(exc)[:200])
        await asyncio.sleep(PRUNE_EVERY_S)
