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
from typing import Any
from pathlib import Path
from fastapi.staticfiles import StaticFiles

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
                  "/opt-out", "/opt-out/confirm", "/opt-out/success", "/landing/stats"}
_AUTH_PREFIXES = ("/auth/", "/billing/")
_STATIC_PREFIX = "/static"

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (path in _OPEN_PATHS or path == "/login"
                or path.startswith(_STATIC_PREFIX)
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
                # A trial that has run past its 7 days no longer grants access.
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
                request.session["subscription_status"] = status
                request.session["trial_ends_at"]       = trial_ends_at
                _user_last_active[uid] = time.time()
        # Billing gate — redirect to paywall unless admin, active subscriber,
        # or within an unexpired free trial.
        if not request.session.get("is_admin") and request.session.get("subscription_status") not in ("active", "trialing"):
            if not request.headers.get("accept", "").startswith("application/json"):
                return RedirectResponse("/billing/paywall", status_code=302)
            return JSONResponse({"detail": "Subscription required"}, status_code=402)
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
_user_last_active: dict[str, float] = {}
_IDLE_STREAM_TIMEOUT = 28800  # 8 hours

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
_MAX_PENDING_CLIPS = 200 # per-user cap on pending clips

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

        # Per-user pending cap
        user_pending = sorted(
            [c for c in _clips.values()
             if c.get("status") == "pending" and c.get("user_id") == clip_uid],
            key=lambda c: c.get("created_at", 0),
        )
        evicted = []
        while len(user_pending) >= _MAX_PENDING_CLIPS:
            oldest = user_pending.pop(0)
            del _clips[oldest["id"]]
            _delete_clip_file(oldest)
            log.info("clip_cap_evicted", clip_id=oldest["id"], channel=oldest.get("channel"))
            evicted.append(oldest)

        _clips[clip["id"]] = clip
        _save_clips()
        increment_clip_counter()

    # A pending clip pushed out at the cap was never approved → weak negative for
    # the training log (the user had it in their queue and didn't keep it).
    from src.profiles import training_log
    for ev in evicted:
        training_log.log_outcome(ev, training_log.EXPIRED_UNREVIEWED)
        await broadcast({"event": "clip_removed", "clip_id": ev["id"]}, user_id=clip_uid)
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
    return {
        "user_id":             uid,
        "username":            request.session.get("username", ""),
        "avatar_url":          request.session.get("avatar_url", ""),
        "is_admin":            request.session.get("is_admin", False),
        "subscription_status": status,
        "trial_ends_at":       trial_ends_at,
        "trial_days_left":     trial_days_left,
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

    user_store.delete(uid)
    request.session.clear()
    log.info("account_deleted", user_id=uid)
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


# ── Stripe billing ─────────────────────────────────────────────────────────────

def _paywall_copy(trial_available: bool) -> dict:
    """Honest paywall copy: only promise '7 days free' to identities that can
    actually get the trial — returning users are charged immediately, and
    telling them otherwise is a chargeback waiting to happen."""
    if trial_available:
        return {
            "headline": "Start your 7-day free trial",
            "subline":  ("add a card to start capturing your best streaming moments. "
                         "Free for 7 days, then it auto-renews monthly. Cancel anytime "
                         "before the trial ends and you won't be charged."),
            "cta":      "Start free trial &#8594;",
            "note":     "Card required to start. Have a promo code? Enter it at checkout for 50% off your first month.",
        }
    return {
        "headline": "Restart your subscription",
        "subline":  ("welcome back. Your one-time free trial has already been used, so "
                     "your subscription starts right away — $15/month, cancel anytime."),
        "cta":      "Subscribe &middot; $15/month &#8594;",
        "note":     "Have a promo code? Enter it at checkout for 50% off your first month.",
    }


@app.get("/billing/paywall", response_class=HTMLResponse)
async def paywall_page(request: Request):
    from src.auth import users as user_store
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    db_user  = user_store.get_by_id(uid) if uid else None
    if db_user is None:
        # Unknown/logged-out visitor: show the standard trial pitch — the
        # checkout itself enforces trial eligibility either way.
        trial_available = True
    else:
        claim_id = user_store.trial_claim_id(db_user)
        trial_available = bool(claim_id) and not user_store.has_claimed_trial(claim_id)
    copy = _paywall_copy(trial_available)
    import html as _html
    page = (PAYWALL_HTML
            .replace("{username}", _html.escape(username))
            .replace("{headline}", copy["headline"])
            .replace("{subline}",  copy["subline"])
            .replace("{cta_label}", copy["cta"])
            .replace("{cta_note}",  copy["note"]))
    return HTMLResponse(page)


@app.get("/billing/checkout")
async def billing_checkout(request: Request):
    """Create a Stripe Checkout session and redirect the user there.

    First-time identities get a 7-day free trial (card still required, auto-
    converts). Anyone who has already used their one trial is charged immediately.
    """
    from src.billing.stripe_billing import create_checkout_url
    from src.auth import users as user_store
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    if not uid:
        return RedirectResponse("/login")
    db_user  = user_store.get_by_id(uid)
    # Already subscribed (or mid webhook-latency after paying)? Never open a
    # second checkout — that mints a second Stripe subscription the user can't
    # see or cancel. Send them into the app instead.
    if db_user and db_user.get("subscription_status") in ("active", "trialing"):
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
    claim_id = user_store.trial_claim_id(db_user) if db_user else ""
    # One free trial per identity; returning users pay immediately.
    trial_days = user_store.TRIAL_DAYS if (claim_id and not user_store.has_claimed_trial(claim_id)) else 0
    # Reuse the existing Stripe customer so a re-subscribe stays on one customer
    # (portal / cancel-on-delete / admin sync all key off the stored id).
    url = await create_checkout_url(
        uid, username, trial_days=trial_days, customer_id=stripe_customer)
    return RedirectResponse(url)


@app.get("/billing/portal")
async def billing_portal(request: Request):
    """Open the Stripe Customer Portal so users can manage / cancel."""
    from src.billing import stripe_billing
    from src.auth import users as user_store
    uid  = _current_user_id(request)
    user = user_store.get_by_id(uid)
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse("/billing/checkout")
    url = await stripe_billing.create_portal_url(user["stripe_customer_id"])
    if not url.startswith("https://billing.stripe.com/"):
        log.error("stripe_portal_url_unexpected", url=url[:64])
        raise HTTPException(status_code=502, detail="Unexpected billing portal URL")
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
    from src.auth import users as user_store
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
            asyncio.create_task(_stop_user_streams_now(user_id))
            await broadcast(
                {"event": "subscription_expired",
                 "message": "Your subscription has ended — streams have been stopped."},
                user_id=user_id,
            )
        elif status in ("active", "trialing") and user_id:
            # The user now has a real subscription (trial or paid) → burn their
            # one-time trial claim so they can't get a second free trial later.
            # Done here (not at checkout creation) so an abandoned checkout doesn't
            # waste the trial.
            db_user  = user_store.get_by_id(user_id)
            claim_id = user_store.trial_claim_id(db_user) if db_user else ""
            if claim_id:
                user_store.record_trial_claim(claim_id)
            # Realtime contract: a trial→paid conversion (or a past_due→active
            # recovery) must clear the paywall/trial banner in any open tab live,
            # mirroring the subscription_expired broadcast on the lapse path.
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
        _save_clips()
    from src.profiles import training_log
    training_log.log_outcome(clip, training_log.APPROVED)
    await broadcast({"event": "clip_updated", "clip": clip}, user_id=uid)
    pm      = get_profile_manager(uid)
    # load() (not cache-only get()) so the approval is always recorded — even if
    # the channel isn't currently being monitored (e.g. reviewing a clip after a
    # deploy or once the stream ended). A cache-miss here used to silently drop
    # the feedback, skewing learning toward rejections only.
    profile = await pm.load(clip["channel"])
    if profile:
        profile.record_clip(approved=True, signals=clip.get("trigger_signals", []))
        await pm.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    return clip


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
    training_log.log_outcome(clip, training_log.REJECTED)
    _delete_clip_file(clip)
    await broadcast({"event": "clip_removed", "clip_id": clip_id}, user_id=uid)
    pm      = get_profile_manager(uid)
    # load() (not cache-only get()) so the rejection is always recorded, matching
    # the approve path — see note there.
    profile = await pm.load(clip["channel"])
    if profile:
        profile.record_clip(approved=False, signals=clip.get("trigger_signals", []))
        await pm.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    return {"status": "deleted", "clip_id": clip_id}


class BulkCullBody(BaseModel):
    min_score: float = 50.0   # clips with score < this are removed


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
        for clip_id in to_remove:
            _delete_clip_file(_clips.pop(clip_id))
        if to_remove:
            _save_clips()

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
    stream_key = f"{uid}:{req.channel}"
    async with _data_lock:
        if stream_key in _streams:
            raise HTTPException(status_code=409, detail="Stream already registered")
        MAX_STREAMS_PER_USER = 10
        user_streams = [s for s in _streams.values() if s.get("user_id") == uid]
        if len(user_streams) >= MAX_STREAMS_PER_USER:
            raise HTTPException(
                status_code=429,
                detail=f"Stream limit reached ({MAX_STREAMS_PER_USER} max). Remove a stream to add a new one.",
            )
        if len(_streams) >= settings.max_concurrent_streams:
            raise HTTPException(status_code=503, detail="Server stream capacity reached. Try again later.")
        record = {
            "channel":         req.channel,
            "platform":        req.platform,
            "preset":          req.preset,
            "status":          "starting",
            "user_id":         uid,
        }
        _streams[stream_key] = record
        _save_streams()
    await broadcast({"event": "stream_added", "stream": record}, user_id=uid)
    if _publish_new_stream:
        await _publish_new_stream(req.channel, req.platform, req.preset, uid)
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
                        await _stop_user_streams_now(uid)
                        await broadcast({"event": "subscription_expired"}, user_id=uid)
                        continue
                    # Kill streams for any lapsed subscription
                    if status not in ("active", "trialing") and not db_user.get("is_admin"):
                        log.info("reaper_subscription_lapsed", user=uid, status=status)
                        await _stop_user_streams_now(uid)
                        await broadcast({"event": "subscription_expired"}, user_id=uid)
                        continue

                # Idle timeout — stop if no HTTP activity in 8 hours
                last_active = _user_last_active.get(uid, now)
                if now - last_active > _IDLE_STREAM_TIMEOUT:
                    log.info("idle_stream_reaper_stopping", user=uid,
                             idle_minutes=round((now - last_active) / 60))
                    await _stop_user_streams_now(uid)
                    await broadcast({"event": "streams_paused_idle"}, user_id=uid)
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
    uid = _current_user_id(request)
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
    if not _ws_is_admin and _ws_sub not in ("active", "trialing"):
        await ws.close(code=1008)
        return
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
    users = user_store.get_all()
    # Attach stream count per user
    for u in users:
        uid = u["id"]
        u["stream_count"] = sum(1 for s in _streams.values() if s.get("user_id") == uid)
        u["clip_count"] = sum(1 for c in _clips.values() if c.get("user_id") == uid)
    return users


@app.post("/admin/users/{user_id}/grant")
async def admin_grant_access(request: Request, user_id: str):
    _require_admin(request)
    from src.auth import users as user_store
    user_store.update_subscription(user_id, None, "active")
    # Realtime: the granted user's open tab should clear the paywall/banner live.
    await broadcast(
        {"event": "subscription_active",
         "message": "Access granted — your subscription is active."},
        user_id=user_id,
    )
    return {"ok": True}


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
    """Admin: most recent clips for a specific user (capped at 100)."""
    _require_admin(request)
    clips = [c for c in _clips.values() if c.get("user_id") == user_id]
    clips.sort(key=lambda c: c.get("created_at", 0), reverse=True)
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
async def login_page(error: str = ""):
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


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Authenticated users reach this only after passing the auth + billing gate
    # in AuthMiddleware, so they get the app. Everyone else sees the public
    # marketing landing page.
    if request.session.get("auth"):
        return HTMLResponse(content=DASHBOARD_HTML)
    return HTMLResponse(content=LANDING_HTML)


@app.get("/landing/stats")
async def landing_stats():
    """Public stats for the landing page (in _OPEN_PATHS — no auth).
    Exposes only an aggregate count; nothing user-identifying."""
    return {"clips_total": get_clip_counter()}


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz — Automatic Twitch clipping. $15/mo, 7 days free.</title>
<meta name="description" content="Highlightz watches your live Twitch streams and clips the best moments automatically using a transparent scoring formula — not AI. 7-day free trial, then $15/month.">
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Highlightz">
<meta property="og:url" content="https://highlightz.app/">
<meta property="og:title" content="Highlightz — Never miss a highlight again">
<meta property="og:description" content="Automatic Twitch clipping with a transparent formula — not AI. 7-day free trial, then $15/month.">
<meta property="og:image" content="https://highlightz.app/static/og-card.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Highlightz — Never miss a highlight again">
<meta name="twitter:description" content="Automatic Twitch clipping with a transparent formula — not AI. 7-day free trial, then $15/month.">
<meta name="twitter:image" content="https://highlightz.app/static/og-card.png">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.6;overflow-x:hidden}
  body::before{content:'';position:fixed;inset:0;z-index:-3;background:radial-gradient(900px 560px at 16% -10%,rgba(168,85,247,.24),transparent 60%),radial-gradient(760px 460px at 88% 2%,rgba(249,67,255,.14),transparent 55%),radial-gradient(760px 640px at 50% 112%,rgba(124,107,255,.13),transparent 60%)}
  body::after{content:'';position:fixed;inset:0;z-index:-2;pointer-events:none;background-image:radial-gradient(rgba(255,255,255,.045) 1px,transparent 1px);background-size:26px 26px;-webkit-mask-image:radial-gradient(900px 620px at 50% 0%,#000 0%,transparent 75%);mask-image:radial-gradient(900px 620px at 50% 0%,#000 0%,transparent 75%)}
  a{text-decoration:none;color:inherit}
  .acc{color:#c79bff}
  .grad-text{background:linear-gradient(135deg,#f943ff 0%,#a855f7 52%,#7c6bff 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
  /* Nav */
  .nav{position:sticky;top:0;z-index:50;display:flex;align-items:center;justify-content:space-between;padding:16px 30px;border-bottom:1px solid rgba(255,255,255,.06);background:rgba(8,8,11,.6);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px)}
  .nav-logo{display:flex;align-items:center;gap:11px}
  .nav-logo img{height:32px;filter:drop-shadow(0 0 9px rgba(199,155,255,.5))}
  .nav-logo span{font-size:17px;font-weight:800;color:#c79bff;letter-spacing:-.02em}
  .nav-actions{display:flex;align-items:center;gap:10px}
  .nav-link{font-size:13px;color:#9c9caa;font-weight:600;padding:9px 14px;border-radius:10px;transition:.15s;white-space:nowrap}
  .nav-link:hover{color:#f6f6f9;background:rgba(255,255,255,.05)}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;font-weight:700;cursor:pointer;border:none;transition:.16s;white-space:nowrap}
  .btn-grad{position:relative;overflow:hidden;background:linear-gradient(135deg,#f943ff 0%,#a855f7 52%,#7c6bff 100%);color:#fff;border-radius:12px;padding:11px 20px;font-size:14px;box-shadow:0 6px 24px -8px rgba(168,85,247,.7)}
  .btn-grad:hover{filter:brightness(1.09);transform:translateY(-1px)}
  .btn-grad::after{content:'';position:absolute;top:0;left:-70%;width:45%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.34),transparent);transform:skewX(-22deg)}
  .btn-ghost{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#f6f6f9;border-radius:12px;padding:12px 22px;font-size:14px}
  .btn-ghost:hover{background:rgba(255,255,255,.09)}
  .btn-lg{padding:15px 30px;font-size:15px;border-radius:13px}
  /* Layout */
  .wrap{max-width:1120px;margin:0 auto;padding:0 24px}
  section{padding:84px 0}
  .eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#c79bff;background:rgba(168,85,247,.1);border:1px solid rgba(168,85,247,.28);padding:7px 15px;border-radius:99px;margin-bottom:22px}
  .eyebrow .dot{width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e}
  h2.sec-title{font-size:34px;font-weight:800;letter-spacing:-.03em;margin-bottom:14px;line-height:1.15}
  .sec-sub{font-size:16px;color:#9c9caa;max-width:620px;line-height:1.65}
  /* Hero */
  .hero{display:grid;grid-template-columns:1.02fr .98fr;gap:52px;align-items:center;padding:74px 0 40px}
  .hero-copy h1{font-size:54px;font-weight:800;letter-spacing:-.04em;line-height:1.04;margin-bottom:20px}
  .hero-copy p.lead{font-size:18px;color:#b8b8c8;max-width:560px;margin-bottom:30px;line-height:1.6}
  .hero-ctas{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
  .hero-note{font-size:13px;color:#5d5d6b}
  .hero-note b{color:#9c9caa;font-weight:700}
  .pills{display:flex;gap:10px;flex-wrap:wrap;margin-top:30px}
  .pill{font-size:12.5px;font-weight:600;padding:7px 14px;border-radius:99px;background:rgba(168,85,247,.1);border:1px solid rgba(168,85,247,.28);color:#c79bff}
  /* ── LIVE DEMO ── */
  .demo-wrap{position:relative}
  .demo-wrap::before{content:'';position:absolute;inset:-36px -30px;background:radial-gradient(60% 60% at 50% 42%,rgba(168,85,247,.28),transparent 70%);filter:blur(14px);z-index:0}
  .demo{position:relative;z-index:1;border-radius:20px;padding:1px;background:linear-gradient(160deg,rgba(249,67,255,.55),rgba(255,255,255,.09) 30%,rgba(255,255,255,.07) 62%,rgba(124,107,255,.5));box-shadow:0 40px 90px -34px rgba(0,0,0,.85),0 0 70px -30px rgba(168,85,247,.55)}
  .demo-in{border-radius:19px;background:rgba(11,11,16,.96);overflow:hidden}
  .demo-bar{display:flex;align-items:center;gap:7px;padding:11px 15px;border-bottom:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.02)}
  .demo-bar i{width:10px;height:10px;border-radius:50%;display:block}
  .demo-bar .b1{background:#ff5f57}.demo-bar .b2{background:#febc2e}.demo-bar .b3{background:#28c840}
  .demo-bar span{margin-left:8px;font-size:11px;color:#5d5d6b;font-weight:600}
  .demo-live{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:10px;font-weight:800;letter-spacing:.06em;color:#ff5f6e;background:rgba(255,95,110,.1);border:1px solid rgba(255,95,110,.3);padding:4px 10px;border-radius:99px}
  .demo-live i{width:6px;height:6px;border-radius:50%;background:#ff5f6e;box-shadow:0 0 7px #ff5f6e;animation:blink 1.2s ease-in-out infinite}
  @keyframes blink{50%{opacity:.35}}
  .demo-body{padding:15px 15px 14px}
  .demo-main{min-width:0}
  .demo-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px}
  .demo-ch{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:800}
  .demo-ch .pd{width:7px;height:7px;border-radius:50%;background:#c79bff;box-shadow:0 0 8px #c79bff}
  .demo-score{font-size:26px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
  .demo-score small{font-size:10.5px;font-weight:700;color:#5d5d6b;letter-spacing:.06em;text-transform:uppercase;margin-right:8px;vertical-align:3px}
  .demo-chart{position:relative;border:1px solid rgba(255,255,255,.07);border-radius:12px;background:rgba(255,255,255,.018);overflow:hidden}
  .demo-chart svg{display:block;width:100%;height:auto}
  .fired-badge{position:absolute;top:9px;right:9px;display:inline-flex;align-items:center;gap:6px;font-size:10.5px;font-weight:800;letter-spacing:.05em;color:#fff;background:linear-gradient(135deg,#f943ff,#a855f7);padding:5px 11px;border-radius:99px;box-shadow:0 4px 18px -4px rgba(249,67,255,.8);opacity:0;transform:translateY(-6px);transition:opacity .25s,transform .25s}
  .fired-badge.on{opacity:1;transform:none}
  .demo.hot .demo-in{box-shadow:inset 0 0 0 1px rgba(249,67,255,.35),inset 0 0 44px -18px rgba(249,67,255,.4)}
  .demo-sigs{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
  .dsig{font-size:10px;font-weight:700;color:#5d5d6b;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);padding:4px 9px;border-radius:8px;transition:.25s;letter-spacing:.02em}
  .dsig.on{background:rgba(168,85,247,.2);color:#f6f6f9;border-color:rgba(168,85,247,.45)}
  /* demo clip card */
  .demo-clip{margin-top:11px;display:flex;align-items:center;gap:11px;border:1px solid rgba(46,224,138,.0);background:rgba(255,255,255,.03);border-radius:12px;padding:9px 11px;opacity:0;transform:translateY(10px);transition:opacity .4s,transform .4s,border-color .4s}
  .demo-clip.show{opacity:1;transform:none}
  .demo-clip.done{border-color:rgba(46,224,138,.35)}
  .dc-thumb{position:relative;width:64px;height:38px;border-radius:8px;flex-shrink:0;background:linear-gradient(135deg,#2a1840,#4a1a5d 52%,#1f1730);display:grid;place-items:center}
  .dc-thumb svg{opacity:.9}
  .dc-meta{flex:1;min-width:0}
  .dc-t{font-size:12.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .dc-s{font-size:11px;color:#9c9caa;margin-top:2px;display:flex;align-items:center;gap:6px}
  .dc-spin{width:10px;height:10px;border:2px solid rgba(255,255,255,.2);border-top-color:#c79bff;border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0}
  @keyframes spin{to{transform:rotate(360deg)}}
  .dc-ok{display:none;color:#2ee08a;font-weight:700}
  .demo-clip.done .dc-spin{display:none}
  .demo-clip.done .dc-ok{display:inline-flex;align-items:center;gap:5px}
  .demo-cap{text-align:center;font-size:12px;color:#5d5d6b;margin-top:14px;position:relative;z-index:1}
  /* Stats band */
  .stats-band{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:26px 0 0}
  .stat{padding:26px 20px;text-align:center}
  .stat .n{font-size:38px;font-weight:800;letter-spacing:-.03em;line-height:1.1;font-variant-numeric:tabular-nums}
  .stat .k{font-size:12.5px;color:#9c9caa;font-weight:600;margin-top:7px}
  .stat .live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#2ee08a;box-shadow:0 0 8px #2ee08a;margin-right:8px;vertical-align:4px}
  /* Glass card */
  .glass{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:20px;-webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px)}
  /* Who it's for */
  .who-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin-top:40px}
  .who-card{padding:26px 24px;transition:transform .25s,border-color .25s}
  .who-card:hover{transform:translateY(-3px);border-color:rgba(168,85,247,.3)}
  .who-card .ic{width:42px;height:42px;border-radius:12px;background:rgba(168,85,247,.14);color:#c79bff;display:grid;place-items:center;font-size:21px;margin-bottom:16px}
  .who-card h3{font-size:17px;font-weight:700;margin-bottom:7px}
  .who-card p{font-size:14px;color:#9c9caa;line-height:1.6}
  /* Steps */
  .steps{display:flex;flex-direction:column;gap:16px;margin-top:44px}
  .step{display:flex;gap:20px;align-items:flex-start;padding:24px 26px}
  .step-n{flex-shrink:0;width:44px;height:44px;border-radius:13px;background:linear-gradient(135deg,rgba(249,67,255,.18),rgba(124,107,255,.18));border:1px solid rgba(168,85,247,.3);color:#c79bff;display:grid;place-items:center;font-size:18px;font-weight:800}
  .step h3{font-size:18px;font-weight:700;margin-bottom:6px}
  .step p{font-size:14.5px;color:#9c9caa;line-height:1.65}
  /* Features */
  .feat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-top:44px}
  .feat{padding:26px 24px;transition:transform .25s,border-color .25s}
  .feat:hover{transform:translateY(-3px);border-color:rgba(168,85,247,.3)}
  .feat .ic{width:40px;height:40px;border-radius:11px;background:rgba(168,85,247,.13);color:#c79bff;display:grid;place-items:center;margin-bottom:15px}
  .feat h3{font-size:16px;font-weight:700;margin-bottom:7px}
  .feat p{font-size:14px;color:#9c9caa;line-height:1.6}
  /* Formula */
  .formula{padding:46px 44px;text-align:center}
  .formula h2{font-size:30px;font-weight:800;letter-spacing:-.025em;margin-bottom:14px}
  .formula p.lead{font-size:16px;color:#b8b8c8;max-width:680px;margin:0 auto 32px;line-height:1.65}
  .signal-row{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
  .signal{display:flex;align-items:center;gap:9px;font-size:14px;font-weight:600;padding:11px 18px;border-radius:13px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08)}
  .signal .sd{width:9px;height:9px;border-radius:50%}
  .plus{display:grid;place-items:center;color:#5d5d6b;font-size:18px;font-weight:700;padding:0 2px}
  .formula-eq{margin-top:30px;font-size:15px;color:#c79bff;font-weight:700;letter-spacing:.01em}
  /* Pricing */
  .price-card{position:relative;max-width:470px;margin:44px auto 0;padding:1px;border-radius:22px;background:linear-gradient(160deg,rgba(249,67,255,.5),rgba(255,255,255,.09) 35%,rgba(255,255,255,.08) 65%,rgba(124,107,255,.45))}
  .price-in{border-radius:21px;background:rgba(11,11,16,.94);padding:42px 40px;text-align:center}
  .price-badge{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#c79bff;background:rgba(199,155,255,.12);border:1px solid rgba(199,155,255,.25);padding:6px 13px;border-radius:99px;margin-bottom:22px}
  .price-amt{display:flex;align-items:baseline;justify-content:center;gap:6px;margin-bottom:4px}
  .price-amt .cur{font-size:26px;font-weight:800;color:#9c9caa;align-self:flex-start;margin-top:8px}
  .price-amt .num{font-size:64px;font-weight:800;letter-spacing:-.045em;line-height:1}
  .price-amt .per{font-size:16px;color:#9c9caa;font-weight:600}
  .price-trialline{display:inline-flex;align-items:center;gap:8px;font-size:14px;font-weight:700;color:#2ee08a;background:rgba(46,224,138,.1);border:1px solid rgba(46,224,138,.3);padding:8px 16px;border-radius:99px;margin:14px 0 10px}
  .price-sub{font-size:13.5px;color:#9c9caa;margin-bottom:26px;line-height:1.6}
  .price-list{text-align:left;display:flex;flex-direction:column;gap:12px;margin-bottom:30px}
  .price-list .li{display:flex;align-items:flex-start;gap:11px;font-size:14.5px;color:#d8d8e2}
  .price-list .ck{flex-shrink:0;width:20px;height:20px;border-radius:6px;background:rgba(52,211,153,.14);color:#34d399;display:grid;place-items:center;font-size:12px;font-weight:800;margin-top:1px}
  .price-promo{font-size:12.5px;color:#5d5d6b;margin-top:14px}
  .price-promo b{color:#c79bff}
  /* Final CTA */
  .final{text-align:center;padding:64px 0 90px}
  .final h2{font-size:38px;font-weight:800;letter-spacing:-.03em;margin-bottom:16px;line-height:1.1}
  .final p{font-size:17px;color:#9c9caa;max-width:540px;margin:0 auto 32px;line-height:1.6}
  /* Footer */
  .footer{border-top:1px solid rgba(255,255,255,.06);padding:36px 24px;text-align:center;font-size:12px;color:#3d3d4a;line-height:1.9}
  .footer a{color:#5d5d6b}.footer a:hover{color:#9c9caa}
  .footer .fl{margin-bottom:8px}
  /* ── Product showcase mockups ── */
  .shots-top{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:44px}
  .shot-frame{background:rgba(13,13,18,.72);border:1px solid rgba(255,255,255,.09);border-radius:16px;overflow:hidden;box-shadow:0 26px 64px -26px rgba(0,0,0,.75)}
  .shot-bar{display:flex;align-items:center;gap:7px;padding:11px 14px;border-bottom:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.02)}
  .shot-bar i{width:10px;height:10px;border-radius:50%;display:block}
  .shot-bar .b1{background:#ff5f57}.shot-bar .b2{background:#febc2e}.shot-bar .b3{background:#28c840}
  .shot-bar span{margin-left:8px;font-size:11px;color:#5d5d6b;font-weight:600}
  .shot-inner{padding:18px}
  .shot-cap{margin:16px 2px 0}
  .shot-cap h3{font-size:16px;font-weight:700;margin-bottom:5px}
  .shot-cap p{font-size:13.5px;color:#9c9caa;line-height:1.55}
  /* mock: stream bubble */
  .mk-stream{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:15px}
  .mk-row{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
  .mk-nm{font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px}
  .mk-nm .pd{width:7px;height:7px;border-radius:50%;background:#c79bff;box-shadow:0 0 8px #c79bff}
  .mk-mt{display:flex;gap:6px;align-items:center;margin-top:7px;flex-wrap:wrap}
  .mk-chip{font-size:10px;font-weight:600;color:#9c9caa;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);padding:3px 8px;border-radius:99px}
  .mk-live{font-size:10px;font-weight:700;color:#2ee08a}
  .mk-acts{display:flex;gap:6px;flex-shrink:0}
  .mk-clip{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;color:#c79bff;background:rgba(168,85,247,.14);border:1px solid rgba(168,85,247,.3);padding:5px 10px;border-radius:9px}
  .mk-x{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;color:#5d5d6b;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);font-size:13px}
  .mk-scorewrap{margin-top:15px}
  .mk-st{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
  .mk-sl{font-size:11px;font-weight:600;color:#9c9caa;letter-spacing:.04em;text-transform:uppercase}
  .mk-sval{font-size:22px;font-weight:800;letter-spacing:-.02em}
  .mk-track{height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
  .mk-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#a855f7,#f943ff)}
  .mk-sigs{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}
  .mk-sig{font-size:10.5px;font-weight:600;color:#9c9caa;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);padding:4px 9px;border-radius:8px}
  .mk-sig.on{background:rgba(168,85,247,.18);color:#f6f6f9;border-color:rgba(168,85,247,.3)}
  .mk-prof{margin-top:15px;border-top:1px solid rgba(255,255,255,.06);padding-top:14px}
  .mk-pgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
  .mk-pc .k{font-size:9.5px;color:#5d5d6b;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .mk-pc .v{font-size:15px;font-weight:700;margin-top:3px}
  .mk-pc .v small{font-size:9px;color:#5d5d6b;font-weight:500}
  .mk-cal{display:flex;align-items:center;gap:7px;margin-top:14px;font-size:11.5px;font-weight:600;color:#2ee08a}
  .mk-cal .ck{width:15px;height:15px;border-radius:5px;background:rgba(46,224,138,.16);display:grid;place-items:center;font-size:10px}
  /* mock: clip detail */
  .mk-media{position:relative;height:152px;border-radius:12px;overflow:hidden;display:grid;place-items:center;background:linear-gradient(135deg,#2a1840,#3a1a4d 52%,#1f1730)}
  .mk-play{width:52px;height:52px;border-radius:50%;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.3);display:grid;place-items:center}
  .mk-badge{position:absolute;top:11px;right:11px;display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;background:rgba(8,8,11,.72);border:1px solid rgba(255,255,255,.12);padding:5px 11px;border-radius:99px}
  .mk-badge .pip{width:7px;height:7px;border-radius:50%}
  .mk-dhead{display:flex;align-items:center;gap:11px;margin-top:16px}
  .mk-av{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#f943ff,#a855f7,#7c6bff);display:grid;place-items:center;font-size:13px;font-weight:800;color:#fff;flex-shrink:0}
  .mk-dhead h4{font-size:14px;font-weight:700}
  .mk-dhead .mt{font-size:11.5px;color:#9c9caa;margin-top:2px}
  .mk-status{font-size:10px;font-weight:700;text-transform:capitalize;padding:4px 10px;border-radius:99px;background:rgba(46,224,138,.14);border:1px solid rgba(46,224,138,.3);color:#2ee08a;flex-shrink:0}
  .mk-eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#c79bff;margin:18px 0 13px}
  .mk-bar{margin-bottom:13px}
  .mk-bh{display:flex;justify-content:space-between;margin-bottom:6px}
  .mk-bk{font-size:12.5px;color:#d8d8e2;font-weight:500}
  .mk-bv{font-size:12.5px;font-weight:700}
  .mk-bt{height:7px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
  .mk-bf{height:100%;border-radius:99px;background:linear-gradient(90deg,#a855f7,#f943ff)}
  /* mock: clip library */
  .mk-lib{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
  .mk-card{border:1px solid rgba(255,255,255,.08);border-radius:13px;overflow:hidden;background:rgba(255,255,255,.025)}
  .mk-cmedia{position:relative;height:100px;display:grid;place-items:center}
  .mk-cbadge{position:absolute;top:9px;left:9px;font-size:10px;font-weight:700;background:rgba(8,8,11,.68);border:1px solid rgba(255,255,255,.12);padding:3px 9px;border-radius:99px}
  .mk-cplay{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.28);display:grid;place-items:center}
  .mk-cbody{padding:11px 12px 13px}
  .mk-ctitle{font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mk-cmeta{font-size:11px;color:#9c9caa;margin-top:5px;display:flex;justify-content:space-between;align-items:center}
  .mk-cpill{font-size:9.5px;font-weight:700;padding:3px 8px;border-radius:99px;text-transform:capitalize}
  .mk-cpill.ok{background:rgba(46,224,138,.14);border:1px solid rgba(46,224,138,.3);color:#2ee08a}
  .mk-cpill.pend{background:rgba(255,194,92,.14);border:1px solid rgba(255,194,92,.3);color:#ffc25c}
  @media(max-width:960px){
    .hero{grid-template-columns:1fr;gap:40px;padding-top:56px}
    .hero-copy{text-align:center}
    .hero-copy p.lead{margin-left:auto;margin-right:auto}
    .hero-ctas,.pills{justify-content:center}
  }
  @media(max-width:680px){
    .shots-top{grid-template-columns:1fr}
    .hero-copy h1{font-size:38px}
    .hero-copy p.lead{font-size:16.5px}
    h2.sec-title{font-size:27px}
    .final h2{font-size:29px}
    section{padding:60px 0}
    .nav{padding:14px 14px}
    .nav-actions{gap:4px}
    .nav-logo span{display:none}
    .nav .nav-link.hide-sm{display:none}
    .formula{padding:34px 22px}
    .stats-band{grid-template-columns:1fr;gap:12px}
    .stat{padding:20px}
    .stat .n{font-size:32px}
    .price-amt .num{font-size:54px}
  }
  /* Scroll-reveal + hero intro (motion-safe only) */
  @media(prefers-reduced-motion:no-preference){
    .reveal{opacity:0;transform:translateY(26px);transition:opacity .7s cubic-bezier(.16,1,.3,1),transform .7s cubic-bezier(.16,1,.3,1)}
    .reveal.in{opacity:1;transform:none}
    .hero .eyebrow,.hero-copy h1,.hero-copy .lead,.hero-ctas,.hero-note,.pills{opacity:0;animation:heroIn .8s cubic-bezier(.16,1,.3,1) forwards}
    .hero-copy h1{animation-delay:.07s}
    .hero-copy .lead{animation-delay:.15s}
    .hero-ctas{animation-delay:.23s}
    .hero-note{animation-delay:.31s}
    .pills{animation-delay:.39s}
    .demo-wrap{opacity:0;animation:heroIn .9s cubic-bezier(.16,1,.3,1) .2s forwards}
    @keyframes heroIn{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:none}}
  }
</style>
</head>
<body>
<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo.jpg" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-actions">
    <a href="#how" class="nav-link hide-sm">How it works</a>
    <a href="#features" class="nav-link hide-sm">Features</a>
    <a href="#pricing" class="nav-link hide-sm">Pricing</a>
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-grad">Start free</a>
  </div>
</nav>

<!-- Hero -->
<header class="wrap hero">
  <div class="hero-copy">
    <div class="eyebrow"><span class="dot"></span>Automatic clipping for Twitch</div>
    <h1>Never miss a <span class="grad-text">highlight</span> again.</h1>
    <p class="lead">Highlightz watches your live stream in real time and clips your best moments automatically — whether 5 people are watching or 50,000. Seconds later the clip is on Twitch, ready for you to approve.</p>
    <div class="hero-ctas">
      <a href="/login" class="btn btn-grad btn-lg">Start your 7-day free trial</a>
      <a href="#how" class="btn btn-ghost btn-lg">See how it works</a>
    </div>
    <p class="hero-note">7 days free &middot; then <b>$15/month</b> &middot; cancel anytime</p>
    <div class="pills">
      <span class="pill">Formula-based — not AI</span>
      <span class="pill">Works at any channel size</span>
      <span class="pill">Multiple streams at once</span>
    </div>
  </div>

  <!-- LIVE DEMO -->
  <div class="demo-wrap">
    <div class="demo" id="demo">
      <div class="demo-in">
        <div class="demo-bar"><i class="b1"></i><i class="b2"></i><i class="b3"></i><span>Highlightz — watching your stream</span><span class="demo-live"><i></i>LIVE</span></div>
        <div class="demo-body">
          <div class="demo-main">
            <div class="demo-head">
              <div class="demo-ch"><span class="pd"></span>your stream</div>
              <div class="demo-score"><small>Trigger score</small><span id="d-score">92</span></div>
            </div>
            <div class="demo-chart">
              <svg viewBox="0 0 520 150" preserveAspectRatio="none" aria-label="Live trigger score chart">
                <defs>
                  <linearGradient id="dArea" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#a855f7" stop-opacity=".34"/>
                    <stop offset="100%" stop-color="#a855f7" stop-opacity="0"/>
                  </linearGradient>
                </defs>
                <line x1="0" y1="42" x2="520" y2="42" stroke="rgba(255,255,255,.05)" stroke-width="1"/>
                <line x1="0" y1="104" x2="520" y2="104" stroke="rgba(255,255,255,.05)" stroke-width="1"/>
                <line id="d-thresh" x1="0" y1="64.8" x2="520" y2="64.8" stroke="#5d5d6b" stroke-width="1" stroke-dasharray="5 5"/>
                <text x="8" y="60" font-size="9" fill="#5d5d6b" font-family="Inter,system-ui,sans-serif" font-weight="600">trigger threshold</text>
                <path id="d-area" d="M0,112 L20,109 L40,113 L60,108 L80,111 L100,106 L120,112 L140,107 L160,110 L180,104 L200,109 L220,105 L240,110 L260,103 L280,108 L300,101 L320,97 L340,80 L355,62 L370,44 L385,28 L400,20 L415,17 L430,19 L445,26 L460,38 L475,52 L490,62 L505,68 L520,71 L520,150 L0,150 Z" fill="url(#dArea)"/>
                <path id="d-line" d="M0,112 L20,109 L40,113 L60,108 L80,111 L100,106 L120,112 L140,107 L160,110 L180,104 L200,109 L220,105 L240,110 L260,103 L280,108 L300,101 L320,97 L340,80 L355,62 L370,44 L385,28 L400,20 L415,17 L430,19 L445,26 L460,38 L475,52 L490,62 L505,68 L520,71" fill="none" stroke="#a855f7" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
                <circle id="d-dot" cx="415" cy="17" r="4.5" fill="#f943ff" stroke="#0b0b10" stroke-width="2"/>
              </svg>
              <span class="fired-badge on" id="d-badge"><svg width="11" height="11" viewBox="0 0 24 24" fill="#fff"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>TRIGGER FIRED</span>
            </div>
            <div class="demo-sigs">
              <span class="dsig on" id="sig-chat">CHAT VELOCITY</span>
              <span class="dsig on" id="sig-audio">AUDIO SPIKE</span>
              <span class="dsig on" id="sig-kw">KEYWORDS</span>
              <span class="dsig" id="sig-sent">SENTIMENT</span>
            </div>
            <div class="demo-clip show done" id="d-clip">
              <div class="dc-thumb"><svg width="15" height="15" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div>
              <div class="dc-meta">
                <div class="dc-t">Big Reaction — clipped automatically</div>
                <div class="dc-s"><span class="dc-spin"></span><span id="d-clipmsg">Creating clip on Twitch&hellip;</span><span class="dc-ok">&#10003; Clip ready</span></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="demo-cap">Live demo — this is what a capture looks like, start to finish.</div>
  </div>
</header>

<!-- Stats band -->
<div class="wrap">
  <div class="stats-band">
    <div class="glass stat" id="stat-clips" style="display:none">
      <div class="n"><span class="live-dot"></span><span id="lp-count">0</span></div>
      <div class="k">clips captured and counting</div>
    </div>
    <div class="glass stat">
      <div class="n">7</div>
      <div class="k">live signals blended into every score</div>
    </div>
    <div class="glass stat">
      <div class="n">1s</div>
      <div class="k">every second of your stream is scored</div>
    </div>
  </div>
</div>

<!-- Who it's for -->
<section class="wrap" id="who">
  <h2 class="sec-title">Built for anyone who clips</h2>
  <p class="sec-sub">Whether you're growing your own channel or clipping for others, Highlightz does the watching so you can focus on the content.</p>
  <div class="who-grid">
    <div class="glass who-card">
      <div class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/><path d="M8 21h8"/></svg></div>
      <h3>Streamers</h3>
      <p>Capture your funniest, hypest, and most viral moments live — no mod team or clip-happy chat required. You play, it clips.</p>
    </div>
    <div class="glass who-card">
      <div class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.12 15.88"/><path d="M14.47 14.48L20 20"/><path d="M8.12 8.12L12 12"/></svg></div>
      <h3>Clippers &amp; editors</h3>
      <p>Monitor several channels at once and let the best moments surface themselves. Spend your time editing, not scrubbing VODs.</p>
    </div>
    <div class="glass who-card">
      <div class="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg></div>
      <h3>Community &amp; mod teams</h3>
      <p>Keep a steady feed of share-ready clips for socials and Discord, pulled straight from the action as it happens.</p>
    </div>
  </div>
</section>

<!-- How it works -->
<section class="wrap" id="how">
  <h2 class="sec-title">How it works</h2>
  <p class="sec-sub">Five simple steps from "go live" to a polished clip in your review queue.</p>
  <div class="steps">
    <div class="glass step">
      <div class="step-n">1</div>
      <div><h3>Add any live Twitch channel</h3><p>Your own channel, streamers you clip for, or anyone live right now — monitor multiple streams at the same time from one dashboard.</p></div>
    </div>
    <div class="glass step">
      <div class="step-n">2</div>
      <div><h3>A formula scores every second — not AI</h3><p>Highlightz uses a transparent mathematical formula that combines chat speed, audio spikes, keywords, viewer surges, and hype moments into one live score. No AI, no black box — you can watch the score move in real time as the stream unfolds.</p></div>
    </div>
    <div class="glass step">
      <div class="step-n">3</div>
      <div><h3>It adapts to every streamer</h3><p>The formula learns each channel's normal — a quiet chess stream and a loud FPS stream trigger with the same fairness. The more it watches a channel, the sharper its judgment becomes.</p></div>
    </div>
    <div class="glass step">
      <div class="step-n">4</div>
      <div><h3>Clips are created right on Twitch</h3><p>Fully connected to your Twitch account. When the score crosses the threshold, a real Twitch clip is created instantly under your account — hosted by Twitch and ready to share. Highlightz never records or re-hosts video.</p></div>
    </div>
    <div class="glass step">
      <div class="step-n">5</div>
      <div><h3>You stay in control</h3><p>Every clip lands in your review queue. Approve the keepers, reject the misses — and the formula quietly tunes itself to your taste with every decision you make.</p></div>
    </div>
  </div>
</section>

<!-- Product showcase -->
<section class="wrap" id="product">
  <h2 class="sec-title">See it in action</h2>
  <p class="sec-sub">A clean, real-time dashboard that shows you exactly what's happening and why.</p>
  <div class="shots-top">
    <!-- Active stream bubble -->
    <div class="shot">
      <div class="shot-frame">
        <div class="shot-bar"><i class="b1"></i><i class="b2"></i><i class="b3"></i><span>Monitoring</span></div>
        <div class="shot-inner">
          <div class="mk-stream">
            <div class="mk-row">
              <div>
                <div class="mk-nm"><span class="pd"></span>novafps</div>
                <div class="mk-mt"><span class="mk-chip" style="color:#c79bff;border-color:rgba(168,85,247,.3)">twitch</span><span class="mk-chip">fps</span><span class="mk-live">live</span></div>
              </div>
              <div class="mk-acts">
                <span class="mk-clip"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>Clip</span>
                <span class="mk-x">&#215;</span>
              </div>
            </div>
            <div class="mk-scorewrap">
              <div class="mk-st"><span class="mk-sl">Trigger score</span><span class="mk-sval" style="color:#ffb03a">72.4</span></div>
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
                <div class="mk-pc"><div class="k">Approval</div><div class="v" style="color:#2ee08a">78%</div></div>
              </div>
              <div class="mk-cal"><span class="ck">&#10003;</span>Calibrated &middot; 142 samples</div>
            </div>
          </div>
        </div>
      </div>
      <div class="shot-cap"><h3>Live stream monitoring</h3><p>Each channel gets its own bubble with a live trigger score, the exact signals firing, and a learned profile that calibrates to that streamer.</p></div>
    </div>

    <!-- Clip score breakdown -->
    <div class="shot">
      <div class="shot-frame">
        <div class="shot-bar"><i class="b1"></i><i class="b2"></i><i class="b3"></i><span>Clip detail</span></div>
        <div class="shot-inner">
          <div class="mk-media">
            <div class="mk-play"><svg width="20" height="20" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div>
            <span class="mk-badge"><span class="pip" style="background:#2ee08a"></span>84% trigger</span>
          </div>
          <div class="mk-dhead">
            <span class="mk-av">NF</span>
            <div style="flex:1"><h4>Insane 1v5 clutch to win it</h4><div class="mt">novafps &middot; VALORANT &middot; 2m ago</div></div>
            <span class="mk-status">approved</span>
          </div>
          <div class="mk-eyebrow">Why it fired</div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Chat velocity</span><span class="mk-bv" style="color:#2ee08a">88%</span></div><div class="mk-bt"><div class="mk-bf" style="width:88%"></div></div></div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Audio spike</span><span class="mk-bv" style="color:#ffb03a">73%</span></div><div class="mk-bt"><div class="mk-bf" style="width:73%"></div></div></div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Keyword hits</span><span class="mk-bv" style="color:#ffb03a">61%</span></div><div class="mk-bt"><div class="mk-bf" style="width:61%"></div></div></div>
          <div class="mk-bar"><div class="mk-bh"><span class="mk-bk">Sentiment</span><span class="mk-bv" style="color:#9c9caa">47%</span></div><div class="mk-bt"><div class="mk-bf" style="width:47%"></div></div></div>
        </div>
      </div>
      <div class="shot-cap"><h3>Every clip, fully explained</h3><p>Open any clip to see the precise breakdown of which signals triggered it — no AI guesswork, just the numbers behind the moment.</p></div>
    </div>
  </div>

  <!-- Clip library -->
  <div class="shot" style="margin-top:20px">
    <div class="shot-frame">
      <div class="shot-bar"><i class="b1"></i><i class="b2"></i><i class="b3"></i><span>Clip library</span></div>
      <div class="shot-inner">
        <div class="mk-lib">
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(135deg,#2a1840,#3a1a4d)"><div class="mk-cplay"><svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#2ee08a">96%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Triple kill, no scope</div><div class="mk-cmeta"><span>novafps</span><span class="mk-cpill ok">approved</span></div></div>
          </div>
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(135deg,#3a1a4d,#1f1730)"><div class="mk-cplay"><svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#ffc25c">81%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Unreal comeback round</div><div class="mk-cmeta"><span>peachy_kat</span><span class="mk-cpill pend">pending</span></div></div>
          </div>
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(135deg,#1f2a4d,#2a1840)"><div class="mk-cplay"><svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#2ee08a">74%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Clutch ace, full team</div><div class="mk-cmeta"><span>drift_season</span><span class="mk-cpill ok">approved</span></div></div>
          </div>
          <div class="mk-card">
            <div class="mk-cmedia" style="background:linear-gradient(135deg,#33184a,#1a2440)"><div class="mk-cplay"><svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div><span class="mk-cbadge" style="color:#ffc25c">68%</span></div>
            <div class="mk-cbody"><div class="mk-ctitle">Perfect comedic timing</div><div class="mk-cmeta"><span>moonvale</span><span class="mk-cpill pend">pending</span></div></div>
          </div>
        </div>
      </div>
    </div>
    <div class="shot-cap"><h3>Your clip library</h3><p>Every captured moment in one place, scored and ready to review. Approve the keepers, reject the rest — the formula learns from every call.</p></div>
  </div>
</section>

<!-- Not AI / formula -->
<section class="wrap">
  <div class="glass formula">
    <div class="eyebrow" style="margin-bottom:18px">100% transparent</div>
    <h2>A formula you can actually understand</h2>
    <p class="lead">Highlightz isn't powered by AI guesswork. It's a clear, explainable formula that blends real signals from the stream into a single live score. Every trigger has a reason you can see.</p>
    <div class="signal-row">
      <div class="signal"><span class="sd" style="background:#f943ff"></span>Chat speed</div>
      <div class="plus">+</div>
      <div class="signal"><span class="sd" style="background:#a855f7"></span>Audio spikes</div>
      <div class="plus">+</div>
      <div class="signal"><span class="sd" style="background:#7c6bff"></span>Keywords</div>
      <div class="plus">+</div>
      <div class="signal"><span class="sd" style="background:#22c55e"></span>Viewer surges</div>
      <div class="plus">+</div>
      <div class="signal"><span class="sd" style="background:#ffcc00"></span>Hype moments</div>
    </div>
    <div class="formula-eq">= one live highlight score, adapting to each streamer</div>
  </div>
</section>

<!-- Features -->
<section class="wrap" id="features">
  <h2 class="sec-title">Everything in the box</h2>
  <p class="sec-sub">A complete clipping toolkit that runs while you do everything else.</p>
  <div class="feat-grid">
    <div class="glass feat">
      <div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></div>
      <h3>Real-time detection</h3>
      <p>Streams are scored second by second, so highlights are caught the instant they happen — not hours later in a VOD.</p>
    </div>
    <div class="glass feat">
      <div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></div>
      <h3>Multiple streams at once</h3>
      <p>Watch many channels simultaneously from one dashboard, each with its own independent learning profile.</p>
    </div>
    <div class="glass feat">
      <div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z"/><path d="M12 7v5l3 3"/></svg></div>
      <h3>Adaptive per-channel learning</h3>
      <p>Each streamer gets a personalized baseline. Loud or quiet, fast chat or slow — it's always calibrated fairly.</p>
    </div>
    <div class="glass feat">
      <div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12l5 5L20 7"/></svg></div>
      <h3>Approve / reject queue</h3>
      <p>Review every clip in one place. Your decisions feed straight back into the formula, sharpening it over time.</p>
    </div>
    <div class="glass feat">
      <div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg></div>
      <h3>Live score analytics</h3>
      <p>Watch the highlight score rise and fall in real time, with a clear breakdown of which signals are firing.</p>
    </div>
    <div class="glass feat">
      <div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4z"/></svg></div>
      <h3>Real Twitch clips, zero risk</h3>
      <p>Every clip is created through Twitch's official API under your own account — hosted by Twitch, never recorded or re-hosted by us.</p>
    </div>
  </div>
</section>

<!-- Pricing -->
<section class="wrap" id="pricing">
  <h2 class="sec-title" style="text-align:center">Simple, honest pricing</h2>
  <p class="sec-sub" style="margin:0 auto;text-align:center">One plan, everything included. Try it free for a week — cancel anytime before the trial ends and you won't be charged.</p>
  <div class="price-card">
    <div class="price-in">
      <span class="price-badge">Highlightz Pro</span>
      <div class="price-amt"><span class="cur">$</span><span class="num">15</span><span class="per">/month</span></div>
      <div class="price-trialline">First 7 days free</div>
      <div class="price-sub">Full access from day one. Auto-renews monthly after the trial &middot; cancel anytime.</div>
      <div class="price-list">
        <div class="li"><span class="ck">&#10003;</span>Automatic clip detection on any live channel</div>
        <div class="li"><span class="ck">&#10003;</span>Monitor multiple streams at the same time</div>
        <div class="li"><span class="ck">&#10003;</span>Adaptive, per-channel scoring formula</div>
        <div class="li"><span class="ck">&#10003;</span>Live trigger-score analytics</div>
        <div class="li"><span class="ck">&#10003;</span>Clip review queue with approve / reject</div>
        <div class="li"><span class="ck">&#10003;</span>VOD scanner — find highlights in past broadcasts</div>
        <div class="li"><span class="ck">&#10003;</span>Clips created instantly on Twitch under your account</div>
      </div>
      <a href="/login" class="btn btn-grad" style="width:100%;padding:14px;font-size:15px">Start free trial &#8594;</a>
      <div class="price-promo">Have a promo code? Enter it at checkout for <b>50% off your first month</b>.</div>
    </div>
  </div>
</section>

<!-- Final CTA -->
<section class="wrap final">
  <h2>Your next viral clip is<br><span class="grad-text">already happening.</span></h2>
  <p>Connect your Twitch account and add your first channel — Highlightz catches every highlight automatically, from the very first stream.</p>
  <a href="/login" class="btn btn-grad btn-lg">Start your 7-day free trial</a>
</section>

<footer class="footer">
  <div class="fl">&copy; 2026 ANTI Technology LLC &mdash; All rights reserved.</div>
  <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a> &middot; <a href="/opt-out">Streamer Opt-Out</a>
</footer>
<script>
(function(){
  if(!('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var sel='.sec-title,.sec-sub,.who-card,.step,.feat,.formula,.price-card,.shot,.stat,.final h2,.final p,.final .btn';
  var els=[].slice.call(document.querySelectorAll(sel));
  els.forEach(function(el){el.classList.add('reveal');});
  document.querySelectorAll('.who-grid,.steps,.feat-grid,.stats-band').forEach(function(group){
    [].slice.call(group.children).forEach(function(child,i){
      if(child.classList.contains('reveal')) child.style.transitionDelay=(i*0.08)+'s';
    });
  });
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);}
    });
  },{threshold:0.12,rootMargin:'0px 0px -8% 0px'});
  els.forEach(function(el){io.observe(el);});
})();

/* ── Live clips counter ── */
(function(){
  var tile=document.getElementById('stat-clips'), el=document.getElementById('lp-count');
  if(!tile||!el) return;
  fetch('/landing/stats').then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(!d||typeof d.clips_total!=='number'||d.clips_total<=0) return;
    tile.style.display='';
    var target=d.clips_total;
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){
      el.textContent=target.toLocaleString('en-US'); return;
    }
    var started=null;
    function tick(ts){
      if(started===null) started=ts;
      var p=Math.min((ts-started)/1400,1);
      var eased=1-Math.pow(1-p,3);
      el.textContent=Math.round(target*eased).toLocaleString('en-US');
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

/* ── Hero live-capture demo ──
   A scripted ~11s loop: calm chat + wandering score → chat floods → score
   spikes past the threshold → TRIGGER badge + window glow → a clip card slides
   in, spins, flips to "Clip ready". The markup ships the fired end-state, so
   no-JS and reduced-motion visitors see the finished capture instead of motion. */
(function(){
  var reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var demo=document.getElementById('demo');
  var line=document.getElementById('d-line'), area=document.getElementById('d-area');
  var dot=document.getElementById('d-dot'), badge=document.getElementById('d-badge');
  var scoreEl=document.getElementById('d-score'), clipCard=document.getElementById('d-clip');
  var sigChat=document.getElementById('sig-chat'), sigAudio=document.getElementById('sig-audio'),
      sigKw=document.getElementById('sig-kw'), sigSent=document.getElementById('sig-sent');
  if(!demo||!line||reduce) return;   // static fired-state markup stays as-is

  var DUR=11000, WINDOW=8000, W=520, H=150, THRESH=60;

  function yFor(s){ return 144 - s*1.32; }

  var samples=[], lastSample=0, fired=false, clipShown=false, clipDone=false, seed=7;
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
    clipCard.classList.remove('show','done');
    dot.setAttribute('r','0');
  }
  reset();

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
      scoreEl.textContent=String(Math.round(s));

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

      if(!fired&&s>=THRESH&&t>4000){
        fired=true;
        badge.classList.add('on'); demo.classList.add('hot');
        dot.setAttribute('r','4.5');
      }
      if(fired&&!clipShown&&t>=7100){
        clipShown=true; clipCard.classList.add('show');
      }
      if(clipShown&&!clipDone&&t>=8700){
        clipDone=true; clipCard.classList.add('done');
      }
      if(t>=10300){ badge.classList.remove('on'); demo.classList.remove('hot'); }
    }
    setSigs(t);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
</script>
</body>
</html>"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz - Sign In</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:24px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:44px 40px;width:360px;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:22px}
  .logo-wrap img{height:80px;width:auto;filter:drop-shadow(0 0 18px rgba(199,155,255,.5))}
  h1{font-size:26px;font-weight:800;color:#c79bff;margin-bottom:4px;letter-spacing:-.02em}
  .sub{font-size:13px;color:#9c9caa;margin-bottom:18px}
  .trial-pill{display:inline-flex;align-items:center;gap:7px;background:rgba(145,70,255,.14);border:1px solid rgba(145,70,255,.35);color:#c79bff;font-size:12px;font-weight:700;padding:8px 14px;border-radius:99px;margin-bottom:22px}
  .trial-pill .dot{width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e}
  .trial-note{font-size:11px;color:#5d5d6b;text-align:center;margin-top:12px}
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
  <div class="logo-wrap"><img src="/static/logo.jpg" alt="Highlightz logo"></div>
  <h1>Highlightz</h1>
  <p class="sub">Sign in to start clipping highlights</p>
  <div class="trial-pill"><span class="dot"></span>7-day free trial — cancel anytime</div>
  {error}
  <a href="/auth/twitch" class="twitch-btn">
    <svg width="20" height="20" viewBox="0 0 2400 2800" fill="#fff"><path d="M500 0L0 500v1800h600v500l500-500h400l900-900V0H500zm1700 1300l-400 400h-400l-350 350v-350H600V200h1600v1100z"/><path d="M1700 550h-200v600h200V550zm-550 0h-200v600h200V550z"/></svg>
    Continue with Twitch
  </a>
  <p class="trial-note">Free for 7 days, then auto-renews monthly. Cancel anytime before it ends and you won't be charged.</p>
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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz - Subscribe</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:20px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:48px 44px;max-width:440px;width:100%;text-align:center;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:20px}
  .logo-wrap img{height:64px;filter:drop-shadow(0 0 14px rgba(199,155,255,.5))}
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
  <div class="logo-wrap"><img src="/static/logo.jpg" alt="Highlightz"></div>
  <span class="badge">Highlightz Pro</span>
  <h1>{headline}</h1>
  <p class="sub">Hi {username} — {subline}</p>
  <div class="features">
    <div class="feat"><span class="ic">›</span>Automatic clip detection on any live channel</div>
    <div class="feat"><span class="ic">›</span>Live trigger score analytics</div>
    <div class="feat"><span class="ic">›</span>Instant clips created on Twitch under your account</div>
    <div class="feat"><span class="ic">›</span>Clip review queue with approve / reject</div>
    <div class="feat"><span class="ic">›</span>Per-channel AI learning baseline</div>
  </div>
  <a href="/billing/checkout" class="cta">{cta_label}</a>
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.7;padding:0 0 80px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.15),transparent 60%)}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .back{display:inline-flex;align-items:center;gap:8px;color:#5d5d6b;font-size:13px;text-decoration:none;margin-bottom:40px;transition:.15s}
  .back:hover{color:#c79bff}
  .logo{display:flex;align-items:center;gap:14px;margin-bottom:32px}
  .logo img{height:40px;filter:drop-shadow(0 0 10px rgba(199,155,255,.5))}
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
    <img src="/static/logo.jpg" alt="Highlightz">
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
  <p>New accounts may start a 7-day free trial with full access to core features. A valid payment method is required to begin the trial. Unless you cancel before the trial ends, your subscription will automatically convert to a paid plan and the payment method on file will be charged the then-current subscription fee, and on each renewal period thereafter, until you cancel. You can cancel at any time — including during the trial to avoid being charged — through the billing portal. The free trial is limited to one per person; accounts that have previously used a trial will be billed immediately upon subscribing.</p>
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.7;padding:0 0 80px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.15),transparent 60%)}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .back{display:inline-flex;align-items:center;gap:8px;color:#5d5d6b;font-size:13px;text-decoration:none;margin-bottom:40px;transition:.15s}
  .back:hover{color:#c79bff}
  .logo{display:flex;align-items:center;gap:14px;margin-bottom:32px}
  .logo img{height:40px;filter:drop-shadow(0 0 10px rgba(199,155,255,.5))}
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
    <img src="/static/logo.jpg" alt="Highlightz">
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;line-height:1.7;padding:0 0 80px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.15),transparent 60%)}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .back{display:inline-flex;align-items:center;gap:8px;color:#5d5d6b;font-size:13px;text-decoration:none;margin-bottom:40px;transition:.15s}
  .back:hover{color:#c79bff}
  .logo{display:flex;align-items:center;gap:14px;margin-bottom:32px}
  .logo img{height:40px;filter:drop-shadow(0 0 10px rgba(199,155,255,.5))}
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
    <img src="/static/logo.jpg" alt="Highlightz">
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
<title>Admin — Highlightz</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;min-height:100vh}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.13),transparent 60%)}
  .topbar{display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.02);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);position:sticky;top:0;z-index:10}
  .logo{display:flex;align-items:center;gap:12px}
  .logo img{height:30px;filter:drop-shadow(0 0 8px rgba(199,155,255,.5))}
  .logo span{font-size:16px;font-weight:800;color:#c79bff;letter-spacing:-.02em}
  .badge{background:rgba(249,67,255,.15);border:1px solid rgba(249,67,255,.3);color:#f943ff;font-size:10px;font-weight:700;padding:3px 9px;border-radius:99px;letter-spacing:.08em;text-transform:uppercase}
  .nav-link{font-size:13px;color:#5d5d6b;text-decoration:none;transition:.15s}
  .nav-link:hover{color:#c79bff}
  .wrap{max-width:1100px;margin:0 auto;padding:36px 24px}
  h1{font-size:26px;font-weight:800;letter-spacing:-.03em;margin-bottom:6px}
  .meta{font-size:13px;color:#5d5d6b;margin-bottom:32px}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:36px}
  .stat{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:20px;text-align:center}
  .stat-val{font-size:32px;font-weight:800;letter-spacing:-.03em;color:#c79bff}
  .stat-lbl{font-size:12px;color:#5d5d6b;margin-top:4px}
  .section{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:18px;overflow:hidden;margin-bottom:28px}
  .section-head{padding:18px 22px;border-bottom:1px solid rgba(255,255,255,.06);font-size:13px;font-weight:700;color:#c79bff;letter-spacing:.04em;text-transform:uppercase}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-size:11px;font-weight:700;color:#5d5d6b;text-transform:uppercase;letter-spacing:.06em;padding:12px 18px;border-bottom:1px solid rgba(255,255,255,.05)}
  td{padding:13px 18px;font-size:13px;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}
  tr:last-child td{border-bottom:none}
  .avatar{width:30px;height:30px;border-radius:50%;object-fit:cover;background:rgba(199,155,255,.15)}
  .avatar-wrap{display:flex;align-items:center;gap:10px}
  .username{font-weight:600}
  .account-id{font-size:11px;color:#5d5d6b;margin-top:2px;font-family:monospace}
  .pill{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;padding:3px 10px;border-radius:99px;letter-spacing:.04em}
  .pill-active{background:rgba(52,211,153,.12);border:1px solid rgba(52,211,153,.25);color:#34d399}
  .pill-inactive{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2);color:#f87171}
  .pill-none{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);color:#9c9caa}
  .pill-admin{background:rgba(249,67,255,.12);border:1px solid rgba(249,67,255,.25);color:#f943ff}
  .actions{display:flex;gap:8px;flex-wrap:wrap}
  .btn{font-size:12px;font-weight:600;padding:5px 12px;border-radius:8px;border:none;cursor:pointer;transition:.15s}
  .btn-grant{background:rgba(52,211,153,.15);color:#34d399;border:1px solid rgba(52,211,153,.25)}
  .btn-grant:hover{background:rgba(52,211,153,.25)}
  .btn-revoke{background:rgba(239,68,68,.12);color:#f87171;border:1px solid rgba(239,68,68,.2)}
  .btn-revoke:hover{background:rgba(239,68,68,.22)}
  .btn-delete{background:rgba(239,68,68,.08);color:#f87171;border:1px solid rgba(239,68,68,.15)}
  .btn-delete:hover{background:rgba(239,68,68,.18)}
  .btn-details{background:rgba(199,155,255,.1);color:#c79bff;border:1px solid rgba(199,155,255,.2)}
  .btn-details:hover{background:rgba(199,155,255,.2)}
  .empty{padding:40px;text-align:center;color:#5d5d6b;font-size:13px}
  .loading{padding:40px;text-align:center;color:#5d5d6b;font-size:13px}
  .toast{position:fixed;bottom:24px;right:24px;background:rgba(30,30,40,.95);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:12px 20px;font-size:13px;font-weight:600;box-shadow:0 8px 32px rgba(0,0,0,.4);opacity:0;transform:translateY(8px);transition:.25s;pointer-events:none;z-index:999}
  .toast.show{opacity:1;transform:none}
  .toast.ok{border-color:rgba(52,211,153,.4);color:#34d399}
  .toast.err{border-color:rgba(239,68,68,.4);color:#f87171}
  /* ── User detail modal ── */
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:100;display:flex;align-items:center;justify-content:center;padding:20px;opacity:0;pointer-events:none;transition:.2s}
  .modal-bg.open{opacity:1;pointer-events:all}
  .modal{background:#111118;border:1px solid rgba(255,255,255,.1);border-radius:20px;width:100%;max-width:780px;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 24px 80px rgba(0,0,0,.7)}
  .modal-head{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid rgba(255,255,255,.07)}
  .modal-title{font-size:15px;font-weight:700;color:#f6f6f9}
  .modal-sub{font-size:12px;color:#5d5d6b;margin-top:2px}
  .modal-close{width:30px;height:30px;border-radius:8px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);color:#9c9caa;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s}
  .modal-close:hover{background:rgba(255,255,255,.12);color:#f6f6f9}
  .modal-body{overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:24px}
  .detail-section-head{font-size:11px;font-weight:700;color:#c79bff;letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px}
  .stream-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);border-radius:12px;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}
  .stream-name{font-size:14px;font-weight:700}
  .stream-meta{font-size:12px;color:#9c9caa;margin-top:3px}
  .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
  .dot-live{background:#34d399;box-shadow:0 0 6px rgba(52,211,153,.6)}
  .dot-offline{background:#5d5d6b}
  .dot-starting{background:#f59e0b;box-shadow:0 0 6px rgba(245,158,11,.5)}
  .clip-row{display:grid;grid-template-columns:1fr auto auto auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:13px}
  .clip-row:last-child{border-bottom:none}
  .clip-ch{font-weight:600}
  .clip-title{font-size:12px;color:#9c9caa;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px}
  .clip-score{font-size:12px;color:#c79bff;font-weight:700;white-space:nowrap}
  .clip-link{font-size:12px;color:#7c6bff;text-decoration:none;white-space:nowrap}
  .clip-link:hover{color:#c79bff}
  .pill-pending{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.2);color:#fbbf24}
  .pill-approved{background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.2);color:#34d399}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">
    <img src="/static/logo.jpg" alt="Highlightz">
    <span>Highlightz</span>
    <span class="badge">Admin</span>
  </div>
  <a href="/" class="nav-link">&#8592; Dashboard</a>
</div>
<div class="wrap">
  <h1>Admin Panel</h1>
  <p class="meta">User management and platform overview</p>
  <div class="stats" id="stats">
    <div class="stat"><div class="stat-val" id="s-total">—</div><div class="stat-lbl">Total Users</div></div>
    <div class="stat"><div class="stat-val" id="s-active">—</div><div class="stat-lbl">Active Subscribers</div></div>
    <div class="stat"><div class="stat-val" id="s-streams">—</div><div class="stat-lbl">Monitored Streams</div></div>
    <div class="stat"><div class="stat-val" id="s-clips">—</div><div class="stat-lbl">Total Clips</div></div>
  </div>
  <div class="section">
    <div class="section-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
      <span>Users</span>
      <label style="display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:500;color:#9c9caa;cursor:pointer">
        <input type="checkbox" id="show-all-users" onchange="renderUsers()" style="cursor:pointer">
        Show inactive users
      </label>
    </div>
    <div id="table-wrap" class="loading">Loading...</div>
  </div>
  <div class="section" style="margin-top:16px">
    <div class="section-head">Opt-Out Registry</div>
    <p style="font-size:13px;color:#9c9caa;margin-bottom:12px">Streamers who have verified and opted out of being clipped.</p>
    <a href="/admin/optout" style="display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);color:#f6f6f9;border-radius:10px;padding:9px 16px;font-size:13px;font-weight:600;text-decoration:none">View Opt-Out Registry &#8594;</a>
    <a href="/admin/feedback-page" id="feedback-link" style="display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);color:#f6f6f9;border-radius:10px;padding:9px 16px;font-size:13px;font-weight:600;text-decoration:none">View Feedback &#8594;</a>
    <script>
      fetch('/admin/feedback').then(r=>r.json()).then(fb=>{
        const unread=fb.filter(f=>!f.read).length;
        if(unread>0){const el=document.getElementById('feedback-link');if(el)el.textContent='View Feedback ('+unread+' new) →';}
      }).catch(()=>{});
    </script>
  </div>
</div>
<!-- User detail modal -->
<div class="modal-bg" id="modal-bg" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-head">
      <div>
        <div class="modal-title" id="modal-title">User Details</div>
        <div class="modal-sub" id="modal-sub"></div>
      </div>
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    </div>
    <div class="modal-body" id="modal-body"><div class="loading">Loading...</div></div>
  </div>
</div>

<div class="toast" id="toast"></div>
<script>
function esc(s){const d=document.createElement('div');d.appendChild(document.createTextNode(String(s)));return d.innerHTML;}
const fmt = ts => ts ? new Date(ts * 1000).toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric'}) : 'N/A';
const fmtTs = ts => ts ? new Date(ts * 1000).toLocaleString('en-US', {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '';

function pill(status, isAdmin) {
  if (isAdmin) return '<span class="pill pill-admin">Admin</span>';
  if (status === 'active' || status === 'trialing') return '<span class="pill pill-active">Active</span>';
  if (status === 'inactive' || status === 'canceled') return '<span class="pill pill-inactive">Inactive</span>';
  return '<span class="pill pill-none">No Sub</span>';
}

function toast(msg, ok=true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + (ok ? 'ok' : 'err');
  setTimeout(() => el.className = 'toast', 2800);
}

async function api(url, method='GET') {
  const r = await fetch(url, {method, credentials:'same-origin'});
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function grant(idx) {
  const u = _users[idx]; if (!u) return;
  try {
    await api('/admin/users/' + u.id + '/grant', 'POST');
    toast('Access granted');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

async function revoke(idx) {
  const u = _users[idx]; if (!u) return;
  if (!confirm('Revoke access for this user?')) return;
  try {
    await api('/admin/users/' + u.id + '/revoke', 'POST');
    toast('Access revoked');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

async function del(idx) {
  const u = _users[idx]; if (!u) return;
  if (!confirm('Permanently delete ' + u.username + ' and all their data? This cannot be undone.')) return;
  try {
    await api('/admin/users/' + u.id, 'DELETE');
    toast('User deleted');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

async function stripeSync(idx) {
  const u = _users[idx]; if (!u) return;
  try {
    const r = await api('/admin/users/' + u.id + '/stripe-sync', 'POST');
    toast(r.synced ? 'Synced: ' + r.app_status : 'No subscription found');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

let _users = [];

// A user counts as "active" if they have an active or trialing subscription.
// Admins always show (it's the operator's own accounts).
function isActiveUser(u) {
  return u.is_admin || u.subscription_status === 'active' || u.subscription_status === 'trialing';
}

async function load() {
  _users = await api('/admin/users');
  const users = _users;
  // Stats count real customers only — admin accounts are excluded
  const customers = users.filter(u => !u.is_admin);
  const total = customers.length;
  const active = customers.filter(u => u.subscription_status === 'active' || u.subscription_status === 'trialing').length;
  const streams = customers.reduce((a,u) => a + (u.stream_count||0), 0);
  const clips = customers.reduce((a,u) => a + (u.clip_count||0), 0);
  document.getElementById('s-total').textContent = total;
  document.getElementById('s-active').textContent = active;
  document.getElementById('s-streams').textContent = streams;
  document.getElementById('s-clips').textContent = clips;

  renderUsers();
}

function renderUsers() {
  const users = _users || [];
  const showAll = document.getElementById('show-all-users') && document.getElementById('show-all-users').checked;

  if (!users.length) {
    document.getElementById('table-wrap').innerHTML = '<div class="empty">No users yet.</div>';
    return;
  }

  // Keep each row's index into the full _users array so grant/revoke/del/viewUser
  // (which read _users[idx]) stay correct even when the list is filtered.
  const visible = users.map((u, idx) => [u, idx]).filter(([u]) => showAll || isActiveUser(u));

  if (!visible.length) {
    document.getElementById('table-wrap').innerHTML = '<div class="empty">No active users. Tick &ldquo;Show inactive users&rdquo; to see everyone.</div>';
    return;
  }

  const rows = visible.map(([u, idx]) => {
    const avatar = u.avatar_url
      ? '<img class="avatar" src="' + esc(u.avatar_url) + '" alt="">'
      : '<div class="avatar"></div>';
    const sub = u.subscription_status || 'none';
    const isAdmin = u.is_admin;
    const canGrant = !isAdmin && sub !== 'active' && sub !== 'trialing';
    const canRevoke = !isAdmin && (sub === 'active' || sub === 'trialing');
    const trialNote = sub === 'trialing' && u.trial_ends_at
      ? '<div style="font-size:11px;color:#a0a0b0;margin-top:2px">Trial ends ' + fmt(u.trial_ends_at) + '</div>' : '';
    const stripeNote = u.stripe_customer_id
      ? '<div style="font-size:11px;color:#a0a0b0;margin-top:2px">Stripe: ' + esc(u.stripe_customer_id) + '</div>' : '<div style="font-size:11px;color:#a0a0b0;margin-top:2px">No Stripe ID</div>';
    return '<tr>' +
      '<td><div class="avatar-wrap">' + avatar + '<div><div class="username">' + esc(u.username) + '</div>' +
        '<div class="account-id">' + (u.twitch_login ? 'Twitch: ' + esc(u.twitch_login) : 'Password auth') + '</div>' +
        stripeNote + '</div></div></td>' +
      '<td>' + pill(sub, isAdmin) + trialNote + '</td>' +
      '<td>' + (u.stream_count || 0) + '</td>' +
      '<td>' + (u.clip_count || 0) + '</td>' +
      '<td>' + fmt(u.created_at) + '</td>' +
      '<td><div class="actions">' +
        '<button class="btn btn-details" onclick="viewUser(' + idx + ')">Details</button>' +
        (canGrant ? '<button class="btn btn-grant" onclick="grant(' + idx + ')">Grant</button>' : '') +
        (canRevoke ? '<button class="btn btn-revoke" onclick="revoke(' + idx + ')">Revoke</button>' : '') +
        (u.stripe_customer_id && !isAdmin ? '<button class="btn" style="background:rgba(99,102,241,.15);border-color:rgba(99,102,241,.3);color:#a5b4fc" onclick="stripeSync(' + idx + ')">Stripe Sync</button>' : '') +
        (!isAdmin ? '<button class="btn btn-delete" onclick="del(' + idx + ')">Delete</button>' : '') +
      '</div></td>' +
    '</tr>';
  }).join('');

  document.getElementById('table-wrap').innerHTML =
    '<table><thead><tr>' +
    '<th>User</th><th>Status</th><th>Streams</th><th>Clips</th><th>Joined</th><th>Actions</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
}

function closeModal() {
  document.getElementById('modal-bg').classList.remove('open');
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

function dotClass(status) {
  if (status === 'live') return 'dot dot-live';
  if (status === 'starting' || status === 'reconnecting') return 'dot dot-starting';
  return 'dot dot-offline';
}

async function viewUser(idx) {
  const u = _users[idx];
  const uid = u.id;
  document.getElementById('modal-title').textContent = u.username;
  document.getElementById('modal-sub').textContent = (u.twitch_login ? '@' + u.twitch_login + ' · ' : '') + 'Streams & clips';
  document.getElementById('modal-body').innerHTML = '<div class="loading">Loading...</div>';
  document.getElementById('modal-bg').classList.add('open');

  const [streams, clips] = await Promise.all([
    api('/admin/users/' + uid + '/streams').catch(() => []),
    api('/admin/users/' + uid + '/clips').catch(() => []),
  ]);

  // ── Streams section ──
  let streamsHtml = '<div><div class="detail-section-head">Monitored Streams (' + streams.length + ')</div>';
  if (!streams.length) {
    streamsHtml += '<div style="font-size:13px;color:#5d5d6b;padding:12px 0">No streams currently registered.</div>';
  } else {
    streamsHtml += streams.map(s => {
      const status = s.status || 'offline';
      return '<div class="stream-card">' +
        '<div>' +
          '<div class="stream-name">' + esc(s.channel) + '</div>' +
          '<div class="stream-meta">' + esc(s.platform) + ' · preset: ' + esc(s.preset || 'default') + '</div>' +
        '</div>' +
        '<div style="display:flex;align-items:center;gap:8px">' +
          '<div class="' + dotClass(status) + '"></div>' +
          '<span style="font-size:12px;color:#9c9caa;text-transform:capitalize">' + esc(status) + '</span>' +
        '</div>' +
      '</div>';
    }).join('');
  }
  streamsHtml += '</div>';

  // ── Clips section ──
  let clipsHtml = '<div><div class="detail-section-head">Recent Clips (' + clips.length + (clips.length === 100 ? '+' : '') + ')</div>';
  if (!clips.length) {
    clipsHtml += '<div style="font-size:13px;color:#5d5d6b;padding:12px 0">No clips yet.</div>';
  } else {
    const pending   = clips.filter(c => c.status === 'pending').length;
    const approved  = clips.filter(c => c.status === 'approved').length;
    clipsHtml += '<div style="font-size:12px;color:#9c9caa;margin-bottom:12px">' +
      approved + ' approved · ' + pending + ' pending</div>';
    clipsHtml += clips.map(c => {
      const statusPill = c.status === 'approved'
        ? '<span class="pill pill-approved" style="font-size:10px;padding:2px 8px">Approved</span>'
        : '<span class="pill pill-pending" style="font-size:10px;padding:2px 8px">Pending</span>';
      const link = c.twitch_url
        ? '<a class="clip-link" href="' + esc(c.twitch_url) + '" target="_blank" rel="noopener">Watch ↗</a>'
        : '<span style="font-size:12px;color:#5d5d6b">No link</span>';
      const title = c.clip_title || c.stream_title || '';
      return '<div class="clip-row">' +
        '<div>' +
          '<div class="clip-ch">' + esc(c.channel) + ' <span style="font-size:11px;color:#5d5d6b;font-weight:400">' + fmtTs(c.created_at) + '</span></div>' +
          (title ? '<div class="clip-title">' + esc(title) + '</div>' : '') +
        '</div>' +
        '<div class="clip-score">Viral ' + Math.round(c.virality_score || 0) + '%</div>' +
        statusPill +
        link +
      '</div>';
    }).join('');
  }
  clipsHtml += '</div>';

  document.getElementById('modal-body').innerHTML = streamsHtml + clipsHtml;
}

load();
</script>
</body>
</html>"""

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>404 — Highlightz</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
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
