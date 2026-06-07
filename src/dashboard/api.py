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

from config.settings import settings
from src.dashboard.aurora_html import DASHBOARD_HTML

_STREAMS_FILE = Path(settings.local_storage_path) / "streams.json"
_CLIPS_FILE   = Path(settings.local_storage_path) / "clips.json"

log = structlog.get_logger(__name__)

app = FastAPI(title="Highlightz Dashboard", version="1.0.0")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Auth middleware ───────────────────────────────────────────────────────────

_OPEN_PATHS    = {"/login", "/logout", "/health", "/favicon.ico"}
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
        if settings.dashboard_https_only:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

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
_VALID_PRESETS   = {"default", "fps", "chess", "irl"}

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
    attempts, window_start = _login_attempts.get(ip, (0, now))
    if now - window_start > _LOGIN_WINDOW:
        attempts, window_start = 0, now
    if attempts >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts — wait a minute")
    _login_attempts[ip] = (attempts + 1, window_start)

def _clear_login_rate(ip: str) -> None:
    _login_attempts.pop(ip, None)

# ── Helper ────────────────────────────────────────────────────────────────────

# Limit heavy FFmpeg re-encodes (caption, watermark, trim) to one at a time
# so they don't compete with clip extraction on a single-core server.
_ffmpeg_sem = asyncio.Semaphore(1)

_FONT = str(Path(__file__).parent / "static" / "fonts" / "Anton-Regular.ttf")

