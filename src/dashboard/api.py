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
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
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

_STREAMS_FILE = Path(settings.local_storage_path) / "streams.json"
_CLIPS_FILE   = Path(settings.local_storage_path) / "clips.json"

log = structlog.get_logger(__name__)

app = FastAPI(title="Highlightz Dashboard", version="1.0.0")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Auth middleware ───────────────────────────────────────────────────────────

_OPEN_PATHS    = {"/login", "/logout", "/health", "/favicon.ico", "/tos", "/privacy", "/cookies"}
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
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)
        # Refresh is_admin and subscription_status from DB on every request
        from src.auth import users as user_store
        uid = request.session.get("user_id")
        if uid:
            db_user = user_store.get_by_id(uid)
            if db_user:
                request.session["is_admin"]            = db_user.get("is_admin", False)
                request.session["subscription_status"] = db_user.get("subscription_status", "none")
        # Billing gate — redirect to paywall unless admin or active subscriber
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
            "connect-src 'self' wss: ws:; "
            "frame-src https://clips.twitch.tv https://player.twitch.tv; "
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
_WEBHOOK_RE      = re.compile(r'^https://discord(?:app)?\.com/api/webhooks/\d+/[\w\-]+$')
_VALID_PLATFORMS = {"twitch", "youtube"}
_VALID_PRESETS   = {"default", "fps", "chess", "irl", "small", "variety", "moba", "casino", "sports"}


def _clean_channel(channel: str) -> str:
    """Validate and normalize a channel path parameter to match stored keys.

    add_stream stores channels lowercased, so path-param routes must apply the
    same regex + lowercasing or lookups silently miss (and malformed input is
    rejected before it reaches the stream/clip pipeline)."""
    if not _CHANNEL_RE.fullmatch(channel):
        raise HTTPException(status_code=400, detail="Invalid channel name")
    return channel.lower()

def _validate_webhook(url: str) -> str:
    if url and not _WEBHOOK_RE.match(url):
        raise HTTPException(status_code=400, detail="Invalid Discord webhook URL")
    return url

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

_clips:        dict[str, dict]           = _load_clips()
_streams:      dict[str, dict]           = _load_streams()
_ws_clients:   dict[str, set[WebSocket]] = {}  # user_id -> set of WebSocket
_cleanup_tasks: dict[str, asyncio.Task]  = {}  # user_id -> pending stream cleanup task
_data_lock = asyncio.Lock()
_ws_lock   = asyncio.Lock()

_WS_CLEANUP_DELAY = 30  # seconds to wait after last WS disconnect before stopping workers

# ── Login rate-limit ──────────────────────────────────────────────────────────
# Simple in-process counter: IP → (attempts, window_start)
_login_attempts: dict[str, tuple[int, float]] = {}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW       = 60  # seconds

def _check_login_rate(ip: str) -> None:
    now = time.time()
    # Purge entries older than the window to prevent unbounded growth
    stale = [k for k, (count, ts) in list(_login_attempts.items()) if now - ts > _LOGIN_WINDOW]
    for k in stale:
        _login_attempts.pop(k, None)
    attempts, window_start = _login_attempts.get(ip, (0, now))
    if now - window_start > _LOGIN_WINDOW:
        attempts, window_start = 0, now
    if attempts >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts — wait a minute")
    _login_attempts[ip] = (attempts + 1, window_start)

def _clear_login_rate(ip: str) -> None:
    _login_attempts.pop(ip, None)

# Per-user force-clip rate limit: each manual clip enqueues an FFmpeg extract,
# so an unbounded loop is a CPU DoS on a single-core box.
_force_clip_hits: dict[str, tuple[int, float]] = {}
_FORCE_CLIP_MAX    = 6     # max manual clips per user per window
_FORCE_CLIP_WINDOW = 60    # seconds

_trim_hits: dict[str, tuple[int, float]] = {}
_TRIM_MAX    = 10   # max trim ops per user per window
_TRIM_WINDOW = 60   # seconds

def _check_trim_rate(uid: str) -> None:
    now = time.time()
    stale = [k for k, (count, ts) in list(_trim_hits.items()) if now - ts > _TRIM_WINDOW]
    for k in stale:
        _trim_hits.pop(k, None)
    attempts, window_start = _trim_hits.get(uid, (0, now))
    if now - window_start > _TRIM_WINDOW:
        attempts, window_start = 0, now
    if attempts >= _TRIM_MAX:
        raise HTTPException(status_code=429, detail="Too many trim requests — wait a moment")
    _trim_hits[uid] = (attempts + 1, window_start)

