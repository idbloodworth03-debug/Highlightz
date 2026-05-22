# -*- coding: utf-8 -*-
"""
FastAPI dashboard: REST endpoints + WebSocket for real-time clip review.

Endpoints:
  GET  /clips          - list clips (filterable by status/channel)
  GET  /clips/{id}     - single clip detail
  POST /clips/{id}/approve
  POST /clips/{id}/reject
  GET  /streams        - list currently monitored streams
  POST /streams        - register a new stream to watch
  DELETE /streams/{channel}
  WS   /ws             - real-time clip notifications
"""

import asyncio
import json
from typing import Any
from pathlib import Path

_STREAMS_FILE = Path("clips/streams.json")
_CLIPS_FILE = Path("clips/clips.json")

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from config.settings import settings

log = structlog.get_logger(__name__)

app = FastAPI(title="Highlightz Dashboard", version="1.0.0")

# ── Auth middleware ───────────────────────────────────────────────────────────

_OPEN_PATHS = {"/login", "/health", "/ws", "/favicon.ico", "/logo.svg"}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _OPEN_PATHS or path.startswith("/login"):
            return await call_next(request)
        if not request.session.get("auth"):
            # API calls get 401; page requests get redirect to login
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)
        return await call_next(request)

# Middleware order: SessionMiddleware runs first (parses cookie), then AuthMiddleware checks it
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.dashboard_secret_key, max_age=86400 * 30, https_only=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from typing import Callable, Awaitable

def _load_clips() -> dict:
    try:
        return {c["id"]: c for c in json.loads(_CLIPS_FILE.read_text())}
    except Exception:
        return {}

def _save_clips() -> None:
    try:
        _CLIPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CLIPS_FILE.write_text(json.dumps(list(_clips.values())))
    except Exception as exc:
        log.error("save_clips_failed", error=str(exc))

def _load_streams() -> dict:
    try:
        return {s["channel"]: s for s in json.loads(_STREAMS_FILE.read_text())}
    except Exception:
        return {}

def _save_streams() -> None:
    try:
        _STREAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STREAMS_FILE.write_text(json.dumps(list(_streams.values())))
    except Exception as exc:
        log.error("save_streams_failed", error=str(exc))

_clips: dict[str, dict] = _load_clips()
_streams: dict[str, dict] = _load_streams()
_ws_clients: set[WebSocket] = set()

# Callbacks set by main.py after Redis is ready
_publish_new_stream: Callable | None = None
_publish_remove_stream: Callable | None = None
_force_clip_cb: Callable | None = None


def set_stream_publisher(add_cb: Callable, remove_cb: Callable) -> None:
    global _publish_new_stream, _publish_remove_stream
    _publish_new_stream = add_cb
    _publish_remove_stream = remove_cb


def set_force_clip_callback(cb: Callable) -> None:
    global _force_clip_cb
    _force_clip_cb = cb


# ── WebSocket broadcast helper ────────────────────────────────────────────────

async def broadcast(event: dict) -> None:
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(json.dumps(event))
        except Exception as exc:
            log.debug("ws_send_failed", error=str(exc))
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ── Called by clip processor when a clip is ready ────────────────────────────

async def notify_clip_ready(clip: dict) -> None:
    _clips[clip["id"]] = clip
    _save_clips()
    await broadcast({"event": "clip_ready", "clip": clip})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/clips")
async def list_clips(status: str | None = None, channel: str | None = None):
    clips = list(_clips.values())
    if status:
        clips = [c for c in clips if c.get("status") == status]
    if channel:
        clips = [c for c in clips if c.get("channel") == channel]
    clips.sort(key=lambda c: c.get("created_at", 0), reverse=True)
    return clips


@app.delete("/clips")
async def clear_clips():
    _clips.clear()
    _save_clips()
    await broadcast({"event": "clips_cleared"})
    return {"status": "cleared"}


