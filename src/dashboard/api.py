"""
FastAPI dashboard: REST endpoints + WebSocket for real-time clip review.

Endpoints:
  GET  /clips          — list clips (filterable by status/channel)
  GET  /clips/{id}     — single clip detail
  POST /clips/{id}/approve
  POST /clips/{id}/reject
  GET  /streams        — list currently monitored streams
  POST /streams        — register a new stream to watch
  DELETE /streams/{channel}
  WS   /ws             — real-time clip notifications
"""

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import time
import uuid
from html import unescape
from typing import Any
from pathlib import Path
from fastapi.staticfiles import StaticFiles

import structlog
from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, HTTPException,
                     Request, Form, File, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (HTMLResponse, RedirectResponse, JSONResponse,
                               PlainTextResponse, Response, FileResponse)
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware as _ProxyHeadersMiddleware
    _HAS_PROXY_HEADERS = True
except ImportError:
    # Without this, every request appears to come from the Nginx loopback IP,
    # collapsing per-IP login rate-limiting into a single shared bucket.
    _HAS_PROXY_HEADERS = False
    _log_boot = logging.getLogger(__name__)
    _log_boot.critical(
        "SECURITY: uvicorn ProxyHeadersMiddleware unavailable — real client IPs "
        "will NOT be honored and login rate-limiting will be ineffective behind Nginx."
    )

from config.settings import settings
from src.dashboard import undo
from src.dashboard.aurora_html import DASHBOARD_HTML

_STREAMS_FILE  = Path(settings.local_storage_path) / "streams.json"
_CLIPS_FILE    = Path(settings.local_storage_path) / "clips.json"
_FEEDBACK_FILE = Path(settings.local_storage_path) / "feedback.json"

log = structlog.get_logger(__name__)

app = FastAPI(title="Highlightz Dashboard", version="1.0.0")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Auth middleware ───────────────────────────────────────────────────────────

_OPEN_PATHS    = {"/login", "/logout", "/health", "/favicon.ico", "/tos", "/privacy", "/cookies",
                  "/opt-out", "/opt-out/confirm", "/opt-out/success", "/landing/stats",
                  "/landing/showcase", "/robots.txt", "/sitemap.xml", "/tutorial"}
# Short referral links. Open, because the whole point is that a signed-out
# stranger clicks them — if the auth middleware bounced them to /login first,
# the ref would be gone before any handler saw it.
def _referral_paths() -> set[str]:
    from src.auth.referrals import all_keys
    return {f"/{k}" for k in all_keys()} | {f"/r/{k}" for k in all_keys()}


# "/i/" is the invite link: a signed-out stranger clicking it is the
# entire point, so it must never be bounced to /login first.
_AUTH_PREFIXES = ("/auth/", "/billing/", "/i/")
_STATIC_PREFIX = "/static"

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (path in _OPEN_PATHS or path == "/login"
                or path.startswith(_STATIC_PREFIX)
                or path.rstrip("/").lower() in _referral_paths()
                or any(path.startswith(p) for p in _AUTH_PREFIXES)):
            return await call_next(request)
        if not request.session.get("auth"):
            # The root path is the public marketing landing page — let it through
            # so visitors see it instead of being bounced straight to sign-in.
            if path == "/":
                return await call_next(request)
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)
        # Refresh is_admin and subscription state from DB on every request
        from src.auth import users as user_store
        uid = request.session.get("user_id")
        if uid:
            db_user = user_store.get_by_id(uid)
            if db_user:
                status        = db_user.get("subscription_status", "none")
                trial_ends_at = db_user.get("trial_ends_at", 0)
                # A timed trial that has run past trial_ends_at no longer grants access.
                if status == "trialing" and time.time() >= trial_ends_at:
                    status = "expired"
                    # Persist the transition once so the DB reflects reality
                    # (accurate admin stats; trial ledger already blocks re-grants).
                    user_store.update_subscription(uid, db_user.get("stripe_customer_id"), "expired")
                    # Stop running streams immediately — don't wait for idle reaper —
                    # and tell every open tab live (realtime contract): the requesting
                    # tab learns via the paywall redirect, but other tabs/sockets
                    # would otherwise keep showing running streams.
                    asyncio.create_task(_stop_user_streams_now(uid))
                    asyncio.create_task(broadcast(
                        {"event": "subscription_expired",
                         "message": "Your free trial has ended — streams have been stopped."},
                        user_id=uid,
                    ))
                request.session["is_admin"]            = db_user.get("is_admin", False)
                request.session["is_labeler"]          = db_user.get("is_labeler", False)
                request.session["subscription_status"] = status
                request.session["trial_ends_at"]       = trial_ends_at
                _user_last_active[uid] = time.time()
        # NO BILLING GATE HERE ANY MORE. Everyone who signs in gets the
        # product; what differs is how much of it (src/billing/plans.py). This
        # used to redirect non-subscribers to /billing/paywall, which meant a
        # signup without a card saw nothing at all — you cannot ask someone to
        # pay $10 to find out whether the detector works on their channel.
        # Access control now lives with the individual limits: add_stream, the
        # pending-clip cap, the VOD gate and the Clip Editor gate each ask
        # limits_for() what this user is allowed. The paywall page still exists
        # and is still linked from upgrade prompts; it is just not a wall.
        return await call_next(request)

# Middleware stack is LIFO — SessionMiddleware added last runs first,
# so the cookie is parsed before AuthMiddleware inspects the session.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.dashboard_secret_key,
    max_age=86400 * 7,
    https_only=settings.dashboard_https_only,
    same_site="lax",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://highlightz.app"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
            "img-src 'self' https: data: blob:; "
            "font-src 'self' https://fonts.gstatic.com; "
            + ("connect-src 'self' wss:; " if settings.dashboard_https_only else "connect-src 'self' wss: ws:; ")
            + "frame-src https://clips.twitch.tv https://player.twitch.tv; "
            "frame-ancestors 'none';"
        )
        if settings.dashboard_https_only:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Trust X-Forwarded-For from local Nginx proxy only (added last = runs first due to LIFO)
if _HAS_PROXY_HEADERS:
    app.add_middleware(_ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1", "::1"])

from typing import Callable

# ── Atomic file writes ────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via a temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

# ── Input validation ──────────────────────────────────────────────────────────

_CHANNEL_RE      = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
_VALID_PLATFORMS = {"twitch", "kick"}
_VALID_PRESETS   = {"default", "fps", "chess", "irl", "small", "variety", "moba", "casino", "sports"}


def _clean_channel(channel: str) -> str:
    """Validate and normalize a channel path parameter to match stored keys.

    add_stream stores channels lowercased, so path-param routes must apply the
    same regex + lowercasing or lookups silently miss (and malformed input is
    rejected before it reaches the stream/clip pipeline)."""
    if not _CHANNEL_RE.fullmatch(channel):
        raise HTTPException(status_code=400, detail="Invalid channel name")
    return channel.lower()

# ── Persistence ───────────────────────────────────────────────────────────────

def _load_clips() -> dict:
    try:
        return {c["id"]: c for c in json.loads(_CLIPS_FILE.read_text())}
    except FileNotFoundError:
        return {}
    except Exception:
        log.error("clips_file_load_failed", path=str(_CLIPS_FILE))
        return {}

def _save_clips() -> None:
    _atomic_write(_CLIPS_FILE, json.dumps(list(_clips.values())))

def _load_streams() -> dict:
    try:
        result = {}
        for s in json.loads(_STREAMS_FILE.read_text()):
            uid = s.get("user_id", "")
            key = f"{uid}:{s['channel']}" if uid else s["channel"]
            result[key] = s
        return result
    except FileNotFoundError:
        return {}
    except Exception:
        log.error("streams_file_load_failed", path=str(_STREAMS_FILE))
        return {}

def _save_streams() -> None:
    _atomic_write(_STREAMS_FILE, json.dumps(list(_streams.values())))

def _load_feedback() -> list:
    try:
        return json.loads(_FEEDBACK_FILE.read_text())
    except FileNotFoundError:
        return []
    except Exception:
        log.error("feedback_file_load_failed", path=str(_FEEDBACK_FILE))
        return []

def _save_feedback() -> None:
    _atomic_write(_FEEDBACK_FILE, json.dumps(_feedback))

_clips:        dict[str, dict]           = _load_clips()
_streams:      dict[str, dict]           = _load_streams()
_feedback:     list                      = _load_feedback()
_ws_clients:   dict[str, set[WebSocket]] = {}  # user_id -> set of WebSocket
_data_lock = asyncio.Lock()
_ws_lock   = asyncio.Lock()

# ── Idle stream reaper ────────────────────────────────────────────────────────
# Track the last time each user made an authenticated request or sent a WS ping.
# Streams persist through browser closes — only the idle reaper or liveness
# check stops them (not WebSocket disconnect).
#
# PERSISTED, because it used to be memory-only and the reaper reads a MISSING
# entry as "active right now". Every restart therefore wiped the clock and gave
# every abandoned stream another full 8 hours — so during any period of
# frequent deploys the reaper effectively never fired and dead streams held
# their slot against the process-wide capacity limit forever.
_ACTIVITY_FILE = Path(settings.local_storage_path) / "user_activity.json"


def _load_activity() -> dict[str, float]:
    try:
        raw = json.loads(_ACTIVITY_FILE.read_text())
        return {str(k): float(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


_user_last_active: dict[str, float] = _load_activity()
_IDLE_STREAM_TIMEOUT = 28800  # 8 hours


def _save_activity() -> None:
    """Called from the reaper's own 5-minute tick, so this costs one small
    write per five minutes rather than one per request."""
    try:
        _atomic_write(_ACTIVITY_FILE, json.dumps(_user_last_active))
    except Exception as exc:      # never let bookkeeping break the reaper
        log.warning("activity_save_failed", error=str(exc))

# ── Login rate-limit ──────────────────────────────────────────────────────────
# Simple in-process counter: IP → (attempts, window_start)
_login_attempts: dict[str, tuple[int, float]] = {}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW       = 60  # seconds
_login_rate_lock    = asyncio.Lock()

# Kick OAuth initiation rate limit (per IP)
_kick_oauth_attempts: dict[str, tuple[int, float]] = {}
_KICK_OAUTH_MAX    = 20
_KICK_OAUTH_WINDOW = 60
_kick_oauth_lock   = asyncio.Lock()

async def _check_kick_oauth_rate(ip: str) -> None:
    async with _kick_oauth_lock:
        now = time.time()
        stale = [k for k, (_, ts) in list(_kick_oauth_attempts.items()) if now - ts > _KICK_OAUTH_WINDOW]
        for k in stale:
            _kick_oauth_attempts.pop(k, None)
        attempts, window_start = _kick_oauth_attempts.get(ip, (0, now))
        if now - window_start > _KICK_OAUTH_WINDOW:
            attempts, window_start = 0, now
        if attempts >= _KICK_OAUTH_MAX:
            raise HTTPException(status_code=429, detail="Too many requests — wait a minute")
        _kick_oauth_attempts[ip] = (attempts + 1, window_start)

async def _check_login_rate(ip: str) -> None:
    async with _login_rate_lock:
        now = time.time()
        stale = [k for k, (count, ts) in list(_login_attempts.items()) if now - ts > _LOGIN_WINDOW]
        for k in stale:
            _login_attempts.pop(k, None)
        attempts, window_start = _login_attempts.get(ip, (0, now))
        if now - window_start > _LOGIN_WINDOW:
            attempts, window_start = 0, now
        if attempts >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many login attempts — wait a minute")
        _login_attempts[ip] = (attempts + 1, window_start)

async def _clear_login_rate(ip: str) -> None:
    async with _login_rate_lock:
        _login_attempts.pop(ip, None)

# Per-user force-clip rate limit
_force_clip_hits: dict[str, tuple[int, float]] = {}
_FORCE_CLIP_MAX      = 6
_FORCE_CLIP_WINDOW   = 60
_force_clip_rate_lock = asyncio.Lock()

async def _check_force_clip_rate(uid: str) -> None:
    async with _force_clip_rate_lock:
        now = time.time()
        stale = [k for k, (count, ts) in list(_force_clip_hits.items()) if now - ts > _FORCE_CLIP_WINDOW]
        for k in stale:
            _force_clip_hits.pop(k, None)
        attempts, window_start = _force_clip_hits.get(uid, (0, now))
        if now - window_start > _FORCE_CLIP_WINDOW:
            attempts, window_start = 0, now
        if attempts >= _FORCE_CLIP_MAX:
            raise HTTPException(status_code=429, detail="Too many manual clips — wait a moment")
        _force_clip_hits[uid] = (attempts + 1, window_start)

# ── Helper ────────────────────────────────────────────────────────────────────

def _delete_clip_file(clip: dict) -> None:
    url = clip.get("storage_url", "")
    if not url:
        return
    try:
        clips_root = Path(settings.local_storage_path).resolve()
        p = Path(url).resolve()
        if p.is_relative_to(clips_root) and p.suffix == ".mp4" and p.exists():
            p.unlink()
            log.info("clip_file_deleted", path=str(p))
    except Exception as exc:
        log.warning("clip_file_delete_failed", path=url, error=str(exc))

# Callbacks set by main.py after Redis is ready
_publish_new_stream:    Callable | None = None
_publish_remove_stream: Callable | None = None
_force_clip_cb:         Callable | None = None

def set_stream_publisher(add_cb: Callable, remove_cb: Callable) -> None:
    global _publish_new_stream, _publish_remove_stream
    _publish_new_stream    = add_cb
    _publish_remove_stream = remove_cb

def set_force_clip_callback(cb: Callable) -> None:
    global _force_clip_cb
    _force_clip_cb = cb

# ── WebSocket broadcast ───────────────────────────────────────────────────────

_MAX_WS_PER_USER = 8

async def broadcast(event: dict, user_id: str | None = None) -> None:
    async with _ws_lock:
        if user_id:
            targets = set(_ws_clients.get(user_id, set()))
        else:
            targets = {ws for clients in _ws_clients.values() for ws in clients}
    dead = set()
    for ws in targets:
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.add(ws)
    if dead:
        async with _ws_lock:
            for clients in _ws_clients.values():
                clients.difference_update(dead)

# ── Clip pipeline ─────────────────────────────────────────────────────────────

_DEDUP_WINDOW    = 45   # seconds — skip clip if same channel+user had one recently
# Per-user pending-clip cap is plan-dependent (Starter 50 / Pro 200) — see
# src/billing/plans.py. Oldest pending clips are evicted when the cap is hit.

# ── All-time clip counter (public, powers the landing-page ticker) ────────────
# Monotonic count of every clip the system has ever captured (live + VOD moments,
# regardless of later approve/reject/delete). Seeded once from historical data:
# per-channel profile tallies + whatever is currently in the clip store.
_CLIP_COUNTER_FILE = Path(settings.local_storage_path) / "clip_counter.json"
_clip_counter: int | None = None   # lazy-loaded cache


def _seed_clip_counter() -> int:
    total = len(_clips)
    profiles_dir = Path(settings.local_storage_path) / "profiles"
    for pf in profiles_dir.glob("**/*.json"):
        try:
            total += int(json.loads(pf.read_text()).get("total_clips", 0) or 0)
        except Exception:
            continue
    return total


def get_clip_counter() -> int:
    global _clip_counter
    if _clip_counter is None:
        try:
            _clip_counter = int(json.loads(_CLIP_COUNTER_FILE.read_text())["total"])
        except Exception:
            _clip_counter = _seed_clip_counter()
            _persist_clip_counter()
    return _clip_counter


def _persist_clip_counter() -> None:
    try:
        _atomic_write(_CLIP_COUNTER_FILE, json.dumps({"total": _clip_counter}))
    except Exception as exc:  # a stats counter must never break the clip pipeline
        log.warning("clip_counter_save_failed", error=str(exc))


def increment_clip_counter(n: int = 1) -> None:
    global _clip_counter
    _clip_counter = get_clip_counter() + n
    _persist_clip_counter()


# ── Landing-page showcase (admin-curated example clips) ───────────────────────
# The owner hand-picks approved clips to feature publicly on the landing page.
# Curated (never automatic) so no other user's activity leaks, and only a
# whitelisted subset of fields is exposed.
_SHOWCASE_FILE = Path(settings.local_storage_path) / "showcase.json"
_SHOWCASE_MAX  = 8


def _load_showcase() -> list[dict]:
    try:
        data = json.loads(_SHOWCASE_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_showcase(items: list[dict]) -> None:
    _atomic_write(_SHOWCASE_FILE, json.dumps(items))


def _showcase_entry(clip: dict) -> dict:
    """Public-safe projection of a clip — nothing user-identifying beyond the
    (public) Twitch channel the clip is from."""
    return {
        "id":            clip.get("id"),
        "clip_title":    clip.get("clip_title") or clip.get("stream_title") or "Clip",
        "channel":       clip.get("channel"),
        "game":          clip.get("game") or "",
        "twitch_url":    clip.get("twitch_url"),
        "embed_url":     clip.get("embed_url") or "",
        "thumbnail_url": clip.get("thumbnail_url") or "",
        "score":         round(float(clip.get("trigger_score") or clip.get("score") or 0)),
        "duration_seconds": clip.get("duration_seconds") or 0,
    }


def prune_showcase(clip_ids: set[str]) -> None:
    """Drop showcase entries whose clip was removed (e.g. deleted on Twitch by
    the dead-clip sweep) so the landing page never advertises a dead link."""
    items = _load_showcase()
    kept  = [e for e in items if e.get("id") not in clip_ids]
    if len(kept) != len(items):
        _save_showcase(kept)
        log.info("showcase_pruned", removed=len(items) - len(kept))


# Clips younger than this are skipped by the dead-clip sweep — they were
# verified queryable at creation, and this avoids any freshness edge case.
_SWEEP_MIN_AGE_SECS = 900.0


async def sweep_dead_twitch_clips(fetch_existing) -> int:
    """Remove stored Twitch clips that were deleted on Twitch's side after
    creation (broadcasters/mods can — and on some channels routinely do —
    delete clips, leaving dead links in the review queue and library).

    fetch_existing(slugs) -> set of ids Twitch still has, or None on lookup
    failure. On None we remove NOTHING — unknown must never read as gone.
    Returns the number of clips removed. The all-time clip counter is not
    decremented (it counts captures, not survivors)."""
    now = time.time()
    async with _data_lock:
        candidates = {
            c["twitch_clip_id"]: c["id"]
            for c in _clips.values()
            if c.get("platform") == "twitch"
            and not c.get("is_vod_moment")
            and c.get("twitch_clip_id")
            and now - c.get("created_at", now) > _SWEEP_MIN_AGE_SECS
        }
    if not candidates:
        return 0

    existing = await fetch_existing(list(candidates.keys()))
    if existing is None:
        log.warning("dead_clip_sweep_skipped", reason="existence lookup failed")
        return 0

    gone_ids = [cid for slug, cid in candidates.items() if slug not in existing]
    removed = []
    async with _data_lock:
        for cid in gone_ids:
            clip = _clips.pop(cid, None)
            if clip:
                removed.append(clip)
        if removed:
            _save_clips()
    for clip in removed:
        _delete_clip_file(clip)
        await broadcast({"event": "clip_removed", "clip_id": clip["id"]},
                        user_id=clip.get("user_id"))
        log.info("dead_clip_removed", clip_id=clip["id"], channel=clip.get("channel"),
                 slug=clip.get("twitch_clip_id"),
                 reason="deleted on Twitch after creation")
    if removed:
        # A featured example that died on Twitch must leave the landing page too.
        prune_showcase({c["id"] for c in removed})
    return len(removed)


async def notify_clip_ready(clip: dict) -> None:
    async with _data_lock:
        channel  = clip.get("channel")
        clip_uid = clip.get("user_id")
        clip_ts  = clip.get("created_at", time.time())

        # Dedup within the same user's clips only
        for existing in _clips.values():
            if (existing.get("channel") == channel
                    and existing.get("user_id") == clip_uid
                    and abs(existing.get("created_at", 0) - clip_ts) < _DEDUP_WINDOW):
                log.info("clip_deduplicated", clip_id=clip["id"], channel=channel,
                         duplicate_of=existing["id"])
                return

        # Per-user pending cap (plan-dependent: Starter 50 / Pro 200)
        from src.billing.plans import limits_for
        from src.auth import users as _plan_user_store
        pending_cap = limits_for(_plan_user_store.get_by_id(clip_uid))["max_pending"]
        user_pending = sorted(
            [c for c in _clips.values()
             if c.get("status") == "pending" and c.get("user_id") == clip_uid],
            key=lambda c: c.get("created_at", 0),
        )
        # FULL QUEUE DROPS THE NEW CLIP — it does not evict an old one.
        # Until 2026-08-03 this deleted the OLDEST unreviewed clip to make room,
        # so a full queue silently destroyed work the user already had. Now the
        # newest moment is the one refused, which is both less destructive and
        # the honest version of "you missed a clip".
        # The processor checks pending_room() before creating the Twitch clip,
        # so this path only fires when the queue filled between that check and
        # here. Keeping it is what stops a race from putting the queue over cap.
        dropped = len(user_pending) >= pending_cap
        if dropped:
            log.info("clip_dropped_queue_full", clip_id=clip.get("id"),
                     channel=channel, pending=len(user_pending), cap=pending_cap)
            _delete_clip_file(clip)
        else:
            _clips[clip["id"]] = clip
            _save_clips()
            increment_clip_counter()
            # Counted at creation, not from _clips — rejected clips are deleted,
            # so a later census would report only survivors.
            from src.stats import stream_stats
            stream_stats.record(stream_stats.CAUGHT, clip)

    # Outside the lock: notify_clip_missed broadcasts, and awaiting a socket
    # write while holding _data_lock stalls every other clip in the pipeline.
    if dropped:
        await notify_clip_missed(clip_uid, channel)
        return

    await broadcast({"event": "clip_ready", "clip": clip}, user_id=clip_uid)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _current_user_id(request: Request) -> str:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uid

# ── Twitch OAuth ──────────────────────────────────────────────────────────────

@app.get("/auth/twitch")
async def twitch_login(request: Request, intent: str = ""):
    """Redirect the browser to Twitch's OAuth consent screen."""
    _capture_ref(request)
    from src.auth.twitch_oauth import authorization_url
    if not settings.twitch_client_id:
        raise HTTPException(status_code=503, detail="Twitch OAuth not configured")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    if intent == "optout":
        request.session["optout_intent"] = True
    return RedirectResponse(authorization_url(state))


@app.get("/auth/twitch/callback")
async def twitch_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle Twitch OAuth callback, create/find user, store tokens, set session."""
    from src.auth import twitch_oauth, users as user_store
    if error:
        return RedirectResponse("/login?error=twitch_failed")
    if not code or state != request.session.pop("oauth_state", None):
        return RedirectResponse("/login?error=invalid_state")
    try:
        tokens = await twitch_oauth.exchange_code(code)
        tuser  = await twitch_oauth.get_user(tokens["access_token"])
    except Exception as exc:
        log.warning("twitch_oauth_failed", error=str(exc))
        return RedirectResponse("/login?error=twitch_failed")

    # Opt-out flow: verify identity then redirect to confirmation page
    if request.session.pop("optout_intent", False):
        request.session["optout_twitch_id"]      = tuser["id"]
        request.session["optout_twitch_login"]   = tuser["login"]
        request.session["optout_display_name"]   = tuser.get("display_name") or tuser["login"]
        request.session["optout_avatar"]         = tuser.get("avatar_url", "")
        return RedirectResponse("/opt-out/confirm")

    # Read the referral BEFORE session.clear() below wipes it. This is the only
    # moment it exists: the code rode the session cookie out to twitch.tv and
    # back, and the session-fixation clear is three lines away.
    pending_ref = request.session.get("ref")
    # Same window, same reason: an invite code lives in the session only for the
    # round trip to twitch.tv and back, and session.clear() is a few lines away.
    pending_invite = request.session.get("invite")

    is_owner = bool(settings.admin_twitch_id and tuser["id"] == settings.admin_twitch_id)
    user = user_store.upsert_twitch_user(
        twitch_id=tuser["id"],
        login=tuser["login"],
        username=tuser["username"],
        avatar_url=tuser.get("avatar_url", ""),
        access_token=tokens.get("access_token", ""),
        refresh_token=tokens.get("refresh_token", ""),
        expires_in=tokens.get("expires_in", 0),
        is_admin=is_owner,
    )
    if pending_ref:
        # First touch only — set_ref_once refuses to overwrite, so a returning
        # user who arrives through a different link keeps their original
        # attribution.
        if user_store.set_ref_once(user["id"], pending_ref):
            log.info("referral_attributed", user_id=user["id"], ref=pending_ref)
    if pending_invite:
        _redeem_invite(pending_invite, user)

    # Clear any existing session before setting new auth data (session fixation)
    request.session.clear()
    request.session["auth"]                = True
    request.session["user_id"]             = user["id"]
    request.session["username"]            = user["username"]
    request.session["avatar_url"]          = user.get("avatar_url", "")
    request.session["is_admin"]            = user.get("is_admin", False)
    request.session["subscription_status"] = user.get("subscription_status", "none")
    request.session["trial_ends_at"]       = user.get("trial_ends_at", 0)
    return RedirectResponse("/")


def _redeem_invite(code: str, user: dict) -> bool:
    """Apply an invite's membership to a freshly signed-in user.

    Never raises: a bad, spent or expired code must leave the person signed in
    on the free tier rather than bouncing them out of an OAuth flow they just
    completed. The link failing is recoverable; being dumped back at /login
    after connecting Twitch is what makes someone give up.

    An admin is skipped rather than downgraded — they already have everything,
    and spending a use on them would burn the invite for its real recipient.
    """
    from src.auth import invites, users as user_store
    from src.billing import plans
    try:
        if user.get("is_admin"):
            return False
        inv = invites.claim(code, user["id"], user.get("username", ""))
        if not inv or inv.plan not in plans.PAID_PLANS:
            return False
        if inv.days:
            user_store.grant_trial(user["id"], inv.days, inv.plan)
        else:
            user_store.grant_plan(user["id"], inv.plan)
        log.info("invite_redeemed", user_id=user["id"], code=code,
                 plan=inv.plan, days=inv.days)
        return True
    except Exception as exc:
        log.warning("invite_redeem_failed", code=code, error=str(exc))
        return False


@app.get("/i/{code}")
async def invite_link(request: Request, code: str):
    """Public entry point for an invite. Stashes the code and sends the visitor
    into the normal Twitch sign-in; the membership is applied on the way back.

    Deliberately NOT gated: the whole point is that a signed-out stranger clicks
    it. It also never says anything about billing — someone who was told they
    are being given access should not meet a price on the way in.
    """
    from src.auth import invites
    inv = invites.get(code)
    if inv and inv.is_live():
        request.session["invite"] = code
    # An already-signed-in user gets it applied without another OAuth round trip.
    if request.session.get("auth"):
        from src.auth import users as user_store
        uid = request.session.get("user_id", "")
        u = user_store.get_by_id(uid) if uid else None
        if u and inv:
            _redeem_invite(code, u)
            request.session.pop("invite", None)
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/auth/twitch", status_code=302)


@app.get("/me")
async def me(request: Request):
    import math
    from src.auth import users as user_store
    trial_ends_at = request.session.get("trial_ends_at", 0) or 0
    status        = request.session.get("subscription_status", "none")
    trial_days_left = 0
    if status == "trialing" and trial_ends_at:
        trial_days_left = max(0, math.ceil((trial_ends_at - time.time()) / 86400))
    uid  = request.session.get("user_id", "")
    user = user_store.get_by_id(uid) if uid else {}
    from src.billing.plans import get_plan, limits_for
    limits = limits_for(user)
    return {
        "user_id":             uid,
        "username":            request.session.get("username", ""),
        "avatar_url":          request.session.get("avatar_url", ""),
        "is_admin":            request.session.get("is_admin", False),
        "is_labeler":          bool(user.get("is_labeler")),
        "subscription_status": status,
        "trial_ends_at":       trial_ends_at,
        "trial_days_left":     trial_days_left,
        # Membership tier + its limits, so the dashboard can mirror them
        # (enforcement stays backend-side).
        "plan":                get_plan(user),
        "plan_label":          limits["label"],
        "plan_limits":         {"max_streams": limits["max_streams"],
                                "max_pending": limits["max_pending"],
                                "vod": limits["vod"],
                                "uploads": limits["uploads"]},
        # Release flags — what is switched ON for everyone, separate from what
        # this user's plan entitles them to. The dashboard shows an
        # under-construction screen for anything off here.
        # Same flag the review_prompt broadcast carries. The event handles the
        # live case; this handles a tab opened after the milestone was crossed,
        # and a reconnect (refetchAll pulls /me).
        "review_prompt":       _review_prompt_due(uid),
        # Clips deleted by the pending cap in the last 24h. On /me rather
        # than only on the event so the notice survives a reload and a
        # reconnect — the event is the live nudge, this is the state.
        "clips_lost_24h":      _clips_lost_24h(uid),
        # The tier above this one, so the queue-full notice can make the
        # concrete offer on a PAGE LOAD too. Without it the reload path —
        # which is how most people will actually see the notice — fell
        # back to "review some to free up space" and never mentioned
        # upgrading at all.
        "next_plan":           _next_tier(user),
        "features":            {"uploads": settings.uploads_enabled,
                                "clip_import": settings.clip_import_enabled,
                                "captions": settings.captions_enabled,
                                # Exposed so the VOD screen can describe what a
                                # scan actually does. With audio on it decodes
                                # the stream's sound, which the old copy ("no
                                # video download needed", chat signals only)
                                # flatly contradicts.
                                "vod_audio": settings.vod_audio_enabled},
        "twitch_login":        user.get("twitch_login") or (request.session.get("username") if user.get("twitch_id") else None),
        "kick_slug":           user.get("kick_slug") or "",
        "kick_username":       user.get("kick_username") or "",
    }


# ── Kick OAuth ────────────────────────────────────────────────────────────────

@app.get("/auth/kick")
async def kick_login(request: Request, login: bool = False):
    from src.auth.kick_oauth import authorization_url
    ip = request.client.host if request.client else "unknown"
    await _check_kick_oauth_rate(ip)
    if not settings.kick_client_id:
        raise HTTPException(status_code=503, detail="Kick OAuth not configured")
    # Sign-in with Kick is disabled — Twitch is the only sign-in method. Kick may
    # still be LINKED to an existing (Twitch-authenticated) account, so allow the
    # flow only when the user is already logged in.
    if login or not request.session.get("user_id"):
        return RedirectResponse("/login")
    state = secrets.token_urlsafe(16)
    url, code_verifier = authorization_url(state)
    request.session["kick_oauth_state"]    = state
    request.session["kick_code_verifier"]  = code_verifier
    if login:
        request.session["kick_login_flow"] = True
    return RedirectResponse(url)


@app.get("/auth/kick/callback")
async def kick_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    import traceback, urllib.parse as _up
    from src.auth import kick_oauth, users as user_store
    ip = request.client.host if request.client else "unknown"
    await _check_kick_oauth_rate(ip)
    login_flow = request.session.pop("kick_login_flow", False)

    def err_redirect(reason: str):
        # Truncate to avoid leaking internal hostnames/stack details in URL/Referer
        safe = reason[:120]
        msg = _up.quote(safe, safe="")
        if login_flow:
            return RedirectResponse(f"/login?error=kick_failed&kick_detail={msg}")
        return RedirectResponse(f"/?kick_error=1&kick_detail={msg}")

    if error:
        log.warning("kick_callback_oauth_error", error=error)
        return err_redirect(f"Kick returned: {error}")
    expected_state = request.session.pop("kick_oauth_state", None)
    if not code or state != expected_state:
        log.warning("kick_callback_state_mismatch",
                    has_code=bool(code), got_state=state, expected=expected_state)
        return err_redirect("Session expired or state mismatch — please try again")
    code_verifier = request.session.pop("kick_code_verifier", "")
    if not code_verifier:
        log.warning("kick_callback_no_verifier")
        return err_redirect("PKCE verifier missing from session — please try again")
    try:
        tokens = await kick_oauth.exchange_code(code, code_verifier)
        log.info("kick_tokens_received", keys=list(tokens.keys()),
                 scope=tokens.get("scope", "MISSING"), token_type=tokens.get("token_type", "MISSING"))
    except Exception as exc:
        log.error("kick_token_exchange_failed", error=str(exc), tb=traceback.format_exc())
        return err_redirect(f"Token exchange failed: {exc}")
    # Try id_token first (OIDC — has user claims baked in)
    kick_user = None
    if tokens.get("id_token"):
        kick_user = kick_oauth._decode_jwt_user(tokens["id_token"])
        if kick_user and kick_user.get("id"):
            log.info("kick_user_from_id_token", username=kick_user.get("username"))
        else:
            kick_user = None
    if not kick_user:
        try:
            kick_user = await kick_oauth.get_user(tokens["access_token"])
        except Exception as exc:
            log.error("kick_get_user_failed", error=str(exc), tb=traceback.format_exc())
            return err_redirect(f"Could not fetch Kick user: {exc}")

    kick_id  = str(kick_user["id"])
    username = (kick_user.get("username") or "").strip()
    slug     = (kick_user.get("slug") or username).strip()
    avatar   = kick_user.get("avatar_url", "")
    # Some Kick token/introspection payloads omit the username/slug. Refuse rather
    # than persist a blank username (which overwrites good data and breaks clip
    # logic keyed on kick_slug) — the user can retry the link.
    if not username or not slug:
        log.warning("kick_username_missing", kick_id=kick_id)
        return err_redirect("Could not read your Kick username — please try linking again.")
    token_kwargs = dict(
        access_token=tokens.get("access_token", ""),
        refresh_token=tokens.get("refresh_token", ""),
        expires_in=tokens.get("expires_in", 0),
    )

    uid = request.session.get("user_id")
    try:
        if uid:
            # Already logged in — link Kick to existing account
            user_store.link_kick_to_user(
                user_id=uid, kick_id=kick_id, username=username,
                slug=slug, avatar_url=avatar, **token_kwargs,
            )
            log.info("kick_linked", user_id=uid, kick_id=kick_id, username=username)
            return RedirectResponse("/?kick_linked=1")
        else:
            # Sign-in / sign-up via Kick is disabled — Twitch is the only sign-in
            # method. Reaching here without an existing session is a Kick sign-in
            # attempt; refuse rather than create an account. (Linking, above, is
            # still allowed for users already authenticated via Twitch.)
            log.info("kick_signin_blocked", kick_id=kick_id)
            return RedirectResponse("/login?error=kick_signin_disabled")
    except Exception as exc:
        log.error("kick_save_failed", error=str(exc))
        return err_redirect(f"Could not save Kick account: {exc}")


@app.get("/auth/kick/status")
async def kick_status(request: Request):
    """Returns whether the current user has a linked Kick account."""
    from src.auth import users as user_store
    uid = request.session.get("user_id", "")
    db_user = user_store.get_by_id(uid) if uid else None
    connected = bool(db_user and db_user.get("kick_id"))
    slug = db_user.get("kick_slug", "") if db_user else ""
    oauth_configured = bool(settings.kick_client_id and settings.kick_client_secret)
    return {"connected": connected, "kick_slug": slug, "oauth_configured": oauth_configured}


@app.delete("/account", status_code=200)
async def delete_account(request: Request):
    """Permanently delete the authenticated user's account and all associated data."""
    uid = _current_user_id(request)
    from src.auth import users as user_store

    # Grab Stripe customer ID before deleting the record
    db_user = user_store.get_by_id(uid)
    stripe_customer_id = db_user.get("stripe_customer_id") if db_user else None

    async with _data_lock:
        user_clips  = [c for c in list(_clips.values()) if c.get("user_id") == uid]
        stream_keys = [k for k in list(_streams.keys()) if k.startswith(f"{uid}:")]
        for clip in user_clips:
            del _clips[clip["id"]]
            _delete_clip_file(clip)
        for key in stream_keys:
            del _streams[key]
        _save_clips()
        _save_streams()

    if _publish_remove_stream:
        for key in stream_keys:
            channel = key.split(":", 1)[-1]
            try:
                await _publish_remove_stream(channel, uid)
            except Exception:
                pass

    # Cancel any active Stripe subscriptions so the user isn't charged after deletion
    if stripe_customer_id and settings.stripe_secret_key:
        from src.billing.stripe_billing import cancel_customer_subscriptions
        await cancel_customer_subscriptions(stripe_customer_id)

    # Uploaded video is the one thing we actually hold bytes for, so it has to
    # go with the account rather than linger on disk after the user has left.
    from src.uploads import library as upload_lib
    removed_uploads = upload_lib.delete_all_for_user(uid)
    from src.publish import schedule as sched
    sched.delete_all_for_user(uid)
    # Their words go with them. A published quote from a deleted account is
    # someone's name on a marketing page with no way left to withdraw it.
    from src.feedback import reviews as _reviews
    _reviews.delete_all_for_user(uid)
    from src.stats import stream_stats as _ss_purge
    _ss_purge.delete_all_for_user(uid)

    user_store.delete(uid)
    request.session.clear()
    log.info("account_deleted", user_id=uid, uploads_removed=removed_uploads)
    return {"status": "deleted"}


# ── Streamer opt-out ──────────────────────────────────────────────────────────

@app.get("/opt-out", response_class=HTMLResponse)
async def optout_landing():
    return HTMLResponse(_OPTOUT_LANDING_HTML)


@app.get("/opt-out/confirm", response_class=HTMLResponse)
async def optout_confirm_page(request: Request):
    import html as _html
    twitch_id    = request.session.get("optout_twitch_id")
    twitch_login = request.session.get("optout_twitch_login", "")
    display_name = request.session.get("optout_display_name", twitch_login)
    avatar       = request.session.get("optout_avatar", "")
    if not twitch_id:
        return RedirectResponse("/opt-out")
    avatar_section = (
        f'<img class="avatar" src="{_html.escape(avatar)}" alt="">'
        if avatar and avatar.startswith("https://") else
        '<div class="avatar-placeholder"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#c79bff" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg></div>'
    )
    html_out = (
        _OPTOUT_CONFIRM_HTML
        .replace("{avatar_section}", avatar_section)
        .replace("{display_name}",  _html.escape(display_name))
        .replace("{twitch_login}",  _html.escape(twitch_login))
    )
    return HTMLResponse(html_out)


@app.post("/opt-out/confirm")
async def optout_confirm_submit(request: Request):
    twitch_id    = request.session.pop("optout_twitch_id",    None)
    twitch_login = request.session.pop("optout_twitch_login", None)
    display_name = request.session.pop("optout_display_name", None)
    request.session.pop("optout_avatar", None)
    if not twitch_id or not twitch_login:
        return RedirectResponse("/opt-out", status_code=302)
    from src.auth.optout import opt_out
    opt_out(twitch_id, twitch_login, display_name or twitch_login)
    log.info("streamer_opted_out", twitch_id=twitch_id, login=twitch_login)
    return RedirectResponse("/opt-out/success", status_code=302)


@app.get("/opt-out/success", response_class=HTMLResponse)
async def optout_success():
    return HTMLResponse(_OPTOUT_SUCCESS_HTML)


@app.get("/admin/feedback-page", response_class=HTMLResponse)
async def admin_feedback_page(request: Request):
    _require_admin(request)
    return HTMLResponse(_ADMIN_FEEDBACK_HTML)


@app.get("/admin/optout", response_class=HTMLResponse)
async def admin_optout_page(request: Request):
    _require_admin(request)
    return HTMLResponse(_ADMIN_OPTOUT_HTML)


@app.get("/admin/optout/list")
async def admin_optout_list(request: Request):
    _require_admin(request)
    from src.auth.optout import get_all
    return get_all()


@app.delete("/admin/optout/{twitch_id}")
async def admin_optout_remove(request: Request, twitch_id: str):
    _require_admin(request)
    from src.auth.optout import remove_opt_out
    removed = remove_opt_out(twitch_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Not found")
    log.info("optout_removed_by_admin", twitch_id=twitch_id, by=request.session.get("user_id"))
    return {"ok": True}


# ── Feedback ──────────────────────────────────────────────────────────────────

class _FeedbackRequest(BaseModel):
    message: str
    category: str = "general"

_feedback_last_submit: dict[str, float] = {}  # user_id -> last submit time
_FEEDBACK_COOLDOWN = 10  # seconds between submissions per user
_feedback_rate_lock = asyncio.Lock()

@app.post("/feedback", status_code=201)
async def submit_feedback(request: Request, body: _FeedbackRequest):
    uid      = _current_user_id(request)
    username = request.session.get("username", "")
    now = time.time()
    async with _feedback_rate_lock:
        last = _feedback_last_submit.get(uid, 0)
        if now - last < _FEEDBACK_COOLDOWN:
            raise HTTPException(status_code=429, detail="Please wait a moment before sending more feedback")
        _feedback_last_submit[uid] = now
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message is required")
    if len(msg) > 2000:
        raise HTTPException(status_code=400, detail="Message too long (2000 chars max)")
    # Cap total stored feedback per user to prevent unbounded disk growth
    if sum(1 for f in _feedback if f.get("user_id") == uid) >= 200:
        raise HTTPException(status_code=429, detail="Feedback limit reached — thank you, we have plenty from you!")
    _VALID_FEEDBACK_CATEGORIES = {"General", "Bug report", "Feature request", "Question"}
    category = body.category.strip() if body.category else "General"
    if category not in _VALID_FEEDBACK_CATEGORIES:
        category = "General"
    import secrets as _sec
    entry = {
        "id":         _sec.token_urlsafe(12),
        "user_id":    uid,
        "username":   username,
        "category":   category,
        "message":    msg,
        "created_at": time.time(),
        "read":       False,
    }
    async with _data_lock:
        _feedback.append(entry)
        _save_feedback()
    log.info("feedback_submitted", user_id=uid, username=username, category=category)
    return {"ok": True}

@app.get("/admin/feedback")
async def admin_feedback_list(request: Request):
    _require_admin(request)
    return sorted(_feedback, key=lambda f: f["created_at"], reverse=True)

@app.post("/admin/feedback/{feedback_id}/read")
async def admin_feedback_mark_read(request: Request, feedback_id: str):
    _require_admin(request)
    async with _data_lock:
        for f in _feedback:
            if f["id"] == feedback_id:
                f["read"] = True
                _save_feedback()
                return {"ok": True}
    raise HTTPException(status_code=404, detail="Not found")

@app.delete("/admin/feedback/{feedback_id}")
async def admin_feedback_delete(request: Request, feedback_id: str):
    _require_admin(request)
    async with _data_lock:
        idx = next((i for i, f in enumerate(_feedback) if f["id"] == feedback_id), None)
        if idx is None:
            raise HTTPException(status_code=404, detail="Not found")
        _feedback.pop(idx)
        _save_feedback()
    return {"ok": True}

@app.get("/feedback/unread-count")
async def feedback_unread_count(request: Request):
    """Admin-only: number of unread feedback items (used for nav badge)."""
    from src.auth import users as user_store
    uid = request.session.get("user_id", "")
    db_user = user_store.get_by_id(uid) if uid else None
    if not db_user or not db_user.get("is_admin"):
        return {"count": 0}
    return {"count": sum(1 for f in _feedback if not f.get("read"))}


# ── Blind training studio ─────────────────────────────────────────────────────
# Team-only (owner + labelers): humans score clips 1-10 on the dimensions a
# human can actually judge from watching (sentiment, audio, virality) WITHOUT
# seeing the bot's numbers. Chat velocity and keyword hits were dropped from
# the sliders on purpose: they're the least important to calibrate and nearly
# impossible for a human to rate honestly from a 30s clip (you'd be guessing
# at message rates, which just adds noise to the dataset). Historical records
# that include those keys remain valid — the analyzer reads whatever is there. The bot's signal vector is
# joined to each submission SERVER-SIDE at save time, so the human/bot pairing
# exists in the dataset without ever being shown to the scorer. The eventual
# goal: enough paired data to fit the signal weights on human judgment.

_HUMAN_SCORES_FILE = Path(settings.local_storage_path) / "human_scores.jsonl"

# The signal dimensions a labeler scores, matched to the bot's signal keys.
# (Virality is scored too but pairs with bot_virality_score, not a signal.)
_TRAIN_DIMENSIONS = {
    "sentiment": "SENTIMENT",
    "audio":     "AUDIO_SPIKE",
}


def _require_labeler(request: Request) -> str:
    """Trainer gate: admins and users with the labeler flag."""
    from src.auth import users as user_store
    uid = request.session.get("user_id", "")
    db_user = user_store.get_by_id(uid) if uid else None
    if not db_user or not (db_user.get("is_admin") or db_user.get("is_labeler")):
        raise HTTPException(status_code=403, detail="Training access only")
    return uid


def _human_scored_pairs() -> set[tuple[str, str]]:
    """(clip_id, labeler_user_id) pairs already scored — one score per clip
    per labeler."""
    pairs = set()
    try:
        for line in _HUMAN_SCORES_FILE.open(encoding="utf-8"):
            try:
                r = json.loads(line)
                pairs.add((r.get("clip_id", ""), r.get("labeler_id", "")))
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    return pairs


def _blind_clip_view(clip: dict) -> dict:
    """What a labeler is allowed to see: enough to WATCH the clip, nothing
    that leaks the bot's judgment. Excluded on purpose: trigger_score,
    trigger_signals, virality_score, review status, and the generated
    clip_title (titles like 'Chat Erupts' name the bot's dominant signal)."""
    return {
        "id":         clip.get("id"),
        "channel":    clip.get("channel"),
        "game":       clip.get("game") or "",
        "created_at": clip.get("created_at"),
        "duration_seconds": clip.get("duration_seconds"),
        "twitch_url": clip.get("twitch_url") or "",
        "embed_url":  clip.get("embed_url") or "",
    }


def _record_human_score(clip: dict, labeler_id: str, labeler_name: str,
                        scores: dict) -> dict:
    """Build + append one paired training record. `scores` values are 1-10
    ints keyed by _TRAIN_DIMENSIONS keys; the bot's matching signal values and
    overall scores are joined here, server-side."""
    bot_signals = {}
    for s in (clip.get("trigger_signals") or []):
        t = str(s.get("type", "")).replace("SignalType.", "")
        bot_signals[t] = round(float(s.get("value", 0.0) or 0.0), 4)
    human = {k: int(scores[k]) for k in _TRAIN_DIMENSIONS}
    # Virality is scored too — it pairs with bot_virality_score (a separate
    # formula from the trigger), not with any single signal.
    if "virality" in scores:
        human["virality"] = int(scores["virality"])
    record = {
        "ts":            round(time.time(), 1),
        "clip_id":       clip.get("id"),
        "channel":       clip.get("channel"),
        "labeler_id":    labeler_id,
        "labeler":       labeler_name,
        "human":         human,
        "bot_signals":   bot_signals,
        "bot_trigger_score":  clip.get("trigger_score"),
        "bot_virality_score": clip.get("virality_score"),
    }
    _HUMAN_SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _HUMAN_SCORES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


@app.get("/training/queue")
async def training_queue(request: Request):
    """Blind list of this labeler's not-yet-scored clips (their own account's
    clips with a signal vector — VOD moments without signals carry no pairing
    value). Clips already reviewed (approved/rejected) are excluded: the
    labeler has watched and judged those in Clip Review, so they can't be
    scored blind anymore. Oldest first — trainers work chronologically from
    the first clip taken, so the backlog drains in capture order instead of
    newest clips jumping the line."""
    uid = _require_labeler(request)
    scored = _human_scored_pairs()
    queue = [
        _blind_clip_view(c) for c in _clips.values()
        if c.get("user_id") == uid
        and (c.get("trigger_signals") or [])
        and c.get("status") not in ("approved", "rejected")
        and (c.get("id"), uid) not in scored
    ]
    queue.sort(key=lambda c: c.get("created_at") or 0)
    return queue[:100]


class _TrainScoreRequest(BaseModel):
    clip_id: str
    sentiment: int = Field(ge=1, le=10)
    audio:     int = Field(ge=1, le=10)
    virality:  int = Field(ge=1, le=10)


@app.post("/training/score", status_code=201)
async def training_score(request: Request, body: _TrainScoreRequest):
    uid = _require_labeler(request)
    username = request.session.get("username", "")
    clip = _clips.get(body.clip_id)
    if not clip or clip.get("user_id") != uid:
        raise HTTPException(status_code=404, detail="Clip not found")
    if (body.clip_id, uid) in _human_scored_pairs():
        raise HTTPException(status_code=409, detail="You already scored this clip")
    async with _data_lock:
        _record_human_score(clip, uid, username, body.model_dump())
    log.info("human_score_recorded", clip_id=body.clip_id, labeler=username)
    # Realtime: every open Training tab (all trainers) sees the team counter
    # tick live. Global broadcast — the count isn't sensitive, and non-labeler
    # tabs simply forward it to a screen that isn't mounted.
    total = len(_human_scored_pairs())
    await broadcast({"event": "training_scored", "total": total, "labeler": username})
    return {"ok": True, "total": total}


@app.get("/training/stats")
async def training_stats(request: Request):
    """Scoreboard: total paired records and per-labeler counts."""
    _require_labeler(request)
    per: dict[str, int] = {}
    total = 0
    try:
        for line in _HUMAN_SCORES_FILE.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            name = r.get("labeler") or r.get("labeler_id") or "?"
            per[name] = per.get(name, 0) + 1
    except FileNotFoundError:
        pass
    return {"total": total, "by_labeler": per}


@app.post("/admin/users/{user_id}/labeler")
async def admin_set_labeler(request: Request, user_id: str, on: bool = True):
    """Grant/revoke training-studio access (labeler role — NOT admin)."""
    _require_admin(request)
    from src.auth import users as user_store
    if not user_store.set_labeler(user_id, on):
        raise HTTPException(status_code=404, detail="User not found")
    log.info("labeler_set", user_id=user_id, on=on, by=request.session.get("user_id"))
    # Realtime: the user's open tab gains/loses the Training nav item live.
    await broadcast({"event": "roles_updated"}, user_id=user_id)
    return {"ok": True, "is_labeler": on}


@app.post("/admin/users/{user_id}/admin")
async def admin_set_admin(request: Request, user_id: str, on: bool = True):
    """Grant/revoke FULL admin (portal, user management, permanent billing
    bypass). Self-demotion is refused — an owner clicking the wrong row must
    never be able to lock every admin out of the panel."""
    _require_admin(request)
    if not on and user_id == request.session.get("user_id"):
        raise HTTPException(status_code=400,
                            detail="You can't revoke your own admin access")
    from src.auth import users as user_store
    if not user_store.set_admin(user_id, on):
        raise HTTPException(status_code=404, detail="User not found")
    log.info("admin_set", user_id=user_id, on=on, by=request.session.get("user_id"))
    # Realtime: the middleware re-reads is_admin from DB per request, so the
    # promoted user's next /me refetch (triggered by this event) flips their UI.
    await broadcast({"event": "roles_updated"}, user_id=user_id)
    return {"ok": True, "is_admin": on}


# ── Stripe billing ─────────────────────────────────────────────────────────────

def _paywall_copy(kind: str) -> dict:
    """Honest paywall copy — there is no self-serve free trial anymore, so the
    page never promises free days. Variants: 'trial_ended' for users whose
    admin-granted trial ran out, 'returning' for past subscribers, 'new' for
    everyone else."""
    if kind == "trial_ended":
        return {
            "headline": "Your free trial has ended",
            "subline":  ("hope you caught some great moments. Pick a plan to keep the "
                         "clips coming — from $10/month, cancel anytime."),
            "note":     "Have a promo code? Enter it at checkout for 50% off your first month.",
        }
    if kind == "returning":
        return {
            "headline": "Restart your subscription",
            "subline":  ("welcome back. Pick a plan and your subscription starts right "
                         "away — from $10/month, cancel anytime."),
            "note":     "Have a promo code? Enter it at checkout for 50% off your first month.",
        }
    return {
        "headline": "Pick your plan",
        "subline":  ("start capturing your best streaming moments automatically — "
                     "from $10/month, cancel anytime."),
        "note":     "Have a promo code? Enter it at checkout for 50% off your first month.",
    }


@app.get("/billing/paywall", response_class=HTMLResponse)
async def paywall_page(request: Request):
    from src.auth import users as user_store
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    db_user  = user_store.get_by_id(uid) if uid else None
    if db_user and db_user.get("subscription_status") == "expired":
        kind = "trial_ended"
    elif db_user and db_user.get("stripe_customer_id"):
        kind = "returning"
    else:
        kind = "new"
    copy = _paywall_copy(kind)
    import html as _html
    page = (PAYWALL_HTML
            .replace("{username}", _html.escape(username))
            .replace("{headline}", copy["headline"])
            .replace("{subline}",  copy["subline"])
            .replace("{cta_note}",  copy["note"]))
    return HTMLResponse(page)


@app.get("/billing/checkout")
async def billing_checkout(request: Request, plan: str = "pro"):
    """Create a Stripe Checkout session for the chosen tier and redirect.

    Billing starts immediately — no self-serve trial. A user on an app-managed
    admin-granted trial (status 'trialing', no Stripe subscription) is allowed
    through so they can subscribe before the trial runs out. Plan switching
    for ALREADY-subscribed users happens in the Stripe portal, not here (the
    active-subscription guard below sends them into the app).
    """
    from src.billing.stripe_billing import create_checkout_url
    from src.auth import users as user_store
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    if plan not in ("starter", "pro"):
        raise HTTPException(status_code=400, detail="Unknown plan")
    price_id = (settings.stripe_price_id_starter if plan == "starter"
                else settings.stripe_price_id_pro)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"{plan} price not configured")
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    if not uid:
        return RedirectResponse("/login")
    db_user  = user_store.get_by_id(uid)
    # Already subscribed (or mid webhook-latency after paying)? Never open a
    # second checkout — that mints a second Stripe subscription the user can't
    # see or cancel. Send them into the app instead. ('trialing' is app-managed
    # with no Stripe subscription behind it, so it may proceed to checkout —
    # the live-status check below still catches any real Stripe subscription.)
    if db_user and db_user.get("subscription_status") == "active":
        return RedirectResponse("/")
    # Webhook-latency window: the user may have JUST paid (Stripe knows, our DB
    # doesn't yet). Ask Stripe live before selling them a second subscription —
    # and self-heal the DB so they get straight into the app.
    stripe_customer = (db_user or {}).get("stripe_customer_id")
    if stripe_customer:
        from src.billing.stripe_billing import live_subscription_status
        live = await live_subscription_status(stripe_customer)
        if live in ("active", "trialing"):
            user_store.update_subscription(uid, stripe_customer, "active")
            return RedirectResponse("/")
        if live == "past_due":
            # They have a subscription in dunning — fixing the card in the
            # portal is the right move, not stacking a new subscription.
            return RedirectResponse("/billing/portal")
    # Reuse the existing Stripe customer so a re-subscribe stays on one customer
    # (portal / cancel-on-delete / admin sync all key off the stored id).
    url = await create_checkout_url(uid, username, price_id, customer_id=stripe_customer)
    return RedirectResponse(url)


# Shown when Stripe refuses to open a portal session even after the
# self-healing retry in create_portal_url. A raw 500 here strands paying
# users with no way back — this page is the never-a-dead-end fallback.
_PORTAL_ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Billing portal unavailable</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0b12;color:#e8e8f0;font:15px/1.6 'Sora',system-ui,sans-serif;text-align:center}
.card{max-width:420px;padding:40px 32px;background:#14141f;border:1px solid #26263a;border-radius:16px}
h1{font-size:19px;margin:0 0 10px}p{color:#9a9aae;margin:0 0 22px}
a{display:inline-block;padding:11px 22px;border-radius:10px;background:linear-gradient(135deg,#7c3aed,#a855f7);
color:#fff;text-decoration:none;font-weight:600}</style></head><body><div class="card">
<h1>Billing portal is temporarily unavailable</h1>
<p>Your subscription is fine — nothing has changed. Please try again in a few
minutes, or email <b>support@highlightz.app</b> and we'll sort it out.</p>
<a href="/">Back to dashboard</a></div></body></html>"""

# Shown to accounts with access but no Stripe subscription behind it (admin,
# trainer, admin-granted trial). Their old path silently bounced portal →
# checkout → dashboard, which looked like a broken button.
_PORTAL_NO_BILLING_HTML = _PORTAL_ERROR_HTML.replace(
    "<h1>Billing portal is temporarily unavailable</h1>",
    "<h1>No billing on this account</h1>",
).replace(
    "<p>Your subscription is fine — nothing has changed. Please try again in a few\n"
    "minutes, or email <b>support@highlightz.app</b> and we'll sort it out.</p>",
    "<p>This account's access is granted in-app (admin, trainer, or trial) — it "
    "isn't billed through Stripe, so there's no subscription to manage or "
    "cancel. Paying subscribers land in the Stripe billing portal here.</p>",
)


@app.get("/billing/portal")
async def billing_portal(request: Request):
    """Open the Stripe Customer Portal so users can manage / cancel."""
    from src.billing import stripe_billing
    from src.auth import users as user_store
    uid  = _current_user_id(request)
    user = user_store.get_by_id(uid)
    if not user or not user.get("stripe_customer_id"):
        # No Stripe customer behind this account. If it nonetheless has access
        # (admin / trainer / app-managed trial — status is set without Stripe),
        # say so plainly: bouncing them into checkout used to loop straight
        # back to the dashboard, which read as a broken button. Accounts
        # WITHOUT access really are would-be subscribers → send to checkout.
        if user and (user.get("is_admin") or user.get("is_labeler")
                     or user.get("subscription_status") in ("active", "trialing")):
            return HTMLResponse(_PORTAL_NO_BILLING_HTML)
        return RedirectResponse("/billing/checkout")
    try:
        url = await stripe_billing.create_portal_url(user["stripe_customer_id"])
    except Exception as exc:
        log.error("stripe_portal_failed", user=uid, error=str(exc))
        return HTMLResponse(_PORTAL_ERROR_HTML, status_code=502)
    if not url.startswith("https://billing.stripe.com/"):
        log.error("stripe_portal_url_unexpected", url=url[:64])
        return HTMLResponse(_PORTAL_ERROR_HTML, status_code=502)
    return RedirectResponse(url)


@app.get("/billing/success", response_class=HTMLResponse)
async def billing_success(request: Request, session_id: str = ""):
    """Stripe redirects here after successful checkout."""
    # Subscription status is updated by the webhook; refresh from DB.
    from src.auth import users as user_store
    uid  = request.session.get("user_id", "")
    user = user_store.get_by_id(uid) if uid else None
    if user:
        request.session["subscription_status"] = user.get("subscription_status", "none")
    return RedirectResponse("/")


@app.get("/billing/cancel", response_class=HTMLResponse)
async def billing_cancel(request: Request):
    return RedirectResponse("/billing/paywall")


# Stripe webhook idempotency: track processed event IDs for 10 minutes to
# reject replays within Stripe's 5-minute signature tolerance window.
_stripe_processed: dict[str, float] = {}
_STRIPE_EVENT_TTL  = 600
_stripe_event_lock = asyncio.Lock()


@app.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Receive and process Stripe subscription lifecycle events."""
    from src.billing.stripe_billing import handle_webhook_event, sync_subscription_event
    from src.auth import users as user_store
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = handle_webhook_event(payload, sig_header)
    except Exception as exc:
        log.warning("stripe_webhook_invalid", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Reject duplicate deliveries — lock guards the check-then-insert atomically
    now = time.time()
    event_id = event.get("id", "")
    return await _process_stripe_event(event, now, event_id)


def apply_subscription_event(user_id: str | None, cust_id: str, status: str) -> str | None:
    """Apply a verified Stripe subscription event to the user store and return
    the id of the user actually affected (None if no user matched).

    Cross-check: metadata user_id must match that user's stored customer. On a
    mismatch (stale/orphaned customer, or tampered metadata) the update is
    applied strictly BY CUSTOMER, and the affected user is whoever owns that
    customer — never the metadata user, whose current subscription may be
    healthy and must not be touched."""
    from src.auth import users as user_store
    if user_id:
        db_user = user_store.get_by_id(user_id)
        stored_cust = db_user.get("stripe_customer_id") if db_user else None
        if stored_cust and stored_cust != cust_id:
            log.warning("stripe_webhook_customer_mismatch",
                        webhook_customer=cust_id, stored_customer=stored_cust,
                        metadata_user=user_id)
            return user_store.update_subscription_by_customer(cust_id, status)
        user_store.update_subscription(user_id, cust_id, status)
        return user_id
    return user_store.update_subscription_by_customer(cust_id, status)


async def _process_stripe_event(event: dict, now: float, event_id: str):
    from src.billing.stripe_billing import sync_subscription_event
    async with _stripe_event_lock:
        stale = [k for k, ts in list(_stripe_processed.items()) if now - ts > _STRIPE_EVENT_TTL]
        for k in stale:
            _stripe_processed.pop(k, None)
        if event_id and event_id in _stripe_processed:
            log.info("stripe_webhook_duplicate", event_id=event_id)
            return {"received": True}
        if event_id:
            _stripe_processed[event_id] = now

    cust_id, user_id, status = sync_subscription_event(event)
    if cust_id and status:
        # Resolve the user this event ACTUALLY affects. On a customer mismatch
        # (metadata names user X but X's stored customer is different — e.g. a
        # stale/orphaned subscription cancelling after the user re-subscribed
        # under a new customer) we must NOT act on the metadata user: their
        # current subscription is fine, and stopping their streams / changing
        # their status would punish them for an old customer's lifecycle event.
        user_id = apply_subscription_event(user_id, cust_id, status)
        log.info("stripe_subscription_updated", customer=cust_id, status=status,
                 affected_user=user_id or "none")
        # Kill active streams immediately when subscription lapses — don't wait
        # for idle reaper — and tell the open tab (realtime contract: the lapse
        # must reach the user live, mirroring admin revoke).
        if status not in ("active", "trialing") and user_id:
            # Down to free, not out. Only the streams beyond the free limit stop.
            asyncio.create_task(_enforce_stream_limit(user_id))
            await broadcast(
                {"event": "subscription_expired",
                 "message": "Your subscription has ended. You are on the free "
                            "plan now — one stream, and your clips are still here."},
                user_id=user_id,
            )
        elif status in ("active", "trialing") and user_id:
            from src.billing.stripe_billing import (
                extract_promo_id, resolve_promo_code, extract_price_id, plan_for_price,
                customer_email, refund_and_cancel_subscription)
            from src.auth import users as user_store
            # Duplicate-signup guard: the same billing email must not pay for
            # two accounts. Twitch login gives us no email, so the first time
            # we learn it is HERE, post-payment — which is why the remediation
            # is refund-then-cancel (refund first: a customer who paid and
            # lost access is the one unacceptable outcome; if the refund
            # fails, access stays and the case is logged for manual handling).
            # Only NEW subscriptions are checked — renewals/updates of a
            # long-standing subscription are never touched.
            email = await customer_email(cust_id)
            if email:
                user_store.set_email(user_id, email)
                if event.get("type") == "customer.subscription.created":
                    dup = user_store.find_other_active_with_email(email, user_id)
                    if dup:
                        log.warning("duplicate_email_signup_blocked",
                                    user_id=user_id, existing_user=dup["id"], email=email)
                        if await refund_and_cancel_subscription(event["data"]["object"]):
                            user_store.update_subscription(user_id, cust_id, "inactive")
                            await broadcast(
                                {"event": "subscription_expired",
                                 "message": ("This email already has an active Highlightz "
                                             "subscription on another account — this signup "
                                             "was canceled and refunded.")},
                                user_id=user_id,
                            )
                            return {"received": True}
                        log.error("duplicate_email_remediation_failed_keeping_access",
                                  user_id=user_id, existing_user=dup["id"])
            # Membership tier: map the subscription's price id to a plan and
            # store it — this is how upgrades/downgrades through the portal
            # take effect. Unknown prices leave the stored plan untouched
            # (legacy $15 maps to 'pro' inside plan_for_price).
            plan = plan_for_price(extract_price_id(event))
            if plan:
                user_store.set_plan(user_id, plan)
            # Promo attribution: if this subscription carries a promotion code
            # (streamer partnership / discount), record which code brought the
            # signup. Best-effort — never blocks webhook processing.
            promo_id = extract_promo_id(event)
            if promo_id:
                code = await resolve_promo_code(promo_id)
                if code:
                    user_store.set_promo_code(user_id, code)
                    log.info("promo_signup_attributed", user_id=user_id, code=code)
            # Realtime contract: a new/recovered subscription must clear the
            # paywall/trial banner in any open tab live, mirroring the
            # subscription_expired broadcast on the lapse path.
            await broadcast(
                {"event": "subscription_active",
                 "message": "Subscription active — you're all set."},
                user_id=user_id,
            )
    return {"received": True}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/clips")
async def list_clips(request: Request, status: str | None = None, channel: str | None = None):
    uid = _current_user_id(request)
    clips = [c for c in _clips.values() if c.get("user_id") == uid]
    if status:
        clips = [c for c in clips if c.get("status") == status]
    if channel:
        clips = [c for c in clips if c.get("channel") == channel]
    clips.sort(key=lambda c: c.get("created_at", 0), reverse=True)
    return clips


# MUST STAY ABOVE @app.get("/clips/{clip_id}") — FastAPI resolves in
# declaration order, so a literal /clips/undo declared after the
# parameterised route is matched as clip_id="undo" and 404s.
@app.get("/clips/undo")
async def undoable(request: Request):
    """What the user could still take back, for the toast to render."""
    uid = _current_user_id(request)
    entry = undo.peek(uid, on_drop=_drop_undo_entry)
    return entry.public() if entry else {}


@app.post("/clips/undo")
async def undo_last(request: Request, entry_id: str | None = None):
    """Put back the clips from the last destructive action, and un-teach it.

    Restoring the clips is the easy half. The half that matters is the profile:
    a reject raises that channel's trigger threshold and trims the weights of
    whichever signals fired, so an accidental bulk reject leaves the detector
    measurably worse on that channel. The profile's scoring state is written
    back from a snapshot taken before the nudge rather than by subtracting the
    step off again — record_clip clamps at both ends, so the arithmetic is not
    reversible but the snapshot is exact.
    """
    uid   = _current_user_id(request)
    entry = undo.pop(uid, entry_id, on_drop=_drop_undo_entry)
    if not entry:
        raise HTTPException(status_code=404, detail="Nothing left to undo")

    async with _data_lock:
        restored = []
        for clip in entry.clips:
            if clip["id"] in _clips:
                continue                      # already back; never double-add
            _clips[clip["id"]] = clip
            restored.append(clip)
        if restored:
            _save_clips()

    from src.profiles.manager import get_profile_manager
    pm = get_profile_manager(uid)
    for channel, snap in entry.profiles_before.items():
        profile = await pm.load(channel)
        if profile:
            undo.restore_profile(profile, snap)
            await pm.save(profile)
            await broadcast({"event": "profile_updated",
                             "profile": profile.to_dict()}, user_id=uid)

    # The ledger is append-only telemetry, so the reject rows stay. This marks
    # them as taken back rather than rewriting history — the count of undone
    # actions is then available to anything that wants to correct for it.
    from src.stats import stream_stats
    for clip in restored:
        if not _is_grabbed(clip):
            stream_stats.record(stream_stats.UNDONE, clip)

    for clip in restored:
        await broadcast({"event": "clip_ready", "clip": clip}, user_id=uid)

    log.info("undo_applied", uid=uid, kind=entry.kind, restored=len(restored))
    return {"restored": len(restored), "kind": entry.kind}


@app.get("/clips/{clip_id}")
async def get_clip(request: Request, clip_id: str):
    uid  = _current_user_id(request)
    clip = _clips.get(clip_id)
    if not clip or clip.get("user_id") != uid:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip


@app.post("/clips/{clip_id}/approve")
async def approve_clip(request: Request, clip_id: str):
    from src.profiles.manager import get_profile_manager
    uid = _current_user_id(request)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        clip["status"] = "approved"
        # WHEN it entered the library, which is not when it was captured. The
        # library sorts on this: a clip caught on Tuesday and approved today
        # belongs at the top today, because "what did I just keep" is the
        # question that screen answers. Clips approved before this field
        # existed have no value and fall back to created_at, which preserves
        # their existing order relative to each other and puts every new
        # approval above them.
        clip["approved_at"] = time.time()
        _save_clips()
    from src.profiles import training_log
    # A grabbed clip was never scored by us for this user — see _is_grabbed.
    if not _is_grabbed(clip):
        training_log.log_outcome(clip, training_log.APPROVED)
        from src.stats import stream_stats
        stream_stats.record(stream_stats.APPROVED, clip)
    await broadcast({"event": "clip_updated", "clip": clip}, user_id=uid)
    pm      = get_profile_manager(uid)
    # load() (not cache-only get()) so the approval is always recorded — even if
    # the channel isn't currently being monitored (e.g. reviewing a clip after a
    # deploy or once the stream ended). A cache-miss here used to silently drop
    # the feedback, skewing learning toward rejections only.
    profile = await pm.load(clip["channel"])
    # Never teach a channel's profile from a grabbed clip: the formula did not
    # produce it, so the decision says nothing about whether the formula was
    # right, and it would drift that channel's threshold on borrowed evidence.
    if profile and not _is_grabbed(clip):
        profile.record_clip(approved=True, signals=clip.get("trigger_signals", []))
        await pm.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    await _maybe_prompt_review(uid)
    return clip


def _drop_undo_entry(entry) -> None:
    """An entry has fallen out of the buffer — now the files can really go."""
    for url in entry.held_files:
        _delete_clip_file({"storage_url": url})


def _hold_files(clips: list[dict]) -> list[str]:
    """storage_urls to keep on disk while the action is still undoable.

    Deleting the .mp4 at reject time would make undo restore a record pointing
    at a file that no longer exists — a clip that looks fine in the grid and
    plays nothing. Twitch-hosted clips have no storage_url and are unaffected.
    """
    return [c["storage_url"] for c in clips if c.get("storage_url")]


@app.post("/clips/{clip_id}/reject")
async def reject_clip(request: Request, clip_id: str):
    from src.profiles.manager import get_profile_manager
    uid = _current_user_id(request)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        del _clips[clip_id]
        _save_clips()
    from src.profiles import training_log
    if not _is_grabbed(clip):
        training_log.log_outcome(clip, training_log.REJECTED)
        from src.stats import stream_stats
        stream_stats.record(stream_stats.REJECTED, clip)
    # File deletion is deferred to when the undo entry expires — unlinking the
    # .mp4 now would let undo restore a record pointing at nothing.
    pm      = get_profile_manager(uid)
    # load() (not cache-only get()) so the rejection is always recorded, matching
    # the approve path — see note there.
    profile = await pm.load(clip["channel"])
    # Never teach a channel's profile from a grabbed clip: the formula did not
    # produce it, so the decision says nothing about whether the formula was
    # right, and it would drift that channel's threshold on borrowed evidence.
    profiles_before = {}
    if profile and not _is_grabbed(clip):
        # Snapshot first: record_clip clamps the threshold and every weight, so
        # subtracting the step back off later would not always land where we
        # started. This is what makes an accidental reject fully reversible —
        # including the damage it does to the channel's detector.
        profiles_before[clip["channel"]] = undo.snapshot_profile(profile)
        profile.record_clip(approved=False, signals=clip.get("trigger_signals", []))
        await pm.save(profile)

    # PUSH BEFORE BROADCAST. clip_removed is what makes the tab ask what it can
    # undo, so the entry has to already be there when it asks. Broadcasting
    # first left a window — here, a profile load and save — in which the answer
    # was "nothing", and the undo offer was silently lost.
    undo.push(undo.UndoEntry(
        user_id=uid, kind="reject", label="Rejected 1 clip",
        clips=[clip], profiles_before=profiles_before,
        held_files=_hold_files([clip])), on_drop=_drop_undo_entry)

    await broadcast({"event": "clip_removed", "clip_id": clip_id}, user_id=uid)
    if profiles_before:
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    return {"status": "deleted", "clip_id": clip_id}


class BulkCullBody(BaseModel):
    min_score: float = 50.0   # clips with score < this are removed


@app.delete("/clips/{clip_id}", status_code=204)
async def delete_clip_endpoint(request: Request, clip_id: str):
    """Housekeeping delete — remove a clip WITHOUT teaching the formula.

    Deliberately separate from /reject: rejecting means "I watched this and
    it's bad" (raises the channel threshold, trims signal weights, logs a
    REJECTED training example). Deleting/culling just tidies the library —
    the user never judged the clip, so it must carry no learning signal at
    all. The frontend's Delete button used to call /reject, silently
    punishing the formula for every cleanup."""
    uid = _current_user_id(request)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        _clips.pop(clip_id)
        _save_clips()
    undo.push(undo.UndoEntry(
        user_id=uid, kind="delete", label="Deleted 1 clip",
        clips=[clip], held_files=_hold_files([clip])), on_drop=_drop_undo_entry)
    await broadcast({"event": "clip_removed", "clip_id": clip_id}, user_id=uid)


@app.post("/clips/clear-pending")
async def clear_pending_clips(request: Request):
    """Empty the review queue without judging anything in it.

    WHY THIS IS NOT "REJECT ALL". Rejecting means "I watched this and it was
    bad": it raises the channel's trigger threshold, trims that clip's signal
    weights, writes a REJECTED training example, and counts against the keep
    rate a streamer is shown. A user who skips a backlog to get fresh clips has
    made none of those statements — punishing the formula for their impatience
    would teach it the wrong lesson from the one action most likely to be taken
    in bulk.

    So this follows the same rule as DELETE /clips/{id} and bulk-cull: remove
    the clip, touch nothing that learns. The only ledger entry is CLEARED,
    which exists so the caught-vs-outcomes books still balance without the
    clips being counted as rejections or as queue evictions.

    PENDING ONLY. Approved clips are the user's library, not their inbox — a
    button labelled "clear the queue" must never reach into it.
    """
    uid = _current_user_id(request)
    removed: list[dict] = []
    async with _data_lock:
        for clip_id, clip in list(_clips.items()):
            if clip.get("user_id") != uid or clip.get("status") != "pending":
                continue
            removed.append(_clips.pop(clip_id))
        if removed:
            _save_clips()

    from src.stats import stream_stats
    for clip in removed:
        # File deletion deferred until the undo entry expires.
        # Grabbed clips never taught the formula, and they do not belong in the
        # channel's outcome ledger either — same carve-out as reject/approve.
        if not _is_grabbed(clip):
            stream_stats.record(stream_stats.CLEARED, clip)

    # Before the broadcasts, not after — clip_removed is what prompts the tab to
    # ask what it can undo, and the entry has to exist by then.
    if removed:
        undo.push(undo.UndoEntry(
            user_id=uid, kind="clear",
            label=f"Cleared {len(removed)} clip" + ("" if len(removed) == 1 else "s"),
            clips=removed, held_files=_hold_files(removed)), on_drop=_drop_undo_entry)

    for clip in removed:
        # Realtime contract: every open tab drops the clip immediately. Reusing
        # clip_removed means no new event name and no new frontend branch.
        await broadcast({"event": "clip_removed", "clip_id": clip["id"]}, user_id=uid)

    log.info("clips_cleared", uid=uid, removed=len(removed))
    return {"removed": len(removed)}


@app.post("/clips/bulk-cull")
async def bulk_cull_clips(request: Request, body: BulkCullBody):
    """Remove all clips for the current user whose score is below min_score."""
    uid = _current_user_id(request)
    min_score = max(0.0, min(100.0, body.min_score))

    to_remove = []
    async with _data_lock:
        for clip_id, clip in list(_clips.items()):
            if clip.get("user_id") != uid:
                continue
            score = float(clip.get("score") or clip.get("trigger_score", 0))  # VOD clips store 'score'; live clips store 'trigger_score'
            if score < min_score:
                to_remove.append(clip_id)
        culled = []
        for clip_id in to_remove:
            culled.append(_clips.pop(clip_id))
        if to_remove:
            _save_clips()

    # Before the broadcasts — see the note in reject_clip.
    if culled:
        undo.push(undo.UndoEntry(
            user_id=uid, kind="cull",
            label=f"Culled {len(culled)} clip" + ("" if len(culled) == 1 else "s"),
            clips=culled, held_files=_hold_files(culled)), on_drop=_drop_undo_entry)

    for clip_id in to_remove:
        await broadcast({"event": "clip_removed", "clip_id": clip_id}, user_id=uid)

    log.info("bulk_cull", uid=uid, removed=len(to_remove), min_score=min_score)
    return {"removed": len(to_remove), "min_score": min_score}



@app.get("/profiles")
async def list_profiles(request: Request):
    from src.profiles.manager import get_profile_manager
    uid      = _current_user_id(request)
    pm       = get_profile_manager(uid)
    profiles = await pm.all_profiles()
    return [p.to_dict() for p in profiles]


@app.get("/profiles/{channel}")
async def get_profile(request: Request, channel: str):
    channel = _clean_channel(channel)
    from src.profiles.manager import get_profile_manager
    uid     = _current_user_id(request)
    pm      = get_profile_manager(uid)
    profile = await pm.get(channel)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.to_dict()


class StreamRequest(BaseModel):
    channel:         str = Field(min_length=1, max_length=64)
    platform:        str = "twitch"
    preset:          str = "default"

    @field_validator("channel")
    @classmethod
    def clean_channel(cls, v: str) -> str:
        if not _CHANNEL_RE.fullmatch(v):
            raise ValueError("Channel name must be 1–64 alphanumeric/underscore/hyphen characters")
        return v.lower()

    @field_validator("platform")
    @classmethod
    def valid_platform(cls, v: str) -> str:
        if v not in _VALID_PLATFORMS:
            raise ValueError(f"platform must be one of {_VALID_PLATFORMS}")
        return v

    @field_validator("preset")
    @classmethod
    def valid_preset(cls, v: str) -> str:
        if v not in _VALID_PRESETS:
            raise ValueError(f"preset must be one of {_VALID_PRESETS}")
        return v


@app.get("/streams")
async def list_streams(request: Request):
    uid = _current_user_id(request)
    return [s for s in _streams.values() if s.get("user_id") == uid]


# Top-live-channels zero-state for the add-stream box, cached so focusing the
# input doesn't burn Helix rate limit — one upstream call per TTL for everyone.
_POPULAR_STREAMS_TTL = 300
_popular_streams_cache: tuple[float, list] = (0.0, [])


@app.get("/streams/suggest")
async def stream_suggestions(request: Request, q: str = ""):
    """Suggestions for the add-stream box, so users don't have to type exact
    channel names. With q: Twitch partial-name search. Without q (zero state):
    the user's previously monitored channels (their profile files, newest
    activity first) + the most-watched live channels right now. Channels the
    user already monitors are filtered out of every list."""
    from src.output import twitch_clips
    uid = _current_user_id(request)
    monitored = {(s.get("channel") or "").lower() for s in _streams.values()
                 if s.get("user_id") == uid}
    q = q.strip()
    if q:
        rows = await twitch_clips.search_channels(q)
        return {"results": [r for r in rows if r["login"].lower() not in monitored]}
    recent = []
    pdir = Path(settings.local_storage_path) / "profiles" / uid
    try:
        files = sorted(pdir.glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        recent = [p.stem for p in files if p.stem.lower() not in monitored][:8]
    except OSError:
        pass
    global _popular_streams_cache
    ts, popular = _popular_streams_cache
    if time.time() - ts > _POPULAR_STREAMS_TTL:
        popular = await twitch_clips.get_top_streams()
        if popular:   # keep serving the stale list through a Helix hiccup
            _popular_streams_cache = (time.time(), popular)
    return {"recent": recent,
            "popular": [p for p in popular
                        if p["login"].lower() not in monitored][:8]}


# Fraction of the global pool held back so somebody with NO streams can always
# start one. Below this much headroom the pool is "tight" and heavy users get
# cut back first.
_CAPACITY_RESERVE_FRAC = 0.15
# Log a warning once utilisation passes this, so the owner finds out the box is
# filling up before a customer does.
_CAPACITY_WARN_FRAC    = 0.80


def _check_server_capacity(uid: str) -> None:
    """Guard the box's total stream count, fairly.

    max_concurrent_streams is a REAL resource guard — every worker holds a chat
    socket, an evaluation loop and (with audio detection on) a streamlink and
    an ffmpeg subprocess, on one vCPU. It is not a number to raise casually.

    The problem was never the cap, it was who it refused. A flat
    `len(_streams) >= cap` is first-come-first-served, so two Pro users at ten
    channels each fill the entire pool and the THIRD customer is refused their
    very first stream — while the page sells "10 channels at once". The person
    turned away is the one using nothing.

    So: anyone with no streams may always start one while the pool is not
    literally full, and once headroom drops into the reserve, the users already
    above their fair share are the ones told to wait. Capacity still degrades,
    but it degrades onto the heaviest user instead of the newest.
    """
    cap   = max(1, settings.max_concurrent_streams)
    total = len(_streams)

    if total >= cap:
        log.error("server_capacity_full", total=total, cap=cap)
        raise HTTPException(
            status_code=503,
            detail="The server is at capacity right now. Try again in a few "
                   "minutes — this is our limit, not your plan's.",
        )

    if total >= int(cap * _CAPACITY_WARN_FRAC):
        log.warning("server_capacity_high", total=total, cap=cap,
                    users=len({k.split(":", 1)[0] for k in _streams}))

    if cap - total > max(1, int(cap * _CAPACITY_RESERVE_FRAC)):
        return                      # plenty of room; plan limits govern

    mine  = sum(1 for k in _streams if k.startswith(f"{uid}:"))
    users = len({k.split(":", 1)[0] for k in _streams}) or 1
    fair  = max(1, cap // users)
    # Somebody with no streams is never caught here: `fair` is at least 1, so
    # `mine >= fair` cannot hold at zero. That is the guarantee — a new customer
    # always gets their first channel while any room exists — and it falls out
    # of the arithmetic rather than needing a special case. An explicit
    # `if mine == 0: return` used to sit above this; it never changed an
    # outcome, and a dead branch that looks load-bearing is worse than none.
    if mine >= fair:
        raise HTTPException(
            status_code=429,
            detail=f"The server is nearly full, so channel slots are being "
                   f"shared out — you have {mine}. Remove one to add another, "
                   f"or try again shortly.",
        )


async def _auto_preset_for(channel: str) -> str:
    """Pick a preset from the channel's live category and size, or "default".

    Fails soft in every direction: an offline channel, a Twitch hiccup or a
    category we have no opinion about all return "default", which is exactly
    what the user would have got anyway. Adding a stream must never fail
    because a nicety could not be computed.
    """
    from src.trigger.rules import auto_preset
    try:
        from src.ingestion.platform.twitch import TwitchPlatform
        platform = TwitchPlatform()
        try:
            info = await asyncio.wait_for(platform.get_stream_info(channel), timeout=6.0)
        finally:
            close = getattr(platform, "close", None)
            if close:
                await close()
    except Exception as exc:
        # Almost always "channel is not live" — get_stream_info raises for an
        # offline channel, and Twitch has no category or viewer count to give
        # for one. Not a failure: the worker re-resolves at go-live.
        log.info("preset_auto_skipped", channel=channel, reason=str(exc)[:120])
        return "default"

    chosen = auto_preset(getattr(info, "game", "") or "",
                         getattr(info, "viewer_count", 0) or 0)
    # Logged on EVERY outcome, including "default". The previous version logged
    # only when it picked something else, so the most common result left no
    # trace at all and an empty journal was indistinguishable from a feature
    # that was never deployed — which is exactly how it read when we went
    # looking. The inputs are logged too, so a bad pick can be explained
    # without reproducing it.
    log.info("preset_auto_selected", channel=channel, preset=chosen,
             game=getattr(info, "game", ""),
             viewers=getattr(info, "viewer_count", 0))
    return chosen


@app.post("/streams", status_code=201)
async def add_stream(request: Request, req: StreamRequest):
    uid        = _current_user_id(request)
    # Kick is temporarily closed off while automated clipping is built — the public
    # Kick API has no clip-creation endpoint, so block new Kick streams rather than
    # silently failing to clip. (UI shows an "under construction" prompt to match.)
    if req.platform == "kick":
        raise HTTPException(
            status_code=503,
            detail="Kick support is under construction — automated Kick clipping is coming soon.",
        )
    from src.auth.optout import is_opted_out
    if req.platform == "twitch" and is_opted_out(req.channel):
        raise HTTPException(status_code=403, detail=f"{req.channel} has opted out of clipping on Highlightz")
    # AUTO-PRESET, resolved before the lock — this makes a Twitch call, and
    # awaiting a network round-trip while holding _data_lock would stall every
    # other clip in the pipeline.
    #
    # WHY IT EXISTS. The dropdown defaults to "default" and most people never
    # touch it, so the per-genre tuning in rules.py almost never reached the
    # streams it was written for. The group that failed hardest were small
    # channels, whose thin chat is exactly what the "small" preset compensates
    # for. Twitch returns the category and the concurrent viewer count in the
    # same lookup we already need, so pick for them.
    #
    # ONLY when the user left it on "default": an explicit choice is a decision
    # and must never be silently overridden.
    preset = req.preset
    if preset == "default" and req.platform == "twitch":
        preset = await _auto_preset_for(req.channel)

    stream_key = f"{uid}:{req.channel}"
    async with _data_lock:
        if stream_key in _streams:
            raise HTTPException(status_code=409, detail="Stream already registered")
        # Per-plan stream limit (Starter 3 / Pro 10) — the backend is the
        # authority; the dashboard only mirrors the number.
        from src.billing.plans import limits_for, get_plan
        from src.auth import users as user_store
        db_user = user_store.get_by_id(uid)
        limits  = limits_for(db_user)
        user_streams = [s for s in _streams.values() if s.get("user_id") == uid]
        if len(user_streams) >= limits["max_streams"]:
            plan = get_plan(db_user)
            # The moment a free user hits this is the moment upgrading means
            # something concrete to them, so name the next tier rather than
            # just refusing.
            upgrade = {"free": " Starter ($10/mo) gives you 3 streams, Pro gives 10.",
                       "starter": " Upgrade to Pro for up to 10 streams."}.get(plan, "")
            raise HTTPException(
                status_code=429,
                detail=f"Stream limit reached ({limits['max_streams']} max on your plan)."
                       f" Remove a stream to add a new one.{upgrade}",
            )
        _check_server_capacity(uid)
        record = {
            "channel":         req.channel,
            "platform":        req.platform,
            "preset":          preset,
            "status":          "starting",
            "user_id":         uid,
            # Needed by _enforce_stream_limit to decide which streams survive a
            # downgrade. Records written before this field existed sort as 0,
            # i.e. oldest, which is the right side of the line to be on.
            "added_at":        time.time(),
        }
        _streams[stream_key] = record
        _save_streams()
    await broadcast({"event": "stream_added", "stream": record}, user_id=uid)
    if _publish_new_stream:
        await _publish_new_stream(req.channel, req.platform, preset, uid)
    return record


@app.delete("/streams/{channel}", status_code=204)
async def remove_stream(request: Request, channel: str):
    uid        = _current_user_id(request)
    channel    = _clean_channel(channel)
    stream_key = f"{uid}:{channel}"
    async with _data_lock:
        if stream_key not in _streams:
            raise HTTPException(status_code=404, detail="Stream not found")
        del _streams[stream_key]
        _save_streams()
    await broadcast({"event": "stream_removed", "channel": channel}, user_id=uid)
    if _publish_remove_stream:
        await _publish_remove_stream(channel, uid)


async def stop_stream_internal(channel: str, uid: str) -> bool:
    """Stop monitoring one stream from BACKEND code (no HTTP request).

    Same three steps the DELETE endpoint performs — drop it, tell the user's
    open tabs, stop the worker — so a stream stopped by the system looks
    exactly like one the user removed. Returns False when it was already gone.

    Used when a channel turns out to be permanently unclippable: leaving it
    running would burn a stream slot (plans cap at 3 or 10) forever on a
    channel that can never produce a clip.
    """
    channel    = _clean_channel(channel)
    stream_key = f"{uid}:{channel}" if uid else channel
    async with _data_lock:
        if stream_key not in _streams:
            return False
        del _streams[stream_key]
        _save_streams()
    # Realtime contract: the tab showing this stream must drop it live.
    await broadcast({"event": "stream_removed", "channel": channel}, user_id=uid)
    if _publish_remove_stream:
        await _publish_remove_stream(channel, uid)
    log.info("stream_stopped_internal", channel=channel, user_id=uid)
    return True


async def _enforce_stream_limit(uid: str) -> int:
    """Trim a user down to what their CURRENT plan allows, newest first.

    This replaces "stop everything" on a lapse. Before the free tier a lapsed
    subscriber was locked out entirely, so killing all their streams was the
    same thing as their access ending. Now lapsing means dropping to free — and
    a free user is entitled to one stream, so stopping all of them would take
    away something they still have a right to.

    Newest-first is deliberate: the channel they added first is the one they
    care most about, and it is the one still running afterwards.
    """
    from src.billing.plans import limits_for
    from src.auth import users as _limit_store
    allowed = limits_for(_limit_store.get_by_id(uid))["max_streams"]

    mine = [(k, v) for k, v in _streams.items() if k.startswith(f"{uid}:")]
    if len(mine) <= allowed:
        return 0
    mine.sort(key=lambda kv: kv[1].get("added_at") or 0)
    excess = mine[allowed:]
    log.info("enforce_stream_limit", user=uid, allowed=allowed,
             had=len(mine), stopping=len(excess))

    removed = []
    async with _data_lock:
        for key, _ in excess:
            stream = _streams.pop(key, None)
            if stream:
                removed.append(stream)
        _save_streams()
    for stream in removed:
        if _publish_remove_stream:
            try:
                await _publish_remove_stream(stream["channel"], uid)
            except Exception as exc:
                log.warning("enforce_stream_limit_failed",
                            channel=stream.get("channel"), error=str(exc))
        await broadcast({"event": "stream_removed", "channel": stream["channel"]},
                        user_id=uid)
    return len(removed)


async def _stop_user_streams_now(uid: str) -> None:
    """Immediately stop all stream workers for a user (no grace period)."""
    keys = [k for k in _streams if k.startswith(f"{uid}:")]
    if not keys:
        return
    log.info("stop_user_streams", user=uid, count=len(keys))
    removed = []
    async with _data_lock:
        for key in keys:
            stream = _streams.pop(key, None)
            if stream:
                removed.append(stream)
        _save_streams()
    if _publish_remove_stream:
        for stream in removed:
            try:
                await _publish_remove_stream(stream["channel"], uid)
            except Exception as exc:
                log.warning("stop_user_streams_failed", channel=stream.get("channel"), error=str(exc))



async def idle_stream_reaper() -> None:
    """Background task: stop stream workers for users idle longer than 8 hours,
    and enforce subscription/trial expiry for any user with active streams.

    Runs every 5 minutes.
    """
    from src.auth import users as _reaper_user_store
    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        try:
            now = time.time()
            seen: set[str] = set()
            for stream_key in list(_streams.keys()):
                if ":" not in stream_key:
                    continue
                uid = stream_key.split(":", 1)[0]
                if not uid or uid in seen:
                    continue
                seen.add(uid)

                db_user = _reaper_user_store.get_by_id(uid)
                if db_user:
                    status = db_user.get("subscription_status", "none")
                    trial_ends_at = db_user.get("trial_ends_at", 0)
                    # Enforce trial expiry for users who never hit an HTTP endpoint
                    if status == "trialing" and now >= trial_ends_at:
                        _reaper_user_store.update_subscription(uid, db_user.get("stripe_customer_id"), "expired")
                        log.info("reaper_trial_expired", user=uid)
                        await _enforce_stream_limit(uid)
                        await broadcast({"event": "subscription_expired"}, user_id=uid)
                        continue
                    # Trim a lapsed subscriber to the free allowance. NOT a
                    # shutdown: they keep using the product on free.
                    if status not in ("active", "trialing") and not db_user.get("is_admin"):
                        if await _enforce_stream_limit(uid):
                            log.info("reaper_subscription_lapsed", user=uid, status=status)
                            await broadcast({"event": "subscription_expired"}, user_id=uid)
                        continue

                # Idle timeout — stop if no HTTP activity in 8 hours.
                # A user with a registered stream and NO activity record has
                # not been seen since before the record existed, so fall back
                # to when the stream was added rather than to `now`. Defaulting
                # to now is what let an abandoned stream survive indefinitely:
                # it always looked like the user had just been here.
                fallback = max((s.get("added_at", 0) for s in _streams.values()
                                if s.get("user_id") == uid), default=now) or now
                last_active = _user_last_active.get(uid, fallback)
                if now - last_active > _IDLE_STREAM_TIMEOUT:
                    log.info("idle_stream_reaper_stopping", user=uid,
                             idle_minutes=round((now - last_active) / 60))
                    await _stop_user_streams_now(uid)
                    await broadcast({"event": "streams_paused_idle"}, user_id=uid)
            # Persist the clock on the reaper's own tick, so a restart resumes
            # where it left off instead of granting everyone a fresh 8 hours.
            _save_activity()
        except Exception as exc:
            log.error("idle_stream_reaper_error", error=str(exc))


# ── VOD analysis ─────────────────────────────────────────────────────────────

_vod_jobs: dict[str, dict] = {}          # job_id -> job dict
_vod_tasks: dict[str, asyncio.Task] = {} # job_id -> running task

_VOD_RATE_LOCK  = asyncio.Lock()
_vod_rate_hits: dict[str, tuple[int, float]] = {}
_VOD_MAX        = 3    # max concurrent/recent jobs per user
_VOD_WINDOW     = 300  # 5-minute window

async def _check_vod_rate(uid: str) -> None:
    async with _VOD_RATE_LOCK:
        now = time.time()
        stale = [k for k, (_, ts) in list(_vod_rate_hits.items()) if now - ts > _VOD_WINDOW]
        for k in stale:
            _vod_rate_hits.pop(k, None)
        count, window_start = _vod_rate_hits.get(uid, (0, now))
        if now - window_start > _VOD_WINDOW:
            count, window_start = 0, now
        if count >= _VOD_MAX:
            raise HTTPException(status_code=429, detail="Too many VOD analyses — wait a few minutes")
        _vod_rate_hits[uid] = (count + 1, window_start)


class _VodRequest(BaseModel):
    vod_url: str = Field(min_length=1, max_length=200)
    preset:  str = "default"

    @field_validator("preset")
    @classmethod
    def valid_preset(cls, v: str) -> str:
        if v not in _VALID_PRESETS:
            raise ValueError(f"preset must be one of {_VALID_PRESETS}")
        return v


@app.post("/vod/analyze", status_code=201)
async def start_vod_analysis(request: Request, req: _VodRequest):
    from src.vod.analyzer import parse_vod_id, run_vod_analysis
    from src.billing.plans import limits_for
    from src.auth import users as user_store
    uid = _current_user_id(request)
    if not limits_for(user_store.get_by_id(uid))["vod"]:
        raise HTTPException(
            status_code=403,
            detail="The VOD scanner is a Pro feature — upgrade to scan past broadcasts.",
        )
    await _check_vod_rate(uid)

    vod_id = parse_vod_id(req.vod_url)
    if not vod_id:
        raise HTTPException(status_code=400, detail="Could not extract a valid VOD ID from the URL")

    job_id = str(__import__("uuid").uuid4())
    job = {
        "id":          job_id,
        "vod_id":      vod_id,
        "vod_url":     req.vod_url,
        "preset":      req.preset,
        "user_id":     uid,
        "status":      "running",
        "progress":    0.0,
        "moments":     [],
        "error":       "",
        "created_at":  time.time(),
        "vod_title":   "",
        "channel":     "",
        "duration":    0.0,
        "game":        "",
        "thumbnail_url": "",
    }
    _vod_jobs[job_id] = job

    async def _on_progress(pct: float, meta: dict) -> None:
        job["progress"] = pct
        if meta:
            job.update({k: v for k, v in meta.items()
                        if k in ("vod_title", "channel", "duration", "game", "thumbnail_url")})
        await broadcast({"event": "vod_progress", "job_id": job_id,
                         "progress": pct, **meta}, user_id=uid)

    async def _on_moment(moment: dict) -> None:
        job["moments"].append(moment)
        # Also inject into the main clip queue so it appears in the review screen
        async with _data_lock:
            _clips[moment["id"]] = moment
            _save_clips()
            increment_clip_counter()
        await broadcast({"event": "vod_moment", "job_id": job_id, "moment": moment}, user_id=uid)
        await broadcast({"event": "clip_ready", "clip": moment}, user_id=uid)

    async def _on_done(moments: list) -> None:
        job["status"]   = "done"
        job["progress"] = 100.0
        # Back-fill excitement duration computed by the post-scan pass
        async with _data_lock:
            for m in moments:
                if m["id"] in _clips:
                    for field in ("end_offset_seconds", "excitement_duration_seconds", "end_timestamp"):
                        if field in m:
                            _clips[m["id"]][field] = m[field]
            _save_clips()
        await broadcast({"event": "vod_done", "job_id": job_id,
                         "moment_count": len(moments)}, user_id=uid)
        _vod_tasks.pop(job_id, None)

    async def _on_error(msg: str) -> None:
        job["status"] = "failed"
        job["error"]  = msg
        await broadcast({"event": "vod_error", "job_id": job_id, "error": msg}, user_id=uid)
        _vod_tasks.pop(job_id, None)

    task = asyncio.create_task(
        run_vod_analysis(vod_id, "", req.preset, uid,
                         _on_progress, _on_moment, _on_done, _on_error),
        name=f"vod-{job_id[:8]}",
    )
    _vod_tasks[job_id] = task
    log.info("vod_analysis_started", job_id=job_id, vod_id=vod_id, user_id=uid)
    return job


@app.get("/vod/jobs")
async def list_vod_jobs(request: Request):
    uid = _current_user_id(request)
    return [j for j in _vod_jobs.values() if j.get("user_id") == uid]


@app.get("/vod/jobs/{job_id}")
async def get_vod_job(request: Request, job_id: str):
    uid = _current_user_id(request)
    job = _vod_jobs.get(job_id)
    if not job or job.get("user_id") != uid:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/vod/jobs/{job_id}", status_code=204)
async def cancel_vod_job(request: Request, job_id: str):
    uid = _current_user_id(request)
    job = _vod_jobs.get(job_id)
    if not job or job.get("user_id") != uid:
        raise HTTPException(status_code=404, detail="Job not found")
    task = _vod_tasks.pop(job_id, None)
    if task and not task.done():
        task.cancel()
    _vod_jobs.pop(job_id, None)


# ── Clip Editor library ───────────────────────────────────────────────────────
#
# The user's own video files, held on our disk. Unlike every other clip path in
# this product, these are real bytes rather than a Twitch embed — because
# TikTok and Instagram publishing both require possessing the file, and so does
# any editing. The source is the user's own upload, never a fetch from Twitch,
# so the automated-clipping compliance promise is untouched.
#
# Pro-only, matching the VOD scanner: this is the feature that consumes the
# shared disk, so it stays behind the tier that pays for it.

_UPLOAD_CHUNK = 1024 * 1024      # 1 MB — bounded memory on a 2 GB box

# Twitch clip import: per-user rate limit + a short cache. Helix allows 800
# points/min across EVERY user we serve, so an import screen that re-fetches
# on each render would compete with live clipping for the same budget.
_IMPORT_WINDOW = 60.0
_IMPORT_MAX    = 12              # pages per minute per user
_import_hits: dict[str, tuple[int, float]] = {}
_IMPORT_RATE_LOCK = asyncio.Lock()
_IMPORT_CACHE_TTL = 120.0
_import_cache: dict[tuple[str, str], tuple[float, dict]] = {}


async def _check_import_rate(uid: str) -> None:
    async with _IMPORT_RATE_LOCK:
        now = time.time()
        for k, (_, ts) in list(_import_hits.items()):
            if now - ts > _IMPORT_WINDOW:
                _import_hits.pop(k, None)
        count, window_start = _import_hits.get(uid, (0, now))
        if now - window_start > _IMPORT_WINDOW:
            count, window_start = 0, now
        if count >= _IMPORT_MAX:
            raise HTTPException(status_code=429,
                                detail="Loading clips too quickly — give it a moment.")
        _import_hits[uid] = (count + 1, window_start)


def _require_import_access(uid: str) -> None:
    from src.auth import users as user_store
    user = user_store.get_by_id(uid)
    if not settings.clip_import_enabled and not (user or {}).get("is_admin"):
        raise HTTPException(
            status_code=503,
            detail="Importing Twitch clips isn't available yet — it's coming soon.",
        )
    return user


@app.get("/twitch/clips")
async def list_my_twitch_clips(request: Request, cursor: str = ""):
    """Every clip on the caller's OWN Twitch channel, one page at a time.

    Metadata only, through documented Helix — this is not, and cannot be, a
    path to the video file (see src/maintenance/probe_clip_media.py for why
    that question is closed). Nothing is stored: the list is fetched live and
    cached briefly, because a mirror of Twitch's data goes stale the moment a
    clip is deleted or retitled and there is no reason to own that problem.

    Scoped to the session's own twitch_id, never a client-supplied channel —
    otherwise this becomes a general-purpose "enumerate anyone's clips"
    endpoint running on our Helix budget.
    """
    from src.output.twitch_clips import list_channel_clips
    uid = _current_user_id(request)
    user = _require_import_access(uid)

    twitch_id = (user or {}).get("twitch_id") or ""
    if not twitch_id:
        raise HTTPException(
            status_code=409,
            detail="Connect your Twitch account first to load your clips.",
        )

    key = (twitch_id, cursor)
    hit = _import_cache.get(key)
    if hit and time.time() - hit[0] < _IMPORT_CACHE_TTL:
        return hit[1]

    await _check_import_rate(uid)
    try:
        page = await list_channel_clips(twitch_id, cursor=cursor)
    except Exception as exc:
        log.warning("twitch_clip_import_failed", user_id=uid, error=str(exc))
        raise HTTPException(status_code=502,
                            detail="Couldn't reach Twitch just now — try again in a moment.")

    # Helix sorts by view count, not recency. Say so in the payload so the UI
    # never has to claim an order it doesn't have.
    page["sorted_by"] = "view_count"
    _import_cache[key] = (time.time(), page)
    if len(_import_cache) > 2000:            # bounded: one entry per page seen
        _import_cache.clear()
    return page


def _require_upload_access(uid: str) -> None:
    from src.billing.plans import limits_for
    from src.auth import users as user_store
    user = user_store.get_by_id(uid)
    # Release flag first: while the feature is held back the API must refuse
    # too, not just the UI. Otherwise a direct POST still writes to the shared
    # disk even though nobody can reach the tab.
    if not settings.uploads_enabled and not (user or {}).get("is_admin"):
        raise HTTPException(
            status_code=503,
            detail="The Clip Editor isn't available yet — it's coming soon.",
        )
    if not limits_for(user)["uploads"]:
        raise HTTPException(
            status_code=403,
            detail="The Clip Editor is a Pro feature — upgrade to edit and publish clips.",
        )


@app.get("/uploads")
async def list_uploads(request: Request):
    """This user's uploads plus their quota, in one call.

    Quota travels with the list because the UI shows a usage bar next to it;
    two endpoints would let the two drift apart on screen.
    """
    from src.uploads import library as upload_lib
    uid = _current_user_id(request)
    _require_upload_access(uid)
    return {
        "uploads": [u.public() for u in upload_lib.for_user(uid)],
        "quota":   upload_lib.quota(uid),
    }


@app.post("/uploads", status_code=201)
async def create_upload(request: Request, file: UploadFile = File(...),
                        source: str = "upload"):
    """Accept one video file, streamed to disk with every cap enforced.

    Nothing here trusts the client: not the filename (the stored path is a
    server UUID), not the Content-Type (the container is sniffed from the
    bytes), and not the declared length (the running total is checked as the
    chunks arrive, so an oversized upload is cut off partway rather than after
    it has already filled the disk).
    """
    from src.uploads import library as upload_lib
    uid = _current_user_id(request)
    _require_upload_access(uid)

    async def _chunks():
        while True:
            chunk = await file.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            yield chunk

    try:
        up = await upload_lib.save_stream(uid, file.filename or "clip", _chunks(),
                                          source=source)
    except upload_lib.UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    finally:
        await file.close()

    payload = up.public()
    # Realtime contract: the user's other open tabs must show this without a
    # refresh, so the event carries the whole record rather than a hint to
    # re-fetch.
    await broadcast({"event": "upload_added", "upload": payload,
                     "quota": upload_lib.quota(uid)}, user_id=uid)
    return payload


@app.get("/uploads/{upload_id}/file")
async def get_upload_file(request: Request, upload_id: str):
    """Serve an upload back to its owner for in-page playback.

    `library.get` scopes by owner, so another user's id is a 404 rather than a
    file. The path comes from the stored record (UUID + whitelisted
    extension), never from `upload_id` directly, so the route cannot be walked
    out of the uploads directory.
    """
    from src.uploads import library as upload_lib
    uid = _current_user_id(request)
    up = upload_lib.get(upload_id, uid)
    if not up:
        raise HTTPException(status_code=404, detail="Upload not found")
    path = upload_lib.path_for(up)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Upload file is missing")
    media = {"mp4": "video/mp4", "mov": "video/quicktime",
             "webm": "video/webm"}[up.kind]
    return FileResponse(
        path, media_type=media,
        # inline + a fixed safe type: the browser plays it rather than being
        # invited to sniff it into something executable.
        headers={"Content-Disposition": f'inline; filename="{up.filename}"',
                 "X-Content-Type-Options": "nosniff"},
    )


@app.delete("/uploads/{upload_id}", status_code=200)
async def remove_upload(request: Request, upload_id: str):
    from src.uploads import library as upload_lib
    uid = _current_user_id(request)
    _require_upload_access(uid)
    if not upload_lib.delete(upload_id, uid):
        raise HTTPException(status_code=404, detail="Upload not found")
    # A queued post whose clip is gone is a reminder to do something impossible.
    from src.publish import schedule as sched
    for dropped in sched.drop_upload(upload_id, uid):
        await broadcast({"event": "schedule_removed", "item_id": dropped}, user_id=uid)
    await broadcast({"event": "upload_removed", "upload_id": upload_id,
                     "quota": upload_lib.quota(uid)}, user_id=uid)
    return {"status": "deleted"}


# ── Auto-captions ─────────────────────────────────────────────────────────────
#
# Whisper runs on THIS box, which is also running an audio meter per monitored
# channel on one core. Captioning is serialised to a single clip at a time in
# src/captions/transcribe.py; here we additionally refuse to queue a second job
# for the same user, so one person cannot fill the queue and make everyone
# else — including live clip detection — wait behind them.

_caption_jobs: dict[str, dict] = {}          # upload_id -> {status, pct, error}

# Finished jobs are kept only long enough that a tab reconnecting just after a
# failure can still read the reason; after that they are dead weight. Without
# pruning this dict grew by one entry per caption ever run and never shrank,
# and the per-user "already captioning?" check below scanned all of it.
_CAPTION_JOB_TTL = 900.0        # 15 minutes


def _prune_caption_jobs() -> None:
    now = time.time()
    for uid_key, job in list(_caption_jobs.items()):
        if job.get("status") == "running":
            continue
        if now - job.get("finished_at", 0) > _CAPTION_JOB_TTL:
            _caption_jobs.pop(uid_key, None)


def _require_captions(uid: str):
    from src.auth import users as user_store
    user = user_store.get_by_id(uid)
    if not settings.captions_enabled and not (user or {}).get("is_admin"):
        raise HTTPException(status_code=503,
                            detail="Auto-captions aren't available yet — coming soon.")
    return user


# ── Reviews ───────────────────────────────────────────────────────────────────
#
# Asked after 25 approved clips, because that is when someone has an opinion
# worth having. The trigger is a CLIP COUNT and never a sentiment score:
# soliciting only happy users is review gating, which Google and Trustpilot
# both prohibit and which Trustpilot removes profiles for.

def _next_tier(user: dict | None) -> dict | None:
    """The plan above this user's, with its real numbers. None on Pro — there
    is nothing above it, and an upgrade button that leads nowhere is worse
    than no button."""
    from src.billing.plans import PLAN_LIMITS, get_plan
    nxt = {"free": "starter", "starter": "pro"}.get(get_plan(user))
    if not nxt:
        return None
    return {"plan": nxt, "label": PLAN_LIMITS[nxt]["label"],
            "max_pending": PLAN_LIMITS[nxt]["max_pending"],
            "max_streams": PLAN_LIMITS[nxt]["max_streams"],
            "price": PLAN_LIMITS[nxt]["price"]}


def pending_room(uid: str) -> tuple[int, int]:
    """(pending clips this user has, what their plan allows).

    Called by the clip processor BEFORE creating the Twitch clip. Checking
    afterwards would leave an orphan clip on the user's Twitch account that
    never appears in Highlightz, and would spend a Helix call from a budget
    shared with every other user.
    """
    from src.billing.plans import limits_for
    from src.auth import users as _room_store
    cap = limits_for(_room_store.get_by_id(uid))["max_pending"]
    used = sum(1 for c in _clips.values()
               if c.get("status") == "pending" and c.get("user_id") == uid)
    return used, cap


async def notify_clip_missed(user_id: str, channel: str, reason: str = "queue_full") -> None:
    """A moment we did NOT clip because the user's queue was full.

    Recorded in the same ledger as everything else so the clip record stays
    honest — a missed moment is not a caught one, and must never read as a
    rejection either.
    """
    from src.billing.plans import PLAN_LIMITS, get_plan
    from src.auth import users as _miss_store
    from src.stats import stream_stats as _ss

    user = _miss_store.get_by_id(user_id)
    used, cap = pending_room(user_id)
    _ss.record(_ss.MISSED, {"id": "", "user_id": user_id, "channel": channel,
                            "created_at": time.time()})
    plan = get_plan(user)
    nxt = {"free": "starter", "starter": "pro"}.get(plan)
    log.info("clip_missed_queue_full", user_id=user_id, channel=channel,
             pending=used, cap=cap, plan=plan)
    await broadcast({
        "event": "clip_missed",
        "channel": channel,
        "reason": reason,
        "plan": plan,
        "limit": cap,
        "missed_24h": _ss.missed_since(user_id, time.time() - 86400),
        "next_plan": nxt,
        "next_limit": PLAN_LIMITS[nxt]["max_pending"] if nxt else 0,
        "next_price": PLAN_LIMITS[nxt]["price"] if nxt else 0,
    }, user_id=user_id)


def _clips_lost_24h(uid: str) -> int:
    """Moments not clipped because the queue was full, since whichever is
    LATER: 24h ago, or the last time the user dismissed the notice.

    Counting from the dismissal is what makes the X button mean something. A
    plain 24h window would bring the same notice straight back on the next page
    load, which is exactly how it behaved before and why it felt broken. New
    misses after a dismissal still count, so the warning returns when there is
    something new to warn about.
    """
    from src.stats import stream_stats
    from src.auth import users as _miss_store
    user = _miss_store.get_by_id(uid) or {}
    since = max(time.time() - 86400, user.get("miss_notice_dismissed_at") or 0)
    return stream_stats.missed_since(uid, since)


@app.post("/me/dismiss-miss-notice")
async def dismiss_miss_notice(request: Request):
    """Close the queue-full notice until something new is missed."""
    from src.auth import users as user_store
    uid = _current_user_id(request)
    user_store.set_miss_notice_dismissed(uid, time.time())
    # Scoped broadcast so the user's OTHER tabs close it too — dismissing in
    # one window and having it still sitting there in another is the same
    # complaint in a different shape.
    await broadcast({"event": "miss_notice_dismissed"}, user_id=uid)
    return {"ok": True}


def _review_prompt_due(uid: str) -> bool:
    from src.auth import users as user_store
    from src.feedback import reviews
    user = user_store.get_by_id(uid)
    return bool(user) and reviews.should_prompt(user, _approved_clip_count(uid))


def _approved_clip_count(uid: str) -> int:
    return sum(1 for c in _clips.values()
               if c.get("user_id") == uid and c.get("status") == "approved")


async def _maybe_prompt_review(uid: str) -> None:
    """Called after an approval. Broadcasts once when a milestone is crossed so
    the prompt appears without a refresh; /me carries the same flag so a tab
    opened later still sees it."""
    from src.auth import users as user_store
    from src.feedback import reviews
    user = user_store.get_by_id(uid)
    if not user:
        return
    count = _approved_clip_count(uid)
    if not reviews.should_prompt(user, count):
        return
    user_store.set_review_prompt_state(uid, reviews.mark_shown(user, count))
    await broadcast({"event": "review_prompt", "clips": count}, user_id=uid)


@app.post("/reviews", status_code=201)
async def submit_review(request: Request):
    from src.auth import users as user_store
    from src.feedback import reviews
    uid = _current_user_id(request)
    user = user_store.get_by_id(uid) or {}
    body = await request.json()

    action = str(body.get("action") or "submit")
    if action == "snooze":
        user_store.set_review_prompt_state(
            uid, reviews.mark_snoozed(user, _approved_clip_count(uid)))
        return {"ok": True}
    if action == "never":
        user_store.set_review_prompt_state(uid, reviews.mark_never(user))
        return {"ok": True}

    try:
        r = reviews.add(uid, user.get("username", ""), int(body.get("stars") or 0),
                        str(body.get("comment") or ""),
                        bool(body.get("publish_consent")),
                        str(body.get("display_name") or ""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    user_store.set_review_prompt_state(uid, reviews.mark_submitted(user))
    return {"ok": True, "id": r.id}


@app.get("/admin/reviews")
async def admin_reviews(request: Request):
    _require_admin(request)
    from src.feedback import reviews
    return {"reviews": [r.admin() for r in reviews.all_reviews()],
            "aggregate": reviews.aggregate()}


@app.post("/admin/reviews/{review_id}/approve")
async def admin_review_approve(request: Request, review_id: str):
    _require_admin(request)
    from src.feedback import reviews
    body = await request.json()
    r = reviews.set_approved(review_id, bool(body.get("approved", True)))
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    # Publishing changes the landing page for everyone, so this is one of the
    # few genuinely global broadcasts.
    await broadcast({"event": "reviews_updated"})
    return r.admin()


@app.delete("/admin/reviews/{review_id}", status_code=204)
async def admin_review_delete(request: Request, review_id: str):
    _require_admin(request)
    from src.feedback import reviews
    if not reviews.remove(review_id):
        raise HTTPException(status_code=404, detail="Not found")
    await broadcast({"event": "reviews_updated"})
    return Response(status_code=204)


@app.get("/admin/referrals")
async def admin_referrals(request: Request):
    """Signups per referrer, and how many of them stuck.

    The column that matters is not signups — it is who came back. Signups say
    who is good at getting attention; week-2 retention says whose lane brought
    people who actually needed this.
    """
    from src.auth import referrals
    from src.auth import users as user_store
    from src.billing.plans import is_paid
    _require_admin(request)

    now = time.time()
    WEEK = 7 * 86400
    buckets: dict[str, dict] = {}
    for key in referrals.all_keys() + ["direct"]:
        buckets[key] = {"ref": key, "label": referrals.label(None if key == "direct" else key),
                        "signups": 0, "connected": 0, "active_wk2": 0, "paid": 0}

    for u in user_store.get_all():
        key = u.get("ref") or "direct"
        b = buckets.setdefault(key, {"ref": key, "label": referrals.label(key),
                                     "signups": 0, "connected": 0,
                                     "active_wk2": 0, "paid": 0})
        b["signups"] += 1
        # "Connected a channel" = they got as far as linking Twitch, which is
        # the first step that means anything.
        if u.get("twitch_id"):
            b["connected"] += 1
        # Week-2 retention: signed up at least 7 days ago AND seen since. A
        # user who joined yesterday is not yet a retention datapoint, so they
        # are excluded from BOTH sides rather than counted as churned.
        created = u.get("created_at") or 0
        if created and now - created >= WEEK:
            last = _user_last_active.get(u["id"], 0)
            if last and now - last < WEEK:
                b["active_wk2"] += 1
        if is_paid(u):
            b["paid"] += 1

    rows = sorted(buckets.values(), key=lambda r: -r["signups"])
    return {"rows": rows, "total": sum(r["signups"] for r in rows)}


@app.get("/admin/stream-stats")
async def admin_stream_stats(request: Request):
    """How many clips were caught per channel, and how many were kept.

    ADMIN ONLY, by request — this is an operator view for showing a streamer
    what the product did on their channel, not a user-facing feature. It spans
    every user, so it must never be reachable without the admin flag.

    See src/stats/stream_stats.py for why sessions are inferred from gaps and
    why this reads a dedicated ledger rather than counting `_clips`.
    """
    from src.stats import stream_stats
    from src.auth import users as user_store
    _require_admin(request)
    names = {u["id"]: u.get("username") or u["id"] for u in user_store.get_all()}
    rows = stream_stats.all_rows()
    for r in rows:
        r["username"] = names.get(r["user_id"], r["user_id"])
    return {"rows": rows,
            "session_gap_hours": stream_stats.SESSION_GAP_S / 3600}


@app.get("/publish/platforms")
async def publish_platforms(request: Request):
    """Where a finished clip can go, and the limits it has to fit.

    We do NOT post on the user's behalf — the clip goes to their machine and
    they share it from there. That is why this endpoint returns specs rather
    than OAuth state: there is no connection to hold, nothing to expire, and
    none of it waits on TikTok/Meta/Google app review.
    """
    from src.publish import platforms as plat
    _current_user_id(request)
    return {"platforms": plat.public_specs()}


# ── Posting queue ─────────────────────────────────────────────────────────────
#
# Reminders, not automation: we hold no platform credentials, so at the due time
# the user still taps share. Every string below has to say that — a queue that
# looks automatic and silently isn't would cost someone a posting slot.

@app.get("/publish/schedule")
async def publish_schedule(request: Request):
    from src.publish import schedule as sched
    uid = _current_user_id(request)
    return {"items": [i.public() for i in sched.for_user(uid)]}


@app.post("/publish/schedule", status_code=201)
async def publish_schedule_add(request: Request):
    from src.publish import schedule as sched
    from src.publish import platforms as plat
    from src.uploads import library as upload_lib
    uid = _current_user_id(request)
    _require_upload_access(uid)

    body = await request.json()
    up = upload_lib.get(str(body.get("upload_id") or ""), uid)
    if not up:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Reject unknown platform ids rather than storing them: they would render
    # as a reminder to post somewhere that does not exist. An empty list is
    # fine — a freshly exported clip has no destination chosen yet.
    targets = [p for p in (body.get("platforms") or []) if p in plat.BY_ID]
    try:
        due_at = float(body.get("due_at") or 0)
        duration_s = float(body.get("duration_s") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid time.")

    try:
        item = sched.add(uid, up.id, up.filename, str(body.get("caption") or ""),
                         targets, due_at, duration_s=duration_s,
                         ratio=str(body.get("ratio") or ""),
                         fmt=str(body.get("fmt") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await broadcast({"event": "schedule_added", "item": item.public()}, user_id=uid)
    return item.public()


@app.patch("/publish/schedule/{item_id}")
async def publish_schedule_update(request: Request, item_id: str):
    from src.publish import schedule as sched
    uid = _current_user_id(request)
    from src.publish import platforms as plat
    body = await request.json()
    try:
        if body.get("status"):
            item = sched.set_status(item_id, uid, str(body["status"]))
        else:
            targets = (None if body.get("platforms") is None
                       else [p for p in body["platforms"] if p in plat.BY_ID])
            item = sched.update(
                item_id, uid,
                caption=None if body.get("caption") is None else str(body["caption"]),
                due_at=None if body.get("due_at") is None else float(body["due_at"]),
                platforms=targets)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    await broadcast({"event": "schedule_updated", "item": item.public()}, user_id=uid)
    return item.public()


@app.delete("/publish/schedule/{item_id}", status_code=204)
async def publish_schedule_delete(request: Request, item_id: str):
    from src.publish import schedule as sched
    uid = _current_user_id(request)
    if not sched.remove(item_id, uid):
        raise HTTPException(status_code=404, detail="Not found")
    await broadcast({"event": "schedule_removed", "item_id": item_id}, user_id=uid)
    return Response(status_code=204)


async def schedule_due_task() -> None:
    """Tell open tabs the moment a queued post comes due.

    The list itself is the source of truth (`due` is derived from the clock on
    every read), so this event is a nudge, not state. That is deliberate: a
    missed broadcast — restart, dropped socket — must not be able to lose a
    reminder, and here it cannot.
    """
    from src.publish import schedule as sched
    while True:
        try:
            for item in sched.newly_due():
                await broadcast({"event": "schedule_due", "item": item.public()},
                                user_id=item.user_id)
        except Exception as exc:                       # never kill the loop
            log.warning("schedule_due_task_error", error=str(exc))
        await asyncio.sleep(30)


@app.get("/uploads/{upload_id}/captions")
async def get_captions(request: Request, upload_id: str):
    """Existing captions, or the state of a run in flight."""
    from src.uploads import library as upload_lib
    from src.captions import transcribe as cap
    uid = _current_user_id(request)
    up = upload_lib.get(upload_id, uid)
    if not up:
        raise HTTPException(status_code=404, detail="Upload not found")
    return {"captions": cap.load(upload_lib.path_for(up)),
            "job": _caption_jobs.get(upload_id)}


@app.post("/uploads/{upload_id}/captions", status_code=202)
async def start_captions(request: Request, upload_id: str):
    """Kick off transcription. Returns immediately; progress arrives over the
    WebSocket, because a 30s clip can take ~30-60s on this hardware and holding
    an HTTP request open that long is how you collect timeouts."""
    from src.uploads import library as upload_lib
    from src.captions import transcribe as cap
    uid = _current_user_id(request)
    _require_upload_access(uid)
    _require_captions(uid)

    up = upload_lib.get(upload_id, uid)
    if not up:
        raise HTTPException(status_code=404, detail="Upload not found")

    _prune_caption_jobs()

    running = _caption_jobs.get(upload_id)
    if running and running.get("status") == "running":
        return running
    # One queued job per user. The transcriber is single-slot process-wide, so
    # letting one user stack five jobs would just push everyone else back.
    if any(j.get("status") == "running" and j.get("user_id") == uid
           for j in _caption_jobs.values()):
        raise HTTPException(status_code=429,
                            detail="Already captioning a clip — one at a time.")

    job = {"status": "running", "pct": 0, "error": "", "user_id": uid,
           "upload_id": upload_id}
    _caption_jobs[upload_id] = job

    async def _progress(pct: int, note: str) -> None:
        job["pct"] = pct
        await broadcast({"event": "captions_progress", "upload_id": upload_id,
                         "pct": pct, "note": note}, user_id=uid)

    async def _work() -> None:
        path = upload_lib.path_for(up)
        try:
            payload = await cap.transcribe(path, on_progress=_progress)
            cap.save(path, payload)
            job.update(status="done", pct=100, finished_at=time.time())
            await broadcast({"event": "captions_ready", "upload_id": upload_id,
                             "captions": payload}, user_id=uid)
        except Exception as exc:
            log.warning("captions_failed", upload_id=upload_id, error=str(exc))
            job.update(status="failed", error=str(exc), finished_at=time.time())
            await broadcast({"event": "captions_failed", "upload_id": upload_id,
                             "message": str(exc)}, user_id=uid)

    asyncio.create_task(_work())
    return job


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    if not ws.session.get("auth"):
        await ws.close(code=1008)
        return
    uid = ws.session.get("user_id")
    if not uid:
        await ws.close(code=1008)
        return
    # Re-validate subscription from DB, not the session cookie, so a subscription
    # that expires mid-session is caught immediately rather than at next HTTP request.
    from src.auth import users as _ws_user_store
    _ws_db_user  = _ws_user_store.get_by_id(uid)
    _ws_is_admin = _ws_db_user.get("is_admin", False) if _ws_db_user else False
    _ws_sub      = _ws_db_user.get("subscription_status", "none") if _ws_db_user else "none"
    if _ws_sub == "trialing" and _ws_db_user:
        import time as _t
        if _t.time() >= (_ws_db_user.get("trial_ends_at") or 0):
            _ws_sub = "expired"
    # Deliberately NO subscription check. Realtime is not a paid feature — it
    # is how the dashboard works at all (CLAUDE.md: "refresh to see it" is a
    # bug). Closing the socket on a free user would leave them with a screen
    # that silently stops updating, which reads as a broken product rather than
    # as a limit. What free users get less OF is enforced at the limits, not
    # by starving the transport.
    async with _ws_lock:
        bucket = _ws_clients.setdefault(uid, set())
        if len(bucket) >= _MAX_WS_PER_USER:
            await ws.close(code=1008)
            return
        bucket.add(ws)
    _user_last_active[uid] = time.time()
    log.info("ws_connected", user=uid, total=sum(len(v) for v in _ws_clients.values()))
    try:
        while True:
            # The browser auto-sends a 'ping' every 30s as a keepalive. That is
            # NOT user activity, so we deliberately do NOT bump _user_last_active
            # here — otherwise an abandoned-but-open tab would never go idle.
            # Real activity (approving clips, navigating, etc.) bumps it via the
            # authenticated HTTP requests in AuthMiddleware.
            msg = await ws.receive_text()
            if len(msg) > 256:
                await ws.close(code=1009)  # 1009 = message too big
                break
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            _ws_clients.get(uid, set()).discard(ws)
            # Streams are NOT stopped on WebSocket disconnect — closing the browser
            # tab should not kill a running stream. Streams continue until:
            #   1. The live stream ends (liveness check in stream_worker)
            #   2. The user manually removes the stream
            #   3. The idle reaper fires after 8 hours of no HTTP activity
        log.info("ws_disconnected", user=uid)


@app.post("/streams/{channel}/force-clip")
async def force_clip(request: Request, channel: str):
    uid        = _current_user_id(request)
    channel    = _clean_channel(channel)
    await _check_force_clip_rate(uid)
    stream_key = f"{uid}:{channel}"
    if stream_key not in _streams:
        raise HTTPException(status_code=404, detail="Stream not registered")
    if not _force_clip_cb:
        raise HTTPException(status_code=503, detail="Force clip not ready")
    await _force_clip_cb(channel, uid)
    return {"status": "queued", "channel": channel}


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    try:
        _require_admin(request)
    except HTTPException:
        uid = request.session.get("user_id", "")
        uname = request.session.get("username", uid or "unknown")
        return HTMLResponse(
            f"""<!DOCTYPE html><html><head><title>Admin — Not Authorized</title>
            <style>body{{font-family:system-ui,sans-serif;background:#0a0a0e;color:#fff;display:flex;
            align-items:center;justify-content:center;min-height:100vh;margin:0}}
            .box{{text-align:center;max-width:520px;padding:40px}}
            h1{{font-size:28px;margin-bottom:12px;color:#f87171}}
            p{{color:#a0a0b0;line-height:1.6;margin-bottom:8px}}
            code{{background:rgba(255,255,255,.08);padding:2px 7px;border-radius:4px;font-size:13px}}
            a{{color:#9146ff;text-decoration:none}}</style></head>
            <body><div class="box">
            <h1>403 — Not an admin</h1>
            <p>Logged in as <code>{uname}</code>. This account does not have the admin flag set.</p>
            <p>To grant yourself admin access, make sure <code>ADMIN_TWITCH_ID</code> in your
            server environment matches your numeric Twitch user ID, then log out and log back in.</p>
            <p>Alternatively, use the server CLI:<br>
            <code>python -m src.auth.grant_admin &lt;username_or_user_id&gt;</code></p>
            <p style="margin-top:24px"><a href="/">← Back to dashboard</a></p>
            </div></body></html>""",
            status_code=403,
        )
    return HTMLResponse(ADMIN_HTML)


@app.get("/admin/users")
async def admin_list_users(request: Request):
    _require_admin(request)
    from src.auth import users as user_store
    from src.billing import plans
    users = user_store.get_all()
    for u in users:
        uid = u["id"]
        u["stream_count"] = sum(1 for s in _streams.values() if s.get("user_id") == uid)
        u["clip_count"] = sum(1 for c in _clips.values() if c.get("user_id") == uid)
        # The RESOLVED membership, not the raw stored field. Those disagree
        # constantly and the stored one is the misleading half: a legacy
        # $15-era subscriber has no `plan` at all but is effectively Pro, an
        # admin has no subscription but gets Pro, and someone who cancelled
        # keeps a stale plan="pro" while actually being on Free. The admin
        # table has to show what the user really has, so it shows this.
        plan = plans.get_plan(u)
        u["plan"] = plan
        limits = plans.PLAN_LIMITS.get(plan, {})
        u["plan_label"] = limits.get("label", plan.title())
        u["plan_price"] = limits.get("price", 0)
        # How they came to be on it — "granted" means an admin comped them.
        u["plan_source"] = u.get("plan_source") or ("stripe" if u.get("stripe_customer_id") else "")
        # Paying = money actually arrives. A granted trial and a comped admin
        # both read as Pro above, and neither belongs in revenue. The
        # discriminator is a STRIPE CUSTOMER, not the plan: now that an admin
        # can comp someone Starter, the stored plan alone would price a gift at
        # $10/mo. No customer behind the account means no money.
        u["is_paying"] = bool(
            not u.get("is_admin") and not u.get("is_labeler")
            and u.get("subscription_status") == "active"
            and u.get("stripe_customer_id")
            and u.get("plan_source") != "granted"
            and limits.get("price", 0) > 0
        )
    return users


class InviteRequest(BaseModel):
    plan: str
    days: int = 0                 # 0 = no end date
    note: str = ""
    max_uses: int = 1
    ttl_days: int = 30            # how long the LINK works, not the membership


@app.post("/admin/invites", status_code=201)
async def admin_create_invite(request: Request, body: InviteRequest):
    """Mint an invite link that grants a membership on sign-in."""
    _require_admin(request)
    from src.auth import invites
    from src.billing import plans
    if body.plan not in plans.PAID_PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown plan {body.plan!r} — choose one of {', '.join(plans.PAID_PLANS)}.")
    if not 0 <= body.days <= 365:
        raise HTTPException(status_code=400, detail="days must be between 0 and 365")
    if not 1 <= body.max_uses <= 100:
        raise HTTPException(status_code=400, detail="max_uses must be between 1 and 100")
    try:
        inv = invites.create(plan=body.plan, days=body.days, note=body.note,
                             max_uses=body.max_uses, ttl_days=body.ttl_days,
                             created_by=request.session.get("user_id", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return inv.public()


@app.get("/admin/invites")
async def admin_list_invites(request: Request):
    _require_admin(request)
    from src.auth import invites
    return {"invites": [i.public() for i in invites.all_invites()]}


@app.delete("/admin/invites/{code}", status_code=204)
async def admin_revoke_invite(request: Request, code: str):
    """Kill a link. Memberships already claimed through it are untouched —
    revoking a link is not a way to take someone's plan away, and pretending
    otherwise would make this button do two things at once."""
    _require_admin(request)
    from src.auth import invites
    if not invites.revoke(code):
        raise HTTPException(status_code=404, detail="Invite not found")
    return Response(status_code=204)


@app.get("/admin/overview")
async def admin_overview(request: Request):
    """Platform totals, computed on the server from the real ledgers.

    WHY THIS EXISTS. The admin header used to derive its figures in the browser
    by summing the user list, and every one of them was wrong in a different
    way. The worst was "Total Clips": it summed `clip_count`, which is how many
    clips are in `_clips` RIGHT NOW. That silently excludes every clip that was
    rejected (deleted on reject), aged out of the queue, or dropped because the
    queue was full — plus every clip belonging to an admin account, which the
    sum filtered out. On a system that has caught five figures of clips it was
    reporting a few hundred.

    The accurate lifetime figure already existed in two places and neither was
    being read here: the persisted clip counter (the same number the landing
    page shows) and the stream_stats ledger, which records caught/kept/rejected/
    aged-out as events and therefore survives deletion. Both are used below.

    Everything here is a count of something that happened, not a count of rows
    that are still lying around.
    """
    _require_admin(request)
    from src.auth import users as user_store
    from src.billing import plans
    from src.stats import stream_stats

    users = user_store.get_all()
    # Staff accounts are excluded from population and revenue but NOT from the
    # clip figures — an admin's clips are real clips the system caught.
    customers = [u for u in users if not u.get("is_admin")]

    by_plan: dict[str, int] = {p: 0 for p in plans.PLAN_LIMITS}
    paying = trialing = mrr = legacy = comped = 0
    for u in customers:
        plan = plans.get_plan(u)
        by_plan[plan] = by_plan.get(plan, 0) + 1
        status = u.get("subscription_status")
        if status == "trialing":
            trialing += 1
        elif status == "active":
            # ENTITLEMENT is not PRICE. A $15-era subscriber has no stored plan
            # and is grandfathered to Pro so they keep every feature — but they
            # are not paying $25, and billing them at the Pro price in this
            # figure would silently inflate MRR by the difference on every one
            # of them. We do not hold their price locally (Stripe does), so
            # they are counted as subscribers and left out of the money, and
            # `mrr_unknown` says how many are missing. MRR is a floor, and the
            # panel labels it as one.
            # A comped membership is not revenue. Since an admin can now grant
            # a specific tier, the stored plan is no longer proof of payment —
            # a Stripe customer is. Without one the account is a gift and is
            # left out of both the count and the money.
            if not u.get("stripe_customer_id") or u.get("plan_source") == "granted":
                comped += 1
                continue
            stored = u.get("plan")
            price = (plans.PLAN_LIMITS.get(plan, {}).get("price", 0)
                     if stored in plans.PAID_PLANS else 0)
            paying += 1
            if price:
                mrr += price
            else:
                legacy += 1

    now = time.time()
    new_7d = sum(1 for u in customers if (u.get("created_at") or 0) >= now - 7 * 86400)
    new_30d = sum(1 for u in customers if (u.get("created_at") or 0) >= now - 30 * 86400)

    rows = stream_stats.all_rows()
    caught = sum(r.get("caught", 0) for r in rows)
    kept = sum(r.get("approved", 0) for r in rows)
    rejected = sum(r.get("rejected", 0) for r in rows)
    expired = sum(r.get("expired", 0) for r in rows)
    missed = sum(r.get("missed", 0) for r in rows)
    reviewed = kept + rejected

    stored = list(_clips.values())
    return {
        "users": {
            "total": len(customers), "admins": len(users) - len(customers),
            "paying": paying, "trialing": trialing, "by_plan": by_plan,
            # Permanently comped accounts — access granted by an admin with no
            # Stripe behind it. Counted apart from both paying and trialing.
            "comped": comped,
            "new_7d": new_7d, "new_30d": new_30d,
        },
        "mrr": mrr,
        # How many active subscribers we could not price locally. MRR is a
        # floor, not a total, whenever this is non-zero.
        "mrr_unknown": legacy,
        "clips": {
            # The lifetime counter — the same number the landing page shows, so
            # the two can never quote different totals.
            "lifetime": get_clip_counter(),
            # What is on disk now. Deliberately reported next to lifetime
            # rather than instead of it, because the gap between them IS the
            # story: rejected, aged out and dropped clips live in that gap.
            "stored": len(stored),
            "pending": sum(1 for c in stored if c.get("status") == "pending"),
            "approved": sum(1 for c in stored if c.get("status") == "approved"),
            # From the ledger, so these survive the clip being deleted.
            "caught": caught, "kept": kept, "rejected": rejected,
            "expired": expired, "missed": missed,
            "keep_rate": round(kept / reviewed * 100) if reviewed else 0,
        },
        "streams": {
            "registered": len(_streams),
            "live": sum(1 for s in _streams.values() if s.get("status") == "live"),
        },
    }


@app.post("/admin/users/{user_id}/grant")
async def admin_grant_access(request: Request, user_id: str, plan: str = "pro"):
    """Comp a user a specific membership, permanently and without Stripe.

    `plan` defaults to pro because that is what this endpoint did before it
    could take one — a bookmarked call or an older tab must keep behaving
    exactly as it did rather than silently start handing out a lesser tier.
    """
    _require_admin(request)
    from src.auth import users as user_store
    from src.billing import plans
    if plan not in plans.PAID_PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown plan {plan!r} — choose one of {', '.join(plans.PAID_PLANS)}.")
    if not user_store.grant_plan(user_id, plan):
        raise HTTPException(status_code=404, detail="User not found")
    label = plans.PLAN_LIMITS[plan]["label"]
    log.info("admin_plan_granted", user_id=user_id, plan=plan,
             by=request.session.get("user_id"))
    # Realtime: the granted user's open tab should clear the paywall/banner live.
    await broadcast(
        {"event": "subscription_active",
         "message": f"You've been given {label} access."},
        user_id=user_id,
    )
    return {"ok": True, "plan": plan}


class TrialGrantRequest(BaseModel):
    days: int
    # Which membership the trial grants. None keeps the original behaviour of
    # showcasing the full product.
    plan: str | None = None


@app.post("/admin/users/{user_id}/grant-trial")
async def admin_grant_trial(request: Request, user_id: str, body: TrialGrantRequest):
    """Give a user timed free access (app-managed, no Stripe): status becomes
    'trialing' with trial_ends_at now + N days. Expiry is enforced by the
    existing middleware/reaper paths, which stop streams and notify live.
    Granting again extends/replaces the current window."""
    _require_admin(request)
    from src.auth import users as user_store
    if not 1 <= body.days <= 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    user = user_store.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("is_admin"):
        raise HTTPException(status_code=400, detail="Admins already have full access")
    if user.get("subscription_status") == "active":
        raise HTTPException(status_code=400, detail="User already has an active subscription")
    from src.billing import plans
    if body.plan is not None and body.plan not in plans.PAID_PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown plan {body.plan!r} — choose one of {', '.join(plans.PAID_PLANS)}.")
    user_store.grant_trial(user_id, body.days, body.plan)
    label = plans.PLAN_LIMITS[body.plan]["label"] if body.plan else "full"
    log.info("admin_trial_granted", user_id=user_id, days=body.days, plan=body.plan,
             by=request.session.get("user_id"))
    # Realtime: the granted user's open tab should clear the paywall/banner live.
    await broadcast(
        {"event": "subscription_active",
         "message": f"You've been given {body.days} day{'s' if body.days != 1 else ''} "
                    f"of {label} access."},
        user_id=user_id,
    )
    return {"ok": True, "days": body.days, "plan": body.plan}


@app.post("/admin/users/{user_id}/revoke")
async def admin_revoke_access(request: Request, user_id: str):
    _require_admin(request)
    from src.auth import users as user_store
    user_store.update_subscription(user_id, None, "inactive")
    # Stop their streams immediately and tell any open session live, rather than
    # waiting up to 5 min for the idle reaper to notice the lapsed subscription.
    await _stop_user_streams_now(user_id)
    await broadcast(
        {"event": "subscription_expired",
         "message": "Your access has been revoked — streams have been stopped."},
        user_id=user_id,
    )
    return {"ok": True}


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(request: Request, user_id: str):
    _require_admin(request)
    # Prevent deleting yourself
    if user_id == request.session.get("user_id"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    from src.auth import users as user_store
    await _stop_user_streams_now(user_id)
    async with _data_lock:
        to_delete = [c for c in list(_clips.values()) if c.get("user_id") == user_id]
        for clip in to_delete:
            _clips.pop(clip["id"], None)
        _save_clips()
    for clip in to_delete:
        _delete_clip_file(clip)
    stale = [k for k, s in _streams.items() if s.get("user_id") == user_id]
    for k in stale:
        _streams.pop(k, None)
    _save_streams()
    user_store.delete(user_id)
    return {"ok": True}


@app.post("/admin/users/{user_id}/stripe-sync")
async def admin_stripe_sync(request: Request, user_id: str):
    """Manually re-sync a user's Stripe subscription status from Stripe's API."""
    _require_admin(request)
    from src.auth import users as user_store
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    user = user_store.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer ID linked to this user")
    try:
        import stripe
        client = stripe.StripeClient(settings.stripe_secret_key)
        subs = client.subscriptions.list(params={"customer": customer_id, "limit": 1})
        items = subs.data if hasattr(subs, "data") else []
        if not items:
            return {"synced": False, "reason": "No subscriptions found for this customer"}
        sub = items[0]
        raw = sub.get("status", "") if isinstance(sub, dict) else sub.status
        from src.billing.stripe_billing import ACTIVE_STATUSES
        status = "active" if raw in ACTIVE_STATUSES else ("inactive" if raw in ("canceled", "unpaid", "incomplete_expired") else raw)
        user_store.update_subscription(user_id, customer_id, status)
        log.info("admin_stripe_sync", user_id=user_id, customer=customer_id, status=status)
        return {"synced": True, "stripe_status": raw, "app_status": status}
    except Exception as exc:
        log.error("admin_stripe_sync_error", user_id=user_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Stripe API error — check server logs")


@app.get("/admin/users/{user_id}/streams")
async def admin_user_streams(request: Request, user_id: str):
    """Admin: streams currently registered for a specific user."""
    _require_admin(request)
    return [s for s in _streams.values() if s.get("user_id") == user_id]


@app.get("/admin/users/{user_id}/clips")
async def admin_user_clips(request: Request, user_id: str):
    """Admin: a user's clips, approved first, newest first within each group.

    APPROVED FIRST IS A SERVER-SIDE DECISION, not a display one. The list is
    capped at 100, so ordering it in the browser would come too late: a user
    with a full pending queue can easily have 100 unreviewed clips newer than
    every clip they ever kept, and a purely chronological cap would hand the
    admin panel a page with zero approved clips on it. Sorting before the slice
    guarantees the clips they kept are the ones that survive it.
    """
    _require_admin(request)
    clips = [c for c in _clips.values() if c.get("user_id") == user_id]
    clips.sort(key=lambda c: (c.get("status") != "approved",
                              -(c.get("approved_at") or c.get("created_at") or 0)))
    return clips[:100]


@app.get("/tos", response_class=HTMLResponse)
async def tos_page():
    return HTMLResponse(TOS_HTML)


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return HTMLResponse(PRIVACY_HTML)


@app.get("/cookies", response_class=HTMLResponse)
async def cookies_page():
    return HTMLResponse(COOKIES_HTML)


@app.get("/tutorial", response_class=HTMLResponse)
async def tutorial_page():
    """The public walkthrough. In _OPEN_PATHS, so a signed-out visitor reads it.

    MUST STAY ABOVE `@app.get("/{slug}")` — that catch-all matches any
    single-segment path, and FastAPI resolves in declaration order, so a route
    declared after it never runs. Registered below the referral handler this
    would 404 on a path that plainly exists, which is a maddening bug to find.

    Rendered per request rather than cached in a module constant because the
    renderer probes the filesystem for each media file: capture a screenshot
    and the placeholder becomes the real image on the next load, with no
    restart. The page is a few hundred KB of string building on a route almost
    nobody hits twice in a row.
    """
    from src.dashboard.tutorial_html import render
    return HTMLResponse(render())


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return HTMLResponse(NOT_FOUND_HTML, status_code=404)


_ERROR_MESSAGES = {
    "twitch_failed":      "Twitch login failed. Please try again.",
    "kick_failed":        "Kick login failed. Please try again.",
    "kick_signin_disabled": "Sign in with Twitch. You can link a Kick account from Settings after signing in.",
    "invalid_state":      "Login session expired. Please try again.",
    "incorrect_password": "Incorrect password. Please try again.",
    "account_deleted":    "Account deleted successfully.",
}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    _capture_ref(request)
    import html as _html
    err_msg = _ERROR_MESSAGES.get(error, "")
    err_html = f'<p class="error">{_html.escape(err_msg)}</p>' if err_msg else ""
    # Sign-in is Twitch-only for now; the Kick sign-in button is removed.
    return HTMLResponse(LOGIN_HTML.replace("{error}", err_html))


@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    import pathlib
    p = pathlib.Path(__file__).parent.parent.parent / "demo.html"
    try:
        return HTMLResponse(p.read_text())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Demo not found")


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    from src.auth import users as user_store
    ip = request.client.host if request.client else "unknown"
    await _check_login_rate(ip)
    # Only scan users with a password hash (admins); Twitch OAuth users never match
    user = next((u for u in user_store._load() if u.get("password_hash") and user_store.verify(u, password)), None)
    if user:
        await _clear_login_rate(ip)
        # Fix 10: clear any existing session before setting new auth data (session fixation)
        request.session.clear()
        request.session["auth"]                = True
        request.session["user_id"]             = user["id"]
        request.session["username"]            = user["username"]
        request.session["avatar_url"]          = user.get("avatar_url", "")
        request.session["is_admin"]            = user.get("is_admin", False)
        request.session["subscription_status"] = user.get("subscription_status", "none")
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=incorrect_password", status_code=302)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/logout")
async def logout_get(request: Request):
    # GET /logout: clear session so browser bookmarks/links actually log users out.
    # SameSite=lax already blocks cross-site form submissions; this endpoint only
    # handles same-site GET navigation (e.g., bookmark, address bar).
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _require_admin(request: Request) -> None:
    """Verify admin status against the DB, not just the session."""
    from src.auth import users as user_store
    uid = request.session.get("user_id", "")
    db_user = user_store.get_by_id(uid) if uid else None
    if not db_user or not db_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")


class _CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    password: str = Field(..., min_length=12, max_length=128)
    is_admin: bool = False


@app.post("/admin/users", status_code=201)
async def create_user(request: Request, body: _CreateUserRequest):
    _require_admin(request)
    from src.auth import users as user_store
    try:
        user = user_store.create(body.username, body.password, is_admin=body.is_admin)
        log.info("admin_user_created", by=request.session.get("user_id"), new_user=user["id"], is_admin=body.is_admin)
        return {"id": user["id"], "username": user["username"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))




@app.get("/stats")
async def get_stats(request: Request):
    import time as _time
    uid      = _current_user_id(request)
    now      = _time.time()
    week_ago = now - 7 * 86400
    channels: dict[str, dict] = {}
    for clip in [c for c in _clips.values() if c.get("user_id") == uid]:
        ch = clip.get("channel", "unknown")
        if ch not in channels:
            channels[ch] = {
                "channel": ch, "total_clips": 0, "clips_this_week": 0,
                "approved": 0, "pending": 0,
                "avg_score": 0.0, "avg_virality": 0.0,
                "top_signal": {}, "_scores": [], "_virality": [], "_signals": {},
            }
        c = channels[ch]
        c["total_clips"] += 1
        if clip.get("created_at", 0) >= week_ago:
            c["clips_this_week"] += 1
        status = clip.get("status", "pending")
        if status == "approved":
            c["approved"] += 1
        elif status == "pending":
            c["pending"] += 1
        c["_scores"].append(clip.get("trigger_score", 0.0))
        c["_virality"].append(clip.get("virality_score", 0.0))
        for sig in clip.get("trigger_signals", []):
            stype = sig.get("type", "")
            sval  = float(sig.get("value", 0.0))
            if stype:
                c["_signals"][stype] = c["_signals"].get(stype, 0.0) + sval

    result = []
    for c in channels.values():
        scores   = c.pop("_scores")
        virality = c.pop("_virality")
        signals  = c.pop("_signals")
        c["avg_score"]      = round(sum(scores) / len(scores), 1) if scores else 0.0
        c["avg_virality"]   = round(sum(virality) / len(virality), 1) if virality else 0.0
        c["approval_rate"]  = round(c["approved"] / c["total_clips"] * 100, 1) if c["total_clips"] else 0.0
        c["top_signal"]     = max(signals, key=signals.get) if signals else "—"
        result.append(c)

    result.sort(key=lambda x: x["total_clips"], reverse=True)
    return result


def _capture_ref(request: Request) -> None:
    """Stash a referral code from the URL into the session.

    The session cookie is what carries it through the Twitch OAuth round-trip —
    the user leaves for twitch.tv and comes back, and nothing else survives
    that. Called on every public entry point because a bio link might point at
    any of them.
    """
    from src.auth import referrals
    ref = referrals.normalise(request.query_params.get("ref"))
    # First touch wins here too: a second link must not overwrite the first
    # within one browsing session either.
    if ref and not request.session.get("ref"):
        request.session["ref"] = ref


def render_landing(html: str | None = None) -> str:
    """Bake the live clip count into the landing HTML before serving it.

    WHY THIS EXISTS: the counter used to be fetched by JavaScript after load,
    and the tile shipped as `display:none` with a literal `0` inside it. Every
    crawler and AI that reads HTML without executing JS — which is most of them
    — saw a hidden element containing zero. That is worse than showing nothing:
    it invites "Highlightz has captured 0 clips".

    The number is now in the first byte of the response, in the visible markup
    AND in the JSON-LD interactionStatistic, which is where machines look for a
    count. The client script still refreshes it live for humans.
    """
    html = LANDING_HTML if html is None else html
    total = get_clip_counter()
    if total <= 0:
        # Nothing captured yet: leave the tile hidden and the JSON-LD at 0
        # rather than advertising a number we do not have.
        return html

    pretty = f"{total:,}"
    html = html.replace(
        '<div class="stat stat-big" id="stat-clips" style="display:none">',
        '<div class="stat stat-big" id="stat-clips">', 1)
    html = html.replace('<span id="lp-count" data-count="0">0</span>',
                        f'<span id="lp-count" data-count="{total}">{pretty}</span>', 1)
    html = html.replace('"userInteractionCount": 0',
                        f'"userInteractionCount": {total}', 1)
    return html


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    _capture_ref(request)
    # Authenticated users reach this only after passing the auth + billing gate
    # in AuthMiddleware, so they get the app. Everyone else sees the public
    # marketing landing page.
    if request.session.get("auth"):
        return HTMLResponse(content=DASHBOARD_HTML)
    return HTMLResponse(content=render_landing())


@app.get("/landing/stats")
async def landing_stats():
    """Public stats for the landing page (in _OPEN_PATHS — no auth).
    Exposes only an aggregate count; nothing user-identifying."""
    return {"clips_total": get_clip_counter()}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Crawler policy: index the public marketing/legal pages, keep the
    app/auth/billing surface out of search results."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /auth/\n"
        "Disallow: /billing/\n"
        "Disallow: /clips\n"
        "Disallow: /streams\n"
        "Disallow: /profiles\n"
        "Disallow: /vod\n"
        "Disallow: /me\n"
        "Sitemap: https://highlightz.app/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
async def sitemap_xml():
    pages = ["/", "/tutorial", "/tos", "/privacy", "/cookies", "/opt-out"]
    urls = "".join(
        f"<url><loc>https://highlightz.app{p}</loc></url>" for p in pages)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + urls + "</urlset>")
    return Response(content=xml, media_type="application/xml")


@app.get("/landing/showcase")
async def landing_showcase():
    """Public, admin-curated example clips for the landing page (in _OPEN_PATHS).
    Only whitelisted fields, only clips the owner explicitly featured."""
    return {"clips": _load_showcase()}


@app.post("/admin/showcase/{clip_id}")
async def admin_toggle_showcase(request: Request, clip_id: str):
    """Admin: toggle an approved clip in/out of the landing-page examples."""
    _require_admin(request)
    items = _load_showcase()
    if any(e.get("id") == clip_id for e in items):
        items = [e for e in items if e.get("id") != clip_id]
        _save_showcase(items)
        # Realtime: every admin tab (Landing Page screen, clip modals) mirrors
        # the curation state, so a change here must reach them without a reload.
        await broadcast({"event": "showcase_updated"})
        return {"featured": False, "count": len(items), "max": _SHOWCASE_MAX}
    clip = _clips.get(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.get("status") != "approved":
        raise HTTPException(status_code=400, detail="Only approved clips can be featured")
    if clip.get("platform") != "twitch" or not clip.get("twitch_url"):
        raise HTTPException(status_code=400, detail="Only Twitch clips can be featured")
    if len(items) >= _SHOWCASE_MAX:
        # Refuse rather than silently evicting the oldest — the admin screen
        # shows the cap, and a surprise drop off the landing page is worse
        # than an explicit "remove one first".
        raise HTTPException(
            status_code=409,
            detail=f"Landing page is full ({_SHOWCASE_MAX} clips) — remove one first")
    items.append(_showcase_entry(clip))
    _save_showcase(items)
    await broadcast({"event": "showcase_updated"})
    return {"featured": True, "count": len(items), "max": _SHOWCASE_MAX}


def _is_grabbed(clip: dict) -> bool:
    """A clip copied from another admin's showcase rather than caught by the
    detector on this user's behalf.

    Every telemetry path has to check this. A grabbed clip did NOT come from
    our scoring on their channel, so counting it would:
      * inflate the per-channel clip record (a "kept" with no matching
        "caught", which is the number being shown to streamers),
      * teach that channel's profile from a decision the formula never made,
      * and put a mislabelled row in the training set.
    """
    return clip.get("source") == "grabbed"


@app.post("/admin/showcase/{clip_id}/grab")
async def admin_grab_showcase(request: Request, clip_id: str):
    """Admin: copy a featured clip into your own library.

    For trading clips between the team — one person's bot catches something on
    a channel and everyone can post it. NOTHING IS COPIED BUT A REFERENCE: the
    video stays on Twitch, exactly as with every other clip in the product, so
    this does not touch the no-re-hosting line at all.
    """
    _require_admin(request)
    uid = _current_user_id(request)

    entry = next((e for e in _load_showcase() if e.get("id") == clip_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="That clip is not on the landing page")

    origin = _clips.get(clip_id)
    if origin and origin.get("user_id") == uid:
        raise HTTPException(status_code=400, detail="That one is already yours")

    url = entry.get("twitch_url")
    if not url:
        raise HTTPException(status_code=400, detail="That clip has no Twitch link to copy")

    async with _data_lock:
        # Dedupe on the Twitch URL, not the clip id: the id is per-record and a
        # second grab would otherwise stack duplicates in their library.
        if any(c.get("user_id") == uid and c.get("twitch_url") == url
               for c in _clips.values()):
            raise HTTPException(status_code=409, detail="You already have that clip")

        copy = {
            "id":            uuid.uuid4().hex,
            "user_id":       uid,
            "platform":      "twitch",
            "channel":       entry.get("channel") or "",
            "clip_title":    entry.get("clip_title") or "Clip",
            "game":          entry.get("game") or "",
            "twitch_url":    url,
            "embed_url":     entry.get("embed_url") or "",
            "thumbnail_url": entry.get("thumbnail_url") or "",
            "duration_seconds": entry.get("duration_seconds") or 0,
            # Approved on arrival: grabbing IS the approval. Landing in the
            # review queue would ask them to judge a clip they just chose.
            "status":        "approved",
            "created_at":    time.time(),
            # Grabbing IS the approval, so it stamps approved_at too — without
            # it a grabbed clip would sort by created_at and land in the same
            # place, but only by coincidence. Anything that enters the library
            # records when it entered.
            "approved_at":   time.time(),
            # The marker every telemetry path checks. Also records who it came
            # from, so "who found this" survives the copy.
            "source":        "grabbed",
            "grabbed_from":  (origin or {}).get("user_id", ""),
            # Deliberately NO trigger_score / trigger_signals: our formula never
            # scored this for them, and inventing a score would put a fabricated
            # row in the training data.
        }
        _clips[copy["id"]] = copy
        _save_clips()

    log.info("clip_grabbed", user_id=uid, source_clip=clip_id, channel=copy["channel"])
    await broadcast({"event": "clip_ready", "clip": copy}, user_id=uid)
    return copy


@app.post("/admin/showcase/{clip_id}/move")
async def admin_move_showcase(request: Request, clip_id: str, dir: str = "up"):
    """Admin: reorder a featured clip. Landing-page order follows this list."""
    _require_admin(request)
    items = _load_showcase()
    idx = next((i for i, e in enumerate(items) if e.get("id") == clip_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Clip is not featured")
    swap = idx - 1 if dir == "up" else idx + 1
    if 0 <= swap < len(items):
        items[idx], items[swap] = items[swap], items[idx]
        _save_showcase(items)
        await broadcast({"event": "showcase_updated"})
    return {"ok": True, "order": [e.get("id") for e in items]}


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz — Automatic Twitch Clipper | Monitor Up To 10 Channels At Once</title>
<meta name="description" content="Highlightz watches every channel you clip for — up to 10 at once — and creates the Twitch clip the moment something pops. Chat spikes, audio pops, hype moments. Transparent formula, not AI. Free on one channel, no card required.">
<link rel="icon" type="image/png" href="/static/icon.png">
<link rel="canonical" href="https://highlightz.app/">
<link rel="preload" href="/static/fonts/lobster-400.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/sora-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-600.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-400.woff2" as="font" type="font/woff2" crossorigin>
<meta property="og:type" content="website">
<meta property="og:site_name" content="Highlightz">
<meta property="og:url" content="https://highlightz.app/">
<meta property="og:title" content="Highlightz — Never miss a highlight again, on 10 streams at once">
<meta property="og:description" content="Automatic Twitch clipping across every channel you watch — a transparent formula, not AI. Free on one channel, no card required.">
<!-- Preview card: social platforms cache this image keyed on the URL, so the
     filename must change whenever the art does. Source, build and the full
     history: scripts/og_card.html, scripts/build_og_card.mjs. -->
<meta property="og:image" content="https://highlightz.app/static/og-card-v3.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Highlightz — never miss a highlight again, on every channel at once. A live trigger score of 92 crossing the threshold and creating a clip on Twitch.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Highlightz — Never miss a highlight again">
<meta name="twitter:description" content="Automatic Twitch clipping across every channel you watch — a transparent formula, not AI. Free on one channel, no card required.">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v3.png">
<meta name="twitter:image:alt" content="Highlightz — never miss a highlight again. A live trigger score of 92 crossing the threshold and creating a clip on Twitch.">
<style>
  /* ══════════════════════════════════════════════════════════════════════
     DAYLIT ROOM, LIT MONITORS
     The room is bright now. The page is warm bone; the PRODUCT is dark — the
     demo, the dashboard mockups, the formula, the clip cards are monitors
     sitting on a light desk. That inversion is the whole idea: purple was
     competing with a dark page and losing, and now it is the only light
     source in a bright room, so it finally reads.

     Every colour is a PAIR, because the page has two surfaces. The light
     variants are the ones that carry text on bone; the dark variants only
     ever appear on a panel. Measured, not eyeballed — the light theme is
     where muted text quietly fails:

       ON LIGHT (contrast vs --bone #F8F5F0)
         --ink       #171219  headings                      16.98
         --ink-2     #4A4150  body                           8.93
         --muted     #6E6472  labels, meta                   5.18  AA
         --plum      #6A2E8A  purple AS TEXT/UI              8.15
         --ember-ink #A55C09  warm counterpoint as text      4.67  AA

       ON DARK (contrast vs --void #0E0B11)
         --iris      #B86ADC  purple as LIGHT                5.71
         --ember     #F7A745  warm counterpoint as light     9.83
         --flare     #D26AFB  threshold crossed, hottest point

     --iris on bone is 3.15 and --ember on bone is 1.83: both FAIL body text
     on light. They are barred from it structurally — if you want purple type
     on bone, it is --plum. That pairing is the single thing that keeps this
     from becoming an unreadable light theme.

     SURFACES
       --bone  #F8F5F0  page
       --sand  #EFE9E1  alternating band. 1.11 against bone — meant to be
                        felt, not seen. A visible stripe is just the long
                        list again in new paint.
       --void  #0E0B11  panels, and exactly ONE full-dark section (the
                        formula), where the content is genuinely instrument-
                        like and the darkness is earned rather than rhythmic.

     TYPE — three faces, all already self-hosted and subset. No new files:
     adding a face to look like type work costs bytes and CLS for nothing.
       Lobster    display, TWICE on the page (h1, closing line)
       Sora       body + section headings
       Plex Mono  every number, score, label — the instrument face

     MOTION — one curve for the entire site, no exceptions.
       --ease  cubic-bezier(.16,1,.3,1)
       150ms micro / 300ms transition / 600-800ms entrance
       transform and opacity ONLY. Entrances go on grouped CHILDREN, never on
       section containers — the same fade-up on every section is the tell.
     ══════════════════════════════════════════════════════════════════════ */

  /* Self-hosted, subsetted — no third-party font dependency. */
  /* TITLES ONLY, and now only two of them. Lobster is a SCRIPT face: its
     letters are drawn to connect in lowercase, so text-transform:uppercase
     mangles it — every rule below deliberately drops uppercase, and a test
     asserts none creeps back. It ships ONE weight (400); asking for bold makes
     the browser smear the glyphs. */
  @font-face{font-family:'Lobster';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/lobster-400.woff2) format('woff2')}
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  /* The instrument face. Every number a visitor reads is a measurement, so it
     is set in a mono with real tabular figures — a score that shifts width as
     it counts is a score you cannot read at a glance. */
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}

  :root{
    /* SURFACES — three tones of the same warm plum-black, lightest to darkest.
       The page is dark again, but a step warmer and lighter than the old
       #0E0B11 so the panels can sit BELOW it and read as lit objects. */
    --bone:#17131C;   /* page base — warm charcoal, plum-tinted */
    --sand:#1E1826;   /* the alternating band, one step up */
    --void:#0E0B11; --wall:#1B1221; --bruise:#33203F;
    /* INKS — one set, light on dark, checked against ALL THREE surface tones:
         --ink   #F2EAF7  15.60 / 14.74 / 16.65
         --ink-2 #B9AEC4   8.65 /  8.17 /  9.23
         --ink-3 #9C90A6   6.07 /  5.73 /  6.47   (dimmest text, still AA)
       against base / band / panel respectively. */
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085); --hair-2:rgba(242,234,247,.15);
    /* Kept as names so nothing that referenced them breaks, but on a dark page
       purple and ember ARE the readable variants — no darker step is needed. */
    --plum:#B86ADC; --ember-ink:#F7A745;
    /* purple and ember as light. */
    --iris:#B86ADC; --glow:#B86ADC; --glow-ink:#C489E4; --flare:#D26AFB; --ember:#F7A745;
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora',system-ui,sans-serif;
    --display:'Lobster',Georgia,serif;
    /* ONE curve, whole site. Durations are the only thing that varies. */
    --ease:cubic-bezier(.16,1,.3,1);
    --t-micro:150ms; --t-move:300ms; --t-enter:600ms;
    /* Measure. Text sections hold this; product sections deliberately do not. */
    --measure:65ch;
    /* 0..1 — how hard the trigger is firing right now. Everything that is
       "light" on this page reads from this one number, including the
       through-line's section wash. It is the product's own mechanic driving
       the page's lighting, which is the point. */
    --lit:0;
  }
  @media(prefers-reduced-motion:reduce){
    /* Not "less motion" — none. One place, no exceptions, so no animation
       added later can quietly opt itself out of this. */
    *,*::before,*::after{
      animation-duration:.001ms !important;animation-iteration-count:1 !important;
      transition-duration:.001ms !important;scroll-behavior:auto !important}
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth;overflow-x:clip}
  /* NO overflow-x here. `overflow-x:hidden` computes overflow-y to `auto`,
     which makes <body> a scroll container — and position:sticky then resolves
     against BODY's scrollport instead of the viewport. Body's scrollport does
     not scroll, so the sticky nav simply scrolls away with the page, taking
     the "Get started" button with it. Measured before removing this: the nav
     sat at y=0, then y=-1554 after scrolling 2500px.
     `html{overflow-x:clip}` above already suppresses sideways scroll, and
     `clip` (unlike `hidden`) does NOT create a scroll container, so sticky
     keeps working. Same fix already applied to /tutorial. */
  body{background:var(--bone);color:var(--ink-2);font-family:var(--sans);font-weight:400;
    font-size:16.5px;line-height:1.65;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}

  /* ── Surfaces. A container declares which world it is in and RE-DECLARES the
     ink tokens; everything inside then resolves correctly with no rule
     changes. Add a dark panel by putting it on this list, not by rewriting
     its colours. ── */
  .band-sand{background:var(--sand)}
  /* A band that also carries .wrap is max-width-constrained, so the colour
     would only paint the centred column. This paints it edge to edge behind
     the content without changing the nesting. 100vw is safe because
     html{overflow-x:clip} already suppresses sideways scroll. */
  .seam{position:relative;isolation:isolate}
  .band-sand.wrap,.band-dark.wrap{background:transparent}
  .band-sand.wrap::before,.band-dark.wrap::before{
    content:'';position:absolute;inset:0 auto;top:0;bottom:0;left:50%;width:100vw;
    transform:translateX(-50%);z-index:-1}
  .band-sand.wrap::before{background:var(--sand)}
  .band-dark.wrap::before{background:var(--void)}

  /* ── THE THROUGH-LINE. Hairline weight, small mono readout, no chrome. It
     costs one fixed element and it is the thing people remember. ── */
  .thread{position:fixed;right:clamp(14px,2.2vw,34px);top:50%;transform:translateY(-50%);
    z-index:55;display:none;flex-direction:column;align-items:center;gap:12px;
    pointer-events:none}
  @media(min-width:900px){ .thread{display:flex} }
  .thread-rail{position:relative;width:1px;height:min(42vh,340px);
    background:linear-gradient(180deg,transparent,var(--hair-2) 12%,var(--hair-2) 88%,transparent)}
  /* Fill is scaled, never resized: transform only, so it never triggers layout. */
  .thread-fill{position:absolute;left:-1px;bottom:0;width:3px;height:100%;
    transform-origin:50% 100%;transform:scaleY(var(--lit));border-radius:2px;
    background:linear-gradient(180deg,var(--flare),var(--plum));
    box-shadow:0 0 10px rgba(184,106,220,calc(.25 + var(--lit)*.55));
    transition:transform var(--t-move) var(--ease)}
  /* The threshold: the line the score has to cross for a clip to fire. */
  .thread-thresh{position:absolute;left:-4px;right:-4px;bottom:62%;height:1px;
    background:var(--ember-ink);opacity:.5}
  .thread-read{font-family:var(--mono);text-align:center;line-height:1}
  .thread-score{display:block;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;
    color:var(--ink-2)}
  .thread.fired .thread-score{color:var(--plum)}
  .thread-lab{display:block;margin-top:3px;font-size:9px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ink-3)}
  /* The fire: a wash of purple over the section being entered. Opacity only. */
  .seam::after,.wash::after{content:'';position:absolute;left:50%;transform:translateX(-50%);
    width:100vw;top:0;height:100%;pointer-events:none;z-index:0;opacity:0;
    background:radial-gradient(120% 60% at 50% 0%,rgba(184,106,220,.16),transparent 70%);
    transition:opacity 700ms var(--ease)}
  .seam.lit::after,.wash.lit::after{opacity:1}
  @media(prefers-reduced-motion:reduce){ .thread{display:none} }

  /* ── HERO. Fills the viewport and runs edge to edge; the demo widget is the
     best asset on the page and it was boxed into a 1140 column. ── */
  .hero-band{min-height:calc(100svh - 60px);display:grid;align-content:center;
    max-width:none;padding-left:clamp(20px,5vw,90px);padding-right:clamp(20px,5vw,90px)}
  @media(min-width:1200px){
    .hero-band{grid-template-columns:minmax(0,.86fr) minmax(0,1.14fr);gap:clamp(40px,5vw,88px)}
  }
  @media(min-width:1600px){ .hero-band{padding-left:6vw;padding-right:6vw} }
  .band-dark,.panel,.demo,.shot-frame,.exl-card{
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085); --hair-2:rgba(242,234,247,.15);
    color:var(--ink-2)}
  .band-dark{background:var(--void)}
  .band-dark h1,.band-dark h2,.band-dark h3,
  .panel h1,.panel h2,.panel h3{color:var(--ink)}
  /* The instrument panel itself: dark object on a light desk. The shadow is
     what sells it as sitting ON the page rather than cut into it. */
  .panel{background:var(--void);border:1px solid rgba(242,234,247,.10);border-radius:18px;
    box-shadow:0 18px 44px -22px rgba(0,0,0,.75),
               0 0 0 1px rgba(242,234,247,.04) inset,
               0 1px 0 rgba(242,234,247,.06) inset}

  /* ── Full-bleed. Sections that break the container use this rather than
     negative margins, so they cannot reintroduce horizontal overflow. ── */
  .bleed{width:100%;max-width:none;padding-left:0;padding-right:0}

  /* ── Entrances. On grouped CHILDREN only — never a section container. The
     same fade-up on every section is the thing that reads as a template.
     Runs once: the observer unobserves after firing. ── */
  .rise{opacity:0;transform:translateY(14px);
    transition:opacity var(--t-enter) var(--ease),transform var(--t-enter) var(--ease)}
  .rise.in{opacity:1;transform:none}

  /* ── Focus. Visible on both surfaces, and never removed. ── */
  a:focus-visible,button:focus-visible,summary:focus-visible,details:focus-visible{
    outline:2px solid var(--plum);outline-offset:3px;border-radius:4px}
  .band-dark a:focus-visible,.band-dark button:focus-visible,
  .band-dark summary:focus-visible{outline-color:var(--iris)}

  /* GRAIN REMOVED. It was drawn for the dark palette — its own comment says
     the tile exists so panels "get tooth" on a near-black wall. On bone it is
     invisible at 3% and it was still a full-viewport fixed layer sitting above
     everything at z-index 9, which is exactly the sort of always-on paint that
     the demo widget cannot afford. Nothing else about the design depended on
     it: the room's texture now comes from the surface change between bone,
     sand and the instrument panels. */

  a{text-decoration:none;color:inherit}
  ::selection{background:rgba(210,106,251,.3);color:#fff}
  /* Focus has to survive a very dark palette: a two-tone ring so it reads on
     both the void and on a lit surface. */
  :focus-visible{outline:2px solid var(--flare);outline-offset:3px;border-radius:4px}

  /* ── Rim light. A panel catches the light on the edge FACING the source.
     The source is above and to the right for the whole page — one position,
     one falloff, no exceptions — so every rim runs 215deg. Two backgrounds
     (padding-box fill + border-box gradient) instead of a pseudo-element:
     no z-index games, no stacking-context surprises. ── */
  .lit{border:1px solid transparent;border-radius:3px;
    background:linear-gradient(var(--wall),var(--wall)) padding-box,
      linear-gradient(215deg,rgba(184,106,220,.40),rgba(184,106,220,.08) 34%,rgba(242,234,247,.05) 64%,rgba(242,234,247,.018)) border-box}
  .lit-deep{border:1px solid transparent;border-radius:3px;
    background:linear-gradient(var(--void),var(--void)) padding-box,
      linear-gradient(215deg,rgba(184,106,220,.26),rgba(242,234,247,.05) 40%,rgba(242,234,247,.015)) border-box}
  /* The one surface standing nearest the monitor. */
  .lit-near{border:1px solid transparent;border-radius:3px;
    background:linear-gradient(168deg,var(--bruise),#291A33 60%,var(--wall)) padding-box,
      linear-gradient(215deg,rgba(210,106,251,.75),rgba(184,106,220,.22) 30%,rgba(242,234,247,.06) 66%,rgba(242,234,247,.02)) border-box}

  /* ── Type scale ── */
  .kicker{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember-ink);display:flex;align-items:center;gap:12px}
  .kicker::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(247,167,69,.35),transparent);max-width:190px}
  .kicker.center{justify-content:center}
  .kicker.center::before{content:'';flex:1;height:1px;background:linear-gradient(270deg,rgba(247,167,69,.35),transparent);max-width:120px}
  .kicker.center::after{max-width:120px}
  h2.sec-title{font-family:var(--sans);font-weight:700;font-size:clamp(27px,3.4vw,36px);
    line-height:1.14;letter-spacing:-.025em;color:var(--ink);margin:0 0 12px}
  .sec-head.kicked h2.sec-title{margin-top:16px}
  .sec-sub{font-size:16px;color:var(--ink-2);max-width:600px;line-height:1.62}
  .mono-l{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ink-3)}
  .num{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
    font-feature-settings:'tnum' 1,'zero' 1;letter-spacing:-.01em}

  /* ── Buttons. Not painted purple — LIT. The face is a surface in the room and
     the rim is where the monitor hits it; hover moves the light closer. Ink
     stays near-white because violet-on-bruise is 4.3:1 and would fail. ── */
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;cursor:pointer;
    font-family:var(--sans);font-weight:600;font-size:14.5px;letter-spacing:-.005em;
    padding:13px 24px;border-radius:3px;border:1px solid transparent;color:var(--ink);
    transition:background .2s,color .2s;white-space:nowrap}
  .btn-key{background:linear-gradient(168deg,#7B3A9E,#5B2472);border-color:transparent;
    color:#FFF9FE;box-shadow:0 10px 26px -10px rgba(184,106,220,.55),
      0 0 0 1px rgba(242,234,247,.10) inset}
  .btn-key:hover{background:linear-gradient(168deg,#8B44B2,#6A2E8A);
    transform:translateY(-1px);box-shadow:0 2px 4px rgba(23,18,25,.16),0 16px 30px -12px rgba(106,46,138,.7)}
  /* A real press state — the button moves back down and the shadow collapses. */
  .btn-key:active{transform:translateY(1px) scale(.995);
    box-shadow:0 1px 2px rgba(23,18,25,.22),0 4px 10px -6px rgba(106,46,138,.5)}
  .btn-quiet{background:rgba(242,234,247,.04);border:1px solid rgba(242,234,247,.20);color:var(--ink)}
  .btn-quiet:hover{color:#FFF;border-color:rgba(184,106,220,.6);
    background:rgba(184,106,220,.12);transform:translateY(-1px)}
  .btn-quiet:active{transform:translateY(1px) scale(.995)}
  /* On a dark band the quiet button inverts back. */
  .band-dark .btn-quiet{background:transparent;border-color:rgba(242,234,247,.24);color:var(--ink)}
  .band-dark .btn-quiet:hover{color:#FFF;border-color:var(--iris);background:rgba(184,106,220,.12)}
  .btn-lg{padding:16px 30px;font-size:15.5px}
  .btn-wide{width:100%;padding:15px}

  /* ── Layout ── */
  /* ── WIDTHS. The old page ran one 1140px column from top to bottom, which is
   most of why it read as a list: every section had the same silhouette. Now a
   section declares its own measure and the rhythm comes from the difference.
   Text stays readable, product goes wide, three sections break out entirely. ── */
  .wrap{width:100%;max-width:1140px;margin:0 auto;
    padding-left:clamp(20px,4.5vw,72px);padding-right:clamp(20px,4.5vw,72px)}
  .wrap.narrow{max-width:min(760px,100%)}          /* pricing, closing */
  .wrap.reading{max-width:min(78ch,100%)}          /* FAQ, prose */
  .wrap.wide{max-width:min(1560px,94vw)}           /* product, showcase */
  .wrap.full{max-width:none;padding-left:0;padding-right:0}
  /* On a big screen, actually use it — wider gutters and a wider product
     measure, rather than a 1140 column marooned in 1920. */
  @media(min-width:1440px){
    .wrap{max-width:1280px}
    .wrap.wide{max-width:min(1720px,94vw)}
  }
  @media(min-width:1800px){
    .wrap{max-width:1360px}
    body{font-size:17px}
  }
  section{padding-top:46px;padding-bottom:46px}
  /* The chapter rule. Two background layers: the void fills the padding box,
     the gradient shows only through the 1px transparent border — without the
     first layer the gradient paints the whole block instead of the edge. */
  .sec-head{max-width:var(--measure);border-top:1px solid var(--hair);padding-top:24px}
  .sec-head.kicked{border-top:none;background:none;padding-top:0}
  .sec-head.center{max-width:var(--measure);margin:0 auto;text-align:center;
    border-top:1px solid var(--hair)}
  .sec-head.center .sec-sub{margin:0 auto}

  /* ── Nav. Sits IN the room: same black, one hairline that is brighter on the
     side the light comes from. No blur, no glass. ── */
  .nav{position:sticky;top:0;z-index:60;
    background:rgba(23,19,28,.78);
    -webkit-backdrop-filter:saturate(1.4) blur(14px);backdrop-filter:saturate(1.4) blur(14px);
    border-bottom:1px solid var(--hair);
    display:flex;align-items:center;gap:18px;padding:13px 26px}
  /* The hairline under the nav is the through-line's first appearance: it
     brightens as the score climbs, so the mechanic is visible before you have
     scrolled anywhere. Transform/opacity only — this is a colour on a 1px box,
     not a layout property. */
  .nav::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;
    background:linear-gradient(90deg,transparent,rgba(184,106,220,calc(.28 + var(--lit)*.72)) 50%,transparent);
    opacity:calc(.35 + var(--lit)*.65);transition:opacity var(--t-move) var(--ease)}
  .nav-logo{display:flex;align-items:center;gap:10px;flex-shrink:0}
  /* No border-radius any more: that existed only to round the corners of the
     plate the old JPEG carried. The mark is transparent now, so there is no
     rectangle to soften. */
  .nav-logo img{height:22px}
  .nav-logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink)}
  .nav-links{display:flex;align-items:center;gap:2px;margin-left:14px}
  .nav-link{font-family:var(--mono);font-weight:400;font-size:12px;letter-spacing:.02em;
    color:var(--ink-3);padding:8px 11px;border-radius:3px;transition:color .16s,background .16s}
  .nav-link:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .nav-right{margin-left:auto;display:flex;align-items:center;gap:8px}

  /* ── SIGNATURE, persistent form. The trigger score never leaves the screen:
     a live readout welded into the nav, fed by the same loop as the hero demo.
     Below threshold it burns amber (the lamp); above, it snaps violet. ── */
  .trig{display:flex;align-items:center;gap:9px;padding:6px 12px 6px 11px;border-radius:3px;
    border:1px solid transparent;
    background:linear-gradient(var(--wall),var(--wall)) padding-box,
      linear-gradient(215deg,rgba(247,167,69,calc(.30 + var(--lit)*.6)),rgba(242,234,247,.05)) border-box;
    margin-right:6px}
  .trig-k{font-family:var(--mono);font-weight:600;font-size:9.5px;letter-spacing:.18em;
    text-transform:uppercase;color:var(--ink-3)}
  .trig-v{font-family:var(--mono);font-weight:600;font-size:14px;font-variant-numeric:tabular-nums;
    color:var(--ember);min-width:2.2ch;text-align:right;transition:color .3s}
  .trig.hot .trig-v{color:var(--flare)}
  .trig svg{display:block;overflow:visible}
  .trig-line{fill:none;stroke:var(--ember);stroke-width:1.4;stroke-linejoin:round;stroke-linecap:round;transition:stroke .3s}
  .trig.hot .trig-line{stroke:var(--flare)}

  /* ══ HERO. Asymmetric on purpose: the copy holds the left, the demo breaks
     the container on the right and becomes the light source for the page. ══ */
  .hero{position:relative;display:grid;grid-template-columns:minmax(0,.92fr) minmax(0,1.08fr);
    gap:56px;align-items:center;padding-top:58px;padding-bottom:46px}
  /* The light itself. Anchored to where the demo panel actually sits, so it
     reads as spill FROM the panel rather than a decoration floating behind it.
     Unblurred, low alpha, and it brightens when the trigger fires. */
  .room-light{display:none}
  .hero-copy h1{font-family:'Lobster',Georgia,serif;font-weight:400;
    font-size:clamp(44px,7vw,78px);line-height:1.03;letter-spacing:-.005em;
    color:var(--ink);margin:20px 0 22px}
  /* The accent word is LIT, not painted: a solid fill plus the spill it would
     throw onto the dark around it. No gradient, no stroke. */
  .accent{color:#B86ADC;-webkit-text-stroke:0;
    text-shadow:0 0 34px rgba(184,106,220,.42),0 0 10px rgba(184,106,220,.28)}
  /* The page is dark throughout now, so there is no second surface for the
     accent to switch on — one value, and the halo can stay. */
  .band-dark .accent{color:#B86ADC}
  .hero-copy p.lead{font-size:19px;line-height:1.55;color:var(--ink-2);max-width:520px;margin-bottom:32px}
  .hero-ctas{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  .hero-note{font-family:var(--mono);font-size:12px;color:var(--ink-3);letter-spacing:.02em}
  .hero-note b{color:var(--ink-2);font-weight:600}
  /* Tags on a rule, not pills with dots. */
  .tags{display:flex;gap:0;flex-wrap:wrap;margin-top:34px;border-top:1px solid var(--hair);padding-top:16px}
  .tag{font-family:var(--mono);font-size:11px;letter-spacing:.09em;text-transform:uppercase;
    color:var(--ink-3);padding-right:16px;margin-right:16px;border-right:1px solid var(--hair);line-height:1.4}
  .tag:last-child{border-right:none;margin-right:0;padding-right:0}

  /* ── LIVE DEMO — the light source ── */
  /* The light in the room. It is a pseudo-element OF THE PANEL, so it always
     has a position and a falloff that belong to a real object — not a blob
     floating in the background. Unblurred, low alpha, and it brightens with
     --lit when the trigger fires. */
  .demo-wrap{position:relative;min-width:0}
  .demo-wrap::before{content:'';position:absolute;inset:-22% -26%;pointer-events:none;
    background:radial-gradient(54% 50% at 54% 42%,rgba(184,106,220,calc(.17 + var(--lit)*.26)),transparent 70%)}
  .demo{position:relative;border-radius:4px;overflow:hidden;border:1px solid transparent;
    background:linear-gradient(172deg,#211628,var(--wall) 55%,#150F1B) padding-box,
      linear-gradient(215deg,rgba(210,106,251,calc(.55 + var(--lit)*.45)),rgba(184,106,220,.18) 34%,rgba(242,234,247,.05) 70%,rgba(242,234,247,.02)) border-box;
    transition:box-shadow .45s}
  .demo.hot{box-shadow:0 0 0 1px rgba(210,106,251,.28),0 0 60px -26px rgba(210,106,251,.7)}
  .demo-bar{display:flex;align-items:center;gap:9px;padding:10px 14px;border-bottom:1px solid var(--hair)}
  .demo-bar .who{font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:var(--ink-3)}
  .demo-live{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);
    font-size:9.5px;font-weight:600;letter-spacing:.18em;color:var(--ember)}
  .demo-live i{width:5px;height:5px;border-radius:50%;background:var(--ember);
    box-shadow:0 0 8px var(--ember);animation:tally 2.6s ease-in-out infinite}
  @keyframes tally{0%,100%{opacity:1}50%{opacity:.32}}
  .demo-body{padding:15px 16px 13px}
  .demo-head{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;margin-bottom:10px}
  .demo-ch{font-family:var(--mono);font-size:12.5px;color:var(--ink-2);letter-spacing:.02em;
    display:flex;align-items:center;gap:8px;padding-bottom:4px}
  .demo-ch .pd{width:5px;height:5px;border-radius:50%;background:var(--glow);box-shadow:0 0 8px var(--glow)}
  .demo-score{text-align:right;line-height:1}
  .demo-score small{display:block;font-family:var(--mono);font-weight:600;font-size:9.5px;
    letter-spacing:.18em;text-transform:uppercase;color:var(--ink-3);margin-bottom:7px}
  /* The brightest number on the page. */
  .demo-score span{font-family:var(--mono);font-weight:600;font-size:clamp(40px,5vw,56px);
    font-variant-numeric:tabular-nums;letter-spacing:-.03em;color:var(--ember);
    transition:color .3s,text-shadow .3s;display:inline-block}
  .demo.hot .demo-score span{color:var(--flare);text-shadow:0 0 30px rgba(210,106,251,.55)}
  .demo-chart{position:relative;border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);
    padding:2px 0;overflow:hidden}
  .demo-chart svg{display:block;width:100%;height:auto}
  .fired{position:absolute;top:9px;right:11px;display:inline-flex;align-items:center;gap:7px;
    font-family:var(--mono);font-weight:600;font-size:10px;letter-spacing:.16em;color:var(--flare);
    opacity:0;transform:translateY(-4px);transition:opacity .3s,transform .3s}
  .fired.on{opacity:1;transform:none}
  .demo-sigs{display:flex;gap:0;flex-wrap:wrap;margin-top:13px}
  .dsig{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;color:var(--ink-3);
    padding-right:13px;margin-right:13px;border-right:1px solid var(--hair);transition:color .3s}
  .dsig:last-child{border-right:none}
  .dsig.on{color:var(--ember)}
  .demo-clip{margin-top:11px;display:flex;align-items:center;gap:11px;padding-top:11px;
    border-top:1px solid var(--hair);opacity:0;transform:translateY(6px);
    transition:opacity .45s,transform .45s}
  .demo-clip.show{opacity:1;transform:none}
  .dc-thumb{position:relative;width:50px;height:28px;border-radius:2px;flex-shrink:0;
    background:linear-gradient(150deg,#3A2348,#231733);display:grid;place-items:center;
    border:1px solid rgba(242,234,247,.07)}
  .dc-meta{flex:1;min-width:0}
  .dc-t{font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink)}
  .dc-s{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:3px;
    display:flex;align-items:center;gap:7px;letter-spacing:.08em}
  .dc-spin{width:9px;height:9px;border:1.5px solid rgba(242,234,247,.16);border-top-color:var(--glow);
    border-radius:50%;animation:spin .8s linear infinite;flex-shrink:0}
  @keyframes spin{to{transform:rotate(360deg)}}
  .dc-ok{display:none;color:var(--ember)}
  .demo-clip.done .dc-spin{display:none}
  .demo-clip.done .dc-ok{display:inline-flex;align-items:center;gap:5px}
  .demo-cap{font-family:var(--mono);font-size:11px;letter-spacing:.08em;color:var(--ink-3);
    margin-top:14px;text-align:right}

  /* ══ STAT STRIP. Not three equal cards — a readout rail with the numbers
     hung off it at uneven weight, the way a broadcast desk is laid out. ══ */
  /* auto-flow, not fixed columns: the clip-count tile is hidden until there is
     a number to show, and a fixed template would leave its column empty. */
  .stats{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:0;
    border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
  .stat{padding:24px 0 24px 26px;border-left:1px solid var(--hair)}
  .stat:first-child{padding-left:0;border-left:none}
  .stat .n{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
    font-size:34px;letter-spacing:-.03em;line-height:1;color:var(--ink);display:flex;align-items:center;gap:11px}
  .stat.stat-big .n{font-size:clamp(40px,5vw,56px);color:var(--ember-ink)}
  .stat .k{font-size:13px;color:var(--ink-2);margin-top:11px;max-width:26ch;line-height:1.5}

  /* ══ EXAMPLE CLIPS ══ */
  .ex-grid{display:flex;flex-wrap:wrap;justify-content:center;gap:14px;margin-top:38px}
  .ex-card{display:block;border-radius:3px;overflow:hidden;min-width:0;flex:0 1 calc(25% - 10.5px);
    border:1px solid transparent;
    background:linear-gradient(var(--wall),var(--wall)) padding-box,
      linear-gradient(215deg,rgba(184,106,220,.28),rgba(242,234,247,.05) 45%,rgba(242,234,247,.02)) border-box;
    transition:background .25s}
  .ex-card:hover{background:linear-gradient(#231829,#231829) padding-box,
      linear-gradient(215deg,var(--flare),rgba(184,106,220,.3) 40%,rgba(242,234,247,.05)) border-box}
  .ex-media{position:relative;height:146px;background:linear-gradient(150deg,#2E1C3B,#1A1224);overflow:hidden}
  .ex-media img{width:100%;height:100%;object-fit:cover;display:block}
  .ex-media::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 42%,rgba(8,5,11,.72))}
  .ex-play{position:absolute;inset:0;display:grid;place-items:center;z-index:2}
  .ex-play span{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;padding-left:3px;
    background:rgba(14,11,17,.5);border:1px solid rgba(242,234,247,.6);color:var(--ink);transition:.2s}
  .ex-card:hover .ex-play span{background:var(--flare);border-color:transparent;color:#170A1E}
  .ex-badge{position:absolute;top:9px;right:9px;z-index:2;font-family:var(--mono);font-weight:600;
    font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ember);
    background:rgba(14,11,17,.72);padding:4px 8px;border-radius:2px}
  .ex-badge i{display:none}
  .ex-body{padding:12px 13px 14px}
  .ex-title{font-size:13.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ex-meta{font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:5px;
    display:flex;gap:6px;align-items:center;min-width:0;letter-spacing:.04em}
  .ex-meta b{color:var(--glow-ink);font-weight:400}
  .ex-meta span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

  /* ══ WHO IT'S FOR — BREAK 1. Not three cards in a row: hairline-ruled rows
     with a mono label in a fixed left gutter and the prose in a wide right
     column. Different shape, different density, no icon-in-a-tinted-square. ══ */
  .who-list{margin-top:40px;border-top:1px solid var(--hair)}
  .who-row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:26px;
    padding:28px 0;border-bottom:1px solid var(--hair);align-items:start;transition:background .25s}
  .who-row:hover{background:linear-gradient(90deg,rgba(184,106,220,.05),transparent 62%)}
  .who-l{display:flex;align-items:flex-start;justify-content:center;color:var(--ember);padding-top:2px}
  .who-l span{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ink-3)}
  .who-row h3{font-size:19px;font-weight:700;letter-spacing:-.02em;margin-bottom:7px}
  .who-row p{font-size:15px;color:var(--ink-2);line-height:1.62;max-width:60ch}

  /* ══ HOW IT WORKS — BREAK 2. The score, plotted vertically. The rail runs
     amber down the left until step 4, where the clip actually fires and it
     crosses to violet; that step is the one surface standing in the light. ══ */
  .steps{margin-top:40px;position:relative}
  .step{display:grid;grid-template-columns:76px minmax(0,1fr);gap:26px;padding:0 0 34px}
  .step:last-child{padding-bottom:0}
  .rail{position:relative;display:flex;justify-content:center}
  .rail::before{content:'';position:absolute;top:0;bottom:-34px;left:50%;width:1px;
    background:var(--rail,rgba(247,167,69,.3))}
  .step:last-child .rail::before{bottom:auto;height:26px}
  .rail-node{position:relative;z-index:1;margin-top:3px;width:26px;height:26px;border-radius:50%;
    background:var(--void);border:1px solid var(--node,rgba(247,167,69,.45));
    display:grid;place-items:center;font-family:var(--mono);font-weight:600;font-size:11px;
    color:var(--node-ink,var(--ember))}
  .step-2 .rail::before,.step-3 .rail::before{--rail:linear-gradient(180deg,rgba(247,167,69,.3),rgba(184,106,220,.4))}
  .step-3 .rail-node{--node:rgba(184,106,220,.5);--node-ink:var(--glow-ink)}
  .step-4 .rail::before,.step-5 .rail::before{--rail:rgba(184,106,220,.45)}
  .step-4 .rail-node{--node:var(--flare);--node-ink:var(--flare);
    box-shadow:0 0 22px -4px rgba(210,106,251,.75)}
  .step-5 .rail-node{--node:rgba(184,106,220,.5);--node-ink:var(--glow-ink)}
  .step-body{padding:2px 0 0}
  .step h3{font-size:19px;font-weight:700;letter-spacing:-.02em;margin-bottom:7px}
  .step p{font-size:15px;color:var(--ink-2);line-height:1.65;max-width:64ch}
  /* Step 4 is where the threshold is crossed, so it is the panel nearest the
     light — the only one in this section with a surface at all. */
  .step-4 .step-body{padding:20px 22px;margin-top:-16px;border-radius:3px;border:1px solid transparent;
    background:linear-gradient(166deg,#26182F,var(--wall)) padding-box,
      linear-gradient(215deg,rgba(210,106,251,.5),rgba(184,106,220,.12) 38%,rgba(242,234,247,.03)) border-box}

  /* ══ PRODUCT SHOWCASE ══ */
  .shots-top{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:40px}
  .shot-frame{border-radius:3px;overflow:hidden;border:1px solid transparent;
    background:linear-gradient(178deg,#1D1424,#150F1B) padding-box,
      linear-gradient(215deg,rgba(184,106,220,.34),rgba(242,234,247,.055) 42%,rgba(242,234,247,.02)) border-box}
  .shot-bar{display:flex;align-items:center;gap:9px;padding:10px 14px;border-bottom:1px solid var(--hair)}
  .shot-bar i{width:5px;height:5px;border-radius:50%;background:var(--ember);
    box-shadow:0 0 7px rgba(247,167,69,.8);flex-shrink:0}
  .shot-bar span{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ink-3)}
  .shot-inner{padding:17px}
  .shot-cap{margin:15px 1px 0}
  .shot-cap h3{font-size:16.5px;font-weight:700;letter-spacing:-.015em;margin-bottom:5px}
  .shot-cap p{font-size:14px;color:var(--ink-2);line-height:1.58}
  /* mock: stream bubble */
  .mk-stream{border-radius:3px;padding:15px;border:1px solid var(--hair);background:rgba(242,234,247,.016)}
  .mk-row{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
  .mk-nm{font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px;letter-spacing:-.01em}
  .mk-nm .pd{width:5px;height:5px;border-radius:50%;background:var(--glow);box-shadow:0 0 8px var(--glow)}
  .mk-mt{display:flex;gap:12px;align-items:center;margin-top:7px;flex-wrap:wrap}
  .mk-chip{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3)}
  .mk-live{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--ember)}
  .mk-acts{display:flex;gap:7px;flex-shrink:0;align-items:center}
  .mk-clip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10px;
    letter-spacing:.1em;text-transform:uppercase;color:var(--glow-ink);border:1px solid rgba(184,106,220,.32);
    padding:5px 9px;border-radius:2px}
  .mk-x{width:24px;height:24px;border-radius:2px;display:grid;place-items:center;color:var(--ink-3);
    border:1px solid var(--hair);font-size:12px}
  .mk-scorewrap{margin-top:16px}
  .mk-st{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px}
  .mk-sl{font-family:var(--mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3)}
  .mk-sval{font-family:var(--mono);font-weight:600;font-size:21px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
  .mk-track{height:3px;background:rgba(242,234,247,.08);overflow:hidden}
  .mk-fill{height:100%;background:var(--ember)}
  .mk-sigs{display:flex;gap:0;flex-wrap:wrap;margin-top:13px}
  .mk-sig{font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;color:var(--ink-3);
    padding-right:11px;margin-right:11px;border-right:1px solid var(--hair)}
  .mk-sig:last-child{border-right:none}
  .mk-sig.on{color:var(--ember)}
  .mk-prof{margin-top:16px;border-top:1px solid var(--hair);padding-top:14px}
  .mk-pgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .mk-pc .k{font-family:var(--mono);font-size:9px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.14em}
  .mk-pc .v{font-family:var(--mono);font-weight:600;font-size:15px;margin-top:5px;font-variant-numeric:tabular-nums}
  .mk-pc .v small{font-size:9px;color:var(--ink-3);font-weight:400}
  .mk-cal{display:flex;align-items:center;gap:8px;margin-top:14px;font-family:var(--mono);
    font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ember)}
  .mk-cal .tk{font-family:var(--sans);letter-spacing:0}
  /* mock: clip detail */
  .mk-media{position:relative;height:150px;overflow:hidden;display:grid;place-items:center;
    background:linear-gradient(150deg,#2E1C3B,#1A1224)}
  .mk-play{width:46px;height:46px;border-radius:50%;background:rgba(14,11,17,.42);
    border:1px solid rgba(242,234,247,.45);display:grid;place-items:center}
  .mk-badge{position:absolute;top:10px;right:10px;font-family:var(--mono);font-size:10px;
    letter-spacing:.1em;background:rgba(14,11,17,.72);padding:4px 9px;border-radius:2px;color:var(--ember)}
  .mk-badge .pip{display:none}
  .mk-dhead{display:flex;align-items:center;gap:11px;margin-top:16px}
  .mk-av{width:34px;height:34px;border-radius:3px;background:linear-gradient(160deg,#4A2C5C,#2B1A36);
    border:1px solid rgba(184,106,220,.3);display:grid;place-items:center;font-family:var(--mono);
    font-size:11px;font-weight:600;color:#E3C2F4;flex-shrink:0}
  .mk-dhead h4{font-size:14.5px;font-weight:700;letter-spacing:-.01em}
  .mk-dhead .mt{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:3px;letter-spacing:.05em}
  .mk-status{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--ember);flex-shrink:0}
  .mk-eyebrow{font-family:var(--mono);font-size:10px;letter-spacing:.18em;text-transform:uppercase;
    color:var(--ink-3);margin:20px 0 14px}
  .mk-bar{margin-bottom:13px}
  .mk-bh{display:flex;justify-content:space-between;margin-bottom:6px;align-items:baseline}
  .mk-bk{font-size:13px;color:var(--ink-2)}
  .mk-bv{font-family:var(--mono);font-weight:600;font-size:12px;font-variant-numeric:tabular-nums}
  .mk-bt{height:3px;background:rgba(242,234,247,.08);overflow:hidden}
  .mk-bf{height:100%;background:var(--glow)}
  /* mock: clip library */
  .mk-lib{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:13px}
  .mk-card{border:1px solid var(--hair);border-radius:3px;overflow:hidden;background:rgba(242,234,247,.014)}
  .mk-cmedia{position:relative;height:96px;display:grid;place-items:center}
  .mk-cbadge{position:absolute;top:8px;left:8px;font-family:var(--mono);font-size:9.5px;
    letter-spacing:.08em;background:rgba(14,11,17,.7);padding:3px 7px;border-radius:2px}
  .mk-cplay{width:34px;height:34px;border-radius:50%;background:rgba(14,11,17,.4);
    border:1px solid rgba(242,234,247,.4);display:grid;place-items:center}
  .mk-cbody{padding:11px 12px 13px}
  .mk-ctitle{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mk-cmeta{font-family:var(--mono);font-size:10px;color:var(--ink-3);margin-top:6px;
    display:flex;justify-content:space-between;align-items:center;letter-spacing:.06em}
  .mk-cpill{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase}
  .mk-cpill.ok{color:var(--ember)}
  .mk-cpill.pend{color:var(--ink-3)}

  /* ══ FORMULA — BREAK 3. Not a centred card of pills: an actual equation.
     Five measured signals stacked on the left, one score on the right. ══ */
  .formula{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr);gap:50px;
    align-items:center;margin-top:38px;padding:38px 40px;border-radius:3px;border:1px solid transparent;
    background:linear-gradient(172deg,#1C1424,#140F1A) padding-box,
      linear-gradient(215deg,rgba(184,106,220,.34),rgba(242,234,247,.05) 44%,rgba(242,234,247,.018)) border-box}
  .signal-row{display:flex;flex-direction:column;gap:0}
  .signal{display:grid;grid-template-columns:152px minmax(0,1fr);gap:20px;align-items:center;
    padding:11px 0;border-bottom:1px solid var(--hair)}
  .signal:last-of-type{border-bottom:none}
  .signal .sk{font-family:var(--mono);font-size:11.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-2)}
  .signal .sb{height:3px;background:rgba(242,234,247,.08);overflow:hidden}
  .signal .sb i{display:block;height:100%;background:var(--sc,var(--ember))}
  .plus{display:none}
  .formula-out{text-align:left;border-left:1px solid var(--hair);padding-left:34px}
  .formula-out .eq{font-family:var(--mono);font-weight:600;font-size:clamp(46px,6vw,68px);
    line-height:1;letter-spacing:-.04em;color:var(--flare);font-variant-numeric:tabular-nums;
    text-shadow:0 0 40px rgba(210,106,251,.4)}
  .formula-eq{font-size:14.5px;color:var(--ink-2);margin-top:16px;line-height:1.5;max-width:24ch}

  /* ══ FEATURES. Two columns, hairlines instead of cards, mono index instead
     of an icon in a tinted square. ══ */
  .feat-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 46px;margin-top:38px;
    border-top:1px solid var(--hair)}
  .feat{padding:26px 0;border-bottom:1px solid var(--hair)}
  .feat .ic{color:var(--ember);margin-bottom:9px;display:block}
  .feat h3{font-size:16.5px;font-weight:700;letter-spacing:-.015em;margin-bottom:6px}
  .feat p{font-size:14.5px;color:var(--ink-2);line-height:1.6}

  /* ══ PRICING. Depth from value, not shadow: Pro stands nearest the monitor
     and is a lit surface; the other two recede into the wall. ══ */
  .price-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(272px,1fr));gap:16px;margin:42px auto 0}
  .price-card{position:relative;border-radius:3px;border:1px solid transparent;
    background:linear-gradient(var(--wall),var(--wall)) padding-box,
      linear-gradient(215deg,rgba(242,234,247,.16),rgba(242,234,247,.04) 50%,rgba(242,234,247,.015)) border-box}
  .price-card.pro{background:linear-gradient(168deg,#2A1B35,#1B1221 70%) padding-box,
      linear-gradient(215deg,var(--flare),rgba(184,106,220,.28) 34%,rgba(242,234,247,.06) 70%,rgba(242,234,247,.02)) border-box}
  .price-in{padding:34px 30px 32px;height:100%;display:flex;flex-direction:column}
  .price-pop{position:absolute;top:0;right:0;font-family:var(--mono);font-weight:600;font-size:9.5px;
    letter-spacing:.16em;text-transform:uppercase;color:var(--flare);padding:9px 14px}
  .price-badge{font-family:var(--mono);font-weight:600;font-size:10.5px;letter-spacing:.18em;
    text-transform:uppercase;color:var(--ink-3);display:block;margin-bottom:20px}
  .price-card.pro .price-badge{color:var(--glow-ink)}
  .price-amt{display:flex;align-items:baseline;gap:3px}
  .price-amt .cur{font-family:var(--mono);font-size:22px;color:var(--ink-3);align-self:flex-start;margin-top:9px}
  .price-amt .num{font-family:var(--mono);font-weight:600;font-size:64px;letter-spacing:-.05em;
    line-height:1;color:var(--ink);font-variant-numeric:tabular-nums}
  .price-card.pro .price-amt .num{color:var(--ember)}
  .price-amt .per{font-family:var(--mono);font-size:13px;color:var(--ink-3);margin-left:5px}
  .price-sub{font-size:14px;color:var(--ink-2);margin:14px 0 24px;line-height:1.55}
  .price-list{flex:1;display:flex;flex-direction:column;gap:11px;margin-bottom:28px;
    border-top:1px solid var(--hair);padding-top:20px}
  /* NOT flex on the row. With display:flex every inline <b> becomes its own
     flex item, so "Monitor up to <b>3 streams</b> at once" laid out as three
     columns and broke mid-phrase. Absolute-positioning the tick keeps the text
     one normal inline flow that wraps like a sentence. */
  .price-list .li{position:relative;padding-left:24px;font-size:14px;color:var(--ink-2);line-height:1.5}
  .price-list .ck{position:absolute;left:0;top:0;font-family:var(--sans);font-size:12px;color:var(--ember)}
  .price-list .li b{color:var(--ink);font-weight:600}
  .price-promo{font-family:var(--mono);font-size:11.5px;letter-spacing:.06em;color:var(--ink-3);margin-top:20px}
  .price-promo b{color:var(--ember);font-weight:400}

  /* ══ FAQ. Hairline rows, no card. ══ */
  .faq-list{max-width:780px;margin:38px auto 0;border-top:1px solid var(--hair)}
  .faq-item{border-bottom:1px solid var(--hair)}
  .faq-item summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:16px;
    padding:19px 2px;font-size:16px;font-weight:600;letter-spacing:-.01em;
    -webkit-tap-highlight-color:transparent;transition:color .16s}
  .faq-item summary::-webkit-details-marker{display:none}
  .faq-item summary:hover{color:var(--glow-ink)}
  .faq-q{flex:1;min-width:0}
  .faq-c{flex-shrink:0;font-family:var(--mono);font-size:15px;color:var(--ink-3);transition:transform .25s,color .25s}
  .faq-item[open] .faq-c{transform:rotate(45deg);color:var(--flare)}
  .faq-a{padding:0 2px 20px;font-size:14.5px;color:var(--ink-2);line-height:1.72;max-width:70ch}
  .faq-a b{color:var(--ink);font-weight:600}

  /* ══ FINAL CTA — the room at its brightest ══ */
  .final{position:relative;text-align:center;padding-top:66px;padding-bottom:86px}
  .final::before{content:'';position:absolute;left:50%;top:0;width:min(760px,90vw);height:320px;
    transform:translateX(-50%);pointer-events:none;z-index:-1;
    background:radial-gradient(50% 60% at 50% 0%,rgba(184,106,220,.2),transparent 70%)}
  .final h2{font-family:'Lobster',Georgia,serif;font-weight:400;font-size:clamp(34px,5.4vw,58px);
    line-height:1.06;letter-spacing:-.005em;color:var(--ink);margin-bottom:20px}
  .final p{font-size:17px;color:var(--ink-2);max-width:530px;margin:0 auto 32px;line-height:1.6}

  /* ══ FOOTER ══ */
  .footer{border-top:1px solid var(--hair);padding:34px 26px;text-align:center;
    font-family:var(--mono);font-size:11px;letter-spacing:.05em;color:var(--ink-3);line-height:2.1}
  .footer a{color:var(--ink-3);border-bottom:1px solid transparent}
  .footer a:hover{color:var(--ink-2);border-bottom-color:rgba(242,234,247,.2)}
  .footer .fl{margin-bottom:6px}

  /* ══ Example-clip lightbox ══ */
  .exl{position:fixed;inset:0;z-index:90;display:grid;place-items:center;padding:22px}
  .exl-bg{position:absolute;inset:0;background:rgba(8,5,11,.9)}
  .exl-card{position:relative;z-index:1;width:min(920px,100%);border-radius:3px;overflow:hidden;
    background:var(--void);border:1px solid transparent;
    background-image:linear-gradient(var(--void),var(--void)),linear-gradient(215deg,rgba(210,106,251,.5),rgba(242,234,247,.06));
    background-origin:padding-box,border-box;background-clip:padding-box,border-box}
  .exl-frame{position:relative;width:100%;padding-bottom:56.25%;background:#000}
  .exl-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
  .exl-meta{display:flex;align-items:center;gap:12px;padding:13px 16px}
  .exl-title{flex:1;min-width:0;font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .exl-out{font-family:var(--mono);font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--glow-ink);white-space:nowrap}
  .exl-out:hover{color:var(--flare)}
  .exl-close{position:absolute;top:9px;right:9px;z-index:2;width:32px;height:32px;border-radius:2px;
    border:1px solid rgba(242,234,247,.15);cursor:pointer;background:rgba(14,11,17,.8);color:var(--ink);
    font-size:16px;line-height:1;display:grid;place-items:center}
  .exl-close:hover{border-color:var(--flare)}

  /* ══ Responsive ══ */
  @media(max-width:1000px){
    .hero{grid-template-columns:minmax(0,1fr);gap:44px;padding-top:52px}
    .ex-card{flex-basis:calc(33.333% - 9.34px)}
    .formula{grid-template-columns:minmax(0,1fr);gap:30px;padding:32px 28px}
    .formula-out{border-left:none;border-top:1px solid var(--hair);padding-left:0;padding-top:26px}
    .feat-grid{grid-template-columns:1fr;gap:0}
    .who-row{grid-template-columns:74px minmax(0,1fr);gap:20px}
  }
  @media(max-width:720px){
    section{padding-top:38px;padding-bottom:38px}
    .wrap{padding-left:20px;padding-right:20px}
    .ex-card{flex-basis:calc(50% - 7px)}
    .shots-top{grid-template-columns:1fr}
    .stats{grid-auto-flow:row;grid-auto-columns:auto}
    .stat{padding:20px 0;border-left:none;border-top:1px solid var(--hair)}
    .stat:first-child{border-top:none}
    .stat .k{max-width:none}
    .nav{padding:11px 18px;gap:10px}
    .nav-links{display:none}
    .nav-logo span{display:none}
    .trig{padding:5px 9px;margin-right:2px}
    .trig-k{display:none}
    .who-row{grid-template-columns:minmax(0,1fr);gap:10px;padding:24px 0}
    .who-l{justify-content:flex-start}
    .step{grid-template-columns:44px minmax(0,1fr);gap:18px}
    .rail-node{width:22px;height:22px;font-size:10px}
    .step-4 .step-body{padding:16px 16px;margin-top:-12px}
    .demo-cap{text-align:left}
    .price-in{padding:28px 24px}
    .price-amt .num{font-size:52px}
    .final{padding-top:52px;padding-bottom:66px}
  }
  /* The section links collapse at 900, not 720. Measured at 768: logo 205 +
     links 349 + right group 293 + padding 44 = 891, so everything from 721 to
     ~900 pushed the right-hand group off the edge — Sign in and Get started
     included. It was invisible rather than fixed: body{overflow-x:hidden} was
     clipping it, so on every tablet and small laptop the primary CTA simply
     was not there. The links are convenience anchors on a single-scroll page;
     the button is the conversion.
     940, not 900: at 901 the group still overflowed by 22px, so the real
     requirement is ~925 and this leaves headroom for a wider CTA label. */
  @media(max-width:940px){
    .nav-links{display:none}
  }
  @media(max-width:560px){
    /* The live trigger sparkline is the nav's signature, but it is decorative
       and it is 90px wide. Below ~560 the nav is logo(94) + trig(90) +
       Sign in(52) + Get started(124) + padding(36) = 396 > a 390px phone, and
       the overflow lands on the RIGHT — which is the Get started button. That
       used to be invisible because body{overflow-x:hidden} clipped it away;
       with the sticky-nav fix the clipping is honest, so the CTA has to
       actually fit. Dropping the sparkline gets it to 324 with room spare, and
       keeps both links. */
    .nav-right .trig{display:none}
  }
  @media(max-width:520px){
    .ex-card{flex-basis:100%}
    .hero-copy p.lead{font-size:17.5px}
    .hero-ctas{flex-direction:column;align-items:stretch}
    .hero-ctas .btn{width:100%}
    /* Vertical rules only work while the row does not wrap. On a phone it
       always wraps, so the separators become orphans hanging off line ends. */
    .tags{flex-direction:column;gap:4px}
    .tag{border-right:none;padding-right:0;margin-right:0}
    .mk-pgrid{grid-template-columns:repeat(2,1fr)}
  }

  /* ══ MOTION. One orchestrated moment — the trigger firing — and a room that
     breathes. Nothing else moves. ══ */
  @media(prefers-reduced-motion:reduce){
    html{scroll-behavior:auto}
    .demo-live i,.dc-spin{animation:none}
    .demo-wrap::before,.breathe{animation:none}
  }
  @media(prefers-reduced-motion:no-preference){
    .demo-wrap::before{animation:breathe 19s ease-in-out infinite}
    @keyframes breathe{0%,100%{opacity:.94}50%{opacity:1.0}}
  }
</style>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Highlightz", "url": "https://highlightz.app/", "applicationCategory": "MultimediaApplication", "operatingSystem": "Web", "description": "Automatic Twitch clipping: Highlightz watches your live stream and creates Twitch clips of the best moments automatically using a transparent scoring formula \u2014 not AI.", "interactionStatistic": {"@type": "InteractionCounter", "interactionType": "https://schema.org/CreateAction", "userInteractionCount": 0, "description": "Twitch clips created automatically by Highlightz"}, "offers": {"@type": "AggregateOffer", "lowPrice": "0.00", "highPrice": "25.00", "priceCurrency": "USD", "offerCount": "3", "description": "Free plan, Starter $10/month or Pro $25/month. Cancel anytime."}, "publisher": {"@type": "Organization", "name": "ANTI Technology LLC", "url": "https://highlightz.app/", "logo": "https://highlightz.app/static/icon.png"}}</script>
<!--FAQ_SCHEMA-->
</head>
<body>
<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <a href="#how" class="nav-link">How it works</a>
    <a href="#examples" class="nav-link" id="nav-examples" style="display:none">Example clips</a>
    <a href="#features" class="nav-link">Features</a>
    <a href="#pricing" class="nav-link">Pricing</a>
    <a href="#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
  </div>
  <div class="nav-right">
    <!-- The signature, in its persistent form: the live trigger score never
         leaves the screen. Same loop as the hero demo, same threshold. -->
    <div class="trig" id="trig" aria-hidden="true">
      <svg width="42" height="14" viewBox="0 0 42 14"><path class="trig-line" id="trig-line" d="M0,10 L42,10"/></svg>
      <span class="trig-v" id="trig-v">92</span>
    </div>
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-key" style="padding:10px 18px;font-size:13.5px">Get started</a>
  </div>
</nav>

<!-- Hero -->
<!-- ── THE THROUGH-LINE ──────────────────────────────────────────────────────
     One fixed hairline carrying a live trigger score down the page edge. This
     is not a scroll-progress bar with a number bolted on: it writes --lit, the
     same variable every light source on this page already reads from, so the
     page's lighting IS the product's mechanic. It crosses the threshold at
     section boundaries and fires, washing the section being entered.
     Hidden under 900px and inert under prefers-reduced-motion. -->
<div class="thread" id="thread" aria-hidden="true">
  <div class="thread-rail"><span class="thread-fill" id="thread-fill"></span>
    <span class="thread-thresh"></span></div>
  <div class="thread-read"><span class="thread-score" id="thread-score">0</span>
    <span class="thread-lab">score</span></div>
</div>
<header class="wrap hero hero-band">
  <div class="hero-copy">
    <div class="kicker">Automatic Twitch clipping — for clippers and streamers</div>
    <h1>Never miss a <span class="accent">highlight</span> again.</h1>
    <p class="lead">On every channel at once. You can only watch one stream at a time — Highlightz watches all of them, every streamer you clip for plus your own, scoring each one second by second and creating the Twitch clip the moment something pops. <b>Up to 10 channels at the same time on Pro.</b></p>
    <div class="hero-ctas">
      <a href="/login" class="btn btn-key btn-lg">Start clipping now</a>
      <a href="#pricing" class="btn btn-quiet btn-lg">See the plans</a>
    </div>
    <p class="hero-note"><b>Free on 1 channel</b> &middot; no card &middot; 3 channels from $10/mo</p>
    <div class="tags">
      <span class="tag">Up to 10 channels at once</span>
      <span class="tag">Formula-based — not AI</span>
      <span class="tag">Works at any channel size</span>
    </div>
  </div>

  <!-- LIVE DEMO — the light source in the room -->
  <div class="demo-wrap">
    <div class="demo" id="demo">
      <div class="demo-bar"><span class="who">Highlightz — monitoring 4 channels</span><span class="demo-live"><i></i>LIVE</span></div>
      <div class="demo-body">
        <div class="demo-main">
          <div class="demo-head">
            <div class="demo-ch"><span class="pd"></span>novafps</div>
            <div class="demo-score"><small>Trigger score</small><span id="d-score">92</span></div>
          </div>
          <div class="demo-chart">
            <svg viewBox="0 0 520 150" preserveAspectRatio="none" aria-label="Live trigger score chart">
              <defs>
                <linearGradient id="dArea" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="#D26AFB" stop-opacity=".26"/>
                  <stop offset="100%" stop-color="#D26AFB" stop-opacity="0"/>
                </linearGradient>
              </defs>
              <line id="d-thresh" x1="0" y1="64.8" x2="520" y2="64.8" stroke="rgba(242,234,247,.16)" stroke-width="1" stroke-dasharray="3 6"/>
              <text x="6" y="59" font-size="9" fill="#9C90A6" font-family="Plex,ui-monospace,monospace" letter-spacing="1.1">trigger threshold</text>
              <path id="d-area" d="M0,112 L20,109 L40,113 L60,108 L80,111 L100,106 L120,112 L140,107 L160,110 L180,104 L200,109 L220,105 L240,110 L260,103 L280,108 L300,101 L320,97 L340,80 L355,62 L370,44 L385,28 L400,20 L415,17 L430,19 L445,26 L460,38 L475,52 L490,62 L505,68 L520,71 L520,150 L0,150 Z" fill="url(#dArea)"/>
              <path id="d-line" d="M0,112 L20,109 L40,113 L60,108 L80,111 L100,106 L120,112 L140,107 L160,110 L180,104 L200,109 L220,105 L240,110 L260,103 L280,108 L300,101 L320,97 L340,80 L355,62 L370,44 L385,28 L400,20 L415,17 L430,19 L445,26 L460,38 L475,52 L490,62 L505,68 L520,71" fill="none" stroke="#D26AFB" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
              <circle id="d-dot" cx="415" cy="17" r="3.2" fill="#D26AFB"/>
            </svg>
            <span class="fired on" id="d-badge">TRIGGER FIRED</span>
          </div>
          <div class="demo-sigs">
            <span class="dsig on" id="sig-chat">CHAT VELOCITY</span>
            <span class="dsig on" id="sig-audio">AUDIO SPIKE</span>
            <span class="dsig on" id="sig-kw">KEYWORDS</span>
            <span class="dsig" id="sig-sent">SENTIMENT</span>
          </div>
          <div class="demo-clip show done" id="d-clip">
            <div class="dc-thumb"><svg width="13" height="13" viewBox="0 0 24 24" fill="#C489E4"><path d="M8 5v14l11-7z"/></svg></div>
            <div class="dc-meta">
              <div class="dc-t">Big Reaction — clipped automatically</div>
              <div class="dc-s"><span class="dc-spin"></span><span id="d-clipmsg">Creating clip on Twitch&hellip;</span><span class="dc-ok">&#10003; Clip ready</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="demo-cap">Live demo — one of the channels, start to finish. Every channel you add gets its own score, signals and profile.</div>
  </div>
</header>

<!-- Stats band -->
<div class="wrap">
  <div class="stats">
    <div class="stat">
      <div class="n" data-count="10">10</div>
      <div class="k">channels watched at once on Pro &mdash; 3 on Starter</div>
    </div>
    <div class="stat">
      <div class="n" data-count="7">7</div>
      <div class="k">live signals blended into every score</div>
    </div>
    <div class="stat">
      <div class="n" data-count="1" data-suffix="s">1s</div>
      <div class="k">every second of every channel is scored</div>
    </div>
    <!-- Last, not first: hidden until there is a real number, and a hidden
         first child would leave a stray divider at the edge of the band. -->
    <div class="stat stat-big" id="stat-clips" style="display:none">
      <div class="n"><span id="lp-count" data-count="0">0</span></div>
      <div class="k">clips captured and counting</div>
    </div>
  </div>
</div>

<!-- Example clips (admin-curated; hidden until the showcase has entries) -->
<section class="wrap full band-sand seam" id="examples" style="display:none">
  <div class="sec-head">
    <h2 class="sec-title">Real clips, caught automatically</h2>
    <p class="sec-sub">Not a highlight reel we edited — these were clipped by the formula on live streams and approved in the review queue. Tap any of them to watch on Twitch.</p>
  </div>
  <div class="ex-grid" id="ex-grid"></div>
</section>

<!-- Who it's for -->
<section class="wrap wide" id="who">
  <div class="sec-head">
    <h2 class="sec-title">Built for clippers first</h2>
    <p class="sec-sub">If clipping is how you grow — or how you get paid — your ceiling is how many streams you can sit through. Highlightz removes that ceiling. It works just as well pointed at your own channel.</p>
  </div>
  <div class="who-list">
    <div class="who-row">
      <div class="who-l">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.12 15.88"/><path d="M14.47 14.48L20 20"/><path d="M8.12 8.12L12 12"/></svg>
      </div>
      <div>
        <h3>Clippers &amp; editors</h3>
        <p>Point it at every streamer on your list — 3 at once on Starter, 10 on Pro — and let the best moments surface themselves. Each channel learns its own baseline, so a quiet variety streamer and a screaming FPS streamer both trigger fairly. Clips land in one review queue, created under your own Twitch account, ready to cut. You spend your night editing instead of scrubbing.</p>
      </div>
    </div>
    <div class="who-row">
      <div class="who-l">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/><path d="M8 21h8"/></svg>
      </div>
      <div>
        <h3>Streamers</h3>
        <p>Capture your funniest, hypest, and most viral moments live — no mod team or clip-happy chat required. You play, it clips. Add your alt, your co-streamers, or the friends you raid, and the clips come to you.</p>
      </div>
    </div>
    <div class="who-row">
      <div class="who-l">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>
      </div>
      <div>
        <h3>Orgs, community &amp; mod teams</h3>
        <p>Cover every member of the roster at the same time and keep a steady feed of share-ready clips for socials and Discord, pulled straight from the action as it happens.</p>
      </div>
    </div>
  </div>
</section>

<!-- How it works -->
<section class="wrap band-sand seam" id="how">
  <div class="sec-head">
    <h2 class="sec-title">How it works</h2>
    <p class="sec-sub">Five simple steps from "they went live" to a clip in your review queue — running on every channel you add, at the same time.</p>
  </div>
  <!-- The rail IS the score: amber while the stream is below threshold, violet
       from step 4 on, where the clip actually fires. -->
  <div class="steps">
    <div class="step step-1">
      <div class="rail"><span class="rail-node">1</span></div>
      <div class="step-body"><h3>Add every channel you clip for</h3><p>Streamers you clip for, your own channel, or anyone live right now. Paste a name and it starts watching — up to 3 channels at once on Starter and 10 on Pro, all in one dashboard, all running in parallel. You do not have to be watching, or even online.</p></div>
    </div>
    <div class="step step-2">
      <div class="rail"><span class="rail-node">2</span></div>
      <div class="step-body"><h3>A formula scores every second — not AI</h3><p>Highlightz uses a transparent mathematical formula that combines chat speed, audio spikes, keywords, viewer surges, and hype moments into one live score. No AI, no black box — you can watch the score move in real time as the stream unfolds.</p></div>
    </div>
    <div class="step step-3">
      <div class="rail"><span class="rail-node">3</span></div>
      <div class="step-body"><h3>It adapts to every streamer</h3><p>The formula learns each channel's normal — a quiet chess stream and a loud FPS stream trigger with the same fairness. The more it watches a channel, the sharper its judgment becomes.</p></div>
    </div>
    <div class="step step-4">
      <div class="rail"><span class="rail-node">4</span></div>
      <div class="step-body"><h3>Clips are created right on Twitch</h3><p>Fully connected to your Twitch account. When the score crosses the threshold, a real Twitch clip is created instantly under your account — hosted by Twitch and ready to share. Highlightz never records or re-hosts video.</p></div>
    </div>
    <div class="step step-5">
      <div class="rail"><span class="rail-node">5</span></div>
      <div class="step-body"><h3>You stay in control</h3><p>Every clip lands in your review queue. Approve the keepers, reject the misses — and the formula quietly tunes itself to your taste with every decision you make.</p></div>
    </div>
  </div>
</section>

<!-- Product showcase -->
<section class="wrap wide" id="product">
  <div class="sec-head">
    <h2 class="sec-title">See it in action</h2>
    <p class="sec-sub">A clean, real-time dashboard that shows you exactly what's happening and why.</p>
  </div>
  <div class="shots-top">
    <!-- Active stream bubble -->
    <div class="shot">
      <div class="shot-frame">
        <div class="shot-bar"><i></i><span>Monitoring</span></div>
        <div class="shot-inner">
          <div class="mk-stream">
            <div class="mk-row">
              <div>
                <div class="mk-nm"><span class="pd"></span>novafps</div>
                <div class="mk-mt"><span class="mk-chip" style="color:#C489E4">twitch</span><span class="mk-chip">fps</span><span class="mk-live">live</span></div>
              </div>
              <div class="mk-acts">
                <span class="mk-clip"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>Clip</span>
                <span class="mk-x">&#215;</span>
              </div>
            </div>
            <div class="mk-scorewrap">
              <div class="mk-st"><span class="mk-sl">Trigger score</span><span class="mk-sval" style="color:#F7A745">72.4</span></div>
              <div class="mk-track"><div class="mk-fill" style="width:72%"></div></div>
              <div class="mk-sigs">
                <span class="mk-sig on">CHAT_VELOCITY: 0.81</span>
                <span class="mk-sig on">AUDIO_SPIKE: 0.64</span>
                <span class="mk-sig">KEYWORD: 0.30</span>
                <span class="mk-sig">SENTIMENT: 0.12</span>
              </div>
            </div>
            <div class="mk-prof">
              <div class="mk-pgrid">
                <div class="mk-pc"><div class="k">Threshold</div><div class="v">58</div></div>
                <div class="mk-pc"><div class="k">Velocity</div><div class="v">4.2<small> m/s</small></div></div>
                <div class="mk-pc"><div class="k">Clips</div><div class="v">37</div></div>
                <div class="mk-pc"><div class="k">Approval</div><div class="v" style="color:#F7A745">78%</div></div>
              </div>
              <div class="mk-cal"><span class="tk">&#10003;</span> Calibrated &middot; 142 samples</div>
            </div>
          </div>
        </div>
      </div>
      <div class="shot-cap"><h3>Live stream monitoring</h3><p>Each channel gets its own bubble with a live trigger score, the exact signals firing, and a learned profile that calibrates to that streamer.</p></div>
    </div>

    <!-- Clip score breakdown -->
    <div class="shot">
      <div class="shot-frame">
        <div class="shot-bar"><i></i><span>Clip detail</span></div>
        <div class="shot-inner">
          <div class="mk-media">
            <div class="mk-play"><svg width="18" height="18" viewBox="0 0 24 24" fill="#F2EAF7"><path d="M8 5v14l11-7z"/></svg></div>
            <span class="mk-badge"><span class="pip"></span>84% trigger</span>
          </div>
          <div class="mk-dhead">
            <span class="mk-av">NF</span>
            <div style="flex:1;min-width:0"><h4>Insane 1v5 clutch to win it</h4><div class="mt">novafps &middot; VALORANT &middot; 2m ago</div></div>
            <span class="mk-status">approved</span>
          </div>
          <div class="mk-eyebrow">Why it fired</div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Chat velocity</span><span class="mk-bv" style="color:#D26AFB">88%</span></div><div class="mk-bt"><div class="mk-bf" style="width:88%;background:#D26AFB"></div></div></div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Audio spike</span><span class="mk-bv" style="color:#B86ADC">73%</span></div><div class="mk-bt"><div class="mk-bf" style="width:73%"></div></div></div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Keyword hits</span><span class="mk-bv" style="color:#F7A745">61%</span></div><div class="mk-bt"><div class="mk-bf" style="width:61%;background:#F7A745"></div></div></div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Sentiment</span><span class="mk-bv" style="color:#9C90A6">47%</span></div><div class="mk-bt"><div class="mk-bf" style="width:47%;background:#9C90A6"></div></div></div>
        </div>
      </div>
      <div class="shot-cap"><h3>Every clip, fully explained</h3><p>Open any clip to see the precise breakdown of which signals triggered it — no AI guesswork, just the numbers behind the moment.</p></div>
    </div>
  </div>

  <!-- Clip library -->
  <div class="shot" style="margin-top:18px">
    <div class="shot-frame">
      <div class="shot-bar"><i></i><span>Clip library</span></div>
      <div class="shot-inner">
        <div class="mk-lib">
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(150deg,#2E1C3B,#1A1224)"><div class="mk-cplay"><svg width="14" height="14" viewBox="0 0 24 24" fill="#F2EAF7"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#F7A745">96%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Triple kill, no scope</div><div class="mk-cmeta"><span>novafps</span><span class="mk-cpill ok">approved</span></div></div>
          </div>
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(150deg,#341F42,#1B1224)"><div class="mk-cplay"><svg width="14" height="14" viewBox="0 0 24 24" fill="#F2EAF7"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#9C90A6">81%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Unreal comeback round</div><div class="mk-cmeta"><span>peachy_kat</span><span class="mk-cpill pend">pending</span></div></div>
          </div>
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(150deg,#27203F,#191226)"><div class="mk-cplay"><svg width="14" height="14" viewBox="0 0 24 24" fill="#F2EAF7"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#F7A745">74%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Clutch ace, full team</div><div class="mk-cmeta"><span>drift_season</span><span class="mk-cpill ok">approved</span></div></div>
          </div>
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(150deg,#2B1B3B,#181428)"><div class="mk-cplay"><svg width="14" height="14" viewBox="0 0 24 24" fill="#F2EAF7"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#9C90A6">68%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Perfect comedic timing</div><div class="mk-cmeta"><span>moonvale</span><span class="mk-cpill pend">pending</span></div></div>
          </div>
        </div>
      </div>
    </div>
    <div class="shot-cap"><h3>Your clip library</h3><p>Every captured moment in one place, scored and ready to review. Approve the keepers, reject the rest — the formula learns from every call.</p></div>
  </div>
</section>

<!-- Not AI / formula -->
<section class="band-dark seam" id="formula"><div class="wrap">
  <div class="sec-head kicked">
    <div class="kicker">100% transparent</div>
    <h2 class="sec-title">A formula you can actually understand</h2>
    <p class="sec-sub">Highlightz isn't powered by AI guesswork. It's a clear, explainable formula that blends real signals from the stream into a single live score. Every trigger has a reason you can see.</p>
  </div>
  <div class="formula">
    <div class="signal-row">
      <div class="signal" style="--sc:#D26AFB"><span class="sk">Chat speed</span><span class="sb"><i style="width:82%"></i></span></div>
      <div class="plus">+</div>
      <div class="signal" style="--sc:#B86ADC"><span class="sk">Audio spikes</span><span class="sb"><i style="width:64%"></i></span></div>
      <div class="plus">+</div>
      <div class="signal" style="--sc:#9C7BD2"><span class="sk">Keywords</span><span class="sb"><i style="width:47%"></i></span></div>
      <div class="plus">+</div>
      <div class="signal" style="--sc:#F7A745"><span class="sk">Viewer surges</span><span class="sb"><i style="width:58%"></i></span></div>
      <div class="plus">+</div>
      <div class="signal" style="--sc:#E08C3A"><span class="sk">Hype moments</span><span class="sb"><i style="width:71%"></i></span></div>
    </div>
    <div class="formula-out">
      <div class="eq num">84</div>
      <div class="formula-eq">= one live highlight score, adapting to each streamer</div>
    </div>
  </div>
</div></section>

<!-- Features -->
<section class="wrap wide" id="features">
  <div class="sec-head">
    <h2 class="sec-title">Everything in the box</h2>
    <p class="sec-sub">A complete clipping toolkit that runs on every channel at once, while you do everything else.</p>
  </div>
  <div class="feat-grid">
    <div class="feat">
      <div>
        <span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span>
        <h3>Up to 10 channels at once</h3>
        <p>1 channel free, 3 on Starter, 10 on Pro — watched simultaneously from one dashboard, each with its own independent learning profile. Nothing queues behind anything else.</p>
      </div>
    </div>
    <div class="feat">
      <div>
        <span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span>
        <h3>Real-time detection</h3>
        <p>Streams are scored second by second, so highlights are caught the instant they happen — not hours later in a VOD.</p>
      </div>
    </div>
    <div class="feat">
      <div>
        <span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z"/><path d="M12 7v5l3 3"/></svg></span>
        <h3>Adaptive per-channel learning</h3>
        <p>Every channel you add gets its own baseline and its own threshold. Loud or quiet, fast chat or slow — each one is calibrated fairly and independently.</p>
      </div>
    </div>
    <div class="feat">
      <div>
        <span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M5 12l5 5L20 7"/></svg></span>
        <h3>One queue for every channel</h3>
        <p>Clips from all your channels land in a single review queue — 15 pending free, 50 on Starter, 200 on Pro. Your decisions feed straight back into that channel's formula, sharpening it over time.</p>
      </div>
    </div>
    <div class="feat">
      <div>
        <span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg></span>
        <h3>Live score analytics</h3>
        <p>Watch the highlight score rise and fall in real time, with a clear breakdown of which signals are firing.</p>
      </div>
    </div>
    <div class="feat">
      <div>
        <span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 2L4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4z"/></svg></span>
        <h3>Real Twitch clips, zero risk</h3>
        <p>Every clip is created through Twitch's official API under your own account — hosted by Twitch, never recorded or re-hosted by us.</p>
      </div>
    </div>
  </div>
</section>

<!-- Pricing -->
<section class="band-sand seam" id="pricing"><div class="wrap">
  <div class="sec-head center">
    <h2 class="sec-title">More channels, more clips.</h2>
    <p class="sec-sub">Every plan is the full product — paid tiers just point it at more streamers at the same time. No card to start, no contracts, cancel anytime.</p>
  </div>
  <div class="price-grid">
    <div class="price-card">
      <div class="price-in">
        <span class="price-badge">Free</span>
        <div class="price-amt"><span class="cur">$</span><span class="num">0</span><span class="per">/forever</span></div>
        <div class="price-sub">The real product on <b>1 channel</b>. No card.</div>
        <div class="price-list">
          <div class="li"><span class="ck">&#10003;</span>Monitor <b>1 channel</b></div>
          <div class="li"><span class="ck">&#10003;</span>Automatic clip detection, live on Twitch</div>
          <div class="li"><span class="ck">&#10003;</span>Review queue for <b>15 pending clips</b></div>
          <div class="li"><span class="ck">&#10003;</span>Adaptive, per-channel scoring formula</div>
          <div class="li"><span class="ck">&#10003;</span>Live trigger-score analytics</div>
        </div>
        <a href="/login" class="btn btn-quiet btn-wide">Start free &#8594;</a>
      </div>
    </div>
    <div class="price-card">
      <div class="price-in">
        <span class="price-badge">Starter</span>
        <div class="price-amt"><span class="cur">$</span><span class="num">10</span><span class="per">/month</span></div>
        <div class="price-sub">For clipping a small roster &mdash; <b>3&times; the coverage</b>.</div>
        <div class="price-list">
          <div class="li"><span class="ck">&#10003;</span>Monitor <b>3 channels at once</b></div>
          <div class="li"><span class="ck">&#10003;</span>Everything in Free</div>
          <div class="li"><span class="ck">&#10003;</span>Review queue for <b>50 pending clips</b></div>
          <div class="li"><span class="ck">&#10003;</span>Adaptive, per-channel scoring formula</div>
          <div class="li"><span class="ck">&#10003;</span>Live trigger-score analytics</div>
        </div>
        <a href="/login" class="btn btn-quiet btn-wide">Choose Starter &#8594;</a>
      </div>
    </div>
    <div class="price-card pro">
      <div class="price-in">
        <span class="price-pop">Most popular</span>
        <span class="price-badge">Highlightz Pro</span>
        <div class="price-amt"><span class="cur">$</span><span class="num">25</span><span class="per">/month</span></div>
        <div class="price-sub">The full toolkit for serious clippers &mdash; <b>10&times; the coverage</b>.</div>
        <div class="price-list">
          <div class="li"><span class="ck">&#10003;</span>Monitor <b>10 channels at once</b></div>
          <div class="li"><span class="ck">&#10003;</span>Everything in Starter</div>
          <div class="li"><span class="ck">&#10003;</span>Review queue for <b>200 pending clips</b></div>
          <div class="li"><span class="ck">&#10003;</span><b>VOD Scanner</b> — find highlights in past broadcasts</div>
        </div>
        <a href="/login" class="btn btn-key btn-wide">Go Pro &#8594;</a>
      </div>
    </div>
  </div>
  <div class="price-promo" style="text-align:center">Have a promo code? Enter it at checkout for <b>50% off your first month</b>.</div>
</div></section>

<!-- FAQ -->
<section class="wrap reading" id="faq">
  <div class="sec-head center">
    <h2 class="sec-title">Frequently asked questions</h2>
    <p class="sec-sub">Quick answers to the things people ask before starting.</p>
  </div>
  <div class="faq-list">
    <details class="faq-item">
      <summary><span class="faq-q">How many channels can I watch at once?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">One on the free plan, <b>3 at the same time on Starter</b>, and <b>10 at the same time on Pro</b>. They all run in parallel — nothing waits in line behind anything else — and each channel keeps its own independent learning profile, so watching ten does not blunt any one of them.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Can I clip channels I don't own?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Yes — that's what most people use it for. Add any live Twitch channel and Highlightz clips it through Twitch's official Clips API using your authorized account, exactly as if you had pressed Twitch's own Clip button while watching. The clip is hosted by Twitch and attributed to you, the same as a manual clip. Streamers who would rather not be clipped through Highlightz can opt out at any time on our opt-out page, and we honour it immediately.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Do I have to be watching for it to work?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">No. Highlightz runs on our servers, not in your browser, so your tab can be closed and your PC can be off. Add a channel before the streamer is even live and it keeps checking every 30 seconds until they are, then watches the whole broadcast and leaves the clips in your queue for when you get back. The one thing it asks is that you check in: if an account shows no activity at all for 8 hours, its streams are stopped so slots aren't held open by abandoned sessions.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">How does Highlightz know what to clip?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">It watches your stream's live signals — chat speed, audio spikes, keywords, viewer surges, and hype moments — and blends them into one score, second by second. Every channel gets its own baseline, so a spike is measured against <b>your</b> normal, not someone else's. When the score crosses your channel's threshold, the clip fires.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Is this AI?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">No. Highlightz runs on a transparent mathematical formula, not a black-box model. You can watch the score move in real time and open any clip to see exactly which signals fired and why.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Do you record or store my stream?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Never. When a moment hits, Highlightz asks Twitch to create a real Twitch clip through the official API — the clip is hosted by Twitch, attributed to your account, exactly as if you'd clicked the Clip button yourself. We never record, download, or re-host video.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Is this allowed on Twitch?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Yes — clips are created through Twitch's official Clips API with your authorized account, the same mechanism as Twitch's own Clip button. Streamers who don't want their channel clipped through Highlightz can also opt out at any time via our opt-out page.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">How long are the clips?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Twitch clips capture roughly the last 30 seconds around the moment — our timing places the highlight inside that window, build-up and payoff. Want longer? Any clip can be trimmed or extended up to 60 seconds in Twitch's own clip editor.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Does it work for small channels?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Yes — this is the whole point of per-channel calibration. A 5-viewer chat and a 50,000-viewer chat get judged with the same fairness, because the formula learns what's normal for each channel and reacts to relative spikes, not raw numbers.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">How does billing work?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">There is a <b>free plan with no card required</b> — one monitored channel and a 15-clip review queue, for as long as you like. Paid plans are Starter at $10/month (3 channels at once, 50-clip queue) and Pro at $25/month (10 channels at once, 200-clip queue, plus the VOD Scanner). Both renew monthly and you can cancel anytime through the billing portal — no contracts, no cancellation hoops.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">What if I don't like the clips it takes?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Every clip lands in your review queue first — approve the keepers, reject the misses. The formula learns from every decision: rejections raise that channel's bar, approvals lower it, so it steadily tunes itself to your taste.</div>
    </details>
    <details class="faq-item">
      <summary><span class="faq-q">Do you support platforms other than Twitch?</span><span class="faq-c">+</span></summary>
      <div class="faq-a">Twitch is fully supported today. More platforms are on the roadmap — follow along in the app for updates.</div>
    </details>
  </div>
</section>

<!-- Final CTA -->
<section class="band-dark final-band seam"><div class="wrap narrow final">
  <h2>Ten streams are live right now.<br><span class="accent">You can only watch one.</span></h2>
  <p>Connect your Twitch account and add your first channel free — then point Highlightz at the whole roster and let it catch the highlights on all of them at once.</p>
  <a href="/login" class="btn btn-key btn-lg">Start clipping now</a>
  <a href="/tutorial" class="btn btn-quiet btn-lg" style="margin-left:10px">Read the walkthrough</a>
</div></section>

<div class="exl" id="exl" style="display:none" role="dialog" aria-modal="true">
  <div class="exl-bg" id="exl-bg"></div>
  <div class="exl-card">
    <button class="exl-close" id="exl-close" aria-label="Close">&#215;</button>
    <div class="exl-frame"><iframe id="exl-iframe" allowfullscreen scrolling="no" title="Clip player"></iframe></div>
    <div class="exl-meta">
      <div class="exl-title" id="exl-title"></div>
      <a class="exl-out" id="exl-out" target="_blank" rel="noopener">Watch on Twitch &#8599;</a>
    </div>
  </div>
</div>

<footer class="footer">
  <div class="fl">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved.</div>
  <a href="/tutorial">Tutorial</a> &middot; <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a> &middot; <a href="/opt-out">Streamer Opt-Out</a>
</footer>
<script>
/* ── Live clips counter ── */
(function(){
  var tile=document.getElementById('stat-clips'), el=document.getElementById('lp-count');
  if(!tile||!el) return;
  // The server has already rendered the real number into the markup, so start
  // the animation FROM it. Counting up from zero would visibly wipe out the
  // server-rendered value for a second and, worse, would put a literal 0 back
  // in the DOM — which is the state a crawler might sample.
  var from=parseInt((el.textContent||'0').replace(/[^0-9]/g,''),10)||0;
  fetch('/landing/stats').then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(!d||typeof d.clips_total!=='number'||d.clips_total<=0) return;
    tile.style.display='';
    var target=d.clips_total;
    if(target===from) return;                 // nothing changed; leave it alone
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      el.textContent=target.toLocaleString('en-US'); return;
    }
    var started=null;
    function tick(ts){
      if(started===null) started=ts;
      var p=Math.min((ts-started)/1400,1);
      var eased=1-Math.pow(1-p,3);
      el.textContent=Math.round(from+(target-from)*eased).toLocaleString('en-US');
      if(p<1) requestAnimationFrame(tick);
    }
    // Start counting when the tile scrolls into view
    if('IntersectionObserver' in window){
      var io=new IntersectionObserver(function(es){
        es.forEach(function(e){ if(e.isIntersecting){ requestAnimationFrame(tick); io.disconnect(); } });
      },{threshold:0.4});
      io.observe(tile);
    } else { requestAnimationFrame(tick); }
  }).catch(function(){});
})();

/* ── Example clips showcase ── */
(function(){
  var sec=document.getElementById('examples'), grid=document.getElementById('ex-grid'), nav=document.getElementById('nav-examples');
  if(!sec||!grid) return;
  // Built with RegExp(), not a literal. This string is a Python triple-quoted
  // block, and Python resolves escapes before the browser ever sees them — a
  // backslash in a JS regex literal here is a landmine, not an escape.
  var SLUG=new RegExp('/clip/([^/?#]+)');
  fetch('/landing/showcase').then(function(r){return r.ok?r.json():null;}).then(function(d){
    var clips=(d&&d.clips)||[];
    if(!clips.length) return;
    clips.forEach(function(c){
      var a=document.createElement('a');
      a.className='ex-card'; a.href=c.twitch_url||'#'; a.target='_blank'; a.rel='noopener';
      a.addEventListener('click', function(ev){
        // Inline player on desktop; small screens keep the direct Twitch link
        // (Twitch's embed is unreliable in many mobile browsers).
        if(window.innerWidth<=700) return;
        var src=c.embed_url;
        if(!src && c.twitch_url){
          var m=SLUG.exec(c.twitch_url);
          if(m) src='https://clips.twitch.tv/embed?clip='+m[1];
        }
        if(!src) return;   // no way to embed — let the link do its thing
        ev.preventDefault();
        var lb=document.getElementById('exl');
        var ifr=document.getElementById('exl-iframe');
        document.getElementById('exl-title').textContent=(c.clip_title||'Clip')+' — '+(c.channel||'');
        document.getElementById('exl-out').href=c.twitch_url||'#';
        ifr.src=src+(src.indexOf('?')>=0?'&':'?')+'parent='+location.hostname+'&autoplay=true';
        lb.style.display='';
        document.body.style.overflow='hidden';
      });
      var media=document.createElement('div'); media.className='ex-media';
      if(c.thumbnail_url){
        var img=document.createElement('img'); img.loading='lazy'; img.alt='';
        img.src=c.thumbnail_url;
        img.onerror=function(){ img.remove(); };
        media.appendChild(img);
      }
      var play=document.createElement('div'); play.className='ex-play';
      play.innerHTML='<span><svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"></path></svg></span>';
      media.appendChild(play);
      if(c.score>0){
        var badge=document.createElement('span'); badge.className='ex-badge';
        badge.appendChild(document.createTextNode(c.score+'% trigger'));
        media.appendChild(badge);
      }
      var body=document.createElement('div'); body.className='ex-body';
      var title=document.createElement('div'); title.className='ex-title';
      title.textContent=c.clip_title||'Clip';
      var meta=document.createElement('div'); meta.className='ex-meta';
      var ch=document.createElement('b'); ch.textContent=c.channel||'';
      meta.appendChild(ch);
      if(c.game){ var g=document.createElement('span'); g.textContent='· '+c.game; meta.appendChild(g); }
      body.appendChild(title); body.appendChild(meta);
      a.appendChild(media); a.appendChild(body);
      grid.appendChild(a);
    });
    sec.style.display='';
    if(nav) nav.style.display='';
  }).catch(function(){});
  function closeLb(){
    var lb=document.getElementById('exl');
    if(!lb||lb.style.display==='none') return;
    lb.style.display='none';
    document.getElementById('exl-iframe').src='about:blank';   // stop playback
    document.body.style.overflow='';
  }
  var bg=document.getElementById('exl-bg'), x=document.getElementById('exl-close');
  if(bg) bg.addEventListener('click', closeLb);
  if(x) x.addEventListener('click', closeLb);
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeLb(); });
})();

/* ── THE SIGNATURE ──────────────────────────────────────────────────────────
   The trigger score is the light in the room. One scripted ~11s capture drives
   it: calm chat and a wandering score, chat floods, the score crosses the
   threshold, the clip is created. Everything lit on this page reads the same
   number:

     --lit   0..1 on <html>, how hard the trigger is firing. The nav rim, the
             hero wash and the demo panel's own rim all scale off it, so when
             the capture fires the ROOM brightens, not just the widget.
     .trig   a permanent readout welded into the nav — amber below threshold
             (the lamp behind you), violet above it (the monitor).

   The markup ships the FIRED end state, so no-JS and reduced-motion visitors
   see a finished capture rather than an empty frame. ───────────────────────*/
(function(){
  var reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var demo=document.getElementById('demo');
  var line=document.getElementById('d-line'), area=document.getElementById('d-area');
  var dot=document.getElementById('d-dot'), badge=document.getElementById('d-badge');
  var scoreEl=document.getElementById('d-score'), clipCard=document.getElementById('d-clip');
  var sigChat=document.getElementById('sig-chat'), sigAudio=document.getElementById('sig-audio'),
      sigKw=document.getElementById('sig-kw'), sigSent=document.getElementById('sig-sent');
  var trig=document.getElementById('trig'), trigV=document.getElementById('trig-v'),
      trigLine=document.getElementById('trig-line');
  var root=document.documentElement;
  if(!demo||!line||reduce) return;   // static fired-state markup stays as-is

  var DUR=11000, WINDOW=8000, W=520, H=150, THRESH=60;

  function yFor(s){ return 144 - s*1.32; }

  var samples=[], lastSample=0, fired=false, clipShown=false, clipDone=false, seed=7, lastLit=-1;
  function rnd(){ seed=(seed*16807)%2147483647; return (seed-1)/2147483646; }

  function scoreAt(t){
    var base;
    if(t<5200)      base=26+6*Math.sin(t/900)+5*Math.sin(t/370);
    else if(t<6400) { var p=(t-5200)/1200; var e=p*p*(3-2*p); base=30+e*62; }
    else if(t<7600) base=92-3*Math.sin((t-6400)/300);
    else if(t<9800) { var q=(t-7600)/2200; base=89-q*46; }
    else            base=43-((t-9800)/1200)*12;
    return Math.max(6,Math.min(97,base+(rnd()*4-2)));
  }

  function setSigs(t){
    var on1=t>=5350&&t<8600, on2=t>=5750&&t<8300, on3=t>=6050&&t<8000, on4=t>=6300&&t<7500;
    sigChat.classList.toggle('on',on1);
    sigAudio.classList.toggle('on',on2);
    sigKw.classList.toggle('on',on3);
    sigSent.classList.toggle('on',on4);
  }

  function reset(){
    samples=[]; lastSample=0; fired=false; clipShown=false; clipDone=false; seed=7;
    badge.classList.remove('on'); demo.classList.remove('hot');
    if(trig) trig.classList.remove('hot');
    clipCard.classList.remove('show','done');
    dot.setAttribute('r','0');
  }
  reset();

  // The chart is the only expensive part of the loop. The nav readout is on
  // screen for the whole page, so it keeps updating; the SVG paths stop the
  // moment the hero scrolls away.
  var heroVisible=true;
  if('IntersectionObserver' in window){
    new IntersectionObserver(function(es){ heroVisible=es[0].isIntersecting; },
      {rootMargin:'120px'}).observe(demo);
  }

  var t0=null;
  function frame(now){
    if(t0===null) t0=now;
    var t=(now-t0)%DUR;
    if(t<lastSample){ reset(); }   // loop wrapped

    if(t-lastSample>=70){
      lastSample=t;
      var s=scoreAt(t);
      samples.push([t,s]);
      while(samples.length&&samples[0][0]<t-WINDOW) samples.shift();
      var shown=Math.round(s);
      scoreEl.textContent=String(shown);
      if(trigV) trigV.textContent=String(shown);

      // One number, one room. Only written when it actually moves — a custom
      // property on <html> invalidates style for the whole document, so this
      // must not fire on every single frame.
      var lit=Math.max(0,Math.min(1,(s-THRESH)/34));
      var q=Math.round(lit*20)/20;
      if(q!==lastLit){ lastLit=q; root.style.setProperty('--lit',String(q)); }

      if(heroVisible){
        var d='',ax,ay;
        for(var i=0;i<samples.length;i++){
          ax=((samples[i][0]-(t-WINDOW))/WINDOW)*W;
          ay=yFor(samples[i][1]);
          d+=(i===0?'M':' L')+ax.toFixed(1)+','+ay.toFixed(1);
        }
        if(samples.length){
          line.setAttribute('d',d);
          area.setAttribute('d',d+' L'+W+','+H+' L'+((samples[0][0]-(t-WINDOW))/WINDOW*W).toFixed(1)+','+H+' Z');
          dot.setAttribute('cx',ax.toFixed(1)); dot.setAttribute('cy',ay.toFixed(1));
        }
      }

      // The nav sparkline: the last 14 samples, 42x14.
      if(trigLine&&samples.length>1){
        var n=Math.min(14,samples.length), td='';
        for(var j=0;j<n;j++){
          var sv=samples[samples.length-n+j][1];
          td+=(j===0?'M':' L')+((j/(n-1))*42).toFixed(1)+','+(13-(sv/100)*12).toFixed(1);
        }
        trigLine.setAttribute('d',td);
      }

      if(!fired&&s>=THRESH&&t>4000){
        fired=true;
        badge.classList.add('on'); demo.classList.add('hot');
        if(trig) trig.classList.add('hot');
        dot.setAttribute('r','3.2');
      }
      if(fired&&!clipShown&&t>=7100){
        clipShown=true; clipCard.classList.add('show');
      }
      if(clipShown&&!clipDone&&t>=8700){
        clipDone=true; clipCard.classList.add('done');
      }
      if(t>=10300){
        badge.classList.remove('on'); demo.classList.remove('hot');
        if(trig) trig.classList.remove('hot');
      }
    }
    setSigs(t);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
</script>

<script>
/* ── THE THROUGH-LINE + COUNT-UP ───────────────────────────────────────────
   Everything here is rAF-throttled and writes only --lit, transforms and
   opacity. No layout property is animated anywhere in this block.
   NOTE: this whole file is a Python triple-quoted string, so a single
   backslash would be eaten before the browser ever sees it. There are none. */
(function(){
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var root = document.documentElement;

  /* ── count-up: stat digits animate once, staggered, when scrolled into view */
  function countUp(el, delay){
    var raw = el.getAttribute('data-count');
    var target = parseFloat(raw);
    if (isNaN(target)) return;
    var suffix = el.getAttribute('data-suffix') || '';
    if (reduce){ el.textContent = target.toLocaleString() + suffix; return; }
    var dur = 900, t0 = 0;
    function step(ts){
      if (!t0) t0 = ts;
      var k = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - k, 3);
      var v = target * eased;
      el.textContent = (target >= 100 ? Math.round(v).toLocaleString()
                                      : (Math.round(v * 10) / 10).toString().replace('.0','')) + suffix;
      if (k < 1) requestAnimationFrame(step);
    }
    setTimeout(function(){ requestAnimationFrame(step); }, delay);
  }

  var seen = new WeakSet();
  var io = ('IntersectionObserver' in window) ? new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if (!e.isIntersecting || seen.has(e.target)) return;
      seen.add(e.target);
      var el = e.target;
      if (el.classList.contains('rise')){ el.classList.add('in'); io.unobserve(el); return; }
      if (el.hasAttribute('data-count')){
        countUp(el, parseInt(el.getAttribute('data-delay') || '0', 10));
        io.unobserve(el);
      }
    });
  }, { rootMargin: '0px 0px -12% 0px', threshold: 0.2 }) : null;

  if (io){
    /* Entrances go on grouped CHILDREN only — stat digits, clip cards, feature
       blocks. Never on a section container: the same fade-up on every section
       is exactly what makes a page read as a template. */
    Array.prototype.forEach.call(document.querySelectorAll('.rise'), function(el, i){
      el.style.transitionDelay = (Math.min(i, 6) * 70) + 'ms';
      io.observe(el);
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-count]'), function(el, i){
      el.setAttribute('data-delay', (i * 80).toString());
      io.observe(el);
    });
  }

  /* ── the score itself. One rAF-throttled scroll read; writes --lit and the
     readout, and fires a wash when it crosses the threshold entering a band. */
  var thread = document.getElementById('thread');
  var scoreEl = document.getElementById('thread-score');
  var seams = Array.prototype.slice.call(document.querySelectorAll('.seam, .wash'));
  var ticking = false, lastFired = -1;

  function frame(){
    ticking = false;
    var h = document.documentElement.scrollHeight - window.innerHeight;
    var y = window.scrollY || window.pageYOffset;
    var prog = h > 0 ? Math.min(1, Math.max(0, y / h)) : 0;

    /* The score is not the scroll position. It rides a wave so it rises and
       falls the way a real trigger score does, and peaks at section seams. */
    var mid = window.innerHeight * 0.5;
    var nearest = 1;
    for (var i = 0; i < seams.length; i++){
      var r = seams[i].getBoundingClientRect();
      var d = Math.abs(r.top - mid) / window.innerHeight;
      if (d < nearest) nearest = d;
    }
    var closeness = Math.max(0, 1 - nearest);
    /* Two components. A slow continuous drift so the score is never static —
       a trigger score that sits on one number reads as broken — plus a peak as
       a section seam passes the middle of the viewport, which is where the
       threshold gets crossed and the wash fires. */
    var drift = 0.30 + 0.16 * Math.sin(prog * 18.0);
    var lit = Math.max(0.06, Math.min(1, drift + closeness * 0.52));
    root.style.setProperty('--lit', lit.toFixed(3));
    if (scoreEl) scoreEl.textContent = Math.round(lit * 100);

    var over = lit > 0.62;
    if (thread) thread.classList.toggle('fired', over);

    /* Wash the section being entered, once per crossing. */
    for (var j = 0; j < seams.length; j++){
      var rect = seams[j].getBoundingClientRect();
      var entering = rect.top < window.innerHeight * 0.72 && rect.bottom > window.innerHeight * 0.25;
      seams[j].classList.toggle('lit', entering && over);
    }
  }
  function onScroll(){ if (!ticking){ ticking = true; requestAnimationFrame(frame); } }

  if (!reduce){
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    frame();
  } else if (thread){ thread.style.display = 'none'; }
})();
</script>
</body>
</html>"""


def _faq_schema(html: str) -> str:
    """Build the FAQPage JSON-LD from the FAQ the page actually shows.

    It used to be a hand-written duplicate sitting in <head>, several thousand
    characters away from the markup it described. Editing the visible FAQ
    without editing the copy left Google reading answers the page no longer
    gave — which is the specific thing structured-data penalties exist for, and
    it is invisible in the browser so nothing catches it. Deriving it means the
    two cannot disagree.
    """
    items = []
    for block in re.findall(
            r'<details class="faq-item">(.*?)</details>', html, re.S):
        q = re.search(r'<span class="faq-q">(.*?)</span>', block, re.S)
        a = re.search(r'<div class="faq-a">(.*?)</div>', block, re.S)
        if not q or not a:
            continue
        # Schema wants prose, not markup: <b> and friends are presentation, and
        # the entities (&mdash;, &middot;) have to be real characters or they
        # end up double-escaped inside the JSON string.
        text = unescape(re.sub(r"<[^>]+>", "", a.group(1))).strip()
        items.append({"@type": "Question",
                      "name": unescape(q.group(1)).strip(),
                      "acceptedAnswer": {"@type": "Answer", "text": text}})
    blob = json.dumps({"@context": "https://schema.org",
                       "@type": "FAQPage", "mainEntity": items})
    return '<script type="application/ld+json">' + blob + "</script>"


LANDING_HTML = LANDING_HTML.replace("<!--FAQ_SCHEMA-->", _faq_schema(LANDING_HTML), 1)

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz - Sign In</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:24px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:44px 40px;width:360px;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:22px}
  .logo-wrap img{height:54px;width:auto;filter:drop-shadow(0 0 18px rgba(199,155,255,.4))}
  h1{font-size:26px;font-weight:800;color:#c79bff;margin-bottom:4px;letter-spacing:-.02em}
  .sub{font-size:13px;color:#9c9caa;margin-bottom:18px}
  .price-pill{display:inline-flex;align-items:center;gap:7px;background:rgba(145,70,255,.14);border:1px solid rgba(145,70,255,.35);color:#c79bff;font-size:12px;font-weight:700;padding:8px 14px;border-radius:99px;margin-bottom:22px}
  .price-pill .dot{width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e}
  .price-note{font-size:11px;color:#5d5d6b;text-align:center;margin-top:12px}
  .twitch-btn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;background:#9146ff;color:#fff;border:none;border-radius:12px;padding:13px;font-size:14px;font-weight:700;cursor:pointer;text-decoration:none;transition:background .15s}
  .twitch-btn:hover{background:#772ce8}
  .twitch-btn svg{flex-shrink:0}
  .or-divider{display:flex;align-items:center;gap:12px;margin:14px 0;color:#5d5d6b;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
  .or-divider::before,.or-divider::after{content:'';flex:1;height:1px;background:rgba(255,255,255,.08)}
  .divider{display:flex;align-items:center;gap:12px;margin:20px 0;color:#5d5d6b;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
  .divider::before,.divider::after{content:'';flex:1;height:1px;background:rgba(255,255,255,.08)}
  label{font-size:12px;color:#9c9caa;display:block;margin-bottom:6px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
  input{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:10px;color:#f6f6f9;padding:11px 13px;font-size:14px;outline:none;margin-bottom:14px;transition:.18s}
  input:focus{border-color:rgba(199,155,255,.5);box-shadow:0 0 0 4px rgba(168,85,247,.1)}
  .pw-btn{width:100%;background:rgba(255,255,255,.06);color:#f6f6f9;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:11px;font-size:13px;font-weight:600;cursor:pointer;transition:.15s}
  .pw-btn:hover{background:rgba(255,255,255,.1)}
  .error{color:#ff5a78;font-size:12px;margin-bottom:14px;background:rgba(255,90,120,.12);padding:10px 13px;border-radius:10px;border:1px solid rgba(255,90,120,.25)}
  .admin-toggle{font-size:11px;color:#5d5d6b;text-align:center;margin-top:18px;cursor:pointer;text-decoration:underline}
  #admin-form{display:none;margin-top:16px}
  .footer{margin-top:28px;text-align:center;font-size:11px;color:#3d3d4a;line-height:1.7}
  .footer a{color:#5d5d6b;text-decoration:none}.footer a:hover{color:#9c9caa}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo-mark.png" alt="Highlightz logo"></div>
  <h1>Highlightz</h1>
  <p class="sub">Sign in to start clipping highlights</p>
  <div class="price-pill"><span class="dot"></span>Free to start &mdash; no card required</div>
  {error}
  <a href="/auth/twitch" class="twitch-btn">
    <svg width="20" height="20" viewBox="0 0 2400 2800" fill="#fff"><path d="M500 0L0 500v1800h600v500l500-500h400l900-900V0H500zm1700 1300l-400 400h-400l-350 350v-350H600V200h1600v1100z"/><path d="M1700 550h-200v600h200V550zm-550 0h-200v600h200V550z"/></svg>
    Continue with Twitch
  </a>
  <p class="price-note">Signing in is free. Paid plans are optional and start at $10/month.</p>
  <p class="admin-toggle" onclick="document.getElementById('admin-form').style.display='block';this.style.display='none'">Admin sign-in</p>
  <div id="admin-form">
    <div class="divider">admin access</div>
    <form method="POST" action="/login">
      <label>Password</label>
      <input type="password" name="password" placeholder="Admin password" autocomplete="current-password">
      <button type="submit" class="pw-btn">Sign In</button>
    </form>
  </div>
</div>
<div class="footer">
  &copy; 2026 ANTI Technology LLC &mdash; All rights reserved.<br>
  <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a><br>
  <a href="/opt-out">Streamer Opt-Out</a>
</div>
</body>
</html>"""

PAYWALL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz - Subscribe</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:20px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:48px 44px;max-width:560px;width:100%;text-align:center;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .plan-row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:8px;text-align:left}
  .plan{position:relative;border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:20px 18px;display:flex;flex-direction:column}
  .plan.pro{border-color:rgba(168,85,247,.55);box-shadow:0 0 30px -12px rgba(168,85,247,.5)}
  .plan-pop{position:absolute;top:-9px;left:50%;transform:translateX(-50%);font-size:9.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#fff;background:linear-gradient(135deg,#f943ff,#a855f7);padding:3px 10px;border-radius:99px;white-space:nowrap}
  .plan-name{font-size:13px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:#c79bff}
  .plan-price{font-size:28px;font-weight:800;margin:4px 0 10px}
  .plan-price span{font-size:13px;color:#9c9caa;font-weight:600}
  .plan-feats{list-style:none;margin:0 0 16px;padding:0;flex:1}
  .plan-feats li{font-size:12.5px;color:#b8b8c8;padding:3px 0 3px 16px;position:relative}
  .plan-feats li::before{content:'✓';position:absolute;left:0;color:#34d399;font-weight:800;font-size:11px}
  .plan .cta{margin-bottom:0;padding:11px;font-size:13.5px}
  .cta.ghost{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);box-shadow:none}
  .cta.ghost:hover{background:rgba(255,255,255,.12);filter:none}
  @media(max-width:520px){.plan-row{grid-template-columns:1fr}}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:20px}
  .logo-wrap img{height:46px;filter:drop-shadow(0 0 14px rgba(199,155,255,.4))}
  .badge{display:inline-flex;align-items:center;gap:6px;background:rgba(199,155,255,.12);border:1px solid rgba(199,155,255,.25);color:#c79bff;font-size:11px;font-weight:700;padding:5px 12px;border-radius:99px;letter-spacing:.06em;text-transform:uppercase;margin-bottom:22px}
  h1{font-size:30px;font-weight:800;letter-spacing:-.025em;margin-bottom:10px}
  .sub{font-size:14px;color:#9c9caa;margin-bottom:32px;line-height:1.6}
  .features{text-align:left;margin-bottom:32px;display:flex;flex-direction:column;gap:10px}
  .feat{display:flex;align-items:center;gap:12px;font-size:14px}
  .feat .ic{width:24px;height:24px;border-radius:8px;background:rgba(199,155,255,.12);color:#c79bff;display:grid;place-items:center;flex-shrink:0;font-size:13px}
  .cta{display:block;width:100%;background:linear-gradient(135deg,#f943ff 0%,#a855f7 52%,#7c6bff 100%);color:#fff;border:none;border-radius:13px;padding:14px;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none;transition:filter .15s;box-shadow:0 6px 24px -6px rgba(168,85,247,.6);margin-bottom:12px}
  .cta:hover{filter:brightness(1.08)}
  .manage{display:block;font-size:12px;color:#5d5d6b;text-align:center;margin-top:6px;text-decoration:none}
  .manage:hover{color:#9c9caa}
  .logout{display:block;font-size:12px;color:#5d5d6b;text-align:center;margin-top:16px;text-decoration:none}
  .logout:hover{color:#9c9caa}
  .footer{margin-top:28px;text-align:center;font-size:11px;color:#3d3d4a;line-height:1.7}
  .footer a{color:#5d5d6b;text-decoration:none}.footer a:hover{color:#9c9caa}
@media(max-width:480px){
  .card{padding:32px 22px;border-radius:18px}
  h1{font-size:24px}
}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo-mark.png" alt="Highlightz"></div>
  <span class="badge">Highlightz</span>
  <h1>{headline}</h1>
  <p class="sub">Hi {username} — {subline}</p>
  <div class="plan-row">
    <div class="plan">
      <div class="plan-name">Starter</div>
      <div class="plan-price">$10<span>/mo</span></div>
      <ul class="plan-feats">
        <li>3 monitored streams</li>
        <li>50-clip review queue</li>
        <li>Live clip detection &amp; analytics</li>
      </ul>
      <a href="/billing/checkout?plan=starter" class="cta ghost">Choose Starter</a>
    </div>
    <div class="plan pro">
      <div class="plan-pop">Most popular</div>
      <div class="plan-name">Pro</div>
      <div class="plan-price">$25<span>/mo</span></div>
      <ul class="plan-feats">
        <li>10 monitored streams</li>
        <li>200-clip review queue</li>
        <li>VOD Scanner included</li>
      </ul>
      <a href="/billing/checkout?plan=pro" class="cta">Choose Pro</a>
    </div>
  </div>
  <p class="sub" style="font-size:13px;margin-top:14px">{cta_note}</p>
  <a href="/billing/portal" class="manage">Already subscribed? Manage billing</a>
  <a href="#" class="logout" onclick="fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';});return false;">Sign out</a>
</div>
<div class="footer">
  &copy; 2026 ANTI Technology LLC &mdash; All rights reserved.<br>
  <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a>
</div>
</body>
</html>"""

TOS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Terms of Service — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.7;padding:0 0 80px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.15),transparent 60%)}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .back{display:inline-flex;align-items:center;gap:8px;color:#5d5d6b;font-size:13px;text-decoration:none;margin-bottom:40px;transition:.15s}
  .back:hover{color:#c79bff}
  .logo{display:flex;align-items:center;gap:14px;margin-bottom:32px}
  .logo img{height:30px;filter:drop-shadow(0 0 10px rgba(199,155,255,.4))}
  .logo span{font-size:22px;font-weight:800;color:#c79bff;letter-spacing:-.02em}
  h1{font-size:32px;font-weight:800;letter-spacing:-.03em;margin-bottom:8px}
  .meta{font-size:13px;color:#5d5d6b;margin-bottom:48px}
  h2{font-size:17px;font-weight:700;color:#c79bff;margin:36px 0 12px;letter-spacing:-.01em}
  p{font-size:14px;color:#b8b8c8;margin-bottom:14px}
  ul{padding-left:20px;margin-bottom:14px}
  li{font-size:14px;color:#b8b8c8;margin-bottom:6px}
  a{color:#c79bff;text-decoration:none}
  a:hover{text-decoration:underline}
  .divider{height:1px;background:rgba(255,255,255,.07);margin:48px 0 0}
  .footer{margin-top:24px;font-size:12px;color:#3d3d4a;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <a href="/login" class="back">&#8592; Back to Highlightz</a>
  <div class="logo">
    <img src="/static/logo-mark.png" alt="Highlightz">
    <span>Highlightz</span>
  </div>
  <h1>Terms of Service</h1>
  <p class="meta">Effective date: June 20, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>Please read these Terms of Service ("Terms") carefully before using Highlightz ("Service"), operated by ANTI Technology LLC ("we," "us," or "our"). By accessing or using the Service you agree to be bound by these Terms. If you do not agree, do not use the Service.</p>

  <h2>1. Description of Service</h2>
  <p>Highlightz is a SaaS platform that monitors live streams on Twitch and Kick, automatically detects highlight moments from public signals such as chat activity and stream audio levels, and — at your direction and on your behalf — creates clips using Twitch's official Clips API. Clips are created, processed, hosted, and stored by Twitch on Twitch's own infrastructure under your Twitch account. Kick stream monitoring uses only Kick's publicly accessible chat and stream-status data — no Kick account credentials are required. Highlightz does not record, copy, download, or re-host stream video from either platform. The Service requires an active paid subscription to access core features.</p>

  <h2>2. Eligibility</h2>
  <p>You must be at least 18 years old to use the Service. By using the Service you represent and warrant that you meet this requirement and that all information you provide is accurate and complete.</p>

  <h2>3. Accounts and Platform Authorization</h2>
  <p>You sign in by authorizing the Service through your Twitch account via OAuth2. By connecting your Twitch account you grant the Service permission to create clips on your behalf using Twitch's Clips API (the <code>clips:edit</code> permission). Every clip created through the Service is made with <em>your</em> Twitch credentials and is attributed to <em>your</em> Twitch account, exactly as if you had clicked Twitch's own "Clip" button.</p>
  <p>Kick stream monitoring does not require a Kick account. The Service reads only publicly available Kick chat messages and stream-status information — the same data accessible to any viewer — via Kick's public WebSocket and API. No Kick credentials are stored.</p>
  <p>You are responsible for maintaining the confidentiality of your account and for all activity that occurs under it, including all clips created through it. Notify us immediately at the contact address below if you suspect unauthorized use. We reserve the right to terminate accounts that violate these Terms.</p>

  <h2>4. Free Trial and Subscriptions</h2>
  <p>Access to the Service requires a paid subscription, billed from the moment you subscribe. We may, at our sole discretion, grant individual accounts free promotional or trial access for a limited period. Promotional access requires no payment method, ends automatically at the end of its stated period without any charge, and does not convert into a paid subscription unless you subscribe yourself. We may modify or withdraw promotional access at any time.</p>
  <p>Subscriptions are billed on a recurring basis through our payment processor, Stripe. By subscribing you authorize us to charge the payment method on file for each billing period until you cancel.</p>
  <ul>
    <li>You may cancel your subscription at any time through the billing portal. Cancellation takes effect at the end of the current billing period.</li>
    <li>We do not issue refunds for partial billing periods or unused time.</li>
    <li>We reserve the right to change pricing with at least 14 days notice to your registered email address.</li>
    <li>Failed payments may result in suspension or termination of your account.</li>
  </ul>

  <h2>5. Clips, Streamer Content, and Your Responsibility</h2>
  <p><strong>You — not Highlightz — create the clips, and you are solely responsible for them.</strong> When the Service creates a clip, it does so on your behalf and with your authorization through Twitch's official Clips API, using your Twitch account. The resulting clip is owned, hosted, and governed by Twitch. Highlightz acts only as a tool that you direct; it never records, stores, or re-hosts any stream video itself.</p>
  <p>When monitoring Kick channels, the Service reads publicly available chat and stream data only. Any highlight detected on a Kick stream is presented to you for review; clip creation remains your action and your responsibility. The same streamer-content responsibilities described below apply equally to channels on any supported platform.</p>
  <p>You acknowledge and agree that:</p>
  <ul>
    <li>Any clip you create may contain content owned by the broadcaster you clipped, by game publishers, by music rights holders, or by other third parties.</li>
    <li><strong>You are solely and exclusively responsible for clipping, saving, sharing, downloading, exporting, posting, or otherwise distributing any clip of any streamer or channel</strong>, including streamers other than yourself, and for obtaining any permission or license required to do so.</li>
    <li>You are solely responsible for complying with Twitch's Terms of Service, the Twitch Developer Services Agreement, the policies of any broadcaster you clip, and all applicable copyright, trademark, publicity, and other laws.</li>
    <li>ANTI Technology LLC does not pre-screen, monitor, review, endorse, or control which channels you choose to monitor or clip, or what you do with the clips afterward, and assumes no liability for those choices.</li>
  </ul>
  <p>If a broadcaster, rights holder, platform, or any other party objects to a clip you created or how you used it, that dispute is between you and that party. You agree that ANTI Technology LLC bears no responsibility or liability for it.</p>

  <h2>6. Acceptable Use</h2>
  <p>You agree not to use the Service to:</p>
  <ul>
    <li>Create, save, or distribute content that infringes the intellectual property, publicity, or other rights of any broadcaster or third party.</li>
    <li>Clip or distribute content in violation of a broadcaster's stated wishes, Twitch's policies, or applicable law.</li>
    <li>Attempt to reverse-engineer, disassemble, or otherwise derive the source code of the Service.</li>
    <li>Circumvent any security or access controls, including those of Twitch.</li>
    <li>Use the Service for any unlawful purpose or in violation of any applicable law or regulation.</li>
    <li>Resell, sublicense, or otherwise commercialize access to the Service without our written consent.</li>
  </ul>
  <p>We reserve the right to suspend or terminate your access immediately if we determine, in our sole discretion, that you have violated these Terms.</p>

  <h2>7. Intellectual Property</h2>
  <p>The Highlightz name, logo, software, branding, and all related materials are the exclusive property of ANTI Technology LLC and are protected by applicable intellectual property laws. Nothing in these Terms grants you any right to use our trademarks or branding without prior written consent.</p>

  <h2>8. Third-Party Services</h2>
  <p>The Service integrates with third-party platforms including Twitch (authentication and clip creation), Kick (public stream and chat monitoring), and Stripe (payments). Your use of those platforms is governed by their respective terms of service, including the <a href="https://www.twitch.tv/p/legal/terms-of-service/">Twitch Terms of Service</a>, the <a href="https://legal.twitch.com/legal/developer-agreement/">Twitch Developer Services Agreement</a>, and the <a href="https://kick.com/terms-of-service">Kick Terms of Service</a>. We are not responsible for the availability, accuracy, or practices of any third-party service.</p>

  <h2>9. Data and Privacy</h2>
  <p>We collect and process information necessary to operate the Service, including your Twitch account information and access tokens (stored in encrypted form), payment information (processed by Stripe — we do not store card details), and clip metadata such as clip links and trigger scores. We do not store stream video. We do not sell your personal data to third parties. By using the Service you consent to this processing, as further described in our <a href="/privacy">Privacy Policy</a>.</p>

  <h2>10. Disclaimers</h2>
  <p>THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR FREE OF HARMFUL COMPONENTS.</p>

  <h2>11. Limitation of Liability</h2>
  <p>TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW, ANTI TECHNOLOGY LLC AND ITS OFFICERS, EMPLOYEES, AND AGENTS SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, INCLUDING LOSS OF PROFITS, DATA, OR GOODWILL, ARISING OUT OF OR IN CONNECTION WITH YOUR USE OF THE SERVICE, EVEN IF WE HAVE BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. OUR TOTAL LIABILITY TO YOU FOR ANY CLAIMS ARISING UNDER THESE TERMS SHALL NOT EXCEED THE AMOUNT YOU PAID TO US IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM.</p>

  <h2>12. Indemnification</h2>
  <p>You agree to indemnify, defend, and hold harmless ANTI Technology LLC and its officers, employees, and agents from and against any claims, liabilities, damages, losses, and expenses (including reasonable legal fees) arising out of or related to your use of the Service; any clip you create, save, post, distribute, or monetize; any content of any streamer or third party that appears in a clip you create; or your violation of these Terms, of Twitch's terms or policies, or of any applicable copyright, trademark, publicity, or other law.</p>

  <h2>13. Termination</h2>
  <p>We may suspend or terminate your access to the Service at any time, with or without cause, with or without notice. Upon termination, your right to use the Service ceases immediately. Provisions that by their nature should survive termination (including sections 5, 7, 10, 11, and 12) shall survive.</p>

  <h2>14. Changes to These Terms</h2>
  <p>We may update these Terms from time to time. We will notify you of material changes by posting the updated Terms at this URL and updating the effective date. Your continued use of the Service after such changes constitutes acceptance of the updated Terms.</p>

  <h2>15. Governing Law</h2>
  <p>These Terms are governed by the laws of the United States and the state in which ANTI Technology LLC is incorporated, without regard to conflict of law principles. Any disputes shall be resolved in the courts of competent jurisdiction in that state.</p>

  <h2>16. Contact</h2>
  <p>Questions about these Terms? Contact us at:<br>
  <strong>ANTI Technology LLC</strong><br>
  Email: <a href="mailto:support@highlightz.app">support@highlightz.app</a></p>

  <div class="divider"></div>
  <div class="footer">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved.</div>
</div>
</body>
</html>"""

PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.7;padding:0 0 80px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.15),transparent 60%)}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .back{display:inline-flex;align-items:center;gap:8px;color:#5d5d6b;font-size:13px;text-decoration:none;margin-bottom:40px;transition:.15s}
  .back:hover{color:#c79bff}
  .logo{display:flex;align-items:center;gap:14px;margin-bottom:32px}
  .logo img{height:30px;filter:drop-shadow(0 0 10px rgba(199,155,255,.4))}
  .logo span{font-size:22px;font-weight:800;color:#c79bff;letter-spacing:-.02em}
  h1{font-size:32px;font-weight:800;letter-spacing:-.03em;margin-bottom:8px}
  .meta{font-size:13px;color:#5d5d6b;margin-bottom:48px}
  h2{font-size:17px;font-weight:700;color:#c79bff;margin:36px 0 12px;letter-spacing:-.01em}
  p{font-size:14px;color:#b8b8c8;margin-bottom:14px}
  ul{padding-left:20px;margin-bottom:14px}
  li{font-size:14px;color:#b8b8c8;margin-bottom:6px}
  a{color:#c79bff;text-decoration:none}
  a:hover{text-decoration:underline}
  .divider{height:1px;background:rgba(255,255,255,.07);margin:48px 0 0}
  .footer{margin-top:24px;font-size:12px;color:#3d3d4a;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <a href="/login" class="back">&#8592; Back to Highlightz</a>
  <div class="logo">
    <img src="/static/logo-mark.png" alt="Highlightz">
    <span>Highlightz</span>
  </div>
  <h1>Privacy Policy</h1>
  <p class="meta">Effective date: June 20, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>This Privacy Policy describes how ANTI Technology LLC ("we," "us," or "our") collects, uses, and shares information when you use Highlightz ("Service"). By using the Service you agree to the practices described here.</p>

  <h2>1. Information We Collect</h2>
  <p>We collect only what is necessary to operate the Service:</p>
  <ul>
    <li><strong>Account information</strong> — your Twitch user ID, login, display name, and avatar URL, obtained when you sign in via Twitch OAuth2.</li>
    <li><strong>Twitch access tokens</strong> — the OAuth access and refresh tokens that authorize the Service to create clips on your behalf. These are stored in encrypted form and are never shared.</li>
    <li><strong>Kick public data</strong> — when you monitor a Kick channel, we read publicly available chat messages and live-stream status from Kick's public API and WebSocket. We do not collect or store any personal data about Kick viewers or streamers beyond the channel slug you enter. No Kick credentials are requested or stored.</li>
    <li><strong>Billing information</strong> — payment processing is handled entirely by Stripe. We store only your Stripe Customer ID and subscription status. We never see or store your card details.</li>
    <li><strong>Clip metadata</strong> — channel names, platform identifiers, timestamps, trigger scores, and the Twitch clip links generated for your account. We do not store any stream video; clips are hosted by Twitch.</li>
    <li><strong>Session data</strong> — a server-side session cookie that keeps you signed in (see our <a href="/cookies">Cookie Policy</a>).</li>
    <li><strong>Log data</strong> — server logs may contain IP addresses and request metadata for security and debugging purposes.</li>
  </ul>

  <h2>2. How We Use Your Information</h2>
  <ul>
    <li>To authenticate you and maintain your session.</li>
    <li>To create clips on your behalf via Twitch's Clips API when you or your trigger settings direct it.</li>
    <li>To process payments and manage your subscription via Stripe.</li>
    <li>To display your clip links and trigger analytics in your dashboard.</li>
    <li>To investigate security incidents and prevent abuse.</li>
  </ul>

  <h2>3. How We Share Your Information</h2>
  <p>We do not sell your personal data. We share information only with the following third parties as necessary to operate the Service:</p>
  <ul>
    <li><strong>Twitch</strong> — for authentication and for creating clips on your behalf. Governed by Twitch's Privacy Notice.</li>
    <li><strong>Kick</strong> — public chat and stream-status data is read from Kick's public API and WebSocket. We do not transmit any of your personal data to Kick. Governed by Kick's Privacy Policy.</li>
    <li><strong>Stripe</strong> — for payment processing. Governed by Stripe's Privacy Policy.</li>
  </ul>
  <p>We may disclose your information if required by law, regulation, or valid legal process.</p>

  <h2>4. Data Retention</h2>
  <p>We retain your account information, encrypted Twitch tokens, and clip metadata for as long as your account is active. When you delete your account, we remove your user record, encrypted tokens, clip metadata, and stream configurations. Clips you have already created remain hosted on Twitch under your Twitch account and are governed by Twitch; you can manage or delete them through Twitch. Log files may be retained for up to 90 days for security purposes.</p>

  <h2>5. Your Rights</h2>
  <p>You may request access to, correction of, or deletion of your personal data at any time by contacting us at <a href="mailto:support@highlightz.app">support@highlightz.app</a>, or by deleting your account directly from the Account settings page within the dashboard.</p>
  <p>If you are in the European Economic Area (EEA) or United Kingdom, you have additional rights under GDPR/UK GDPR, including the right to data portability and the right to lodge a complaint with your local supervisory authority.</p>

  <h2>6. Security</h2>
  <p>We implement reasonable technical and organizational safeguards including session-based authentication, HTTPS-only transmission, encryption of stored Twitch tokens at rest, and per-user data isolation. No system is perfectly secure; we encourage you to protect your Twitch account with a strong, unique password and two-factor authentication.</p>

  <h2>6a. Clips and Streamer Content</h2>
  <p>Clips you create through the Service are created with your Twitch account and are hosted by Twitch, not by us. You are solely responsible for the clips you create and for how you share or distribute them, including clips of streamers other than yourself. See Section 5 of our <a href="/tos">Terms of Service</a> for details on your responsibilities.</p>

  <h2>7. Children</h2>
  <p>The Service is not directed at persons under 18 years of age. We do not knowingly collect personal data from minors. If you believe a minor has provided us with data, contact us and we will delete it promptly.</p>

  <h2>8. Changes to This Policy</h2>
  <p>We may update this Privacy Policy from time to time. We will notify you of material changes by posting the updated policy at this URL and updating the effective date. Continued use of the Service after such changes constitutes acceptance.</p>

  <h2>9. Contact</h2>
  <p>Privacy questions or requests:<br>
  <strong>ANTI Technology LLC</strong><br>
  Email: <a href="mailto:support@highlightz.app">support@highlightz.app</a></p>

  <div class="divider"></div>
  <div class="footer">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved. &middot; <a href="/tos" style="color:#5d5d6b">Terms of Service</a> &middot; <a href="/cookies" style="color:#5d5d6b">Cookie Policy</a></div>
</div>
</body>
</html>"""

COOKIES_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cookie Policy — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.7;padding:0 0 80px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.15),transparent 60%)}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .back{display:inline-flex;align-items:center;gap:8px;color:#5d5d6b;font-size:13px;text-decoration:none;margin-bottom:40px;transition:.15s}
  .back:hover{color:#c79bff}
  .logo{display:flex;align-items:center;gap:14px;margin-bottom:32px}
  .logo img{height:30px;filter:drop-shadow(0 0 10px rgba(199,155,255,.4))}
  .logo span{font-size:22px;font-weight:800;color:#c79bff;letter-spacing:-.02em}
  h1{font-size:32px;font-weight:800;letter-spacing:-.03em;margin-bottom:8px}
  .meta{font-size:13px;color:#5d5d6b;margin-bottom:48px}
  h2{font-size:17px;font-weight:700;color:#c79bff;margin:36px 0 12px;letter-spacing:-.01em}
  p{font-size:14px;color:#b8b8c8;margin-bottom:14px}
  table{width:100%;border-collapse:collapse;margin-bottom:14px;font-size:13px}
  th{text-align:left;color:#5d5d6b;font-weight:600;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.07)}
  td{padding:10px 12px;color:#b8b8c8;border-bottom:1px solid rgba(255,255,255,.04)}
  a{color:#c79bff;text-decoration:none}
  a:hover{text-decoration:underline}
  .divider{height:1px;background:rgba(255,255,255,.07);margin:48px 0 0}
  .footer{margin-top:24px;font-size:12px;color:#3d3d4a;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <a href="/login" class="back">&#8592; Back to Highlightz</a>
  <div class="logo">
    <img src="/static/logo-mark.png" alt="Highlightz">
    <span>Highlightz</span>
  </div>
  <h1>Cookie Policy</h1>
  <p class="meta">Effective date: June 20, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>This Cookie Policy explains how Highlightz uses cookies and similar technologies. By using the Service you consent to the use of cookies as described here.</p>

  <h2>What is a Cookie?</h2>
  <p>A cookie is a small text file placed on your device by a website. Cookies help the site remember information about your visit so you don't have to re-enter it each time.</p>

  <h2>Cookies We Use</h2>
  <p>Highlightz uses a minimal number of cookies — only what is strictly necessary to operate the Service:</p>
  <table>
    <tr><th>Name</th><th>Purpose</th><th>Duration</th><th>Type</th></tr>
    <tr><td><code>session</code></td><td>Keeps you signed in between page loads. Contains an encrypted session identifier — no personal data is stored in the cookie itself.</td><td>7 days</td><td>Strictly necessary</td></tr>
  </table>
  <p>We do not use advertising cookies, tracking pixels, or third-party analytics cookies. We do not use Google Analytics or any equivalent service.</p>

  <h2>Third-Party Cookies</h2>
  <p>When you sign in via Twitch, Twitch may set cookies on their own domain as part of the OAuth2 flow. These are governed by <a href="https://www.twitch.tv/p/legal/privacy-notice/" target="_blank" rel="noopener">Twitch's Privacy Notice</a>. When you complete a payment via Stripe, Stripe may set cookies on their domain. These are governed by <a href="https://stripe.com/privacy" target="_blank" rel="noopener">Stripe's Privacy Policy</a>. Kick stream and chat data is fetched server-side via Kick's public WebSocket and API — no cookies are set on your device as a result of Kick monitoring. We have no control over or access to any third-party cookies set on their respective domains.</p>

  <h2>Managing Cookies</h2>
  <p>You can control cookies through your browser settings. Blocking or deleting the session cookie will sign you out of the Service and require you to sign in again on your next visit. Most browsers allow you to:</p>
  <p>View and delete cookies · Block cookies from specific sites · Block all cookies · Delete all cookies when you close the browser</p>
  <p>Refer to your browser's help documentation for instructions. Note that disabling strictly necessary cookies will prevent the Service from functioning.</p>

  <h2>Changes to This Policy</h2>
  <p>We may update this Cookie Policy from time to time. Material changes will be posted at this URL with an updated effective date.</p>

  <h2>Contact</h2>
  <p>Questions? Contact us at <a href="mailto:support@highlightz.app">support@highlightz.app</a></p>

  <div class="divider"></div>
  <div class="footer">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved. &middot; <a href="/tos" style="color:#5d5d6b">Terms of Service</a> &middot; <a href="/privacy" style="color:#5d5d6b">Privacy Policy</a></div>
</div>
</body>
</html>"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Admin — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<link rel="preload" href="/static/fonts/sora-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-600.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/plexmono-400.woff2" as="font" type="font/woff2" crossorigin>
<style>
  /* The admin panel is an INSTRUMENT, not marketing — same room, same light,
     but no display face and no glow. Every number is mono because every number
     here is a measurement you compare against another one. */
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}
  :root{
    --void:#0E0B11; --wall:#1B1221; --bruise:#33203F;
    --glow:#B86ADC; --glow-ink:#C489E4; --flare:#D26AFB; --ember:#F7A745;
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --good:#4ADE80; --bad:#FF7A8A;
    --hair:rgba(242,234,247,.085);
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora',system-ui,sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--void);color:var(--ink);font-family:var(--sans);font-weight:400;
    font-size:14px;line-height:1.6;min-height:100vh;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
  .grain{position:fixed;inset:0;z-index:9;pointer-events:none;opacity:.03;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)'/%3E%3C/svg%3E");
    background-size:180px 180px}
  a{text-decoration:none;color:inherit}
  :focus-visible{outline:2px solid var(--flare);outline-offset:2px;border-radius:3px}
  ::selection{background:rgba(210,106,251,.3)}

  /* ── Top bar ── */
  .topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;
    padding:12px 26px;background:var(--void);border-bottom:1px solid transparent;
    background-image:linear-gradient(var(--void),var(--void)),
      linear-gradient(270deg,rgba(184,106,220,.34),rgba(242,234,247,.06) 55%,rgba(242,234,247,.02));
    background-origin:padding-box,border-box;background-clip:padding-box,border-box}
  .logo{display:flex;align-items:center;gap:10px}
  .logo img{height:22px}
  .logo span{font-family:var(--mono);font-weight:600;font-size:13px;letter-spacing:.12em;text-transform:uppercase}
  .badge{font-family:var(--mono);font-weight:600;font-size:9.5px;letter-spacing:.18em;
    text-transform:uppercase;color:var(--flare);border:1px solid rgba(210,106,251,.35);
    padding:3px 8px;border-radius:2px}
  .topbar-right{margin-left:auto;display:flex;align-items:center;gap:6px}
  .tlink{font-family:var(--mono);font-size:11.5px;letter-spacing:.04em;color:var(--ink-3);
    padding:7px 11px;border-radius:3px;transition:color .15s,background .15s;white-space:nowrap}
  .tlink:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .tlink .n{color:var(--ember)}

  .wrap{max-width:1240px;margin:0 auto;padding:30px 26px 70px}
  h1{font-size:25px;font-weight:700;letter-spacing:-.025em}
  .meta{font-size:13.5px;color:var(--ink-2);margin-top:4px}

  /* ── Overview rail. Not six floating tiles: one ruled strip, uneven weight,
     each figure carrying the second number that makes it mean something. ── */
  .rail{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin:26px 0 8px;
    border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
  .cell{padding:18px 0 18px 20px;border-left:1px solid var(--hair);min-width:0}
  .cell:first-child{padding-left:0;border-left:none}
  .cell .v{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
    font-size:27px;letter-spacing:-.03em;line-height:1.1;color:var(--ink)}
  .cell.hot .v{color:var(--ember)}
  .cell .k{font-family:var(--mono);font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
    color:var(--ink-3);margin-top:9px}
  .cell .s{font-size:12px;color:var(--ink-2);margin-top:5px;line-height:1.4;
    min-height:2.8em;padding-right:14px}

  /* ── Tabs ── */
  .tabs{display:flex;gap:0;margin:30px 0 0;border-bottom:1px solid var(--hair);flex-wrap:wrap}
  .tab{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--ink-3);background:none;border:none;border-bottom:2px solid transparent;
    padding:12px 16px;cursor:pointer;transition:color .16s,border-color .16s;margin-bottom:-1px}
  .tab:hover{color:var(--ink-2)}
  .tab.on{color:var(--ink);border-bottom-color:var(--flare)}
  .tab .c{color:var(--ink-3);margin-left:7px}
  .tab.on .c{color:var(--ember)}
  .panel{display:none;padding-top:24px}
  .panel.on{display:block}
  .lede{font-size:13.5px;color:var(--ink-2);max-width:74ch;margin-bottom:18px;line-height:1.6}
  .lede b{color:var(--ink);font-weight:600}
  .block{margin-bottom:44px}
  .block-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
  .block-head h2{font-size:17px;font-weight:700;letter-spacing:-.02em}
  .block-head .c{font-family:var(--mono);font-size:11px;letter-spacing:.1em;color:var(--ink-3)}

  /* ── Toolbar ── */
  .toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:16px}
  .field{background:var(--wall);border:1px solid var(--hair);color:var(--ink);border-radius:3px;
    padding:8px 11px;font-size:13px;font-family:var(--sans);flex:1 1 260px;max-width:340px;min-width:0}
  .field::placeholder{color:var(--ink-3)}
  .field:focus{outline:none;border-color:rgba(184,106,220,.5)}
  .chips{display:flex;gap:0;border:1px solid var(--hair);border-radius:3px;overflow:hidden}
  .chip{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--ink-3);background:none;border:none;border-right:1px solid var(--hair);
    padding:8px 12px;cursor:pointer;transition:.15s}
  .chip:last-child{border-right:none}
  .chip:hover{color:var(--ink-2);background:rgba(242,234,247,.04)}
  .chip.on{color:var(--void);background:var(--ember);font-weight:600}
  .spacer{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--ink-3);letter-spacing:.06em}

  /* ── Tables ── */
  .tw{overflow-x:auto}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-family:var(--mono);font-size:9.5px;font-weight:600;color:var(--ink-3);
    text-transform:uppercase;letter-spacing:.16em;padding:0 14px 10px 0;border-bottom:1px solid var(--hair);
    white-space:nowrap}
  th:last-child{padding-right:0}
  td{padding:13px 14px 13px 0;font-size:13.5px;border-bottom:1px solid var(--hair);vertical-align:middle}
  td:last-child{padding-right:0}
  tbody tr:hover{background:linear-gradient(90deg,rgba(184,106,220,.05),transparent 70%)}
  tr.u-row{cursor:pointer}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600}
  .dim{color:var(--ink-3)}
  .who{display:flex;align-items:center;gap:11px;min-width:0}
  .avatar{width:30px;height:30px;border-radius:3px;object-fit:cover;flex-shrink:0;
    background:linear-gradient(150deg,#3A2348,#231733);border:1px solid var(--hair)}
  .username{font-weight:600;letter-spacing:-.01em}
  .sub{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:3px;letter-spacing:.03em}

  /* ── Plan + status marks. Text, not filled pills: a table of eight coloured
     lozenges is unreadable, and the plan is the thing you scan for. ── */
  .plan{font-family:var(--mono);font-weight:600;font-size:11px;letter-spacing:.14em;text-transform:uppercase}
  .plan-pro{color:var(--flare)}
  .plan-starter{color:var(--glow-ink)}
  .plan-free{color:var(--ink-3)}
  .plan-note{font-family:var(--mono);font-size:10px;color:var(--ink-3);margin-top:3px;letter-spacing:.06em}
  .plan-note.pay{color:var(--good)}
  .plan-note.trial{color:var(--ember)}
  .plan-note.lapsed{color:var(--bad)}
  .tagm{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--ember);border:1px solid rgba(247,167,69,.3);padding:1px 6px;border-radius:2px;margin-left:7px}
  .tagm.adm{color:var(--flare);border-color:rgba(210,106,251,.35)}

  /* ── Buttons ── */
  .btn{font-family:var(--sans);font-size:12px;font-weight:600;padding:6px 12px;border-radius:3px;
    cursor:pointer;border:1px solid var(--hair);background:var(--wall);color:var(--ink-2);
    transition:.15s;white-space:nowrap}
  .btn:hover{color:var(--ink);border-color:rgba(184,106,220,.45)}
  .btn-key{border-color:rgba(210,106,251,.5);color:var(--ink);
    background:linear-gradient(166deg,var(--bruise),#25172E)}
  .btn-key:hover{background:linear-gradient(166deg,#412852,#2C1B36);border-color:#EFA6FF}
  .btn-good{color:var(--good);border-color:rgba(74,222,128,.3)}
  .btn-good:hover{color:var(--good);border-color:rgba(74,222,128,.6)}
  .btn-bad{color:var(--bad);border-color:rgba(255,122,138,.28)}
  .btn-bad:hover{color:var(--bad);border-color:rgba(255,122,138,.6)}
  select.btn{font-family:var(--mono);font-size:11px;letter-spacing:.06em}
  select.btn option{background:#16121C;color:var(--ink);font-family:var(--sans)}
  .acts{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}
  .empty,.loading{padding:34px 0;text-align:center;color:var(--ink-3);font-size:13px}
  .err{color:var(--bad);font-size:13px;padding:20px 0}

  /* ── Sortable header ── */
  th.sortable{cursor:pointer;user-select:none}
  th.sortable:hover{color:var(--ink-2)}
  th.sortable.on{color:var(--ember)}
  tr.expandable{cursor:pointer}
  .drill{background:rgba(242,234,247,.02)}
  .drill td{padding:16px 0}
  .bar{flex:1;height:3px;background:rgba(242,234,247,.08);overflow:hidden}
  .bar i{display:block;height:100%;background:var(--glow)}

  /* ── Toast ── */
  .toast{position:fixed;bottom:22px;right:22px;background:var(--wall);border:1px solid var(--hair);
    border-radius:3px;padding:12px 18px;font-size:13px;font-weight:600;opacity:0;transform:translateY(6px);
    transition:.22s;pointer-events:none;z-index:999}
  .toast.show{opacity:1;transform:none}
  .toast.ok{border-color:rgba(74,222,128,.45);color:var(--good)}
  .toast.err{border-color:rgba(255,122,138,.45);color:var(--bad)}

  /* ── User drawer. A drawer, not a centred modal: it holds the identity, the
     billing facts and the rare/destructive actions, so it is a place you read
     top to bottom rather than a dialog you dismiss. ── */
  .scrim{position:fixed;inset:0;background:rgba(6,4,9,.72);z-index:100;opacity:0;
    pointer-events:none;transition:.2s}
  .scrim.open{opacity:1;pointer-events:auto}
  .drawer{position:fixed;top:0;right:0;bottom:0;width:min(560px,100%);z-index:101;
    background:var(--void);border-left:1px solid transparent;
    background-image:linear-gradient(var(--void),var(--void)),
      linear-gradient(200deg,rgba(210,106,251,.45),rgba(242,234,247,.06) 60%,rgba(242,234,247,.02));
    background-origin:padding-box,border-box;background-clip:padding-box,border-box;
    display:flex;flex-direction:column;transform:translateX(100%);visibility:hidden;
    transition:transform .26s cubic-bezier(.4,0,.2,1),visibility .26s}
  .drawer.open{transform:none;visibility:visible}
  .drawer-head{display:flex;align-items:flex-start;gap:12px;padding:20px 24px;border-bottom:1px solid var(--hair)}
  .drawer-head h3{font-size:17px;font-weight:700;letter-spacing:-.02em}
  .drawer-head .s{font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:4px;letter-spacing:.05em}
  .x{margin-left:auto;width:28px;height:28px;border-radius:3px;background:var(--wall);
    border:1px solid var(--hair);color:var(--ink-2);font-size:14px;cursor:pointer;flex-shrink:0}
  .x:hover{color:var(--ink);border-color:rgba(210,106,251,.5)}
  .drawer-body{overflow-y:auto;padding:22px 24px 40px;display:flex;flex-direction:column;gap:26px}
  .dh{font-family:var(--mono);font-size:9.5px;font-weight:600;letter-spacing:.18em;text-transform:uppercase;
    color:var(--ink-3);padding-bottom:9px;border-bottom:1px solid var(--hair);margin-bottom:13px}
  .kv{display:grid;grid-template-columns:132px minmax(0,1fr);gap:7px 14px;font-size:13px}
  .kv dt{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);padding-top:2px}
  .kv dd{color:var(--ink-2);word-break:break-word}
  .kv dd b{color:var(--ink);font-weight:600}
  .srow{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px solid var(--hair);font-size:13px}
  .srow:last-child{border-bottom:none}
  .dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
  .dot-live{background:var(--good);box-shadow:0 0 7px rgba(74,222,128,.7)}
  .dot-offline{background:var(--ink-3)}
  .dot-starting{background:var(--ember);box-shadow:0 0 7px rgba(247,167,69,.6)}
  /* Approved clips are the ones that mattered, so they lead the list and are
     marked. Colour alone would not be enough — the status word carries the same
     information for anyone who cannot see the difference between the rules. */
  .crow{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto;gap:12px;align-items:center;
    padding:9px 0 9px 11px;border-bottom:1px solid var(--hair);border-left:2px solid var(--hair);
    font-size:13px}
  .crow:last-child{border-bottom:none}
  .crow.ok{border-left-color:var(--good);
    background:linear-gradient(90deg,rgba(74,222,128,.06),transparent 45%)}
  .crow .st{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ink-3)}
  .crow.ok .st{color:var(--good)}
  .crow.ok b{color:var(--ink)}
  .ct{display:block;font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:3px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:.03em}
  .danger{border:1px solid rgba(255,122,138,.22);border-radius:3px;padding:16px 18px}
  .danger .dh{border-bottom-color:rgba(255,122,138,.22);color:var(--bad)}
  .danger p{font-size:12.5px;color:var(--ink-3);margin-bottom:13px;line-height:1.55}

  @media(max-width:980px){
    .rail{grid-template-columns:repeat(3,minmax(0,1fr))}
    .cell:nth-child(4){padding-left:0;border-left:none}
    .cell:nth-child(-n+3){border-bottom:1px solid var(--hair)}
  }
  @media(max-width:660px){
    .wrap{padding:22px 16px 60px}
    .topbar{padding:10px 16px;gap:8px}
    .logo span{display:none}
    .rail{grid-template-columns:repeat(2,minmax(0,1fr))}
    .cell{padding-left:16px}
    .cell:nth-child(odd){padding-left:0;border-left:none}
    .cell:nth-child(-n+4){border-bottom:1px solid var(--hair)}
    .field{max-width:none;width:100%}
    .acts{justify-content:flex-start}
    .topbar{flex-wrap:wrap;row-gap:4px}
    .topbar-right{margin-left:0;width:100%;justify-content:flex-start;gap:0}
    .tlink{padding:6px 9px;font-size:11px}
    .tab{padding:10px 11px;font-size:10.5px}
    /* User | Membership | Actions only. Streams, clips and joined-date are all
       in the drawer, and keeping them here just pushed the actions off-screen. */
    #u-wrap th:nth-child(n+3),#u-wrap td:nth-child(n+3){display:none}
    /* Seven filters do not fit 390px, and a clipped row of them reads as
       broken rather than as scrollable. */
    .chips{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
    .chip{flex:0 0 auto}
    .kv{grid-template-columns:minmax(0,1fr);gap:2px 0}
    .kv dd{margin-bottom:9px}
  }
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">
    <img src="/static/logo-mark.png" alt="Highlightz">
    <span>Highlightz</span>
  </div>
  <span class="badge">Admin</span>
  <div class="topbar-right">
    <a href="/admin/optout" class="tlink">Opt-out registry</a>
    <a href="/admin/feedback-page" id="feedback-link" class="tlink">Feedback</a>
    <a href="/" class="tlink">&#8592; Dashboard</a>
  </div>
</div>

<div class="wrap">
  <h1>Admin</h1>
  <p class="meta">Platform overview, memberships and user management.</p>

  <!-- Overview. Every figure comes from /admin/overview, computed on the
       server from the real ledgers — see that endpoint for why the browser is
       no longer allowed to add these up itself. -->
  <div class="rail" id="rail">
    <div class="cell"><div class="v" id="ov-users">&mdash;</div><div class="k">Users</div><div class="s" id="ov-users-s"></div></div>
    <div class="cell"><div class="v" id="ov-paying">&mdash;</div><div class="k">Paying</div><div class="s" id="ov-paying-s"></div></div>
    <div class="cell hot"><div class="v" id="ov-mrr">&mdash;</div><div class="k">MRR</div><div class="s" id="ov-mrr-s"></div></div>
    <div class="cell hot"><div class="v" id="ov-clips">&mdash;</div><div class="k">Clips caught</div><div class="s" id="ov-clips-s"></div></div>
    <div class="cell"><div class="v" id="ov-keep">&mdash;</div><div class="k">Keep rate</div><div class="s" id="ov-keep-s"></div></div>
    <div class="cell"><div class="v" id="ov-live">&mdash;</div><div class="k">Live now</div><div class="s" id="ov-live-s"></div></div>
  </div>

  <div class="tabs" id="tabs">
    <button class="tab on" data-tab="users">Users<span class="c" id="tc-users"></span></button>
    <button class="tab" data-tab="growth">Growth<span class="c" id="tc-growth"></span></button>
    <button class="tab" data-tab="clips">Clip record<span class="c" id="tc-clips"></span></button>
    <button class="tab" data-tab="reviews">Reviews<span class="c" id="tc-reviews"></span></button>
  </div>

  <!-- ── USERS ── -->
  <div class="panel on" id="panel-users">
    <div class="toolbar">
      <input class="field" id="u-search" placeholder="Search name, Twitch login or email">
      <div class="chips" id="u-chips">
        <button class="chip on" data-f="active">Active</button>
        <button class="chip" data-f="pro">Pro</button>
        <button class="chip" data-f="starter">Starter</button>
        <button class="chip" data-f="free">Free</button>
        <button class="chip" data-f="trialing">Trial</button>
        <button class="chip" data-f="lapsed">Lapsed</button>
        <button class="chip" data-f="all">All</button>
      </div>
      <span class="spacer" id="u-count"></span>
    </div>
    <div class="tw"><div id="u-wrap" class="loading">Loading&hellip;</div></div>
  </div>

  <!-- ── GROWTH: referrals and promo codes answer the same question, so they
       stopped being two sections on opposite ends of a long scroll. ── -->
  <div class="panel" id="panel-growth">
    <div class="block">
      <div class="block-head"><h2>Invite links</h2><span class="c" id="iv-c"></span></div>
      <p class="lede">
        Hand someone a membership without ever showing them a price. They click
        the link, sign in with Twitch, and the plan is already on their account
        when the dashboard loads &mdash; no payment page, nothing to cancel.
        <b>Single use and 30-day expiry by default</b>, because a link that
        grants Pro to everyone who sees it is one screenshot from being public.
      </p>
      <div class="toolbar">
        <select class="btn" id="iv-plan"></select>
        <select class="btn" id="iv-days"></select>
        <input class="field" id="iv-note" placeholder="Who is it for? (optional)" maxlength="80">
        <button class="btn btn-key" id="iv-make">Create link</button>
      </div>
      <div class="tw"><div id="iv-wrap" class="loading">Loading&hellip;</div></div>
    </div>
    <div class="block">
      <div class="block-head"><h2>Referrals</h2><span class="c" id="rf-c"></span></div>
      <p class="lede">
        Signups per person, from <code>?ref=</code> links and typed codes alike.
        <b>Still active wk2</b> is the column that matters &mdash; signups say who
        is good at getting attention, retention says whose lane brought people who
        actually needed this. Users who signed up less than 7 days ago are
        excluded from that column entirely rather than counted as churned.
      </p>
      <div class="tw"><div id="rf-wrap" class="loading">Loading&hellip;</div></div>
    </div>
    <div class="block">
      <div class="block-head"><h2>Promo codes</h2><span class="c" id="pr-c"></span></div>
      <p class="lede">Signups attributed to each promo code (recorded from Stripe at checkout). Payouts are manual &mdash; Stripe's redemption count stays the source of truth.</p>
      <div class="tw"><div id="pr-wrap" class="loading">Loading&hellip;</div></div>
    </div>
  </div>

  <!-- ── CLIP RECORD ── -->
  <div class="panel" id="panel-clips">
    <div class="block-head"><h2>Clip record</h2><span class="c" id="cr-c"></span></div>
    <p class="lede">
      What Highlightz caught per channel and how much of it was kept &mdash; the
      numbers to show a streamer. Counted from a dedicated ledger, so rejected
      and aged-out clips still count as caught. Click a column to sort; click a
      row to see it broken down per stream.
    </p>
    <div class="toolbar"><input class="field" id="cr-filter" placeholder="Filter by channel or user"></div>
    <div class="tw"><div id="cr-wrap" class="loading">Loading&hellip;</div></div>
  </div>

  <!-- ── REVIEWS ── -->
  <div class="panel" id="panel-reviews">
    <div class="block-head"><h2>Reviews</h2><span class="c" id="rv-c"></span></div>
    <p class="lede">
      Star ratings from users, asked after 25 approved clips. A review is only
      publishable if the user ticked the consent box AND you approve it here.
      The average shown is over APPROVED reviews only &mdash; that is the number that
      would ever appear as a rating on the site, so it has to match what a
      visitor can actually read.
    </p>
    <div class="tw"><div id="rv-wrap" class="loading">Loading&hellip;</div></div>
  </div>
</div>

<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-labelledby="dr-name">
  <div class="drawer-head">
    <div>
      <h3 id="dr-name">User</h3>
      <div class="s" id="dr-sub"></div>
    </div>
    <button class="x" id="dr-close" aria-label="Close">&#x2715;</button>
  </div>
  <div class="drawer-body" id="dr-body"><div class="loading">Loading&hellip;</div></div>
</aside>

<div class="toast" id="toast"></div>
<script>
/* ═══════════════════════════════════════════════════════════════════════════
   ONE SCRIPT BLOCK, ZERO BACKSLASHES, ZERO INLINE HANDLERS.

   This whole page is a Python triple-quoted string, so Python resolves escapes
   before the browser ever sees them. An inline handler written as
   approve(<escaped-quote>ID<escaped-quote>) once arrived as approve(''), a
   SyntaxError that killed the entire script and left every section stuck on
   "Loading..." with nothing in the test suite noticing. The structural fix is
   to have nothing for Python to eat: every handler is a delegated listener
   reading data- attributes, and there is no backslash in here at all. Tests
   assert both, and the assertions are substring checks — so this comment
   cannot name the attribute it is describing.
   ═══════════════════════════════════════════════════════════════════════════ */

// ── helpers ─────────────────────────────────────────────────────────────────
function rvEsc(t){ const d=document.createElement('div'); d.textContent = t==null ? '' : String(t); return d.innerHTML; }
const esc = rvEsc;
const n0 = v => (Number(v)||0).toLocaleString('en-US');
const fmt = ts => ts ? new Date(ts*1000).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : '—';
const fmtTs = ts => ts ? new Date(ts*1000).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '';
const RV_STAR = String.fromCharCode(9733);

function toast(msg, ok){
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + (ok === false ? 'err' : 'ok');
  setTimeout(() => { el.className = 'toast'; }, 2800);
}
async function api(url, method){
  const r = await fetch(url, {method: method || 'GET', credentials:'same-origin'});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
function fail(id, what){
  const el = document.getElementById(id);
  if(el){ el.className = 'err'; el.textContent = 'Could not load ' + what + '.'; }
}

// ── tabs ────────────────────────────────────────────────────────────────────
document.getElementById('tabs').addEventListener('click', e => {
  const b = e.target.closest('.tab'); if(!b) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t === b));
  document.querySelectorAll('.panel').forEach(p =>
    p.classList.toggle('on', p.id === 'panel-' + b.dataset.tab));
});

// ── overview ────────────────────────────────────────────────────────────────
// Read straight from the server. The header used to be computed in the browser
// by summing the user list, and "Total Clips" counted only clips still sitting
// in storage — every rejected, aged-out and dropped clip was invisible, and
// admin-owned clips were filtered out on top of that.
async function loadOverview(){
  let d;
  try { d = await api('/admin/overview'); } catch(e){ return; }
  const set = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
  const u = d.users || {}, c = d.clips || {}, s = d.streams || {}, bp = u.by_plan || {};
  set('ov-users', n0(u.total));
  set('ov-users-s', n0(u.new_7d) + ' joined this week · ' + n0(u.admins) + ' staff');
  set('ov-paying', n0(u.paying));
  set('ov-paying-s', n0(bp.pro) + ' Pro · ' + n0(bp.starter) + ' Starter · '
    + n0(u.trialing) + ' on trial' + (u.comped ? ' · ' + n0(u.comped) + ' comped' : ''));
  // A legacy subscriber's price is not in our records, so MRR says so rather
  // than pricing them at the tier they were grandfathered into.
  set('ov-mrr', '$' + n0(d.mrr) + (d.mrr_unknown ? '+' : ''));
  set('ov-mrr-s', d.mrr_unknown
    ? 'per month · ' + n0(d.mrr_unknown) + ' legacy subscriber' + (d.mrr_unknown === 1 ? '' : 's') + ' not priced here'
    : 'per month, active subscriptions only');
  set('ov-clips', n0(c.lifetime));
  // The gap between lifetime and stored is the whole point of showing both.
  set('ov-clips-s', n0(c.stored) + ' in storage now · ' + n0(c.pending) + ' awaiting review');
  set('ov-keep', c.keep_rate + '%');
  set('ov-keep-s', n0(c.kept) + ' kept of ' + n0(c.kept + c.rejected) + ' reviewed · ' + n0(c.expired) + ' aged out');
  set('ov-live', n0(s.live));
  set('ov-live-s', n0(s.registered) + ' streams registered');
}

// ── users ───────────────────────────────────────────────────────────────────
let USERS = [], ME = '', U_FILTER = 'active', U_Q = '';

// The comp-able tiers. Kept next to the table and the drawer so the two can
// never offer different memberships; the server validates against PAID_PLANS
// regardless, so an edit here cannot invent a plan.
const PLAN_OPTIONS = [['starter', 'Starter'], ['pro', 'Pro']];
const DURATIONS = [['0','Forever'],['3','3 days'],['7','1 week'],
                   ['14','2 weeks'],['30','1 month'],['90','3 months']];

function planClass(p){ return p === 'pro' ? 'plan-pro' : p === 'starter' ? 'plan-starter' : 'plan-free'; }

// One line under the plan saying WHY they are on it. Without this a row reading
// "Pro" is ambiguous between a paying customer, a comped admin, a granted trial
// and a legacy grandfathered subscriber — four situations you act on
// differently.
function planNote(u){
  if(u.is_admin) return ['Staff — comped', ''];
  if(u.is_labeler) return ['Trainer — comped', ''];
  const st = u.subscription_status;
  if(st === 'trialing') return ['Trial' + (u.trial_ends_at ? ' ends ' + fmt(u.trial_ends_at) : ''), 'trial'];
  // A comped tier and a paid one look identical without this — and they are
  // the two you most need to tell apart when reading the table.
  if(st === 'active') return u.plan_source === 'granted'
    ? ['Granted — comped', '']
    : [u.plan_price ? '$' + u.plan_price + '/mo' : 'Granted', u.plan_price ? 'pay' : ''];
  if(st === 'inactive' || st === 'canceled') return ['Lapsed — on free', 'lapsed'];
  return ['Never subscribed', ''];
}

function userState(u){
  if(u.is_admin) return 'admin';
  const st = u.subscription_status;
  if(st === 'trialing') return 'trialing';
  if(st === 'active') return 'active';
  if(st === 'inactive' || st === 'canceled') return 'lapsed';
  return 'none';
}

function userMatches(u){
  if(U_Q){
    const hay = ((u.username||'') + ' ' + (u.twitch_login||'') + ' ' + (u.email||'') + ' ' + (u.promo_code||'')).toLowerCase();
    if(hay.indexOf(U_Q) < 0) return false;
  }
  const st = userState(u);
  if(U_FILTER === 'all') return true;
  // "Active" means someone currently getting the paid product, staff included —
  // that is the working set, and it is the default because a list dominated by
  // signed-up-once accounts buries the people who are actually using this.
  if(U_FILTER === 'active') return st === 'admin' || st === 'active' || st === 'trialing';
  if(U_FILTER === 'trialing') return st === 'trialing';
  if(U_FILTER === 'lapsed') return st === 'lapsed';
  return u.plan === U_FILTER;
}

function renderUsers(){
  const wrap = document.getElementById('u-wrap');
  if(!USERS.length){ wrap.className='empty'; wrap.textContent='No users yet.'; return; }
  const rows = USERS.map((u,i) => [u,i]).filter(p => userMatches(p[0]));
  document.getElementById('u-count').textContent = rows.length + ' of ' + USERS.length;
  document.getElementById('tc-users').textContent = USERS.length;
  if(!rows.length){ wrap.className='empty'; wrap.textContent='No users match that filter.'; return; }
  wrap.className = '';

  let html = '<table><thead><tr><th>User</th><th>Membership</th><th>Streams</th>'
    + '<th>Clips</th><th>Joined</th><th style="text-align:right">Actions</th></tr></thead><tbody>';
  rows.forEach(pair => {
    const u = pair[0], i = pair[1];
    const st = userState(u);
    const note = planNote(u);
    const avatar = u.avatar_url
      ? '<img class="avatar" src="' + esc(u.avatar_url) + '" alt="">'
      : '<span class="avatar"></span>';
    const marks = (u.is_admin ? '<span class="tagm adm">Admin</span>' : '')
                + (u.is_labeler ? '<span class="tagm">Trainer</span>' : '');
    // Only the three actions you take from a LIST live here. Promote, sync,
    // delete are one-at-a-time decisions you make after looking at someone, so
    // they moved into the drawer where you can see who they are first.
    const canGrant  = !u.is_admin && st !== 'active' && st !== 'trialing';
    const canRevoke = !u.is_admin && (st === 'active' || st === 'trialing');
    const canTrial  = !u.is_admin && st !== 'active';
    let acts = '<button class="btn u-open" data-i="' + i + '">Details</button>';
    // A plan picker, not a Grant button. It used to comp everyone Pro because
    // the endpoint took no tier — so "grant access" and "grant Pro" were the
    // same action and there was no way to hand someone Starter.
    if(canGrant)  acts += '<select class="btn btn-good u-grant" data-i="' + i + '">'
      + '<option value="">Grant…</option>'
      + PLAN_OPTIONS.map(p => '<option value="' + p[0] + '">' + p[1] + '</option>').join('')
      + '</select>';
    if(canTrial)  acts += '<select class="btn u-trial" data-i="' + i + '">'
      + '<option value="">' + (st === 'trialing' ? 'Extend…' : 'Trial…') + '</option>'
      + '<option value="3">3 days</option><option value="7">1 week</option>'
      + '<option value="14">2 weeks</option><option value="30">1 month</option>'
      + '<option value="90">3 months</option></select>';
    if(canRevoke) acts += '<button class="btn btn-bad u-revoke" data-i="' + i + '">Revoke</button>';

    html += '<tr class="u-row" data-i="' + i + '">'
      + '<td><div class="who">' + avatar + '<div style="min-width:0"><div class="username">'
        + esc(u.username) + marks + '</div><div class="sub">'
        + (u.twitch_login ? '@' + esc(u.twitch_login) : 'password auth')
        + (u.email ? ' · ' + esc(u.email) : '') + '</div></div></div></td>'
      + '<td><span class="plan ' + planClass(u.plan) + '">' + esc(u.plan_label || u.plan) + '</span>'
        + '<div class="plan-note ' + note[1] + '">' + esc(note[0]) + '</div></td>'
      + '<td class="num">' + (u.stream_count||0) + '</td>'
      + '<td class="num">' + (u.clip_count||0) + '</td>'
      + '<td class="dim">' + fmt(u.created_at) + '</td>'
      + '<td><div class="acts">' + acts + '</div></td></tr>';
  });
  wrap.innerHTML = html + '</tbody></table>';
}

async function loadUsers(){
  try { USERS = await api('/admin/users'); }
  catch(e){ fail('u-wrap', 'users'); return; }
  renderUsers();
}

document.getElementById('u-search').addEventListener('input', e => {
  U_Q = (e.target.value || '').toLowerCase().trim(); renderUsers();
});
document.getElementById('u-chips').addEventListener('click', e => {
  const c = e.target.closest('.chip'); if(!c) return;
  U_FILTER = c.dataset.f;
  document.querySelectorAll('#u-chips .chip').forEach(x => x.classList.toggle('on', x === c));
  renderUsers();
});

document.getElementById('u-wrap').addEventListener('click', async e => {
  const t = e.target.closest('.u-open, .u-revoke, .u-row');
  if(!t) return;
  const u = USERS[Number(t.dataset.i)]; if(!u) return;
  // The row itself opens the drawer, but only when the click did not land on a
  // control inside it — otherwise Grant would also slide the drawer open.
  if(t.classList.contains('u-row')){
    if(e.target.closest('button, select, a')) return;
    return openUser(u);
  }
  if(t.classList.contains('u-open')) return openUser(u);
  if(t.classList.contains('u-revoke')){
    if(!confirm('Revoke access for ' + u.username + '?')) return;
    try { await api('/admin/users/' + u.id + '/revoke', 'POST'); toast('Access revoked'); refresh(); }
    catch(err){ toast('Error: ' + err.message, false); }
  }
});

// One helper for every grant, from either the table or the drawer, so the two
// cannot drift apart. days=0 means a permanent comp.
async function grantMembership(u, plan, days){
  const label = (PLAN_OPTIONS.find(p => p[0] === plan) || [plan, plan])[1];
  const how = days ? days + ' day' + (days === 1 ? '' : 's') + ' of ' + label
                   : label + ' (no end date)';
  if(!confirm('Give ' + u.username + ' ' + how + '?')) return;
  try {
    if(days){
      const r = await fetch('/admin/users/' + u.id + '/grant-trial', {
        method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({days, plan}) });
      if(!r.ok) throw new Error(await r.text());
    } else {
      await api('/admin/users/' + u.id + '/grant?plan=' + encodeURIComponent(plan), 'POST');
    }
    toast('Granted ' + how); refresh();
  } catch(err){ toast('Error: ' + err.message, false); }
}

document.getElementById('u-wrap').addEventListener('change', async e => {
  const sel = e.target.closest('.u-trial, .u-grant'); if(!sel) return;
  const u = USERS[Number(sel.dataset.i)]; if(!u) return;
  const val = sel.value;
  sel.value = '';                        // reset so the same option can be re-picked
  if(!val) return;
  if(sel.classList.contains('u-grant')) return grantMembership(u, val, 0);
  // The quick trial in the table stays Pro — it is the showcase default, and
  // picking a tier as well belongs in the drawer where there is room for it.
  return grantMembership(u, 'pro', parseInt(val, 10));
});

// ── user drawer ─────────────────────────────────────────────────────────────
let DR_USER = null, DR_STATE = '';

function closeDrawer(){
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('scrim').classList.remove('open');
  DR_USER = null;
}
document.getElementById('dr-close').addEventListener('click', closeDrawer);
document.getElementById('scrim').addEventListener('click', closeDrawer);
document.addEventListener('keydown', e => { if(e.key === 'Escape') closeDrawer(); });

function dotClass(s){
  if(s === 'live') return 'dot dot-live';
  if(s === 'starting' || s === 'reconnecting') return 'dot dot-starting';
  return 'dot dot-offline';
}

async function openUser(u){
  DR_USER = u;
  document.getElementById('dr-name').textContent = u.username;
  document.getElementById('dr-sub').textContent =
    (u.twitch_login ? '@' + u.twitch_login + ' · ' : '') + (u.plan_label || u.plan);
  document.getElementById('dr-body').innerHTML = '<div class="loading">Loading…</div>';
  document.getElementById('drawer').classList.add('open');
  document.getElementById('scrim').classList.add('open');

  const note = planNote(u);
  const st = userState(u);
  DR_STATE = st;
  // The same three membership actions the table offers. Duplicated on purpose:
  // the table row is a desktop convenience and disappears on a phone, so the
  // drawer has to be able to do everything on its own.
  // Pick a tier AND a duration, then grant. Two coupled choices in one control
  // rather than a Grant button that silently meant Pro and a separate trial
  // dropdown that also silently meant Pro — between them there was no way to
  // comp anyone Starter at all.
  let mem = '';
  if(!u.is_admin){
    mem += '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
      + '<select class="btn dr-plan">'
      + PLAN_OPTIONS.map(p => '<option value="' + p[0] + '"'
          + (p[0] === u.plan ? ' selected' : '') + '>' + p[1] + '</option>').join('')
      + '</select>'
      + '<select class="btn dr-days">'
      + DURATIONS.map(d => '<option value="' + d[0] + '">' + d[1] + '</option>').join('')
      + '</select>'
      + '<button class="btn btn-good dr-grant">'
      + (st === 'active' || st === 'trialing' ? 'Change membership' : 'Grant membership')
      + '</button>'
      + (st === 'active' || st === 'trialing'
          ? '<button class="btn btn-bad dr-revoke">Revoke</button>' : '')
      + '</div>';
  }
  let html = '<div><div class="dh">Membership</div><dl class="kv">'
    + '<dt>Plan</dt><dd><b>' + esc(u.plan_label || u.plan) + '</b> — ' + esc(note[0]) + '</dd>'
    + '<dt>Status</dt><dd>' + esc(u.subscription_status || 'none') + '</dd>'
    + '<dt>Stripe</dt><dd>' + (u.stripe_customer_id ? esc(u.stripe_customer_id) : 'no customer record') + '</dd>'
    + (u.promo_code ? '<dt>Promo</dt><dd>' + esc(u.promo_code) + '</dd>' : '')
    + (u.ref ? '<dt>Referred by</dt><dd>' + esc(u.ref) + '</dd>' : '')
    + '<dt>Email</dt><dd>' + (u.email ? esc(u.email) : '—') + '</dd>'
    + '<dt>Joined</dt><dd>' + fmt(u.created_at) + '</dd>'
    + '<dt>User id</dt><dd>' + esc(u.id) + '</dd>'
    + '</dl>'
    + (mem ? '<div class="acts" style="justify-content:flex-start;margin-top:16px">' + mem + '</div>' : '')
    + '</div>';

  const [streams, clips] = await Promise.all([
    api('/admin/users/' + u.id + '/streams').catch(() => []),
    api('/admin/users/' + u.id + '/clips').catch(() => []),
  ]);
  if(DR_USER !== u) return;              // drawer moved on while we were loading

  html += '<div><div class="dh">Monitored streams (' + streams.length + ')</div>';
  html += streams.length
    ? streams.map(s => '<div class="srow"><span class="' + dotClass(s.status||'offline') + '"></span>'
        + '<span style="flex:1;min-width:0"><b>' + esc(s.channel) + '</b>'
        + '<span class="ct">' + esc(s.platform) + ' · preset ' + esc(s.preset || 'default') + '</span></span>'
        + '<span class="dim" style="font-size:12px;text-transform:capitalize">' + esc(s.status||'offline') + '</span></div>').join('')
    : '<div class="dim" style="font-size:13px">No streams registered.</div>';
  html += '</div>';

  const pend = clips.filter(c => c.status === 'pending').length;
  const appr = clips.filter(c => c.status === 'approved').length;
  html += '<div><div class="dh">Recent clips (' + clips.length + (clips.length === 100 ? '+' : '') + ')</div>';
  if(clips.length) html += '<div class="dim" style="font-size:12px;margin-bottom:10px">'
    + appr + ' approved · ' + pend + ' pending'
    + (clips.length === 100 ? ' · showing the first 100, approved first' : '') + '</div>';
  // The server already returns these approved-first (see admin_user_clips for
  // why that cannot be left to the browser); this just marks the two groups.
  html += clips.length
    ? clips.map(c => {
        const ok = c.status === 'approved';
        return '<div class="crow' + (ok ? ' ok' : '') + '"><span style="min-width:0"><b>' + esc(c.channel) + '</b>'
        + '<span class="ct">' + esc(c.clip_title || c.stream_title || '') + ' · ' + fmtTs(c.created_at) + '</span></span>'
        + '<span class="st">' + (ok ? 'Approved' : esc(c.status || 'pending')) + '</span>'
        + '<span class="num" style="font-size:12px;color:var(--glow-ink)">' + Math.round(c.virality_score||0) + '%</span>'
        + (c.twitch_url ? '<a class="btn" href="' + esc(c.twitch_url) + '" target="_blank" rel="noopener">Watch ↗</a>'
                        : '<span class="dim" style="font-size:12px">no link</span>')
        + '</div>';
      }).join('')
    : '<div class="dim" style="font-size:13px">No clips yet.</div>';
  html += '</div>';

  if(!u.is_admin || u.id !== ME){
    html += '<div class="danger"><div class="dh">Admin actions</div>'
      + '<p>These change what someone can do, or remove them entirely. They live here rather than in the table so you are always looking at the account before you act on it.</p>'
      + '<div class="acts" style="justify-content:flex-start">';
    if(!u.is_admin){
      html += '<button class="btn dr-labeler">' + (u.is_labeler ? 'Revoke trainer' : 'Make trainer') + '</button>';
      html += '<button class="btn dr-admin">Make admin</button>';
    } else if(u.id !== ME){
      html += '<button class="btn btn-bad dr-admin">Revoke admin</button>';
    }
    if(u.stripe_customer_id && !u.is_admin) html += '<button class="btn dr-sync">Sync with Stripe</button>';
    if(!u.is_admin) html += '<button class="btn btn-bad dr-del">Delete account</button>';
    html += '</div></div>';
  }
  document.getElementById('dr-body').innerHTML = html;
}

document.getElementById('dr-body').addEventListener('click', async e => {
  const u = DR_USER; if(!u) return;
  const b = e.target.closest('.dr-grant, .dr-revoke, .dr-labeler, .dr-admin, .dr-sync, .dr-del');
  if(!b) return;
  try {
    if(b.classList.contains('dr-grant')){
      const body = document.getElementById('dr-body');
      const plan = body.querySelector('.dr-plan').value;
      const days = parseInt(body.querySelector('.dr-days').value, 10) || 0;
      // A timed grant on someone who is already active would be refused by the
      // server ("already has an active subscription"), so clear them first —
      // otherwise "give this paying user a month of Starter" is a dead button.
      if(days && DR_STATE === 'active') await api('/admin/users/' + u.id + '/revoke', 'POST');
      closeDrawer();
      await grantMembership(u, plan, days);
      return;
    }
    if(b.classList.contains('dr-revoke')){
      if(!confirm('Revoke access for ' + u.username + '?')) return;
      await api('/admin/users/' + u.id + '/revoke', 'POST');
      toast('Access revoked'); closeDrawer(); refresh(); return;
    }
    if(b.classList.contains('dr-labeler')){
      const on = !u.is_labeler;
      if(!confirm((on ? 'Grant ' : 'Revoke ') + 'training-studio access for ' + u.username + '?')) return;
      await api('/admin/users/' + u.id + '/labeler?on=' + on, 'POST');
      toast(on ? 'Trainer access granted' : 'Trainer access revoked');
    } else if(b.classList.contains('dr-admin')){
      const on = !u.is_admin;
      const msg = on
        ? 'Make ' + u.username + ' a FULL ADMIN? They get the admin portal, control over every user (grant/revoke/delete), and free access. Only do this for someone you completely trust.'
        : 'Revoke admin access for ' + u.username + '?';
      if(!confirm(msg)) return;
      await api('/admin/users/' + u.id + '/admin?on=' + on, 'POST');
      toast(on ? u.username + ' is now an admin' : 'Admin access revoked');
    } else if(b.classList.contains('dr-sync')){
      const r = await api('/admin/users/' + u.id + '/stripe-sync', 'POST');
      toast(r.synced ? 'Synced: ' + r.app_status : 'No subscription found');
    } else if(b.classList.contains('dr-del')){
      if(!confirm('Permanently delete ' + u.username + ' and all their data? This cannot be undone.')) return;
      await api('/admin/users/' + u.id, 'DELETE');
      toast('User deleted');
    }
    closeDrawer(); refresh();
  } catch(err){ toast('Error: ' + err.message, false); }
});

// ── invite links ────────────────────────────────────────────────────────────
// The point of these is that the recipient never sees a billing page. Comping
// someone used to mean telling them to sign in first, and the sign-in page
// advertised a monthly price — being promised free access and then shown a
// price above a Connect-your-Twitch button is the exact shape of a scam.
(function initInviteForm(){
  const plan = document.getElementById('iv-plan'), days = document.getElementById('iv-days');
  if(!plan || !days) return;
  plan.innerHTML = PLAN_OPTIONS.map(p => '<option value="' + p[0] + '">' + p[1] + '</option>').join('');
  plan.value = 'pro';
  days.innerHTML = DURATIONS.map(d => '<option value="' + d[0] + '">' + d[1] + '</option>').join('');
})();

function inviteUrl(code){ return location.origin + '/i/' + code; }

function ivRow(i){
  const url = inviteUrl(i.code);
  const who = i.claims && i.claims.length
    ? i.claims.map(c => rvEsc(c.username || c.user_id)).join(', ')
    : '<span class="dim">unclaimed</span>';
  const state = i.live
    ? '<span class="st" style="color:var(--good)">Live</span>'
    : '<span class="st">' + (i.expired ? 'Expired' : 'Used up') + '</span>';
  return '<tr><td><span class="num" style="font-size:12px">' + rvEsc(url) + '</span>'
    + (i.note ? '<span class="ct">' + rvEsc(i.note) + '</span>' : '') + '</td>'
    + '<td><span class="plan ' + planClass(i.plan) + '">' + rvEsc(i.plan) + '</span>'
    + '<div class="plan-note">' + (i.days ? i.days + ' days' : 'no end date') + '</div></td>'
    + '<td>' + state + '<div class="plan-note">' + i.uses_left + ' of ' + i.max_uses + ' left</div></td>'
    + '<td>' + who + '</td>'
    + '<td><div class="acts"><button class="btn iv-copy" data-url="' + rvEsc(url) + '">Copy</button>'
    + '<button class="btn btn-bad iv-del" data-code="' + rvEsc(i.code) + '">Revoke</button></div></td></tr>';
}

async function loadInvites(){
  const wrap = document.getElementById('iv-wrap');
  if(!wrap) return;
  let d;
  try { d = await api('/admin/invites'); } catch(e){ fail('iv-wrap', 'invite links'); return; }
  const rows = d.invites || [];
  document.getElementById('iv-c').textContent =
    rows.filter(i => i.live).length + ' live of ' + rows.length;
  if(!rows.length){
    wrap.className = 'empty';
    wrap.textContent = 'No invite links yet. Create one above and send it to someone.';
    return;
  }
  wrap.className = '';
  wrap.innerHTML = '<table><thead><tr><th>Link</th><th>Grants</th><th>Status</th>'
    + '<th>Claimed by</th><th style="text-align:right">Actions</th></tr></thead><tbody>'
    + rows.map(ivRow).join('') + '</tbody></table>';
}

document.getElementById('iv-make').addEventListener('click', async () => {
  const plan = document.getElementById('iv-plan').value;
  const days = parseInt(document.getElementById('iv-days').value, 10) || 0;
  const note = document.getElementById('iv-note').value;
  try {
    const r = await fetch('/admin/invites', {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({plan, days, note, max_uses:1, ttl_days:30})});
    if(!r.ok) throw new Error(await r.text());
    const inv = await r.json();
    document.getElementById('iv-note').value = '';
    // Straight to the clipboard: the link is the whole deliverable, and making
    // someone hunt for it in a table they just created is a needless step.
    copyText(inviteUrl(inv.code), 'Invite link copied');
    loadInvites();
  } catch(err){ toast('Error: ' + err.message, false); }
});

function copyText(text, msg){
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(() => toast(msg))
      .catch(() => toast(text, true));
    return;
  }
  // Clipboard API needs a secure context; without one, show the link so it can
  // still be selected by hand rather than silently doing nothing.
  toast(text, true);
}

document.getElementById('iv-wrap').addEventListener('click', async e => {
  const cp = e.target.closest('.iv-copy'), del = e.target.closest('.iv-del');
  if(cp) return copyText(cp.dataset.url, 'Invite link copied');
  if(del){
    if(!confirm('Revoke this link? Anyone who already claimed it keeps their membership.')) return;
    try { await fetch('/admin/invites/' + encodeURIComponent(del.dataset.code),
                      {method:'DELETE', credentials:'same-origin'});
          toast('Link revoked'); loadInvites(); }
    catch(err){ toast('Error: ' + err.message, false); }
  }
});

// ── growth: referrals + promo codes ─────────────────────────────────────────
async function loadReferrals(){
  let d;
  try { d = await api('/admin/referrals'); } catch(e){ fail('rf-wrap', 'referrals'); return; }
  const rows = d.rows || [], wrap = document.getElementById('rf-wrap');
  document.getElementById('rf-c').textContent = (d.total || 0) + ' users attributed';
  document.getElementById('tc-growth').textContent = rows.length;
  if(!rows.length){ wrap.className='empty'; wrap.textContent='Nothing attributed yet.'; return; }
  wrap.className = '';
  wrap.innerHTML = '<table><thead><tr><th>Who</th><th>Signups</th><th>Connected a channel</th>'
    + '<th>Still active wk2</th><th>Paid</th><th>Link</th></tr></thead><tbody>'
    + rows.map(r => '<tr><td style="font-weight:600">' + rvEsc(r.label) + '</td>'
        + '<td class="num">' + r.signups + '</td>'
        + '<td class="num">' + r.connected + '</td>'
        + '<td class="num" style="color:var(--good)">' + r.active_wk2 + '</td>'
        + '<td class="num">' + r.paid + '</td>'
        + '<td class="dim" style="font-family:var(--mono);font-size:11.5px">'
        + (r.ref === 'direct' ? '—' : 'highlightz.app/?ref=' + rvEsc(r.ref)) + '</td></tr>').join('')
    + '</tbody></table>';
}

function renderPromo(){
  const wrap = document.getElementById('pr-wrap');
  const byCode = {};
  USERS.filter(u => !u.is_admin).forEach(u => {
    if(!u.promo_code) return;
    const k = u.promo_code;
    byCode[k] = byCode[k] || {signups:0, active:0};
    byCode[k].signups++;
    if(u.subscription_status === 'active') byCode[k].active++;
  });
  const codes = Object.keys(byCode).sort((a,b) => byCode[b].signups - byCode[a].signups);
  document.getElementById('pr-c').textContent = codes.length + ' codes';
  if(!codes.length){
    wrap.className = 'empty';
    wrap.textContent = 'No promo-code signups yet. Codes are attributed automatically when a subscriber uses one at checkout.';
    return;
  }
  wrap.className = '';
  wrap.innerHTML = '<table><thead><tr><th>Code</th><th>Signups</th><th>Active now</th>'
    + '<th>Est. payout ($5/signup)</th></tr></thead><tbody>'
    + codes.map(c => '<tr><td style="font-weight:600;color:var(--glow-ink)">' + esc(c) + '</td>'
        + '<td class="num">' + byCode[c].signups + '</td>'
        + '<td class="num">' + byCode[c].active + '</td>'
        + '<td class="num">$' + (byCode[c].signups * 5) + '</td></tr>').join('')
    + '</tbody></table>';
}

// ── clip record ─────────────────────────────────────────────────────────────
let CR_ROWS = [], CR_SORT = 'caught', CR_DESC = true, CR_OPEN = null;
const CR_COLS = [
  ['channel','Channel'], ['username','User'], ['caught','Caught'], ['approved','Kept'],
  ['rejected','Rejected'], ['expired','Aged out'], ['kept_pct','Keep rate'], ['last_at','Last clip'],
];
const CR_TEXT = {channel:1, username:1};

function crWhen(ts){
  if(!ts) return '—';
  const d = new Date(ts*1000);
  return d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' '
       + d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
}

function crSorted(){
  const q = (document.getElementById('cr-filter').value || '').toLowerCase();
  const rows = CR_ROWS.filter(r => !q
    || String(r.channel).toLowerCase().indexOf(q) >= 0
    || String(r.username).toLowerCase().indexOf(q) >= 0);
  // Text columns compare as text, numbers as numbers. Sorting 'caught' as a
  // string puts 9 above 40, which is the kind of wrong nobody notices until
  // they quote the wrong figure at a streamer.
  rows.sort((a,b) => {
    const x = a[CR_SORT], y = b[CR_SORT];
    const cmp = CR_TEXT[CR_SORT]
      ? String(x).toLowerCase().localeCompare(String(y).toLowerCase())
      : (Number(x)||0) - (Number(y)||0);
    return CR_DESC ? -cmp : cmp;
  });
  return rows;
}

function crRender(){
  const wrap = document.getElementById('cr-wrap');
  document.getElementById('cr-c').textContent = CR_ROWS.length + ' channels';
  document.getElementById('tc-clips').textContent = CR_ROWS.length;
  if(!CR_ROWS.length){
    wrap.className = 'empty';
    wrap.textContent = 'Nothing recorded yet. Counting starts from the first clip caught after this shipped.';
    return;
  }
  const rows = crSorted();
  if(!rows.length){ wrap.className='empty'; wrap.textContent='No match.'; return; }
  wrap.className = '';
  const arrow = k => CR_SORT === k ? (CR_DESC ? ' ▾' : ' ▴') : '';
  let html = '<table><thead><tr>' + CR_COLS.map(c =>
      '<th class="sortable cr-sort' + (CR_SORT === c[0] ? ' on' : '') + '" data-k="' + c[0] + '">'
      + c[1] + arrow(c[0]) + '</th>').join('') + '</tr></thead><tbody>';
  rows.forEach(r => {
    const key = r.user_id + '|' + r.channel;
    html += '<tr class="expandable cr-row" data-key="' + rvEsc(key) + '">'
      + '<td style="font-weight:600">' + rvEsc(r.channel) + '</td>'
      + '<td class="dim">' + rvEsc(r.username) + '</td>'
      + '<td class="num">' + r.caught + '</td>'
      + '<td class="num" style="color:var(--good)">' + r.approved + '</td>'
      + '<td class="num">' + r.rejected + '</td>'
      + '<td class="num dim">' + r.expired + '</td>'
      + '<td class="num">' + (r.caught ? r.kept_pct + '%' : '—') + '</td>'
      + '<td class="dim">' + crWhen(r.last_at) + '</td></tr>';
    if(CR_OPEN === key){
      const ss = r.sessions || [];
      let inner = '<div class="dh">Per stream (' + ss.length + ')</div>';
      if(r.expired){
        inner += '<p class="dim" style="font-size:12px;margin-bottom:10px">' + r.expired
          + ' aged out before review, so the keep rate counts them as not kept. '
          + 'Of what was actually reviewed: ' + r.kept_of_reviewed_pct + '%.</p>';
      }
      inner += ss.map(s => '<div style="display:flex;align-items:center;gap:12px;padding:5px 0;font-size:12.5px">'
        + '<span class="dim" style="width:118px;flex-shrink:0;font-family:var(--mono);font-size:11px">' + crWhen(s.started_at) + '</span>'
        + '<span class="bar"><i style="width:' + (s.caught ? (s.approved/s.caught*100) : 0) + '%"></i></span>'
        + '<span class="num" style="width:150px;flex-shrink:0;text-align:right;font-size:12px">'
        + s.approved + ' kept of ' + s.caught + (s.caught ? ' (' + s.kept_pct + '%)' : '') + '</span></div>').join('');
      html += '<tr class="drill"><td colspan="' + CR_COLS.length + '">' + inner + '</td></tr>';
    }
  });
  wrap.innerHTML = html + '</tbody></table>';
}

async function loadClipRecord(){
  try { const d = await api('/admin/stream-stats'); CR_ROWS = d.rows || []; }
  catch(e){ fail('cr-wrap', 'the clip record'); return; }
  crRender();
}

document.getElementById('cr-wrap').addEventListener('click', e => {
  const th = e.target.closest('.cr-sort');
  if(th){
    const k = th.dataset.k;
    // Same column toggles direction; a new column starts descending, because
    // "who has the most" is what you want first every time except on the two
    // text columns.
    if(CR_SORT === k) CR_DESC = !CR_DESC;
    else { CR_SORT = k; CR_DESC = !CR_TEXT[k]; }
    crRender();
    return;
  }
  const tr = e.target.closest('.cr-row');
  if(tr){ CR_OPEN = (CR_OPEN === tr.dataset.key) ? null : tr.dataset.key; crRender(); }
});
document.getElementById('cr-filter').addEventListener('input', crRender);

// ── reviews ─────────────────────────────────────────────────────────────────
function rvStars(n){
  return '<span style="color:var(--ember);letter-spacing:1px">' + RV_STAR.repeat(n)
    + '<span style="color:#3A3242">' + RV_STAR.repeat(5-n) + '</span></span>';
}

function rvRow(r){
  // Consent is the user's decision and is NOT overridable here. The publish
  // button only exists for reviews they agreed to publish; offering it
  // otherwise invites putting someone's words on the public site by accident.
  const act = r.publish_consent
    ? '<button class="btn rv-act" data-id="' + rvEsc(r.id) + '" data-on="' + (!r.approved) + '">'
      + (r.approved ? 'Unpublish' : 'Publish') + '</button>'
    : '<span class="dim" style="font-size:11px">no consent</span>';
  return '<tr><td>' + rvStars(r.stars) + '</td>'
    + '<td style="max-width:340px;white-space:pre-wrap">' + rvEsc(r.comment || '—') + '</td>'
    + '<td class="dim">' + rvEsc(r.username || r.user_id) + '</td>'
    + '<td>' + (r.publish_consent ? rvEsc(r.display_name || 'Highlightz user')
                                  : '<span class="dim">—</span>') + '</td>'
    + '<td><div class="acts" style="justify-content:flex-start">' + act
    + ' <button class="btn btn-bad rv-del" data-id="' + rvEsc(r.id) + '">Delete</button></div></td></tr>';
}

async function loadReviews(){
  const wrap = document.getElementById('rv-wrap');
  let d;
  try { d = await api('/admin/reviews'); } catch(e){ fail('rv-wrap', 'reviews'); return; }
  const rows = d.reviews || [], agg = d.aggregate || {count:0, average:0};
  document.getElementById('rv-c').textContent = rows.length + ' total'
    + (agg.count ? ' · ' + agg.average + RV_STAR + ' from ' + agg.count + ' published' : '');
  document.getElementById('tc-reviews').textContent = rows.length;
  if(!rows.length){
    wrap.className = 'empty';
    wrap.textContent = 'Nothing yet. The prompt appears at 25 approved clips.';
    return;
  }
  wrap.className = '';
  wrap.innerHTML = '<table><thead><tr><th>Rating</th><th>Comment</th><th>User</th>'
    + '<th>Shows as</th><th>Public</th></tr></thead><tbody>'
    + rows.map(rvRow).join('') + '</tbody></table>';
}

document.getElementById('rv-wrap').addEventListener('click', async e => {
  const act = e.target.closest('.rv-act'), del = e.target.closest('.rv-del');
  if(act){
    await fetch('/admin/reviews/' + act.dataset.id + '/approve', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({approved: act.dataset.on === 'true'})});
    loadReviews();
  } else if(del){
    if(!confirm('Delete this review permanently?')) return;
    await fetch('/admin/reviews/' + del.dataset.id, {method:'DELETE'});
    loadReviews();
  }
});

// ── boot ────────────────────────────────────────────────────────────────────
// refresh() is what every mutating action calls, so a grant/revoke/delete
// updates the header figures too — the old page reloaded only the user table
// and left the totals stale until you hit F5.
async function refresh(){
  await loadUsers();
  renderPromo();
  loadOverview();
}

fetch('/me').then(r => r.json()).then(m => { ME = m.user_id || ''; }).catch(() => {});
fetch('/admin/feedback').then(r => r.json()).then(fb => {
  const unread = (fb || []).filter(f => !f.read).length;
  const el = document.getElementById('feedback-link');
  if(el && unread > 0) el.innerHTML = 'Feedback <span class="n">(' + unread + ')</span>';
}).catch(() => {});

refresh();
loadReferrals();
loadInvites();
loadClipRecord();
loadReviews();
</script>
</body>
</html>"""

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>404 — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;text-align:center}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 50% 30%,rgba(168,85,247,.18),transparent 60%)}
  .wrap{padding:40px 24px}
  .code{font-size:100px;font-weight:800;letter-spacing:-.05em;background:linear-gradient(135deg,#f943ff 0%,#a855f7 52%,#7c6bff 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1}
  h1{font-size:22px;font-weight:700;margin:16px 0 8px;letter-spacing:-.02em}
  p{font-size:14px;color:#9c9caa;margin-bottom:28px}
  a{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);color:#f6f6f9;border-radius:12px;padding:11px 20px;font-size:13px;font-weight:600;text-decoration:none;transition:.15s}
  a:hover{background:rgba(255,255,255,.1)}
</style>
</head>
<body>
<div class="wrap">
  <div class="code">404</div>
  <h1>Page not found</h1>
  <p>The page you're looking for doesn't exist or was moved.</p>
  <a href="/">&#8592; Back to dashboard</a>
</div>
</body>
</html>"""

# ── Opt-out HTML ───────────────────────────────────────────────────────────────

_OPTOUT_BASE_STYLE = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 500px at 50% 20%,rgba(168,85,247,.15),transparent 60%)}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:20px;padding:40px 36px;max-width:480px;width:100%;text-align:center}
.logo{font-size:22px;font-weight:800;background:linear-gradient(135deg,#f943ff,#a855f7,#7c6bff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:28px}
h1{font-size:22px;font-weight:700;letter-spacing:-.02em;margin-bottom:10px}
p{font-size:14px;color:#9c9caa;line-height:1.6;margin-bottom:20px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:13px 24px;border-radius:12px;font-size:14px;font-weight:600;cursor:pointer;border:none;text-decoration:none;transition:.15s}
.btn-twitch{background:#9146ff;color:#fff}
.btn-twitch:hover{background:#7c39d4}
.btn-confirm{background:linear-gradient(135deg,#f943ff,#a855f7);color:#fff;width:100%;justify-content:center;font-size:15px;padding:14px}
.btn-confirm:hover{opacity:.9}
.btn-back{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);color:#9c9caa;font-size:13px}
.btn-back:hover{background:rgba(255,255,255,.1);color:#f6f6f9}
.avatar{width:72px;height:72px;border-radius:50%;object-fit:cover;margin:0 auto 16px;display:block;border:2px solid rgba(168,85,247,.4)}
.avatar-placeholder{width:72px;height:72px;border-radius:50%;background:rgba(168,85,247,.2);margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:28px}
.name{font-size:18px;font-weight:700;margin-bottom:4px}
.handle{font-size:13px;color:#9c9caa;margin-bottom:24px}
.warning{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:10px;padding:12px 16px;font-size:13px;color:#fca5a5;margin-bottom:24px;text-align:left}
.success-icon{font-size:52px;margin-bottom:16px}
.steps{text-align:left;margin-bottom:24px}
.steps li{font-size:13px;color:#9c9caa;padding:5px 0;padding-left:20px;position:relative;line-height:1.5}
.steps li::before{content:'✓';position:absolute;left:0;color:#a855f7}
.divider{height:1px;background:rgba(255,255,255,.07);margin:20px 0}
"""

_OPTOUT_LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Streamer Opt-Out — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>""" + _OPTOUT_BASE_STYLE + """</style>
</head>
<body>
<div class="card">
  <div class="logo">Highlightz</div>
  <h1>Streamer Opt-Out</h1>
  <p>Highlightz lets users automatically create clips of live streams using Twitch's official Clips API. If you are a streamer and do not want your channel to be clipped through this platform, you can opt out below.</p>
  <ul class="steps">
    <li>Verify your identity by signing in with Twitch</li>
    <li>Confirm your opt-out on the next screen</li>
    <li>Your channel will be permanently blacklisted — no users will be able to add it</li>
  </ul>
  <div class="divider"></div>
  <p style="font-size:13px;margin-bottom:20px">You must be the actual owner of the Twitch channel. We verify this through Twitch's official login — you cannot opt out someone else's channel.</p>
  <a href="/auth/twitch?intent=optout" class="btn btn-twitch">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714z"/></svg>
    Verify with Twitch
  </a>
  <div class="divider"></div>
  <p style="font-size:12px;color:#6b6b7b;margin-bottom:0">Already a Highlightz user? <a href="/login" style="color:#a855f7;text-decoration:none">Log in here</a></p>
</div>
</body>
</html>"""

_OPTOUT_CONFIRM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Confirm Opt-Out — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>""" + _OPTOUT_BASE_STYLE + """</style>
</head>
<body>
<div class="card">
  <div class="logo">Highlightz</div>
  <h1>Confirm Opt-Out</h1>
  <p>You are opting out the following channel:</p>
  {avatar_section}
  <div class="name">{display_name}</div>
  <div class="handle">@{twitch_login}</div>
  <div class="warning">
    <strong>This is permanent.</strong> Once confirmed, no Highlightz user will be able to add <strong>@{twitch_login}</strong> as a monitored channel. Any existing monitoring will be blocked on the next attempt.
  </div>
  <form method="POST" action="/opt-out/confirm">
    <button type="submit" class="btn btn-confirm">Yes, opt out @{twitch_login}</button>
  </form>
  <div style="margin-top:14px">
    <a href="/opt-out" class="btn btn-back">Cancel</a>
  </div>
</div>
</body>
</html>"""

_OPTOUT_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Opted Out — Highlightz</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>""" + _OPTOUT_BASE_STYLE + """</style>
</head>
<body>
<div class="card">
  <div class="logo">Highlightz</div>
  <div class="success-icon">✅</div>
  <h1>You've been opted out</h1>
  <p>Your channel has been added to the Highlightz blacklist. No users on this platform will be able to monitor or clip your stream going forward.</p>
  <p style="font-size:13px">If you change your mind in the future, contact <a href="mailto:support@highlightz.app" style="color:#a855f7;text-decoration:none">support@highlightz.app</a> to be removed.</p>
</div>
</body>
</html>"""

_ADMIN_FEEDBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Feedback — Highlightz Admin</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;padding:32px 24px;min-height:100vh}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.18),transparent 60%)}
  .topbar{display:flex;align-items:center;gap:16px;margin-bottom:28px}
  .back{color:#9c9caa;text-decoration:none;font-size:13px;font-weight:600}
  .back:hover{color:#f6f6f9}
  h1{font-size:22px;font-weight:800;letter-spacing:-.02em}
  .badge{display:inline-flex;align-items:center;gap:5px;background:rgba(145,70,255,.15);border:1px solid rgba(145,70,255,.3);color:#c79bff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:99px}
  .empty{text-align:center;padding:64px 0;color:#5d5d6b;font-size:14px}
  .fb-list{display:flex;flex-direction:column;gap:12px;max-width:820px}
  .fb-item{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:18px 20px;transition:border-color .15s}
  .fb-item.unread{border-color:rgba(145,70,255,.4);background:rgba(145,70,255,.06)}
  .fb-meta{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
  .fb-user{font-weight:700;font-size:14px;color:#f6f6f9}
  .fb-cat{font-size:11px;font-weight:700;padding:2px 9px;border-radius:99px;background:rgba(255,255,255,.07);color:#9c9caa;text-transform:capitalize}
  .fb-time{font-size:11px;color:#5d5d6b;margin-left:auto}
  .fb-msg{font-size:14px;color:#d4d4e0;line-height:1.65;white-space:pre-wrap;word-break:break-word}
  .fb-actions{display:flex;gap:8px;margin-top:12px}
  .btn{padding:6px 14px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;border:none;transition:.15s}
  .btn-read{background:rgba(255,255,255,.07);color:#9c9caa}
  .btn-read:hover{background:rgba(255,255,255,.12);color:#f6f6f9}
  .btn-del{background:rgba(255,80,80,.12);color:#ff8080;border:1px solid rgba(255,80,80,.2)}
  .btn-del:hover{background:rgba(255,80,80,.2)}
  .new-dot{width:8px;height:8px;border-radius:50%;background:#a855f7;box-shadow:0 0 8px #a855f7;flex-shrink:0}
  .toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1e1e2e;border:1px solid rgba(255,255,255,.12);color:#f6f6f9;padding:10px 20px;border-radius:12px;font-size:13px;font-weight:600;opacity:0;transition:opacity .25s;pointer-events:none}
  .toast.show{opacity:1}
</style>
</head>
<body>
<div class="topbar">
  <a href="/admin" class="back">&#8592; Admin panel</a>
  <h1>User Feedback</h1>
  <span class="badge" id="unread-badge" style="display:none"></span>
</div>
<div class="fb-list" id="list"><p class="empty">Loading…</p></div>
<div class="toast" id="toast"></div>
<script>
  let items=[];
  function fmt(ts){if(!ts)return '';const d=new Date(ts*1000);return d.toLocaleDateString()+' '+d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});}
  async function api(url,method='GET'){const r=await fetch(url,{method});if(!r.ok)throw new Error(r.status);return r.json();}
  function toast(msg){const el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2500);}
  function render(){
    const list=document.getElementById('list');
    if(!items.length){list.innerHTML='<p class="empty">No feedback yet.</p>';return;}
    const unread=items.filter(f=>!f.read).length;
    const badge=document.getElementById('unread-badge');
    if(unread>0){badge.textContent=unread+' unread';badge.style.display='inline-flex';}
    else badge.style.display='none';
    list.innerHTML=items.map(f=>`
      <div class="fb-item${f.read?'':' unread'}" id="fb-${f.id}">
        <div class="fb-meta">
          ${f.read?'':'<span class="new-dot"></span>'}
          <span class="fb-user">${esc(f.username||f.user_id)}</span>
          <span class="fb-cat">${esc(f.category||'general')}</span>
          <span class="fb-time">${fmt(f.created_at)}</span>
        </div>
        <div class="fb-msg">${esc(f.message)}</div>
        <div class="fb-actions">
          ${f.read?'':`<button class="btn btn-read" onclick="markRead('${f.id}')">Mark read</button>`}
          <button class="btn btn-del" onclick="del('${f.id}')">Delete</button>
        </div>
      </div>`).join('');
  }
  function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
  async function markRead(id){
    try{await api('/admin/feedback/'+id+'/read','POST');items.find(f=>f.id===id).read=true;render();toast('Marked as read');}
    catch{toast('Error');}
  }
  async function del(id){
    try{await api('/admin/feedback/'+id,'DELETE');items=items.filter(f=>f.id!==id);render();toast('Deleted');}
    catch{toast('Error');}
  }
  async function load(){
    try{items=await api('/admin/feedback');render();}
    catch{document.getElementById('list').innerHTML='<p class="empty">Failed to load feedback.</p>';}
  }
  load();
</script>
</body>
</html>"""

_ADMIN_OPTOUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Opt-Out Registry — Highlightz Admin</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;padding:32px 24px}
body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 50% 0,rgba(168,85,247,.12),transparent 60%)}
h1{font-size:20px;font-weight:700;margin-bottom:4px}
.sub{font-size:13px;color:#9c9caa;margin-bottom:24px}
.back{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:#a855f7;text-decoration:none;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;color:#9c9caa;border-bottom:1px solid rgba(255,255,255,.08);font-weight:500}
td{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.05);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.02)}
.btn-remove{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.25);color:#fca5a5;padding:5px 12px;border-radius:8px;font-size:12px;cursor:pointer;font-family:inherit}
.btn-remove:hover{background:rgba(239,68,68,.2)}
.empty{color:#9c9caa;font-size:14px;padding:32px 0;text-align:center}
.toast{position:fixed;bottom:24px;right:24px;background:#1a1a2e;border:1px solid rgba(168,85,247,.3);color:#f6f6f9;padding:12px 20px;border-radius:12px;font-size:13px;opacity:0;transition:.3s;z-index:9999}
.toast.show{opacity:1}
</style>
</head>
<body>
<a href="/admin" class="back">&#8592; Admin panel</a>
<h1>Streamer Opt-Out Registry</h1>
<div class="sub">Streamers who have verified and opted out of being clipped on Highlightz.</div>
<div id="wrap"><div class="empty">Loading...</div></div>
<div class="toast" id="toast"></div>
<script>
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500)}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML}
async function api(url,method='GET'){const r=await fetch(url,{method,headers:{'Content-Type':'application/json'}});if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||r.status)}return r.json()}
function fmt(ts){if(!ts)return'—';return new Date(ts*1000).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}
async function remove(id,name){
  if(!confirm('Remove @'+name+' from the opt-out list? They will be clippable again.'))return;
  try{await api('/admin/optout/'+id,'DELETE');toast('@'+name+' removed');load()}
  catch(e){toast('Error: '+e.message)}
}
async function load(){
  const items=await api('/admin/optout/list');
  if(!items.length){document.getElementById('wrap').innerHTML='<div class="empty">No streamers have opted out yet.</div>';return}
  const rows=items.map(i=>'<tr><td><strong>'+esc(i.display_name)+'</strong><br><span style="color:#9c9caa;font-size:12px">@'+esc(i.twitch_login)+'</span></td><td style="color:#9c9caa">'+esc(i.twitch_id)+'</td><td>'+fmt(i.opted_out_at)+'</td><td><button class="btn-remove" onclick="remove('+JSON.stringify(i.twitch_id)+','+JSON.stringify(i.twitch_login)+')">Remove</button></td></tr>').join('');
  document.getElementById('wrap').innerHTML='<table><thead><tr><th>Streamer</th><th>Twitch ID</th><th>Opted Out</th><th></th></tr></thead><tbody>'+rows+'</tbody></table>';
}
load();
</script>
</body>
</html>"""


# ── Short referral links ──────────────────────────────────────────────────────
#
# `highlightz.app/ian` instead of `highlightz.app/?ref=ian`. A bio field shows
# whatever URL you type into it, so the attribution cannot be hidden outright —
# but a bare path reads as a page rather than as tracking, which is the whole
# difference in a bio.
#
# REGISTERED LAST ON PURPOSE. FastAPI matches routes in registration order, so
# putting a single-segment path here means every real route above already had
# its chance. The handler additionally refuses anything not in REFERRERS, so it
# can never shadow a future /settings or /pricing — an unknown slug 404s exactly
# as it would have without this route.

@app.get("/r/{slug}")
async def referral_short_link(request: Request, slug: str):
    return _referral_redirect(request, slug)


@app.get("/{slug}")
async def referral_bare_link(request: Request, slug: str):
    return _referral_redirect(request, slug)


def _referral_redirect(request: Request, slug: str):
    from src.auth import referrals
    ref = referrals.normalise(slug)
    if not ref:
        # Not a referrer. Behave exactly as if this route did not exist.
        raise HTTPException(status_code=404, detail="Not found")
    # First touch wins here too, matching _capture_ref.
    if not request.session.get("ref"):
        request.session["ref"] = ref
    # 302, not 301: browsers cache a permanent redirect, and a cached redirect
    # from /tommy would keep sending that person to the landing page even after
    # they are signed in.
    return RedirectResponse("/", status_code=302)