def _check_force_clip_rate(uid: str) -> None:
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

# Limit heavy FFmpeg re-encodes (caption, watermark, trim) to one at a time
# so they don't compete with clip extraction on a single-core server.
_ffmpeg_sem = asyncio.Semaphore(1)


def _ffmpeg_escape(s: str) -> str:
    """Escape a string for safe use in an FFmpeg drawtext filter option value."""
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("[", "\\[").replace("]", "\\]")

_FONT = str(Path(__file__).parent / "static" / "fonts" / "Anton-Regular.ttf")

async def _burn_watermark(source: Path) -> None:
    """Burn a semi-transparent 'Highlightz' text watermark into the bottom-right corner."""
    out = source.parent / (source.stem + "_wm.mp4")
    vf = (
        "drawtext=text='Highlightz'"
        f":fontfile={_FONT}"
        ":fontsize=42:fontcolor=0xc79bff@0.65"
        ":x=w-text_w-22:y=h-text_h-22"
        ":shadowcolor=0x2a0045@0.7:shadowx=3:shadowy=3"
    )
    async with _ffmpeg_sem:
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path, "-y",
                "-threads", "0",
                "-i", str(source),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy",
                str(out),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0 or not out.exists():
                log.warning("watermark_failed", ffmpeg_err=stderr.decode(errors="replace")[-600:])
                out.unlink(missing_ok=True)
                return
            source.unlink(missing_ok=True)
            out.rename(source)
            log.info("watermark_applied", path=str(source))
        except Exception as exc:
            log.warning("watermark_error", error=str(exc))
            out.unlink(missing_ok=True)


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
_MAX_PENDING_CLIPS = 50 # per-user cap on pending clips

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
        while len(user_pending) >= _MAX_PENDING_CLIPS:
            oldest = user_pending.pop(0)
            del _clips[oldest["id"]]
            _delete_clip_file(oldest)
            log.info("clip_cap_evicted", clip_id=oldest["id"], channel=oldest.get("channel"))

        _clips[clip["id"]] = clip
        _save_clips()

    await broadcast({"event": "clip_ready", "clip": clip}, user_id=clip_uid)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _current_user_id(request: Request) -> str:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uid

# ── Twitch OAuth ──────────────────────────────────────────────────────────────

@app.get("/auth/twitch")
async def twitch_login(request: Request):
    """Redirect the browser to Twitch's OAuth consent screen."""
    from src.auth.twitch_oauth import authorization_url
    if not settings.twitch_client_id:
        raise HTTPException(status_code=503, detail="Twitch OAuth not configured")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
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
    return RedirectResponse("/")


@app.get("/me")
async def me(request: Request):
    return {
        "user_id":             request.session.get("user_id", ""),
        "username":            request.session.get("username", ""),
        "avatar_url":          request.session.get("avatar_url", ""),
        "is_admin":            request.session.get("is_admin", False),
        "subscription_status": request.session.get("subscription_status", "none"),
    }


@app.delete("/account", status_code=200)
async def delete_account(request: Request):
    """Permanently delete the authenticated user's account and all associated data."""
    uid = _current_user_id(request)
    from src.auth import users as user_store

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

    user_store.delete(uid)
    request.session.clear()
    log.info("account_deleted", user_id=uid)
    return {"status": "deleted"}


# ── Stripe billing ─────────────────────────────────────────────────────────────

@app.get("/billing/paywall", response_class=HTMLResponse)
async def paywall_page(request: Request):
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    import html as _html
    return HTMLResponse(PAYWALL_HTML.replace("{username}", _html.escape(username)))