@app.get("/clips/{clip_id}")
async def get_clip(clip_id: str):
    clip = _clips.get(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip


@app.post("/clips/{clip_id}/approve")
async def approve_clip(clip_id: str):
    from src.profiles.manager import profile_manager
    clip = _clips.get(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip["status"] = "approved"
    _save_clips()
    await broadcast({"event": "clip_updated", "clip": clip})
    profile = await profile_manager.get(clip["channel"])
    if profile:
        profile.record_clip(approved=True, signals=clip.get("trigger_signals", []))
        await profile_manager.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()})
    return clip


@app.post("/clips/{clip_id}/reject")
async def reject_clip(clip_id: str):
    from src.profiles.manager import profile_manager
    clip = _clips.get(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip["status"] = "rejected"
    _save_clips()
    await broadcast({"event": "clip_updated", "clip": clip})
    profile = await profile_manager.get(clip["channel"])
    if profile:
        profile.record_clip(approved=False, signals=clip.get("trigger_signals", []))
        await profile_manager.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()})
    return clip


@app.get("/profiles")
async def list_profiles():
    from src.profiles.manager import profile_manager
    profiles = await profile_manager.all_profiles()
    return [p.to_dict() for p in profiles]


@app.get("/profiles/{channel}")
async def get_profile(channel: str):
    from src.profiles.manager import profile_manager
    profile = await profile_manager.get(channel)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.to_dict()


class StreamRequest(BaseModel):
    channel: str
    platform: str = "twitch"
    preset: str = "default"


@app.get("/streams")
async def list_streams():
    return list(_streams.values())


@app.post("/streams", status_code=201)
async def add_stream(req: StreamRequest):
    if req.channel in _streams:
        raise HTTPException(status_code=409, detail="Stream already registered")
    record = {"channel": req.channel, "platform": req.platform, "preset": req.preset, "status": "starting"}
    _streams[req.channel] = record
    _save_streams()
    await broadcast({"event": "stream_added", "stream": record})
    if _publish_new_stream:
        await _publish_new_stream(req.channel, req.platform, req.preset)
    return record


@app.delete("/streams/{channel}", status_code=204)
async def remove_stream(channel: str):
    if channel not in _streams:
        raise HTTPException(status_code=404, detail="Stream not found")
    del _streams[channel]
    _save_streams()
    await broadcast({"event": "stream_removed", "channel": channel})
    if _publish_remove_stream:
        await _publish_remove_stream(channel)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    log.info("ws_client_connected", total=len(_ws_clients))
    try:
        while True:
            await ws.receive_text()   # keep connection alive; client pings
    except WebSocketDisconnect:
        _ws_clients.discard(ws)
        log.info("ws_client_disconnected", total=len(_ws_clients))


@app.get("/clip-file")
async def serve_clip_file(path: str):
    allowed_root = (Path(settings.local_storage_path) / "clips").resolve()
    p = Path(path).resolve()
    if not str(p).startswith(str(allowed_root)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not p.exists() or p.suffix != ".mp4":
        raise HTTPException(status_code=404, detail="Clip file not found")
    return FileResponse(str(p), media_type="video/mp4")


@app.post("/streams/{channel}/force-clip")
async def force_clip(channel: str):
    if channel not in _streams:
        raise HTTPException(status_code=404, detail="Stream not registered")
    if not _force_clip_cb:
        raise HTTPException(status_code=503, detail="Force clip not ready")
    await _force_clip_cb(channel)
    return {"status": "queued", "channel": channel}


@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    err_html = f'<p class="error">{error}</p>' if error else ""
    return HTMLResponse(LOGIN_HTML.replace("{error}", err_html))


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    import secrets as _secrets
    if _secrets.compare_digest(password, settings.dashboard_password):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=Incorrect+password", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/health")
async def health():
    from src.main import SHARED_BUFFERS
    buffer_info = {}
    for channel, buf in SHARED_BUFFERS.items():
        segs = list(buf.buffer_dir.glob("seg*.ts")) if buf.buffer_dir.exists() else []
        buffer_info[channel] = {
            "running": buf._state.is_running,
            "segments": len(segs),
            "buffer_dir": str(buf.buffer_dir),
        }
    return {
        "status": "ok",
        "clips": len(_clips),
        "streams": len(_streams),
        "buffers": buffer_info,
    }