async def _burn_watermark(source: Path) -> None:
    """Burn a semi-transparent 'Highlightz' text watermark into the bottom-right corner."""
    out = source.parent / (source.stem + "_wm.mp4")
    vf = (
        "drawtext=text='Highlightz'"
        f":fontfile={_FONT}"
        ":fontsize=28:fontcolor=0xc79bff@0.55"
        ":x=w-text_w-18:y=h-text_h-18"
        ":shadowcolor=0x2a0045@0.6:shadowx=2:shadowy=2"
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

# ── Discord OAuth ─────────────────────────────────────────────────────────────

@app.get("/auth/discord")
async def discord_login(request: Request):
    """Redirect the browser to Discord's OAuth consent screen."""
    from src.auth.discord_oauth import authorization_url
    if not settings.discord_client_id:
        raise HTTPException(status_code=503, detail="Discord OAuth not configured")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(authorization_url(state))


@app.get("/auth/discord/callback")
async def discord_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle Discord OAuth callback, create/find user, set session."""
    from src.auth import discord_oauth, users as user_store
    if error:
        return RedirectResponse(f"/login?error={error}")
    if state != request.session.pop("oauth_state", None):
        return RedirectResponse("/login?error=Invalid+state")
    try:
        token = await discord_oauth.exchange_code(code)
        duser = await discord_oauth.get_user(token)
    except Exception as exc:
        log.warning("discord_oauth_failed", error=str(exc))
        return RedirectResponse("/login?error=Discord+login+failed")

    user = user_store.upsert_discord_user(
        discord_id=duser["id"],
        username=duser["username"],
        avatar_url=duser.get("avatar_url", ""),
    )
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
        "user_id":  request.session.get("user_id", ""),
        "username": request.session.get("username", ""),
        "avatar_url": request.session.get("avatar_url", ""),
        "is_admin": request.session.get("is_admin", False),
    }


# ── Stripe billing ─────────────────────────────────────────────────────────────

@app.get("/billing/paywall", response_class=HTMLResponse)
async def paywall_page(request: Request):
    uid      = request.session.get("user_id", "")
    username = request.session.get("username", "")
    return HTMLResponse(PAYWALL_HTML.replace("{username}", username))


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
                f"drawtext=textfile={tmp_txt}"
                f":fontfile={_FONT}"
                f":fontsize=46:fontcolor=black"
                f":x=(w-text_w)/2:y=48"
                f":box=1:boxcolor=white:boxborderw=14"
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
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        storage_url = clip.get("storage_url", "")
        if not storage_url:
            raise HTTPException(status_code=400, detail="Clip has no video file yet")

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
    stream_key = f"{uid}:{channel}"
    async with _data_lock:
        if stream_key not in _streams:
            raise HTTPException(status_code=404, detail="Stream not found")
        del _streams[stream_key]
        _save_streams()
    await broadcast({"event": "stream_removed", "channel": channel}, user_id=uid)
    if _publish_remove_stream:
        await _publish_remove_stream(channel, uid)


async def _stop_user_streams(uid: str) -> None:
    """Stop all stream workers for a user after the grace period."""
    await asyncio.sleep(_WS_CLEANUP_DELAY)
    # Re-check: user may have reconnected during the grace period
    if _ws_clients.get(uid):
        return
    keys = [k for k in _streams if k.startswith(f"{uid}:")]
    if not keys:
        return
    log.info("ws_cleanup_streams", user=uid, count=len(keys))
    for key in keys:
        stream = _streams.get(key)
        if stream and _publish_remove_stream:
            try:
                await _publish_remove_stream(stream["channel"], uid)
            except Exception as exc:
                log.warning("ws_cleanup_remove_failed", channel=stream.get("channel"), error=str(exc))


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
    stream_key = f"{uid}:{channel}"
    if stream_key not in _streams:
        raise HTTPException(status_code=404, detail="Stream not registered")
    if not _force_clip_cb:
        raise HTTPException(status_code=503, detail="Force clip not ready")
    await _force_clip_cb(channel, uid)
    return {"status": "queued", "channel": channel}


@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    import html as _html
    err_html = f'<p class="error">{_html.escape(error)}</p>' if error else ""
    return HTMLResponse(LOGIN_HTML.replace("{error}", err_html))


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    from src.auth import users as user_store
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
    user = next((u for u in user_store._load() if user_store.verify(u, password)), None)
    if user:
        _clear_login_rate(ip)
        request.session["auth"]                = True
        request.session["user_id"]             = user["id"]
        request.session["username"]            = user["username"]
        request.session["avatar_url"]          = user.get("avatar_url", "")
        request.session["is_admin"]            = user.get("is_admin", False)
        request.session["subscription_status"] = user.get("subscription_status", "none")
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=Incorrect+password", status_code=302)


@app.get("/logout")
async def logout(request: Request):
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


@app.post("/admin/users", status_code=201)
async def create_user(request: Request, body: dict):
    _require_admin(request)
    from src.auth import users as user_store
    username   = body.get("username", "").strip()
    password   = body.get("password", "").strip()
    make_admin = bool(body.get("is_admin", False))
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    try:
        user = user_store.create(username, password, is_admin=make_admin)
        log.info("admin_user_created", by=request.session.get("user_id"), new_user=user["id"], is_admin=make_admin)
        return {"id": user["id"], "username": user["username"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/admin/users")
async def list_users(request: Request):
    _require_admin(request)
    from src.auth import users as user_store
    return user_store.get_all()


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
  .discord-btn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;background:#5865f2;color:#fff;border:none;border-radius:12px;padding:13px;font-size:14px;font-weight:700;cursor:pointer;text-decoration:none;transition:background .15s}
  .discord-btn:hover{background:#4752c4}
  .discord-btn svg{flex-shrink:0}
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
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo.jpg" alt="Highlightz logo"></div>
  <h1>Highlightz</h1>
  <p class="sub">Sign in to start clipping highlights</p>
  {error}
  <a href="/auth/discord" class="discord-btn">
    <svg width="20" height="20" viewBox="0 0 127.14 96.36" fill="#fff"><path d="M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z"/></svg>
    Continue with Discord
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
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo.jpg" alt="Highlightz"></div>
  <span class="badge">⚡ Highlightz Pro</span>
  <h1>Never miss a clip again</h1>
  <p class="sub">Hi {username} — activate your subscription to start automatically capturing your best streaming moments.</p>
  <div class="features">
    <div class="feat"><span class="ic">🎬</span>Automatic clip detection on any stream</div>
    <div class="feat"><span class="ic">📊</span>Live trigger score analytics</div>
    <div class="feat"><span class="ic">✂️</span>16:9 + 9:16 vertical crop export</div>
    <div class="feat"><span class="ic">🔔</span>Discord notifications on approval</div>
    <div class="feat"><span class="ic">🧠</span>Per-channel AI learning baseline</div>
  </div>
  <a href="/billing/checkout" class="cta">Start Subscription →</a>
  <a href="/billing/portal" class="manage">Already subscribed? Manage billing</a>
  <a href="/logout" class="logout">Sign out</a>
</div>
</body>
</html>"""