@app.get("/billing/checkout")
async def billing_checkout(request: Request):
    """Create a Stripe Checkout session and redirect the user there."""
    from src.billing.stripe_billing import create_checkout_url
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    if not uid:
        return RedirectResponse("/login")
    url = await create_checkout_url(uid, username)
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

    cust_id, user_id, status = sync_subscription_event(event)
    if cust_id and status:
        if user_id:
            user_store.update_subscription(user_id, cust_id, status)
        else:
            user_store.update_subscription_by_customer(cust_id, status)
        log.info("stripe_subscription_updated", customer=cust_id, status=status)
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
    from src.discord.notifier import post_clip as discord_post
    uid = _current_user_id(request)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        clip["status"] = "approved"
        _save_clips()
    await broadcast({"event": "clip_updated", "clip": clip}, user_id=uid)
    pm      = get_profile_manager(uid)
    profile = await pm.get(clip["channel"])
    if profile:
        profile.record_clip(approved=True, signals=clip.get("trigger_signals", []))
        await pm.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    # Burn watermark in background — doesn't block the approve response
    # Read storage_url inside a lock snapshot to avoid a race with deletion.
    async with _data_lock:
        snap = _clips.get(clip_id, {})
        storage_url  = snap.get("storage_url", "")
        already_done = snap.get("watermarked", False)
    if storage_url and not already_done:
        source = Path(storage_url)
        if source.exists():
            async def _wm_task():
                await _burn_watermark(source)
                async with _data_lock:
                    if clip_id in _clips:
                        _clips[clip_id]["watermarked"] = True
                        _clips[clip_id]["watermarked_at"] = time.time()
                        _save_clips()
                updated_wm = _clips.get(clip_id)
                if updated_wm:
                    await broadcast({"event": "clip_updated", "clip": updated_wm}, user_id=uid)
            task = asyncio.create_task(_wm_task())
            task.add_done_callback(
                lambda t: t.exception() and log.warning("watermark_task_failed", exc=str(t.exception()))
            )
    stream_key = f"{uid}:{clip['channel']}"
    stream     = _streams.get(stream_key)
    webhook    = stream.get("discord_webhook", "") if stream else ""
    if webhook:
        task = asyncio.create_task(discord_post(webhook, clip))
        task.add_done_callback(
            lambda t: t.exception() and log.warning("discord_post_failed", exc=str(t.exception()))
        )
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
    _delete_clip_file(clip)
    await broadcast({"event": "clip_removed", "clip_id": clip_id}, user_id=uid)
    pm      = get_profile_manager(uid)
    profile = await pm.get(clip["channel"])
    if profile:
        profile.record_clip(approved=False, signals=clip.get("trigger_signals", []))
        await pm.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    return {"status": "deleted", "clip_id": clip_id}


