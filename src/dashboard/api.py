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

_OPEN_PATHS = {"/login", "/health", "/ws", "/favicon.ico"}

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
    _CLIPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CLIPS_FILE.write_text(json.dumps(list(_clips.values())))

def _load_streams() -> dict:
    try:
        return {s["channel"]: s for s in json.loads(_STREAMS_FILE.read_text())}
    except Exception:
        return {}

def _save_streams() -> None:
    _STREAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STREAMS_FILE.write_text(json.dumps(list(_streams.values())))

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
        except Exception:
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
    p = Path(path)
    if not p.exists() or not p.suffix == ".mp4":
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
    return {"status": "ok", "clips": len(_clips), "streams": len(_streams)}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz - Sign In</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0e0e10; color: #efeff1; font-family: Inter, system-ui, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: #1f1f23; border: 1px solid #2d2d35; border-radius: 12px; padding: 40px; width: 340px; }
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
  <h1>Highlightz</h1>
  <p class="sub">Sign in to your dashboard</p>
  {error}
  <form method="POST" action="/login">
    <label>Password</label>
    <input type="password" name="password" autofocus placeholder="Enter your password">
    <button type="submit">Sign In</button>
  </form>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Highlightz</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0e0e10; color: #efeff1; font-family: Inter, system-ui, sans-serif; min-height: 100vh; }
  header { background: #1f1f23; border-bottom: 1px solid #2d2d35; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; font-weight: 700; color: #bf94ff; }
  .badge { background: #26262c; border-radius: 20px; padding: 4px 12px; font-size: 12px; color: #adadb8; }
  .badge.live { background: #1a3a2a; color: #00c853; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #00c853; display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  main { display: grid; grid-template-columns: 300px 1fr; gap: 0; height: calc(100vh - 57px); }

  /* Sidebar */
  aside { background: #1a1a1f; border-right: 1px solid #2d2d35; display: flex; flex-direction: column; overflow: hidden; }
  .sidebar-section { padding: 16px; border-bottom: 1px solid #2d2d35; }
  .sidebar-section h2 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: #adadb8; margin-bottom: 12px; }
  .add-stream { display: flex; gap: 8px; }
  .add-stream input { flex: 1; background: #26262c; border: 1px solid #3a3a44; border-radius: 6px; color: #efeff1; padding: 8px 10px; font-size: 13px; outline: none; }
  .add-stream input:focus { border-color: #bf94ff; }
  .add-stream select { background: #26262c; border: 1px solid #3a3a44; border-radius: 6px; color: #efeff1; padding: 8px 8px; font-size: 13px; outline: none; }
  .btn { background: #bf94ff; color: #0e0e10; border: none; border-radius: 6px; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; transition: background .15s; white-space: nowrap; }
  .btn:hover { background: #a970ff; }
  .btn.danger { background: #eb0400; color: #fff; }
  .btn.danger:hover { background: #c0302e; }
  .btn.approve { background: #00c853; color: #000; }
  .btn.approve:hover { background: #00a844; }
  .btn.sm { padding: 5px 10px; font-size: 12px; }

  .stream-list { flex: 1; overflow-y: auto; padding: 8px; }
  .stream-item { padding: 10px 12px; border-radius: 8px; margin-bottom: 6px; background: #26262c; }
  .stream-item-header { display: flex; align-items: center; justify-content: space-between; }
  .stream-item .name { font-size: 13px; font-weight: 600; }
  .stream-item .meta { font-size: 11px; color: #adadb8; margin-top: 2px; }
  .stream-item .remove { background: none; border: none; color: #adadb8; cursor: pointer; font-size: 16px; padding: 2px 6px; border-radius: 4px; }
  .stream-item .remove:hover { color: #eb0400; background: #2d2d35; }
  .score-bar-wrap { margin-top: 8px; }
  .score-label { display: flex; justify-content: space-between; font-size: 11px; color: #adadb8; margin-bottom: 4px; }
  .score-label .score-val { font-weight: 700; color: #efeff1; font-variant-numeric: tabular-nums; }
  .score-track { background: #1a1a1f; border-radius: 4px; height: 6px; overflow: hidden; }
  .score-fill { height: 100%; border-radius: 4px; transition: width .4s ease, background .4s ease; width: 0%; background: #bf94ff; }
  .breakdown-row { display: flex; gap: 4px; margin-top: 5px; flex-wrap: wrap; }
  .breakdown-chip { font-size: 10px; background: #1a1a1f; border-radius: 3px; padding: 2px 6px; color: #adadb8; font-variant-numeric: tabular-nums; }
  .profile-panel { margin-top: 8px; background: #1a1a1f; border-radius: 6px; padding: 8px 10px; }
  .profile-row { display: flex; justify-content: space-between; font-size: 11px; color: #adadb8; margin-bottom: 3px; }
  .profile-row span:last-child { color: #efeff1; font-weight: 600; font-variant-numeric: tabular-nums; }
  .profile-learning { font-size: 10px; color: #bf94ff; margin-top: 4px; text-align: center; }
  .empty-state { text-align: center; color: #adadb8; font-size: 13px; padding: 24px; }

  /* Clips panel */
  .clips-panel { display: flex; flex-direction: column; overflow: hidden; }
  .clips-toolbar { padding: 16px 20px; border-bottom: 1px solid #2d2d35; display: flex; align-items: center; gap: 12px; }
  .clips-toolbar h2 { font-size: 15px; font-weight: 700; flex: 1; }
  .filter-btn { background: #26262c; border: 1px solid #3a3a44; border-radius: 20px; color: #adadb8; padding: 5px 14px; font-size: 12px; cursor: pointer; transition: all .15s; }
  .filter-btn.active, .filter-btn:hover { background: #bf94ff22; border-color: #bf94ff; color: #bf94ff; }
  .clips-grid { flex: 1; overflow-y: auto; padding: 16px 20px; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; align-content: start; }

  .clip-card { background: #1f1f23; border: 1px solid #2d2d35; border-radius: 10px; overflow: hidden; transition: border-color .15s; }
  .clip-card:hover { border-color: #bf94ff55; }
  .clip-video { width: 100%; aspect-ratio: 16/9; background: #0e0e10; display: flex; align-items: center; justify-content: center; color: #3a3a44; font-size: 13px; position: relative; }
  .clip-video video { width: 100%; height: 100%; object-fit: cover; }
  .clip-score { position: absolute; top: 8px; right: 8px; background: #0009; border-radius: 20px; padding: 3px 10px; font-size: 11px; font-weight: 700; }
  .clip-score.high { color: #00c853; }
  .clip-score.med { color: #ffb300; }
  .clip-score.low { color: #adadb8; }
  .clip-body { padding: 12px; }
  .clip-channel { font-size: 13px; font-weight: 700; color: #bf94ff; }
  .clip-title { font-size: 12px; color: #adadb8; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .clip-meta { display: flex; gap: 8px; margin-top: 8px; font-size: 11px; color: #adadb8; flex-wrap: wrap; }
  .clip-tag { background: #26262c; border-radius: 4px; padding: 2px 7px; }
  .clip-actions { display: flex; gap: 8px; margin-top: 12px; }
  .clip-status { display: inline-block; border-radius: 20px; padding: 3px 10px; font-size: 11px; font-weight: 600; }
  .clip-status.pending { background: #2a2a16; color: #ffb300; }
  .clip-status.approved { background: #1a3a2a; color: #00c853; }
  .clip-status.rejected { background: #3a1a1a; color: #eb0400; }

  .toast { position: fixed; bottom: 24px; right: 24px; background: #1f1f23; border: 1px solid #bf94ff55; border-radius: 8px; padding: 12px 18px; font-size: 13px; color: #efeff1; transform: translateY(80px); opacity: 0; transition: all .3s; z-index: 999; }
  .toast.show { transform: translateY(0); opacity: 1; }

  ::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-track { background: transparent; } ::-webkit-scrollbar-thumb { background: #3a3a44; border-radius: 3px; }
</style>
</head>
<body>

<header>
  <h1>Highlightz</h1>
  <span class="badge live"><span class="status-dot"></span>Connected</span>
  <span class="badge" id="clip-count">0 clips</span>
  <span class="badge" id="stream-count">0 streams</span>
  <a href="/logout" style="margin-left:auto;font-size:12px;color:#adadb8;text-decoration:none;padding:5px 12px;background:#26262c;border-radius:6px;border:1px solid #3a3a44">Sign Out</a>
</header>

<main>
  <aside>
    <div class="sidebar-section">
      <h2>Add Stream</h2>
      <div class="add-stream">
        <input id="channel-input" placeholder="channel name" onkeydown="if(event.key==='Enter')addStream()">
        <select id="preset-select">
          <option value="default">Default</option>
          <option value="fps">FPS</option>
          <option value="chess">Chess</option>
          <option value="irl">IRL</option>
        </select>
        <button class="btn" onclick="addStream()">+</button>
      </div>
    </div>
    <div class="sidebar-section" style="flex:0 0 auto">
      <h2>Test Clip</h2>
      <div class="add-stream">
        <input id="force-channel-input" placeholder="channel name">
        <button class="btn" onclick="forceClipFromInput()" style="background:#e05a00;white-space:nowrap">Force Clip</button>
      </div>
    </div>
    <div class="sidebar-section" style="flex:0 0 auto">
      <h2>Monitored Streams</h2>
    </div>
    <div class="stream-list" id="stream-list">
      <div class="empty-state">No streams yet.<br>Add one above.</div>
    </div>
  </aside>

  <div class="clips-panel">
    <div class="clips-toolbar">
      <h2>Clips</h2>
      <button class="filter-btn active" onclick="setFilter('all', this)">All</button>
      <button class="filter-btn" onclick="setFilter('pending', this)">Pending</button>
      <button class="filter-btn" onclick="setFilter('approved', this)">Approved</button>
      <button class="filter-btn" onclick="setFilter('rejected', this)">Rejected</button>
    </div>
    <div class="clips-grid" id="clips-grid">
      <div class="empty-state" id="clips-empty" style="grid-column:1/-1;padding:60px 0">
        <div style="font-size:40px;margin-bottom:12px">--</div>
        <div style="font-size:15px;font-weight:600;margin-bottom:6px">Waiting for clips</div>
        <div>Add a live Twitch stream to start monitoring</div>
      </div>
    </div>
  </div>
</main>

<div class="toast" id="toast"></div>

<script>
let allClips = {};
let streams = {};
let currentFilter = 'all';
let ws;

function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.event === 'clip_ready' || msg.event === 'clip_updated') {
      allClips[msg.clip.id] = msg.clip;
      renderClips();
      if (msg.event === 'clip_ready') toast('New clip from ' + msg.clip.channel);
    }
    if (msg.event === 'stream_added' || msg.event === 'stream_updated') {
      streams[msg.stream.channel] = msg.stream; renderStreams();
    }
    if (msg.event === 'stream_removed') { delete streams[msg.channel]; renderStreams(); }
    if (msg.event === 'score_update') updateScore(msg.channel, msg.score, msg.breakdown);
    if (msg.event === 'profile_updated') updateProfilePanel(msg.profile);
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
  setInterval(() => ws.readyState === 1 && ws.send('ping'), 30000);
}

async function loadInitial() {
  const [clipsRes, streamsRes] = await Promise.all([fetch('/clips'), fetch('/streams')]);
  const clipsArr = await clipsRes.json();
  const streamsArr = await streamsRes.json();
  clipsArr.forEach(c => allClips[c.id] = c);
  streamsArr.forEach(s => streams[s.channel] = s);
  renderClips();
  renderStreams();
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

  const existing = new Set([...grid.querySelectorAll('.clip-card')].map(el => el.dataset.id));
  const incoming = new Set(clips.map(c => c.id));

  // Remove cards no longer visible
  grid.querySelectorAll('.clip-card').forEach(el => { if (!incoming.has(el.dataset.id)) el.remove(); });

  if (clips.length === 0) { empty && (empty.style.display = ''); return; }
  empty && (empty.style.display = 'none');

  clips.forEach(clip => {
    if (existing.has(clip.id) && incoming.has(clip.id)) {
      const card = grid.querySelector(`[data-id="${clip.id}"]`);
      if (card) updateCardStatus(card, clip);
      return;
    }
    grid.insertAdjacentHTML('afterbegin', clipCard(clip));
  });
}

function scoreClass(s) { return s >= 75 ? 'high' : s >= 50 ? 'med' : 'low'; }

function clipCard(c) {
  const score = Math.round(c.trigger_score);
  const date = new Date(c.created_at * 1000).toLocaleTimeString();
  const dur = c.duration_seconds ? Math.round(c.duration_seconds) + 's' : '';
  const media = c.storage_url && !c.storage_url.startsWith('http')
    ? `<video src="/clip-file?path=${encodeURIComponent(c.storage_url)}" controls></video>`
    : `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#3a3a44">▶ ${c.channel}</div>`;

  return `<div class="clip-card" data-id="${c.id}">
    <div class="clip-video">
      ${media}
      <span class="clip-score ${scoreClass(c.trigger_score)}">${score}%</span>
    </div>
    <div class="clip-body">
      <div class="clip-channel">${c.channel}</div>
      <div class="clip-title">${c.stream_title || 'Live Stream'}</div>
      <div class="clip-meta">
        <span class="clip-tag">${date}</span>
        ${dur ? `<span class="clip-tag">${dur}</span>` : ''}
        ${c.game ? `<span class="clip-tag">${c.game}</span>` : ''}
        <span class="clip-status ${c.status}" id="status-${c.id}">${c.status}</span>
      </div>
      <div class="clip-actions" id="actions-${c.id}">
        ${actionButtons(c)}
      </div>
    </div>
  </div>`;
}

function actionButtons(c) {
  if (c.status === 'pending') return `
    <button class="btn approve sm" onclick="setStatus('${c.id}','approve')">✓ Approve</button>
    <button class="btn danger sm" onclick="setStatus('${c.id}','reject')">✗ Reject</button>`;
  return `<span style="font-size:12px;color:#adadb8">${c.status === 'approved' ? '✓ Approved' : '✗ Rejected'}</span>`;
}

function updateCardStatus(card, clip) {
  const s = card.querySelector(`#status-${clip.id}`);
  if (s) { s.className = 'clip-status ' + clip.status; s.textContent = clip.status; }
  const a = card.querySelector(`#actions-${clip.id}`);
  if (a) a.innerHTML = actionButtons(clip);
}

async function setStatus(id, action) {
  await fetch(`/clips/${id}/${action}`, { method: 'POST' });
  const res = await fetch(`/clips/${id}`);
  allClips[id] = await res.json();
  renderClips();
}

function renderStreams() {
  const list = document.getElementById('stream-list');
  const arr = Object.values(streams);
  document.getElementById('stream-count').textContent = arr.length + ' streams';
  if (!arr.length) { list.innerHTML = '<div class="empty-state">No streams yet.<br>Add one above.</div>'; return; }

  // Only rebuild cards that are new; preserve existing ones so score bars animate smoothly
  const existing = new Set([...list.querySelectorAll('.stream-item')].map(el => el.dataset.channel));
  arr.forEach(s => {
    if (!existing.has(s.channel)) {
      list.querySelector('.empty-state') && list.querySelector('.empty-state').remove();
      const div = document.createElement('div');
      div.className = 'stream-item';
      div.dataset.channel = s.channel;
      div.innerHTML = `
        <div class="stream-item-header">
          <div>
            <div class="name">${s.channel}</div>
            <div class="meta">${s.platform} · ${s.preset} · <span class="s-status" style="color:#00c853">${s.status}</span></div>
          </div>
            <div style="display:flex;gap:6px;align-items:center">
            <button class="btn sm" onclick="forceClip('${s.channel}')" style="background:#e05a00;font-size:11px;padding:4px 8px">Test Clip</button>
            <button class="remove" onclick="removeStream('${s.channel}')" title="Remove">x</button>
          </div>
        </div>
        <div class="score-bar-wrap">
          <div class="score-label">
            <span>Trigger Score</span>
            <span class="score-val">0.000</span>
          </div>
          <div class="score-track"><div class="score-fill"></div></div>
          <div class="breakdown-row"></div>
        </div>
        <div class="profile-panel" id="profile-${s.channel}">
          <div class="profile-row"><span>Threshold</span><span class="p-threshold">—</span></div>
          <div class="profile-row"><span>Avg Velocity</span><span class="p-velocity">—</span></div>
          <div class="profile-row"><span>Clips</span><span class="p-clips">—</span></div>
          <div class="profile-row"><span>Approval Rate</span><span class="p-approval">—</span></div>
          <div class="p-weights" style="margin-top:5px;display:none"></div>
          <div class="profile-learning p-learning">Learning baseline...</div>
        </div>`;
      list.appendChild(div);
    } else {
      const card = list.querySelector(`[data-channel="${s.channel}"]`);
      if (card) card.querySelector('.s-status').textContent = s.status;
    }
  });

  // Remove cards for deleted streams
  list.querySelectorAll('.stream-item').forEach(el => {
    if (!streams[el.dataset.channel]) el.remove();
  });
  if (!list.querySelector('.stream-item')) {
    list.innerHTML = '<div class="empty-state">No streams yet.<br>Add one above.</div>';
  }
}

function updateScore(channel, score, breakdown) {
  const card = document.querySelector(`.stream-item[data-channel="${channel}"]`);
  if (!card) return;
  const fill = card.querySelector('.score-fill');
  const val = card.querySelector('.score-val');
  const brow = card.querySelector('.breakdown-row');

  fill.style.width = (score) + '%';
  fill.style.background = score >= 75 ? '#00c853' : score >= 50 ? '#ffb300' : '#bf94ff';
  val.textContent = score.toFixed(1);
  val.style.color = score >= 75 ? '#00c853' : score >= 50 ? '#ffb300' : '#efeff1';

  if (breakdown) {
    brow.innerHTML = Object.entries(breakdown).map(([k, v]) =>
      `<span class="breakdown-chip">${k.replace('SignalType.','')}: ${v.toFixed(2)}</span>`
    ).join('');
  }
}

async function addStream() {
  const ch = document.getElementById('channel-input').value.trim();
  const preset = document.getElementById('preset-select').value;
  if (!ch) return;
  await fetch('/streams', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({channel: ch, platform:'twitch', preset}) });
  document.getElementById('channel-input').value = '';
  toast('Monitoring ' + ch);
}

async function removeStream(channel) {
  await fetch(`/streams/${channel}`, { method: 'DELETE' });
  delete streams[channel];
  renderStreams();
}

async function forceClip(channel) {
  const res = await fetch(`/streams/${channel}/force-clip`, { method: 'POST' });
  if (res.ok) toast('Test clip queued for ' + channel);
  else toast('Failed to queue clip - is stream active?');
}

async function forceClipFromInput() {
  const ch = document.getElementById('force-channel-input').value.trim();
  if (!ch) return;
  await forceClip(ch);
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

function updateProfilePanel(p) {
  const panel = document.getElementById(`profile-${p.channel}`);
  if (!panel) return;
  panel.querySelector('.p-threshold').textContent = p.trigger_threshold?.toFixed(1) ?? '—';
  panel.querySelector('.p-velocity').textContent = p.avg_velocity > 0 ? p.avg_velocity.toFixed(2) + ' msg/s' : '—';
  panel.querySelector('.p-clips').textContent = p.total_clips ?? 0;
  const ar = p.approval_rate;
  const arEl = panel.querySelector('.p-approval');
  arEl.textContent = p.total_clips > 0 ? Math.round(ar * 100) + '%' : '—';
  arEl.style.color = ar >= 0.7 ? '#00c853' : ar >= 0.4 ? '#ffb300' : '#eb0400';

  // Show learned signal weights when any have drifted from 1.0
  const weightsEl = panel.querySelector('.p-weights');
  const sw = p.signal_weights;
  if (sw && p.total_clips > 0) {
    const labels = {CHAT_VELOCITY:'Vel',AUDIO_SPIKE:'Audio',KEYWORD:'KW',SENTIMENT:'Sent',VIEWER_SPIKE:'View',SILENCE_BURST:'Sil'};
    const chips = Object.entries(sw).map(([k, v]) => {
      const color = v > 1.1 ? '#00c853' : v < 0.9 ? '#eb0400' : '#adadb8';
      return `<span style="font-size:10px;background:#26262c;border-radius:3px;padding:2px 5px;color:${color}">${labels[k]||k}: ${v.toFixed(2)}</span>`;
    }).join('');
    weightsEl.innerHTML = '<div style="font-size:10px;color:#adadb8;margin-bottom:3px">Signal weights</div><div style="display:flex;flex-wrap:wrap;gap:3px">' + chips + '</div>';
    weightsEl.style.display = '';
  }

  const learning = panel.querySelector('.p-learning');
  const samples = p.velocity_samples ?? 0;
  if (samples >= 10) {
    learning.textContent = `Calibrated · ${samples} samples`;
    learning.style.color = '#00c853';
  } else {
    learning.textContent = `Learning baseline... (${samples}/10 samples)`;
    learning.style.color = '#bf94ff';
  }
}

async function loadProfiles() {
  const res = await fetch('/profiles');
  const profiles = await res.json();
  profiles.forEach(updateProfilePanel);
}

loadInitial();
loadProfiles();
connectWS();
</script>
</body>
</html>"""