_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="hg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#c084fc"/>
      <stop offset="100%" stop-color="#7c3aed"/>
    </linearGradient>
    <linearGradient id="pg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#f0abfc"/>
      <stop offset="100%" stop-color="#e879f9"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="11" fill="#0d0d18"/>
  <rect x="8" y="8" width="8" height="32" rx="2" fill="url(#hg)"/>
  <rect x="16" y="18" width="16" height="12" rx="2" fill="url(#hg)"/>
  <rect x="32" y="8" width="8" height="12" rx="2" fill="url(#hg)"/>
  <polygon points="32,22 42,30 32,38" fill="url(#pg)"/>
</svg>"""


@app.get("/logo.svg")
async def logo_svg():
    from fastapi.responses import Response
    return Response(content=_LOGO_SVG, media_type="image/svg+xml")


@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/logo.svg")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz</title>
<link rel="icon" type="image/svg+xml" href="/logo.svg">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #08080d; color: #e8e8f0; font-family: 'Inter', system-ui, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .wrap { width: 360px; }
  .logo-area { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  .logo { font-size: 28px; font-weight: 700; background: linear-gradient(135deg, #a78bfa, #7c3aed); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -0.5px; }
  .tagline { font-size: 13px; color: #6b6b80; margin-bottom: 36px; }
  .card { background: #111118; border: 1px solid #1e1e2e; border-radius: 16px; padding: 32px; }
  label { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: #6b6b80; display: block; margin-bottom: 8px; }
  input[type=password] { width: 100%; background: #08080d; border: 1px solid #1e1e2e; border-radius: 10px; color: #e8e8f0; padding: 11px 14px; font-size: 14px; font-family: inherit; outline: none; transition: border-color .2s; }
  input[type=password]:focus { border-color: #7c3aed; }
  button { width: 100%; background: linear-gradient(135deg, #7c3aed, #5b21b6); color: #fff; border: none; border-radius: 10px; padding: 12px; font-size: 14px; font-weight: 600; font-family: inherit; cursor: pointer; margin-top: 20px; transition: opacity .15s; letter-spacing: .01em; }
  button:hover { opacity: .88; }
  .error { font-size: 12px; color: #f87171; background: #1f0a0a; border: 1px solid #3f1515; border-radius: 8px; padding: 10px 14px; margin-bottom: 20px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo-area">
    <img src="/logo.svg" width="48" height="48" alt="Highlightz logo">
    <div class="logo">Highlightz</div>
  </div>
  <div class="tagline">AI-powered stream highlight detection</div>
  <div class="card">
    {error}
    <form method="POST" action="/login">
      <label>Password</label>
      <input type="password" name="password" autofocus placeholder="Enter your password">
      <button type="submit">Sign in</button>
    </form>
  </div>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz</title>
<link rel="icon" type="image/svg+xml" href="/logo.svg">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #08080d;
    --surface: #0f0f17;
    --surface2: #14141e;
    --border: #1c1c2a;
    --border2: #232333;
    --text: #e2e2ef;
    --muted: #5a5a72;
    --accent: #7c3aed;
    --accent-light: #a78bfa;
    --green: #34d399;
    --yellow: #fbbf24;
    --red: #f87171;
  }

  body { background: var(--bg); color: var(--text); font-family: 'Inter', system-ui, sans-serif; min-height: 100vh; overflow: hidden; font-size: 13px; }

  /* ── Header ── */
  header {
    height: 52px; background: var(--surface); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 20px; gap: 16px; position: relative; z-index: 10;
  }
  .logo { font-size: 16px; font-weight: 700; background: linear-gradient(135deg, var(--accent-light), var(--accent)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -0.3px; margin-right: 4px; }
  .hbadge { display: flex; align-items: center; gap: 6px; background: var(--surface2); border: 1px solid var(--border2); border-radius: 20px; padding: 4px 10px; font-size: 11px; color: var(--muted); }
  .hbadge.live { color: var(--green); border-color: #1a3a2e; background: #0d1f18; }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; flex-shrink: 0; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .hspacer { flex: 1; }
  .signout { font-size: 12px; color: var(--muted); text-decoration: none; padding: 5px 12px; background: var(--surface2); border: 1px solid var(--border2); border-radius: 8px; transition: color .15s; }
  .signout:hover { color: var(--text); }

  /* ── Layout ── */
  .layout { display: grid; grid-template-columns: 280px 1fr; height: calc(100vh - 52px); }

  /* ── Sidebar ── */
  aside { background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }

  .sb-top { padding: 16px; flex-shrink: 0; }
  .sb-label { font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }

  .input-row { display: flex; gap: 6px; margin-bottom: 8px; }
  .sb-input { flex: 1; background: var(--bg); border: 1px solid var(--border2); border-radius: 8px; color: var(--text); padding: 8px 11px; font-size: 12px; font-family: inherit; outline: none; transition: border-color .2s; min-width: 0; }
  .sb-input:focus { border-color: var(--accent); }
  .sb-select { background: var(--bg); border: 1px solid var(--border2); border-radius: 8px; color: var(--text); padding: 8px 8px; font-size: 12px; font-family: inherit; outline: none; cursor: pointer; }

  .btn { border: none; border-radius: 8px; padding: 8px 14px; font-size: 12px; font-weight: 600; font-family: inherit; cursor: pointer; transition: opacity .15s; white-space: nowrap; }
  .btn:hover { opacity: .85; }
  .btn-primary { background: linear-gradient(135deg, var(--accent), #5b21b6); color: #fff; }
  .btn-orange { background: #c2410c; color: #fff; }
  .btn-green { background: #059669; color: #fff; }
  .btn-red { background: #b91c1c; color: #fff; }
  .btn-sm { padding: 5px 10px; font-size: 11px; border-radius: 6px; }
  .btn-ghost { background: var(--surface2); border: 1px solid var(--border2); color: var(--muted); }
  .btn-ghost:hover { color: var(--text); border-color: var(--muted); }

  .divider { height: 1px; background: var(--border); margin: 12px 0; }

  /* Stream list */
  .sb-streams-label { padding: 0 16px 8px; font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); flex-shrink: 0; }
  .stream-list { flex: 1; overflow-y: auto; padding: 0 10px 10px; }

  .stream-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 8px; }
  .stream-card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
  .stream-name { font-size: 13px; font-weight: 600; color: var(--text); line-height: 1.2; }
  .stream-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .stream-actions { display: flex; gap: 5px; flex-shrink: 0; }
  .icon-btn { background: none; border: none; color: var(--muted); cursor: pointer; width: 26px; height: 26px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; transition: all .15s; }
  .icon-btn:hover { background: var(--border); color: var(--red); }

  .score-section { margin-bottom: 6px; }
  .score-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
  .score-label-text { font-size: 10px; color: var(--muted); }
  .score-val { font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text); }
  .score-track { background: var(--bg); border-radius: 99px; height: 4px; overflow: hidden; }
  .score-fill { height: 100%; border-radius: 99px; transition: width .5s ease, background .5s ease; width: 0%; background: var(--accent-light); }
  .breakdown-row { display: flex; gap: 3px; margin-top: 6px; flex-wrap: wrap; }
  .chip { font-size: 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px; color: var(--muted); font-variant-numeric: tabular-nums; }

  .profile-section { background: var(--bg); border-radius: 7px; padding: 8px 10px; margin-top: 8px; }
  .profile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 8px; }
  .profile-stat { display: flex; flex-direction: column; }
  .profile-stat-label { font-size: 9px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
  .profile-stat-val { font-size: 12px; font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; margin-top: 1px; }
  .learn-badge { font-size: 10px; color: var(--accent-light); margin-top: 6px; text-align: center; }

  .empty-state { text-align: center; color: var(--muted); padding: 32px 16px; line-height: 1.6; }

  /* ── Clips panel ── */
  .clips-panel { display: flex; flex-direction: column; overflow: hidden; background: var(--bg); }

  .clips-header { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .clips-title { font-size: 14px; font-weight: 600; color: var(--text); }
  .hspacer { flex: 1; }
  .filter-group { display: flex; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .filter-btn { background: none; border: none; color: var(--muted); padding: 5px 13px; font-size: 11px; font-weight: 500; font-family: inherit; cursor: pointer; transition: all .15s; }
  .filter-btn.active { background: var(--accent); color: #fff; }
  .filter-btn:hover:not(.active) { color: var(--text); }

  .clips-grid { flex: 1; overflow-y: auto; padding: 18px 20px; display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; align-content: start; }

  .clip-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; transition: border-color .2s, transform .2s; }
  .clip-card:hover { border-color: var(--border2); transform: translateY(-1px); }

  .clip-thumb { width: 100%; aspect-ratio: 16/9; background: var(--surface2); position: relative; display: flex; align-items: center; justify-content: center; color: var(--border2); overflow: hidden; }
  .clip-thumb video { width: 100%; height: 100%; object-fit: cover; display: block; }
  .clip-thumb-placeholder { display: flex; flex-direction: column; align-items: center; gap: 8px; color: var(--muted); }
  .clip-thumb-placeholder svg { opacity: .4; }

  .score-pill { position: absolute; top: 8px; right: 8px; border-radius: 20px; padding: 3px 9px; font-size: 11px; font-weight: 700; backdrop-filter: blur(8px); }
  .score-pill.high { background: #052a18cc; color: var(--green); }
  .score-pill.med { background: #2a1a05cc; color: var(--yellow); }
  .score-pill.low { background: #00000099; color: var(--muted); }

  .clip-body { padding: 12px 14px; }
  .clip-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .clip-channel { font-size: 13px; font-weight: 600; color: var(--accent-light); }
  .clip-title { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
  .status-pill { border-radius: 20px; padding: 3px 9px; font-size: 10px; font-weight: 600; flex-shrink: 0; }
  .status-pill.pending { background: #2a200a; color: var(--yellow); }
  .status-pill.approved { background: #052a18; color: var(--green); }
  .status-pill.rejected { background: #2a0a0a; color: var(--red); }

  .clip-tags { display: flex; gap: 5px; margin: 8px 0; flex-wrap: wrap; }
  .tag { background: var(--surface2); border: 1px solid var(--border); border-radius: 5px; padding: 2px 7px; font-size: 11px; color: var(--muted); }

  .clip-actions { display: flex; gap: 7px; margin-top: 10px; }

  /* ── Toast ── */
  .toast { position: fixed; bottom: 20px; right: 20px; border-radius: 10px; padding: 11px 16px; font-size: 12px; font-weight: 500; transform: translateY(60px); opacity: 0; transition: all .25s cubic-bezier(.34,1.56,.64,1); z-index: 999; max-width: 320px; }
  .toast.show { transform: translateY(0); opacity: 1; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 99px; }
</style>
</head>
<body>

<header>
  <img src="/logo.svg" width="28" height="28" alt="" style="border-radius:7px;flex-shrink:0">
  <span class="logo">Highlightz</span>
  <span class="hbadge live"><span class="dot"></span>Live</span>
  <span class="hbadge" id="clip-count">0 clips</span>
  <span class="hbadge" id="stream-count">0 streams</span>
  <span class="hspacer"></span>
  <a href="/logout" class="signout">Sign out</a>
</header>

<div class="layout">
  <aside>
    <div class="sb-top">
      <div class="sb-label">Add Stream</div>
      <div class="input-row">
        <input class="sb-input" id="channel-input" placeholder="Channel name" onkeydown="if(event.key==='Enter')addStream()">
        <select class="sb-select" id="preset-select">
          <option value="default">Default</option>
          <option value="fps">FPS</option>
          <option value="chess">Chess</option>
          <option value="irl">IRL</option>
        </select>
      </div>
      <button class="btn btn-primary" style="width:100%" onclick="addStream()">Monitor Stream</button>
    </div>

    <div class="sb-streams-label">Monitored Streams</div>
    <div class="stream-list" id="stream-list">
      <div class="empty-state">No streams yet.<br>Add one above to start.</div>
    </div>
  </aside>

  <div class="clips-panel">
    <div class="clips-header">
      <span class="clips-title">Clips</span>
      <span class="hspacer"></span>
      <button class="btn btn-ghost btn-sm" onclick="clearClips()" style="margin-right:8px">Clear All</button>
      <div class="filter-group">
        <button class="filter-btn active" onclick="setFilter('all',this)">All</button>
        <button class="filter-btn" onclick="setFilter('pending',this)">Pending</button>
        <button class="filter-btn" onclick="setFilter('approved',this)">Approved</button>
        <button class="filter-btn" onclick="setFilter('rejected',this)">Rejected</button>
      </div>
    </div>
    <div class="clips-grid" id="clips-grid">
      <div id="clips-empty" style="grid-column:1/-1;padding:80px 0;text-align:center;color:var(--muted)">
        <div style="font-size:36px;margin-bottom:14px;opacity:.3">*</div>
        <div style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:6px">Waiting for clips</div>
        <div style="font-size:13px">Add a live Twitch stream to start monitoring</div>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let allClips = {}, streams = {}, currentFilter = 'all', ws;

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.event === 'clip_ready' || msg.event === 'clip_updated') {
      allClips[msg.clip.id] = msg.clip;
      renderClips();
      if (msg.event === 'clip_ready') toast('New clip from ' + msg.clip.channel);
    }
    if (msg.event === 'clips_cleared') { allClips = {}; renderClips(); toast('All clips cleared'); }
    if (msg.event === 'stream_added' || msg.event === 'stream_updated') { streams[msg.stream.channel] = msg.stream; renderStreams(); }
    if (msg.event === 'stream_removed') { delete streams[msg.channel]; renderStreams(); }
    if (msg.event === 'score_update') updateScore(msg.channel, msg.score, msg.breakdown);
    if (msg.event === 'profile_updated') updateProfilePanel(msg.profile);
    if (msg.event === 'clip_error') toast(msg.error, true);
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
  setInterval(() => ws && ws.readyState === 1 && ws.send('ping'), 30000);
}

async function loadInitial() {
  const [cr, sr] = await Promise.all([fetch('/clips'), fetch('/streams')]);
  (await cr.json()).forEach(c => allClips[c.id] = c);
  (await sr.json()).forEach(s => streams[s.channel] = s);
  renderClips(); renderStreams();
}

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderClips();
}

function renderClips() {
  const grid = document.getElementById('clips-grid');
  const empty = document.getElementById('clips-empty');
  let clips = Object.values(allClips);
  if (currentFilter !== 'all') clips = clips.filter(c => c.status === currentFilter);
  clips.sort((a,b) => b.created_at - a.created_at);
  document.getElementById('clip-count').textContent = Object.keys(allClips).length + ' clips';

  grid.querySelectorAll('.clip-card').forEach(el => el.remove());

  if (!clips.length) { if(empty) empty.style.display = ''; return; }
  if(empty) empty.style.display = 'none';

  clips.forEach(clip => grid.insertAdjacentHTML('beforeend', clipCard(clip)));
}

function scoreClass(s) { return s >= 75 ? 'high' : s >= 50 ? 'med' : 'low'; }

function clipCard(c) {
  const score = Math.round(c.trigger_score);
  const date = new Date(c.created_at * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  const dur = c.duration_seconds ? Math.round(c.duration_seconds) + 's' : '';
  const hasVideo = c.storage_url && !c.storage_url.startsWith('http');
  const media = hasVideo
    ? `<video src="/clip-file?path=${encodeURIComponent(c.storage_url)}" controls preload="none"></video>`
    : `<div class="clip-thumb-placeholder"><svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg><span style="font-size:12px">${c.channel}</span></div>`;

  return `<div class="clip-card" data-id="${c.id}">
    <div class="clip-thumb">
      ${media}
      <span class="score-pill ${scoreClass(c.trigger_score)}">${score}</span>
    </div>
    <div class="clip-body">
      <div class="clip-top">
        <div>
          <div class="clip-channel">${c.channel}</div>
          <div class="clip-title">${c.stream_title || 'Live Stream'}</div>
        </div>
        <span class="status-pill ${c.status}" id="status-${c.id}">${c.status}</span>
      </div>
      <div class="clip-tags">
        <span class="tag">${date}</span>
        ${dur ? `<span class="tag">${dur}</span>` : ''}
        ${c.game ? `<span class="tag">${c.game}</span>` : ''}
      </div>
      <div class="clip-actions" id="actions-${c.id}">${actionButtons(c)}</div>
    </div>
  </div>`;
}

function actionButtons(c) {
  if (c.status !== 'pending') return `<span style="font-size:11px;color:var(--muted)">${c.status === 'approved' ? '✓ Approved' : '✗ Rejected'}</span>`;
  return `<button class="btn btn-green btn-sm" onclick="setStatus('${c.id}','approve')">Approve</button>
          <button class="btn btn-red btn-sm" onclick="setStatus('${c.id}','reject')">Reject</button>`;
}

async function setStatus(id, action) {
  await fetch(`/clips/${id}/${action}`, {method:'POST'});
  const r = await fetch(`/clips/${id}`);
  allClips[id] = await r.json();
  renderClips();
}

function renderStreams() {
  const list = document.getElementById('stream-list');
  const arr = Object.values(streams);
  document.getElementById('stream-count').textContent = arr.length + ' stream' + (arr.length !== 1 ? 's' : '');
  if (!arr.length) { list.innerHTML = '<div class="empty-state">No streams yet.<br>Add one above to start.</div>'; return; }

  const existing = new Set([...list.querySelectorAll('.stream-card')].map(el => el.dataset.channel));
  arr.forEach(s => {
    if (existing.has(s.channel)) {
      const el = list.querySelector(`[data-channel="${s.channel}"] .stream-meta`);
      if (el) el.innerHTML = statusDot(s.status) + s.platform + ' - ' + s.preset;
      return;
    }
    list.querySelector('.empty-state')?.remove();
    const div = document.createElement('div');
    div.className = 'stream-card'; div.dataset.channel = s.channel;
    div.innerHTML = `
      <div class="stream-card-top">
        <div style="min-width:0">
          <div class="stream-name">${s.channel}</div>
          <div class="stream-meta">${statusDot(s.status)}${s.platform} - ${s.preset}</div>
        </div>
        <div class="stream-actions">
          <button class="btn btn-orange btn-sm" onclick="forceClip('${s.channel}')">Test</button>
          <button class="icon-btn" onclick="removeStream('${s.channel}')" title="Remove">✕</button>
        </div>
      </div>
      <div class="score-section">
        <div class="score-header">
          <span class="score-label-text">Trigger Score</span>
          <span class="score-val" id="sv-${s.channel}">0</span>
        </div>
        <div class="score-track"><div class="score-fill" id="sf-${s.channel}"></div></div>
        <div class="breakdown-row" id="bd-${s.channel}"></div>
      </div>
      <div class="profile-section" id="profile-${s.channel}">
        <div class="profile-grid">
          <div class="profile-stat"><span class="profile-stat-label">Threshold</span><span class="profile-stat-val p-threshold">-</span></div>
          <div class="profile-stat"><span class="profile-stat-label">Velocity</span><span class="profile-stat-val p-velocity">-</span></div>
          <div class="profile-stat"><span class="profile-stat-label">Clips</span><span class="profile-stat-val p-clips">-</span></div>
          <div class="profile-stat"><span class="profile-stat-label">Approval</span><span class="profile-stat-val p-approval">-</span></div>
        </div>
        <div class="learn-badge p-learning">Learning baseline...</div>
      </div>`;
    list.appendChild(div);
  });

  list.querySelectorAll('.stream-card').forEach(el => { if (!streams[el.dataset.channel]) el.remove(); });
  if (!list.querySelector('.stream-card')) list.innerHTML = '<div class="empty-state">No streams yet.<br>Add one above to start.</div>';
}

function statusDot(status) {
  const color = status === 'live' ? 'var(--green)' : status === 'starting' ? 'var(--yellow)' : 'var(--muted)';
  return `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${color};margin-right:5px;vertical-align:middle"></span>`;
}

function updateScore(channel, score, breakdown) {
  const fill = document.getElementById(`sf-${channel}`);
  const val = document.getElementById(`sv-${channel}`);
  const brow = document.getElementById(`bd-${channel}`);
  if (!fill) return;

  fill.style.width = score + '%';
  fill.style.background = score >= 75 ? 'var(--green)' : score >= 50 ? 'var(--yellow)' : 'var(--accent-light)';
  val.textContent = score.toFixed(1);
  val.style.color = score >= 75 ? 'var(--green)' : score >= 50 ? 'var(--yellow)' : 'var(--text)';

  if (breakdown && brow) {
    brow.innerHTML = Object.entries(breakdown)
      .filter(([,v]) => v > 0.05)
      .map(([k,v]) => `<span class="chip">${k.replace('chat_','').replace('_',' ')}: ${v.toFixed(2)}</span>`)
      .join('');
  }
}

async function addStream() {
  const ch = document.getElementById('channel-input').value.trim();
  const preset = document.getElementById('preset-select').value;
  if (!ch) return;
  await fetch('/streams', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({channel:ch, platform:'twitch', preset})});
  document.getElementById('channel-input').value = '';
  toast('Now monitoring ' + ch);
}

async function clearClips() {
  if (!confirm('Clear all clips? This cannot be undone.')) return;
  await fetch('/clips', {method:'DELETE'});
}

async function removeStream(channel) {
  await fetch(`/streams/${channel}`, {method:'DELETE'});
  delete streams[channel];
  renderStreams();
}

async function forceClip(channel) {
  const res = await fetch(`/streams/${channel}/force-clip`, {method:'POST'});
  toast(res.ok ? 'Test clip queued for ' + channel : 'Stream not active - start monitoring first', !res.ok);
}

function toast(msg, isError = false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = isError ? '#7f1d1d' : '#1e1b4b';
  t.style.border = `1px solid ${isError ? '#991b1b' : '#3730a3'}`;
  t.style.color = isError ? '#fca5a5' : '#c4b5fd';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), isError ? 6000 : 3000);
}

function updateProfilePanel(p) {
  const panel = document.getElementById(`profile-${p.channel}`);
  if (!panel) return;
  panel.querySelector('.p-threshold').textContent = p.trigger_threshold?.toFixed(1) ?? '-';
  panel.querySelector('.p-velocity').textContent = p.avg_velocity > 0 ? p.avg_velocity.toFixed(2) + '/s' : '-';
  panel.querySelector('.p-clips').textContent = p.total_clips ?? 0;
  const ar = p.approval_rate;
  const arEl = panel.querySelector('.p-approval');
  arEl.textContent = p.total_clips > 0 ? Math.round(ar * 100) + '%' : '-';
  arEl.style.color = ar >= 0.7 ? 'var(--green)' : ar >= 0.4 ? 'var(--yellow)' : 'var(--red)';
  const learning = panel.querySelector('.p-learning');
  const samples = p.velocity_samples ?? 0;
  if (samples >= 10) { learning.textContent = 'Calibrated - ' + samples + ' samples'; learning.style.color = 'var(--green)'; }
  else { learning.textContent = 'Learning... ' + samples + '/10 samples'; learning.style.color = 'var(--accent-light)'; }
}

async function loadProfiles() {
  const profiles = await (await fetch('/profiles')).json();
  profiles.forEach(updateProfilePanel);
}

loadInitial(); loadProfiles(); connectWS();
</script>
</body>
</html>"""