@app.post("/clips/{clip_id}/caption")
async def render_caption(request: Request, clip_id: str, body: dict):
    uid = _current_user_id(request)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        storage_url = clip.get("storage_url", "")
        if not storage_url:
            raise HTTPException(status_code=400, detail="Clip has no video file yet")
        if clip.get("caption_rendering"):
            raise HTTPException(status_code=409, detail="Caption render already in progress")

    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Caption text is required")
    if len(text) > 200:
        raise HTTPException(status_code=400, detail="Caption must be 200 characters or less")

    source = Path(storage_url)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Clip file not found on disk")

    # Mark as rendering so the UI can show progress
    async with _data_lock:
        if clip_id in _clips:
            _clips[clip_id]["caption_rendering"] = True
            _save_clips()
    await broadcast({"event": "clip_updated", "clip": _clips.get(clip_id, clip)}, user_id=uid)
    log.info("caption_render_start", clip_id=clip_id, source=str(source))

    async def _do_render():
        tmp_txt  = source.parent / f"{clip_id}_caption.txt"
        out_path = source.parent / f"{clip_id}_captioned.mp4"

        async def _fail(reason: str) -> None:
            """Clear caption_rendering in both DB and frontend, then send failure event."""
            async with _data_lock:
                if clip_id in _clips:
                    _clips[clip_id].pop("caption_rendering", None)
                    _save_clips()
            cleared = _clips.get(clip_id, clip)
            # Send clip_updated first so the frontend clears the spinner,
            # then send caption_failed so the UI can show the error message.
            await broadcast({"event": "clip_updated", "clip": cleared}, user_id=uid)
            await broadcast({"event": "caption_failed", "clip_id": clip_id, "detail": reason}, user_id=uid)

        try:
            tmp_txt.write_text(text, encoding="utf-8")
            # scale=1280:-2 downsizes 1080p→720p before captioning (half the pixels,
            # ~3x faster encode on a single CPU) while keeping the caption legible.
            vf = (
                f"drawtext=textfile='{_ffmpeg_escape(str(tmp_txt))}'"
                f":fontfile='{_ffmpeg_escape(str(_FONT))}'"
                f":fontsize=62:fontcolor=black"
                f":x=(w-text_w)/2:y=52"
                f":box=1:boxcolor=white:boxborderw=18"
            )
            ffmpeg_args = [
                settings.ffmpeg_path, "-y",
                "-threads", "0",
                "-i", str(source),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy",
                str(out_path),
            ]
            log.info("caption_ffmpeg_cmd", clip_id=clip_id, args=" ".join(ffmpeg_args))
            async with _ffmpeg_sem:
                proc = await asyncio.create_subprocess_exec(
                    *ffmpeg_args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                log.info("caption_ffmpeg_pid", clip_id=clip_id, pid=proc.pid)
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            err_txt = stderr.decode(errors="replace")
            if proc.returncode != 0 or not out_path.exists():
                log.warning("caption_render_failed", clip_id=clip_id,
                            returncode=proc.returncode, ffmpeg_err=err_txt[-1200:])
                await _fail("Render failed — see server logs")
                return
            log.info("caption_ffmpeg_ok", clip_id=clip_id, stderr_tail=err_txt[-200:])
            source.unlink(missing_ok=True)
            out_path.rename(source)
            log.info("caption_rendered", clip_id=clip_id)
        except asyncio.TimeoutError:
            log.warning("caption_render_timeout", clip_id=clip_id)
            out_path.unlink(missing_ok=True)
            await _fail("Render timed out")
            return
        except Exception as exc:
            log.warning("caption_render_error", clip_id=clip_id, error=str(exc))
            out_path.unlink(missing_ok=True)
            await _fail(str(exc))
            return
        finally:
            tmp_txt.unlink(missing_ok=True)

        async with _data_lock:
            if clip_id in _clips:
                _clips[clip_id]["caption"] = text
                _clips[clip_id]["caption_at"] = time.time()
                _clips[clip_id].pop("caption_rendering", None)
                _save_clips()
        updated = _clips.get(clip_id, clip)
        await broadcast({"event": "clip_updated", "clip": updated}, user_id=uid)

    task = asyncio.create_task(_do_render())
    task.add_done_callback(lambda t: t.exception() and log.warning("caption_task_exc", error=str(t.exception())))
    return {"status": "rendering", "clip_id": clip_id}


@app.post("/clips/{clip_id}/trim")
async def trim_clip(request: Request, clip_id: str, body: dict):
    uid = _current_user_id(request)
    _check_trim_rate(uid)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        storage_url = clip.get("storage_url", "")
        if not storage_url:
            raise HTTPException(status_code=400, detail="Clip has no video file yet")
        if clip.get("caption_rendering"):
            raise HTTPException(status_code=409, detail="Caption render in progress — try again shortly")

    try:
        start = float(body.get("start", 0))
        end   = float(body.get("end", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="start and end must be numbers")

    if start < 0 or end <= 0 or end <= start:
        raise HTTPException(status_code=400, detail="end must be greater than start, both non-negative")
    if (end - start) < 1:
        raise HTTPException(status_code=400, detail="Trimmed clip must be at least 1 second long")
    clip_dur = clip.get("duration_seconds", 0)
    if clip_dur > 0 and end > clip_dur + 1:
        raise HTTPException(status_code=400, detail=f"end ({end}s) exceeds clip duration ({clip_dur:.1f}s)")

    source   = Path(storage_url)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Clip file not found on disk")

    out_path = source.parent / f"{clip_id}_trimmed.mp4"
    try:
        async with _ffmpeg_sem:
            proc = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path, "-y",
                "-i", str(source),
                "-ss", str(start),
                "-to", str(end),
                "-c", "copy",
                str(out_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0 or not out_path.exists():
            err = stderr.decode(errors="replace")[-400:]
            log.warning("trim_failed", clip_id=clip_id, ffmpeg_err=err)
            raise HTTPException(status_code=500, detail="Trim failed — check FFmpeg logs")
    except asyncio.TimeoutError:
        out_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Trim timed out")

    source.unlink(missing_ok=True)
    out_path.rename(source)
    log.info("clip_trimmed", clip_id=clip_id, start=start, end=end)

    new_duration = end - start
    async with _data_lock:
        if clip_id in _clips:
            _clips[clip_id]["duration_seconds"] = new_duration
            _save_clips()
    updated = _clips.get(clip_id, clip)
    await broadcast({"event": "clip_updated", "clip": updated}, user_id=uid)
    return updated


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
    discord_webhook: str = ""

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

    @field_validator("discord_webhook")
    @classmethod
    def valid_webhook(cls, v: str) -> str:
        if v and not _WEBHOOK_RE.match(v):
            raise ValueError("Invalid Discord webhook URL")
        return v


@app.get("/streams")
async def list_streams(request: Request):
    uid = _current_user_id(request)
    return [s for s in _streams.values() if s.get("user_id") == uid]


@app.post("/streams", status_code=201)
async def add_stream(request: Request, req: StreamRequest):
    uid        = _current_user_id(request)
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
            "discord_webhook": req.discord_webhook,
            "user_id":         uid,
        }
        _streams[stream_key] = record
        _save_streams()
    await broadcast({"event": "stream_added", "stream": record}, user_id=uid)
    if _publish_new_stream:
        await _publish_new_stream(req.channel, req.platform, req.preset, uid)
    return record


@app.patch("/streams/{channel}/webhook", status_code=200)
async def set_stream_webhook(request: Request, channel: str, body: dict):
    uid        = _current_user_id(request)
    channel    = _clean_channel(channel)
    stream_key = f"{uid}:{channel}"
    webhook    = _validate_webhook(str(body.get("discord_webhook", "")))
    async with _data_lock:
        if stream_key not in _streams:
            raise HTTPException(status_code=404, detail="Stream not found")
        _streams[stream_key]["discord_webhook"] = webhook
        _save_streams()
    return _streams[stream_key]


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
    for key in keys:
        stream = _streams.get(key)
        if stream and _publish_remove_stream:
            try:
                await _publish_remove_stream(stream["channel"], uid)
            except Exception as exc:
                log.warning("stop_user_streams_failed", channel=stream.get("channel"), error=str(exc))


async def _stop_user_streams(uid: str) -> None:
    """Stop all stream workers for a user after the WS-disconnect grace period."""
    await asyncio.sleep(_WS_CLEANUP_DELAY)
    # Re-check: user may have reconnected during the grace period
    if _ws_clients.get(uid):
        return
    await _stop_user_streams_now(uid)


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
    async with _ws_lock:
        bucket = _ws_clients.setdefault(uid, set())
        if len(bucket) >= _MAX_WS_PER_USER:
            await ws.close(code=1008)
            return
        bucket.add(ws)
        # Cancel any pending stream cleanup — user is back
        pending = _cleanup_tasks.pop(uid, None)
        if pending:
            pending.cancel()
    log.info("ws_connected", user=uid, total=sum(len(v) for v in _ws_clients.values()))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            _ws_clients.get(uid, set()).discard(ws)
            # Schedule cleanup only when this was the last connection for this user
            if not _ws_clients.get(uid):
                task = asyncio.create_task(_stop_user_streams(uid))
                _cleanup_tasks[uid] = task
                task.add_done_callback(lambda t: _cleanup_tasks.pop(uid, None))
        log.info("ws_disconnected", user=uid)


@app.get("/clip-file")
async def serve_clip_file(request: Request, path: str):
    uid        = _current_user_id(request)
    clips_root = Path(settings.local_storage_path).resolve()
    p          = Path(path).resolve()
    if not p.is_relative_to(clips_root) or p.suffix != ".mp4":
        raise HTTPException(status_code=404, detail="Clip file not found")
    # Verify ownership inside the lock so deletion can't race the check.
    async with _data_lock:
        stem = p.stem
        clip = _clips.get(stem)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=403, detail="Access denied")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")
    return FileResponse(str(p), media_type="video/mp4")


@app.post("/streams/{channel}/force-clip")
async def force_clip(request: Request, channel: str):
    uid        = _current_user_id(request)
    channel    = _clean_channel(channel)
    _check_force_clip_rate(uid)
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
        return RedirectResponse("/")
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
    return {"ok": True}


@app.post("/admin/users/{user_id}/revoke")
async def admin_revoke_access(request: Request, user_id: str):
    _require_admin(request)
    from src.auth import users as user_store
    user_store.update_subscription(user_id, None, "inactive")
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
    "twitch_failed":     "Twitch login failed. Please try again.",
    "discord_failed":    "Login failed. Please try again.",
    "invalid_state":     "Login session expired. Please try again.",
    "incorrect_password": "Incorrect password. Please try again.",
    "account_deleted":   "Account deleted successfully.",
}


