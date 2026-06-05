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

_OPEN_PATHS   = {"/login", "/health", "/favicon.ico"}
_STATIC_PREFIX = "/static"

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _OPEN_PATHS or path == "/login" or path.startswith(_STATIC_PREFIX):
            return await call_next(request)
        if not request.session.get("auth"):
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)
        return await call_next(request)

# Middleware stack is LIFO — SessionMiddleware added last runs first,
# so the cookie is parsed before AuthMiddleware inspects the session.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.dashboard_secret_key,
    max_age=86400 * 30,
    https_only=settings.dashboard_https_only,
    same_site="lax",
    http_only=True,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type"],
)

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

_clips:      dict[str, dict]          = _load_clips()
_streams:    dict[str, dict]          = _load_streams()
_ws_clients: dict[str, set[WebSocket]] = {}  # user_id -> set of WebSocket
_data_lock = asyncio.Lock()
_ws_lock   = asyncio.Lock()

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
    log.info("ws_connected", user=uid, total=sum(len(v) for v in _ws_clients.values()))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            _ws_clients.get(uid, set()).discard(ws)
        log.info("ws_disconnected", user=uid)


@app.get("/clip-file")
async def serve_clip_file(request: Request, path: str):
    # Auth is enforced by AuthMiddleware; this just validates the path stays in-bounds.
    _current_user_id(request)
    clips_root = Path(settings.local_storage_path).resolve()
    p          = Path(path).resolve()
    if not p.is_relative_to(clips_root) or p.suffix != ".mp4" or not p.exists():
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
    await _force_clip_cb(channel)
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
        request.session["auth"]     = True
        request.session["user_id"]  = user["id"]
        request.session["username"] = user["username"]
        request.session["is_admin"] = user.get("is_admin", False)
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=Incorrect+password", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/admin/users", status_code=201)
async def create_user(request: Request, body: dict):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    from src.auth import users as user_store
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    try:
        user = user_store.create(username, password)
        return {"id": user["id"], "username": user["username"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/admin/users")
async def list_users(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
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
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0e0e10; color: #efeff1; font-family: Inter, system-ui, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: #1f1f23; border: 1px solid #2d2d35; border-radius: 12px; padding: 40px; width: 340px; }
  .logo-wrap { display: flex; justify-content: center; margin-bottom: 20px; }
  .logo-wrap img { height: 120px; width: auto; }
  h1 { font-size: 24px; font-weight: 700; color: #bf94ff; margin-bottom: 4px; }
  .sub { font-size: 13px; color: #adadb8; margin-bottom: 28px; }
  label { font-size: 12px; color: #adadb8; display: block; margin-bottom: 6px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
  input { width: 100%; background: #26262c; border: 1px solid #3a3a44; border-radius: 6px; color: #efeff1; padding: 10px 12px; font-size: 14px; outline: none; margin-bottom: 16px; }
  input:focus { border-color: #bf94ff; }
  button { width: 100%; background: #bf94ff; color: #0e0e10; border: none; border-radius: 6px; padding: 11px; font-size: 14px; font-weight: 700; cursor: pointer; transition: background .15s; }
  button:hover { background: #a970ff; }
  .error { color: #eb0400; font-size: 12px; margin-bottom: 14px; background: #3a1a1a; padding: 8px 12px; border-radius: 6px; }
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo.jpg" alt="Highlightz logo"></div>
  <h1>Highlightz</h1>
  <p class="sub">Sign in to your dashboard</p>
  {error}
  <form method="POST" action="/login">
    <label>Password</label>
    <input type="password" name="password" autofocus placeholder="Enter your password" autocomplete="current-password">
    <button type="submit">Sign In</button>
  </form>
</div>
</body>
</html>"""