@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    err_msg = _ERROR_MESSAGES.get(error, "")
    err_html = f'<p class="error">{err_msg}</p>' if err_msg else ""
    return HTMLResponse(LOGIN_HTML.replace("{error}", err_html))


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    from src.auth import users as user_store
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
    user = next((u for u in user_store._load() if user_store.verify(u, password)), None)
    if user:
        _clear_login_rate(ip)
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
    # GET /logout is intentionally a no-op redirect — actual logout requires POST.
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


@app.post("/admin/users", status_code=201)
async def create_user(request: Request, body: dict):
    _require_admin(request)
    from src.auth import users as user_store
    username   = body.get("username", "").strip()
    password   = body.get("password", "").strip()
    make_admin = bool(body.get("is_admin", False))
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    if len(password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")
    try:
        user = user_store.create(username, password, is_admin=make_admin)
        log.info("admin_user_created", by=request.session.get("user_id"), new_user=user["id"], is_admin=make_admin)
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
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz - Sign In</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(168,85,247,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:44px 40px;width:360px;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:22px}
  .logo-wrap img{height:80px;width:auto;filter:drop-shadow(0 0 18px rgba(199,155,255,.5))}
  h1{font-size:26px;font-weight:800;color:#c79bff;margin-bottom:4px;letter-spacing:-.02em}
  .sub{font-size:13px;color:#9c9caa;margin-bottom:30px}
  .twitch-btn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;background:#9146ff;color:#fff;border:none;border-radius:12px;padding:13px;font-size:14px;font-weight:700;cursor:pointer;text-decoration:none;transition:background .15s}
  .twitch-btn:hover{background:#772ce8}
  .twitch-btn svg{flex-shrink:0}
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
  {error}
  <a href="/auth/twitch" class="twitch-btn">
    <svg width="20" height="20" viewBox="0 0 2400 2800" fill="#fff"><path d="M500 0L0 500v1800h600v500l500-500h400l900-900V0H500zm1700 1300l-400 400h-400l-350 350v-350H600V200h1600v1100z"/><path d="M1700 550h-200v600h200V550zm-550 0h-200v600h200V550z"/></svg>
    Continue with Twitch
  </a>
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
  <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a>
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
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
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
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo.jpg" alt="Highlightz"></div>
  <span class="badge">Highlightz Pro</span>
  <h1>Never miss a clip again</h1>
  <p class="sub">Hi {username} — activate your subscription to start automatically capturing your best streaming moments.</p>
  <div class="features">
    <div class="feat"><span class="ic">›</span>Automatic clip detection on any live channel</div>
    <div class="feat"><span class="ic">›</span>Live trigger score analytics</div>
    <div class="feat"><span class="ic">›</span>Instant clips created on Twitch under your account</div>
    <div class="feat"><span class="ic">›</span>Discord notifications on approval</div>
    <div class="feat"><span class="ic">›</span>Per-channel AI learning baseline</div>
  </div>
  <a href="/billing/checkout" class="cta">Start Subscription →</a>
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
  <p class="meta">Effective date: June 11, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>Please read these Terms of Service ("Terms") carefully before using Highlightz ("Service"), operated by ANTI Technology LLC ("we," "us," or "our"). By accessing or using the Service you agree to be bound by these Terms. If you do not agree, do not use the Service.</p>

  <h2>1. Description of Service</h2>
  <p>Highlightz is a SaaS platform that monitors live streams on Twitch, automatically detects highlight moments from public signals such as chat activity and stream audio levels, and — at your direction and on your behalf — creates clips using Twitch's official Clips API. Clips are created, processed, hosted, and stored by Twitch on Twitch's own infrastructure under your Twitch account. Highlightz does not record, copy, download, or re-host stream video. The Service requires an active paid subscription to access core features.</p>

  <h2>2. Eligibility</h2>
  <p>You must be at least 18 years old to use the Service. By using the Service you represent and warrant that you meet this requirement and that all information you provide is accurate and complete.</p>

  <h2>3. Accounts and Twitch Authorization</h2>
  <p>You sign in by authorizing the Service through your Twitch account via OAuth2. By connecting your Twitch account you grant the Service permission to create clips on your behalf using Twitch's Clips API (the <code>clips:edit</code> permission). Every clip created through the Service is made with <em>your</em> Twitch credentials and is attributed to <em>your</em> Twitch account, exactly as if you had clicked Twitch's own "Clip" button. You are responsible for maintaining the confidentiality of your account and for all activity that occurs under it, including all clips created through it. Notify us immediately at the contact address below if you suspect unauthorized use. We reserve the right to terminate accounts that violate these Terms.</p>

  <h2>4. Subscriptions and Billing</h2>
  <p>Access to the Service requires a paid subscription. Subscriptions are billed on a recurring basis through our payment processor, Stripe. By subscribing you authorize us to charge the payment method on file for each billing period until you cancel.</p>
  <ul>
    <li>You may cancel your subscription at any time through the billing portal. Cancellation takes effect at the end of the current billing period.</li>
    <li>We do not issue refunds for partial billing periods or unused time.</li>
    <li>We reserve the right to change pricing with at least 14 days notice to your registered email address.</li>
    <li>Failed payments may result in suspension or termination of your account.</li>
  </ul>

  <h2>5. Clips, Streamer Content, and Your Responsibility</h2>
  <p><strong>You — not Highlightz — create the clips, and you are solely responsible for them.</strong> When the Service creates a clip, it does so on your behalf and with your authorization through Twitch's official Clips API, using your Twitch account. The resulting clip is owned, hosted, and governed by Twitch. Highlightz acts only as a tool that you direct; it never records, stores, or re-hosts any stream video itself.</p>
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
  <p>The Service integrates with third-party platforms including Twitch (authentication and clip creation) and Stripe (payments), and may optionally deliver notifications to a Discord webhook you provide. Your use of those platforms is governed by their respective terms of service, including the <a href="https://www.twitch.tv/p/legal/terms-of-service/">Twitch Terms of Service</a> and <a href="https://legal.twitch.com/legal/developer-agreement/">Twitch Developer Services Agreement</a>. We are not responsible for the availability, accuracy, or practices of any third-party service.</p>

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
  <p class="meta">Effective date: June 11, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>This Privacy Policy describes how ANTI Technology LLC ("we," "us," or "our") collects, uses, and shares information when you use Highlightz ("Service"). By using the Service you agree to the practices described here.</p>

  <h2>1. Information We Collect</h2>
  <p>We collect only what is necessary to operate the Service:</p>
  <ul>
    <li><strong>Account information</strong> — your Twitch user ID, login, display name, and avatar URL, obtained when you sign in via Twitch OAuth2.</li>
    <li><strong>Twitch access tokens</strong> — the OAuth access and refresh tokens that authorize the Service to create clips on your behalf. These are stored in encrypted form and are never shared.</li>
    <li><strong>Billing information</strong> — payment processing is handled entirely by Stripe. We store only your Stripe Customer ID and subscription status. We never see or store your card details.</li>
    <li><strong>Clip metadata</strong> — channel names, timestamps, trigger scores, and the Twitch clip links generated for your account. We do not store any stream video; clips are hosted by Twitch.</li>
    <li><strong>Session data</strong> — a server-side session cookie that keeps you signed in (see our <a href="/cookies">Cookie Policy</a>).</li>
    <li><strong>Log data</strong> — server logs may contain IP addresses and request metadata for security and debugging purposes.</li>
  </ul>

  <h2>2. How We Use Your Information</h2>
  <ul>
    <li>To authenticate you and maintain your session.</li>
    <li>To create clips on your behalf via Twitch's Clips API when you or your trigger settings direct it.</li>
    <li>To process payments and manage your subscription via Stripe.</li>
    <li>To display your clip links and trigger analytics in your dashboard.</li>
    <li>To send notifications to a Discord webhook if you have configured one.</li>
    <li>To investigate security incidents and prevent abuse.</li>
  </ul>

  <h2>3. How We Share Your Information</h2>
  <p>We do not sell your personal data. We share information only with the following third parties as necessary to operate the Service:</p>
  <ul>
    <li><strong>Twitch</strong> — for authentication and for creating clips on your behalf. Governed by Twitch's Privacy Notice.</li>
    <li><strong>Stripe</strong> — for payment processing. Governed by Stripe's Privacy Policy.</li>
    <li><strong>Discord</strong> — only if you configure a notification webhook. Governed by Discord's Privacy Policy.</li>
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
  <p class="meta">Effective date: June 11, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

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
  <p>When you sign in via Twitch, Twitch may set cookies on their own domain as part of the OAuth2 flow. These are governed by <a href="https://www.twitch.tv/p/legal/privacy-notice/" target="_blank" rel="noopener">Twitch's Privacy Notice</a>. When you complete a payment via Stripe, Stripe may set cookies on their domain. These are governed by <a href="https://stripe.com/privacy" target="_blank" rel="noopener">Stripe's Privacy Policy</a>. We have no control over or access to these third-party cookies.</p>

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
  .discord-id{font-size:11px;color:#5d5d6b;margin-top:2px;font-family:monospace}
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
  .empty{padding:40px;text-align:center;color:#5d5d6b;font-size:13px}
  .loading{padding:40px;text-align:center;color:#5d5d6b;font-size:13px}
  .toast{position:fixed;bottom:24px;right:24px;background:rgba(30,30,40,.95);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:12px 20px;font-size:13px;font-weight:600;box-shadow:0 8px 32px rgba(0,0,0,.4);opacity:0;transform:translateY(8px);transition:.25s;pointer-events:none;z-index:999}
  .toast.show{opacity:1;transform:none}
  .toast.ok{border-color:rgba(52,211,153,.4);color:#34d399}
  .toast.err{border-color:rgba(239,68,68,.4);color:#f87171}
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
    <div class="section-head">Users</div>
    <div id="table-wrap" class="loading">Loading...</div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
function esc(s){const d=document.createElement('div');d.appendChild(document.createTextNode(String(s)));return d.innerHTML;}
const fmt = ts => ts ? new Date(ts * 1000).toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric'}) : 'N/A';

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

async function grant(id, btn) {
  try {
    await api('/admin/users/' + id + '/grant', 'POST');
    toast('Access granted');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

async function revoke(id) {
  if (!confirm('Revoke access for this user?')) return;
  try {
    await api('/admin/users/' + id + '/revoke', 'POST');
    toast('Access revoked');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

async function del(id, name) {
  if (!confirm('Permanently delete ' + name + ' and all their data? This cannot be undone.')) return;
  try {
    await api('/admin/users/' + id, 'DELETE');
    toast('User deleted');
    load();
  } catch(e) { toast('Error: ' + e.message, false); }
}

async function load() {
  const users = await api('/admin/users');
  const total = users.length;
  const active = users.filter(u => u.subscription_status === 'active' || u.subscription_status === 'trialing').length;
  const streams = users.reduce((a,u) => a + (u.stream_count||0), 0);
  const clips = users.reduce((a,u) => a + (u.clip_count||0), 0);
  document.getElementById('s-total').textContent = total;
  document.getElementById('s-active').textContent = active;
  document.getElementById('s-streams').textContent = streams;
  document.getElementById('s-clips').textContent = clips;

  if (!users.length) {
    document.getElementById('table-wrap').innerHTML = '<div class="empty">No users yet.</div>';
    return;
  }

  const rows = users.map(u => {
    const avatar = u.avatar_url
      ? '<img class="avatar" src="' + esc(u.avatar_url) + '" alt="">'
      : '<div class="avatar"></div>';
    const sub = u.subscription_status || 'none';
    const isAdmin = u.is_admin;
    const canGrant = !isAdmin && sub !== 'active' && sub !== 'trialing';
    const canRevoke = !isAdmin && (sub === 'active' || sub === 'trialing');
    return '<tr>' +
      '<td><div class="avatar-wrap">' + avatar + '<div><div class="username">' + esc(u.username) + '</div>' +
        '<div class="discord-id">' + (u.discord_id ? 'Discord: ' + esc(u.discord_id) : 'Password auth') + '</div></div></div></td>' +
      '<td>' + pill(sub, isAdmin) + '</td>' +
      '<td>' + (u.stream_count || 0) + '</td>' +
      '<td>' + (u.clip_count || 0) + '</td>' +
      '<td>' + fmt(u.created_at) + '</td>' +
      '<td><div class="actions">' +
        (canGrant ? '<button class="btn btn-grant" onclick="grant(' + JSON.stringify(u.id) + ')">Grant</button>' : '') +
        (canRevoke ? '<button class="btn btn-revoke" onclick="revoke(' + JSON.stringify(u.id) + ')">Revoke</button>' : '') +
        (!isAdmin ? '<button class="btn btn-delete" onclick="del(' + JSON.stringify(u.id) + ',' + JSON.stringify(u.username) + ')">Delete</button>' : '') +
      '</div></td>' +
    '</tr>';
  }).join('');

  document.getElementById('table-wrap').innerHTML =
    '<table><thead><tr>' +
    '<th>User</th><th>Status</th><th>Streams</th><th>Clips</th><th>Joined</th><th>Actions</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
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
  body{background:#08080b;color:#f6f6f9;font-family:Inter,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}
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
