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
from html import escape as html_escape, unescape
from typing import Any, NamedTuple
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
from src.trigger import dismissed_suggestions as _dismissed
from src.billing import plans as _plans
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
                  "/landing/showcase", "/robots.txt", "/sitemap.xml", "/tutorial",
                  # A crawler-facing file behind a login is a crawler-facing
                  # file that does not exist. robots.txt and sitemap.xml are
                  # here for the same reason.
                  "/compare", "/llms.txt", "/llms-full.txt"}
# ── shared head tags for the secondary public pages ──────────────────────────

SITE_ORIGIN = "https://highlightz.app"
OG_CARD = SITE_ORIGIN + "/static/og-card-v6.png"

# Official Highlightz profiles on other sites, for schema.org sameAs. Each entry
# is a claim that this URL is the SAME organisation, so only put a profile here
# that is genuinely ours — a wrong one is worse than an empty list. Filling this
# in is what links the social accounts to the brand in a knowledge panel.
ORG_PROFILES: tuple[str, ...] = ()


def _social_head(path: str, title: str, desc: str) -> str:
    """Canonical URL and link-preview tags for a public page.

    WHY CANONICAL MATTERS MORE THAN IT LOOKS. Without it, `/tos`, `/tos/`,
    `www.highlightz.app/tos` and the http:// form are four separate documents
    to a crawler, and whatever ranking the page earns is split between them.
    The three marketing pages have always declared one; the legal pages and
    the opt-out page never did, which is the whole reason this exists.

    THE PREVIEW TAGS ARE NOT VANITY. The opt-out page is what a broadcaster
    lands on when they are annoyed and want off the service, and its link gets
    pasted into Discord. A bare grey link there reads as sketchy; the same link
    with a title and a card reads as a real company.

    ONE CARD IMAGE. Every page points at OG_CARD rather than carrying its own
    literal, because the three pages that had one had already drifted onto two
    different versions of the same picture.
    """
    t, d = html_escape(title, quote=True), html_escape(desc, quote=True)
    url = SITE_ORIGIN + path
    return (
        '<link rel="canonical" href="' + url + '">\n'
        '<meta property="og:type" content="website">\n'
        '<meta property="og:site_name" content="Highlightz">\n'
        '<meta property="og:url" content="' + url + '">\n'
        '<meta property="og:title" content="' + t + '">\n'
        '<meta property="og:description" content="' + d + '">\n'
        '<meta property="og:image" content="' + OG_CARD + '">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        '<meta name="twitter:title" content="' + t + '">\n'
        '<meta name="twitter:description" content="' + d + '">\n'
        '<meta name="twitter:image" content="' + OG_CARD + '">\n'
        # The plain-markdown brief for language models. There is no registered
        # discovery mechanism for /llms.txt beyond the well-known path, so a
        # rel="alternate" is the closest honest signal that it exists.
        '<link rel="alternate" type="text/markdown" href="'
        + SITE_ORIGIN + '/llms.txt" title="Highlightz for language models">'
    )


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
                # A timed trial that has run past trial_ends_at no longer grants
                # access.
                #
                # `trial_ends_at > 0` IS LOAD-BEARING, not a tidy-up. Without it
                # this reads "time.time() >= 0", which is true always — so any
                # trialing user with no stored end date is expired on their very
                # next request, streams stopped, "your free trial has ended"
                # toast fired. That was unreachable while every trial was
                # app-managed and always carried an end date. It stopped being
                # unreachable the moment Stripe became able to send us
                # `trialing`: a card-up-front trial whose webhook has not landed
                # yet, or landed without a trial_end, has exactly this shape.
                # It would have locked out the paying customer at the instant
                # they paid.
                #
                # Failing open is the right direction here regardless. A
                # trialing status with no end date means we do not know when it
                # ends; Stripe does, and it has their card. Letting them work
                # until Stripe says otherwise costs at most a few days of
                # product. Guessing "expired" costs a customer.
                # The condition itself now lives in plans.trial_expired, so
                # this and every reader that does NOT run on the user's own
                # request (the admin table, get_plan, funnel_stage) reach the
                # same verdict. Two copies is how the admin page came to show
                # "Trial ends" against a date already past.
                if _plans.trial_expired(db_user):
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
class HeadRequestMiddleware(BaseHTTPMiddleware):
    """Answer HEAD wherever GET is answered.

    FastAPI's @app.get registers GET only. Plain Starlette Routes add HEAD
    automatically; APIRoute does not — so EVERY page on this site returned 405
    to a HEAD request while returning 200 to GET. Only /static worked, because
    StaticFiles handles it itself.

    That is not a technicality. `curl -I` sends HEAD, and so do uptime monitors,
    link checkers and some crawlers — all of which would have read this site as
    broken. It was found by running `curl -sI https://highlightz.app/favicon.ico`
    to confirm the favicon fix and getting 405 instead of 200.

    HEAD is dispatched as GET and the body dropped, keeping the original
    Content-Length, which is what RFC 9110 asks for: identical headers to the
    GET, no body.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method != "HEAD":
            return await call_next(request)
        request.scope["method"] = "GET"
        response = await call_next(request)

        # Drain the body so the length is known, then discard it.
        body = b""
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                body += chunk if isinstance(chunk, bytes) else chunk.encode()
        else:
            body = getattr(response, "body", b"") or b""

        headers = dict(response.headers)
        headers["content-length"] = str(len(body))
        return Response(content=b"", status_code=response.status_code,
                        headers=headers)


# Registered AFTER AuthMiddleware so it runs BEFORE it: Starlette applies
# middleware in reverse, and the method has to read as GET by the time the auth
# gate and the routes see it.
app.add_middleware(HeadRequestMiddleware)
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

# Fields that used to be written and must not survive on disk. Stripping at
# load (rather than in a script somebody has to remember to run) means the next
# _save_clips() writes the file back without them, so a deploy is the whole
# migration.
#
# `suggested_by` held the Twitch display name of the viewer who made a suggested
# clip. Nothing read it, the UI never showed it, and it is a third party's
# identity — so it was removed from ClipMetadata on 2026-08-27. Deleting the
# field from the dataclass stops NEW clips carrying it; this is what clears the
# ones already stored.
_RETIRED_CLIP_FIELDS = ("suggested_by",)


def _load_clips() -> dict:
    try:
        rows = json.loads(_CLIPS_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        log.error("clips_file_load_failed", path=str(_CLIPS_FILE))
        return {}
    global _retired_fields_stripped
    for c in rows:
        for field in _RETIRED_CLIP_FIELDS:
            if field in c:
                del c[field]
                _retired_fields_stripped += 1
    return {c["id"]: c for c in rows}


# Set by _load_clips, read once at import below. Stripping in memory is not the
# same as deleting the data: without the rewrite the name stays in clips.json
# until some unrelated clip activity happens to save the file, and "we removed
# it" would be true of the running process but not of the disk.
_retired_fields_stripped = 0

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

# One rewrite, the first time a store containing a retired field is opened, so
# the data actually leaves the disk rather than only the running process. After
# that _load_clips finds nothing to strip and this never fires again.
#
# Wrapped, because a stats-style cleanup must never be the reason the service
# fails to boot: a read-only or full disk here should cost us the rewrite, not
# the process. The in-memory copy is already clean either way.
if _retired_fields_stripped:
    try:
        _save_clips()
        log.info("retired_clip_fields_purged", count=_retired_fields_stripped)
    except Exception as exc:
        log.warning("retired_clip_fields_purge_failed", error=str(exc))

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
    # The captured file, addressed by clip id. Handled here rather than at each
    # call site because every path that removes a clip — reject, delete, the
    # dead-clip sweep, bulk clear, account deletion — already routes through
    # this function, so one edit covers all five and none of them can be
    # forgotten later.
    cid = clip.get("id", "")
    if cid:
        try:
            from src.clips import files as clip_files
            clip_files.delete(cid)
        except Exception as exc:
            log.warning("clip_capture_delete_failed", clip_id=cid, error=str(exc))

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


# ── Public keep rate (the landing page's second number) ───────────────────────
# What share of the clips the formula catches do streamers actually keep. Only
# meaningful next to the raw count: the count says how much it caught, this
# says how much of it was worth catching.
#
# Below this many judged clips the rate is not published at all. A percentage
# taken off a handful of decisions is noise dressed as evidence, and this is a
# marketing page — the floor is what stops one good afternoon from becoming a
# public claim. Raise it as the ledger grows; do not lower it to make a number
# appear.
_KEEP_MIN_SAMPLE = 50
# The landing page is the highest-traffic route on the server and this scans the
# whole ledger, so it is cached. The number moves in hours, not seconds; serving
# it five minutes stale costs nothing and keeps a crawler burst off the disk.
_KEEP_TTL_S = 300
_keep_cache: tuple[float, int | None, int] | None = None


def public_keep_rate(force: bool = False) -> tuple[int | None, int]:
    """(kept_pct, sample) for the landing page. kept_pct is None below the floor.

    Never raises: a stats read that fails must not take the marketing page with
    it. On any error the rate is simply withheld, which is the same thing the
    page does before there is enough data — so the failure mode is a tile that
    does not appear, not a page that does not load.
    """
    global _keep_cache
    now = time.time()
    if not force and _keep_cache and now - _keep_cache[0] < _KEEP_TTL_S:
        return _keep_cache[1], _keep_cache[2]
    try:
        from src.stats import stream_stats
        t = stream_stats.overall_totals()
        sample = t["reviewed"]
        kept = t["kept_pct"] if sample >= _KEEP_MIN_SAMPLE else None
    except Exception as exc:
        log.warning("keep_rate_failed", error=str(exc))
        kept, sample = None, 0
    _keep_cache = (now, kept, sample)
    return kept, sample


# ── Landing-page showcase (admin-curated example clips) ───────────────────────
# The owner hand-picks approved clips to feature publicly on the landing page.
# Curated (never automatic) so no other user's activity leaks, and only a
# whitelisted subset of fields is exposed.
_SHOWCASE_FILE = Path(settings.local_storage_path) / "showcase.json"
_SHOWCASE_MAX  = 8


def _load_showcase() -> list[dict]:
    try:
        data = json.loads(_SHOWCASE_FILE.read_text())
        if not isinstance(data, list):
            return []
    except Exception:
        return []
    # Entries curated before placement existed carry neither key. Default them
    # to BOTH: that is exactly what they were doing, and defaulting to neither
    # would silently blank the live landing page on deploy.
    for e in data:
        e.setdefault("hero", True)
        e.setdefault("gallery", True)
    # A broadcaster who opts out leaves the homepage too. The opt-out is
    # checked when a channel is ADDED; a clip featured before the opt-out
    # would otherwise stay on the marketing page indefinitely, which is the
    # one place it would be most visible. Filtered at read time so an opt-out
    # takes effect on the next render without anyone re-curating.
    from src.auth.optout import is_opted_out
    return [e for e in data if not is_opted_out(str(e.get("channel") or ""))]


def _save_showcase(items: list[dict]) -> None:
    _atomic_write(_SHOWCASE_FILE, json.dumps(items))


def _showcase_entry(clip: dict) -> dict:
    """Public-safe projection of a clip — nothing user-identifying beyond the
    (public) Twitch channel the clip is from.

    `hero` and `gallery` say WHERE it appears. One curated list feeding two
    very different places: the hero wall wants four channels being scored, the
    examples grid wants a spread of good clips. New entries go in both, which
    is what everything did before placement existed."""
    return {
        "hero":    True,
        "gallery": True,
        "id":            clip.get("id"),
        "clip_title":    clip.get("clip_title") or clip.get("stream_title") or "Clip",
        "channel":       clip.get("channel"),
        "game":          clip.get("game") or "",
        "twitch_url":    clip.get("twitch_url"),
        "embed_url":     clip.get("embed_url") or "",
        "thumbnail_url": clip.get("thumbnail_url") or "",
        "score":         round(float(clip.get("trigger_score") or clip.get("score") or 0)),
        "duration_seconds": clip.get("duration_seconds") or 0,
        "signal":        _top_signal(clip),
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

        # TWO BUDGETS, NOT ONE. Triggered clips draw on max_pending (Free 20 /
        # Starter 50 / Pro 200); crowd suggestions draw on max_suggested (Free
        # 3). They are counted separately so a suggestion can never take a slot
        # a triggered clip wanted — the guarantee the old 50% reserve only
        # approximated — and so the free tier's twenty means twenty of OUR
        # clips with the three suggestions on top rather than inside.
        from src.billing.plans import limits_for
        from src.auth import users as _plan_user_store
        _limits = limits_for(_plan_user_store.get_by_id(clip_uid))
        is_suggestion = bool(clip.get("suggested"))
        pending_cap = (_limits.get("max_suggested", 0) if is_suggestion
                       else _limits["max_pending"])
        user_pending = sorted(
            [c for c in _clips.values()
             if c.get("status") == "pending" and c.get("user_id") == clip_uid
             and bool(c.get("suggested")) == is_suggestion],
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
        # The public counter is "clips the formula caught", and it is published
        # beside a keep rate computed from APPROVED/REJECTED — which excludes
        # crowd suggestions. Counting suggestions here and not there would put
        # the two landing-page numbers on different denominators and quietly
        # depress the rate, so a suggestion counts toward neither.
        counts_as_caught = not _excluded_from_learning(clip)
        if dropped:
            log.info("clip_dropped_queue_full", clip_id=clip.get("id"),
                     channel=channel, pending=len(user_pending), cap=pending_cap)
            _delete_clip_file(clip)
            # COUNTED ANYWAY, and it is not a fudge: notify_clip_ready runs
            # AFTER the processor already created the clip on Twitch, so this
            # clip was genuinely captured. It is discarded here only because the
            # user's review queue filled in the race between pending_room() and
            # this check. The counter's contract is "every clip the system has
            # ever captured, regardless of later approve/reject/delete" — a clip
            # deleted for capacity is exactly that case, so omitting it was an
            # undercount against the counter's own definition.
            if counts_as_caught:
                increment_clip_counter()
        else:
            _clips[clip["id"]] = clip
            _save_clips()
            if counts_as_caught:
                increment_clip_counter()
            # Counted at creation, not from _clips — rejected clips are deleted,
            # so a later census would report only survivors.
            #
            # CAUGHT means our detector found the moment. A crowd suggestion is
            # the opposite claim — viewers found it and we did not — so counting
            # one here would inflate the exact per-channel number the streamer
            # is shown, and would do it in the direction that flatters us. It
            # would also silently corrupt the acceptance rate on the dashboard,
            # which is a ratio over these records. The APPROVED/REJECTED sides
            # exclude suggestions too (see _excluded_from_learning); excluding
            # them at only one end of the ratio would be worse than either.
            if counts_as_caught:
                from src.stats import stream_stats
                stream_stats.record(stream_stats.CAUGHT, clip)

    # Outside the lock: notify_clip_missed broadcasts, and awaiting a socket
    # write while holding _data_lock stalls every other clip in the pipeline.
    if dropped:
        # "You missed a clip" is a claim about the product failing the user,
        # and it drives an upgrade prompt. A crowd suggestion that did not fit
        # is not that: nothing the user pays for was lost, and the suggester
        # already holds back a reserve of the queue precisely so a suggestion
        # can never be what fills it. Dropping it quietly is the honest end of
        # that promise — nagging here would manufacture a miss to sell against.
        if counts_as_caught:
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
    # Twitch only returns an email when the token carries user:read:email, which
    # tokens minted before that scope was requested do not — so this is empty
    # for everyone signing in on an old grant, and set_email ignores an empty
    # value rather than blanking an address we already have from Stripe.
    if tuser.get("email"):
        user_store.set_email(user["id"], tuser["email"], source="twitch")

    # They have just completed OAuth, so this grant is current. Recorded here
    # rather than in upsert_twitch_user because that runs for a returning user
    # too and this is specifically about the authorisation, not the account.
    user_store.mark_login(user["id"])

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
                                # Its own budget, not a slice of max_pending —
                                # the dashboard has to be able to say so, or a
                                # free user counting 20 + 3 clips on screen
                                # reads the queue meter as broken.
                                "max_suggested": limits.get("max_suggested", 0),
                                # The weekly library allowance. The dashboard
                                # counts what has been kept itself, from
                                # approved_at on the clips it already holds —
                                # so only the CEILING travels here, and the
                                # meter cannot drift out of step with the list
                                # on screen or go stale between events.
                                "max_library_week": limits.get("max_library_week", 0),
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
        # Whether this trial has a card behind it, which decides what the
        # dashboard tells them to do about it. There are two kinds now and the
        # advice is opposite:
        #
        #   Stripe trial  — card on file, converts to a paid subscription by
        #                   itself on day 7. "Subscribe to keep access" is
        #                   wrong: they already did, and following that advice
        #                   sends them into a second checkout. What they need
        #                   is the date and the cancel link.
        #   Admin comp    — app-managed, no card, no Stripe subscription. It
        #                   really does just stop, and "Subscribe" is exactly
        #                   the right thing to say.
        #
        # A stripe_customer_id is what separates them: a comp never has one.
        "trial_converts":      bool(status == "trialing" and user.get("stripe_customer_id")),
        "twitch_login":        user.get("twitch_login") or (request.session.get("username") if user.get("twitch_id") else None),
        "kick_slug":           user.get("kick_slug") or "",
        "kick_username":       user.get("kick_username") or "",
    }


# ── Kick OAuth: REMOVED 2026-08-27 ────────────────────────────────────────────
# There was a "Connect" button that ran a full Kick OAuth flow and stored the
# user's Kick access AND refresh tokens — while Kick monitoring itself is
# switched off (POST /streams returns 503 for platform="kick"). So we collected
# credentials for a feature that cannot run, and both the Terms and the Privacy
# Policy stated, in as many words, that no Kick credentials are requested or
# stored. Removing the flow was the honest way to make that sentence true again.
#
# The "Kick is coming soon" UI, KICK_BLOCKED and the 503 all stay exactly as
# they were — this removes the login, not the roadmap. Bring the flow back with
# the feature, and disclose it in the same commit.


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
            release_live_slot(key)
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
        '<div class="avatar-placeholder"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#c489e4" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg></div>'
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
    # The Terms promise the Service "will not create clips from" an opted-out
    # channel, and the landing page says the opt-out "takes effect immediately
    # across every account". Until now it only blocked ADDING the channel, so
    # a monitor already running kept clipping until the stream ended. Stop
    # every user's monitor on this channel now.
    stopped = await _stop_monitors_for_channel(twitch_login)
    if stopped:
        log.info("streamer_opted_out_monitors_stopped", login=twitch_login, count=stopped)
    return RedirectResponse("/opt-out/success", status_code=302)


async def _stop_monitors_for_channel(channel: str) -> int:
    """Stop every user's monitor on one Twitch channel. Returns how many.

    Each stop goes through stop_stream_internal, so every affected user's
    open tabs drop the row live (the realtime contract) and each worker is
    told to stop. Failures on one user must not leave another's running.
    """
    channel = _clean_channel(channel)
    keys = [k for k, v in list(_streams.items())
            if k.split(":", 1)[-1] == channel
            and (v.get("platform") or "twitch") == "twitch"]
    stopped = 0
    for key in keys:
        uid = key.split(":", 1)[0] if ":" in key else ""
        try:
            if await stop_stream_internal(channel, uid):
                stopped += 1
        except Exception as exc:
            log.warning("optout_stop_failed", channel=channel, user_id=uid, error=str(exc))
    return stopped


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
# Threads per user, whichever side opened them. Disk is the reason it exists,
# and the direction a thread started in does not change what it costs.
_MAX_THREADS_PER_USER = 200

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
    if sum(1 for f in _feedback if f.get("user_id") == uid) >= _MAX_THREADS_PER_USER:
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

class _FeedbackReply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/admin/feedback/{feedback_id}/reply")
async def admin_feedback_reply(request: Request, feedback_id: str, body: _FeedbackReply):
    """Answer a piece of feedback, in the app.

    NOT email. Twitch OAuth hands us no address, so the only emails we hold are
    the ones Stripe gave us for people who have PAID — which excludes trial
    users, who are exactly the people most likely to write in. An in-app reply
    reaches everybody, needs no mail service, and rides the socket that is
    already there.
    """
    _require_admin(request)
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Reply is required")

    async with _data_lock:
        entry = next((f for f in _feedback if f["id"] == feedback_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="Not found")
        entry.setdefault("replies", []).append({
            "message":    msg,
            "at":         time.time(),
            "from_admin": True,
        })
        # Answering it IS reading it — leaving it unread would keep the badge lit
        # on something already dealt with.
        entry["read"] = True
        # Their unread marker, cleared when they open the tab.
        entry["reply_unread"] = True
        _save_feedback()

    uid = entry.get("user_id") or ""
    # Realtime contract: the reply has to reach an open tab without a refresh,
    # and the nav badge has to light up for the RECIPIENT, not for admins.
    await broadcast({"event": "feedback_reply", "feedback_id": feedback_id,
                     "message": msg}, user_id=uid)
    log.info("feedback_replied", feedback_id=feedback_id, to_user=uid)
    return {"ok": True}


# Reaching out to somebody who has NOT written in. Bounded so one request
# cannot fan out to an unbounded number of threads; the real user base is far
# below this, so hitting it means something went wrong rather than something
# ambitious.
_MAX_MESSAGE_RECIPIENTS = 500


class _AdminMessage(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=_MAX_MESSAGE_RECIPIENTS)
    message: str = Field(min_length=1, max_length=2000)


@app.post("/admin/feedback/new", status_code=201)
async def admin_feedback_start(request: Request, body: _AdminMessage):
    """Start a support thread with someone who has not written in.

    THE GAP THIS FILLS. Every path here was reactive: the admin could only ever
    answer a thread the user opened. So the people you most need to reach —
    somebody who stopped at the paywall, a trial about to lapse, a user whose
    streams you had to stop — were exactly the ones you had no way to contact,
    because not writing in was the whole problem.

    NOT EMAIL, for the same reason the reply endpoint is not email: Twitch OAuth
    gives us no address unless the user signs in again under the new scope, so
    the only addresses we hold are Stripe's, i.e. people who have already paid.
    An in-app message reaches everybody and rides the socket that is already
    there.

    SHAPE. The thread is a normal feedback entry with an EMPTY opening message
    and the admin's text as the first reply, flagged `from_admin_start`. That is
    deliberate: both the user's screen and the admin's list already render
    `replies` with the right attribution and colour, so an admin-started thread
    displays correctly through the code that already exists, and the user can
    answer it with the reply endpoint they already have. Putting the admin's
    words in `message` would instead have made every existing reader of that
    field — the admin list, the user's own thread view — attribute the admin's
    text to the user.

    It lands READ (there is nothing for an admin to action on a thread they just
    wrote) and `reply_unread`, which is what lights the recipient's nav badge.
    """
    _require_admin(request)
    from src.auth import users as user_store

    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message is required")

    # De-duplicated but ORDER-PRESERVING, so "sent to 3 people" counts three
    # people and the same id pasted twice does not become two threads.
    wanted, seen = [], set()
    for uid in body.user_ids:
        uid = (uid or "").strip()
        if uid and uid not in seen:
            seen.add(uid)
            wanted.append(uid)
    if not wanted:
        raise HTTPException(status_code=400, detail="Pick at least one recipient")

    known = {u["id"]: u for u in user_store.get_all()}
    now = time.time()
    import secrets as _sec

    sent, skipped = [], []
    async with _data_lock:
        # Counted once for everybody rather than re-scanned per recipient — this
        # runs over the whole feedback list and the list is held in memory.
        per_user: dict[str, int] = {}
        for f in _feedback:
            fid = f.get("user_id")
            if fid:
                per_user[fid] = per_user.get(fid, 0) + 1
        for uid in wanted:
            u = known.get(uid)
            if u is None:
                skipped.append({"user_id": uid, "reason": "no such user"})
                continue
            if per_user.get(uid, 0) >= _MAX_THREADS_PER_USER:
                skipped.append({"user_id": uid, "reason": "thread limit reached"})
                continue
            entry = {
                "id":               _sec.token_urlsafe(12),
                "user_id":          uid,
                "username":         u.get("username") or u.get("twitch_login") or "",
                "category":         "Message",
                "message":          "",
                "from_admin_start": True,
                "created_at":       now,
                "read":             True,
                "reply_unread":     True,
                "replies": [{"message": msg, "at": now, "from_admin": True}],
            }
            _feedback.append(entry)
            per_user[uid] = per_user.get(uid, 0) + 1
            sent.append(entry)
        if sent:
            # One write for the batch. Saving per recipient would rewrite the
            # whole file once per person for no benefit.
            _save_feedback()

    # Realtime contract, scoped: each recipient's own socket, never everyone's.
    # A distinct event from feedback_reply because the copy differs — telling
    # someone they have "a reply to your feedback" when they never sent any is
    # a small lie that makes the product look confused.
    for entry in sent:
        await broadcast({"event": "feedback_message", "feedback_id": entry["id"],
                         "message": msg}, user_id=entry["user_id"])
    log.info("admin_message_sent", recipients=len(sent), skipped=len(skipped),
             by=request.session.get("user_id"))
    return {"ok": True, "sent": len(sent),
            "usernames": [e["username"] for e in sent], "skipped": skipped}


@app.get("/feedback/mine")
async def feedback_mine(request: Request):
    """This user's own feedback, with any replies. Scoped to the caller."""
    uid = _current_user_id(request)
    mine = [f for f in _feedback if f.get("user_id") == uid]
    mine.sort(key=lambda f: f.get("created_at") or 0, reverse=True)
    return [{"id": f["id"], "category": f.get("category", "General"),
             "message": f.get("message", ""), "created_at": f.get("created_at", 0),
             "replies": f.get("replies", []),
             # Tells the screen not to draw an empty bubble where the user's
             # own opening message would be — there isn't one, we started it.
             "from_admin_start": bool(f.get("from_admin_start")),
             "reply_unread": bool(f.get("reply_unread"))} for f in mine]


async def _notify_admins(event: dict) -> None:
    """Push an event to every admin's open tabs.

    broadcast() targets one user or EVERYONE, and everyone is wrong here — a
    reply on someone's support thread must not land on every socket. So it is
    fanned out to the admins by id.
    """
    from src.auth import users as user_store
    try:
        for u in user_store.get_all():
            if u.get("is_admin"):
                await broadcast(event, user_id=u["id"])
    except Exception as exc:            # never let a notification break a reply
        log.warning("admin_notify_failed", error=str(exc))


# Bounded so one thread cannot grow forever — this is a support conversation,
# not a chat room, and _feedback is held in memory and rewritten on every save.
_MAX_THREAD_REPLIES = 50


@app.post("/feedback/{feedback_id}/reply", status_code=201)
async def feedback_user_reply(request: Request, feedback_id: str, body: _FeedbackReply):
    """The user answering back on their OWN thread.

    Same shape as the admin reply, opposite direction — from_admin False. The
    entry is marked UNREAD again so it returns to the admin's queue: a reply
    that did not reopen the thread would be filed as answered and never seen.
    """
    uid = _current_user_id(request)
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message is required")

    # Validate BEFORE spending the cooldown slot. Two reasons: a probe for an id
    # that is not yours has to look identical to a probe for one that does not
    # exist (a 429 on one and a 404 on the other confirms the id is real), and a
    # refused request must not burn the caller's next ten seconds.
    async with _data_lock:
        entry = next((f for f in _feedback if f["id"] == feedback_id), None)
        if entry is None or entry.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Not found")
        if len(entry.get("replies", [])) >= _MAX_THREAD_REPLIES:
            raise HTTPException(status_code=429,
                                detail="This conversation is full — start a new message")

    now = time.time()
    # Same lock and same dict as /feedback, so replying on a thread is not a way
    # around the cooldown on new feedback.
    async with _feedback_rate_lock:
        last = _feedback_last_submit.get(uid, 0)
        if now - last < _FEEDBACK_COOLDOWN:
            raise HTTPException(status_code=429,
                                detail="Please wait a moment before sending more")
        _feedback_last_submit[uid] = now

    async with _data_lock:
        # Re-resolve: the entry could have been deleted while the rate lock was held.
        entry = next((f for f in _feedback if f["id"] == feedback_id), None)
        if entry is None or entry.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Not found")
        replies = entry.setdefault("replies", [])
        if len(replies) >= _MAX_THREAD_REPLIES:
            raise HTTPException(status_code=429,
                                detail="This conversation is full — start a new message")
        replies.append({"message": msg, "at": now, "from_admin": False})
        entry["read"] = False           # back into the admin's queue
        _save_feedback()

    # Realtime: the admin's badge has to light up without a refresh, or a reply
    # sits unseen until they happen to reload.
    await _notify_admins({"event": "feedback_new", "feedback_id": feedback_id,
                          "username": entry.get("username", "")})
    log.info("feedback_user_replied", feedback_id=feedback_id, user_id=uid)
    return {"ok": True}


@app.post("/feedback/mark-read")
async def feedback_mark_replies_read(request: Request):
    """Clear this user's reply badge once they have actually seen the thread."""
    uid = _current_user_id(request)
    async with _data_lock:
        touched = 0
        for f in _feedback:
            if f.get("user_id") == uid and f.get("reply_unread"):
                f["reply_unread"] = False
                touched += 1
        if touched:
            _save_feedback()
    return {"ok": True, "cleared": touched}


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
    """The nav badge, which now means two different things by role.

    For an ADMIN it is unanswered feedback. For everyone else it is replies
    they have not read yet — previously this returned 0 for non-admins, so a
    user could be answered and never find out. Same endpoint because the nav
    asks one question ("is there anything for me on the Feedback tab?") and the
    answer just depends on who is asking.
    """
    from src.auth import users as user_store
    uid = request.session.get("user_id", "")
    db_user = user_store.get_by_id(uid) if uid else None
    if db_user and db_user.get("is_admin"):
        return {"count": sum(1 for f in _feedback if not f.get("read"))}
    if not uid:
        return {"count": 0}
    return {"count": sum(1 for f in _feedback
                         if f.get("user_id") == uid and f.get("reply_unread"))}


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


def _labelers_by_clip() -> dict[str, set[str]]:
    """clip_id -> the set of labelers who have scored it."""
    out: dict[str, set[str]] = {}
    try:
        for line in _HUMAN_SCORES_FILE.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = r.get("clip_id")
            if cid:
                out.setdefault(cid, set()).add(r.get("labeler_id", ""))
    except FileNotFoundError:
        pass
    return out


@app.get("/training/queue")
async def training_queue(request: Request, mode: str = "own"):
    """Blind list of clips for this labeler to score.

    mode=own (default) — this labeler's own account's clips, not yet scored by
    them. Clips already reviewed (approved/rejected) are excluded: the labeler
    has watched and judged those in Clip Review, so they cannot be scored
    blind any more. Oldest first, so the backlog drains in capture order
    instead of newest clips jumping the line.

    mode=agreement — clips SOMEBODY ELSE has already rated, that this labeler
    has not. This is the only way to find out whether "human virality" is a
    real, shared judgement or one person's taste.

    WHY THIS MODE HAD TO EXIST. The own-queue serves each labeler only their
    own account's clips, so across 1,641 ratings not a single clip had been
    seen by two people. Two consequences, both fatal to the analysis:

      * There was no way to know whether the humans agree with each other,
        and therefore no way to read a weak bot-vs-human correlation. A
        formula cannot match a target that is not stable, so a flat result
        was evidence about the TARGET as much as about the bot.
      * Labeler was perfectly confounded with CHANNEL — each person rated only
        their own streamers — so "this rater is harsher" and "these channels
        are quieter" were the same number and could never be separated.

    STILL BLIND, AND MORE SO. The same _blind_clip_view is used, and the other
    person's rating is never sent — an anchored second opinion measures
    suggestibility, not agreement, and would be worse than no data at all.

    Cross-account by design: a labeler scores clips captured by other accounts.
    The blind view carries only what is needed to watch the moment — channel,
    game, timestamp, duration and the public Twitch URLs — and nothing about
    who captured it.
    """
    uid = _require_labeler(request)
    scored = _human_scored_pairs()

    if mode == "agreement":
        rated_by = _labelers_by_clip()
        queue = [
            _blind_clip_view(c) for c in _clips.values()
            if (c.get("trigger_signals") or [])
            and (c.get("id"), uid) not in scored
            # Somebody who is not this labeler has already judged it.
            and (rated_by.get(c.get("id"), set()) - {uid})
            # A clip this labeler OWNS and has already resolved has been seen
            # in Clip Review, so it can no longer be rated blind. Other
            # people's clips never had that exposure.
            and not (c.get("user_id") == uid
                     and c.get("status") in ("approved", "rejected"))
        ]
        # Fewest raters first: a third opinion on a clip that already has two
        # is worth less than a second opinion on a clip that has one.
        queue.sort(key=lambda c: (len(rated_by.get(c["id"], set())),
                                  c.get("created_at") or 0))
        return queue[:100]

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
    # Own clips always; someone else's ONLY when another labeler has already
    # rated it — i.e. exactly the agreement queue. Scoped this narrowly on
    # purpose: without the second condition a labeler could post a score
    # against any clip id in the system, which is a much larger permission
    # than "help measure whether we agree".
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.get("user_id") != uid:
        others = _labelers_by_clip().get(body.clip_id, set()) - {uid}
        if not others:
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
    # clip_id rides along so another trainer's open Cross-rate queue can drop a
    # clip THEY were about to score a second time, and so the agreement panel
    # refreshes the moment a pair completes.
    await broadcast({"event": "training_scored", "total": total,
                     "labeler": username, "clip_id": body.clip_id})
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


@app.get("/training/agreement")
async def training_agreement(request: Request):
    """Do the humans agree with each other? Live, for the Training Studio.

    This is the ceiling on every bot-vs-human number in the analysis. If two
    people who watch the same clip rank it differently, there is no stable
    "human virality" for any formula to match, and a flat correlation is a
    fact about the target rather than about the bot. It is shown in the studio
    so the team can see the number they are building rather than waiting for
    someone to run a report.
    """
    _require_labeler(request)
    from src.maintenance.analyze_human_scores import spearman

    by_clip: dict[str, list[dict]] = {}
    try:
        for line in _HUMAN_SCORES_FILE.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            v = (r.get("human") or {}).get("virality")
            if r.get("clip_id") and v is not None:
                by_clip.setdefault(r["clip_id"], []).append(r)
    except FileNotFoundError:
        pass

    pairs = {c: rs for c, rs in by_clip.items()
             if len({r.get("labeler_id") for r in rs}) > 1}
    a_side, b_side, gaps, per_pair = [], [], [], {}
    for rs in pairs.values():
        seen, picked = set(), []
        for r in rs:
            lid = r.get("labeler_id")
            if lid in seen:
                continue
            seen.add(lid)
            picked.append(r)
            if len(picked) == 2:
                break
        x = float(picked[0]["human"]["virality"])
        y = float(picked[1]["human"]["virality"])
        a_side.append(x)
        b_side.append(y)
        gaps.append(abs(x - y))
        key = " + ".join(sorted([picked[0].get("labeler") or "?",
                                 picked[1].get("labeler") or "?"]))
        per_pair.setdefault(key, []).append(abs(x - y))

    agree = spearman(a_side, b_side) if len(a_side) >= 10 else None
    gaps_sorted = sorted(gaps)
    return {
        "clips_rated_twice": len(pairs),
        "agreement": round(agree, 3) if agree is not None else None,
        "median_gap": (round(gaps_sorted[len(gaps_sorted) // 2], 1)
                       if gaps_sorted else None),
        "within_two": (round(100 * sum(1 for g in gaps if g <= 2) / len(gaps))
                       if gaps else None),
        "by_pair": {k: {"n": len(v), "median_gap": round(sorted(v)[len(v) // 2], 1)}
                    for k, v in sorted(per_pair.items(), key=lambda kv: -len(kv[1]))},
    }


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

def _checkout_trial_days(db_user: dict | None) -> int:
    """How many free days a NEW checkout carries. Always zero now.

    THE SELF-SERVE TRIAL IS RETIRED (2026-08-26). Free is the front door again,
    so there is nothing left for a trial to do: somebody who wants to try the
    product before paying signs up and uses the free tier, with no card and no
    clock. A checkout is now unambiguously "start paying".

    Kept as a function rather than deleted with its call site, because the
    decision "how many free days does this checkout carry" is a real question
    that has been answered three different ways in this product's life, and the
    next change to it belongs here rather than inlined into the endpoint.

    TRIALS ALREADY RUNNING ARE NOT AFFECTED. This only shapes new Checkout
    sessions; Stripe keeps billing an existing trialing subscription on the
    terms it was created with, `trialing` still resolves to pro in get_plan,
    and those accounts convert or lapse exactly as they would have.
    """
    return 0


def _paywall_copy(kind: str) -> dict:
    """Paywall copy.

    THIS PAGE IS NOT A WALL and has not been one since the free tier came back.
    Nobody is redirected here; it is reached from upgrade prompts, so everyone
    reading it already has a working account on free. That changes what the copy
    can honestly say: "start your trial" promised access they now already have,
    and every variant that implied their access had STOPPED was telling somebody
    on a working free account that they had nothing.

    Variants: 'trial_ended' for the last cohort whose Stripe trial ran out,
    'returning' for past subscribers, 'new' for everyone else.
    """
    if kind == "trial_ended":
        return {
            "headline": "Your trial has ended",
            "subline":  ("hope you caught some great moments. Your account is on the "
                         "free plan now — one channel and a queue of 20. Pick a plan "
                         "to open it back up, from $10/month, cancel anytime."),
            "note":     "Have a promo code? Enter it at checkout for 50% off your first month.",
        }
    if kind == "returning":
        return {
            "headline": "Restart your subscription",
            "subline":  ("welcome back. You are on the free plan in the meantime, so "
                         "nothing has stopped. Pick a plan and it starts right "
                         "away — from $10/month, cancel anytime."),
            "note":     "Have a promo code? Enter it at checkout for 50% off your first month.",
        }
    return {
        "headline": "Watch more channels at once",
        "subline":  ("free covers one channel and a queue of 20 clips, which is "
                     "enough to see whether the detector earns its place. Paid "
                     "plans widen both, and Pro adds the VOD Scanner for streams "
                     "that already happened — from $10/month, cancel anytime."),
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
    from src.billing.plans import get_plan
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
        # ALREADY SUBSCRIBED. This used to redirect to "/" and do nothing, which
        # is why upgrades never landed: a Starter customer clicked Go Pro, was
        # bounced back to the dashboard, and stayed on Starter with no error and
        # no charge. The comment said tier changes happen in the Stripe portal,
        # but the pricing card points here.
        #
        # A second Checkout is NOT the fix — that mints a second subscription
        # and bills them twice. Change the price on the subscription they
        # already have.
        from src.billing.stripe_billing import change_subscription_price
        cust = db_user.get("stripe_customer_id") or ""
        current = get_plan(db_user)
        if current == plan:
            return RedirectResponse("/?plan=unchanged")
        changed = await change_subscription_price(cust, price_id) if cust else None
        if not changed:
            # Never report success we did not get. Send them to the portal,
            # where they can change tier themselves, rather than back to a
            # dashboard that still shows the old plan.
            log.error("upgrade_failed", user=uid, from_plan=current, to_plan=plan,
                      customer=cust)
            return RedirectResponse("/billing/portal")
        user_store.set_plan(uid, plan)
        log.info("plan_changed_in_place", user=uid, from_plan=current, to_plan=plan)
        await broadcast({"event": "subscription_active",
                         "message": f"You are on {plan.title()} now."}, user_id=uid)
        return RedirectResponse("/?plan=" + plan)
    # Webhook-latency window: the user may have JUST paid (Stripe knows, our DB
    # doesn't yet). Ask Stripe live before selling them a second subscription —
    # and self-heal the DB so they get straight into the app.
    stripe_customer = (db_user or {}).get("stripe_customer_id")
    if stripe_customer:
        from src.billing.stripe_billing import live_subscription_status
        live = await live_subscription_status(stripe_customer)
        if live in ("active", "trialing"):
            # Record what Stripe actually said. Writing "active" over a live
            # trial would tell a customer mid-trial that they are being billed,
            # and this self-heal path exists precisely for the window where the
            # webhook has not landed — i.e. seconds after a card-up-front trial
            # starts, which is now the single most likely time to be here.
            user_store.update_subscription(uid, stripe_customer, live)
            return RedirectResponse("/")
        if live == "past_due":
            # They have a subscription in dunning — fixing the card in the
            # portal is the right move, not stacking a new subscription.
            return RedirectResponse("/billing/portal")
    # Reuse the existing Stripe customer so a re-subscribe stays on one customer
    # (portal / cancel-on-delete / admin sync all key off the stored id).
    url = await create_checkout_url(uid, username, price_id,
                                    customer_id=stripe_customer,
                                    trial_days=_checkout_trial_days(db_user))
    # Stamped only once the session actually exists. Stamping before this line
    # would count people whose checkout we failed to create as people who
    # reached the card form and walked away — the opposite conclusion.
    user_store.mark_checkout_started(uid)
    return RedirectResponse(url)


# Shown when Stripe refuses to open a portal session even after the
# self-healing retry in create_portal_url. A raw 500 here strands paying
# users with no way back — this page is the never-a-dead-end fallback.
_PORTAL_ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Billing portal unavailable</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0b12;color:#e8e8f0;font:15px/1.6 'Sora','Sora Fallback',system-ui,sans-serif;text-align:center}
.card{max-width:420px;padding:32px 32px;background:#14141f;border:1px solid #26263a;border-radius:16px}
h1{font-size:17px;margin:0 0 8px}p{color:#9a9aae;margin:0 0 24px}
a{display:inline-block;padding:12px 24px;border-radius:10px;background:linear-gradient(135deg,#7c3aed,#b86adc);
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
    """Stripe redirects here after successful checkout.

    SELF-HEALS RATHER THAN TRUSTING THE WEBHOOK. This used to just re-read the
    DB and hope the webhook had landed. That was survivable while a new signup
    already held an app-managed trial — the webhook only had to fix up billing
    state, not hand out access. Now access comes from the Stripe subscription
    and nothing else, so a webhook endpoint without
    `customer.subscription.created` enabled means a customer enters their card
    and is redirected to a locked dashboard.

    Stripe puts the session id in this URL, so the truth is one API call away
    at exactly the moment it matters. If Stripe cannot be reached we fall back
    to the old behaviour and the webhook still arrives.
    """
    from src.auth import users as user_store
    from src.billing.stripe_billing import subscription_from_checkout_session
    uid  = request.session.get("user_id", "")
    user = user_store.get_by_id(uid) if uid else None
    if user and session_id:
        # `uid` is passed so Stripe's own metadata.user_id can be checked
        # against the signed-in account: session_id comes out of a query
        # string, so without that check any live session id granted its
        # billing state to whoever opened the link.
        cust, status, trial_end = await subscription_from_checkout_session(
            session_id, uid)
        if cust and status in ("active", "trialing"):
            stored = user.get("subscription_status")
            user_store.update_subscription(
                uid, cust, status,
                trial_end if status == "trialing" else 0)
            # Only announce a CHANGE. Landing here with everything already in
            # order (the webhook won the race, which is the normal case) must
            # not fire a toast at somebody about something that did not happen.
            if stored != status:
                log.info("checkout_self_healed", user=uid, customer=cust,
                         status=status, was=stored)
                if status == "trialing":
                    from src.auth import trial_ledger
                    if user.get("twitch_id"):
                        trial_ledger.record_trial("twitch", str(user["twitch_id"]))
                await broadcast({"event": "subscription_active",
                                 "message": "You're all set — welcome in."},
                                user_id=uid)
            user = user_store.get_by_id(uid)
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


def _lapse_message(plan: str) -> str:
    """What to tell somebody whose subscription just ended.

    IT HAS TO NAME THE PLAN THEY ACTUALLY LAND ON, and which plan that is has
    changed twice. Both lapse paths once said "You are on the free plan now —
    one stream, and your clips are still here" unconditionally, which was
    written when every lapse fell back to free; after the card cutover almost
    nobody did, so the product was promising a stream it had just taken away at
    the exact moment somebody was deciding whether we were worth paying for.

    With the free tier reopened (2026-08-26) a lapse lands on free again, so
    the free branch is the common one once more. THE POINT IS NOT WHICH BRANCH
    WINS — it is that the message is chosen from the resolved plan rather than
    assumed, so the next time this moves the copy moves with it. The fallback
    still promises nothing but the clips, which is the safe direction for an
    unrecognised plan.

    One function, called from both the webhook and the reconcile sweep, because
    two copies of this message are how it went wrong the first time.
    """
    if plan == "free":
        return ("Your subscription has ended. You are on the free plan now — "
                "one stream, and your clips are still here.")
    # locked, or anything unexpected: promise nothing about what they keep
    # except the clips, which they genuinely do keep — GET /clips has no plan
    # gate, and a test in test_no_free_tier_claim.py holds it that way.
    return ("Your subscription has ended and monitoring has stopped. Your "
            "clips are still in your library — resubscribe any time to start "
            "watching again.")


def apply_subscription_event(user_id: str | None, cust_id: str, status: str,
                             trial_ends_at: float | None = None) -> str | None:
    """Apply a verified Stripe subscription event to the user store and return
    the id of the user actually affected (None if no user matched).

    Cross-check: metadata user_id must match that user's stored customer. On a
    mismatch (stale/orphaned customer, or tampered metadata) the update is
    applied strictly BY CUSTOMER, and the affected user is whoever owns that
    customer — never the metadata user, whose current subscription may be
    healthy and must not be touched.

    `trial_ends_at` carries Stripe's trial_end on a card-up-front trial, and
    None on every other event so an app-managed trial's own end date is never
    overwritten by a status change that has nothing to do with it."""
    from src.auth import users as user_store
    if user_id:
        db_user = user_store.get_by_id(user_id)
        stored_cust = db_user.get("stripe_customer_id") if db_user else None
        if stored_cust and stored_cust != cust_id:
            log.warning("stripe_webhook_customer_mismatch",
                        webhook_customer=cust_id, stored_customer=stored_cust,
                        metadata_user=user_id)
            return user_store.update_subscription_by_customer(
                cust_id, status, trial_ends_at)
        user_store.update_subscription(user_id, cust_id, status, trial_ends_at)
        return user_id
    return user_store.update_subscription_by_customer(cust_id, status, trial_ends_at)


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

    # A subscription ENDING is not the same as a customer leaving. Our own
    # duplicate sweep cancels the extra subscription and Stripe then sends a
    # deleted event for it; the customer's real subscription is still running
    # and still being charged. Acting on that event locked them out — the fix
    # for double billing was causing it. Same shape when somebody cancels one
    # of two subscriptions in the portal.
    #
    # Only skip on a positive True. None means Stripe could not be reached, and
    # treating "unknown" as "another one is live" would let real cancellations
    # pile up unapplied during a Stripe outage.
    from src.billing.plans import GRACE_STATUSES
    if cust_id and status and status not in ("active", "trialing") \
            and status not in GRACE_STATUSES:
        from src.billing.stripe_billing import has_other_live_subscription
        ended_sub_id = (event.get("data", {}).get("object", {}) or {}).get("id") or ""
        others = await has_other_live_subscription(cust_id, ended_sub_id)
        if others is True:
            log.info("stripe_subscription_ended_but_customer_still_active",
                     customer=cust_id, ended=ended_sub_id,
                     event_type=event.get("type"))
            return {"received": True}
        if others is None:
            log.warning("stripe_other_subscription_unknown_applying_lapse",
                        customer=cust_id, ended=ended_sub_id)

    if cust_id and status:
        # Resolve the user this event ACTUALLY affects. On a customer mismatch
        # (metadata names user X but X's stored customer is different — e.g. a
        # stale/orphaned subscription cancelling after the user re-subscribed
        # under a new customer) we must NOT act on the metadata user: their
        # current subscription is fine, and stopping their streams / changing
        # their status would punish them for an old customer's lifecycle event.
        # Only a trialing subscription carries a trial end worth storing. Every
        # other event passes None, which leaves the stored value alone — an
        # app-managed trial must not have its end date blanked by, say, an
        # unrelated `customer.subscription.updated`.
        from src.billing.stripe_billing import extract_trial_end
        if status == "trialing":
            trial_end = extract_trial_end(event)
        elif status == "active":
            # The trial converted (or they subscribed outright): the card is
            # being charged, so no trial is running and a leftover end date
            # would have the dashboard counting down to a day that means
            # nothing. Safe to clear unconditionally — the only way to reach an
            # `active` Stripe subscription is to be paying for it.
            trial_end = 0
        else:
            trial_end = None
        user_id = apply_subscription_event(user_id, cust_id, status, trial_end)
        log.info("stripe_subscription_updated", customer=cust_id, status=status,
                 affected_user=user_id or "none", trial_end=trial_end or 0)
        # Burn the trial HERE, not when the checkout session was created. A
        # session that gets abandoned costs nothing and must not spend
        # somebody's one free week; a subscription that reaches `trialing` is
        # the week actually being taken. The ledger outlives the account, so
        # this is what stops delete-and-resignup from farming free weeks.
        if status == "trialing" and user_id:
            from src.auth import users as _us
            from src.auth import trial_ledger
            _u = _us.get_by_id(user_id)
            if _u and _u.get("twitch_id"):
                trial_ledger.record_trial("twitch", str(_u["twitch_id"]))
        # Kill active streams immediately when subscription lapses — don't wait
        # for idle reaper — and tell the open tab (realtime contract: the lapse
        # must reach the user live, mirroring admin revoke).
        if status in GRACE_STATUSES and user_id:
            # Mid-collection, not gone: a card being retried, or a first payment
            # that has not confirmed yet. Their status is recorded (so the admin
            # panel and a later reconcile see the truth) but nothing is taken
            # away — no streams stopped, and above all no "your subscription has
            # ended" toast fired at somebody in the middle of buying it.
            log.info("stripe_subscription_in_grace", user_id=user_id,
                     customer=cust_id, status=status)
        elif status not in ("active", "trialing") and user_id:
            # Down to whatever their account actually falls back to — free for a
            # grandfathered account, locked for everybody else. _enforce_stream_limit
            # already reads the real limits; the message now does too, instead of
            # asserting a free plan the user may not have.
            from src.auth import users as _lapse_store
            from src.billing.plans import get_plan
            asyncio.create_task(_enforce_stream_limit(user_id))
            await broadcast(
                {"event": "subscription_expired",
                 "message": _lapse_message(get_plan(_lapse_store.get_by_id(user_id)))},
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
            # DOUBLE-BILLING SWEEP. A brand-new subscription for a customer who
            # already has one means a duplicate got through — the checkout guard
            # fails open on a Stripe error, and two tabs can both reach checkout
            # inside the webhook-latency window. Kill the others NOW, seconds
            # after it appears, rather than letting the customer discover it as
            # a second charge at the end of the month. Keeps the newest, which
            # is the tier they just chose.
            if event.get("type") == "customer.subscription.created":
                from src.billing.stripe_billing import cancel_duplicate_subscriptions
                obj = event.get("data", {}).get("object", {}) or {}
                new_sub_id = obj.get("id") or ""
                if cust_id and new_sub_id:
                    killed = await cancel_duplicate_subscriptions(cust_id, new_sub_id)
                    if killed:
                        log.error("double_billing_prevented", user_id=user_id,
                                  customer=cust_id, cancelled=killed)
            # Membership tier: map the subscription's price id to a plan and
            # store it.
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

def _clip_out(clip: dict) -> dict:
    """A clip on its way to the browser, with the download flag attached.

    `has_file` is DERIVED FROM DISK rather than stored on the record, and that
    is deliberate. The file is written by the stream worker after the clip is
    already queued, possibly in a different process, and swept later by
    retention — so a flag written onto the record would need keeping in step
    with two things that do not know about each other. Asking the filesystem
    is one stat() and cannot be wrong.
    """
    try:
        from src.clips import files as clip_files
        return {**clip, "has_file": clip_files.exists(clip.get("id", ""))}
    except Exception:
        return {**clip, "has_file": False}


@app.get("/clips")
async def list_clips(request: Request, status: str | None = None, channel: str | None = None):
    uid = _current_user_id(request)
    clips = [c for c in _clips.values() if c.get("user_id") == uid]
    if status:
        clips = [c for c in clips if c.get("status") == status]
    if channel:
        clips = [c for c in clips if c.get("channel") == channel]
    clips.sort(key=lambda c: c.get("created_at", 0), reverse=True)
    return [_clip_out(c) for c in clips]


@app.get("/clips/{clip_id}/file")
async def get_clip_file(request: Request, clip_id: str, download: int = 0):
    """Serve a captured clip back to its owner.

    ON EVERY PLAN, including free. The editor and the scheduler are Pro
    features, but a clip the product caught for you is yours to have — putting
    the download behind the paywall would make the free plan a demo of a clip
    you can look at and not keep.

    Ownership is checked against the clip record, so another user's clip id is
    a 404 rather than a file, and the path is built from the record's id
    through `files.path_for`, which refuses anything that is not a plain id.
    """
    uid = _current_user_id(request)
    clip = _clips.get(clip_id)
    if not clip or clip.get("user_id") != uid:
        raise HTTPException(status_code=404, detail="Clip not found")

    from src.clips import files as clip_files
    path = clip_files.path_for(clip_id)
    if not path or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No downloadable file for this clip. Highlightz only keeps "
                   "the video for clips it captured while the stream was live.")

    # A filename the user recognises in their downloads folder, built from the
    # channel and title rather than the uuid. Sanitised because both come from
    # the platform and end up in a header.
    raw = f"{clip.get('channel', 'clip')}-{clip.get('clip_title') or clip.get('stream_title') or 'highlight'}"
    safe = re.sub(r"[^A-Za-z0-9 ._-]", "", raw)[:80].strip() or "highlight"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type="video/mp4",
        headers={"Content-Disposition": f'{disposition}; filename="{safe}.mp4"',
                 "X-Content-Type-Options": "nosniff"},
    )


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
        if not _excluded_from_learning(clip):
            stream_stats.record(stream_stats.UNDONE, clip)

    # "Gone unless it comes back from the undo button" — so undo, and only undo,
    # lifts the tombstone. An undo entry that merely EXPIRES leaves it standing:
    # that dismissal was real.
    _dismissed.forget(uid, restored)

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
    return _clip_out(clip)


@app.post("/clips/{clip_id}/approve")
async def approve_clip(request: Request, clip_id: str):
    from src.profiles.manager import get_profile_manager
    uid = _current_user_id(request)
    async with _data_lock:
        clip = _clips.get(clip_id)
        if not clip or clip.get("user_id") != uid:
            raise HTTPException(status_code=404, detail="Clip not found")
        # THE WEEKLY LIBRARY CAP. Checked inside the lock and against live
        # state, so two tabs approving at once cannot both pass a check that
        # only one of them had room for.
        #
        # RE-APPROVING SOMETHING ALREADY IN THE LIBRARY IS FREE. Without this
        # the clip would be counted by library_room and then blocked by its own
        # presence, so a double-click on an approved clip would report the
        # library as full.
        if clip.get("status") != "approved":
            used, cap = library_room(uid)
            if used >= cap:
                raise HTTPException(
                    status_code=403,
                    detail=(f"You have kept {used} of {cap} clips this week. "
                            f"Upgrade to keep more, or come back when your "
                            f"week rolls over — this clip stays in review "
                            f"until then."))
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
    # Never scored by us for this user — see _excluded_from_learning.
    if not _excluded_from_learning(clip):
        training_log.log_outcome(clip, training_log.APPROVED)
        from src.stats import stream_stats
        stream_stats.record(stream_stats.APPROVED, clip)
    await broadcast({"event": "clip_updated", "clip": _clip_out(clip)}, user_id=uid)
    pm      = get_profile_manager(uid)
    # load() (not cache-only get()) so the approval is always recorded — even if
    # the channel isn't currently being monitored (e.g. reviewing a clip after a
    # deploy or once the stream ended). A cache-miss here used to silently drop
    # the feedback, skewing learning toward rejections only.
    profile = await pm.load(clip["channel"])
    # Never teach a channel's profile from a clip the formula did not produce:
    # the decision says nothing about whether the formula was right, and it
    # would drift that channel's threshold on borrowed evidence. For a crowd
    # suggestion the drift has a direction — see _excluded_from_learning.
    if profile and not _excluded_from_learning(clip):
        profile.record_clip(approved=True, signals=clip.get("trigger_signals", []))
        await pm.save(profile)
        await broadcast({"event": "profile_updated", "profile": profile.to_dict()}, user_id=uid)
    await _maybe_prompt_review(uid)
    return _clip_out(clip)


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
    if not _excluded_from_learning(clip):
        training_log.log_outcome(clip, training_log.REJECTED)
        from src.stats import stream_stats
        stream_stats.record(stream_stats.REJECTED, clip)
    # File deletion is deferred to when the undo entry expires — unlinking the
    # .mp4 now would let undo restore a record pointing at nothing.
    pm      = get_profile_manager(uid)
    # load() (not cache-only get()) so the rejection is always recorded, matching
    # the approve path — see note there.
    profile = await pm.load(clip["channel"])
    # Never teach a channel's profile from a clip the formula did not produce:
    # the decision says nothing about whether the formula was right, and it
    # would drift that channel's threshold on borrowed evidence. For a crowd
    # suggestion the drift has a direction — see _excluded_from_learning.
    profiles_before = {}
    if profile and not _excluded_from_learning(clip):
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
    # A crowd suggestion is a Twitch clip that still exists and that the poll
    # keeps returning for several more minutes. Deleting our row was the only
    # thing that had been stopping it being suggested again, so the "no" has to
    # outlive the row. Undo lifts it; see dismissed_suggestions.
    _dismissed.dismiss(uid, [clip])

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
    _dismissed.dismiss(uid, [clip])
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
        if not _excluded_from_learning(clip):
            stream_stats.record(stream_stats.CLEARED, clip)

    # THE REPORTED BUG. Clearing the queue is the action most likely to be taken
    # in bulk and the one that used to bring suggestions straight back: it
    # deletes the rows, and the rows were the only durable record that these
    # moments had already been offered.
    _dismissed.dismiss(uid, removed)

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
    """Remove PENDING clips for the current user whose score is below min_score.

    PENDING ONLY, same rule as clear-queue and for the same reason: approved
    clips are the user's library, not their inbox. This button lives on Clip
    Review, which shows the pending queue and nothing else, so a cull that
    reached past it would delete work the user had already decided to keep and
    could not see from the screen they pressed it on. It used to sweep every
    status.

    CROWD SUGGESTIONS ARE NEVER CULLED. They carry score 0 because no score
    produced them — they are surfaced precisely because the formula did not
    rate the moment — so ANY threshold above zero deletes all of them. A user
    culling at 50 to tidy up low-scoring clips would have silently wiped every
    moment the crowd found, which is the one thing on this screen the score
    cannot speak for. Score-based culling has no opinion to offer about a clip
    that was never scored.
    """
    uid = _current_user_id(request)
    min_score = max(0.0, min(100.0, body.min_score))

    to_remove = []
    async with _data_lock:
        for clip_id, clip in list(_clips.items()):
            if clip.get("user_id") != uid or clip.get("status") != "pending":
                continue
            if clip.get("suggested"):
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
        _dismissed.dismiss(uid, culled)
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


# ── Hidden "recently monitored" suggestions ──────────────────────────────────
# HIDDEN, NOT DELETED, and the distinction is the whole design. The recent list
# is derived from the user's profile files, and a profile is not a history
# entry — it holds the learned signal_weights and the approve/reject record for
# that channel. Deleting one to tidy a dropdown would silently throw away every
# correction the user ever made for that streamer, and they would only find out
# by noticing the scoring had gone dumb weeks later. So clearing writes a
# dismissal here and leaves the profile untouched: monitor the channel again and
# it picks up exactly where it left off.
_HIDDEN_SUGG_FILE = Path(settings.local_storage_path) / "hidden_suggestions.json"


def _load_hidden_suggestions() -> dict:
    try:
        data = json.loads(_HIDDEN_SUGG_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _hidden_for(uid: str) -> set:
    return {str(c).lower() for c in _load_hidden_suggestions().get(uid, [])}


def _set_hidden_for(uid: str, logins: set) -> None:
    data = _load_hidden_suggestions()
    if logins:
        data[uid] = sorted(logins)
    else:
        data.pop(uid, None)
    _atomic_write(_HIDDEN_SUGG_FILE, json.dumps(data))


def _unhide_suggestion(uid: str, channel: str) -> None:
    """Deliberately monitoring a channel again un-dismisses it.

    Otherwise a channel cleared once would be gone from the list forever, even
    after the user went back to it for months — the dismissal would outlive the
    intent behind it. Adding the stream is a clearer statement of interest than
    the old clearing was of disinterest, so it wins.
    """
    hidden = _hidden_for(uid)
    if channel.lower() in hidden:
        _set_hidden_for(uid, hidden - {channel.lower()})


def _recent_channels(uid: str, monitored: set, hidden: set) -> list:
    """Previously monitored channels, newest activity first. Not truncated —
    callers slice. Clearing needs the full set or "clear all" would only hide
    the visible page and the next eight would slide up to replace them."""
    pdir = Path(settings.local_storage_path) / "profiles" / uid
    try:
        files = sorted(pdir.glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError:
        return []
    return [p.stem for p in files
            if p.stem.lower() not in monitored and p.stem.lower() not in hidden]


@app.get("/streams/suggest")
async def stream_suggestions(request: Request, q: str = ""):
    """Suggestions for the add-stream box, so users don't have to type exact
    channel names. With q: Twitch partial-name search. Without q (zero state):
    the user's previously monitored channels (their profile files, newest
    activity first) + the most-watched live channels right now. Channels the
    user already monitors — or has cleared from the recent list — are filtered
    out."""
    from src.output import twitch_clips
    uid = _current_user_id(request)
    monitored = {(s.get("channel") or "").lower() for s in _streams.values()
                 if s.get("user_id") == uid}
    q = q.strip()
    if q:
        rows = await twitch_clips.search_channels(q)
        return {"results": [r for r in rows if r["login"].lower() not in monitored]}
    recent = _recent_channels(uid, monitored, _hidden_for(uid))[:8]
    global _popular_streams_cache
    ts, popular = _popular_streams_cache
    if time.time() - ts > _POPULAR_STREAMS_TTL:
        popular = await twitch_clips.get_top_streams()
        if popular:   # keep serving the stale list through a Helix hiccup
            _popular_streams_cache = (time.time(), popular)
    return {"recent": recent,
            "popular": [p for p in popular
                        if p["login"].lower() not in monitored][:8]}


# MUST STAY ABOVE @app.delete("/streams/{channel}") — FastAPI resolves in
# declaration order. "/streams/suggest/recent" has two segments after /streams
# so it cannot be swallowed by the single-segment route, but the sibling that
# WOULD be ("/streams/suggest") is deliberately not used for exactly that
# reason: it would be matched as channel="suggest" and delete nothing.
@app.delete("/streams/suggest/recent", status_code=204)
async def clear_recent_suggestions(request: Request):
    """Clear the whole 'Recently monitored' list.

    Hides every channel currently eligible for the list, not just the eight on
    screen — clearing a visible page and watching the next eight slide up to
    take their place is not what anyone means by clear.
    """
    uid = _current_user_id(request)
    monitored = {(s.get("channel") or "").lower() for s in _streams.values()
                 if s.get("user_id") == uid}
    hidden = _hidden_for(uid)
    _set_hidden_for(uid, hidden | {c.lower() for c in
                                   _recent_channels(uid, monitored, hidden)})
    await broadcast({"event": "suggestions_cleared"}, user_id=uid)
    return Response(status_code=204)


@app.delete("/streams/suggest/recent/{channel}", status_code=204)
async def clear_one_recent_suggestion(request: Request, channel: str):
    """Remove a single channel from 'Recently monitored'."""
    if not _CHANNEL_RE.match(channel):
        raise HTTPException(status_code=400, detail="Invalid channel name")
    uid = _current_user_id(request)
    _set_hidden_for(uid, _hidden_for(uid) | {channel.lower()})
    await broadcast({"event": "suggestions_cleared"}, user_id=uid)
    return Response(status_code=204)


# Fraction of the global pool held back so somebody with NO streams can always
# start one. Below this much headroom the pool is "tight" and heavy users get
# cut back first.
_CAPACITY_RESERVE_FRAC = 0.15
# Log a warning once utilisation passes this, so the owner finds out the box is
# filling up before a customer does.
_CAPACITY_WARN_FRAC    = 0.80


# ── LIVE SLOTS ───────────────────────────────────────────────────────────────
# What actually costs the box is a LIVE stream: two OS subprocesses, a chat
# socket and a scoring loop. A registered channel whose streamer is offline
# costs an is_live poll every 30 seconds and nothing else, and offline is the
# normal state — people queue an evening's roster hours ahead.
#
# So the hardware ceiling is enforced HERE, at go-live, rather than at the point
# somebody adds a channel. A worker asks for a slot the moment its channel is
# confirmed live, and if there is none it goes back to waiting and tries again,
# reusing the loop that already handles waiting for the streamer. Nothing gets
# to start metering audio without a slot, so a queued roster all going live at
# once cannot bury the box.
_live_slots: set[str] = set()


def live_stream_count() -> int:
    return len(_live_slots)


def acquire_live_slot(stream_key: str) -> bool:
    """Take a slot for a channel that just went live. False means wait."""
    if stream_key in _live_slots:
        return True                       # already holding one; re-entrant
    if len(_live_slots) >= max(1, settings.max_concurrent_streams):
        log.warning("live_slots_full", held=len(_live_slots),
                    cap=settings.max_concurrent_streams, waiting=stream_key)
        return False
    _live_slots.add(stream_key)
    return True


def release_live_slot(stream_key: str) -> None:
    """Give the slot back. Must be unconditional and idempotent: a slot leaked
    on an error path is capacity this process never gets back until restart."""
    _live_slots.discard(stream_key)


def _check_server_capacity(uid: str) -> None:
    """Guard how many channels may be REGISTERED, fairly.

    NOT the hardware guard. That is acquire_live_slot() above, and the
    distinction matters: this used to cap registrations with the CPU ceiling,
    which refused people for queueing channels that were costing nothing. Prod
    showed 8 registered against a load average of 0.00.

    What this stops is one account filling the process with registrations. The
    bound is deliberately loose because registration is nearly free.

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
    cap   = max(1, settings.max_registered_streams)
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
        # Going back to a channel outranks having once cleared it from the
        # list, so the dismissal is lifted here rather than surviving forever.
        _unhide_suggestion(uid, req.channel)
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
        release_live_slot(stream_key)
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
        release_live_slot(stream_key)
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



# ── Subscription reconciliation ───────────────────────────────────────────────
# Stripe is the only writer of subscription state, and until now it only wrote
# via webhook. That makes a missed delivery PERMANENT and invisible: the
# endpoint is down through a deploy, or an event errors past Stripe's retries,
# and the account is wrong forever with nothing to notice it. Every billing bug
# found in this audit was also, underneath, a bug that nothing would have
# detected on its own.
#
# This walks the accounts that have a Stripe customer and fixes drift both ways
# — restoring access somebody paid for AND removing access somebody stopped
# paying for.
_RECONCILE_INTERVAL = 3600     # hourly; drift is measured in minutes, not seconds
_RECONCILE_GAP      = 0.4      # pause between customers (1 vCPU, Stripe rate limits)


def reconcile_skip_reason(user: dict) -> str | None:
    """Why this account must NOT be reconciled against Stripe, or None.

    Shared by the hourly sweep and the admin's manual sync so the two cannot
    drift apart — they used to be separate implementations, and the manual one
    had a bug the sweep did not.
    """
    if not user.get("stripe_customer_id"):
        return "no Stripe customer is linked to this account"
    # THERE IS DELIBERATELY NO `trialing` SKIP HERE ANY MORE.
    #
    # There used to be one: app-managed access has no Stripe subscription
    # behind it, so reconciling a comp would revoke it the moment Stripe
    # reported nothing live — which was always, because there never was one.
    #
    # That held while `trialing` could ONLY mean a comp. It cannot any more: a
    # card-up-front trial is the opening state of every new paying
    # subscription. Keeping the skip would have excluded the bulk of new
    # customers from the only sweep that catches drift — precisely the people
    # whose access depends on a webhook having landed.
    #
    # The comp is still protected, by the check ABOVE rather than by this one.
    # A comp is app-managed and has no Stripe customer, so it has already
    # returned. Anything reaching this line has a customer id, so its trial is
    # a real Stripe subscription. reconcile_one_user carries the second belt:
    # it refuses to downgrade a trialing user when Stripe reports nothing live,
    # which is the shape of a comped account that subscribed once long ago.
    if user.get("is_admin") or user.get("is_labeler"):
        return "admins and trainers are not billed through Stripe"
    return None


async def reconcile_one_user(user: dict) -> dict:
    """Make one account's local state match Stripe. Returns what happened.

    Applies drift in BOTH directions and broadcasts a visible change to that
    user's open tabs — an access change they can see has to reach them live,
    the same as the webhook path does it.
    """
    from src.auth import users as _rc_store
    from src.billing.plans import get_plan
    from src.billing.stripe_billing import authoritative_subscription

    uid   = user["id"]
    cust  = user.get("stripe_customer_id") or ""
    truth = await authoritative_subscription(cust)
    if truth is None:
        # NOT "no subscription" — we could not ask. Changing anything here is
        # how a Stripe outage cancels paying customers.
        return {"ok": False, "reason": "Stripe could not be reached", "drift": []}

    was   = get_plan(user)
    local = user.get("subscription_status")

    # A COMP THAT ONCE SUBSCRIBED. They hold a stale Stripe customer from an
    # old subscription, so reconcile_skip_reason lets them through, and Stripe
    # honestly reports nothing live — which would revoke the comp an admin
    # deliberately granted. Their trial is app-managed: it has an end date we
    # set, and the middleware already retires it on that date.
    #
    # Only refuses the DOWNGRADE. If Stripe has something live for them, the
    # branches below still apply it, because that is real.
    if local == "trialing" and truth["status"] not in ("active", "trialing"):
        return {"ok": True, "drift": [], "stripe_status": truth["raw"],
                "app_status": truth["status"], "plan": truth["plan"],
                "subscription": truth["subscription"],
                "reason": "left alone: an app-managed trial or comp, which "
                          "Stripe knows nothing about by design"}

    drift = []
    if truth["status"] != local:
        drift.append(("status", local, truth["status"]))
        # Carry Stripe's trial end across, or a reconciled trial lands with no
        # date for the dashboard to count down to.
        _rc_store.update_subscription(
            uid, cust, truth["status"],
            truth.get("trial_end", 0) if truth["status"] == "trialing" else 0)
    if truth["plan"] and truth["plan"] != user.get("plan"):
        drift.append(("plan", user.get("plan"), truth["plan"]))
        _rc_store.set_plan(uid, truth["plan"])

    result = {"ok": True, "drift": drift, "stripe_status": truth["raw"],
              "app_status": truth["status"], "plan": truth["plan"],
              "subscription": truth["subscription"], "reason": ""}
    if not drift:
        return result

    log.warning("subscription_drift_corrected", user_id=uid, customer=cust,
                stripe_status=truth["raw"], drift=drift)
    now = get_plan(_rc_store.get_by_id(uid))
    result["plan_before"], result["plan_after"] = was, now
    if now == was:
        return result
    if now in ("locked", "free"):
        asyncio.create_task(_enforce_stream_limit(uid))
        # `now` is already the resolved plan, so this path knows exactly what
        # they landed on — it just used to say "free" regardless.
        await broadcast(
            {"event": "subscription_expired", "message": _lapse_message(now)},
            user_id=uid)
    else:
        await broadcast(
            {"event": "subscription_active",
             "message": f"Your {now.title()} plan is active."},
            user_id=uid)
    return result


async def subscription_reconcile_task() -> None:
    """Background task: make the local subscription state match Stripe."""
    from src.auth import users as _rc_store

    while True:
        await asyncio.sleep(_RECONCILE_INTERVAL)
        if not settings.stripe_secret_key:
            continue
        checked = fixed = 0
        try:
            for user in _rc_store.get_all():
                if reconcile_skip_reason(user):
                    continue
                await asyncio.sleep(_RECONCILE_GAP)
                checked += 1
                result = await reconcile_one_user(user)
                if result["ok"] and result["drift"]:
                    fixed += 1
            adopted = await _adopt_orphaned_subscriptions()
            if adopted:
                fixed += adopted
        except Exception as exc:
            log.error("subscription_reconcile_failed", error=str(exc))
        if checked:
            log.info("subscription_reconcile_done", checked=checked, corrected=fixed)


async def _adopt_orphaned_subscriptions() -> int:
    """Link live subscriptions to accounts that have no Stripe customer stored.

    THE LOOP THIS BREAKS. A customer id is written by the webhook and nothing
    else. When the webhook does not fire — and on this account it never did,
    because the Stripe endpoint had no customer.subscription.* events enabled
    while still returning 200 to everything it was sent — the account keeps no
    customer id. /billing/checkout then sees no customer, passes customer_id
    None, and Stripe mints a BRAND NEW customer for the next purchase. The same
    person ends up billed two or three times across separate customer records,
    and the duplicate guard never fires because it searches within one customer.

    Stripe already knows the answer: create_checkout_url stamps user_id into the
    subscription's metadata, so the link can be read back without the webhook
    ever working. Reconciling from that makes the whole billing path survive a
    misconfigured (or deleted, or silently 200-ing) webhook endpoint.
    """
    from src.auth import users as _rc_store
    from src.billing.plans import get_plan
    from src.billing.stripe_billing import live_subscriptions_by_user

    subs = await live_subscriptions_by_user()
    if not subs:
        return 0                    # None = unreachable, [] = nothing live
    by_id = {u["id"]: u for u in _rc_store.get_all()}
    adopted = 0
    for s in subs:
        user = by_id.get(s["user_id"])
        if user is None:
            continue                # a deleted account; not ours to fix
        existing = user.get("stripe_customer_id")
        if existing and existing != s["customer"]:
            # Already pointing somewhere else. Overwriting would silently move
            # this person's billing to a different customer record.
            log.warning("orphan_subscription_customer_conflict",
                        user_id=user["id"], stored=existing, found=s["customer"])
            continue
        if existing and user.get("subscription_status") == "active":
            continue                # already known and correct
        was = get_plan(user)
        _rc_store.update_subscription(user["id"], s["customer"], "active")
        if s["plan"]:
            _rc_store.set_plan(user["id"], s["plan"])
        adopted += 1
        log.warning("orphan_subscription_adopted", user_id=user["id"],
                    customer=s["customer"], subscription=s["subscription"],
                    plan=s["plan"] or "legacy")
        now = get_plan(_rc_store.get_by_id(user["id"]))
        if now != was:
            await broadcast({"event": "subscription_active",
                             "message": f"Your {now.title()} plan is active."},
                            user_id=user["id"])
    return adopted


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
    _require_upload_access(uid)
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
    # "locked" — a new account whose 7-day trial ran out — must map to Starter.
    # Without it the upgrade prompt vanishes for exactly the person the paywall
    # exists to convert, which is how a caught-by-test regression looks.
    nxt = {"locked": "starter", "free": "starter", "starter": "pro"}.get(get_plan(user))
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

    CROWD SUGGESTIONS ARE NOT COUNTED. They draw on their own per-plan budget
    (see suggestion_room), so they can never occupy a slot a triggered clip
    wanted — which is the guarantee the old 50% reserve was approximating. On
    the free tier this is what makes "20 clips" mean twenty of OUR clips plus
    the three suggestions, rather than three of the twenty.
    """
    from src.billing.plans import limits_for
    from src.auth import users as _room_store
    cap = limits_for(_room_store.get_by_id(uid))["max_pending"]
    used = sum(1 for c in _clips.values()
               if c.get("status") == "pending" and c.get("user_id") == uid
               and not c.get("suggested"))
    return used, cap


def suggestion_room(uid: str) -> tuple[int, int]:
    """(crowd suggestions waiting on this user, what their plan allows).

    A separate budget from the pending queue, on purpose — see pending_room.
    Free gets 3: enough that the feature can actually show its value on the
    tier where the detector is most likely to be doubted, and few enough that
    a busy channel cannot bury the queue in other people's clips.
    """
    from src.billing.plans import limits_for
    from src.auth import users as _room_store
    cap = limits_for(_room_store.get_by_id(uid)).get("max_suggested", 0)
    used = sum(1 for c in _clips.values()
               if c.get("status") == "pending" and c.get("user_id") == uid
               and c.get("suggested"))
    return used, cap


# Seven days, in seconds. Rolling rather than a calendar week — see the note on
# _LIB_WEEK in plans.py for why a Monday reset is worse for everybody.
LIBRARY_WEEK_SECS = 7 * 24 * 60 * 60


def library_room(uid: str) -> tuple[int, int]:
    """(clips this user has kept in the last seven days, what their plan allows).

    A DIFFERENT QUESTION FROM pending_room. That one caps how many undecided
    clips may wait; this caps how many a user may KEEP. Someone can hit either
    first, and hitting one says nothing about the other.

    COUNTS WHAT IS IN THE LIBRARY, so deleting a clip returns the slot. Counting
    approvals from the append-only ledger instead would be a truer rate limit
    and impossible to work around, but it would tell a user staring at an empty
    library that they are out of room. The cap is on what you store, so it
    counts what is stored.

    `approved_at` is when it entered the library, which is the date this cap is
    about — not created_at, which is when the moment happened. A clip captured
    three weeks ago and kept today is kept today. Clips approved before that
    field existed have no value; they fall back to created_at, which for a
    clip that old is well outside the window either way.
    """
    from src.billing.plans import limits_for
    from src.auth import users as _room_store
    cap = limits_for(_room_store.get_by_id(uid)).get("max_library_week", 0)
    cutoff = time.time() - LIBRARY_WEEK_SECS
    used = sum(1 for c in _clips.values()
               if c.get("status") == "approved" and c.get("user_id") == uid
               and (c.get("approved_at") or c.get("created_at") or 0) >= cutoff)
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
    from src.billing.plans import UNLIMITED_PENDING, limits_for
    user = _miss_store.get_by_id(uid) or {}
    # A queue that cannot fill cannot have been full. Misses recorded against
    # this account are from BEFORE the cap was lifted, and the notice reads in
    # the present tense off the CURRENT cap — so an admin was being told "your
    # review queue is full at 1000000000 clips", asserting a state that is now
    # impossible and printing the sentinel as though it were a real number.
    # Suppressed here rather than in the banner so no client can render it.
    if limits_for(user)["max_pending"] >= UNLIMITED_PENDING:
        return 0
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
    uid = _current_user_id(request)
    _require_upload_access(uid)
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
    _require_upload_access(uid)
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
    _require_upload_access(uid)
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
    _require_upload_access(uid)
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
    _require_upload_access(uid)
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
            .box{{text-align:center;max-width:520px;padding:32px}}
            h1{{font-size:30px;margin-bottom:12px;color:#f87171}}
            p{{color:#a0a0b0;line-height:1.6;margin-bottom:8px}}
            code{{background:rgba(255,255,255,.08);padding:4px 8px;border-radius:4px;font-size:12px}}
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
    # Lifetime outcome counts, read once for everybody rather than per user.
    # These CANNOT come from _clips: rejecting deletes the record and clearing
    # deletes it too, so counting there reports only what survived — a user who
    # rejected 30 of 40 would read as having taken 10 clips, the opposite of
    # the truth. stream_stats is the append-only census that exists for this.
    from src.stats import stream_stats
    outcomes = stream_stats.totals_by_user()
    for u in users:
        uid = u["id"]
        u["stream_count"] = sum(1 for s in _streams.values() if s.get("user_id") == uid)
        # What is in their library RIGHT NOW — a different question from how
        # many they have ever approved, and both are worth seeing.
        u["clip_count"] = sum(1 for c in _clips.values() if c.get("user_id") == uid)
        o = outcomes.get(uid) or {}
        u["clips_caught"]   = o.get("caught", 0)
        u["clips_approved"] = o.get("approved", 0)
        u["clips_rejected"] = o.get("rejected", 0)
        u["clips_cleared"]  = o.get("cleared", 0)
        u["clips_expired"]  = o.get("expired", 0)
        u["clips_kept_pct"] = o.get("kept_pct", 0)
        # The RESOLVED membership, not the raw stored field. Those disagree
        # constantly and the stored one is the misleading half: a legacy
        # $15-era subscriber has no `plan` at all but is effectively Pro, an
        # admin has no subscription but gets Pro, and someone who cancelled
        # keeps a stale plan="pro" while actually being on Free. The admin
        # table has to show what the user really has, so it shows this.
        # How far they got, which is a different question from what they may
        # do. A locked account that never opened checkout and one that paid and
        # was not linked resolve to the SAME plan; only this tells them apart,
        # and the second is somebody owed a refund or a fix.
        # Where the email came from, so the panel can say. A billing address is
        # one somebody typed to receive receipts; a Twitch account address may
        # be years old and unread. Worth telling apart before you rely on it.
        # THE EFFECTIVE STATUS, not the stored one. The stored field only
        # becomes "expired" when the user themselves makes a request — and
        # somebody whose trial ran out and who never came back is exactly the
        # person that never happens for. Reading it raw is why a finished trial
        # showed as "Trial ends <a date last week>". Not written back here: a
        # GET must not mutate, and the middleware persists it on their return.
        if _plans.trial_expired(u):
            u["subscription_status"] = "expired"
        u["email_source"] = u.get("email_source", "") if u.get("email") else ""
        # Two different questions, so two fields.
        #
        #   last_active_at — the last authenticated request or socket connect.
        #     Answers "are they still using this". Read from the same in-memory
        #     clock the idle reaper uses, which is persisted every five minutes,
        #     so it survives a deploy to within that window. The browser's 30s
        #     keepalive ping deliberately does NOT bump it, so an abandoned open
        #     tab reads as idle rather than as a daily user.
        #
        #   last_login_at — the last completed Twitch OAuth. Answers "is their
        #     grant current", which is what decides whether a newly requested
        #     scope can reach them. Only recorded from the day it shipped, so a
        #     0 here means "not since we started counting", NOT "never".
        u["last_active_at"] = _user_last_active.get(uid, 0)
        u["last_login_at"] = u.get("last_login_at", 0)
        u["funnel_stage"] = plans.funnel_stage(u)
        u["funnel_label"] = plans.FUNNEL_LABELS.get(u["funnel_stage"], "")
        u["checkout_started_at"] = u.get("checkout_started_at", 0)
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


@app.get("/admin/clip-refusals")
async def admin_clip_refusals(request: Request):
    """Channels Twitch is currently refusing to clip, worst-recent first.

    Exists because "is this one channel or everywhere" was only answerable by
    grepping journalctl on the right day. A channel drops off this list the
    moment it produces a clip again, so what is here is what is broken NOW.
    """
    _require_admin(request)
    from src.stats import clip_refusals
    rows = clip_refusals.all_rows()
    return {"rows": rows, "total": sum(int(r.get("count", 0)) for r in rows),
            "channels": len(rows)}


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

    # TWO DIFFERENT QUESTIONS, and mixing them is what made the header lie.
    #
    #   by_plan  — ENTITLEMENT. What each account may currently do. A trialing
    #              user resolves to pro here, and so does a comped one.
    #   segment  — HOW THEY GOT THERE, and these are MUTUALLY EXCLUSIVE: every
    #              customer lands in exactly one, so the segments partition the
    #              population and can be read as a breakdown.
    #
    # The panel used to render "N Pro · N Starter · N on trial" out of by_plan
    # plus the trial count, which double-counts: a trialing account is inside
    # the Pro figure AND inside the trial figure, so the numbers summed to more
    # than the population and no reading of them was correct. Free was missing
    # from that line entirely, which mattered little while free was legacy-only
    # and matters most of all now that it is the front door and the majority of
    # the population is on it.
    by_plan: dict[str, int] = {p: 0 for p in plans.PLAN_LIMITS}
    segment: dict[str, int] = {"paying": 0, "trialing": 0, "comped": 0,
                               "grace": 0, "free": 0}
    # Of the PAYING only. The "Paying" tile's own sub-line describes the people
    # in that tile rather than the whole population.
    paying_by_tier: dict[str, int] = {"pro": 0, "starter": 0, "legacy": 0}
    paying = trialing = mrr = legacy = comped = 0
    for u in customers:
        plan = plans.get_plan(u)
        by_plan[plan] = by_plan.get(plan, 0) + 1
        status = u.get("subscription_status")
        if status == "trialing":
            trialing += 1
            segment["trialing"] += 1
        elif status in plans.GRACE_STATUSES:
            # Still customers: Stripe is mid-collection. Neither paying yet nor
            # free, and lumping them into either hides the one segment where a
            # nudge actually recovers money.
            segment["grace"] += 1
        elif status != "active":
            segment["free"] += 1
        if status == "active":
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
                segment["comped"] += 1
                continue
            stored = u.get("plan")
            price = (plans.PLAN_LIMITS.get(plan, {}).get("price", 0)
                     if stored in plans.PAID_PLANS else 0)
            paying += 1
            segment["paying"] += 1
            if price:
                mrr += price
                paying_by_tier[stored] = paying_by_tier.get(stored, 0) + 1
            else:
                legacy += 1
                paying_by_tier["legacy"] += 1

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
            # The mutually-exclusive population breakdown. These sum to `total`
            # exactly, which is the property the old header did not have and
            # the reason its numbers could not be read.
            "segment": segment,
            # The tiers of the people in `paying`, so the Paying tile's
            # sub-line describes the tile's own number rather than the whole
            # population. `legacy` are $15-era subscribers whose price we do
            # not hold locally.
            "paying_by_tier": paying_by_tier,
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
    """Revoke access AND stop the billing that paid for it.

    Revoking used to touch only our own records, so a revoked user lost the
    product and kept being charged for it every month — the worst of both, and
    the fastest route to a chargeback. Their Stripe subscription is cancelled
    here too.

    ORDER: Stripe first, because we still hold the customer id and want its real
    answer; then revoke locally REGARDLESS of what Stripe said. Removing access
    is the admin's actual intent and it must not become contingent on Stripe
    being up. If the cancel failed, the revoke still happens and the response
    says so, so the admin knows to cancel by hand rather than assuming it is
    done.
    """
    _require_admin(request)
    from src.auth import users as user_store

    db_user  = user_store.get_by_id(user_id)
    customer = (db_user or {}).get("stripe_customer_id") or ""
    cancelled: int | None = 0
    if customer:
        from src.billing.stripe_billing import cancel_customer_subscriptions
        cancelled = await cancel_customer_subscriptions(customer)

    user_store.update_subscription(user_id, None, "inactive")
    # Stop their streams immediately and tell any open session live, rather than
    # waiting up to 5 min for the idle reaper to notice the lapsed subscription.
    await _stop_user_streams_now(user_id)
    await broadcast(
        {"event": "subscription_expired",
         "message": "Your access has been revoked — streams have been stopped."},
        user_id=user_id,
    )
    if cancelled is None:
        log.error("revoke_stripe_cancel_failed", user=user_id, customer=customer)
    else:
        log.info("admin_revoked", user=user_id, stripe_cancelled=cancelled)
    # None is surfaced, not swallowed: the admin has to know billing may still
    # be running.
    return {"ok": True, "stripe_cancelled": cancelled,
            "stripe_ok": cancelled is not None}


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(request: Request, user_id: str):
    _require_admin(request)
    # Prevent deleting yourself
    if user_id == request.session.get("user_id"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    from src.auth import users as user_store
    db_user = user_store.get_by_id(user_id)      # read before the record is gone
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

    # Cancel billing BEFORE the record goes. The stripe_customer_id is the only
    # handle we have on their subscription, and user_store.delete destroys it —
    # so a delete that skipped this left the customer being charged forever with
    # nothing on our side left to find them by. The self-service delete path has
    # always done this; the admin path did not.
    customer = (db_user or {}).get("stripe_customer_id") or ""
    cancelled: int | None = 0
    if customer:
        from src.billing.stripe_billing import cancel_customer_subscriptions
        cancelled = await cancel_customer_subscriptions(customer)
    if cancelled is None:
        # Deliberately loud and deliberately NOT fatal: refusing to delete would
        # leave the admin unable to remove an account because Stripe is down.
        # But this is the last moment the customer id exists, so it goes in the
        # log where it can still be acted on.
        log.error("admin_delete_stripe_cancel_failed", user=user_id, customer=customer)

    user_store.delete(user_id)
    return {"ok": True, "stripe_cancelled": cancelled,
            "stripe_ok": cancelled is not None}


@app.post("/admin/users/{user_id}/stripe-sync")
async def admin_stripe_sync(request: Request, user_id: str):
    """Manually re-sync one user's subscription from Stripe, now.

    Runs the SAME reconciliation the hourly sweep does, rather than its own
    version of it. Its own version had four bugs the sweep did not:

      * `limit: 1` with no status filter read whatever subscription Stripe
        returned first as the truth — and a cancelled duplicate can be newer
        than the live one, so syncing a healthy customer could cancel them.
      * A customer with no subscriptions returned "no subscriptions found" and
        changed nothing, so syncing a lapsed account left it active forever —
        the exact case an admin reaches for this button to fix.
      * It never synced the PLAN, only the status, so a tier that drifted
        stayed drifted.
      * It changed the user's access without telling their open tab.
    """
    _require_admin(request)
    from src.auth import users as user_store
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    user = user_store.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    skip = reconcile_skip_reason(user)
    if skip:
        # Refused rather than applied: "sync from Stripe" on a comped account
        # would otherwise revoke the comp the admin themselves granted.
        raise HTTPException(status_code=400, detail=f"Nothing to sync — {skip}.")
    try:
        result = await reconcile_one_user(user)
    except Exception as exc:
        log.error("admin_stripe_sync_error", user_id=user_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Stripe API error — check server logs")
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["reason"])
    # A comped account that once subscribed still holds a stale customer id, so
    # reconcile_skip_reason lets it through — deliberately, because that is also
    # the shape of a real card-up-front trial and those DO need reconciling. The
    # refusal moved here, where we have asked Stripe and know which it is.
    # Silently returning "synced" for a button that changed nothing, and could
    # not have, is how an admin concludes the button is broken.
    if result.get("reason") and not result["drift"]:
        raise HTTPException(status_code=400,
                            detail=f"Nothing to sync — {result['reason']}.")
    log.info("admin_stripe_sync", user_id=user_id,
             customer=user.get("stripe_customer_id"),
             status=result["app_status"], drift=result["drift"])
    return {"synced": True, "stripe_status": result["stripe_status"],
            "app_status": result["app_status"], "plan": result["plan"],
            "changed": [d[0] for d in result["drift"]]}


@app.get("/admin/users/{user_id}/streams")
async def admin_user_streams(request: Request, user_id: str):
    """Admin: streams currently registered for a specific user."""
    _require_admin(request)
    return [s for s in _streams.values() if s.get("user_id") == user_id]


# STOPPING, NOT BANNING. Both routes below free the slot and nothing else: the
# user keeps their plan, their clips and their right to start the channel again
# the moment they notice. That is deliberate — the reason this exists is a box
# that is CPU-bound before it is slot-bound, so the admin needs to shed load
# without it being a punishment. Taking away the ability to re-add is a
# different decision (revoke) and has its own button.
#
# The user is TOLD. A stream silently vanishing from someone's dashboard is
# indistinguishable from a bug, and the person it happens to is the one who
# cannot tell the difference — they would report it, or quietly conclude the
# product is broken. stop_stream_internal already broadcasts stream_removed so
# the row disappears live; the notice on top of it says who did it and that
# they can start it again.
# Returns a JSON body rather than 204, which is the convention every endpoint
# the admin page's api() helper calls already follows — that helper ends in
# `return r.json()`, so a 204 makes it throw on an empty body and report a
# successful stop as an error. The 204 endpoints here (invites, reviews) are
# deliberately called with a raw fetch instead.
@app.delete("/admin/users/{user_id}/streams/{channel}")
async def admin_stop_user_stream(request: Request, user_id: str, channel: str):
    """Admin: stop one of a user's streams."""
    _require_admin(request)
    if not _CHANNEL_RE.match(channel):
        raise HTTPException(status_code=400, detail="Invalid channel name")
    if not await stop_stream_internal(channel, user_id):
        raise HTTPException(status_code=404, detail="Stream not found")
    await broadcast(
        {"event": "streams_stopped_by_admin", "count": 1, "channel": channel},
        user_id=user_id,
    )
    log.info("admin_stopped_stream", admin=_current_user_id(request),
             user=user_id, channel=channel)
    return {"stopped": 1, "channel": channel}


@app.delete("/admin/users/{user_id}/streams", status_code=200)
async def admin_stop_all_user_streams(request: Request, user_id: str):
    """Admin: stop every stream a user has running."""
    _require_admin(request)
    channels = [s.get("channel") for s in _streams.values()
                if s.get("user_id") == user_id and s.get("channel")]
    stopped = 0
    for ch in channels:
        if await stop_stream_internal(ch, user_id):
            stopped += 1
    if stopped:
        # ONE notice for the batch, not one per stream. Each stop already sends
        # its own stream_removed so every row clears; a toast per channel would
        # bury the screen in duplicates of the same news.
        await broadcast(
            {"event": "streams_stopped_by_admin", "count": stopped},
            user_id=user_id,
        )
    log.info("admin_stopped_all_streams", admin=_current_user_id(request),
             user=user_id, count=stopped)
    return {"stopped": stopped}


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


@app.get("/tutorial/content")
async def tutorial_content_json():
    """The walkthrough as data, for the dashboard's own Tutorial tab.

    The dashboard cannot simply link to /tutorial: it is a long-lived SPA
    holding a live socket, and navigating away throws that state out. That is
    why the link used to open a new tab — which works, but leaves the reader
    flipping between two windows to follow steps about the one they left.

    Serialised from tutorial_content, the same dataclasses the public page
    renders, so the two cannot drift. Registered ABOVE the `/{slug}` catch-all
    for the same reason /tutorial is; a two-segment path is not matched by it,
    but keeping the tutorial routes together is what stops the next one being
    added in the wrong place.

    Not in _OPEN_PATHS: the public page already serves signed-out readers, and
    this exists for the app. No reason to open a second door onto it.
    """
    from src.dashboard import tutorial_content
    return tutorial_content.as_dict()


@app.get("/compare", response_class=HTMLResponse)
async def compare_page():
    """Highlightz against Opus Clip and Eklipse. Public, so it can be linked
    and indexed.

    MUST STAY ABOVE `@app.get("/{slug}")` for the same reason /tutorial does —
    the catch-all matches any single-segment path and FastAPI resolves in
    declaration order.
    """
    from src.dashboard.compare_html import render
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
        # Labelled HERE rather than in the template, because the stored value is
        # the stringified enum ("SignalType.CHAT_VELOCITY") and this endpoint is
        # the only thing that knows it. Rendering it raw is what put a Python
        # repr on the Settings screen.
        from src.trigger.signals import signal_label
        top = max(signals, key=signals.get) if signals else ""
        c["top_signal"] = signal_label(top) or "—"
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

    # THE FRAMES. Real clip previews from the curated showcase, or the
    # product's own screens where nothing is curated yet. Per request, so a
    # newly featured clip is on the page with no restart.
    frames = _frames()
    if frames:
        hero = _frame_tag(frames[0], "", _frame_alt(frames[0], "A moment"), eager=True)
        end = _frame_tag(frames[-1], "", _frame_alt(frames[-1], "A moment"))
        scrub = "".join(_frame_tag(e, "", _frame_alt(e, "A moment")) for e in frames[:3])
    else:
        hero, end = _screen_tag(0, "", eager=True), _screen_tag(3, "")
        scrub = "".join(_screen_tag(k, "") for k in range(3))
    html = (html.replace("<!--FRAME_HERO-->", hero, 1)
                .replace("<!--FRAME_END-->", end, 1)
                .replace("<!--SCRUB_FRAMES-->", scrub, 1)
                .replace("<!--SHELF-->", _shelf_html(_frames("gallery")), 1))
    try:
        from src.auth import users as _users
        streamers = len(_users._load())
    except Exception:
        streamers = 0
    kept_now, _n = public_keep_rate()
    html = html.replace("<!--BIGNUMS-->",
                        _bignums_html(get_clip_counter(), kept_now, streamers), 1)

    # The keep rate is baked in for the same reason and is independent of the
    # count: it can be publishable while the count is still zero (a fresh
    # counter file) and withheld while the count is large (not enough judged).
    # Nesting it under the `total <= 0` return would have tied the two together
    # for no reason other than where the code sat.
    kept, _sample = public_keep_rate()
    if kept is not None:
        html = html.replace(
            '<div class="stat stat-big" id="stat-kept" style="display:none">',
            '<div class="stat stat-big" id="stat-kept">', 1)
        html = html.replace('<span id="lp-kept" data-kept="0">0%</span>',
                            f'<span id="lp-kept" data-kept="{kept}">{kept}%</span>', 1)

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
    Exposes only aggregates; nothing user-identifying."""
    kept, sample = public_keep_rate()
    return {"clips_total": get_clip_counter(),
            # null, not 0, below the sample floor. A landing page that prints
            # "0% kept" because nobody has reviewed anything yet is worse than
            # one that prints nothing, and the client tells them apart by type.
            "kept_pct": kept,
            "kept_sample": sample}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the icon at the domain root as well as via <link rel="icon">.

    Google's favicon crawler asks for /favicon.ico at the root in ADDITION to
    reading the link tag, and a 404 here is the most common reason a site shows
    the default globe in search results instead of its own mark. The link tag
    was correct all along; this path simply did not exist.

    Kept as a route rather than a file so there is one icon on disk — a second
    copy at the root is the kind of thing that silently goes stale when the
    logo changes.
    """
    icon = _STATIC_DIR / "icon.png"
    if not icon.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        icon,
        media_type="image/png",
        # A favicon changes about never, and Google re-fetches it on its own
        # schedule; a long cache keeps it out of the request path entirely.
        headers={"Cache-Control": "public, max-age=604800"},
    )


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
        # Personal invite and referral links. Nothing secret behind them, but
        # they are one-to-one links meant for the person they were sent to;
        # indexing them puts somebody's referral code in search results.
        "Disallow: /i/\n"
        "Disallow: /r/\n"
        "Sitemap: https://highlightz.app/sitemap.xml\n"
        # A comment, because robots.txt has no directive for this and inventing
        # one would just be ignored. /llms.txt is found at its well-known path;
        # this is here for the crawlers that read the file as text, and the
        # rel="alternate" in each page head is the machine-readable half.
        "# LLM-readable summary: https://highlightz.app/llms.txt\n"
        "# Full public copy as markdown: https://highlightz.app/llms-full.txt\n"
    )


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """What this product is, in the format a language model reads best.

    WHY IT EXISTS. A model answering "what should I use to clip my Twitch
    stream automatically" reads pages, not marketing. HTML makes it infer the
    product from nav, CSS and copy written to persuade a human; a short
    markdown brief states the same facts plainly and links the pages worth
    reading. This is the /llms.txt convention (llmstxt.org) — a growing number
    of crawlers look for it, and the ones that do not lose nothing.

    EVERY NUMBER IS DERIVED. Same rule as every other surface in this codebase:
    the plan limits come from PLAN_LIMITS, so this cannot start advertising
    figures the product stopped offering. That failure has already happened
    twice here, on the pricing page and on /compare.

    NOTHING HERE IS NON-PUBLIC. It is the landing page's own claims in plainer
    words — no internals, no counts of real users, no channel names.
    """
    from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
    f, st, pro = PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]

    def keeps(p: dict) -> str:
        n = p["max_library_week"]
        return "unlimited clips kept" if n >= UNLIMITED_PENDING else f"{n} clips kept a week"

    sig = ", ".join(t.lower() for t in _signal_titles().values()) or (
        "chat erupts, loud reaction, chat calls for the clip, viewers flood in, "
        "emotions run high, silence then chaos, chat speaks as one")
    return f"""# Highlightz

> Highlightz watches live Twitch streams and creates the clip itself, the
> moment something happens. It is not an editor you upload footage to: it
> monitors the live broadcast, scores every second, and calls Twitch's official
> Clips API on your behalf. Not AI: a readable formula, and every clip shows
> which signal fired.

The full public copy — every FAQ answer, the walkthrough, the plans and the
comparison — is at https://highlightz.app/llms-full.txt.

## What it does

- Monitors live Twitch channels continuously and clips automatically, with no
  one watching the stream. Add a channel before it goes live and it is
  rechecked every 30 seconds; monitoring stops after 8 hours without the
  dashboard being opened.
- Scores each second from seven live signals ({sig}) against that channel's
  own threshold, so a small channel and a huge one are judged the same way.
- Creates real Twitch clips through the official Clips API using your own
  authorised Twitch account. It never records, downloads, re-hosts or stores
  stream video.
- Highlight clips: alongside the score, a second way of finding moments. They
  arrive in the review queue marked Highlight, in purple, are usually the
  higher-quality clips, and a green label marks the ones that stood out even
  more. Each plan has its own Highlight allowance.
- Puts every clip in a review queue first. Nothing is published automatically.
  Approving and rejecting tunes the channel's threshold to your taste.
- Scans finished broadcasts (VODs) for highlights on the Pro plan.
- Any Twitch broadcaster can opt their channel out at any time; it takes
  effect immediately across every account.

## Who it is for

Streamers who want their own highlights clipped while they play, and clippers
and editors who follow several channels at once and cannot watch them all.

## Plans

- Free — $0, no card, no time limit. {f['max_streams']} channel monitored,
  {f['max_pending']}-clip review queue, {f['max_suggested']} Highlight clips,
  {keeps(f)}.
- Starter — ${st['price']}/month. {st['max_streams']} channels at once,
  {st['max_pending']}-clip queue, {st['max_suggested']} Highlight clips,
  {keeps(st)}.
- Pro — ${pro['price']}/month. {pro['max_streams']} channels at once,
  {pro['max_pending']}-clip queue, {pro['max_suggested']} Highlight clips,
  {keeps(pro)}, plus the VOD Scanner.

Nothing is metered by the minute: a plan buys channels, and a channel is
watched for every second it is live. Cancelling returns the account to Free
and keeps every approved clip.

## How it differs from upload-based clippers

Tools like Opus Clip and Eklipse take a finished video and cut it up
afterwards, and both sell a subscription and then meter it in credits or
minutes that run out and have to be bought again. Highlightz watches the
stream live and clips as it happens, so a moment is captured while it is
still on air. Because clips are made through Twitch's own API, they live on
Twitch under the streamer's account rather than being re-hosted elsewhere.

## Pages

- [Home](https://highlightz.app/): what it catches, how it scores, pricing, FAQ.
- [Tutorial](https://highlightz.app/tutorial): step-by-step setup and how each
  screen works.
- [Comparison](https://highlightz.app/compare): Highlightz vs Opus Clip vs
  Eklipse on price, credits and features.
- [Terms of Service](https://highlightz.app/tos)
- [Privacy Policy](https://highlightz.app/privacy)
- [Cookie Policy](https://highlightz.app/cookies)
- [Broadcaster opt-out](https://highlightz.app/opt-out): any Twitch streamer
  can remove their channel from the service here.

## Notes

- Kick support is not live yet.
- Highlightz is operated by ANTI Technology LLC. Support: support@highlightz.app.
"""


def _md_text(html: str) -> str:
    """Rendered copy as one line of plain text: tags out, entities decoded."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html))).strip()


def _landing_faq_pairs() -> list[tuple[str, str]]:
    """The landing FAQ's questions and answers, from the same markup the page
    renders and the FAQPage schema is built from."""
    return [(_md_text(q), _md_text(a)) for q, a in re.findall(
        r'<summary class="faq-q">(.*?)</summary>\s*<p class="faq-a">(.*?)</p>',
        LANDING_HTML, re.S)]


@app.get("/llms-full.txt", response_class=PlainTextResponse)
async def llms_full_txt():
    """The whole public site as one markdown file (the llms-full.txt half of
    the llmstxt.org convention). Generated from the content modules the pages
    render from, so it cannot say something the pages do not. Nothing
    non-public: it is the same copy, minus the HTML."""
    from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
    from src.dashboard import compare_content as CC, tutorial_content as TC
    f, st, pro = PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]

    def week(p: dict) -> str:
        n = p["max_library_week"]
        return "unlimited" if n >= UNLIMITED_PENDING else str(n)

    out = []
    w = out.append
    w("# Highlightz — the full public copy\n")
    w("> Automatic Twitch clipping. Highlightz watches a live channel, scores every "
      "second from seven live signals against that channel's own threshold, and when "
      "the score crosses it creates a real Twitch clip through the official Clips API. "
      "Every clip lands in a review queue first. Nothing is recorded or re-hosted. "
      "Operated by ANTI Technology LLC. Short brief: https://highlightz.app/llms.txt\n")

    w("## The seven signals\n")
    for t in _signal_titles().values():
        w(f"- {t}")
    w("")

    w("## Plans (from the pricing page)\n")
    w("| | Free | Starter | Pro |")
    w("|---|---|---|---|")
    w(f"| Price | $0, no card, no time limit | ${st['price']}/month | ${pro['price']}/month |")
    w(f"| Channels at once | {f['max_streams']} | {st['max_streams']} | {pro['max_streams']} |")
    w(f"| Clips held for review | {f['max_pending']} | {st['max_pending']} | {pro['max_pending']} |")
    w(f"| Highlight clips | {f['max_suggested']} | {st['max_suggested']} | {pro['max_suggested']} |")
    w(f"| Clips kept per week | {week(f)} | {week(st)} | {week(pro)} |")
    w(f"| VOD Scanner | {'Yes' if f['vod'] else 'No'} | {'Yes' if st['vod'] else 'No'} | {'Yes' if pro['vod'] else 'No'} |")
    w("\nMove between plans whenever you like; cancel from the Account tab. Cancelling "
      "returns the account to Free and keeps every approved clip. Streamers can opt out "
      "at any time and it applies everywhere at once.\n")

    w("## FAQ (from the home page)\n")
    for q, a in _landing_faq_pairs():
        w(f"### {q}\n\n{a}\n")

    w("## Walkthrough (from /tutorial)\n")
    w(TC.HERO_LEAD + "\n")
    w(f"### {TC.QUICKSTART_TITLE}\n\n{TC.QUICKSTART_LEAD}\n")
    for sec in TC.QUICKSTART + TC.FEATURES:
        w(f"### {sec.title}" + (f" ({sec.plan})" if sec.plan else "") + "\n")
        if sec.body:
            w(sec.body.replace("**", "") + "\n")
        for i, step in enumerate(sec.steps, 1):
            w(f"{i}. {step.replace('**', '')}")
        if sec.steps:
            w("")
        if sec.note:
            w("Note: " + sec.note.replace("**", "").replace("*", "") + "\n")
        if sec.tip:
            w("Tip: " + sec.tip.replace("**", "") + "\n")
    w(f"### {TC.FAQ_TITLE}\n")
    for q, a in TC.FAQ:
        w(f"#### {q}\n\n{_md_text(a)}\n")

    w("## Comparison (from /compare)\n")
    w(CC.HERO_LEAD + "\n")
    for prod in CC.PRODUCTS:
        src = "our own pricing" if prod.is_us else f"as published at {prod.source_url}, checked {prod.checked_on}"
        w(f"### {prod.name}\n\n{prod.tagline} ({src})\n")
        for pl in prod.plans:
            w(f"- {pl.name}: {pl.price}. {pl.note}")
        w("")
    w(f"### {CC.THE_MATH['title']}\n\n" + CC.THE_MATH["body"].replace("\n\n", "\n\n") + "\n")
    w(f"### {CC.CREDITS['title']}\n\n{CC.CREDITS['lead']}\n")
    w("| | Highlightz | Opus Clip | Eklipse |")
    w("|---|---|---|---|")
    for label, ours, opus, ekl in CC.CREDITS["rows"]:
        w(f"| {label} | {ours} | {opus} | {ekl} |")
    w(f"\nSources, read on {CC.CREDITS_CHECKED_ON}: " + "; ".join(f"{t} ({u})" for t, u in CC.CREDITS["sources"]) + "\n")
    w("### Feature by feature\n")

    def cell(v):
        return "Yes" if v is True else ("No" if v is False else str(v))
    w("| Feature | Highlightz | Opus Clip | Eklipse | Why |")
    w("|---|---|---|---|---|")
    for feat, ours, opus, ekl, why in CC.FEATURES:
        w(f"| {feat} | {cell(ours)} | {cell(opus)} | {cell(ekl)} | {why} |")
    w(f"\n### {CC.THEY_DO_BETTER['title']}\n")
    for t, d in CC.THEY_DO_BETTER["points"]:
        w(f"- {t} {d}")
    w("\n### Before you decide\n")
    for q, a in CC.FAQ:
        w(f"#### {q}\n\n{a}\n")
    w(f"### {CC.CLOSER['title']}\n\n{CC.CLOSER['body']}\n")

    w("## Legal\n")
    w("- Terms of Service: https://highlightz.app/tos")
    w("- Privacy Policy: https://highlightz.app/privacy")
    w("- Cookie Policy: https://highlightz.app/cookies")
    w("- Broadcaster opt-out: https://highlightz.app/opt-out")
    return "\n".join(out) + "\n"


# Which source file actually renders each public page. This is what makes
# <lastmod> honest: the pages are generated from Python, so the mtime of the
# module that builds one IS the date that page last changed. Deploys are a git
# checkout, so the mtime moves when — and only when — the file is rewritten.
_PAGE_SOURCE = {
    "/":         "src/dashboard/api.py",
    "/tos":      "src/dashboard/api.py",
    "/privacy":  "src/dashboard/api.py",
    "/cookies":  "src/dashboard/api.py",
    "/opt-out":  "src/dashboard/api.py",
    "/tutorial": "src/dashboard/tutorial_content.py",
    "/compare":  "src/dashboard/compare_content.py",
}


def _page_lastmod(path: str) -> str:
    """W3C date for a page, or "" if the file cannot be read.

    A MISSING lastmod IS BETTER THAN A WRONG ONE. A sitemap that claims every
    page changed today, every day, is the single fastest way to get crawlers to
    stop trusting the file — so anything unreadable is simply omitted rather
    than filled in with now().
    """
    src = _PAGE_SOURCE.get(path)
    if not src:
        return ""
    try:
        ts = (Path(__file__).resolve().parents[2] / src).stat().st_mtime
    except OSError:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


@app.get("/sitemap.xml")
async def sitemap_xml():
    pages = ["/", "/tutorial", "/compare", "/tos", "/privacy", "/cookies", "/opt-out"]
    urls = ""
    for p in pages:
        mod = _page_lastmod(p)
        urls += ("<url><loc>https://highlightz.app" + p + "</loc>"
                 + (f"<lastmod>{mod}</lastmod>" if mod else "")
                 + "</url>")
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
    from src.auth.optout import is_opted_out
    if is_opted_out(str(clip.get("channel") or "")):
        raise HTTPException(
            status_code=400,
            detail="This broadcaster has opted out of Highlightz, so their clips "
                   "cannot be featured on the landing page.")
    # The landing hero plays featured clips in an iframe, and an age-gated clip
    # cannot play in one — Twitch has no way to confirm a viewer's age inside a
    # third-party frame. Featuring one puts a dead black player on the marketing
    # page, which is the first thing a visitor sees. The dashboard routes these
    # to Twitch instead; the landing page has no signed-in viewer to route.
    if clip.get("age_restricted"):
        raise HTTPException(
            status_code=400,
            detail="This clip is age-restricted on Twitch, so it cannot play in "
                   "the landing page player. Feature a different one.")
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


def _excluded_from_learning(clip: dict) -> bool:
    """A clip our scoring did not produce, so a review of it teaches us nothing.

    Every telemetry and learning path has to check this. Such a clip did NOT
    come from our formula running on this user's channel, so counting it would:
      * inflate the per-channel clip record (a "kept" with no matching
        "caught", which is the number being shown to streamers),
      * teach that channel's profile from a decision the formula never made,
      * and put a mislabelled row in the training set.

    TWO KINDS QUALIFY, for the same reason:

    `grabbed` — copied from another admin's showcase.

    `suggested` — surfaced because VIEWERS clipped the moment, with the score
    deliberately never consulted (src/trigger/suggested_clips.py). This one is
    the sharper trap of the two, because the damage runs BACKWARDS: rejecting a
    suggestion would call record_clip(approved=False), which raises the
    channel's trigger threshold by 0.75 — punishing the detector for a moment
    it never claimed, and making it fire LESS on the very channel where the
    crowd is finding things it missed. Its trigger_signals are empty besides,
    so a training row from it would be a label attached to no features.

    Renamed from `_is_grabbed` when the second kind arrived: the old name had
    become a lie about what the check means, and a future reader calling it on
    a suggested clip would have had every reason to expect False.
    """
    return clip.get("source") == "grabbed" or bool(clip.get("suggested"))


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


class PlacementBody(BaseModel):
    where: str            # "hero" | "gallery"
    on: bool


@app.post("/admin/showcase/{clip_id}/placement")
async def admin_showcase_placement(request: Request, clip_id: str,
                                   body: PlacementBody):
    """Admin: choose whether a featured clip appears in the hero wall, the
    examples grid, or both.

    One curated list, two destinations. The wall shows four channels being
    scored and wants variety across channels; the grid is a spread of the best
    clips and can happily repeat a channel. Before this they were the same
    list, so tuning one wrecked the other.
    """
    _require_admin(request)
    if body.where not in ("hero", "gallery"):
        raise HTTPException(status_code=400,
                            detail="where must be 'hero' or 'gallery'")
    items = _load_showcase()
    entry = next((e for e in items if e.get("id") == clip_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Clip is not featured")
    entry[body.where] = bool(body.on)
    _save_showcase(items)
    await broadcast({"event": "showcase_updated"})
    return {"id": clip_id, "hero": entry.get("hero", True),
            "gallery": entry.get("gallery", True),
            "hero_count": sum(1 for e in items if e.get("hero", True)),
            "gallery_count": sum(1 for e in items if e.get("gallery", True))}


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
<meta name="description" content="Highlightz watches every channel you clip for — up to 10 at once — and creates the Twitch clip the moment something pops. Chat spikes, audio pops, hype moments. Transparent formula, not AI. Free to start — no card, no time limit.">
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
<meta property="og:description" content="Automatic Twitch clipping across every channel you watch — a transparent formula, not AI. Free to start — no card, no time limit.">
<!-- Preview card: social platforms cache this image keyed on the URL, so the
     filename must change whenever the art does. Source, build and the full
     history: scripts/og_card.html, scripts/build_og_card.mjs. -->
<meta property="og:image" content="https://highlightz.app/static/og-card-v6.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="The Highlightz logo: a magenta-to-violet H with a play mark, and the Highlightz wordmark, on black.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Highlightz — Never miss a highlight again">
<meta name="twitter:description" content="Automatic Twitch clipping across every channel you watch — a transparent formula, not AI. Free to start — no card, no time limit.">
<meta name="twitter:image" content="https://highlightz.app/static/og-card-v6.png">
<meta name="twitter:image:alt" content="The Highlightz logo: a magenta-to-violet H with a play mark, and the Highlightz wordmark, on black.">
<link rel="alternate" type="text/markdown" href="https://highlightz.app/llms.txt" title="Highlightz for language models">
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

     MOTION — one curve for the entire site, and one named exception.
       --ease         the curve everything uses
       --ease-spring  a slight overshoot, for the two places something is
                      meant to read as snapping into position
       --dur-fast 150ms hover and state / --dur-slow 400ms entrance /
       --dur-event 900ms, reserved for the score wall trigger
       transform and opacity ONLY. Entrances go on grouped CHILDREN, never on
       section containers — the same fade-up on every section is the tell.
     ══════════════════════════════════════════════════════════════════════ */

  /* Self-hosted, subsetted — no third-party font dependency. */
  /* TITLES ONLY, and now every big one of them — the section headings, the
     hero's headline and the closing line. The scope was deliberately narrow
     before (a display face on four headings stops being an accent) and was
     widened on the owner's instruction; the trade is that the script IS the
     page's title voice now rather than a flourish at the end.

     Lobster is a SCRIPT face: its letters are drawn to connect in lowercase,
     so text-transform:uppercase mangles it — every rule below deliberately
     drops uppercase, and a test asserts none creeps back. It ships ONE weight
     (400); asking for bold makes the browser smear the glyphs. Both hold
     wherever it is used, which is why the test checks the rules rather than a
     list of selectors.

     NOT the cover wordmark. That is the mono, matching the nav's lockup,
     settled separately and deliberately. */
  @font-face{font-family:'Lobster';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/lobster-400.woff2) format('woff2')}
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  /* METRIC-MATCHED FALLBACK. Sora is the only one of the three faces that
     causes layout shift: isolated by loading one font at a time, it measured
     CLS 0.0416 on its own while Lobster and Plex Mono came in at 0.0004 and
     0.0001. The page total was 0.0759 against a 0.05 target, and the shift
     landed on the hero CTA row at the instant of the swap.

     The numbers are measured, not guessed. The same string at 100px is
     3154.6px in Sora and 2790.5px in Arial — a ratio of 1.1305, tuned to 1.144 after measuring the two
     against each other with both actually loaded — and Sora's
     ascent/descent are 97/29 against Arial's 91/21. size-adjust scales the
     fallback to Sora's advance; the overrides restate those metrics against
     the adjusted em, so the line box is the same height before and after the
     swap and nothing below it moves. */
  @font-face{font-family:'Sora Fallback';font-style:normal;font-weight:100 900;
    src:local('Arial'),local('Helvetica'),local('Liberation Sans');
    size-adjust:114.4%;ascent-override:84.8%;descent-override:25.3%;line-gap-override:0%}
  /* The instrument face. Every number a visitor reads is a measurement, so it
     is set in a mono with real tabular figures — a score that shifts width as
     it counts is a score you cannot read at a glance. */
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}

  :root{
    /* SURFACES — three tones of the same warm plum-black, lightest to darkest.
       The page is dark again, but a step warmer and lighter than the old
       #0E0B11 so the panels can sit BELOW it and read as lit objects. */
    --bone:#0A0A0C;   /* page base below the cover: black, a shade off pure */
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
    --sans:'Sora','Sora Fallback',system-ui,sans-serif;
    --display:'Lobster',Georgia,serif;
    /* ONE curve, whole site. Durations are the only thing that varies. */
    --ease:cubic-bezier(.16,1,.3,1);
    --t-micro:150ms; --t-move:300ms; --t-enter:600ms; --t-slow:800ms;
    /* The same three names the dashboard uses, so one vocabulary covers both
       surfaces. The four above are kept because existing rules reference them. */
    --dur-fast:150ms; --dur-slow:400ms; --dur-event:900ms;
    /* The one deliberate exception to the single curve: a slight overshoot,
       for the two places something is meant to feel like it snapped into
       position rather than eased there. Named rather than written out, so
       nobody adds a third variant by hand. */
    --ease-spring:cubic-bezier(.34,1.4,.64,1);
    /* SPACING SCALE. Section rhythm is --s-9; the score wall, and only the
       score wall, gets --s-10. If --s-10 appears twice, the second is wrong. */
    --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px;
    --s-6:32px; --s-7:48px; --s-8:64px; --s-9:96px; --s-10:128px;
    /* TYPE SCALE. Seven steps. The nine fractional sizes this replaced never
       landed on a device pixel. */
    --t-caption:12px; --t-small:14px; --t-body:16px; --t-h3:17px;
    --t-h2:24px; --t-h1:30px;

    /* Measure. Text sections hold this; product sections deliberately do not. */
    /* 52ch, NOT the 68 the plan called for. `ch` is the width of the digit
       zero, which in Sora is 9.17px at 14px while the average character is
       6.9px — so a 68ch cap actually allowed 90 characters, well past the
       band it was supposed to enforce, and thirteen blocks stayed too wide
       while appearing to be capped. Measured with canvas metrics against each
       element's own resolved font rather than assumed. 52ch lands at 67-69
       real characters at every size on the scale. */
    --measure:52ch;
    /* 0..1 — how hard the trigger is firing right now. Everything that is
       "light" on this page reads from this one number, including the
       through-line's section wash. It is the product's own mechanic driving
       the page's lighting, which is the point. */
    --lit:0;
    /* The sticky bar's height, which anchor targets subtract so a jump to
       #pricing does not land the heading underneath it, and which slide 2
       subtracts so nav + hero come to exactly one screen. This is only the
       FALLBACK: the links wrap in a band around 940px and the bar grows, so a
       hardcoded number is wrong across a 200px stretch. The real value is
       measured and written here at runtime — see the nav-height block near the
       end of the body. */
    --nav-h:71px;
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
  /* ── ANCHOR TARGETS CLEAR THE NAV ────────────────────────────────────────
     Every in-page link was landing its section at viewport top — which is
     UNDERNEATH a 71px sticky nav. #features has 44px of top padding, so its
     heading came to rest at y=44 with the nav covering it down to y=71: over
     half of "What you get" hidden. From a short scroll that reads as the link
     doing nothing at all, because the thing you clicked toward is the thing
     the nav is sitting on.

     scroll-margin-top is exactly the mechanism for this and the page never had
     it. Applied to the elements that are actually linked to, so a section
     added later inherits the fix instead of quietly repeating the bug. */
  section[id],header[id],article[id]{scroll-margin-top:calc(var(--nav-h) + 12px)}
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
    font-size:16px;line-height:1.6;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}

  /* ── Nav. It sits IN the room rather than on top of it: no border under it,
     no second glowing hairline, no glass plate or blur — the three things that
     used to announce it as a separate strip. The background is the hero band's
     own top tone (#09070C, the literal below), so where the bar arrives it and
     the wall are one continuous surface with no edge between them.

     The bottom 30% fades to nothing instead of ending on a line. That is what
     replaces the hairline: scrolled down over the lighter sections the bar
     dissolves into what is under it rather than stopping on a hard edge. ── */
  /* FIXED, over the top of the page, on the owner's call: the bar sits on
     the cover the way a nav sits on a hero image, and stays there. It is
     out of flow, so the hero pads its own top by --nav-h to keep slide 2
     one screen (see .hero.hero-band). Over the cover's #000 the bar's
     near-black is invisible; over the site it is the hero band's own top
     tone, so there is never an edge under it. */
  .nav{position:fixed;top:0;left:0;right:0;z-index:60;
    background:linear-gradient(180deg,#09070C 0%,#09070C 70%,rgba(9,7,12,0) 100%);
    display:flex;align-items:center;gap:16px;padding:12px 24px 16px}
  .nav-logo{display:flex;align-items:center;gap:8px;flex-shrink:0}
  .nav-logo img{height:22px}
  .nav-logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink)}
  .nav-links{display:flex;align-items:center;gap:4px;margin-left:12px}
  .nav-link{font-family:var(--mono);font-weight:400;font-size:12px;letter-spacing:.02em;
    color:var(--ink-3);padding:8px 12px;border-radius:3px;
    transition:color var(--dur-fast),background var(--dur-fast)}
  .nav-link:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .nav-right{margin-left:auto;display:flex;align-items:center;gap:8px}
  /* 940, not 700: measured, the bar needs 818px with its links shown, so
     anything from ~820 to 940 pushed Get started off a tablet's right edge.
     The tutorial page's copy of this bar uses the same number on purpose. */
  @media(max-width:940px){ .nav-links{display:none} }
  @media(max-width:700px){
    .nav{padding:12px 16px 16px;gap:8px}
    .nav-logo span{display:none}
  }

  /* ── THE COVER. One screen: the mark, the name, the numbers. ──────────────
     #000 and not var(--void): the point is that it is emptier than the site
     behind it, and --void is the site's black.
     The lockup is centred on the SCREEN, not in the space above the cue — the
     cue is out of flow at the bottom edge. Giving it a grid row of its own
     pushed everything up and left a dead band under the numbers. The symmetric
     padding floor (72px) is taller than the cue (~65px), which is what keeps
     the two apart on a short window without positioning anything by hand.
     min-height, not height: at 375 the band stacks and can outgrow the
     viewport, and centring inside a fixed height clips it off the TOP where it
     cannot be scrolled to. */
  .cover{background:#000;min-height:100svh;
    display:flex;flex-direction:column;justify-content:center;align-items:center;
    padding:clamp(72px,12vh,140px) clamp(16px,4vw,72px);
    position:relative;overflow:hidden}
  /* The seam. Pitch black meeting the site's plum-black in a hard cut is what
     made the two screens read as two different websites -- the slide swept
     straight through an edge between two worlds. So the site's own light
     leaks up onto the bottom of the cover: its base tone (the rgba is
     --bone #17131C) blending in, and the same purple every seam wash on the
     page uses, rising from below the fold. The cover becomes the same room
     with the lights off, and the cue's line points down into the glow. */
  /* The linear layer's target is the HERO's top tone (#09070C), because that
     is what now sits under the fold — it used to be the nav's plum, and with
     the nav gone that left a one-frame tone step mid-slide. Cover bottom and
     wall top are the same colour now, so the slide reads as one surface. */
  .cover::after{content:'';position:absolute;left:0;right:0;bottom:0;z-index:0;
    height:clamp(200px,32vh,340px);pointer-events:none;
    background:
      radial-gradient(110% 100% at 50% 100%,rgba(184,106,220,.14),transparent 64%),
      linear-gradient(180deg,rgba(9,7,12,0),rgba(9,7,12,.9))}
  /* Content and cue above the leak -- the light is behind them, not on them. */
  .cover-in{position:relative;z-index:1;
    display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:clamp(32px,7vh,80px);
    width:100%;max-width:1140px;margin:0 auto}
  .cover-mark{display:flex;flex-direction:column;align-items:center;
    gap:var(--s-4);text-align:center}
  /* Flat and mono-600-uppercase-.12em: the nav's lockup at the size a first
     screen needs, and nothing else. Only font-size differs. One logo painted
     two ways, or a name set two ways, reads as two of them. */
  /* The big blurred mark. A real blur on the real file, not a painted
     gradient standing in for one: filter:blur on a static image is composited
     once and never re-rendered, so it costs nothing after first paint. It is
     taller than the viewport on purpose — the edges of the blur must leave
     the screen, or it reads as a soft sticker rather than as light. Sized on
     the short side so a phone gets the same fill. The floor glow (::after,
     later in the DOM) paints over it, so the two lights meet at the bottom. */
  .cover-bg{position:absolute;inset:0;z-index:0;overflow:hidden;pointer-events:none;
    display:flex;align-items:center;justify-content:center}
  .cover-bg img{height:clamp(440px,104vh,1040px);width:auto;display:block;
    filter:blur(clamp(14px,1.5vw,22px));opacity:.5;transform:translateY(-3%);
    -webkit-user-select:none;user-select:none}
  /* Blur is in screen pixels: a phone's mark is a third the size, so the
     same radius would dissolve it. Enough to soften, not enough to lose the H. */
  @media(max-width:700px){
    .cover-bg img{height:clamp(400px,96vh,720px);filter:blur(12px);opacity:.45}
  }
  .cover-word{font-family:var(--mono);font-weight:600;
    font-size:clamp(24px,4.6vw,54px);letter-spacing:.12em;text-transform:uppercase;
    color:var(--ink);line-height:1}
  .cover .stats{width:100%}
  /* The only instruction on the screen, so it is small and it is the only
     thing moving. */
  .cover-cue{position:absolute;left:0;right:0;bottom:clamp(20px,3.5vh,36px);z-index:1;
    display:flex;flex-direction:column;align-items:center;gap:var(--s-3);
    font-family:var(--mono);font-size:11px;letter-spacing:.22em;
    text-transform:uppercase;color:var(--ink-3);pointer-events:none}
  .cover-cue-l{width:1px;height:clamp(24px,4vh,40px);transform-origin:top;
    background:linear-gradient(180deg,transparent,var(--glow));
    animation:cue 2.4s var(--ease) infinite}
  @media(max-width:700px){
    .cover{padding-top:clamp(28px,6vh,48px)}
    .cover-in{gap:clamp(28px,5vh,44px)}
  }

  /* ══ THE WALL ══════════════════════════════════════════════════════════
     Four channels, scored live. This is not a screenshot and not a drawing of
     the dashboard — it is the same loop the product runs: a score per channel
     per second, a per-channel threshold, five weighted signals underneath it.
     Most of what happens is a channel getting loud and NOT crossing. That is
     the point of showing it: the threshold has to mean something before the
     one that does cross means anything.

     Everything that moves here moves on transform, opacity or clip-path. No
     width, height, top or filter is animated anywhere in this block. ══ */
  /* The stats band's language, exactly: one hairline above, one below, cells
     divided by vertical hairlines, no fills. The wall is the page's biggest
     surface, and as four gradient-bordered cards it was the one thing still
     dressed like a different website than the cover. */
  /* The top hairline reads the live score, exactly like the nav's: the frame
     of the instrument brightens as a channel climbs. This keeps the wall a
     CONSUMER of --lit -- the tiles' gradient border used to be one, and a
     room that stops responding to the score has lost the page's entire
     mechanic, however clean it looks. */
  /* A 2x2 EXHIBIT, not a four-across instrument row. The wall shrank when
     the spread arrived: beside a text column, four tiles in one row gave each
     chart a letterbox, and the whole slide read as machinery. Two by two the
     charts keep real height and the wall reads as one object. */
  .wall{position:relative;display:grid;gap:0;min-height:0;
    border-top:1px solid rgba(247,167,69,calc(.12 + var(--lit)*.30));
    border-bottom:1px solid var(--hair);
    transition:border-color var(--t-move) var(--ease);
    grid-template-columns:repeat(2,minmax(0,1fr));
    grid-template-rows:repeat(2,minmax(0,1fr))}
  /* Under 1180 the wall drops to TWO channels, not to a 2x2. Four tiles in two
     rows needs about 900px of height, the lede takes the rest, and the second
     row ended up below the fold — a wall you have to scroll to see is not a
     wall. Two tiles stay one row deep and stay in view. The fire moment still
     happens, and the near-miss moves onto the other visible tile so the
     threshold still gets to mean something. */
  /* A phone gets a single column of two: four stacked charts is a scroll,
     not a wall. visibleCount() in the loop matches this breakpoint — the two
     hidden tiles are not simulated. */
  @media(max-width:700px){
    .wall{grid-template-columns:minmax(0,1fr);grid-template-rows:repeat(2,minmax(0,1fr))}
    .wall .tile:nth-child(n+3){display:none}
  }

  /* A cell, not a card: transparent on the dark ground, a hairline on the
     left edge like the stat dividers, everything carried by the trace and the
     number. The gradient border, the gradient fill and the radius all went --
     three pieces of chrome the cover proved the room does not need. */
  .tile{position:relative;display:flex;flex-direction:column;min-width:0;min-height:0;
    padding:var(--s-4) var(--s-4) var(--s-3);overflow:hidden;
    border-left:1px solid var(--hair);background:transparent;
    transition:box-shadow var(--t-move) var(--ease),opacity var(--t-move) var(--ease),
      background var(--t-move) var(--ease),transform var(--t-move) var(--ease)}
  /* 2x2 dividers: odd children (1,3) start a row, so no left hairline and no
     left padding; the second row (3,4) gets the horizontal divider. */
  .tile:nth-child(odd){border-left:none;padding-left:0}
  .tile:nth-child(n+3){border-top:1px solid var(--hair)}
  /* Stacked rows on a phone, so the dividers turn horizontal -- same as the
     stats band when it stacks. AFTER the base rule on purpose: these are the
     same specificity, so putting them before it (inside the wall's own media
     block above) let the base border-left win and drew a stray vertical
     hairline on every stacked tile. */
  @media(max-width:700px){
    .tile{border-left:none;border-top:1px solid var(--hair);padding-left:0;padding-right:0}
    .tile:first-child{border-top:none}
  }
  /* Entry. Staggered in JS by writing --d; transform and opacity only. */
  .tile{opacity:0;transform:translate3d(0,14px,0)}
  .tile.in{opacity:1;transform:none;
    transition:opacity var(--t-enter) var(--ease) var(--d,var(--dur-fast)),
      transform var(--t-enter) var(--ease) var(--d,0ms)}
  /* Hot = climbing hard but still under. Fire = over the line. */
  /* REMOVED: a soft purple glow around the whole tile whenever a channel came
     within ten of its threshold. It earned its place when the threshold line
     was an 18%-opacity dash and the trace was one flat colour — the glow was
     the ONLY way to tell a tile was close. Now the line is a real datum and
     the trace changes weight and colour the moment it crosses, both of which
     say the same thing precisely instead of atmospherically. Two signals for
     one state, and the vaguer one goes. .tile.fire keeps its glow: that marks
     an event, not a proximity. */
  /* A wash of light in the cell, not a ring around a card -- the cell has no
     card edge to ring any more. Still an event marker, still temporary. */
  .tile.fire{background:rgba(247,167,69,.06);
    box-shadow:0 0 70px -22px rgba(247,167,69,.7)}

  .tile-top{display:flex;align-items:baseline;gap:8px;min-width:0}
  .tile-ch{display:inline-flex;align-items:center;gap:8px;min-width:0;
    font-family:var(--mono);font-size:12px;letter-spacing:.02em;color:var(--ink);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tile-dot{flex:none;width:5px;height:5px;border-radius:50%;background:var(--ember);
    box-shadow:0 0 8px var(--ember)}
  .tile-game{margin-left:auto;flex:none;font-family:var(--mono);font-size:12px;
    letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);
    max-width:44%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  .tile-read{display:flex;align-items:baseline;gap:8px;margin-top:4px}
  .tile-score{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
    font-size:clamp(34px,4.4vw,66px);line-height:1;letter-spacing:-.03em;color:var(--ember);
    transition:color var(--t-micro) var(--ease),text-shadow var(--t-move) var(--ease)}
  .tile.hot  .tile-score{color:var(--ink)}
  .tile.fire .tile-score{color:var(--ember);text-shadow:0 0 30px rgba(247,167,69,.55)}
  .tile-th{font-family:var(--mono);font-size:12px;letter-spacing:.13em;text-transform:uppercase;
    color:var(--ink-3);font-variant-numeric:tabular-nums}

  /* margin-top:auto, so the readout stays at the top of the tile and the
     history hangs off the bottom. The chart is CAPPED: scores live between
     roughly 25 and 95, so a chart stretched to a 430px tile plotted a thin
     line across a large empty rectangle. Bounding it keeps the trace dense
     and gives the slack to the number, which is what people read. */
  /* Taller. The chart is the only part of the tile that carries the argument —
     the header names the channel, the readout gives the number, and both are
     legible in a glance. The trace needs room for the threshold line to sit
     clear of the top and bottom edges, or a crossing happens in the last few
     pixels of the box and reads as the line being touched rather than passed. */
  /* flex:1, NOT margin-top:auto. The chart used to be a fixed height pinned to
     the bottom of the tile, so every pixel the tile had spare opened as a gap
     between the readout and the top of the chart. Making the chart taller made
     the gap SMALLER but did not close it; making the tile taller reopened it.
     Letting the chart take the slack means the trace grows with the tile and
     the readout stays where it belongs, directly above its own chart. */
  /* 84, down from 112: half-width tiles in the 2x2 do not need the floor the
     four-across letterboxes did, and the exhibit shares a viewport with the
     voice column now. flex:1 still hands the chart all the slack. */
  .tile-chart{position:relative;flex:1 1 auto;min-height:84px;margin-top:12px;
    border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
  .tile-chart svg{display:block;width:100%;height:100%}
  /* Deliberately quieter than it was: this is the below-threshold half, and
     it has to sit back so the clipped bright copy above the line reads as an
     event rather than as more of the same. */
  /* Rested further than before: the wall is slide 2 in its entirety now, and
     four full-brightness traces filling the screen read as the loud half of
     the page when the cover just set a near-silent register. Opacity, not a
     new colour — the hue stays the accent, it just sits back until something
     happens. Firing returns to full strength: the payoff is unchanged. */
  .tile-line{fill:none;stroke:var(--ink);stroke-width:1.5;stroke-linejoin:round;
    stroke-linecap:round;vector-effect:non-scaling-stroke;stroke-opacity:.55;
    transition:stroke var(--t-move) var(--ease),stroke-opacity var(--t-move) var(--ease)}
  .tile.hot  .tile-line{stroke-opacity:.8}
  .tile.fire .tile-line{stroke:var(--ember);stroke-opacity:1}
  /* THE DATUM. This was rgba(242,234,247,.18) in a 3/6 dash — the faintest
     mark in the tile. It is the line the entire product is about: the whole
     claim is that a channel is measured against ITS OWN number and you can
     watch the score cross it. Drawn at 18% it read as chart furniture, so the
     crossing had to be inferred from the numeral rather than seen. Now it is
     the strongest hairline in the tile, and it is the accent colour rather
     than ink because it belongs to the formula, not to the grid. */
  .tile-thline{stroke:rgba(242,234,247,.4);stroke-width:1;stroke-dasharray:none;
    vector-effect:non-scaling-stroke;transition:stroke var(--dur-slow) var(--ease)}
  .tile.hot  .tile-thline{stroke:rgba(242,234,247,.75)}
  .tile.fire .tile-thline{stroke:var(--ember)}
  /* The reading, at the line rather than in the header. `thr 71` used to sit
     in the top row about a hundred pixels above the mark it describes, so the
     number and the line it names were two unrelated pieces of furniture. */
  .tile-thmark{position:absolute;right:0;transform:translateY(-50%);
    font-family:var(--mono);font-size:12px;letter-spacing:.1em;
    color:var(--ink-3);opacity:.8;pointer-events:none;
    background:linear-gradient(90deg,transparent,var(--bone) 40%);padding-left:8px;
    transition:opacity var(--dur-fast) var(--ease),color var(--dur-fast) var(--ease)}
  .tile.fire .tile-thmark{color:var(--ember);opacity:1}
  /* THE CROSSING, DRAWN. The trace was one stroke for the whole nine-second
     window, so the moment that matters looked identical to the moment before
     it. This is the same path a second time, clipped to the region above the
     channel's own threshold: below the line the trace is quiet, above it the
     stroke is brighter and heavier. Nothing is animated to do this — it is
     two paths and a clip rect, so it costs one extra draw per frame. */
  .tile-line-over{fill:none;stroke:var(--ember);stroke-width:2.4;
    stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
  .tile-head{fill:var(--ink);transition:fill var(--t-move) var(--ease)}
  .tile.fire .tile-head{fill:var(--ember)}
  /* The near-miss caption. It is the honest half of the demo, so it gets to
     be legible rather than decorative. */
  .tile-flag{position:absolute;left:0;bottom:6px;font-family:var(--mono);font-weight:600;
    font-size:12px;letter-spacing:.18em;color:var(--ink-3);
    opacity:0;transform:translate3d(0,4px,0);
    transition:opacity var(--t-move) var(--ease),transform var(--t-move) var(--ease)}
  .tile-flag.on{opacity:1;transform:none;color:var(--ember)}
  .tile.fire .tile-flag{color:var(--ember)}

  .tile-sigs{list-style:none;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));
    gap:4px;margin-top:8px}
  .tile-sigs li{min-width:0}
  .sg-k{display:block;font-family:var(--mono);font-size:12px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--ink-3);white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  .sg-b{display:block;height:2px;margin-top:4px;background:var(--hair);border-radius:2px;
    overflow:hidden}
  /* scaleX, never width — this updates every 250ms on twenty bars at once. */
  .sg-b i{display:block;height:100%;background:rgba(242,234,247,.45);transform-origin:left center;
    transform:scaleX(var(--v,0));transition:transform var(--t-move) linear}
  .tile.fire .sg-b i{background:var(--ember)}

  /* The clip stage's stylesheet lived here (~100 lines). The stage itself
     was removed from the wall -- a monitor that stops monitoring to play a
     video is not showing the thing it claims to do -- and dead CSS is not
     harmless: a dead FAQ stylesheet once overrode the live one's measure.
     .wall.staged is still toggled by the loop's JS, but styles nothing. */

  .wall-cap{display:flex;align-items:center;gap:8px;margin-top:8px;
    font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ink-3)}
  .wall-cap b{color:var(--ink-2);font-weight:600;font-variant-numeric:tabular-nums}
  .wall-cap .sep{flex:1 1 auto;height:1px;background:var(--hair)}
  /* One line or nothing. Wrapped, the caption stole a second row from the wall
     and the tiles paid for it. */
  .wall-cap{white-space:nowrap}
  @media(max-width:900px){ .cap-why{display:none} }

  /* ── PHONE. This block existed because the lede cost 650 of an 844px
     viewport and pushed the wall below the fold. The lede is gone, so the
     rules that shrank it went with it; what stays is the wall's own sizing,
     which still has to fit a whole tile on a short screen. ── */
  @media(max-width:700px){
    .hero.hero-band{padding-top:calc(var(--nav-h) + 8px)}
    .wall{gap:8px}
    .tile{padding:8px 12px 8px}
    .tile-chart{height:118px;max-height:118px;min-height:64px}
    .tile-score{font-size:30px}
    .wall-cap{font-size:12px;letter-spacing:.08em}
  }


  /* Small phones. Everything above buys enough room on a 390x844; a 360x740
     needs another ~35px to keep the whole first tile above the fold, and this
     is where it comes from. */
  @media(max-width:420px){
    .tile-chart{height:106px;max-height:106px}
  }


  /* ══ STAT STRIP. Not three equal cards — a readout rail with the numbers
     hung off it at uneven weight, the way a broadcast desk is laid out. ══ */
  /* auto-flow, not fixed columns: the clip-count tile is hidden until there is
     a number to show, and a fixed template would leave its column empty. */
  /* The band lives on the cover now, so it no longer owns the page's one
     rhythm break — see the margin-bottom on .hero.hero-band, which is where
     that --s-10 moved to. It is still a margin and not padding for the same
     reason it always was: the hero is min-height:100svh with the wall in a
     minmax(0,1fr) row, so air added INSIDE the hero comes straight out of the
     wall. It lost 97px that way once already. */
  .stats{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:0;
    border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
  /* Room inside to match the room around it: at 24px of padding with a 12px
     caption these numbers read as marooned rather than as quiet. */
  /* Right padding, not 0: at 768 the four cells are ~176px wide and the
     longest caption wraps to exactly the divider, so "Starter" sits on the
     rule. That was survivable when the band was a strip halfway down the
     page. It is the first thing on the site now. */
  .stat{padding:var(--s-6) var(--s-5) var(--s-6) var(--s-6);border-left:1px solid var(--hair)}
  .stat:first-child{padding-left:0;border-left:none}
  .stat .n{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
    font-size:30px;letter-spacing:-.03em;line-height:1;color:var(--ink);display:flex;align-items:center;gap:12px}
  .stat.stat-big .n{font-size:clamp(40px,5vw,56px);color:var(--ember-ink)}
  .stat .k{font-size:14px;color:var(--ink-2);margin-top:12px;max-width:30ch;line-height:1.5}
  /* On a phone the band is two cells across, not five: five columns of a
     328px band are 82px each, and a five-digit count in the big mono ran
     out of its cell and off the right edge of the screen. Rows divide with
     the band's own hairline; the vertical dividers go, because the first
     cell of each row cannot be told from the second with the live cells
     hidden and revealed by id. Desktop rules above are untouched. */
  @media(max-width:700px){
    .cover .stats{grid-auto-flow:row;grid-template-columns:repeat(2,minmax(0,1fr));
      column-gap:var(--s-4)}
    .cover .stat{border-left:none;border-top:1px solid var(--hair);
      padding:var(--s-4) 0 var(--s-4) 0}
    .cover .stat:nth-child(-n+2){border-top:none}
    .cover .stat.stat-big .n{font-size:36px}
  }

  /* ══ BELOW THE COVER: THE CINEMATIC PAGE (v4, 2026-09-02) ═══════════════════
     Everything under the cover was deleted and rebuilt to one blueprint:
     imagery first, explanation second, hard cuts between dark and light.
       2  proof    dark   a full-bleed clip frame, one headline, the product
                         screens fanned across the foreground
       3  numbers  dark   one line and the big live figures
       4  catches  light  the category rail and the shelf of clip frames
       5  score    light  the scroll-scrubbed score timeline beside a frame
       6  watch    dark   ten channels being scored, one of them firing
       7  pricing  light  Starter and Pro as two tall columns of facts
       8  end      dark   the closing frame, one line, one button, the footer
     Palette below the cover: black, white, the orange, and the paper of the
     light sections. The violet stays in the logo and the cover's floor glow;
     nothing down here borrows it. Type: the mono for numbers and labels, the
     sans for sentences, and the sans at its heaviest weight and tightest
     tracking for headlines — one display voice, no second face to load. ══ */

  /* ── Tokens the sheet adds ── */
  :root{
    --paper:#F4F4F2; --paper-2:#EAEAE6;
    --paper-ink:#0A0A0C; --paper-ink-2:#4B4A50; --paper-ink-3:#77767C;
    --paper-hair:rgba(10,10,12,.14);
    --white:#FFFFFF;
  }
  /* The dark-context ink block. Kept as one selector list on purpose — the
     palette test reads the tokens re-declared here. */
  .band-dark,.panel,.tile,.stage,.exl-card{
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085); --hair-2:rgba(242,234,247,.15);
    color:var(--ink-2)}

  /* ── Base ── */
  a{text-decoration:none;color:inherit}
  ::selection{background:rgba(247,167,69,.35);color:#fff}
  /* One focus ring for the whole page, in the orange, on both grounds. */
  :focus-visible{outline:2px solid var(--ember);outline-offset:3px;border-radius:2px}
  a:focus-visible,button:focus-visible{outline:2px solid var(--ember);outline-offset:3px;border-radius:2px}
  .wrap{width:100%;max-width:1280px;margin:0 auto;
    padding-left:clamp(20px,4.5vw,72px);padding-right:clamp(20px,4.5vw,72px)}
  @media(min-width:1800px){ .wrap{max-width:1440px} }
  /* Sections meter their own space; this is the floor the tests read. */
  section{padding-top:var(--s-7);padding-bottom:var(--s-7)}
  .num{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
    font-feature-settings:'tnum' 1,'zero' 1;letter-spacing:-.02em}
  .k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    text-transform:uppercase}
  /* Light ground. A section declares it and every ink inside resolves. */
  .light{background:var(--paper);color:var(--paper-ink-2);
    --ink:var(--paper-ink); --ink-2:var(--paper-ink-2); --ink-3:var(--paper-ink-3);
    --hair:var(--paper-hair); --hair-2:rgba(10,10,12,.28)}
  .light h2,.light h3{color:var(--paper-ink)}
  /* The display voice: the sans at 800, tight. Dark sections set it white,
     light sections set it ink. */
  .disp{font-family:var(--sans);font-weight:800;letter-spacing:-.04em;line-height:.98;
    color:var(--white);margin:0}
  .light .disp{color:var(--paper-ink)}
  .l-h{font-size:clamp(36px,5.2vw,68px);max-width:16ch}
  .l-sub{margin:var(--s-4) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:var(--ink-2);max-width:var(--measure)}

  /* ── Buttons ── */
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;cursor:pointer;
    font-family:var(--sans);font-weight:700;font-size:15px;letter-spacing:-.005em;
    padding:12px 24px;border-radius:3px;border:1px solid transparent;color:var(--ink);
    transition:background var(--dur-fast),color var(--dur-fast),border-color var(--dur-fast);
    white-space:nowrap}
  .btn-lg{padding:16px 32px;font-size:16px}
  /* The one action colour. Black type on the orange: 9.2:1. */
  .btn-go{background:var(--ember);border-color:var(--ember);color:#0A0A0C}
  .btn-go:hover{background:#FFB65A;border-color:#FFB65A}
  .btn-go:active{transform:translateY(1px)}
  /* The quiet one: an outline in the ground's own ink. */
  .btn-ghost{background:transparent;border-color:rgba(255,255,255,.35);color:var(--white)}
  .btn-ghost:hover{border-color:var(--white)}
  .btn-ghost:active{transform:translateY(1px)}
  .light .btn-ghost{border-color:rgba(10,10,12,.35);color:var(--paper-ink)}
  .light .btn-ghost:hover{border-color:var(--paper-ink)}
  .btn-dark{background:var(--paper-ink);border-color:var(--paper-ink);color:var(--white)}
  .btn-dark:hover{background:#26252B;border-color:#26252B}
  .btn-dark:active{transform:translateY(1px)}
  /* The nav's button wears the action colour too — one button language. */
  .nav .btn{font-size:14px;padding:8px 16px}

  /* ══ 2. PROOF. A full-bleed clip frame, one headline, the screens fanned. ══
     The frame is a real clip's preview, served by the showcase and baked in
     at render time (see _frames in the module). Two gradients: black down
     from the top so the cover's black runs straight into the picture with no
     edge, and black up from the bottom so the screens sit on something. */
  .proof{position:relative;min-height:100svh;background:#000;overflow:hidden;
    display:flex;flex-direction:column;padding:0;color:var(--white)}
  .proof-bg{position:absolute;inset:0;z-index:0;pointer-events:none}
  .proof-bg img{width:100%;height:100%;object-fit:cover;object-position:50% 28%;display:block}
  /* A product screen standing in for a frame is pushed back further: it is
     a fallback, and it must not compete with the headline. */
  .proof-bg img.ui{opacity:.55;object-position:0 0}
  .proof-bg::before{content:'';position:absolute;inset:0;pointer-events:none;
    background:linear-gradient(180deg,#000 0%,rgba(0,0,0,.55) 22%,rgba(0,0,0,0) 48%)}
  .proof-bg::after{content:'';position:absolute;inset:0;pointer-events:none;
    background:linear-gradient(180deg,rgba(0,0,0,0) 40%,rgba(0,0,0,.72) 68%,#000 100%)}
  .proof-in{position:relative;z-index:2;padding-top:clamp(112px,18vh,180px);padding-bottom:0}
  .proof-h{font-size:clamp(44px,7.6vw,110px);max-width:11ch;
    text-shadow:0 2px 40px rgba(0,0,0,.55)}
  .proof-act{margin-top:var(--s-6);display:flex;align-items:center;gap:var(--s-4);flex-wrap:wrap}
  .proof-note{font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;
    color:rgba(255,255,255,.62);margin:0}
  /* THE FAN. Three flat screens, one dominant in the centre, the outer two
     bleeding off the viewport. Real shadows between layers, no tilt, no
     device chrome. Heights come from the images' own ratio (1200x750). */
  .fan{position:relative;z-index:1;margin-top:var(--s-8);height:clamp(200px,30vw,440px);
    pointer-events:none}
  /* Hung from the TOP of the fan's box, so they start under the button and
     run off the bottom of the section — objects cut by the table's edge, not
     objects floating up into the headline. */
  .fan img{position:absolute;top:0;height:auto;display:block;border-radius:10px;
    border:1px solid rgba(255,255,255,.09);background:#0E0B11}
  .fan-c{left:50%;transform:translateX(-50%);width:min(62vw,880px);z-index:3;
    box-shadow:0 30px 60px -10px rgba(0,0,0,.9),0 80px 120px -40px rgba(0,0,0,.9)}
  .fan-l{left:-12%;top:10%;width:min(48vw,680px);z-index:2;
    box-shadow:0 30px 60px -20px rgba(0,0,0,.85)}
  .fan-r{right:-12%;top:6%;width:min(48vw,680px);z-index:1;
    box-shadow:0 30px 60px -20px rgba(0,0,0,.85)}
  @media(max-width:900px){
    .fan{height:clamp(180px,40vw,400px);margin-top:var(--s-7)}
    /* `.fan img` above sets display:block at a higher specificity than a bare
       class, so `.fan-l{display:none}` lost and both outer screens kept
       rendering on phones, bleeding off each edge under the headline. */
    .fan .fan-l,.fan .fan-r{display:none}
    .fan-c{width:min(94vw,720px)}
  }

  /* ══ 3. NUMBERS. Black. One line, then the figures, huge, in the mono. ══ */
  /* Centred, and the three figures kept apart: each sits in the middle of
     its own third with a real gutter between them, and the size is capped so
     a six-digit count (or a seven-digit one, later) stays inside its third
     instead of running into the next figure — which is what 8vw did at
     1440 with 39,581. */
  .numbers{background:#000;color:var(--ink-2);padding-top:var(--s-9);padding-bottom:var(--s-9);
    text-align:center}
  .num-lead{margin:0 auto var(--s-8);font-size:clamp(18px,1.7vw,22px);line-height:1.4;
    color:var(--white);max-width:var(--measure)}
  .bignums{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--s-7) var(--s-8);
    justify-items:center;align-items:start}
  .bign{min-width:0;text-align:center}
  .bign-n{font-family:var(--mono);font-weight:600;font-size:clamp(44px,5.6vw,92px);line-height:.95;
    letter-spacing:-.05em;color:var(--ember);font-variant-numeric:tabular-nums;white-space:nowrap}
  .bign-n i{font-style:normal;font-size:.45em;letter-spacing:-.02em}
  .bign-k{font-family:var(--mono);font-size:12px;letter-spacing:.16em;text-transform:uppercase;
    color:var(--ink-3);margin-top:var(--s-3)}
  @media(max-width:700px){
    .numbers{padding-top:var(--s-8);padding-bottom:var(--s-8)}
    .bignums{grid-template-columns:minmax(0,1fr);gap:var(--s-7)}
    .bign-n{font-size:clamp(48px,15vw,72px)}
  }

  /* ══ 4. CATCHES. Hard cut to paper: the rail and the shelf. ══ */
  .catches{padding-top:var(--s-9);padding-bottom:var(--s-9);overflow:hidden}
  .rail{display:flex;flex-wrap:wrap;gap:var(--s-2);margin-top:var(--s-6);padding:0 0 var(--s-2)}
  @media(max-width:900px){
    .rail{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;white-space:nowrap;
      margin-right:-16px;padding-right:16px}
  }
  .rail::-webkit-scrollbar{display:none}
  .rail-t{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--paper-ink-2);background:transparent;
    border:1px solid var(--paper-hair);border-radius:999px;padding:8px 16px;cursor:pointer;
    flex:0 0 auto;transition:background var(--dur-fast),color var(--dur-fast),border-color var(--dur-fast)}
  .rail-t:hover{border-color:var(--paper-ink)}
  .rail-t.is-on{background:var(--paper-ink);border-color:var(--paper-ink);color:var(--white)}
  /* THE SHELF. Cards taller than they are wide, cut by the viewport edges,
     and MOVING: a carousel that glides left and wraps without a seam. The
     script clones the set once (.shelf-set x2) and marks the shelf .is-loop;
     the track then slides by exactly one set's width, so the second set
     takes over where the first left off. Each set carries the gap as its own
     right padding, which is what makes -50% land exactly on the join.
     Hovering (or focusing a card) pauses it so a clip can be clicked; off
     screen it is paused too; under reduced motion it does not move at all
     and is a plain sideways scroll, which is also what no-JS gets. */
  .shelf{position:relative;margin-top:var(--s-6);overflow-x:auto;scrollbar-width:none;
    padding:var(--s-1) clamp(20px,4.5vw,72px) var(--s-4)}
  .shelf::-webkit-scrollbar{display:none}
  .shelf.is-loop{overflow:hidden;padding-left:0;padding-right:0}
  .shelf-track{display:flex;width:max-content}
  .shelf-set{display:flex;gap:var(--s-4);padding-right:var(--s-4);flex:none}
  .shelf.is-loop .shelf-track{animation:shelf-roll var(--shelf-t,60s) linear infinite}
  .shelf.is-loop:hover .shelf-track,.shelf.is-loop:focus-within .shelf-track,
  .shelf.is-loop.is-off .shelf-track{animation-play-state:paused}
  @keyframes shelf-roll{to{transform:translate3d(-50%,0,0)}}
  @media(prefers-reduced-motion:reduce){
    .shelf.is-loop{overflow-x:auto;padding-left:clamp(20px,4.5vw,72px);padding-right:clamp(20px,4.5vw,72px)}
    .shelf.is-loop .shelf-track{animation:none}
    .shelf.is-loop .shelf-set + .shelf-set{display:none}
  }
  .card{position:relative;flex:0 0 auto;width:clamp(260px,26vw,400px);aspect-ratio:3/4;
    border-radius:6px;overflow:hidden;background:#0A0A0C;
    color:var(--white);display:block}
  .card[hidden]{display:none}
  .card img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;
    transition:transform 1400ms var(--ease);transform-origin:50% 40%}
  .card:hover img{transform:scale(1.06)}
  .card img.ui{object-position:0 0;opacity:.85}
  .card::after{content:'';position:absolute;inset:0;pointer-events:none;
    background:linear-gradient(180deg,rgba(0,0,0,0) 40%,rgba(0,0,0,.55) 66%,rgba(0,0,0,.92) 100%)}
  .card-t{position:absolute;left:0;right:0;bottom:0;z-index:1;padding:var(--s-5)}
  .card-k{display:block;font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--ember);margin-bottom:var(--s-2)}
  .card-t h3{font-family:var(--sans);font-weight:800;font-size:clamp(20px,1.9vw,26px);
    letter-spacing:-.03em;line-height:1.08;color:var(--white);margin:0 0 var(--s-2);
    overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
  .card-t p{margin:0;font-size:14px;line-height:1.45;color:rgba(255,255,255,.72)}
  .card-t p b{color:var(--white);font-weight:600}
  .shelf-empty{display:none;font-size:15px;color:var(--paper-ink-3);padding:var(--s-6) 0}
  .shelf.is-empty .shelf-empty{display:block}
  @media(max-width:700px){
    .card{width:min(76vw,320px)}
    .card-t{padding:var(--s-4)}
  }

  /* ══ 5. SCORE. The machine, watching: a frame beside the score timeline,
     driven by the scroll. The track is tall; the scene inside it is stuck. ══ */
  .score{padding:0}
  .score-track{position:relative;height:260vh}
  .score-stick{position:sticky;top:0;height:100svh;display:flex;align-items:center;overflow:hidden}
  .score-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);
    column-gap:clamp(24px,4vw,64px);align-items:center;width:100%}
  .score-head{grid-column:1 / -1;margin-bottom:var(--s-3)}
  .score-head .l-h{font-size:clamp(30px,4vw,52px)}
  .score-head .l-sub{margin-top:var(--s-3);max-width:44ch}
  .score-frame{position:relative;aspect-ratio:16/10;border-radius:8px;overflow:hidden;background:#000;
    box-shadow:0 30px 60px -30px rgba(0,0,0,.6)}
  .score-frame img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;
    opacity:0;transition:opacity 600ms var(--ease),transform 8s linear;transform:scale(1.0)}
  .score-frame img.on{opacity:1;transform:scale(1.06)}
  .score-frame img.ui{object-position:0 0}
  .score-cap{position:absolute;left:var(--s-4);bottom:var(--s-4);z-index:2;display:flex;gap:var(--s-2);
    align-items:center;font-family:var(--mono);font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--white);background:rgba(0,0,0,.55);padding:4px 8px;border-radius:3px}
  .score-cap i{width:6px;height:6px;border-radius:50%;background:var(--ember);font-style:normal}
  .score-fired .score-cap i{animation:none;box-shadow:0 0 12px var(--ember)}
  .score-ui{display:flex;flex-direction:column;gap:var(--s-4);min-width:0}
  .score-top{display:flex;align-items:baseline;justify-content:space-between;gap:var(--s-4)}
  .score-k{font-family:var(--mono);font-size:12px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--paper-ink-3)}
  .score-n{font-size:clamp(48px,5vw,72px);line-height:1;color:var(--paper-ink);
    transition:color var(--dur-fast)}
  .score-fired .score-n{color:var(--ember)}
  .score-svg{display:block;width:100%;height:auto;aspect-ratio:600/220;
    border-top:1px solid var(--paper-hair);border-bottom:1px solid var(--paper-hair)}
  .sc-th{stroke:rgba(10,10,12,.35);stroke-width:1;vector-effect:non-scaling-stroke}
  .sc-line{fill:none;stroke:var(--paper-ink);stroke-width:1.5;stroke-linejoin:round;
    stroke-linecap:round;vector-effect:non-scaling-stroke}
  .sc-over{fill:none;stroke:var(--ember);stroke-width:2.5;stroke-linejoin:round;
    stroke-linecap:round;vector-effect:non-scaling-stroke}
  .sc-head{fill:var(--paper-ink)}
  .score-fired .sc-head{fill:var(--ember)}
  .sc-thlab{position:absolute;right:0;font-family:var(--mono);font-size:12px;letter-spacing:.1em;
    color:var(--paper-ink-3)}
  .sc-sigs{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:var(--s-2)}
  .sc-sigs li{font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--paper-ink-3);border:1px solid var(--paper-hair);border-radius:999px;padding:4px 12px;
    opacity:.45;transition:opacity var(--dur-slow) var(--ease),color var(--dur-slow),border-color var(--dur-slow)}
  .sc-sigs li.on{opacity:1;color:var(--paper-ink);border-color:var(--paper-ink)}
  .score-line{margin:0;font-size:15px;line-height:1.5;color:var(--paper-ink-2);max-width:var(--measure);min-height:3em}
  .score-line b{color:var(--paper-ink);font-weight:700}
  /* The head's home on a phone. Empty and hidden on a desktop; the script
     moves the head into it under 900px. */
  .score-lead{display:none}
  @media(max-width:900px){
    .score-track{height:220vh}
    /* On a phone the head LEAVES the stuck scene. With the headline and its
       copy inside, the scene was taller than a 740px viewport: the headline
       sat under the fixed bar (sticky top:0 is the bar's own pixels) and the
       signals ran off the bottom, unreachable, because the scene is stuck
       for the length of the track. The head reads first in normal flow, and
       the scene pads its top by the bar's real height. */
    .score-lead{display:block;padding-top:var(--s-8);padding-bottom:var(--s-3)}
    .score-stick{box-sizing:border-box;padding-top:calc(var(--nav-h) + var(--s-3));
      align-items:flex-start}
    .score-grid{grid-template-columns:minmax(0,1fr);row-gap:var(--s-5);align-content:start}
    .score-head .l-h{font-size:clamp(28px,7vw,40px)}
    .score-frame{aspect-ratio:16/9}
    .score-n{font-size:40px}
  }
  @media(max-width:700px){
    .score-svg{aspect-ratio:600/160}
    .score-grid{row-gap:var(--s-4)}
    .score-ui{gap:var(--s-3)}
    .score-line{min-height:0}
  }
  @media(prefers-reduced-motion:reduce){
    .score-track{height:auto}
    .score-stick{position:static;height:auto;padding:var(--s-9) 0}
    .card:hover img{transform:none}
    .score-frame img.on{transform:none}
  }

  /* ══ 6. WATCH. Ten channels being scored, one of them crossing. The wall is
     the engine that used to be slide 2, widened to ten tiles and told to
     fire in the orange. The grid is five across on a desktop, three on a
     tablet, two on a phone; tiles past the visible count are not simulated. ══ */
  .watch{background:#000;padding-top:var(--s-9);padding-bottom:var(--s-9);overflow:hidden}
  .watch-head{margin-bottom:var(--s-7)}
  .watch-head .l-sub{color:var(--ink-2)}
  .watch .wall{grid-template-columns:repeat(5,minmax(0,1fr));grid-template-rows:none;
    grid-auto-rows:minmax(220px,auto);border-top:none;border-bottom:none;overflow:hidden;
    margin:0 calc(-1 * clamp(20px,4.5vw,72px))}
  .watch .tile,.watch .tile:nth-child(odd),.watch .tile:nth-child(n+3){
    border-left:none;border-top:none;padding:var(--s-4);
    box-shadow:-1px 0 0 var(--hair),0 -1px 0 var(--hair)}
  .watch .tile:nth-child(n+3){display:flex}
  .watch .tile.fire{box-shadow:-1px 0 0 var(--hair),0 -1px 0 var(--hair),
    0 0 90px -24px rgba(247,167,69,.8)}
  .watch .tile-chart{min-height:72px}
  .watch .sg-k{letter-spacing:.02em}
  .watch .tile-score{font-size:clamp(34px,3.2vw,52px)}
  /* The clip, pulled out of the tile: a chip that rises out of the firing
     tile's top edge and stays, the way a clip lands in the queue. Transform
     and opacity only. */
  .tile-pull{position:absolute;right:var(--s-3);top:var(--s-3);z-index:2;
    font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:#0A0A0C;background:var(--ember);padding:4px 8px;border-radius:3px;
    opacity:0;transform:translate3d(0,14px,0);pointer-events:none;
    transition:opacity var(--t-move) var(--ease),transform 700ms var(--ease-spring)}
  .tile-pull.go{opacity:1;transform:none}
  .watch .wall-cap{margin:var(--s-5) 0 0}
  @media(max-width:1000px){
    .watch .wall{grid-template-columns:repeat(3,minmax(0,1fr))}
    .watch .wall .tile:nth-child(n+7){display:none}
  }
  @media(max-width:700px){
    .watch .wall{grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:none;gap:0}
    .watch .wall .tile:nth-child(n+3){display:flex}
    .watch .wall .tile:nth-child(n+5){display:none}
    .watch .tile{padding:var(--s-2)}
    /* Half-width tiles: the game drops under the channel name instead of
       fighting it for one line, and the five signal labels size to their
       own text (chat, audio, keys, views, hype) rather than to a fifth of
       a 160px tile, which cut every one of them to two letters. */
    .watch .tile-top{flex-wrap:wrap}
    .watch .tile-game{margin-left:0;flex-basis:100%;max-width:100%}
    .watch .tile-sigs{grid-template-columns:repeat(5,auto);justify-content:space-between}
    .watch .sg-k{font-size:10px;letter-spacing:0}
    .watch .tile-chart{height:auto;max-height:none;min-height:64px}
    .watch .wall-cap{white-space:normal;flex-wrap:wrap;row-gap:var(--s-1)}
    .watch .wall-cap .sep{display:none}
  }

  /* ══ 7. PRICING. Paper. Two tall columns of facts, the price in the mono. ══ */
  .pricing{padding-top:var(--s-9);padding-bottom:var(--s-9)}
  .price-lead{margin:var(--s-4) 0 0;font-size:clamp(16px,1.4vw,19px);line-height:1.5;
    color:var(--paper-ink-2);max-width:var(--measure)}
  .price-lead b{color:var(--paper-ink);font-weight:700}
  .price-lead a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}
  .plans{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:clamp(24px,3vw,48px);
    margin-top:var(--s-8);align-items:start}
  .plan{display:flex;flex-direction:column;min-width:0;border-top:2px solid var(--paper-ink);
    padding-top:var(--s-5)}
  .plan-name{font-family:var(--sans);font-weight:800;font-size:clamp(28px,3vw,40px);
    letter-spacing:-.035em;line-height:1;color:var(--paper-ink);margin:0}
  .plan-price{font-family:var(--mono);font-weight:600;font-size:clamp(44px,5vw,72px);
    letter-spacing:-.04em;line-height:1;color:var(--paper-ink);margin:var(--s-5) 0 0;
    font-variant-numeric:tabular-nums}
  .plan-price i{font-style:normal;font-family:var(--mono);font-size:12px;font-weight:400;
    letter-spacing:.12em;text-transform:uppercase;color:var(--paper-ink-3);margin-left:var(--s-2)}
  .plan-facts{list-style:none;margin:var(--s-6) 0 0;padding:0}
  .plan-facts li{display:flex;justify-content:space-between;gap:var(--s-4);align-items:baseline;
    padding:var(--s-3) 0;border-top:1px solid var(--paper-hair);font-size:15px;color:var(--paper-ink-2)}
  .plan-facts li:last-child{border-bottom:1px solid var(--paper-hair)}
  .plan-facts b{font-family:var(--mono);font-weight:600;color:var(--paper-ink);
    font-variant-numeric:tabular-nums;white-space:nowrap}
  .plan .btn{margin-top:var(--s-6);align-self:flex-start}
  .price-tiny{margin:var(--s-6) 0 0;font-size:13px;color:var(--paper-ink-3);max-width:var(--measure)}
  @media(max-width:900px){
    .plan-price{font-size:clamp(40px,7vw,56px)}
  }
  @media(max-width:700px){
    .plans{grid-template-columns:minmax(0,1fr);gap:var(--s-7)}
  }

  /* ══ 7b. FAQ. Paper, after the plans, cut from them by one hairline. The
     heading sits in a narrow left column and stays put while the questions
     scroll; each question is a native <details>, so it works with no script,
     and the only thing that moves is the sign turning. ══ */
  .faq{padding-top:0;padding-bottom:var(--s-9)}
  .faq-grid{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.6fr);
    column-gap:clamp(32px,5vw,96px);border-top:1px solid var(--paper-hair);
    padding-top:var(--s-8);align-items:start}
  .faq-lead{position:sticky;top:calc(var(--nav-h) + var(--s-6))}
  .faq-lead .l-sub a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}
  .faq-lead .l-sub a:hover{border-bottom-color:var(--paper-ink)}
  .faq-h{margin:0 0 var(--s-3);font-family:var(--mono);font-weight:600;font-size:12px;
    letter-spacing:.16em;text-transform:uppercase;color:var(--paper-ink-3)}
  .faq-group + .faq-group{margin-top:var(--s-8)}
  .faq-item{border-top:1px solid var(--paper-hair)}
  .faq-group .faq-item:last-child{border-bottom:1px solid var(--paper-hair)}
  .faq-q{display:flex;align-items:baseline;justify-content:space-between;gap:var(--s-5);
    padding:var(--s-4) 0;cursor:pointer;list-style:none;
    font-family:var(--sans);font-weight:700;font-size:clamp(17px,1.4vw,20px);
    letter-spacing:-.015em;line-height:1.3;color:var(--paper-ink)}
  .faq-q::-webkit-details-marker{display:none}
  .faq-q::after{content:'+';flex:none;font-family:var(--mono);font-weight:400;font-size:22px;
    line-height:1;color:var(--paper-ink-3);transition:transform var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease)}
  .faq-item[open] .faq-q::after{transform:rotate(45deg);color:var(--paper-ink)}
  .faq-q:hover::after{color:var(--paper-ink)}
  .faq-q:focus-visible{outline:2px solid var(--ember);outline-offset:4px}
  .faq-a{margin:0;padding:0 var(--s-8) var(--s-5) 0;font-size:16px;line-height:1.55;
    color:var(--paper-ink-2);max-width:var(--measure)}
  .faq-a b{color:var(--paper-ink);font-weight:700}
  .faq-a a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}
  @media(max-width:900px){
    .faq-grid{grid-template-columns:minmax(0,1fr);row-gap:var(--s-6)}
    .faq-lead{position:static}
    .faq-a{padding-right:0}
  }

  /* ══ 8. END. The closing frame, darker and quieter, and the footer. ══ */
  .end{position:relative;min-height:80svh;background:#000;overflow:hidden;
    display:flex;align-items:center;padding:var(--s-9) 0;color:var(--white)}
  .end-bg{position:absolute;inset:0;z-index:0;pointer-events:none}
  .end-bg img{width:100%;height:100%;object-fit:cover;object-position:50% 35%;display:block;opacity:.55}
  .end-bg img.ui{opacity:.3;object-position:0 0}
  .end-bg::after{content:'';position:absolute;inset:0;pointer-events:none;
    background:linear-gradient(180deg,#000 0%,rgba(0,0,0,.35) 30%,rgba(0,0,0,.45) 70%,#000 100%)}
  .end-in{position:relative;z-index:1}
  .end-h{font-size:clamp(36px,6vw,88px);max-width:14ch}
  .end-act{margin-top:var(--s-6);display:flex;gap:var(--s-4);align-items:center;flex-wrap:wrap}
  .footer{background:#000;border-top:1px solid var(--hair);
    padding:var(--s-5) clamp(20px,4.5vw,72px);display:flex;align-items:center;gap:var(--s-5);
    flex-wrap:wrap;font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--ink-3)}
  .footer img{height:20px;width:auto;display:block}
  .footer nav{display:flex;flex-wrap:wrap;gap:var(--s-2) var(--s-4)}
  .footer a:hover{color:var(--ink)}
  .footer .fl{margin-left:auto;white-space:nowrap}
  @media(max-width:700px){ .footer .fl{margin-left:0} }

  /* ══ The clip player (opened from a shelf card). ══
     THE PLAYER IS THE WHOLE SCREEN. Twitch's clip embed picks its rendition
     from the size of the player it boots in, and there is no URL parameter
     that forces quality on a clip embed — so the only way to get the 1080p
     rendition is to hand the player 1080 rows of pixels. The card fills the
     viewport edge to edge, and on click the script asks for full screen on
     top of that, so on a 1080p (or larger) display the embed boots at the
     display's full size. The direct MP4 route is closed, and documented as
     such in HANDOFF (probe_clip_media); do not go looking for it. */
  .exl{position:fixed;inset:0;z-index:90;display:grid;place-items:stretch;padding:0;background:#000}
  .exl-bg{position:absolute;inset:0;background:#000}
  .exl-card{position:relative;z-index:1;overflow:hidden;width:100vw;height:100vh;height:100dvh;
    display:flex;flex-direction:column;background:#000}
  .exl-frame{position:relative;flex:1 1 auto;min-height:0;width:100%;background:#000}
  .exl-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
  .exl-meta{display:flex;align-items:center;gap:12px;padding:12px 16px;flex:none}
  /* In full screen the bar goes, so the embed is exactly the display. */
  .exl:fullscreen .exl-meta{display:none}
  .exl-title{flex:1;min-width:0;font-size:14px;font-weight:600;color:var(--ink);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .exl-out{font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ink-2);white-space:nowrap}
  .exl-out:hover{color:var(--ink)}
  .exl-close{position:absolute;top:8px;right:8px;z-index:2;width:32px;height:32px;border-radius:3px;
    border:1px solid rgba(242,234,247,.15);cursor:pointer;background:rgba(0,0,0,.8);color:var(--ink);
    font-size:16px;line-height:1;display:grid;place-items:center}
  .exl-close:hover{border-color:var(--ember)}

  /* ══ MOTION. One orchestrated moment — the trigger firing — and a room that
     breathes. Nothing else moves. ══ */
  @media(prefers-reduced-motion:reduce){
    html{scroll-behavior:auto}
    .breathe{animation:none}
    /* The cue still reads as a cue standing still — it is a line pointing down
       under the word "scroll". */
    .cover-cue-l{animation:none;opacity:.75}
  }
  @media(prefers-reduced-motion:no-preference){
    @keyframes breathe{0%,100%{opacity:.94}50%{opacity:1.0}}
    /* A stroke drawn downward, twice as long a pause as it takes to draw. */
    @keyframes cue{
      0%{transform:scaleY(0);opacity:0}
      28%{transform:scaleY(1);opacity:1}
      70%{transform:scaleY(1);opacity:1}
      100%{transform:scaleY(1);opacity:0}
    }
  }
</style>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Highlightz", "url": "https://highlightz.app/", "applicationCategory": "MultimediaApplication", "operatingSystem": "Web", "description": "Automatic Twitch clipping: Highlightz watches your live stream and creates Twitch clips of the best moments automatically using a transparent scoring formula \u2014 not AI.", "interactionStatistic": {"@type": "InteractionCounter", "interactionType": "https://schema.org/CreateAction", "userInteractionCount": 0, "description": "Twitch clips created automatically by Highlightz"}, "offers": {"@type": "AggregateOffer", "lowPrice": "0.00", "highPrice": "25.00", "priceCurrency": "USD", "offerCount": "3", "description": "Free plan with no card required, then Starter $10/month or Pro $25/month. Cancel anytime."}, "publisher": {"@type": "Organization", "name": "ANTI Technology LLC", "url": "https://highlightz.app/", "logo": "https://highlightz.app/static/icon.png"}}</script>
<!--FAQ_SCHEMA-->
</head>
<body>
<!-- ── NAV. Over the top of the page, on the owner's call: the bar sits
     on the cover the way a nav sits on a hero image, and stays fixed. It
     is the first thing in the document because it is the first thing on
     the screen. slideTo() lands on coverEl.offsetHeight; the hero pads its
     own top by the bar's height so nothing lands under it.

     NO LINES ON IT. The old bar carried a hairline border and a second glowing
     one under it (.nav::after), plus a bordered sparkline pill. All three are
     gone: the bar has no border of its own, and it wears the hero band's own
     top tone so the nav and the wall below it are one unbroken surface. -->
<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <!-- ORDER MATTERS AND IT IS THE PAGE'S ORDER. A nav that lists sections in
         a different sequence to the one you scroll through makes the page feel
         like it jumps around. Held by a test. -->
    <a href="#catches" class="nav-link">What it catches</a>
    <a href="#score" class="nav-link">How it scores</a>
    <a href="#watch" class="nav-link">Channels</a>
    <a href="#pricing" class="nav-link">Pricing</a>
    <a href="#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
    <a href="/compare" class="nav-link">Compare</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-go">Get started</a>
  </div>
</nav>

<!-- THE COVER, the first thing under the bar. The nav
     that used to sit between the two slides was removed on the owner's call —
     the cover already carries the lockup, so slide 2 is the wall alone.
     width/height are the file's NATURAL 374x501 — the browser takes the ratio
     from them and combines it with the CSS height, so the reserved box is the
     right SHAPE; a square would be a layout shift dressed up as a fix. -->
<div class="cover" id="cover">
  <!-- THE MARK, as the room's light. Owner's call: the logo is not a small
       sharp object above the name any more; it is huge, blurred, and behind
       everything — the brand's colour filling the cover the way a sign
       glows through fog. Decorative, so aria-hidden and an empty alt; the
       name is carried by the h1. -->
  <div class="cover-bg" aria-hidden="true">
    <img src="/static/logo-mark.png" alt="" width="374" height="501"
         decoding="async" fetchpriority="high">
  </div>
  <div class="cover-in" id="cover-in">
    <div class="cover-mark">
      <!-- THE PAGE'S h1, and it has to live here now. It used to be "Never
           miss a highlight again." in the hero, which was removed with the
           rest of that block; a landing page with no h1 at all is a real SEO
           regression, not a cosmetic one. An <h1> renders identically to the
           <span> it replaces -- .cover-word sets font, size and line-height,
           and the reset already zeroes heading margins -- so this changes the
           document outline and nothing on screen. No hidden keyword text: the
           phrase a search engine needs is carried by <title>, the meta
           description and the SoftwareApplication schema, which is where it
           belongs. -->
      <h1 class="cover-word">Highlightz</h1>
    </div>
    <!-- Stats band, moved here from below the hero. The id and style
         attributes are matched by string replacement in _landing_html to
         reveal the live numbers, so they must stay byte-identical. -->
    <div class="stats">
      <div class="stat">
        <div class="n" data-count="10">10</div>
        <div class="k">channels watched at once on Pro, 3 on Starter</div>
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
      <!-- Beside the count, and only ever beside it: how many of those clips
           streamers actually kept. Hidden until enough have been judged for the
           percentage to mean anything. -->
      <div class="stat stat-big" id="stat-kept" style="display:none">
        <div class="n"><span id="lp-kept" data-kept="0">0%</span></div>
        <div class="k">kept reviewed clips</div>
      </div>
    </div>
  </div>
  <div class="cover-cue" id="cover-cue" aria-hidden="true">
    <span class="cover-cue-l"></span><span>scroll</span>
  </div>
</div>


<!-- ══ 2. PROOF ═══════════════════════════════════════════════════════════════
     A full-bleed clip frame, one headline, one button, and the product's own
     screens fanned across the foreground. The frame is served by the
     showcase and baked in at render time (FRAME_HERO); with nothing curated
     yet the Live Streams screen stands in, pushed back. -->
<section class="proof" id="proof" aria-labelledby="proof-h">
  <div class="proof-bg"><!--FRAME_HERO--></div>
  <div class="proof-in wrap">
    <h2 class="disp proof-h" id="proof-h">Every big moment,<br>already a clip.</h2>
    <div class="proof-act">
      <a href="/login" class="btn btn-go btn-lg">Start clipping free</a>
      <p class="proof-note">Free plan &middot; one Twitch channel &middot; no card &middot; no time limit</p>
    </div>
  </div>
  <div class="fan">
    <img class="fan-l" src="/static/landing/tour-live.webp" width="1200" height="750" loading="lazy" decoding="async"
         alt="The Live Streams screen: a channel's trigger score climbing toward its threshold">
    <img class="fan-c" src="/static/landing/tour-review.webp" width="1200" height="750" decoding="async" fetchpriority="high"
         alt="The Clip Review screen: pending clips with their scores and Approve and Reject buttons">
    <img class="fan-r" src="/static/landing/tour-library.webp" width="1200" height="750" loading="lazy" decoding="async"
         alt="The Clip Library screen: approved clips, hosted by Twitch">
  </div>
</section>

<!-- ══ 3. NUMBERS ═════════════════════════════════════════════════════════════
     Black. One line, then the figures. Rendered per request from the same
     counters the cover reads (BIGNUMS); the digits count up on entry. -->
<section class="numbers" id="numbers" aria-label="By the numbers">
  <div class="wrap"><!--BIGNUMS--></div>
</section>

<!-- ══ 4. CATCHES ═════════════════════════════════════════════════════════════
     Hard cut to paper. The rail is the engine's own signal titles — the
     words it puts on a clip when that signal led the moment. The shelf is
     the curated showcase, one frame per card, filtered by the rail. -->
<section class="light catches" id="catches" aria-labelledby="catches-h">
  <div class="wrap">
    <h2 class="disp l-h" id="catches-h">What it catches</h2>
    <p class="l-sub">Seven signals, each named for the moment it finds. Every card is a real clip the formula made on a live stream. Pick a signal.</p>
    <div class="rail" id="rail" aria-label="Filter clips by signal"><!--RAIL--></div>
  </div>
  <div class="shelf" id="shelf">
    <div class="shelf-track" id="shelf-track"><div class="shelf-set"><!--SHELF--></div></div>
    <p class="shelf-empty">No featured clip for that signal yet.</p>
  </div>
</section>

<!-- ══ 5. SCORE ═══════════════════════════════════════════════════════════════
     The machine, watching. Scrolling scrubs the score along a real-shaped
     trace beside a clip frame that advances through the moment; the seven
     signals light up as they contribute. Under reduced motion the section is
     the finished frame, not a shorter animation. -->
<section class="light score" id="score" aria-labelledby="score-h">
  <div class="wrap score-lead" id="score-lead"></div>
  <div class="score-track" id="score-track">
    <div class="score-stick">
      <div class="wrap score-grid">
        <div class="score-head">
          <h2 class="disp l-h" id="score-h">It watches so you don't have to.</h2>
          <p class="l-sub">One score, every second, against the channel's own threshold. No black box: the formula is readable, and every clip shows which signal fired.</p>
        </div>
        <div class="score-frame" id="score-frame">
          <!--SCRUB_FRAMES-->
          <div class="score-cap"><i></i><span id="sc-t">0:00</span></div>
        </div>
        <div class="score-ui">
          <div class="score-top">
            <span class="score-k">Trigger score &middot; live</span>
            <span class="score-n num" id="sc-n">31</span>
          </div>
          <div style="position:relative">
            <svg class="score-svg" viewBox="0 0 600 220" preserveAspectRatio="none" aria-hidden="true">
              <line class="sc-th" x1="0" x2="600" y1="63" y2="63"></line>
              <path class="sc-line" id="sc-path" d=""></path>
              <path class="sc-over" id="sc-over" d=""></path>
              <circle class="sc-head" id="sc-head" r="4" cx="0" cy="150"></circle>
            </svg>
            <span class="sc-thlab" style="top:calc(63 / 220 * 100% - 18px)">thr 71</span>
          </div>
          <ol class="sc-sigs" id="sc-sigs">
            <li data-at=".18">Chat velocity</li>
            <li data-at=".34">Keyword hits</li>
            <li data-at=".44">Emote wall</li>
            <li data-at=".52">Sentiment</li>
            <li data-at=".58">Audio spike</li>
            <li data-at=".64">Viewer spike</li>
            <li data-at=".72">Silence burst</li>
          </ol>
          <p class="score-line" id="sc-line">Baseline. Nothing to clip.</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ══ 6. WATCH ═══════════════════════════════════════════════════════════════
     Cut back to black. Ten channels being scored by the same engine that ran
     slide 2, one of them crossing its line and its clip pulled out. -->
<section class="watch" id="watch" aria-labelledby="watch-h">
  <div class="wrap">
    <div class="watch-head">
      <h2 class="disp l-h" id="watch-h">Ten streams are live right now.</h2>
      <p class="l-sub">Pro watches ten channels at once, Starter three, and the free plan one, with no card and no time limit. Each is scored against its own threshold. The one that crosses gets a real Twitch clip made through the official Twitch API, waiting in your review queue. Add a channel before it goes live: it is rechecked every 30 seconds until it is, and monitoring stops after 8 hours without you opening the dashboard.</p>
    </div>
    <div class="wall" id="wall"></div>
    <div class="wall-cap">
      <span>Live &middot; <b id="wall-rate">10</b> channels<span class="cap-why"> &middot; every channel scored against its own threshold</span></span>
      <span class="sep"></span>
      <span id="wall-state">watching</span>
    </div>
  </div>
</section>

<!-- ══ 7. PRICING ═════════════════════════════════════════════════════════════
     Paper. Two tall columns of facts from plans.py (PRICING). -->
<section class="light pricing" id="pricing" aria-labelledby="pricing-h">
  <div class="wrap">
    <h2 class="disp l-h" id="pricing-h">Pricing</h2>
    <!--PRICING-->
  </div>
</section>

<!-- ══ 7b. FAQ ════════════════════════════════════════════════════════════════
     Paper, under the plans, a hairline between them. Owner's call: everything
     important, in questions, and Highlight clips explained because nobody
     arriving knows what they are. Built by _faq() so every number is read
     from PLAN_LIMITS, and _faq_schema derives the FAQPage from this markup. -->
<section class="light faq" id="faq" aria-labelledby="faq-h">
  <div class="wrap faq-grid">
    <div class="faq-lead">
      <h2 class="disp l-h" id="faq-h">Questions</h2>
      <p class="l-sub">The short answers. The <a href="/tutorial">walkthrough</a> has the long ones, screen by screen.</p>
    </div>
    <div class="faq-cols">
      <!--FAQ-->
    </div>
  </div>
</section>

<!-- ══ 8. END ═════════════════════════════════════════════════════════════════
     One last frame, darker and quieter, the answer to the first headline,
     one button. Then the footer. -->
<section class="end" id="start" aria-labelledby="end-h">
  <div class="end-bg"><!--FRAME_END--></div>
  <div class="end-in wrap">
    <h2 class="disp end-h" id="end-h">Go stream. The clips will be waiting.</h2>
    <div class="end-act">
      <a href="/login" class="btn btn-go btn-lg">Start clipping free</a>
      <a href="/tutorial" class="btn btn-ghost btn-lg">Read the walkthrough</a>
    </div>
  </div>
</section>

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
  <img src="/static/logo-mark.png" alt="Highlightz" width="374" height="501">
  <nav aria-label="Site"><a href="/tutorial">Tutorial</a><a href="/compare">Compare</a><a href="/tos">Terms of Service</a><a href="/privacy">Privacy Policy</a><a href="/cookies">Cookie Policy</a><a href="/opt-out">Streamer Opt-Out</a></nav>
  <span class="fl">&copy; 2026 ANTI Technology LLC</span>
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
    // The keep rate is a separate number with its own gate — the server sends
    // null below the sample floor, so `typeof` and not truthiness: a genuine
    // 0 is a number we would show, and null is one we must not.
    var ktile=document.getElementById('stat-kept'), kel=document.getElementById('lp-kept');
    if(ktile&&kel&&d&&typeof d.kept_pct==='number'){
      kel.textContent=d.kept_pct+'%';
      kel.setAttribute('data-kept',String(d.kept_pct));
      ktile.style.display='';
    }
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

/* ── THE WALL ───────────────────────────────────────────────────────────────
   The hero is the detector, running. Four channels, each with its own baseline
   and its own threshold, each scored on a rolling window. One of them crosses,
   fires, and the clip that plays is a real clip this formula actually caught.

   WHY IT IS NOT RANDOM. Random noise reads as a screensaver. This is seeded
   value noise (deterministic: same cycle index, same trace) with scripted
   beats laid on top of it, and the beats are the point:

     plateau   the channel gets busy and stays busy. Nothing happens.
     nearmiss  it climbs to just under the line, holds, and decays. Nothing
               happens. THIS IS THE LOAD-BEARING ONE. A threshold that is
               never approached and missed is a threshold nobody believes.
     spike     it goes over. Exactly one tile per cycle gets one of these.

   The near-miss peak is chosen so that baseline + peak + the largest drift the
   noise can produce still lands under that tile's threshold. It cannot fire by
   accident; there is no clamp anywhere covering for it.

   Everything written here is a transform, an opacity, a clip-path, or text.
   No layout property is animated. The loop stops when the tab is hidden or the
   wall leaves the viewport, and the clock freezes with it so resuming does not
   teleport. ─────────────────────────────────────────────────────────────── */
(function(){
  var wall=document.getElementById('wall');
  if(!wall) return;
  var capRate=document.getElementById('wall-rate'),
      capState=document.getElementById('wall-state');
  var thread=document.getElementById('thread'),
      thScore=document.getElementById('thread-score');
  var root=document.documentElement;
  var reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var CYCLE=14000, WIN=9000, STEP=250, VW=300, VH=110;
  // Ten channels: what Pro watches. visibleCount() decides how many of
  // them are on screen at this width; the rest are neither drawn nor scored.
  var N=10;

  /* ── SOUND ────────────────────────────────────────────────────────────────
     The clip autoplays MUTED and there is no way around that from out here.
     Two separate walls, and both are worth writing down because the obvious
     fix does not work:

       1. Browsers block autoplay with audio outright. Muted is what buys the
          hero the right to play on its own at all.
       2. A Twitch CLIP embed cannot be unmuted from the outside. `muted=false`
          in the embed URL is a documented no-op — per Twitch's own developer
          forums the embed URL's muted flag does nothing until the viewer has
          used the player's own controls — and the clips embed exposes no JS
          API to call instead. The Twitch player SDK covers channels, videos
          and collections; clips are not on that list.

     AN EARLIER PASS HERE SHIPPED A SOUND ON/OFF BUTTON that rebuilt the src
     with muted=false. It was verified against a STUB iframe, which ignored
     every parameter it was handed, so the test proved the URL changed and
     nothing else. Against the real player it does nothing at all. Do not add
     it back; the reason it looks like it should work is the reason it keeps
     getting re-added.

     The only control that can unmute the clip lives inside the iframe. The bar
     points at it rather than impersonating it, and the code below notices when
     the viewer takes it up on that. */
  var FIRE_MIN=4800;             // nothing may fire before the wall has settled
  var SIGS=['chat','audio','keys','views','hype'];

  /* Built with RegExp(), never a literal: this file is inside a Python
     triple-quoted string, so a backslash here is Python's and is eaten before
     the browser ever sees it. */
  var SLUG=new RegExp('/clip/([^/?#]+)');
  var RE_PREVIEW=new RegExp('-preview-[0-9]+x[0-9]+[.]');
  function hiRes(u){ return (u||'').replace(RE_PREVIEW,'-preview-1280x720.'); }

  function mulberry(a){
    return function(){
      a|=0; a=a+0x6D2B79F5|0;
      var t=Math.imul(a^a>>>15,1|a);
      t=t+Math.imul(t^t>>>7,61|t)^t;
      return ((t^t>>>14)>>>0)/4294967296;
    };
  }
  function smooth(x){ x=x<0?0:(x>1?1:x); return x*x*(3-2*x); }
  function clamp(v,lo,hi){ return v<lo?lo:(v>hi?hi:v); }
  function noiseArr(rnd,n){ var a=[],i; for(i=0;i<n;i++) a.push(rnd()*2-1); return a; }
  function noiseAt(a,x){
    var n=a.length, i=Math.floor(x), f=smooth(x-i);
    var v0=a[((i%n)+n)%n], v1=a[(((i+1)%n)+n)%n];
    return v0+(v1-v0)*f;
  }
  function envAt(b,t){
    var u=t-b.at;
    if(u<=0) return 0;
    if(u<b.rise) return b.peak*smooth(u/b.rise);
    if(u<b.rise+b.hold) return b.peak;
    var f=(u-b.rise-b.hold)/b.fall;
    return f>=1?0:b.peak*(1-smooth(f));
  }

  /* ── the clips ─────────────────────────────────────────────────────────── */
  /* REAL CHANNELS, all of them (owner's call). The curated showcase fills
     the first tiles with the streamers whose clips it holds; the rest are
     backfilled from this list of well-known Twitch channels and the category
     each is best known for. Invented names read as a mockup; the wall is
     meant to read as ten real channels being scored. The category is only a
     label — nothing here claims a channel is live or a customer. */
  var clips=[], names=[
    {n:'kaicenat',g:'Just Chatting'},{n:'xqc',g:'Just Chatting'},
    {n:'caseoh_',g:'Minecraft'},{n:'plaqueboymax',g:'Just Chatting'},
    {n:'lacy',g:'Just Chatting'},{n:'tarik',g:'VALORANT'},
    {n:'shroud',g:'Counter-Strike'},{n:'summit1g',g:'Grand Theft Auto V'},
    {n:'ibai',g:'Just Chatting'},{n:'clix',g:'Fortnite'},
    {n:'nickmercs',g:'Call of Duty: Warzone'},{n:'pokimane',g:'Just Chatting'},
    {n:'timthetatman',g:'Call of Duty: Warzone'},{n:'jynxzi',g:'Rainbow Six Siege'},
    {n:'stableronaldo',g:'Just Chatting'},{n:'duxxion',g:'Rocket League'}];
  function embedFor(c){
    if(c.embed_url) return c.embed_url;
    var m=c.twitch_url?SLUG.exec(c.twitch_url):null;
    return m?('https://clips.twitch.tv/embed?clip='+m[1]):'';
  }

  /* ── a cycle's worth of channels ───────────────────────────────────────── */
  function buildCycle(idx){
    var rnd=mulberry(1013904223+idx*2654435761);
    var vis=visibleCount();
    var fireI=idx%vis;                     // rotates, so every channel gets one
    var missI=vis>1?((fireI+1+Math.floor(rnd()*(vis-1)))%vis):0;
    if(missI===fireI) missI=(fireI+1)%vis;
    // FOUR DISTINCT NAMES, always. `clips` is deduplicated by channel when it
    // is fetched, but it can still be shorter than four — or empty, before the
    // request lands — so the invented names backfill the rest, skipping any
    // that a real clip has already taken. Rotating by idx means each cycle
    // starts one further along, so over time every curated channel appears.
    var pick=[], used={}, ci=0, ni=0, j;
    for(j=0;j<N;j++){
      var chosen=null;
      while(ci<clips.length){
        var c=clips[(idx*N+ci)%clips.length]; ci++;
        // Trailing underscores are dropped from the KEY only, so a curated
        // "caseoh" and the list's "caseoh_" count as the same channel.
        var k=(c.channel||'').toLowerCase().replace(/_+$/,'');
        if(k && !used[k]){ used[k]=1; chosen={clip:c,name:c.channel}; break; }
      }
      if(!chosen){
        while(ni<names.length*2){
          var nm=names[(idx*N+ni)%names.length]; ni++;
          var nk=nm.n.toLowerCase().replace(/_+$/,'');
          if(!used[nk]){ used[nk]=1; chosen={clip:null,name:nm.n,game:nm.g}; break; }
        }
      }
      pick.push(chosen||{clip:null,name:names[j%names.length].n,game:names[j%names.length].g});
    }
    var out=[],i;
    for(i=0;i<N;i++){
      var base=Math.round(24+rnd()*11);          // 24..35
      var thresh=Math.round(64+rnd()*11);        // 64..75
      var beats=[];
      // Everyone gets some ordinary business early on.
      beats.push({at:900+rnd()*900, rise:800+rnd()*500, hold:1100+rnd()*1400,
                  fall:900+rnd()*700, peak:9+rnd()*11});
      if(i===fireI){
        base=33; thresh=71;
        // The spike HOLDS for as long as the clip is on screen. It decays
        // only after the stage collapses. A moment that is still being
        // clipped has not stopped being hot, and letting the score sag while
        // its own clip plays made the nav readout contradict the wall.
        beats=[{at:1500,rise:900,hold:1500,fall:900,peak:15},
               {at:5300,rise:950,hold:6300,fall:1400,peak:56}];
      } else if(i===missI){
        base=30; thresh=68;
        // 30 + 31 + 5.0 (the most the noise can ever add) = 66 < 68.
        beats=[{at:3000,rise:850,hold:520,fall:1400,peak:31}];
      }
      var sn=[],k;
      for(k=0;k<5;k++) sn.push(noiseArr(rnd,10));
      out.push({
        base:base, thresh:thresh, beats:beats,
        n1:noiseArr(rnd,14), n2:noiseArr(rnd,26), sn:sn,
        w:[.9+rnd()*.1,.8+rnd()*.2,.7+rnd()*.3,.75+rnd()*.25,.85+rnd()*.15],
        fires:(i===fireI), misses:(i===missI),
        clip:pick[i].clip,
        name:pick[i].name,
        game:pick[i].game||''
      });
    }
    out.fireI=fireI; out.missI=missI;
    return out;
  }

  // Drift is bounded on purpose: 3.2 + 1.8 = 5.0 is the most it can ever add,
  // and the near-miss peak above is picked against exactly that number.
  function driftAt(tl,t){
    return noiseAt(tl.n1,t/1400)*3.2 + noiseAt(tl.n2,t/430)*1.8;
  }
  function envTotal(tl,t){
    var e=0,i; for(i=0;i<tl.beats.length;i++) e+=envAt(tl.beats[i],t);
    return e;
  }
  function scoreAt(tl,t){
    return clamp(tl.base+driftAt(tl,t)+envTotal(tl,t),4,99);
  }
  function sigAt(tl,t,k){
    var e=envTotal(tl,t-k*70);
    var lift=tl.w[k]*(e/56)*0.86;
    return clamp(0.10+noiseAt(tl.sn[k],t/700+k*3)*0.13+lift,0.03,1);
  }

  // Must track the CSS above exactly. If this says four and the stylesheet is
  // showing two, the cycle can pick a hidden tile to fire and the payoff of
  // the whole hero happens off screen.
  function visibleCount(){
    /* Mirrors the CSS: the 2x2 shows all four everywhere except a phone,
       where the wall is a single column of two. */
    if(window.matchMedia('(max-width:700px)').matches) return 4;
    if(window.matchMedia('(max-width:1000px)').matches) return 6;
    return N;
  }

  /* ── DOM ───────────────────────────────────────────────────────────────── */
  var els=[];
  function buildTiles(){
    var i,k,frag=document.createDocumentFragment();
    for(i=0;i<N;i++){
      var t=document.createElement('article');
      t.className='tile';
      t.setAttribute('aria-hidden','true');
      t.style.setProperty('--d',(i*80)+'ms');
      var top=document.createElement('header'); top.className='tile-top';
      var ch=document.createElement('span'); ch.className='tile-ch';
      var dot=document.createElement('i'); dot.className='tile-dot';
      var chT=document.createTextNode('');
      ch.appendChild(dot); ch.appendChild(chT);
      var gm=document.createElement('span'); gm.className='tile-game';
      top.appendChild(ch); top.appendChild(gm);

      var read=document.createElement('div'); read.className='tile-read';
      var sc=document.createElement('span'); sc.className='tile-score'; sc.textContent='0';
      var th=document.createElement('span'); th.className='tile-th';
      read.appendChild(sc); read.appendChild(th);

      var chart=document.createElement('div'); chart.className='tile-chart';
      var svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 '+VW+' '+VH);
      svg.setAttribute('preserveAspectRatio','none');
      svg.setAttribute('aria-hidden','true');
      var thl=document.createElementNS('http://www.w3.org/2000/svg','line');
      thl.setAttribute('class','tile-thline');
      thl.setAttribute('x1','0'); thl.setAttribute('x2',String(VW));
      var ln=document.createElementNS('http://www.w3.org/2000/svg','path');
      ln.setAttribute('class','tile-line');
      // THE ABOVE-THRESHOLD HALF. Same d, drawn again, clipped to everything
      // above this channel's own line. Two paths and a rect rather than a
      // stroke that changes colour part way along, because SVG has no way to
      // vary a stroke by y and splitting the point list would put a seam at
      // the crossing — the one place it must not be.
      var defs=document.createElementNS('http://www.w3.org/2000/svg','defs');
      var cp=document.createElementNS('http://www.w3.org/2000/svg','clipPath');
      var cid='thclip'+i; cp.setAttribute('id',cid);
      var crect=document.createElementNS('http://www.w3.org/2000/svg','rect');
      crect.setAttribute('x','0'); crect.setAttribute('y','0');
      crect.setAttribute('width',String(VW)); crect.setAttribute('height','0');
      cp.appendChild(crect); defs.appendChild(cp);
      var lnOver=document.createElementNS('http://www.w3.org/2000/svg','path');
      lnOver.setAttribute('class','tile-line-over');
      lnOver.setAttribute('clip-path','url(#'+cid+')');
      var hd=document.createElementNS('http://www.w3.org/2000/svg','circle');
      hd.setAttribute('class','tile-head'); hd.setAttribute('r','2.6');
      svg.appendChild(defs); svg.appendChild(thl); svg.appendChild(ln);
      svg.appendChild(lnOver); svg.appendChild(hd);
      var flag=document.createElement('span'); flag.className='tile-flag';
      // The clip, pulled out of the tile when it fires.
      var pull=document.createElement('span'); pull.className='tile-pull';
      pull.textContent='clip saved';
      var thmark=document.createElement('span'); thmark.className='tile-thmark';
      chart.appendChild(svg); chart.appendChild(flag); chart.appendChild(thmark);

      var sigs=document.createElement('ul'); sigs.className='tile-sigs';
      var bars=[];
      for(k=0;k<5;k++){
        var li=document.createElement('li');
        var kk=document.createElement('span'); kk.className='sg-k'; kk.textContent=SIGS[k];
        var bb=document.createElement('span'); bb.className='sg-b';
        var fill=document.createElement('i');
        bb.appendChild(fill); li.appendChild(kk); li.appendChild(bb);
        sigs.appendChild(li); bars.push(fill);
      }
      t.appendChild(top); t.appendChild(read); t.appendChild(chart); t.appendChild(sigs);
      t.appendChild(pull);
      frag.appendChild(t);
      els.push({root:t,chT:chT,game:gm,score:sc,th:th,line:ln,head:hd,thl:thl,
                lineOver:lnOver,clipRect:crect,thmark:thmark,
                flag:flag,pull:pull,bars:bars,pts:[]});
    }
    wall.appendChild(frag);
  }
  buildTiles();

  function yFor(s){ return VH-6-s*(VH-12)/100; }

  function dress(cyc){
    var i;
    for(i=0;i<N;i++){
      var tl=cyc[i], e=els[i];
      var nm=(tl.clip&&tl.clip.channel)?tl.clip.channel:tl.name;
      e.chT.nodeValue=nm;
      e.game.textContent=(tl.clip&&tl.clip.game)?tl.clip.game:(tl.game||'');
      e.th.textContent='thr '+tl.thresh;
      var y=yFor(tl.thresh).toFixed(1);
      e.thl.setAttribute('y1',y); e.thl.setAttribute('y2',y);
      // Everything above the line, in viewBox units, is what the bright copy
      // of the trace is allowed to paint into.
      e.clipRect.setAttribute('height',y);
      // And the reading rides the line. The SVG is stretched with
      // preserveAspectRatio=none, so a percentage of the chart's height is the
      // only position that survives the tile being any size.
      e.thmark.textContent='thr '+tl.thresh;
      e.thmark.style.top=(yFor(tl.thresh)/VH*100).toFixed(2)+'%';
      e.root.classList.remove('hot','fire');
      e.flag.classList.remove('on'); e.flag.textContent='';
      e.pull.classList.remove('go');
      // Backfill the rolling window before the first frame. A chart that draws
      // itself in from an empty left edge announces that it just started, and
      // the whole claim here is that these channels were already being watched
      // when you arrived. The backfill is the same score function evaluated at
      // negative time, so it is the real trace, not filler: the beats have not
      // begun yet, which is exactly what the minute before a spike looks like.
      e.pts=[];
      var bt;
      for(bt=-WIN;bt<0;bt+=STEP) e.pts.push([bt,scoreAt(tl,bt)]);
    }
  }

  /* ── the fire ──────────────────────────────────────────────────────────── */
  /* REMOVED: teardownFrame, openStage, embedSrc, closeStage and holdFor —
     the machinery that expanded the firing tile into a full-width Twitch
     player, held it for the clip's real duration, then collapsed it back.
     About 120 lines, an iframe, a poster, a blurred wash, a clip-path
     animation and a sound-consent path, all so the wall could stop being a
     wall every fourteen seconds.

     The wall is a monitor. The crossing is still the moment — the trace goes
     bright and heavy above the line, the tile marks OVER THRESHOLD, and the
     caption says a clip was taken — and the page carries on monitoring
     instead of pausing to play a video at you. */

  /* ── the loop ──────────────────────────────────────────────────────────── */
  /* `closed` and `engaged` are gone with the stage: one tracked whether the
     clip player had collapsed again, the other whether a visitor had clicked
     into it for sound. Neither has anything to be true about now. */
  var cycIdx=-1, cyc=null, cycFireI=0, fired=false, staged=false;
  var started=false;
  var elapsed=0, cycleStart=0, cycleLen=CYCLE, firedScore=0;
  var last=null, raf=0, lastStep=-1, lastLit=-1, vis=visibleCount();

  function reseed(){
    cycIdx++;
    cyc=buildCycle(cycIdx); cycFireI=cyc.fireI;
    fired=false; staged=false; lastStep=-1;
    cycleLen=CYCLE; firedScore=0;
    dress(cyc);
    if(capRate) capRate.textContent=String(vis);
    if(capState) capState.textContent='watching';
    var i;
    for(i=0;i<N;i++){ (function(e){ requestAnimationFrame(function(){
      e.root.classList.add('in'); }); })(els[i]); }
  }

  function render(t){
    var i,k,best=0,bestName='';
    for(i=0;i<vis;i++){
      var tl=cyc[i], e=els[i];
      var s=scoreAt(tl,t), sr=Math.round(s);
      e.score.textContent=String(sr);
      if(s>best){ best=s; bestName=e.chT.nodeValue; }

      e.pts.push([t,s]);
      while(e.pts.length&&e.pts[0][0]<t-WIN) e.pts.shift();
      var d='',ax=0,ay=0;
      for(k=0;k<e.pts.length;k++){
        ax=((e.pts[k][0]-(t-WIN))/WIN)*VW;
        ay=yFor(e.pts[k][1]);
        d+=(k===0?'M':' L')+ax.toFixed(1)+','+ay.toFixed(1);
      }
      if(d){ e.line.setAttribute('d',d);
             e.lineOver.setAttribute('d',d);
             e.head.setAttribute('cx',ax.toFixed(1));
             e.head.setAttribute('cy',ay.toFixed(1)); }
      for(k=0;k<5;k++) e.bars[k].style.setProperty('--v',sigAt(tl,t,k).toFixed(3));

      var over=s>=tl.thresh;
      // Ten points, not six. The near-miss beat peaks about seven under its
      // threshold by construction, so a six-point band caught it for a single
      // sample or not at all — the one beat that has to be legible was the one
      // nobody could see.
      var near=!over&&s>=tl.thresh-10;
      e.root.classList.toggle('hot',near||over);
      if(over&&t>=FIRE_MIN&&!e.root.classList.contains('fire')){
        e.root.classList.add('fire');
        e.pull.classList.add('go');
        e.flag.textContent='OVER THRESHOLD';
        e.flag.classList.add('on');
      } else if(near&&!e.flag.classList.contains('on')&&!over){
        e.flag.textContent='NEAR MISS';
        e.flag.classList.add('on');
      } else if(!near&&!over&&e.flag.classList.contains('on')&&
                !e.root.classList.contains('fire')){
        e.flag.classList.remove('on');
      }

      if(over&&t>=FIRE_MIN&&i===cycFireI&&!fired){
        fired=true;
        if(capState) capState.textContent='trigger fired · clipping';
      }
    }

    // While a clip is on screen the readouts hold at the value that fired it.
    // The firing tile's own beat decays underneath the stage, and letting the
    // nav and the rail follow it down meant they drifted back to baseline
    // while the clip that crossed the line was still playing.
    if(staged&&firedScore) best=firedScore;

    navPaint(best);
    // The through-line down the page edge reads the wall while the wall is on
    // screen. Leaving it on the scroll wave meant the rail said 0 while four
    // tiles behind it were showing real numbers.
    if(thScore) thScore.textContent=String(Math.round(best));
    if(thread) thread.classList.toggle('fired',fired);
    if(capState&&!fired&&t>3200&&t<FIRE_MIN)
      capState.textContent='chat surging on '+bestName;
  }

  /* Everything the NAV shows, and nothing else. Lifted out of render() so the
     wall loop and the nav loop below cannot drift into showing two different
     numbers for the same thing — there is one writer for these three nodes. */
  function navPaint(best){
    // One number lights the whole room. Only written when it actually moves —
    // a custom property on <html> invalidates style for the entire document.
    var lit=clamp((best-58)/34,0,1), q=Math.round(lit*20)/20;
    if(q!==lastLit){ lastLit=q; root.style.setProperty('--lit',String(q)); }
  }

  function tick(now){
    raf=0;
    if(last===null) last=now;
    var dt=now-last; last=now;
    if(dt>500) dt=STEP;                 // came back from a freeze; do not jump
    elapsed+=dt;
    // A cycle START and a cycle LENGTH, not a modulo. The length is not fixed
    // any more: with sound on, the stage holds until the clip has actually
    // finished, and modulo arithmetic cannot express a period that changes
    // partway through.
    var t=elapsed-cycleStart;
    if(t>=cycleLen){ cycleStart=elapsed; t=0; reseed(); }
    if(t-lastStep>=STEP||lastStep<0){
      lastStep=t;
      render(t);
      // The fire used to open the clip stage here. It now only reports: the
      // tile keeps its OVER THRESHOLD mark and the caption says what happened.
      // The cycle length stays CYCLE, because nothing has to be held on screen
      // for a video to finish playing any more.
      if(fired&&!staged&&t>=6000){
        staged=true;
        firedScore=Math.round(scoreAt(cyc[cycFireI],t));
        if(capState) capState.textContent='trigger fired · clip saved to your queue';
      }
    }
    raf=requestAnimationFrame(tick);
  }

  /* ── THE NAV READOUT OUTLIVES THE WALL ─────────────────────────────────────
     The nav is position:sticky and never leaves the screen — the markup calls
     the readout "the signature, in its persistent form". But every write to it
     lived in render(), which is driven by the loop below, which is parked the
     moment the hero wall scrolls out of view. So the sparkline froze mid-stroke
     on the first scroll and held one number for the entire rest of the page.

     PARKING THE WALL LOOP IS STILL RIGHT and is not what changed. It drives
     four tiles and twenty signal bars, none of which are on screen. (It also
     used to drive a clip-path stage and an embedded Twitch iframe, which made
     parking it doubly important — that machinery is gone now, but the loop is
     still work nobody can see.) What was wrong is that a 42x14 decoration in a
     sticky bar was chained to it.

     So the readout gets its own loop. It reads the SAME cycle through the SAME
     scoreAt(), so the number still means what it meant before — it just does
     pure arithmetic and writes three attributes, and it never advances the
     cycle, touches the stage, or reseeds. Exactly one of the two loops runs at
     a time; sync() below is the only place that hands over. */
  var navRaf=0, navT=0, navLast=null;
  function navTick(now){
    navRaf=0;
    if(navLast===null) navLast=now;
    var dt=now-navLast; navLast=now;
    if(dt>500) dt=STEP;               // came back from a freeze; do not jump
    navT+=dt; if(navT>=CYCLE) navT-=CYCLE;
    var i,best=0;
    for(i=0;i<vis;i++){ var s=scoreAt(cyc[i],navT); if(s>best) best=s; }
    navPaint(best);
    navRaf=requestAnimationFrame(navTick);
  }
  // Hidden tab and reduced motion are refusals to animate at all. A scroll
  // position is not one, which is the whole bug — so onScreen is absent here
  // on purpose. `cyc` is required: the wall loop guards on `started` for the
  // same reason, the showcase fetch has not resolved on the first callback.
  function navRunning(){
    return started && cyc && !document.hidden && !reduce;
  }

  function running(){
    // `started` is not decoration. The wall is observed the moment it exists,
    // and the observer fires its first callback immediately — before the
    // showcase fetch has resolved and therefore before there is a cycle to
    // render. Without this the loop started against a null cycle and threw on
    // the first frame, on whichever viewports lost that race.
    return started && !document.hidden && onScreen && !reduce;
  }
  function sync(){
    if(running()){
      // The wall is back and owns the readout again.
      if(navRaf){ cancelAnimationFrame(navRaf); navRaf=0; }
      if(!raf){ last=null; raf=requestAnimationFrame(tick); }
      return;
    }
    if(raf){ cancelAnimationFrame(raf); raf=0; }
    if(navRunning()){
      // Pick the readout up where the wall left it rather than at zero, so
      // scrolling past the hero is not a visible jump in the sparkline.
      // Modulo, not a bare subtraction: with sound on, cycleLen runs past
      // CYCLE for the clip's real duration, so the handover time can be well
      // outside one period.
      if(!navRaf){ navT=(elapsed-cycleStart)%CYCLE; navLast=null;
                   navRaf=requestAnimationFrame(navTick); }
    } else if(navRaf){ cancelAnimationFrame(navRaf); navRaf=0; }
  }
  var onScreen=true;
  if('IntersectionObserver' in window){
    new IntersectionObserver(function(es){
      onScreen=es[0].isIntersecting;
      root.setAttribute('data-hero',onScreen?'1':'0');
      sync();
    },{rootMargin:'80px'}).observe(wall);
  }
  root.setAttribute('data-hero','1');
  document.addEventListener('visibilitychange',sync);

  /* REMOVED with the stage: a blur/activeElement probe that detected a click
     INTO the cross-origin player (the only signal that crosses that boundary),
     plus the click-to-unmute handler it fed. Both existed to give an embedded
     clip sound consent and to hold the cycle open for the clip's real duration.
     There is no player to click into any more. */
  window.addEventListener('resize',function(){
    var v=visibleCount();
    if(v!==vis){ vis=v; if(capRate) capRate.textContent=String(vis); }
  },{passive:true});

  /* Reduced motion gets the composed frame, not a blank one and not a reduced
     one: the wall as it looks the instant after the fire, nothing moving. Not
     "less motion" — none.

     The stage stays clipped to the FIRING TILE rather than opening over the
     whole wall. Opening it full-width was a frame where the only thing on
     screen was a clip, which throws away the part that carries the argument —
     three channels still being scored, thresholds visible, one of them over
     the line with its clip already sitting in its place. */
  function composeStatic(){
    cycIdx=0; cyc=buildCycle(0); cycFireI=cyc.fireI; dress(cyc);
    var i; for(i=0;i<N;i++) els[i].root.classList.add('in');
    var t=6600;
    render(t);
    if(capState) capState.textContent='trigger fired · clip saved to your queue';
    // Reduced motion gets the same frame everyone else ends on: four channels
    // scored, thresholds drawn, one of them over its line. It used to also
    // compose the clip player into the firing tile, which was the most moving
    // part of a frame whose whole purpose is that nothing moves.
  }

  function start(){
    if(reduce){ composeStatic(); return; }
    reseed();
    started=true;
    sync();
  }

  fetch('/landing/showcase')
    .then(function(r){ return r.ok?r.json():null; })
    // One curated list, two destinations. `!== false` rather than a truthy
    // check so an entry saved before placement existed still shows up.
    .then(function(d){
      // ONE PER CHANNEL. The wall shows four tiles side by side, and the
      // curated list can easily hold three clips from the same streamer — so
      // the same name appeared twice, or even three times, in a row of four.
      // A wall that claims to watch four channels at once has to show four
      // channels. Later clips from a channel already in the pool are dropped
      // here rather than filtered at render, so every consumer of `clips`
      // sees the same deduplicated list.
      var seen={}, pool=[];
      (((d&&d.clips)||[]).filter(function(c){ return c.hero !== false; }))
        .forEach(function(c){
          var k=(c.channel||'').toLowerCase();
          if(!k||seen[k]) return;
          seen[k]=1; pool.push(c);
        });
      clips=pool;
    })
    .catch(function(){})
    .then(start,start);
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

  /* ── the cover reveal. The cover keeps its box; only its contents lift and
     fade, so the page below rises into a screen that is emptying rather than
     one being pushed. Transform and opacity only — nothing here reflows.
     The resting state is fully visible and untransformed, which is what JS-off
     and reduced motion get (frame() is never called there), so everything
     below may only ever take the cover AWAY. */
  var coverIn = document.getElementById('cover-in');
  var coverCue = document.getElementById('cover-cue');
  var coverEl = document.getElementById('cover');
  var coverWasPast = null;

  /* HOW FAR THE FADE GETS TO RUN. Not a fixed fraction of the screen: the
     content is centred, so it starts leaving through the TOP of the viewport
     after only its own offset — 272px at 1440x950. A budget of 0.72 of a
     screen meant it was still at 60% opacity when the top edge cut it in half,
     which is the "half on the black, half off" state. The budget is the
     content's own distance to the top instead, less the lift, so opacity hits
     zero at the exact moment its first pixel would be clipped: it is either
     on the black screen or it is gone, never sliced.
     offsetTop and not getBoundingClientRect: the rect includes the transform
     this same code is writing, so measuring with it would feed back on
     itself. offsetTop is layout, which the transform does not touch. */
  var coverBudget = 1, coverLift = 0, coverMeasuredAt = -1;
  function measureCover(){
    coverMeasuredAt = window.innerHeight;
    var top = coverIn.offsetTop + (coverEl ? coverEl.offsetTop : 0);
    /* The lift eats the budget it moves through, so it is capped at a quarter
       of it. On a phone the content nearly fills the screen — top is about 95
       against 272 on a desktop — and a flat 40px lift would have spent nearly
       half of what there is to spend. */
    coverLift = Math.min(40, top * 0.25);
    coverBudget = Math.max(40, top - coverLift);
  }

  function frame(){
    ticking = false;
    var h = document.documentElement.scrollHeight - window.innerHeight;
    var y = window.scrollY || window.pageYOffset;
    var prog = h > 0 ? Math.min(1, Math.max(0, y / h)) : 0;

    if (coverIn){
      /* Re-measured only when the viewport height changes, because the budget
         depends on where centring put the content. Reading it every frame
         would force a layout on every scroll event. */
      if (window.innerHeight !== coverMeasuredAt) measureCover();
      var k = Math.min(1, y / coverBudget);
      coverIn.style.opacity = (1 - k).toFixed(3);
      coverIn.style.transform = 'translate3d(0,' + (-k * coverLift).toFixed(1) + 'px,0)';
      /* Same k, so the whole cover shares one timeline and nothing is left
         behind on the black after the rest of it has gone. */
      if (coverCue) coverCue.style.opacity = (1 - k).toFixed(3);
      var past = y > window.innerHeight * 0.6;
      if (past !== coverWasPast){
        coverWasPast = past;
        document.body.classList.toggle('past-cover', past);
      }
    }

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
    /* ONE writer at a time. While the hero is on screen the WALL owns --lit —
       it is a real score off a real threshold, and the scroll wave second-
       guessing it made the room flicker between two different numbers. The
       wall sets data-hero on <html> while it is visible; below the fold the
       scroll wave takes over. */
    if (root.getAttribute('data-hero') !== '1'){
      root.style.setProperty('--lit', lit.toFixed(3));
      if (scoreEl) scoreEl.textContent = Math.round(lit * 100);
    }

    var over = lit > 0.62;
    if (thread) thread.classList.toggle('fired', over);

    /* Wash the section being entered, once per crossing. */
    for (var j = 0; j < seams.length; j++){
      var rect = seams[j].getBoundingClientRect();
      var entering = rect.top < window.innerHeight * 0.72 && rect.bottom > window.innerHeight * 0.25;
      seams[j].classList.toggle('lit', entering && over);
    }
  }
  var slidesBound = false;
  function onScroll(){
    if (!ticking){ ticking = true; requestAnimationFrame(frame); }
    if (slidesBound) armRest();
  }

  /* ── TWO SLIDES ──────────────────────────────────────────────────────────
     The cover and the site, with nothing in between. One scroll gesture on the
     cover carries you to the top of the site in a single move; one scroll up
     from the top of the site carries you back. Fading alone was not enough:
     you could still come to REST anywhere inside the transition, which is what
     made the cover read as half on the black screen and half off it. There is
     no third resting place now.

     Not CSS scroll-snap. Measured in Chromium on this page: `mandatory` with
     snap points only at the top drags you back to 0 from anywhere in the
     document (150/500/900/1400/2500 all landed at 0), and `proximity` leaves
     the middle un-snapped (500 landed at 334) -- which is the exact state
     being fixed.

     It engages only when the cover FITS the window. If the content is taller
     than the viewport there is real scrolling to do inside the cover, and
     swallowing that would trap the reader with no way to see the rest. */
  var sliding = false, touchY = 0;

  function coverFits(){
    return !!coverEl && coverEl.offsetHeight <= window.innerHeight + 1;
  }
  function scrollY(){ return window.scrollY || window.pageYOffset || 0; }
  /* -1 on the cover, 0 at the top of the site, 1 past both (hands off). */
  function slideZone(){
    if (!coverEl) return 1;
    var h = coverEl.offsetHeight, y = scrollY();
    if (y < h - 2) return -1;
    if (y <= h + 2) return 0;
    return 1;
  }
  function slideTo(target){
    sliding = true;
    window.scrollTo({ top: target, behavior: 'smooth' });
    var t0 = Date.now();
    (function settle(){
      /* Released on arrival, with a ceiling so a scroll that never lands --
         an interrupted smooth scroll, a background tab -- cannot leave input
         swallowed forever. */
      if (Math.abs(scrollY() - target) < 2 || Date.now() - t0 > 1400){
        sliding = false; return;
      }
      requestAnimationFrame(settle);
    })();
  }
  /* True only when this gesture was consumed, so the caller knows whether it
     may cancel the event. Cancelling on a gesture we did NOT act on would stop
     the reader scrolling down off the top of the site. */
  function slideIntent(down){
    if (sliding || !coverFits()) return false;
    var z = slideZone();
    if (down && z === -1){ slideTo(coverEl.offsetHeight); return true; }
    if (!down && z === 0){ slideTo(0); return true; }
    return false;
  }

  /* The backstop. Hijacking gestures covers the wheel, the keys and a swipe,
     but not every way into the middle: dragging the scrollbar lands wherever
     you drop it, and scrolling UP from deep in the page is deliberately not
     hijacked (hands off past both slides) so it can carry you into the cover
     from below. Whatever the route, if the scroll comes to rest INSIDE the
     transition it is taken to the nearer of the two slides. Debounced, so it
     only ever acts on a scroll that has already stopped -- it never fights a
     gesture in progress. */
  var restTimer = 0;
  function armRest(){
    if (!coverFits()) return;
    clearTimeout(restTimer);
    restTimer = setTimeout(function(){
      if (sliding) return;
      var h = coverEl.offsetHeight, y = scrollY();
      if (y > 2 && y < h - 2) slideTo(y * 2 < h ? 0 : h);
    }, 140);
  }

  function bindSlides(){
    slidesBound = true;
    window.addEventListener('wheel', function(e){
      if (!coverFits()) return;
      if (sliding && slideZone() < 1){ e.preventDefault(); return; }
      if (!e.deltaY) return;
      if (slideIntent(e.deltaY > 0)) e.preventDefault();
    }, { passive: false });

    window.addEventListener('keydown', function(e){
      var t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      var k = e.key;
      var down = (k === 'ArrowDown' || k === 'PageDown' || k === ' ' || k === 'Spacebar');
      var up = (k === 'ArrowUp' || k === 'PageUp');
      if (!down && !up) return;
      if (slideIntent(down)) e.preventDefault();
    });

    window.addEventListener('touchstart', function(e){
      if (e.touches && e.touches.length) touchY = e.touches[0].clientY;
    }, { passive: true });

    window.addEventListener('touchmove', function(e){
      if (!coverFits() || !e.touches || !e.touches.length) return;
      if (sliding && slideZone() < 1){ e.preventDefault(); return; }
      var dy = touchY - e.touches[0].clientY;
      if (Math.abs(dy) < 6) return;
      if (slideIntent(dy > 0)) e.preventDefault();
    }, { passive: false });
  }

  if (!reduce){
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    frame();
    /* Not under reduced motion: taking someone's scroll away and teleporting
       them a screen is the kind of movement that setting exists to refuse.
       There the page just scrolls, and the cover is one tall black block. */
    bindSlides();
  } else if (thread){ thread.style.display = 'none'; }
})();
</script>

<script>
/* ── The shelf, the player, and the scrubbed score ──────────────────────────
   Three small controllers. No backslashes anywhere in this block: the page
   is a Python triple-quoted string and a backslash would be eaten before the
   browser saw it. */
(function(){
  var reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── the rail filters the shelf ── */
  var rail=document.getElementById('rail'), shelf=document.getElementById('shelf');
  var track=document.getElementById('shelf-track');
  /* THE LOOP. The set is cloned as many times as the shelf needs — an even
     number, so sliding the track by exactly half of it lands on a seam
     between two identical sets and the wrap is invisible — and the
     duration is read from the set's real width so the speed is the same
     however many clips are curated (about 55px a second). A set narrower
     than the shelf is not a reason to stand still: with three clips the
     track is simply built from more copies. Only when there is something
     to loop: one card gliding alone is a bug. Every clone is decorative and
     out of the tab order. */
  var looped=false;
  function cloneSet(first){
    var copy=first.cloneNode(true);
    copy.setAttribute('aria-hidden','true');
    Array.prototype.forEach.call(copy.querySelectorAll('a,button'),function(el){ el.setAttribute('tabindex','-1'); });
    return copy;
  }
  function sizeLoop(){
    if(!track||!looped) return;
    var first=track.querySelector('.shelf-set');
    if(!first) return;
    // Rebuild from the one real set every time: a filter changes which
    // cards are visible, so the clones are remade from the filtered set.
    Array.prototype.slice.call(track.querySelectorAll('.shelf-set')).slice(1).forEach(function(el){ track.removeChild(el); });
    var visible=Array.prototype.filter.call(first.querySelectorAll('.card'),function(c){ return !c.hidden; }).length;
    var w=first.getBoundingClientRect().width;
    var loop=visible>1&&w>0;
    shelf.classList.toggle('is-loop',loop);
    if(!loop) return;
    // Enough copies to cover the shelf twice over, rounded up to an even
    // count so -50% is a whole number of sets.
    var need=Math.ceil((shelf.clientWidth+8)/w);
    var copies=Math.max(2,need*2);
    for(var i=1;i<copies;i++) track.appendChild(cloneSet(first));
    shelf.style.setProperty('--shelf-t',Math.max(24,Math.round((w*copies/2)/55))+'s');
  }
  if(track){
    var first=track.querySelector('.shelf-set');
    var live=first?first.querySelectorAll('.card').length:0;
    if(first&&live>1){
      looped=true;
      sizeLoop();
      window.addEventListener('resize',sizeLoop,{passive:true});
      // Paused while nobody is looking: an animation off screen is work for nothing.
      if('IntersectionObserver' in window){
        new IntersectionObserver(function(es){ shelf.classList.toggle('is-off',!es[0].isIntersecting); },
                                 {rootMargin:'120px'}).observe(shelf);
      }
    }
  }
  if(rail&&shelf){
    var tabs=Array.prototype.slice.call(rail.querySelectorAll('.rail-t'));
    var cards=Array.prototype.slice.call((track||shelf).querySelector('.shelf-set').querySelectorAll('.card'));
    function pick(cat){
      var shown=0;
      tabs.forEach(function(t){
        var on=t.getAttribute('data-cat')===cat;
        t.classList.toggle('is-on',on);
        if(on) t.setAttribute('aria-current','true'); else t.removeAttribute('aria-current');
      });
      cards.forEach(function(c){
        var show=cat==='all'||c.getAttribute('data-cat')===cat;
        c.hidden=!show; if(show) shown++;
      });
      shelf.classList.toggle('is-empty',shown===0);
      // A filtered set is narrower, so the loop's duration follows it; and
      // the track starts over so the visible cards are the first ones.
      if(track){ track.style.animation='none'; void track.offsetWidth; track.style.animation=''; }
      sizeLoop();
      if(!shelf.classList.contains('is-loop')) shelf.scrollTo({left:0,behavior:reduce?'auto':'smooth'});
    }
    tabs.forEach(function(t){ t.addEventListener('click',function(){ pick(t.getAttribute('data-cat')); }); });
  }

  /* ── the player: a shelf card opens its clip in place ── */
  var SLUG=new RegExp('/clip/([^/?#]+)');
  var lb=document.getElementById('exl'), ifr=document.getElementById('exl-iframe');
  function closeLb(){
    if(!lb||lb.style.display==='none') return;
    if(document.fullscreenElement===lb&&document.exitFullscreen){
      try{ document.exitFullscreen().catch(function(){}); }catch(e){}
    }
    lb.style.display='none';
    if(ifr) ifr.src='about:blank';   // stop playback
    document.body.style.overflow='';
  }
  function openLb(a){
    var src=a.getAttribute('data-embed');
    if(!src){ var m=SLUG.exec(a.href||''); if(m) src='https://clips.twitch.tv/embed?clip='+m[1]; }
    if(!src||!lb||!ifr) return false;
    document.getElementById('exl-title').textContent=a.getAttribute('data-title')||'Clip';
    document.getElementById('exl-out').href=a.href||'#';
    // Reveal, force a reflow so the iframe has its size, THEN load: Twitch's
    // clip embed picks its rendition from the player's size when it boots.
    lb.style.display='';
    document.body.style.overflow='hidden';
    // FULL SCREEN, from the click that opened it (a gesture is required).
    // The embed reads its size when it boots, so this comes BEFORE src: on a
    // 1080p display the player then measures 1920x1080 and takes the 1080p
    // rendition. If full screen is refused the card is still the whole
    // viewport, which is the next best thing.
    try{ if(lb.requestFullscreen){ lb.requestFullscreen().catch(function(){}); } }catch(e){}
    void lb.offsetHeight;
    ifr.src=src+(src.indexOf('?')>=0?'&':'?')+'parent='+location.hostname+'&autoplay=true';
    return true;
  }
  // Leaving full screen (Esc, or the browser's own control) closes the
  // player too: one gesture to be done with it, not two.
  document.addEventListener('fullscreenchange',function(){
    if(!document.fullscreenElement) closeLb();
  });
  if(shelf){
    shelf.addEventListener('click',function(ev){
      var a=ev.target.closest?ev.target.closest('a.card'):null;
      if(!a) return;
      // Small screens keep the direct Twitch link: the embed is unreliable
      // in many mobile browsers.
      if(window.innerWidth<=700) return;
      if(openLb(a)) ev.preventDefault();
    });
  }
  var bg=document.getElementById('exl-bg'), x=document.getElementById('exl-close');
  if(bg) bg.addEventListener('click',closeLb);
  if(x) x.addEventListener('click',closeLb);
  document.addEventListener('keydown',function(e){ if(e.key==='Escape') closeLb(); });

  /* ── the scrubbed score ──
     Scroll position through the tall track becomes p in 0..1. The trace is
     a fixed shape — an idle baseline, a small lift, then the crossing — so
     the section tells the same story on every visit. Everything written is
     an SVG attribute, a class, or text. */
  var track=document.getElementById('score-track'), sec=document.getElementById('score');
  var path=document.getElementById('sc-path'), over=document.getElementById('sc-over');
  var head=document.getElementById('sc-head'), num=document.getElementById('sc-n');
  var tEl=document.getElementById('sc-t'), line=document.getElementById('sc-line');
  var sigs=track?Array.prototype.slice.call(track.querySelectorAll('#sc-sigs li')):[];
  var frames=track?Array.prototype.slice.call(track.querySelectorAll('#score-frame img')):[];
  var THR=71, VW=600, VH=220, SAMPLES=140;
  /* On a phone the head lives above the stuck scene (see .score-lead in the
     CSS): moved, not duplicated, and moved back if the viewport widens. */
  var lead=document.getElementById('score-lead');
  var grid=track?track.querySelector('.score-grid'):null;
  var headEl=track?track.querySelector('.score-head'):null;
  var narrow=window.matchMedia?window.matchMedia('(max-width: 900px)'):null;
  function placeHead(){
    if(!lead||!grid||!headEl) return;
    if(narrow&&narrow.matches){ if(headEl.parentNode!==lead) lead.appendChild(headEl); }
    else if(headEl.parentNode!==grid){ grid.insertBefore(headEl,grid.firstChild); }
  }
  placeHead();
  if(narrow){
    if(narrow.addEventListener) narrow.addEventListener('change',placeHead);
    else if(narrow.addListener) narrow.addListener(placeHead);
  }
  var LINES=['Baseline. Nothing to clip.',
             'Chat speeds up. The audio jumps. The signals stack.',
             'Over the line. Twitch makes the clip; it lands in your queue.'];
  function clamp(v,lo,hi){ return v<lo?lo:(v>hi?hi:v); }
  function bell(p,c,w){ var z=(p-c)/w; return Math.exp(-z*z); }
  function ramp(p,a,b){ var z=clamp((p-a)/(b-a),0,1); return z*z*(3-2*z); }
  function scoreAt(p){
    // Idle, a small lift, then the crossing — and it HOLDS over the line for
    // the rest of the track, the way a moment that is still being clipped
    // has not stopped being hot.
    var s=31+bell(p,0.30,0.09)*13+ramp(p,0.52,0.64)*52-ramp(p,0.86,1.0)*6
          +Math.sin(p*41)*1.2+Math.sin(p*17)*0.8;
    return clamp(s,20,96);
  }
  function yFor(s){ return VH-8-s*(VH-16)/100; }
  var lastK=-1, lastPhase=-1, lastFrame=-1;
  function paint(p){
    if(!path) return;
    var k=Math.round(p*SAMPLES);
    if(k===lastK) return;
    lastK=k;
    var d='',i,x=0,y=yFor(scoreAt(0)),s=scoreAt(0);
    for(i=0;i<=k;i++){
      var q=i/SAMPLES; s=scoreAt(q); x=q*VW; y=yFor(s);
      d+=(i===0?'M':' L')+x.toFixed(1)+','+y.toFixed(1);
    }
    path.setAttribute('d',d); over.setAttribute('d',d);
    head.setAttribute('cx',x.toFixed(1)); head.setAttribute('cy',y.toFixed(1));
    var sr=Math.round(s);
    if(num) num.textContent=String(sr);
    if(tEl){ var secs=Math.round(p*42); tEl.textContent='0:'+(secs<10?'0':'')+secs; }
    var fired=s>=THR;
    if(sec) sec.classList.toggle('score-fired',fired);
    sigs.forEach(function(li){ li.classList.toggle('on',p>=parseFloat(li.getAttribute('data-at'))); });
    var phase=p<0.34?0:(fired||p>0.66?2:1);
    if(phase!==lastPhase){ lastPhase=phase; if(line) line.textContent=LINES[phase]; }
    var fi=frames.length?Math.min(frames.length-1,Math.floor(p*frames.length*0.999)):-1;
    if(fi!==lastFrame){ lastFrame=fi; frames.forEach(function(im,j){ im.classList.toggle('on',j===fi); }); }
  }
  // The bright half of the trace is clipped to everything above the line,
  // like the wall's tiles: two paths and a clip rect.
  if(over){
    var svg=over.ownerSVGElement, ns='http://www.w3.org/2000/svg';
    var defs=document.createElementNS(ns,'defs'), cp=document.createElementNS(ns,'clipPath');
    cp.setAttribute('id','scclip');
    var rc=document.createElementNS(ns,'rect');
    rc.setAttribute('x','0'); rc.setAttribute('y','0'); rc.setAttribute('width',String(VW));
    rc.setAttribute('height',yFor(THR).toFixed(1));
    cp.appendChild(rc); defs.appendChild(cp); svg.insertBefore(defs,svg.firstChild);
    over.setAttribute('clip-path','url(#scclip)');
    var th=svg.querySelector('.sc-th');
    if(th){ th.setAttribute('y1',yFor(THR).toFixed(1)); th.setAttribute('y2',yFor(THR).toFixed(1)); }
  }
  if(track){
    if(reduce||!('requestAnimationFrame' in window)){
      paint(1);
    } else {
      var ticking=false;
      function frame(){
        ticking=false;
        var r=track.getBoundingClientRect();
        var span=r.height-window.innerHeight;
        var p=span>0?clamp(-r.top/span,0,1):1;
        paint(p);
      }
      function onScroll(){ if(!ticking){ ticking=true; requestAnimationFrame(frame); } }
      window.addEventListener('scroll',onScroll,{passive:true});
      window.addEventListener('resize',onScroll,{passive:true});
      frame();
    }
  }
})();
</script>

<script>
/* ── The nav's real height ────────────────────────────────────────────────
   Anchor targets clear the bar by subtracting --nav-h, and slide 2 subtracts
   it so nav + hero come to exactly one screen. The bar is not one fixed
   height: its links wrap in a band around 940px and it grows, so the CSS
   fallback is wrong across a couple of hundred pixels of width. Measure it
   and write the real number back.

   ResizeObserver rather than a resize listener alone, because the bar also
   changes height when its own contents change — the Example clips link is
   revealed from JS once the showcase loads, and that can be the thing that
   makes the links wrap. */
(function(){
  var nav = document.querySelector('.nav'), root = document.documentElement, last = 0;
  if (!nav) return;
  function measure(){
    var h = Math.round(nav.getBoundingClientRect().height);
    if (h && h !== last){ last = h; root.style.setProperty('--nav-h', h + 'px'); }
  }
  measure();
  window.addEventListener('resize', measure, { passive: true });
  if ('ResizeObserver' in window) new ResizeObserver(measure).observe(nav);
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
    # TAG-AGNOSTIC, BY THE THIRD TIME OF ASKING. This has now broken twice for
    # the same reason: it keyed on <details> and the FAQ became open Q&A, then
    # it keyed on <p> and the FAQ became <details> again. Each time the schema
    # silently emptied and the page published a FAQPage with no questions in it,
    # which is invisible in a browser. It now matches the two CLASSES on
    # whatever element carries them, so the container can change shape freely.
    for q, a in re.findall(
            r'<(?:p|summary|h[1-6]|div)[^>]*\bclass="[^"]*\bfaq-q\b[^"]*"[^>]*>(.*?)'
            r'</(?:p|summary|h[1-6]|div)>\s*'
            r'<(?:p|div)[^>]*\bclass="[^"]*\bfaq-a\b[^"]*"[^>]*>(.*?)</(?:p|div)>',
            html, re.S):
        if not q or not a:
            continue
        # Schema wants prose, not markup: <b> and friends are presentation, and
        # the entities (&mdash;, &middot;) have to be real characters or they
        # end up double-escaped inside the JSON string.
        text = unescape(re.sub(r"<[^>]+>", "", a)).strip()
        items.append({"@type": "Question",
                      "name": unescape(re.sub(r"<[^>]+>", "", q)).strip(),
                      "acceptedAnswer": {"@type": "Answer", "text": text}})
    if not items:
        return ""
    blob = json.dumps({"@context": "https://schema.org",
                       "@type": "FAQPage", "mainEntity": items})
    return '<script type="application/ld+json">' + blob + "</script>"


def _org_schema() -> str:
    """Organization and WebSite as first-class entities.

    WHY, WHEN SoftwareApplication ALREADY NAMES THE PUBLISHER. A nested
    `publisher` describes who made the product; it does not declare that the
    company itself is a thing with an identity. The top-level Organization is
    what a knowledge graph attaches to — and what a language model reads when
    asked who is behind Highlightz. @id ties the two together so this reads as
    one entity rather than two companies with the same name.

    sameAs IS EMPTY ON PURPOSE. It is the list of profiles that PROVE the same
    organisation — X, Discord, GitHub, LinkedIn. Filling it with guesses would
    assert ownership of accounts we have not confirmed, and a wrong sameAs is
    worse than none. Add the real handles to ORG_PROFILES and this picks them
    up; that is the single change that most helps a brand panel appear.
    """
    org = {"@type": "Organization",
           "@id": SITE_ORIGIN + "/#organization",
           "name": "ANTI Technology LLC",
           "alternateName": "Highlightz",
           "url": SITE_ORIGIN + "/",
           "logo": {"@type": "ImageObject",
                    "url": SITE_ORIGIN + "/static/logo-mark.png"},
           "email": "support@highlightz.app",
           "foundingDate": "2026",
           "address": {"@type": "PostalAddress",
                       "addressRegion": "NJ", "addressCountry": "US"},
           "contactPoint": {"@type": "ContactPoint",
                            "contactType": "customer support",
                            "email": "support@highlightz.app",
                            "availableLanguage": "English"}}
    if ORG_PROFILES:
        org["sameAs"] = list(ORG_PROFILES)
    site = {"@type": "WebSite",
            "@id": SITE_ORIGIN + "/#website",
            "url": SITE_ORIGIN + "/",
            "name": "Highlightz",
            "description": "Automatic Twitch clipping across every channel you "
                           "watch, using a transparent scoring formula.",
            "inLanguage": "en",
            "publisher": {"@id": SITE_ORIGIN + "/#organization"}}
    blob = json.dumps({"@context": "https://schema.org",
                       "@graph": [org, site]})
    return '<script type="application/ld+json">' + blob + "</script>"


# The FAQ schema is NOT built here. It reads the FAQ's own markup, and one of
# those answers is still a <!--FREEPLAN--> placeholder at this point in the
# file — so building it here published "How does billing work?" with an empty
# answer, which is the exact failure _faq_schema exists to prevent and is
# invisible in a browser. It is built after every placeholder is filled; see
# the bottom of this module.


# ── The cinematic page's generated parts ─────────────────────────────────────
# Everything below the cover that depends on data is built here, not typed
# into the markup: the frames come from the curated showcase, the numbers from
# the same counters the cover reads, the categories from the engine's own
# signal titles, the plans from plans.py. Typed copies are how this page once
# advertised a tier that did not exist.

# The product screens that stand in wherever a clip frame is wanted and the
# showcase has none to give. Real screens of the dashboard, cropped from the
# tutorial captures (static/landing/tour-*.webp).
_SCREENS = (
    ("tour-live.webp",
     "The Live Streams screen: a channel's trigger score climbing toward its threshold"),
    ("tour-review.webp",
     "The Clip Review screen: pending clips with their scores and Approve and Reject buttons"),
    ("tour-library.webp",
     "The Clip Library screen: approved clips, hosted by Twitch"),
    ("tour-vod.webp",
     "The VOD Scanner screen: a Twitch VOD link pasted in, ready to scan"),
)

# What each signal catches, in the words the engine puts on the clip. The
# titles are read from the engine so the rail cannot drift from the product;
# the one-line descriptions are the landing page's own.
_CATCH_LINES = {
    "CHAT_VELOCITY":     "Chat goes from talking to flooding, measured against this channel's own pace.",
    "AUDIO_SPIKE":       "The stream gets loud against its own recent level.",
    "KEYWORD":           "The words you chose, or the preset's, landing in chat.",
    "VIEWER_SPIKE":      "The count jumps against what is normal here.",
    "SENTIMENT":         "Chat's mood lurches, either way.",
    "SILENCE_BURST":     "The quiet before it lands, then the eruption.",
    "EMOTE_HOMOGENEITY": "The wall of one emote.",
}

# The size suffix Twitch puts on a clip thumbnail, in BOTH of its layouts:
# the old `…-preview-480x272.jpg` and the newer
# `…/landscape/thumb/thumb-0000000000-480x272.jpg`. Only the trailing
# WIDTHxHEIGHT before the extension is swapped; nothing else in the path is
# touched. Probed on prod 2026-09-02: the newer layout serves 1920x1080,
# 1280x720, 1080x608, 960x540 and 640x360 for the same clip.
_RE_PREVIEW = re.compile(r"-\d+x\d+(?=\.[A-Za-z]+$)")


def _signal_titles() -> dict[str, str]:
    """{SIGNAL_KEY: title} from the engine's own title table."""
    from src.trigger import engine as _eng
    for obj in vars(_eng).values():
        table = getattr(obj, "_SIGNAL_TITLES", None)
        if isinstance(table, dict) and table:
            return {str(k).split(".")[-1].upper(): v for k, v in table.items()}
    return {}


def _catches() -> list[tuple[str, str, str]]:
    """(key, title, line) for every signal the engine can name a clip after."""
    titles = _signal_titles()
    return [(k, titles[k], _CATCH_LINES.get(k, "")) for k in _CATCH_LINES if k in titles]


def _top_signal(clip: dict) -> str:
    """The signal that led a stored clip, as a SIGNAL_KEY, or ""."""
    best, best_v = "", -1.0
    for s in clip.get("trigger_signals") or []:
        if isinstance(s, dict):
            t, v = s.get("type") or s.get("signal") or "", float(s.get("value") or 0)
        else:
            t, v = str(s), 0.0
        key = str(t).split(".")[-1].upper()
        if key and v >= best_v:
            best, best_v = key, v
    return best


# The preview sizes to ask Twitch for, sharpest first. Twitch stores a clip's
# thumbnail under a size suffix, and the sizes it keeps vary by clip and by
# age; none of the large ones is guaranteed. So every frame carries the whole
# ladder and steps down ONE rung per miss (onerror), ending on the stored URL,
# which is the one size known to exist. A URL with no size suffix at all has
# no ladder and is used as stored.
_PREVIEW_SIZES = ("1920x1080", "1280x720")


def _hi(url: str) -> str:
    """The sharpest preview to try first: 1920x1080 when the URL carries a
    size suffix, the URL itself otherwise."""
    return _RE_PREVIEW.sub("-" + _PREVIEW_SIZES[0], url or "")


def _preview_ladder(url: str) -> list[str]:
    """Every candidate for a clip's frame, sharpest first, stored URL last."""
    if not url:
        return []
    if not _RE_PREVIEW.search(url):
        return [url]
    out = [_RE_PREVIEW.sub("-" + size, url) for size in _PREVIEW_SIZES]
    if url not in out:
        out.append(url)
    return out


def _frame_tag(entry: dict, cls: str, alt: str, eager: bool = False) -> str:
    ladder = _preview_ladder(entry.get("thumbnail_url") or "")
    first, rest = (ladder[0] if ladder else ""), ladder[1:]
    load = 'fetchpriority="high"' if eager else 'loading="lazy"'
    # The step-down is one statement of inline JS with single quotes only —
    # no backslashes, because this string never passes through the landing
    # page's triple-quoted block, but the rule is kept the same everywhere.
    step = ("var n=(this.dataset.next||'').split('|').filter(Boolean);"
            "if(n.length){this.src=n.shift();this.dataset.next=n.join('|')}else{this.onerror=null}")
    return ('<img class="' + cls + '" src="' + html_escape(first, quote=True)
            + '" data-next="' + html_escape("|".join(rest), quote=True)
            + '" onerror="' + step + '"'
            + ' alt="' + html_escape(alt, quote=True) + '" width="1920" height="1080" decoding="async" '
            + load + ">")


def _screen_tag(i: int, cls: str, eager: bool = False) -> str:
    f, alt = _SCREENS[i % len(_SCREENS)]
    load = 'fetchpriority="high"' if eager else 'loading="lazy"'
    return ('<img class="' + cls + ' ui" src="/static/landing/' + f + '" alt="' + html_escape(alt, quote=True)
            + '" width="1200" height="750" decoding="async" ' + load + ">")


def _frames(where: str = "hero") -> list[dict]:
    """Curated clips with a preview, for one placement. `!= False`, not a
    truthy check: entries saved before placement existed carry neither key
    and must not blank the page."""
    return [e for e in _load_showcase()
            if e.get("thumbnail_url") and e.get(where) is not False]


def _frame_alt(e: dict, what: str) -> str:
    ch = e.get("channel") or "a streamer"
    return what + " from " + ch + "'s stream, clipped by Highlightz"


def _rail_html() -> str:
    out = ['<button type="button" class="rail-t is-on" data-cat="all" aria-current="true">All</button>']
    for key, title, _line in _catches():
        out.append('<button type="button" class="rail-t" data-cat="' + key + '">'
                   + html_escape(title) + "</button>")
    return "".join(out)


def _shelf_html(frames: list[dict]) -> str:
    """One card per curated clip. A clip's category is the signal that led it
    when the entry carries one; entries curated before that field existed are
    spread across the categories so every rail tab has something to show."""
    cats = _catches()
    if not cats:
        return ""
    keys = [c[0] for c in cats]
    titles = {c[0]: c[1] for c in cats}
    lines = {c[0]: c[2] for c in cats}
    if not frames:
        # Nothing curated yet: the seven categories themselves, on the
        # product's own screens, so the shelf still says what it catches.
        out = []
        for i, (key, title, line) in enumerate(cats):
            out.append('<div class="card" data-cat="' + key + '">' + _screen_tag(i, "")
                       + '<div class="card-t"><span class="card-k">Signal ' + str(i + 1).zfill(2)
                       + "</span><h3>" + html_escape(title) + "</h3><p>" + html_escape(line) + "</p></div></div>")
        return "".join(out)
    out = []
    for i, e in enumerate(frames):
        key = e.get("signal") if e.get("signal") in titles else keys[i % len(keys)]
        title = e.get("clip_title") or "Clip"
        meta = html_escape(e.get("channel") or "")
        if e.get("game"):
            meta += " &middot; " + html_escape(e["game"])
        if e.get("score"):
            meta += ' &middot; <b>' + str(int(e["score"])) + "</b> score"
        out.append('<a class="card" data-cat="' + key + '" href="' + html_escape(e.get("twitch_url") or "#", quote=True)
                   + '" data-embed="' + html_escape(e.get("embed_url") or "", quote=True)
                   + '" data-title="' + html_escape(title, quote=True) + '" target="_blank" rel="noopener">'
                   + _frame_tag(e, "", _frame_alt(e, html_escape(title)))
                   + '<div class="card-t"><span class="card-k">' + html_escape(titles[key]) + "</span><h3>"
                   + html_escape(title) + "</h3><p>" + meta + "</p></div></a>")
    return "".join(out)


def _bignums_html(total: int, kept: int | None, streamers: int) -> str:
    from src.billing.plans import PLAN_LIMITS
    figs: list[tuple[str, str, str]] = []   # (number markup, caption, attrs)
    if total > 0:
        figs.append((f"{total:,}", "clips captured and counting",
                     ' id="bn-clips" data-count="' + str(total) + '"'))
    if kept is not None:
        figs.append((str(kept) + "<i>%</i>", "of reviewed clips kept",
                     ' data-count="' + str(kept) + '" data-suffix="%"'))
    figs.append((str(PLAN_LIMITS["pro"]["max_streams"]), "channels watched at once on Pro",
                 ' data-count="' + str(PLAN_LIMITS["pro"]["max_streams"]) + '"'))
    if len(figs) < 3:
        figs.append(("7", "live signals in every score", ' data-count="7"'))
    figs = figs[:3]
    if streamers >= 50:
        lead = ("Join the " + f"{streamers:,}" + " streamers who stopped scrubbing "
                "eight-hour VODs for thirty seconds of gold.")
    else:
        lead = "Join the streamers who stopped scrubbing eight-hour VODs for thirty seconds of gold."
    cells = "".join('<div class="bign"><div class="bign-n"' + attrs + ">" + n
                    + '</div><div class="bign-k">' + cap + "</div></div>"
                    for n, cap, attrs in figs)
    return '<p class="num-lead">' + lead + '</p><div class="bignums">' + cells + "</div>"


# ── Pricing, built from plans.py ─────────────────────────────────────────────
# Two tall columns, Starter and Pro, real limits and real prices. No badge, no
# highlighted column. Free is the way in and is stated first, above them.
def _pricing() -> str:
    from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
    free, st, pro = PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]

    def week(limits: dict) -> str:
        w = limits.get("max_library_week", 0)
        return "Unlimited" if w >= UNLIMITED_PENDING else str(w)

    def chans(n: int) -> str:
        return str(n) + (" channel" if n == 1 else " channels")

    def plan(limits: dict, cta: str, suffix: str = "per month") -> str:
        facts = [
            ("Channels at once", chans(limits["max_streams"])),
            ("Clips held for review", str(limits["max_pending"])),
            ("Highlight clips", str(limits["max_suggested"])),
            ("Clips kept per week", week(limits)),
            ("VOD Scanner", "Yes" if limits["vod"] else "No"),
        ]
        return ('<div class="plan"><h3 class="plan-name">' + limits["label"] + "</h3>"
                + '<p class="plan-price">$' + str(limits["price"]) + "<i>" + suffix + "</i></p>"
                + '<ul class="plan-facts">'
                + "".join("<li><span>" + k + "</span><b>" + v + "</b></li>" for k, v in facts)
                + "</ul>"
                + '<a href="/login" class="btn btn-dark btn-lg">' + cta + "</a></div>")

    # THREE COLUMNS, Free first (owner's call: the free tier is displayed, not
    # described). Same construction for all three; the plans differ on the
    # rows and nowhere else, and Free's price says why it is zero.
    return (
        '<p class="price-lead"><b>Start free and stay free.</b> There is no card to '
        "enter and no time limit on it. Upgrade when you want. The plans differ on "
        "the rows below and nowhere else.</p>"
        '<div class="plans">' + plan(free, "Start free", "no card") + plan(st, "Get Starter")
        + plan(pro, "Get Pro") + "</div>"
        '<p class="price-tiny">Move between them whenever you like. Cancel from the Account tab. '
        "No contracts. Streamers can opt out at any time, and it applies everywhere at once.</p>")


def _tos_plans() -> str:
    """Section 4 of the Terms, generated from PLAN_LIMITS.

    The Terms said "the Service requires an active paid subscription" for two
    months after the free tier reopened, because the sentence was typed and
    nothing connected it to the plans. Deriving it means the document cannot
    describe a product we do not sell.
    """
    from src.billing.plans import PLAN_LIMITS
    f, st, pro = PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]

    def chans(n: int) -> str:
        return str(n) + (" channel" if n == 1 else " channels")

    return (
        '<p>The Service offers a free plan and two paid plans. The free plan does '
        "not expire and never requires a payment method: it monitors "
        + chans(f["max_streams"]) + " at a time, holds " + str(f["max_pending"])
        + " clips in your review queue, adds up to " + str(f["max_suggested"])
        + " suggested clips on top of those, and lets you keep "
        + str(f["max_library_week"]) + " clips a week in your library. Starter "
        "is $" + str(st["price"]) + "/month for " + chans(st["max_streams"])
        + ", a " + str(st["max_pending"]) + "-clip queue and "
        + str(st["max_library_week"]) + " clips a week. Pro is $"
        + str(pro["price"]) + "/month for " + chans(pro["max_streams"]) + ", a "
        + str(pro["max_pending"]) + "-clip queue, no weekly limit on what you "
        "keep, and the VOD Scanner. Current plan details and "
        "prices are shown on our pricing page and in your Account tab.</p>"
        "<p>Where a plan limits how many clips you may keep in a period, "
        "reaching that limit pauses new approvals until the period rolls over. "
        "Clips already in your library are never removed because of it.</p>")


def _faq() -> str:
    """The FAQ, with every number read from PLAN_LIMITS so the answers cannot
    drift from the plans. Highlight clips get their own group: they are the
    one thing on the page a visitor has no way of knowing about, and the
    pricing column names them without saying what they are."""
    from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
    free, st, pro = PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]

    def week(limits: dict) -> str:
        w = limits.get("max_library_week", 0)
        return "unlimited" if w >= UNLIMITED_PENDING else str(w)

    def item(q: str, a: str) -> str:
        return ('      <details class="faq-item">\n'
                '        <summary class="faq-q">' + q + '</summary>\n'
                '        <p class="faq-a">' + a + '</p>\n'
                '      </details>\n')

    def group(title: str, items: list[tuple[str, str]]) -> str:
        return ('    <div class="faq-group">\n'
                '      <h3 class="faq-h">' + title + '</h3>\n'
                + "".join(item(q, a) for q, a in items)
                + '    </div>\n')

    using = [
        ("What does Highlightz actually do?",
         "It watches a live Twitch channel for you. Every second it blends seven live signals "
         "&mdash; chat speed, keywords, emotes, sentiment, audio, viewer movement and silence "
         "&mdash; into one score and checks it against that channel's own threshold. When the "
         "score crosses, it asks Twitch to make a real Twitch clip of that moment through the "
         "official Clips API, and the clip lands in your review queue. Nothing is recorded, "
         "downloaded or re-hosted."),
        ("Can I clip channels I don't own?",
         "Yes. That is what most people use it for. Add any live Twitch channel and the clip is "
         "created with your authorized account, exactly as if you had pressed Twitch's own Clip "
         "button while watching. Twitch hosts it and it is attributed to you, same as a manual clip."),
        ("Do I have to leave anything running?",
         "No. The watching happens on our servers, not in your browser. Add a channel, close the "
         "tab, shut the laptop. If the channel is not live yet it is rechecked every 30 seconds "
         "until it is. Monitoring stops after 8 hours without you opening the dashboard, so a "
         "forgotten tab does not run forever."),
        ("How many channels can it watch at once?",
         f"<b>{free['max_streams']}</b> on Free, <b>{st['max_streams']}</b> on Starter, "
         f"<b>{pro['max_streams']}</b> on Pro. At the same time, not in rotation, and each one "
         "carries its own profile so a busy channel and a quiet one do not interfere with each other."),
        ("Does it work for small channels?",
         "Yes, and this is the whole point of scoring each channel against itself. A five-viewer "
         "chat and a fifty-thousand-viewer chat are judged the same way, because the formula learns "
         "what is normal for each channel and reacts to relative spikes rather than raw numbers."),
        ("Is this AI?",
         "No. It runs on a transparent mathematical formula you can read. Watch the score move in "
         "real time on the Live Streams screen, then open any clip to see which signals fired and "
         "how strongly."),
        ("What if I don't like the clips it takes?",
         "Every clip lands in your review queue first. Approve the keepers, reject the misses. The "
         "formula learns from each decision: rejections raise that channel's bar, approvals lower "
         "it, so it steadily tunes toward your taste. There is also a preset per channel (FPS, MOBA, "
         "Just Chatting, IRL, Chess and more) that decides where it starts, and a sensitivity dial "
         "for a stream running hotter or quieter than usual."),
        ("What happens when my review queue fills up?",
         f"<b>{free['max_pending']}</b> clips can sit waiting on Free, <b>{st['max_pending']}</b> on "
         f"Starter, <b>{pro['max_pending']}</b> on Pro. When the queue is full the newest arrival is "
         "dropped, and nothing you have already caught is ever deleted to make room for it. The "
         "dashboard tells you how many moments were missed that way, so a full queue is something "
         "you find out about rather than something that happens silently."),
    ]
    highlights = [
        # HOW they are found is deliberately not said anywhere on the public
        # site (owner: "this is our secret sauce"). Quality and the labels
        # only. Do not add mechanism here, in the tutorial, or in a card title.
        ("What are Highlight clips?",
         "The clips to look at first. Alongside the score, Highlightz has a second way of finding "
         "moments, and the clips it finds arrive in your review queue marked <b>Highlight</b>, in "
         "purple. They are usually the higher-quality clips: the ones most likely to travel. When "
         "a Highlight clip also carries a <b>green label</b>, it stood out even more, and those are "
         "the best clips you will get. Each one is already a real Twitch clip, hosted by Twitch."),
        ("How are Highlight clips different from the clips the formula makes?",
         "The formula's clips are made by Highlightz when the score crosses the channel's line, and "
         "the card shows which signals fired. A Highlight clip is found a different way, one we keep "
         "to ourselves, so it carries no trigger score and its card says why it is there instead. "
         "Rejecting one does not move the channel's threshold, because the formula never claimed it. "
         "And they can never take more than half of the review queue, so a Highlight clip is never "
         "the reason a clip the formula caught did not land."),
        ("How many Highlight clips do I get?",
         f"They have their own budget on top of the review queue: <b>{free['max_suggested']}</b> on "
         f"Free, <b>{st['max_suggested']}</b> on Starter, <b>{pro['max_suggested']}</b> on Pro. "
         "Approve one and it is kept like any other clip; the Clip Library files them under their "
         "own Highlights row."),
    ]
    fine = [
        ("What does “clips kept per week” mean?",
         f"How many clips you can approve into your library in a week: <b>{week(free)}</b> on Free, "
         f"<b>{week(st)}</b> on Starter, <b>{week(pro)}</b> on Pro. Reaching the number pauses new "
         "approvals until the week rolls over. Nothing already in your library is ever removed "
         "because of it."),
        ("What is the VOD Scanner?",
         "A Pro feature. It runs the same scoring over a stream that has already ended, so a back "
         "catalogue nobody was watching live is still worth mining, and every hit links to its own "
         "timestamp in the VOD."),
        ("Is this allowed on Twitch?",
         "Yes. Clips are created through Twitch's official Clips API with your authorized account, "
         "the same mechanism as Twitch's own Clip button. Nothing here works around a rate limit or "
         "scrapes a page, and there is no second copy of anyone's video anywhere."),
        ("What if a streamer does not want to be clipped?",
         "They can opt out at any time on our <a href=\"/opt-out\">opt-out page</a>, and it takes "
         "effect immediately across every account, with nothing to email and nobody to wait on. A "
         "channel that has opted out cannot be added by anyone."),
        ("Do you record or store my stream?",
         "Never. When a moment hits, Highlightz asks Twitch to create a real Twitch clip through the "
         "official API. Twitch hosts it and it is attributed to your account. We keep the clip's "
         "title, score and link, and nothing else."),
        ("How does billing work?",
         f"Free is free: it asks for no card and it never expires. Starter is <b>${st['price']}</b> a month and Pro is "
         f"<b>${pro['price']}</b> a month. Move between them whenever you like and cancel from the "
         "Account tab. Cancelling puts you back on Free, and every clip you approved stays in your "
         "library."),
    ]
    return (group("Using it", using)
            + group("Highlight clips", highlights)
            + group("Plans and the fine print", fine))


LANDING_HTML = LANDING_HTML.replace("<!--PRICING-->", _pricing(), 1)
LANDING_HTML = LANDING_HTML.replace("<!--FAQ-->", _faq(), 1)
LANDING_HTML = LANDING_HTML.replace("<!--RAIL-->", _rail_html(), 1)

# LAST, and that is the whole point. _faq_schema derives the FAQPage from the
# FAQ's own markup so the two cannot disagree — but it can only read what is
# already there. Built before the line above, the billing answer was still the
# <!--FREEPLAN--> placeholder, tags got stripped, and the page published a
# FAQPage whose "How does billing work?" answer was the empty string. Nothing
# in a browser shows that. Any future placeholder that lands inside a .faq-a
# has to be filled before this line for the same reason.
LANDING_HTML = LANDING_HTML.replace(
    "<!--FAQ_SCHEMA-->", _org_schema() + _faq_schema(LANDING_HTML), 1)

# The price note under the Twitch button used to read "Signing in is free. Paid
# plans are optional and start at $10/month" — two lines below a badge saying a
# card is required, so the page contradicted itself on the last screen before
# signup. Making an account is still free; using the product is not optional any
# more, so the note states the price after the trial instead.
#
# In PYTHON, not an HTML comment: the first version of this note was an HTML
# comment quoting the old sentence, which meant the retired claim was still
# being served to every visitor and every crawler that reads markup.
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
  body{background:#0e0b11;color:#f2eaf7;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:24px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(184,106,220,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  /* width was a flat 360px, which hangs off a 320px screen — and this is the
     sign-in card, so the overflow lands on the one button the page exists
     for. max-width keeps the same size everywhere it fits. */
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:48px 32px;width:100%;max-width:360px;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:24px}
  .logo-wrap img{height:54px;width:auto;filter:drop-shadow(0 0 18px rgba(196,137,228,.4))}
  h1{font-size:24px;font-weight:800;color:#c489e4;margin-bottom:4px;letter-spacing:-.02em}
  .sub{font-size:12px;color:#b9aec4;margin-bottom:16px}
  .price-pill{display:inline-flex;align-items:center;gap:8px;background:rgba(145,70,255,.14);border:1px solid rgba(145,70,255,.35);color:#c489e4;font-size:12px;font-weight:700;padding:8px 12px;border-radius:99px;margin-bottom:24px}
  .price-pill .dot{width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e}
  .price-note{font-size:12px;color:#9c90a6;text-align:center;margin-top:12px}
  .twitch-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;background:#9146ff;color:#fff;border:none;border-radius:12px;padding:12px;font-size:14px;font-weight:700;cursor:pointer;text-decoration:none;transition:background var(--dur-fast)}
  .twitch-btn:hover{background:#772ce8}
  .twitch-btn svg{flex-shrink:0}
  .or-divider{display:flex;align-items:center;gap:12px;margin:12px 0;color:#9c90a6;font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
  .or-divider::before,.or-divider::after{content:'';flex:1;height:1px;background:rgba(255,255,255,.08)}
  .divider{display:flex;align-items:center;gap:12px;margin:16px 0;color:#9c90a6;font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
  .divider::before,.divider::after{content:'';flex:1;height:1px;background:rgba(255,255,255,.08)}
  label{font-size:12px;color:#b9aec4;display:block;margin-bottom:4px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
  input{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:10px;color:#f2eaf7;padding:12px 12px;font-size:14px;outline:none;margin-bottom:12px;transition:var(--dur-fast)}
  input:focus{border-color:rgba(196,137,228,.5);box-shadow:0 0 0 4px rgba(184,106,220,.1)}
  .pw-btn{width:100%;background:rgba(255,255,255,.06);color:#f2eaf7;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px;font-size:12px;font-weight:600;cursor:pointer;transition:var(--dur-fast)}
  .pw-btn:hover{background:rgba(255,255,255,.1)}
  .error{color:#ff5a78;font-size:12px;margin-bottom:12px;background:rgba(255,90,120,.12);padding:8px 12px;border-radius:10px;border:1px solid rgba(255,90,120,.25)}
  .admin-toggle{font-size:12px;color:#9c90a6;text-align:center;margin-top:16px;cursor:pointer;text-decoration:underline}
  #admin-form{display:none;margin-top:16px}
  .footer{margin-top:24px;text-align:center;font-size:12px;color:#9c90a6;line-height:1.7}
  .footer a{color:#9c90a6;text-decoration:none}.footer a:hover{color:#b9aec4}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo-mark.png" alt="Highlightz logo"></div>
  <h1>Highlightz</h1>
  <p class="sub">Sign in to start clipping highlights</p>
  <div class="price-pill"><span class="dot"></span>Free to start &mdash; no card needed</div>
  {error}
  <a href="/auth/twitch" class="twitch-btn">
    <svg width="20" height="20" viewBox="0 0 2400 2800" fill="#fff"><path d="M500 0L0 500v1800h600v500l500-500h400l900-900V0H500zm1700 1300l-400 400h-400l-350 350v-350H600V200h1600v1100z"/><path d="M1700 550h-200v600h200V550zm-550 0h-200v600h200V550z"/></svg>
    Continue with Twitch
  </a>
  <p class="price-note">The free plan has no time limit. Want more channels? Plans start at $10/month, cancel any time from your account.</p>
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
  body{background:#0e0b11;color:#f2eaf7;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:16px}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 20% -10%,rgba(184,106,220,.22),transparent 60%),radial-gradient(600px 350px at 85% 8%,rgba(249,67,255,.14),transparent 55%)}
  .card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:48px 48px;max-width:560px;width:100%;text-align:center;-webkit-backdrop-filter:blur(22px);backdrop-filter:blur(22px)}
  .plan-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:8px;text-align:left}
  .plan{position:relative;border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:16px 16px;display:flex;flex-direction:column}
  .plan.pro{border-color:rgba(184,106,220,.55);box-shadow:0 0 30px -12px rgba(184,106,220,.5)}
  .plan-pop{position:absolute;top:-9px;left:50%;transform:translateX(-50%);font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#fff;background:linear-gradient(135deg,#f943ff,#b86adc);padding:4px 8px;border-radius:99px;white-space:nowrap}
  .plan-name{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:#c489e4}
  .plan-price{font-size:30px;font-weight:800;margin:4px 0 8px}
  .plan-price span{font-size:12px;color:#b9aec4;font-weight:600}
  .plan-feats{list-style:none;margin:0 0 16px;padding:0;flex:1}
  .plan-feats li{font-size:12px;color:#b9aec4;padding:4px 0 4px 16px;position:relative}
  .plan-feats li::before{content:'✓';position:absolute;left:0;color:#34d399;font-weight:800;font-size:12px}
  .plan .cta{margin-bottom:0;padding:12px;font-size:14px}
  .cta.ghost{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);box-shadow:none}
  .cta.ghost:hover{background:rgba(255,255,255,.12);filter:none}
  @media(max-width:520px){.plan-row{grid-template-columns:1fr}}
  .logo-wrap{display:flex;justify-content:center;margin-bottom:16px}
  .logo-wrap img{height:46px;filter:drop-shadow(0 0 14px rgba(196,137,228,.4))}
  .badge{display:inline-flex;align-items:center;gap:4px;background:rgba(196,137,228,.12);border:1px solid rgba(196,137,228,.25);color:#c489e4;font-size:12px;font-weight:700;padding:4px 12px;border-radius:99px;letter-spacing:.06em;text-transform:uppercase;margin-bottom:24px}
  h1{font-size:30px;font-weight:800;letter-spacing:-.025em;margin-bottom:8px}
  .sub{font-size:14px;color:#b9aec4;margin-bottom:32px;line-height:1.6}
  .features{text-align:left;margin-bottom:32px;display:flex;flex-direction:column;gap:8px}
  .feat{display:flex;align-items:center;gap:12px;font-size:14px}
  .feat .ic{width:24px;height:24px;border-radius:8px;background:rgba(196,137,228,.12);color:#c489e4;display:grid;place-items:center;flex-shrink:0;font-size:12px}
  .cta{display:block;width:100%;background:linear-gradient(135deg,#f943ff 0%,#b86adc 52%,#7c6bff 100%);color:#fff;border:none;border-radius:13px;padding:12px;font-size:14px;font-weight:700;cursor:pointer;text-decoration:none;transition:filter var(--dur-fast);box-shadow:0 6px 24px -6px rgba(184,106,220,.6);margin-bottom:12px}
  .cta:hover{filter:brightness(1.08)}
  .manage{display:block;font-size:12px;color:#9c90a6;text-align:center;margin-top:4px;text-decoration:none}
  .manage:hover{color:#b9aec4}
  .logout{display:block;font-size:12px;color:#9c90a6;text-align:center;margin-top:16px;text-decoration:none}
  .logout:hover{color:#b9aec4}
  .footer{margin-top:24px;text-align:center;font-size:12px;color:#9c90a6;line-height:1.7}
  .footer a{color:#9c90a6;text-decoration:none}.footer a:hover{color:#b9aec4}
@media(max-width:480px){
  .card{padding:32px 24px;border-radius:18px}
  h1{font-size:24px}
}

  /* The one page in the product with no reduced-motion rule at all. It has a
     single transition, which is not much to suppress — but "we honour this
     everywhere except the page that asks you for money" is not a position
     worth holding, and the next transition added here inherits the guard. */
  @media (prefers-reduced-motion: reduce){
    *,*::before,*::after{
      animation-duration:.01ms !important;animation-iteration-count:1 !important;
      transition-duration:.01ms !important;scroll-behavior:auto !important}
  }
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap"><img src="/static/logo-mark.png" alt="Highlightz"></div>
  <span class="badge">Highlightz</span>
  <h1>{headline}</h1>
  <p class="sub">Hi {username} — {subline}</p>
  <!--PAYWALLPLANS-->
  <p class="sub" style="font-size:12px;margin-top:14px">{cta_note}</p>
  <a href="/billing/portal" class="manage">Already subscribed? Manage billing</a>
  <a href="#" class="logout" onclick="fetch('/logout',{method:'POST'}).then(()=>{location.href='/login';});return false;">Sign out</a>
</div>
<div class="footer">
  &copy; 2026 ANTI Technology LLC &mdash; All rights reserved.<br>
  <a href="/tos">Terms of Service</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="/cookies">Cookie Policy</a>
</div>
</body>
</html>"""

def _paywall_plans() -> str:
    """The two paid cards on the upgrade page, generated from PLAN_LIMITS.

    Every figure here was typed. They happened to be correct, but this is the
    page where somebody decides to pay — the worst place to quote a stale
    number — and it had already fallen behind in a way that mattered: the
    weekly keep limit is now one of the main reasons to upgrade and the page
    did not mention it at all.
    """
    from src.billing.plans import PLAN_LIMITS, UNLIMITED_PENDING
    st, pro = PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]

    def keeps(p: dict) -> str:
        n = p["max_library_week"]
        return ("Unlimited clips kept" if n >= UNLIMITED_PENDING
                else str(n) + " clips kept a week")

    def card(p: dict, key: str, cls: str, cta_cls: str, extra: list[str],
             popular: bool) -> str:
        feats = ([str(p["max_streams"]) + " monitored streams",
                  str(p["max_pending"]) + "-clip review queue",
                  keeps(p)] + extra)
        return ('<div class="plan ' + cls + '">'
                + ('<div class="plan-pop">Most popular</div>' if popular else "")
                + '<div class="plan-name">' + p["label"] + "</div>"
                + '<div class="plan-price">$' + str(p["price"]) + "<span>/mo</span></div>"
                + '<ul class="plan-feats">'
                + "".join("<li>" + f + "</li>" for f in feats)
                + "</ul>"
                + '<a href="/billing/checkout?plan=' + key + '" class="cta '
                + cta_cls + '">Choose ' + p["label"] + "</a></div>")

    return ('<div class="plan-row">'
            + card(st, "starter", "", "ghost",
                   ["Live clip detection &amp; analytics"], False)
            + card(pro, "pro", "pro", "",
                   ["VOD Scanner included"], True)
            + "</div>")


PAYWALL_HTML = PAYWALL_HTML.replace("<!--PAYWALLPLANS-->", _paywall_plans(), 1)



# ── The legal pages' system (2026-09-02): the landing page's bar and footer,
#    the paper ground, the display voice for titles, hairline rules between
#    sections. One stylesheet, one bar, one footer, shared by /tos, /privacy
#    and /cookies so the three documents cannot drift apart visually either.
_LEGAL_STYLE = """
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  @font-face{font-family:'Sora Fallback';font-style:normal;font-weight:100 900;
    src:local('Arial'),local('Helvetica'),local('Liberation Sans');
    size-adjust:114.4%;ascent-override:84.8%;descent-override:25.3%;line-gap-override:0%}
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}
  :root{
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6; --hair:rgba(242,234,247,.085);
    --paper:#F4F4F2; --paper-ink:#0A0A0C; --paper-ink-2:#4B4A50; --paper-ink-3:#77767C;
    --paper-hair:rgba(10,10,12,.14); --white:#FFFFFF; --ember:#F7A745;
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora','Sora Fallback',system-ui,sans-serif;
    --dur-fast:150ms;
    --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:32px; --s-7:48px; --s-8:64px; --s-9:96px;
    --nav-h:71px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth;overflow-x:clip;scroll-padding-top:96px}
  body{background:var(--paper);color:var(--paper-ink-2);font-family:var(--sans);font-size:16px;line-height:1.6;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
  a{color:inherit;text-decoration:none}
  ::selection{background:rgba(247,167,69,.35);color:#fff}
  :focus-visible{outline:2px solid var(--ember);outline-offset:3px;border-radius:2px}
  .nav{position:fixed;top:0;left:0;right:0;z-index:60;
    background:linear-gradient(180deg,#09070C 0%,#09070C 70%,rgba(9,7,12,0) 100%);
    display:flex;align-items:center;gap:16px;padding:12px 24px 16px}
  .nav-logo{display:flex;align-items:center;gap:8px;flex-shrink:0}
  .nav-logo img{height:22px}
  .nav-logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink)}
  .nav-links{display:flex;align-items:center;gap:4px;margin-left:12px}
  .nav-link{font-family:var(--mono);font-weight:400;font-size:12px;letter-spacing:.02em;color:var(--ink-3);padding:8px 12px;border-radius:3px;
    transition:color var(--dur-fast),background var(--dur-fast)}
  .nav-link:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .nav-right{margin-left:auto;display:flex;align-items:center;gap:8px}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;cursor:pointer;font-family:var(--sans);font-weight:700;
    font-size:14px;padding:8px 16px;border-radius:3px;border:1px solid transparent;white-space:nowrap}
  .btn-go{background:var(--ember);border-color:var(--ember);color:#0A0A0C}
  .btn-go:hover{background:#FFB65A;border-color:#FFB65A}
  .legal{max-width:760px;margin:0 auto;padding:calc(var(--nav-h) + var(--s-8)) clamp(20px,4.5vw,72px) var(--s-9)}
  .legal .k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--ember)}
  .legal h1{font-family:var(--sans);font-weight:800;letter-spacing:-.04em;line-height:.98;font-size:clamp(36px,5.2vw,64px);
    color:var(--paper-ink);margin:var(--s-3) 0 var(--s-4)}
  .legal .meta{font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--paper-ink-3);
    padding-bottom:var(--s-6);border-bottom:1px solid var(--paper-hair);margin-bottom:var(--s-6)}
  .legal h2{font-family:var(--sans);font-weight:800;letter-spacing:-.03em;line-height:1.1;font-size:22px;color:var(--paper-ink);
    margin:var(--s-7) 0 var(--s-3);padding-top:var(--s-5);border-top:1px solid var(--paper-hair)}
  .legal p,.legal li{font-size:16px;line-height:1.6;color:var(--paper-ink-2);margin-bottom:var(--s-3)}
  .legal ul{padding-left:var(--s-5);margin-bottom:var(--s-3)}
  .legal li{margin-bottom:var(--s-2)}
  .legal strong{color:var(--paper-ink);font-weight:700}
  .legal em{color:var(--paper-ink)}
  .legal code{font-family:var(--mono);font-size:14px;color:var(--paper-ink);background:rgba(10,10,12,.06);padding:0 4px;border-radius:3px}
  .legal a{color:var(--paper-ink);border-bottom:1px solid var(--paper-hair)}
  .legal a:hover{border-bottom-color:var(--paper-ink)}
  .legal table{width:100%;border-collapse:collapse;margin-bottom:var(--s-4);font-size:14px}
  .legal th{text-align:left;font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--paper-ink-3);padding:var(--s-2) var(--s-3) var(--s-2) 0;border-bottom:2px solid var(--paper-ink)}
  .legal td{padding:var(--s-3) var(--s-3) var(--s-3) 0;border-bottom:1px solid var(--paper-hair);vertical-align:top;color:var(--paper-ink-2)}
  .legal td code{white-space:nowrap}
  /* The cookie table is four columns with a long Purpose cell; narrower than
     that it scrolls inside itself rather than pushing the page sideways. */
  @media(max-width:560px){ table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch} }
  .footer{background:#000;border-top:1px solid var(--hair);padding:var(--s-5) clamp(20px,4.5vw,72px);display:flex;align-items:center;
    gap:var(--s-5);flex-wrap:wrap;font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--ink-3)}
  .footer img{height:20px;width:auto;display:block}
  .footer nav{display:flex;flex-wrap:wrap;gap:var(--s-2) var(--s-4)}
  .footer a:hover{color:var(--ink)}
  .footer .fl{margin-left:auto;white-space:nowrap}
  @media(max-width:940px){ .nav-links{display:none} }
  @media(max-width:700px){ .nav-logo span{display:none} .footer .fl{margin-left:0} }
  @media(prefers-reduced-motion:reduce){ html{scroll-behavior:auto} }
"""

_LEGAL_NAV = """<nav class="nav">
  <a href="/" class="nav-logo"><img src="/static/logo-mark.png" alt="Highlightz"><span>Highlightz</span></a>
  <div class="nav-links">
    <a href="/#catches" class="nav-link">What it catches</a>
    <a href="/#score" class="nav-link">How it scores</a>
    <a href="/#watch" class="nav-link">Channels</a>
    <a href="/#pricing" class="nav-link">Pricing</a>
    <a href="/#faq" class="nav-link">FAQ</a>
    <a href="/tutorial" class="nav-link">Tutorial</a>
    <a href="/compare" class="nav-link">Compare</a>
  </div>
  <div class="nav-right">
    <a href="/login" class="nav-link">Sign in</a>
    <a href="/login" class="btn btn-go">Get started</a>
  </div>
</nav>"""

_LEGAL_FOOT = """<footer class="footer">
  <img src="/static/logo-mark.png" alt="Highlightz" width="374" height="501">
  <nav aria-label="Site"><a href="/tutorial">Tutorial</a><a href="/compare">Compare</a><a href="/tos">Terms of Service</a><a href="/privacy">Privacy Policy</a><a href="/cookies">Cookie Policy</a><a href="/opt-out">Streamer Opt-Out</a></nav>
  <span class="fl">&copy; 2026 ANTI Technology LLC</span>
</footer>"""

TOS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Terms of Service — Highlightz</title>
<meta name="description" content="The terms that govern your use of Highlightz, including plans, your responsibilities for clips you create, and how broadcasters can opt out.">
<link rel="icon" type="image/png" href="/static/icon.png">
<!--SOCIAL_TOS-->
<style>""" + _LEGAL_STYLE + """</style>
</head>
<body>
""" + _LEGAL_NAV + """
<main class="legal">
  <div class="k">Legal</div>
  <h1>Terms of Service</h1>
  <p class="meta">Effective date: September 2, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>Please read these Terms of Service ("Terms") carefully before using Highlightz ("Service"), operated by ANTI Technology LLC ("we," "us," or "our"). By accessing or using the Service you agree to be bound by these Terms. If you do not agree, do not use the Service.</p>

  <h2>1. Description of Service</h2>
  <p>Highlightz is a SaaS platform that monitors live streams on Twitch, automatically detects highlight moments from public signals such as chat activity and stream audio levels, and — at your direction and on your behalf — creates clips using Twitch's official Clips API. Clips are created, processed, hosted, and stored by Twitch on Twitch's own infrastructure under your Twitch account. Highlightz does not record, copy, download, or re-host stream video. To measure loudness we may read a stream's audio in real time and, when you scan a past broadcast, decode an audio-only rendition of it; that audio is measured and discarded, never written to disk or retained.</p>
  <p>The Service may also place in your review queue clips that were created on Twitch by someone other than you ("Highlight clips"). Highlightz does not create those clips; it points you to clips that already exist on Twitch. See Section 5.</p>
  <p>Support for Kick is not yet available. Kick channels cannot currently be monitored and no Kick account is connected to or required by the Service. If Kick support ships, these Terms and our Privacy Policy will be updated before it does.</p>
  <p>The Service offers a free plan that does not expire and does not require a payment method, alongside paid plans. See Section 4.</p>

  <h2>2. Eligibility</h2>
  <p>You must be at least 18 years old to use the Service. By using the Service you represent and warrant that you meet this requirement and that all information you provide is accurate and complete.</p>

  <h2>3. Accounts and Platform Authorization</h2>
  <p>You sign in by authorizing the Service through your Twitch account via OAuth2. Twitch is the only way to sign in. By connecting your Twitch account you grant the Service permission to create clips on your behalf using Twitch's Clips API (the <code>clips:edit</code> permission), and, if you approve it on the sign-in screen, to read the email address on your Twitch account (the <code>user:read:email</code> permission). Every clip created through the Service is made with <em>your</em> Twitch credentials and is attributed to <em>your</em> Twitch account, exactly as if you had clicked Twitch's own "Clip" button.</p>
  <p>You are responsible for maintaining the confidentiality of your account and for all activity that occurs under it, including all clips created through it. Notify us immediately at the contact address below if you suspect unauthorized use. We reserve the right to terminate accounts that violate these Terms.</p>

  <h2>4. Plans and Subscriptions</h2>
  <!--TOSPLANS-->
  <p>We may change what the free plan includes, or withdraw it, at any time. We may also, at our sole discretion, grant individual accounts free promotional access to a paid plan for a limited period. Promotional access requires no payment method, ends automatically at the end of its stated period without any charge, and does not convert into a paid subscription unless you subscribe yourself.</p>
  <p>Paid subscriptions are billed on a recurring basis through our payment processor, Stripe. By subscribing you authorize us to charge the payment method on file for each billing period until you cancel.</p>
  <ul>
    <li>You may cancel your subscription at any time through the billing portal. Cancellation takes effect at the end of the current billing period.</li>
    <li>We do not issue refunds for partial billing periods or unused time, except where we choose to at our discretion (for example, to reverse a duplicate subscription).</li>
    <li>We reserve the right to change pricing with at least 14 days notice, sent to your registered email address where we hold one and otherwise posted in the dashboard.</li>
    <li>Failed payments may result in suspension or termination of your account.</li>
  </ul>

  <h2>5. Clips, Streamer Content, and Your Responsibility</h2>
  <p><strong>You — not Highlightz — create the clips, and you are solely responsible for them.</strong> When the Service creates a clip, it does so on your behalf and with your authorization through Twitch's official Clips API, using your Twitch account. The resulting clip is owned, hosted, and governed by Twitch. Highlightz acts only as a tool that you direct; it never records, stores, or re-hosts any stream video itself.</p>
  <p><strong>Highlight clips are someone else's clips.</strong> A Highlight clip was created on Twitch by another Twitch user, remains that user's clip under Twitch's terms, and is hosted by Twitch under their account. Approving one keeps a link to it in your library; Highlightz does not copy, re-host, or alter it, and cannot delete it. Everything in this Section about your responsibility for how you use, share, or distribute a clip applies equally to a Highlight clip.</p>
  <p><strong>Broadcasters may opt out.</strong> Any broadcaster can remove their channel from the Service at <a href="/opt-out">highlightz.app/opt-out</a>. Once a channel has opted out, no user can add it for monitoring, any monitoring of it already running is stopped, and the Service will not create clips from it. If you are asked by a broadcaster to stop clipping their channel, stop; the opt-out page exists so that request can be enforced for everyone at once rather than relying on you.</p>
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
  <p>The Service integrates with two third-party platforms: Twitch (authentication and clip creation) and Stripe (payments). Your use of those platforms is governed by their respective terms, including the <a href="https://www.twitch.tv/p/legal/terms-of-service/">Twitch Terms of Service</a>, the <a href="https://legal.twitch.com/legal/developer-agreement/">Twitch Developer Services Agreement</a>, and the <a href="https://stripe.com/legal/ssa">Stripe Services Agreement</a>. We are not responsible for the availability, accuracy, or practices of any third-party service.</p>

  <h2>9. Data and Privacy</h2>
  <p>We collect and process information necessary to operate the Service, including your Twitch account information and access tokens (stored in encrypted form), payment information (processed by Stripe — we do not store card details), clip metadata such as clip links and trigger scores, a short sample of public chat messages captured alongside each clip, and any video you upload to the Clip Editor. We do not store stream video. We do not sell your personal data to third parties. By using the Service you consent to this processing, as further described in our <a href="/privacy">Privacy Policy</a>.</p>

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
  <p>These Terms are governed by the laws of the State of New Jersey and applicable United States federal law, without regard to conflict of law principles. Any disputes shall be resolved in the state or federal courts of competent jurisdiction located in New Jersey, and you consent to the personal jurisdiction of those courts.</p>

  <h2>16. Contact</h2>
  <p>Questions about these Terms? Contact us at:<br>
  <strong>ANTI Technology LLC</strong><br>
  Email: <a href="mailto:support@highlightz.app">support@highlightz.app</a></p>

</main>
""" + _LEGAL_FOOT + """
</body>
</html>"""

# Filled here rather than beside _tos_plans(): TOS_HTML does not exist until
# the literal above closes.
TOS_HTML = TOS_HTML.replace("<!--TOSPLANS-->", _tos_plans(), 1)

PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy — Highlightz</title>
<meta name="description" content="What Highlightz collects, why, who it is shared with, how long it is kept, and how to have it deleted.">
<link rel="icon" type="image/png" href="/static/icon.png">
<!--SOCIAL_PRIVACY-->
<style>""" + _LEGAL_STYLE + """</style>
</head>
<body>
""" + _LEGAL_NAV + """
<main class="legal">
  <div class="k">Legal</div>
  <h1>Privacy Policy</h1>
  <p class="meta">Effective date: September 2, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>This Privacy Policy describes how ANTI Technology LLC ("we," "us," or "our") collects, uses, and shares information when you use Highlightz ("Service"). By using the Service you agree to the practices described here.</p>

  <h2>1. Information We Collect</h2>
  <p>We collect only what is necessary to operate the Service:</p>
  <ul>
    <li><strong>Account information</strong> — your Twitch user ID, login, display name, and avatar URL, obtained when you sign in via Twitch OAuth2; when your account was created and when you last signed in; the referral code, if any, on the link you signed up through, so we know which outreach brought you here; and, if you ever opened the payment page, when you first did, so we can tell where people stop.</li>
    <li><strong>Email address</strong> — the email on your Twitch account, which Twitch provides to us only if you approve the <code>user:read:email</code> permission on the sign-in screen, and the billing email on your Stripe customer record if you subscribe. We use it to contact you about your account and to prevent the same person paying twice for two accounts. We do not sell it, share it, or add you to a mailing list. You can ask us to delete it at any time, and deleting your account deletes it with the rest of your data.</li>
    <li><strong>Twitch access tokens</strong> — the OAuth access and refresh tokens that authorize the Service to create clips on your behalf. These are stored in encrypted form and are never shared.</li>
    <li><strong>Chat samples</strong> — the detector reads public chat in real time to measure how busy it is. It does not retain that stream, with one exception: when a clip is created we keep up to <!--CHATN--> of the chat messages from around that moment, so you can see why the clip was flagged. These are message texts only — we do not store who sent them.</li>
    <li><strong>Uploaded video</strong> — if you upload a video to the Clip Editor, that file is stored on our servers under your account so it can be played back and edited. It is visible only to you, and it is deleted when you delete it or when you delete your account.</li>
    <li><strong>Billing information</strong> — payment processing is handled entirely by Stripe. We store only your Stripe Customer ID and subscription status. We never see or store your card details.</li>
    <li><strong>Clip metadata</strong> — channel names, platform identifiers, timestamps, trigger scores, and the Twitch clip links generated for your account. For a Highlight clip we also store the two numbers used to rank it, an audience-interest count and a view count — counts, not identities; we do not store who anyone was. We record whether the channel is flagged on Twitch as intended for mature audiences, which is Twitch's own label on the channel rather than anything about a person, so the dashboard knows to send you to Twitch to watch it. We do not store any stream video; clips are hosted by Twitch.</li>
    <li><strong>Public clip records</strong> — for a channel being watched, we keep a record of the public clips other Twitch users create on it while it is live: the clip's id, title, time, and view count, and what our own score was at that moment, which is how we measure and improve the detector. In place of the clipper we store a one-way code derived from their Twitch id, so the same person is not counted twice; we do not store their name, and the code cannot be turned back into who they are.</li>
    <li><strong>Session data</strong> — a server-side session cookie that keeps you signed in (see our <a href="/cookies">Cookie Policy</a>).</li>
    <li><strong>Log data</strong> — server logs may contain IP addresses and request metadata for security and debugging purposes.</li>
    <li><strong>Feedback you send us</strong> — if you use the Feedback screen, we store your message along with your account id and username so we can reply. We may publish a quote from feedback as a testimonial; tell us not to and we will not.</li>
    <li><strong>Broadcaster opt-out records</strong> — if a broadcaster opts their channel out of the Service at <a href="/opt-out">/opt-out</a>, we store their Twitch id, login and display name so we can keep enforcing it. Apart from the public clip records above, this is the only information we hold about people who are not users of the Service, and it exists solely to honour their request. Ask us and we will remove the record, which also lifts the block.</li>
  </ul>
  <p><strong>Kick.</strong> Kick support is not live. We do not monitor Kick channels, and no Kick account can be connected to the Service — no Kick credentials are requested or stored. This will be updated before that changes.</p>

  <h2>2. How We Use Your Information</h2>
  <ul>
    <li>To authenticate you and maintain your session.</li>
    <li>To create clips on your behalf via Twitch's Clips API when you or your trigger settings direct it.</li>
    <li>To process payments and manage your subscription via Stripe.</li>
    <li>To display your clip links and trigger analytics in your dashboard.</li>
    <li>To tune detection for you: approving or rejecting a clip adjusts how sensitive the detector is on that channel, so the Service gets better at matching your taste. This affects only your own account.</li>
    <li>To investigate security incidents and prevent abuse.</li>
  </ul>

  <h2>3. How We Share Your Information</h2>
  <p>We do not sell your personal data. We share information only with the following third parties as necessary to operate the Service:</p>
  <ul>
    <li><strong>Twitch</strong> — for authentication and for creating clips on your behalf. Governed by Twitch's Privacy Notice.</li>
    <li><strong>Stripe</strong> — for payment processing. Governed by Stripe's Privacy Policy.</li>
  </ul>
  <p>We may disclose your information if required by law, regulation, or valid legal process.</p>

  <h2>4. Data Retention</h2>
  <p>We retain your account information, encrypted Twitch tokens, and clip metadata for as long as your account is active. When you delete your account we remove your user record, encrypted tokens, clip metadata and the chat samples stored with it, uploaded video, stream configurations, scheduled and exported items, per-stream statistics, and any feedback you sent us. Clips you have already created remain hosted on Twitch under your Twitch account and are governed by Twitch; you can manage or delete them through Twitch. Log files may be retained for up to 90 days for security purposes.</p>
  <p>Broadcaster opt-out records are kept for as long as the opt-out stands, because deleting the record would lift the block — which is the opposite of what the broadcaster asked for. Contact us to have it removed.</p>

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

</main>
""" + _LEGAL_FOOT + """
</body>
</html>"""

# The one quantity the Privacy Policy states about retained chat. Read from
# the worker rather than typed, so changing how much chat a clip keeps
# cannot leave the policy describing the old amount.
from src.ingestion.stream_worker import CHAT_SNAPSHOT_MESSAGES  # noqa: E402
PRIVACY_HTML = PRIVACY_HTML.replace(
    "<!--CHATN-->", str(CHAT_SNAPSHOT_MESSAGES), 1)

COOKIES_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cookie Policy — Highlightz</title>
<meta name="description" content="The one cookie Highlightz sets, the two browser preferences it stores, and the third-party cookies it does not control.">
<link rel="icon" type="image/png" href="/static/icon.png">
<!--SOCIAL_COOKIES-->
<style>""" + _LEGAL_STYLE + """</style>
</head>
<body>
""" + _LEGAL_NAV + """
<main class="legal">
  <div class="k">Legal</div>
  <h1>Cookie Policy</h1>
  <p class="meta">Effective date: September 2, 2026 &nbsp;|&nbsp; ANTI Technology LLC</p>

  <p>This Cookie Policy explains how Highlightz uses cookies and similar technologies. By using the Service you consent to the use of cookies as described here.</p>

  <h2>What is a Cookie?</h2>
  <p>A cookie is a small text file placed on your device by a website. Cookies help the site remember information about your visit so you don't have to re-enter it each time.</p>

  <h2>Cookies We Use</h2>
  <p>Highlightz uses a minimal number of cookies — only what is strictly necessary to operate the Service:</p>
  <table>
    <tr><th>Name</th><th>Purpose</th><th>Duration</th><th>Type</th></tr>
    <tr><td><code>session</code></td><td>Keeps you signed in between page loads. It carries your session details — your account id, Twitch username and avatar URL, and your plan status — signed so they cannot be altered, and readable only by your browser and our server over HTTPS. Nothing in it is used to track you across other sites.</td><td>7 days</td><td>Strictly necessary</td></tr>
  </table>
  <p>The dashboard also uses your browser's local storage for two small preferences. These are not cookies and are never sent to our servers — they stay in your browser, and clearing your site data clears them.</p>
  <table>
    <tr><th>Name</th><th>Purpose</th><th>Duration</th><th>Type</th></tr>
    <tr><td><code>hz_platform</code></td><td>Remembers which platform tab you were last on, so the dashboard opens where you left it.</td><td>Until cleared</td><td>Preference</td></tr>
    <tr><td><code>hz_welcome_seen</code></td><td>Remembers that you have already seen the welcome screen, so it is not shown again.</td><td>Until cleared</td><td>Preference</td></tr>
  </table>
  <p>We do not use advertising cookies, tracking pixels, or third-party analytics cookies. We do not use Google Analytics or any equivalent service.</p>

  <h2>Third-Party Cookies</h2>
  <p>When you sign in via Twitch, Twitch may set cookies on their own domain as part of the OAuth2 flow. These are governed by <a href="https://www.twitch.tv/p/legal/privacy-notice/" target="_blank" rel="noopener">Twitch's Privacy Notice</a>. When you complete a payment via Stripe, Stripe may set cookies on their domain. These are governed by <a href="https://stripe.com/privacy" target="_blank" rel="noopener">Stripe's Privacy Policy</a>. Clips are embedded from Twitch's own player, which may set cookies on Twitch's domain when a clip is played. We have no control over or access to any third-party cookies set on their respective domains.</p>

  <h2>Managing Cookies</h2>
  <p>You can control cookies through your browser settings. Blocking or deleting the session cookie will sign you out of the Service and require you to sign in again on your next visit. Most browsers allow you to:</p>
  <p>View and delete cookies · Block cookies from specific sites · Block all cookies · Delete all cookies when you close the browser</p>
  <p>Refer to your browser's help documentation for instructions. Note that disabling strictly necessary cookies will prevent the Service from functioning.</p>

  <h2>Changes to This Policy</h2>
  <p>We may update this Cookie Policy from time to time. Material changes will be posted at this URL with an updated effective date.</p>

  <h2>Contact</h2>
  <p>Questions? Contact us at <a href="mailto:support@highlightz.app">support@highlightz.app</a></p>

</main>
""" + _LEGAL_FOOT + """
</body>
</html>"""

# Filled after the literals close, not beside _social_head() — the same
# ordering rule the plan placeholders in this file follow. A NameError here is
# an import-time crash of the whole app, so it is worth being boring about.
TOS_HTML = TOS_HTML.replace("<!--SOCIAL_TOS-->", _social_head(
    "/tos", "Terms of Service — Highlightz",
    "The terms that govern your use of Highlightz, including plans, your "
    "responsibilities for clips you create, and how broadcasters can opt out."), 1)
PRIVACY_HTML = PRIVACY_HTML.replace("<!--SOCIAL_PRIVACY-->", _social_head(
    "/privacy", "Privacy Policy — Highlightz",
    "What Highlightz collects, why, who it is shared with, how long it is kept, "
    "and how to have it deleted."), 1)
COOKIES_HTML = COOKIES_HTML.replace("<!--SOCIAL_COOKIES-->", _social_head(
    "/cookies", "Cookie Policy — Highlightz",
    "The one cookie Highlightz sets, the two browser preferences it stores, and "
    "the third-party cookies it does not control."), 1)

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
  /* The admin panel is an INSTRUMENT, not marketing: the same black ground
     and the same three inks as the site, the display voice for the one
     heading, mono for every number because every number here is a
     measurement you compare against another one. One action colour (the
     ember), and it is reserved for the thing you would act on: the live
     figure, the current tab, the selected filter, the button that mints
     something. Everything else is ink on hairlines. */
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  /* Metric-matched fallback so the swap to Sora does not move the page. The
     numbers are the ones measured for the landing page (size-adjust from the
     advance-width ratio, the overrides restated against the adjusted em). */
  @font-face{font-family:'Sora Fallback';font-style:normal;font-weight:100 900;
    src:local('Arial'),local('Helvetica'),local('Liberation Sans');
    size-adjust:114.4%;ascent-override:84.8%;descent-override:25.3%;line-gap-override:0%}
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}
  :root{
    /* Surfaces: the site's black, one step up for fields and panels. */
    --bone:#0A0A0C; --void:#0E0B11; --wall:#151119; --bruise:#33203F;
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085); --hair-2:rgba(242,234,247,.15);
    /* The action colour, the brand light (kept for the staff marks only),
       and the two verdict colours. */
    --ember:#F7A745; --plum:#B86ADC; --glow:#B86ADC; --glow-ink:#C489E4; --flare:#D26AFB;
    --good:#4ADE80; --bad:#FF7A8A;
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora','Sora Fallback',system-ui,sans-serif;
    /* One curve, two durations: the same names the site uses, defined HERE
       because the old sheet referenced them without ever declaring them, so
       every transition on this page was silently instant. */
    --ease:cubic-bezier(.16,1,.3,1); --dur-fast:150ms; --dur-slow:400ms;
    --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:32px; --s-7:48px; --s-8:64px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{-webkit-text-size-adjust:100%}
  body{background:var(--bone);color:var(--ink);font-family:var(--sans);font-weight:400;
    font-size:14px;line-height:1.6;min-height:100vh;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
  a{text-decoration:none;color:inherit}
  code{font-family:var(--mono);font-size:12px;color:var(--ink);background:var(--wall);
    padding:0 var(--s-1);border-radius:3px}
  :focus-visible{outline:2px solid var(--ember);outline-offset:2px;border-radius:3px}
  ::selection{background:rgba(247,167,69,.35)}
  button,input,select{font:inherit;color:inherit}

  /* ── Mono label: the same voice the site uses for every kicker. ── */
  .k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3)}

  /* ── Top bar. The site's bar, sticky: lockup left, three links right.
     The word Admin sits in the lockup in the action colour so the page
     is never mistaken for the dashboard when three tabs are open. ── */
  .topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:var(--s-4);
    padding:var(--s-3) var(--s-5);background:#09070C;border-bottom:1px solid var(--hair)}
  .logo{display:flex;align-items:center;gap:var(--s-2);flex-shrink:0}
  .logo img{height:22px;display:block}
  .logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink)}
  .badge{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);padding-left:var(--s-3);border-left:1px solid var(--hair-2);line-height:1.2}
  .topbar-right{margin-left:auto;display:flex;align-items:center;gap:var(--s-1);min-width:0}
  .tlink{font-family:var(--mono);font-size:12px;letter-spacing:.02em;color:var(--ink-3);
    padding:var(--s-2) var(--s-3);border-radius:3px;white-space:nowrap;
    transition:color var(--dur-fast),background var(--dur-fast)}
  .tlink:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .tlink .n{color:var(--ember)}

  .wrap{max-width:1240px;margin:0 auto;padding:var(--s-7) var(--s-5) var(--s-8)}
  .head{display:flex;align-items:flex-end;justify-content:space-between;gap:var(--s-4);flex-wrap:wrap}
  h1{font-family:var(--sans);font-weight:800;letter-spacing:-.04em;line-height:.98;
    font-size:clamp(36px,5vw,56px);color:#fff;margin-top:var(--s-2)}
  .meta{font-size:14px;color:var(--ink-2);margin-top:var(--s-3);max-width:52ch;line-height:1.5}

  /* ── Overview rail. Not six floating tiles: one ruled strip, uneven weight,
     each figure carrying the second number that makes it mean something. ── */
  .rail{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin:var(--s-6) 0 0;
    border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
  .cell{padding:var(--s-5) var(--s-4) var(--s-5) var(--s-4);border-left:1px solid var(--hair);min-width:0}
  .cell:first-child{padding-left:0;border-left:none}
  .cell .v{font-family:var(--sans);font-weight:800;font-variant-numeric:tabular-nums;
    font-size:clamp(28px,2.6vw,36px);letter-spacing:-.04em;line-height:1;color:#fff}
  .cell.hot .v{color:var(--ember)}
  .cell .k{margin-top:var(--s-3)}
  .cell .s{font-size:12px;color:var(--ink-2);margin-top:var(--s-1);line-height:1.5;min-height:3em}

  /* ── Refusals. Only exists when something is wrong, so it is allowed the
     one red rule on the page. ── */
  #refusals-box{border:1px solid rgba(255,122,138,.3);border-left:3px solid var(--bad);border-radius:3px;
    padding:var(--s-4) var(--s-5);margin-top:var(--s-5)}
  #refusals-box .hd{display:flex;align-items:baseline;gap:var(--s-3);flex-wrap:wrap;margin-bottom:var(--s-3)}
  #refusals-box h2{font-size:17px;font-weight:700;letter-spacing:-.02em;color:var(--bad)}
  #refusals-box .sub{margin-top:0}

  /* ── Tabs ── */
  .tabs{display:flex;gap:0;margin:var(--s-7) 0 0;border-bottom:1px solid var(--hair);flex-wrap:wrap}
  .tab{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--ink-3);background:none;border:none;border-bottom:2px solid transparent;
    padding:var(--s-3) var(--s-4);cursor:pointer;
    transition:color var(--dur-fast),border-color var(--dur-fast)}
  .tab:hover{color:var(--ink-2)}
  .tab.on{color:var(--ink);border-bottom-color:var(--ember)}
  .tab .c{color:var(--ink-3);margin-left:var(--s-2);font-weight:400}
  .tab.on .c{color:var(--ember)}
  .panel{display:none;padding-top:var(--s-5)}
  .panel.on{display:block}
  .lede{font-size:14px;color:var(--ink-2);max-width:74ch;margin-bottom:var(--s-4);line-height:1.6}
  .lede b{color:var(--ink);font-weight:600}
  .block{margin-bottom:var(--s-7)}
  .block-head{display:flex;align-items:baseline;gap:var(--s-3);flex-wrap:wrap;margin-bottom:var(--s-1)}
  .block-head h2{font-family:var(--sans);font-weight:800;letter-spacing:-.03em;font-size:24px;color:#fff}
  .block-head .c{font-family:var(--mono);font-size:12px;letter-spacing:.1em;color:var(--ink-3)}

  /* ── Toolbar ── */
  .toolbar{display:flex;align-items:center;gap:var(--s-2);flex-wrap:wrap;margin-bottom:var(--s-4)}
  .field{background:var(--wall);border:1px solid var(--hair-2);color:var(--ink);border-radius:3px;
    padding:var(--s-2) var(--s-3);font-size:14px;font-family:var(--sans);flex:1 1 260px;max-width:360px;min-width:0;
    transition:border-color var(--dur-fast)}
  .field::placeholder{color:var(--ink-3)}
  .field:focus{outline:none;border-color:var(--ember)}
  /* Filters are separate chips, not one joined strip: a strip cannot wrap,
     and on a phone nine of them have to. */
  .chips{display:flex;gap:var(--s-1);flex-wrap:wrap}
  .chip{font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ink-3);background:none;border:1px solid var(--hair);border-radius:3px;
    padding:var(--s-1) var(--s-3);cursor:pointer;
    transition:color var(--dur-fast),background var(--dur-fast),border-color var(--dur-fast)}
  .chip:hover{color:var(--ink);border-color:var(--hair-2)}
  .chip.on{color:var(--bone);background:var(--ember);border-color:var(--ember);font-weight:600}
  .spacer{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--ink-3);letter-spacing:.06em}

  /* ── Tables ── */
  .tw{overflow-x:auto}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-family:var(--mono);font-size:12px;font-weight:600;color:var(--ink-3);
    text-transform:uppercase;letter-spacing:.14em;padding:0 var(--s-3) var(--s-3) 0;border-bottom:1px solid var(--hair-2);
    white-space:nowrap}
  th:last-child{padding-right:0}
  td{padding:var(--s-3) var(--s-3) var(--s-3) 0;font-size:14px;border-bottom:1px solid var(--hair);vertical-align:middle}
  td:last-child{padding-right:0}
  tbody tr{transition:background var(--dur-fast)}
  tbody tr:hover{background:rgba(242,234,247,.03)}
  tr.u-row{cursor:pointer}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600}
  .dim{color:var(--ink-3)}
  .mono{font-family:var(--mono);font-size:12px;letter-spacing:.02em}
  .who{display:flex;align-items:center;gap:var(--s-3);min-width:0}
  .avatar{width:32px;height:32px;border-radius:3px;object-fit:cover;flex-shrink:0;
    background:var(--wall);border:1px solid var(--hair)}
  .username{font-weight:600;letter-spacing:-.01em;color:#fff}
  .sub{font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-top:var(--s-1);letter-spacing:.02em;
    overflow-wrap:anywhere}

  /* ── Plan + status marks. Text, not filled pills: a table of eight coloured
     lozenges is unreadable, and the plan is the thing you scan for. Pro wears
     the action colour because it is the row that pays. ── */
  .plan{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.14em;text-transform:uppercase}
  .plan-pro{color:var(--ember)}
  .plan-starter{color:var(--ink)}
  .plan-free{color:var(--ink-3)}
  .plan-note{font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-top:var(--s-1);letter-spacing:.02em}
  .plan-note.pay{color:var(--good)}
  .plan-note.trial{color:var(--ember)}
  .plan-note.lapsed{color:var(--bad)}
  .tagm{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--ember);border:1px solid rgba(247,167,69,.35);padding:0 var(--s-1);border-radius:2px;margin-left:var(--s-2);
    font-weight:600;vertical-align:middle}
  .tagm.adm{color:var(--glow-ink);border-color:rgba(184,106,220,.45)}

  /* ── Buttons. The same shape as the site's: square corners, one weight. ── */
  .btn{font-family:var(--sans);font-size:12px;font-weight:600;padding:var(--s-1) var(--s-3);border-radius:3px;
    cursor:pointer;border:1px solid var(--hair-2);background:transparent;color:var(--ink-2);
    line-height:1.5;white-space:nowrap;
    transition:color var(--dur-fast),background var(--dur-fast),border-color var(--dur-fast)}
  .btn:hover{color:#fff;border-color:rgba(242,234,247,.45)}
  .btn:active{transform:translateY(1px)}
  .btn:disabled{opacity:.5;cursor:default}
  /* The drawer's "Send a message" is a LINK, because it goes to another page —
     a button that navigates cannot be opened in a new tab. Scoped to a.btn so
     the real buttons and the select.btn pickers keep their own display mode. */
  a.btn{display:inline-flex;align-items:center;text-decoration:none}
  /* The one filled button: it mints something. */
  .btn-key{background:var(--ember);border-color:var(--ember);color:var(--bone)}
  .btn-key:hover{background:#FFB65A;border-color:#FFB65A;color:var(--bone)}
  .btn-good{color:var(--good);border-color:rgba(74,222,128,.35)}
  .btn-good:hover{color:var(--good);border-color:var(--good)}
  .btn-bad{color:var(--bad);border-color:rgba(255,122,138,.3)}
  .btn-bad:hover{color:var(--bad);border-color:var(--bad)}
  select.btn{font-family:var(--mono);font-size:12px;letter-spacing:.04em;background:var(--wall);
    padding-right:var(--s-5);appearance:none;-webkit-appearance:none;
    background-image:linear-gradient(45deg,transparent 50%,var(--ink-3) 50%),linear-gradient(135deg,var(--ink-3) 50%,transparent 50%);
    background-position:calc(100% - 12px) 55%,calc(100% - 8px) 55%;background-size:4px 4px,4px 4px;background-repeat:no-repeat}
  select.btn option{background:var(--wall);color:var(--ink);font-family:var(--sans)}
  .acts{display:flex;gap:var(--s-2);flex-wrap:wrap;justify-content:flex-end}
  .empty,.loading{padding:var(--s-6) 0;color:var(--ink-3);font-size:14px;font-family:var(--mono);letter-spacing:.02em}
  .err{color:var(--bad);font-size:14px;padding:var(--s-4) 0}

  /* ── Sortable header ── */
  th.sortable{cursor:pointer;user-select:none;transition:color var(--dur-fast)}
  th.sortable:hover{color:var(--ink)}
  th.sortable.on{color:var(--ember)}
  tr.expandable{cursor:pointer}
  .drill{background:rgba(242,234,247,.02)}
  .drill td{padding:var(--s-4) 0 var(--s-4) var(--s-4);border-left:2px solid var(--ember)}
  .drl{display:flex;align-items:center;gap:var(--s-3);padding:var(--s-1) 0;font-size:12px}
  .drl-when{width:118px;flex-shrink:0;font-family:var(--mono);font-size:12px;color:var(--ink-3)}
  .drl-n{width:150px;flex-shrink:0;text-align:right;font-size:12px}
  .bar{flex:1;height:3px;background:rgba(242,234,247,.08);overflow:hidden;min-width:32px}
  .bar i{display:block;height:100%;background:var(--ember)}

  /* ── Toast ── */
  .toast{position:fixed;bottom:var(--s-5);right:var(--s-5);background:var(--wall);border:1px solid var(--hair-2);
    border-radius:3px;padding:var(--s-3) var(--s-4);font-size:14px;font-weight:600;opacity:0;transform:translateY(6px);
    max-width:min(420px,calc(100vw - 48px));overflow-wrap:anywhere;
    transition:opacity var(--dur-slow) var(--ease),transform var(--dur-slow) var(--ease);pointer-events:none;z-index:999}
  .toast.show{opacity:1;transform:none}
  .toast.ok{border-color:rgba(74,222,128,.5);color:var(--good)}
  .toast.err{border-color:rgba(255,122,138,.5);color:var(--bad)}

  /* ── User drawer. A drawer, not a centred modal: it holds the identity, the
     billing facts and the rare/destructive actions, so it is a place you read
     top to bottom rather than a dialog you dismiss. ── */
  .scrim{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;opacity:0;
    pointer-events:none;transition:opacity var(--dur-fast)}
  .scrim.open{opacity:1;pointer-events:auto}
  .drawer{position:fixed;top:0;right:0;bottom:0;width:min(560px,100%);z-index:101;
    background:var(--bone);border-left:1px solid var(--hair-2);
    display:flex;flex-direction:column;transform:translateX(100%);visibility:hidden;
    transition:transform var(--dur-slow) var(--ease),visibility var(--dur-slow)}
  .drawer.open{transform:none;visibility:visible}
  .drawer-head{display:flex;align-items:flex-start;gap:var(--s-3);padding:var(--s-4) var(--s-5);border-bottom:1px solid var(--hair)}
  .drawer-head h3{font-family:var(--sans);font-weight:800;letter-spacing:-.03em;font-size:24px;color:#fff;overflow-wrap:anywhere}
  .drawer-head .s{font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-top:var(--s-1);letter-spacing:.04em}
  .x{margin-left:auto;width:32px;height:32px;border-radius:3px;background:transparent;
    border:1px solid var(--hair-2);color:var(--ink-2);font-size:14px;cursor:pointer;flex-shrink:0;
    transition:color var(--dur-fast),border-color var(--dur-fast)}
  .x:hover{color:#fff;border-color:var(--ember)}
  .drawer-body{overflow-y:auto;padding:var(--s-5) var(--s-5) var(--s-6);display:flex;flex-direction:column;gap:var(--s-6)}
  .dh{font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;
    color:var(--ink-3);padding-bottom:var(--s-2);border-bottom:1px solid var(--hair-2);margin-bottom:var(--s-3)}
  .kv{display:grid;grid-template-columns:132px minmax(0,1fr);gap:var(--s-2) var(--s-3);font-size:14px}
  .kv dt{font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);padding-top:var(--s-1)}
  .kv dd{color:var(--ink-2);overflow-wrap:anywhere}
  .kv dd b{color:#fff;font-weight:600}
  /* Lifetime clip decisions. Five small readouts on one row so the shape of
     someone's usage is one glance, not five lines of prose. Wraps rather than
     scrolls: the drawer is narrow on a phone and a number you have to swipe
     to reach is a number nobody reads. */
  .stat-row{display:flex;flex-wrap:wrap;gap:var(--s-2)}
  .sbox{flex:1 1 84px;min-width:0;padding:var(--s-2) var(--s-3);border-radius:3px;
    border:1px solid var(--hair);background:rgba(242,234,247,.02)}
  .sbox b{display:block;font-family:var(--sans);font-weight:800;font-size:24px;letter-spacing:-.03em;
    font-variant-numeric:tabular-nums;line-height:1.1;color:#fff}
  .sbox span{display:block;margin-top:var(--s-1);font-family:var(--mono);font-size:12px;
    letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3)}
  .sbox.good b{color:var(--good)}
  .sbox.bad b{color:var(--bad)}
  .srow{display:flex;align-items:center;gap:var(--s-3);padding:var(--s-2) 0;border-bottom:1px solid var(--hair);font-size:14px}
  .srow:last-child{border-bottom:none}
  .srow b{color:#fff}
  .dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
  .dot-live{background:var(--good);box-shadow:0 0 7px rgba(74,222,128,.7)}
  .dot-offline{background:var(--ink-3)}
  .dot-starting{background:var(--ember);box-shadow:0 0 7px rgba(247,167,69,.6)}
  /* Approved clips are the ones that mattered, so they lead the list and are
     marked. Colour alone would not be enough — the status word carries the same
     information for anyone who cannot see the difference between the rules. */
  .crow{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto;gap:var(--s-3);align-items:center;
    padding:var(--s-2) 0 var(--s-2) var(--s-3);border-bottom:1px solid var(--hair);border-left:2px solid var(--hair);
    font-size:14px}
  .crow:last-child{border-bottom:none}
  .crow.ok{border-left-color:var(--good);
    background:linear-gradient(90deg,rgba(74,222,128,.06),transparent 45%)}
  .crow .st{font-family:var(--mono);font-size:12px;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink-3)}
  .crow.ok .st{color:var(--good)}
  .crow.ok b{color:#fff}
  .ct{display:block;font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-top:var(--s-1);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:.02em}
  .danger{border:1px solid rgba(255,122,138,.25);border-radius:3px;padding:var(--s-4) var(--s-4)}
  .danger .dh{border-bottom-color:rgba(255,122,138,.25);color:var(--bad)}
  .danger p{font-size:14px;color:var(--ink-3);margin-bottom:var(--s-3);line-height:1.5}

  @media(max-width:980px){
    .rail{grid-template-columns:repeat(3,minmax(0,1fr))}
    .cell:nth-child(4){padding-left:0;border-left:none}
    .cell:nth-child(-n+3){border-bottom:1px solid var(--hair)}
  }
  @media(max-width:700px){
    .wrap{padding:var(--s-5) var(--s-4) var(--s-8)}
    .topbar{padding:var(--s-2) var(--s-4);flex-wrap:wrap;row-gap:var(--s-1);gap:var(--s-3)}
    .topbar-right{margin-left:0;width:100%;gap:0;flex-wrap:wrap}
    .tlink{padding:var(--s-1) var(--s-2) var(--s-1) 0}
    .tlink + .tlink{padding-left:var(--s-2)}
    .rail{grid-template-columns:repeat(2,minmax(0,1fr))}
    .cell{padding:var(--s-4) var(--s-3) var(--s-4) var(--s-3)}
    .cell:nth-child(odd){padding-left:0;border-left:none}
    .cell:nth-child(-n+4){border-bottom:1px solid var(--hair)}
    .cell .s{min-height:0}
    /* Four tabs as a two-by-two grid: four in a row do not fit 390px, and a
       row that wraps one orphan onto its own line reads as broken. */
    .tabs{margin-top:var(--s-6);display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
    .tab{padding:var(--s-2) var(--s-3);text-align:center}
    .field{max-width:none;width:100%;flex-basis:100%}
    .spacer{margin-left:0;width:100%}
    .block-head h2{font-size:20px}
    #refusals-box{padding:var(--s-3) var(--s-4)}

    /* ── EVERY TABLE STACKS. Nothing is hidden on a phone: each row becomes
       a card, each cell a label/value pair, the label copied from the
       column header by the script (data-l). The first cell is the row's
       name and needs no label. Sorting survives because the clip record's
       header row turns into a row of sort chips instead of vanishing. ── */
    .tw{overflow-x:visible}
    .tw table,.tw tbody,.tw tr,.tw td{display:block}
    .tw thead{display:none}
    .tw tbody tr{border:1px solid var(--hair);border-radius:3px;padding:var(--s-2) var(--s-3);
      margin-bottom:var(--s-2);background:rgba(242,234,247,.02)}
    .tw td{display:grid;grid-template-columns:96px minmax(0,1fr);gap:var(--s-1) var(--s-3);
      align-items:start;padding:var(--s-1) 0;border-bottom:none;font-size:14px}
    .tw td::before{content:attr(data-l);font-family:var(--mono);font-size:12px;font-weight:600;
      letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);line-height:1.8}
    /* The label is the pseudo-element in column one; EVERY real child goes to
       column two, so a cell holding a plan plus two notes stacks its lines
       under the value instead of flowing them into the label column. */
    .tw td > *{grid-column:2}
    .tw td:first-child{display:block;padding:var(--s-1) 0 var(--s-2);margin-bottom:var(--s-1);
      border-bottom:1px solid var(--hair)}
    .tw td:first-child::before,.tw td:not([data-l])::before,.tw td[data-l=""]::before{display:none}
    .tw td:not([data-l]),.tw td[data-l=""]{grid-template-columns:minmax(0,1fr)}
    .tw td[style*="max-width"]{max-width:none}
    .tw .acts{justify-content:flex-start}
    .tw tr.drill{margin-top:calc(-1 * var(--s-2));border-top:none;border-radius:0 0 3px 3px}
    .drill td{border-left:none;padding:var(--s-2) 0}
    #cr-wrap thead,#cr-wrap thead tr{display:flex;flex-wrap:wrap;gap:var(--s-1)}
    #cr-wrap thead{margin-bottom:var(--s-3)}
    #cr-wrap thead th{display:block;border:1px solid var(--hair);border-radius:3px;
      padding:var(--s-1) var(--s-2);letter-spacing:.08em;border-bottom:1px solid var(--hair)}
    #cr-wrap thead th.on{color:var(--bone);background:var(--ember);border-color:var(--ember)}
    .drl{flex-wrap:wrap}
    .drl-when{width:auto}
    .drl-n{width:100%;text-align:left}
    .bar{flex-basis:100%;order:3}
    .kv{grid-template-columns:minmax(0,1fr);gap:var(--s-1) 0}
    .kv dt{padding-top:0}
    .kv dd{margin-bottom:var(--s-2)}
    .drawer{width:100%;border-left:none}
    .drawer-head{padding:var(--s-4)}
    .drawer-body{padding:var(--s-4) var(--s-4) var(--s-6);gap:var(--s-5)}
    .crow{grid-template-columns:minmax(0,1fr) auto;row-gap:var(--s-1)}
    .crow .st{grid-column:1}
    .crow .num{grid-column:2;grid-row:1;text-align:right}
    .crow .btn,.crow .dim{grid-column:2;grid-row:2;justify-self:end}
    .toast{left:var(--s-4);right:var(--s-4);bottom:var(--s-4);max-width:none}
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
  <div class="k">Control room</div>
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

  <!-- Channels Twitch is refusing to clip. HIDDEN when there are none, because
       an empty panel on a healthy box is noise that trains you to ignore the
       row. It appears only when something is actually wrong. -->
  <div id="refusals-box" style="display:none;margin:0 0 18px">
    <div class="hd"><h2>Channels Twitch is refusing to clip</h2>
      <span class="sub" id="refusals-sum"></span></div>
    <div class="tw"><div id="refusals-wrap"></div></div>
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
        <button class="chip on" data-f="active" title="Everyone currently on the product — free, paying, on trial and staff. Cancelled accounts keep free access but are listed under Lapsed.">Active</button>
        <button class="chip" data-f="pro">Pro</button>
        <button class="chip" data-f="starter">Starter</button>
        <button class="chip" data-f="free">Free</button>
        <button class="chip" data-f="trialing">Trial</button>
        <button class="chip" data-f="lapsed">Lapsed</button>
        <button class="chip" data-f="stalled" title="Started signing up and did not finish">Stalled</button>
        <button class="chip" data-f="noemail" title="No email on file — they have not paid, and their Twitch grant predates the email scope">No email</button>
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
// Relative, because the question the Last seen column answers is "how long
// ago", and a reader should not have to subtract dates in their head. Past a
// month it hands back to fmt, where "47d ago" stops being easier to read than
// the date. Defined HERE, beside the fmt it falls back to: this page has three
// separate script blocks that each define their own fmt, and putting it in the
// wrong one is a ReferenceError that only appears at runtime.
const ago = ts => {
  if(!ts) return '';
  const s = Date.now()/1000 - ts;
  if(s < 90) return 'just now';
  if(s < 3600) return Math.round(s/60) + 'm ago';
  if(s < 86400) return Math.round(s/3600) + 'h ago';
  if(s < 2592000) return Math.round(s/86400) + 'd ago';
  return fmt(ts);
};
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
  if(!r.ok){
    // FastAPI puts the human-readable reason in `detail`. Showing the raw body
    // made every refusal read as a stack trace to the person who caused it.
    const body = await r.text();
    let msg = body;
    try { const d = JSON.parse(body); if(d && d.detail) msg = d.detail; } catch(e) {}
    throw new Error(msg || ('HTTP ' + r.status));
  }
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
  const u = d.users || {}, c = d.clips || {}, s = d.streams || {};
  const seg = u.segment || {}, tier = u.paying_by_tier || {};
  set('ov-users', n0(u.total));
  // EACH SUB-LINE DESCRIBES ITS OWN TILE'S NUMBER. This one breaks down the
  // POPULATION, so it uses the mutually-exclusive segments and every account
  // appears exactly once. The breakdown used to live under Paying and was
  // built from by_plan (entitlement) mixed with the trial count — so a
  // trialing account was inside both "Pro" and "on trial", the parts summed to
  // more than the whole, and Free was not shown at all. Free is the front door
  // now, so leaving it out omitted most of the people on the platform.
  const parts = [];
  if(seg.free)     parts.push(n0(seg.free) + ' free');
  if(seg.paying)   parts.push(n0(seg.paying) + ' paying');
  if(seg.trialing) parts.push(n0(seg.trialing) + ' on trial');
  if(seg.comped)   parts.push(n0(seg.comped) + ' comped');
  if(seg.grace)    parts.push(n0(seg.grace) + ' card failing');
  parts.push(n0(u.new_7d) + ' joined this week');
  if(u.admins) parts.push(n0(u.admins) + ' staff');
  set('ov-users-s', parts.join(' · '));
  set('ov-paying', n0(u.paying));
  // The tiers OF THE PAYING, which is what this tile counts. `legacy` are
  // $15-era subscribers whose price Stripe holds and we do not.
  const pt = [];
  if(tier.pro)     pt.push(n0(tier.pro) + ' Pro');
  if(tier.starter) pt.push(n0(tier.starter) + ' Starter');
  if(tier.legacy)  pt.push(n0(tier.legacy) + ' legacy');
  set('ov-paying-s', pt.length ? pt.join(' · ') : 'no paid subscriptions yet');
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
  loadRefusals();
}

// Which channels are currently un-clippable, and why. Three different causes
// with three different fixes, so the reason is spelled out rather than left as
// a code — the answer to "what do I do about it" is different for each.
const REFUSAL_LABEL = {
  classification: 'Twitch cannot determine the content classification for this channel — the streamer needs to set their Content Classification Labels.',
  title_automod:  'The stream title did not pass Twitch automod — clears when the streamer renames the stream.',
  not_authorized: 'The broadcaster has clipping restricted on Twitch.',
};

async function loadRefusals(){
  let d;
  try { d = await api('/admin/clip-refusals'); } catch(e){ return; }
  const box = document.getElementById('refusals-box');
  if(!d || !d.rows || !d.rows.length){ box.style.display='none'; return; }
  box.style.display='';
  document.getElementById('refusals-sum').textContent =
    d.channels + ' channel' + (d.channels===1?'':'s') + ' · ' + n0(d.total) + ' refused attempts';
  let html = '<table><thead><tr><th>Channel</th><th>Refusals</th>'
    + '<th>Last seen</th><th>Why, and who can fix it</th></tr></thead><tbody>';
  d.rows.forEach(function(r){
    html += '<tr><td><b>' + esc(r.channel) + '</b></td>'
      + '<td>' + n0(r.count) + '</td>'
      + '<td>' + (r.last_seen ? new Date(r.last_seen*1000).toLocaleString() : '&mdash;') + '</td>'
      + '<td>' + esc(REFUSAL_LABEL[r.reason] || r.reason || '') + '</td></tr>';
  });
  document.getElementById('refusals-wrap').innerHTML = html + '</tbody></table>';
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
  // Past tense once the date is behind us. The backend now sends 'expired'
  // for a finished trial, so this branch is normally only reached by a live
  // one — but a row rendered from a cached payload, or a trial that ends
  // between the fetch and the paint, must not claim a past date is upcoming.
  if(st === 'trialing'){
    if(u.trial_ends_at && u.trial_ends_at * 1000 <= Date.now())
      return ['Trial ended ' + fmt(u.trial_ends_at), 'lapsed'];
    return ['Trial' + (u.trial_ends_at ? ' ends ' + fmt(u.trial_ends_at) : ''), 'trial'];
  }
  // A trial that ran out. Distinct from a cancelled subscription: nobody
  // decided to leave, the clock did, and the follow-up is different.
  if(st === 'expired') return [u.trial_ends_at ? 'Trial ended ' + fmt(u.trial_ends_at)
                                               : 'Trial ended', 'lapsed'];
  // A comped tier and a paid one look identical without this — and they are
  // the two you most need to tell apart when reading the table.
  if(st === 'active') return u.plan_source === 'granted'
    ? ['Granted — comped', '']
    : [u.plan_price ? '$' + u.plan_price + '/mo' : 'Granted', u.plan_price ? 'pay' : ''];
  if(st === 'inactive' || st === 'canceled') return ['Lapsed — on free', 'lapsed'];
  return ['Never subscribed', ''];
}

// Where they got to, shown ONLY when it adds something the plan pill does not.
// A paying customer's stage is "paying" and the pill already says so; printing
// it again is noise on every row. The stalls are the rows worth a second line.
const STALLED = <!--STALLED-->;

function stageNote(u){
  const st = u.funnel_stage || '';
  if(!STALLED.includes(st)) return '';
  const label = u.funnel_label || st;
  const when = u.checkout_started_at ? ' · ' + fmt(u.checkout_started_at) : '';
  // checkout_dropped is the one that can mean money changed hands and we did
  // not record it, so it is the one that gets the eye.
  const cls = st === 'checkout_dropped' ? 'lapsed' : '';
  return '<div class="plan-note ' + cls + '">' + esc(label) + when + '</div>';
}

function userState(u){
  if(u.is_admin) return 'admin';
  const st = u.subscription_status;
  if(st === 'trialing') return 'trialing';
  if(st === 'active') return 'active';
  // A trial that ran out belongs with the churn, not with fresh signups.
  // 'expired' used to fall through to 'none' — the bucket meaning "brand new,
  // never subscribed" — so a finished trial sat in the default Active view and
  // never appeared under Lapsed, which is the one list you would go looking
  // for them in. They still keep free access; this is about which story the
  // row tells, and theirs is "started paying attention, then stopped".
  if(st === 'inactive' || st === 'canceled' || st === 'expired') return 'lapsed';
  return 'none';
}

function userMatches(u){
  if(U_Q){
    const hay = ((u.username||'') + ' ' + (u.twitch_login||'') + ' ' + (u.email||'') + ' ' + (u.promo_code||'')).toLowerCase();
    if(hay.indexOf(U_Q) < 0) return false;
  }
  const st = userState(u);
  if(U_FILTER === 'all') return true;
  // "Active" = everyone whose relationship with the product is CURRENT, which
  // now includes the free tier.
  //
  // It used to mean "getting the PAID product", and that was right while free
  // was closed: an account that had signed up and not paid had nothing, so a
  // list full of them buried the real users. Reopening the free tier inverted
  // that. A free signup is a live user of the product — often the newest one —
  // and the default view was hiding exactly the people the free tier exists to
  // attract. `none` is what a fresh Twitch signup carries.
  //
  // WHY `lapsed` IS STILL OUT, since it is the obvious next question: a
  // cancelled account also drops to free (see plans.get_plan — "never
  // subscribed, cancelled, lapsed, or a finished trial — all four land on
  // free"), so it is NOT excluded for lack of access. It is excluded because
  // somebody who paid and stopped is a churn event, and folding them in here
  // would bury that in the one view that is open by default. The Lapsed chip
  // is where they belong.
  if(U_FILTER === 'active') return st === 'admin' || st === 'active'
                                || st === 'trialing' || st === 'none';
  if(U_FILTER === 'trialing') return st === 'trialing';
  if(U_FILTER === 'lapsed') return st === 'lapsed';
  // Everyone who began the signup and did not come out the other side. The
  // list you actually want when somebody says "a guy subscribed and has no
  // access" — checkout_dropped is the stage that can mean exactly that.
  if(U_FILTER === 'stalled') return STALLED.includes(u.funnel_stage || '');
  // The list to work from when you want to reach people and cannot. Everyone
  // here is waiting on a re-login: no Stripe email because they never paid, and
  // no Twitch email because their grant predates the scope.
  if(U_FILTER === 'noemail') return !u.email;
  return u.plan === U_FILTER;
}

function renderUsers(){
  const wrap = document.getElementById('u-wrap');
  if(!USERS.length){ wrap.className='empty'; wrap.textContent='No users yet.'; return; }
  const rows = USERS.map((u,i) => [u,i]).filter(p => userMatches(p[0]));
  // Email coverage next to the row count: the one number that says whether
  // you can actually reach these people. Only shown when somebody is
  // missing one, so it stays quiet once the answer is everybody.
  const withEmail = USERS.filter(u => u.email).length;
  document.getElementById('u-count').textContent = rows.length + ' of ' + USERS.length
    + (withEmail < USERS.length ? '  ·  ' + withEmail + '/' + USERS.length + ' with email' : '');
  document.getElementById('tc-users').textContent = USERS.length;
  if(!rows.length){ wrap.className='empty'; wrap.textContent='No users match that filter.'; return; }
  wrap.className = '';

  // Kept = what they have in their library right now. The three after it are
  // LIFETIME decisions and come from the append-only ledger, not from the clip
  // store — rejecting and clearing both DELETE the record, so counting there
  // would report only what survived.
  let html = '<table><thead><tr><th>User</th><th>Membership</th><th>Streams</th>'
    + '<th title="Clips in their library right now">Kept</th>'
    + '<th title="Clips they approved, all time">Accepted</th>'
    + '<th title="Clips they rejected one by one, all time">Rejected</th>'
    + '<th title="Clips they binned in bulk with Clear queue, all time">Cleared</th>'
    + '<th title="Last authenticated request. The browser keepalive does not count, so an abandoned open tab reads as idle.">Last seen</th>'
    + '<th style="text-align:right">Actions</th></tr></thead><tbody>';
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
        + '<div class="plan-note ' + note[1] + '">' + esc(note[0]) + '</div>'
        + stageNote(u) + '</td>'
      + '<td class="num">' + (u.stream_count||0) + '</td>'
      + '<td class="num">' + (u.clip_count||0) + '</td>'
      + '<td class="num">' + (u.clips_approved||0) + '</td>'
      + '<td class="num">' + (u.clips_rejected||0) + '</td>'
      + '<td class="num">' + (u.clips_cleared||0) + '</td>'
      + '<td><div>' + (u.last_active_at ? esc(ago(u.last_active_at))
          : '<span class="dim">not since we started counting</span>') + '</div>'
        + '<div class="plan-note">joined ' + fmt(u.created_at) + '</div></td>'
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
    if(!confirm('Revoke access for ' + u.username + '? This also cancels their Stripe subscription.')) return;
    try {
      var res = await api('/admin/users/' + u.id + '/revoke', 'POST');
      // A Stripe failure is the one outcome that must NOT read as success:
      // access is gone either way, but if the cancel did not land the customer
      // is still being charged and only this message will say so.
      if(res && res.stripe_ok === false){
        toast('Access revoked, but the Stripe cancel FAILED — cancel it manually', false);
      } else if(res && res.stripe_cancelled > 0){
        toast('Access revoked and Stripe subscription cancelled');
      } else {
        toast('Access revoked (no active Stripe subscription)');
      }
      refresh();
    }
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
    + '<dt>Last seen</dt><dd>' + (u.last_active_at
        ? esc(ago(u.last_active_at)) + ' <span class="dim" style="font-size:12px">('
          + fmt(u.last_active_at) + ')</span>'
        : '<span class="dim">not since we started counting</span>') + '</dd>'
    // Distinct from Last seen: this only moves when they go through Twitch and
    // re-approve, which is what decides whether a newly requested permission
    // can reach them. Recorded only from the day it shipped, so an empty value
    // means "not since then", not "never" — and saying "never" about a daily
    // user would be a lie the panel tells with a straight face.
    + '<dt>Last sign-in</dt><dd>' + (u.last_login_at
        ? esc(ago(u.last_login_at)) + ' <span class="dim" style="font-size:12px">('
          + fmt(u.last_login_at) + ')</span>'
        : '<span class="dim">not since we started recording it</span>') + '</dd>'
    + '<dt>Joined</dt><dd>' + fmt(u.created_at) + '</dd>'
    + '<dt>Email</dt><dd>' + (u.email
        ? esc(u.email) + (u.email_source
            ? ' <span class="dim" style="font-size:12px">(' + esc(u.email_source) + ')</span>'
            : '')
        : '<span class="dim">none &mdash; arrives when they next sign in</span>') + '</dd>'
    + '<dt>User id</dt><dd>' + esc(u.id) + '</dd>'
    + '</dl>'
    + (mem ? '<div class="acts" style="justify-content:flex-start;margin-top:16px">' + mem + '</div>' : '')
    // Reaching them. Deliberately NOT gated on is_admin like the membership
    // controls are — there is no such thing as an account you must not be able
    // to talk to. It opens the composer with this person already picked,
    // because deciding somebody needs a message happens HERE, while you are
    // looking at them, and making you find them again in a second list is how
    // you end up messaging the wrong person.
    + '<div class="acts" style="justify-content:flex-start;margin-top:10px">'
    + '<a class="btn" href="/admin/feedback-page?to=' + encodeURIComponent(u.id) + '">'
    + 'Send a message</a></div>'
    + '</div>';

  const [streams, clips] = await Promise.all([
    api('/admin/users/' + u.id + '/streams').catch(() => []),
    api('/admin/users/' + u.id + '/clips').catch(() => []),
  ]);
  if(DR_USER !== u) return;              // drawer moved on while we were loading

  html += '<div><div class="dh" style="display:flex;align-items:center;gap:10px">'
    + '<span style="flex:1">Monitored streams (' + streams.length + ')</span>'
    + (streams.length > 1
        ? '<button class="btn btn-bad dr-stopall">Stop all</button>' : '')
    + '</div>';
  // data-ch carries the channel rather than the button's position, so the
  // handler cannot act on the wrong stream after the list re-renders.
  html += streams.length
    ? streams.map(s => '<div class="srow"><span class="' + dotClass(s.status||'offline') + '"></span>'
        + '<span style="flex:1;min-width:0"><b>' + esc(s.channel) + '</b>'
        + '<span class="ct">' + esc(s.platform) + ' · preset ' + esc(s.preset || 'default') + '</span></span>'
        + '<span class="dim mono" style="text-transform:capitalize">' + esc(s.status||'offline') + '</span>'
        + '<button class="btn btn-bad dr-stopone" data-ch="' + esc(s.channel) + '">Stop</button>'
        + '</div>').join('')
    : '<div class="dim">No streams registered.</div>';
  html += '</div>';

  // LIFETIME decisions, straight off the ledger. Above this the drawer shows
  // recent clips, which is a different question: that list is what still
  // exists, this is everything that ever happened.
  const caught = u.clips_caught||0, ok = u.clips_approved||0,
        no = u.clips_rejected||0, cl = u.clips_cleared||0, ex = u.clips_expired||0;
  if(caught){
    html += '<div><div class="dh">Clip decisions (all time)</div>'
      + '<div class="stat-row">'
      + '<div class="sbox"><b>' + caught + '</b><span>caught</span></div>'
      + '<div class="sbox good"><b>' + ok + '</b><span>accepted</span></div>'
      + '<div class="sbox bad"><b>' + no + '</b><span>rejected</span></div>'
      + '<div class="sbox"><b>' + cl + '</b><span>cleared</span></div>'
      + '<div class="sbox"><b>' + ex + '</b><span>expired</span></div>'
      + '</div>'
      + '<div class="dim" style="font-size:12px;margin-top:8px">'
      + (ok + no
          ? (u.clips_kept_pct||0) + '% kept of the ' + (ok + no) + ' they actually judged. '
          : 'Nothing judged yet. ')
      + 'Cleared and expired are not counted as rejections: nobody ruled on them.'
      + '</div></div>';
  }

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
        + '<span class="num" style="font-size:12px;color:var(--ink-2)">' + Math.round(c.virality_score||0) + '%</span>'
        + (c.twitch_url ? '<a class="btn" href="' + esc(c.twitch_url) + '" target="_blank" rel="noopener">Watch ↗</a>'
                        : '<span class="dim" style="font-size:12px">no link</span>')
        + '</div>';
      }).join('')
    : '<div class="dim">No clips yet.</div>';
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
  // THIS LIST IS THE GATE. A dr-* button missing from it is inert — the click
  // lands, closest() returns null, and the handler gives up before the branch
  // that would have acted. It fails silently and looks exactly like a button
  // that does nothing, so tests/test_admin_stop_streams.py checks every dr-*
  // class in the drawer markup appears here.
  const b = e.target.closest('.dr-grant, .dr-revoke, .dr-labeler, .dr-admin, .dr-sync, .dr-del, .dr-stopone, .dr-stopall');
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
    // Both stop branches REOPEN the drawer instead of closing it. Shedding load
    // means stopping several streams in a row, and closing after each one would
    // make the admin re-find the same user every time.
    if(b.classList.contains('dr-stopone')){
      const ch = b.getAttribute('data-ch');
      if(!confirm('Stop monitoring ' + ch + ' for ' + u.username + '? This frees the slot now, they keep their plan and can start it again themselves.')) return;
      b.disabled = true;
      await api('/admin/users/' + u.id + '/streams/' + encodeURIComponent(ch), 'DELETE');
      toast('Stopped ' + ch);
      await openUser(u); refresh(); return;
    }
    if(b.classList.contains('dr-stopall')){
      if(!confirm('Stop ALL streams for ' + u.username + '? This frees their slots now, they keep their plan and can start them again themselves.')) return;
      b.disabled = true;
      const r = await api('/admin/users/' + u.id + '/streams', 'DELETE');
      toast('Stopped ' + (r.stopped || 0) + ' stream' + ((r.stopped === 1) ? '' : 's'));
      await openUser(u); refresh(); return;
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
      const changed = r.changed || [];
      // "No subscription found" used to be the answer for a cancelled customer,
      // which is exactly when this button DID something. Say what moved.
      toast(changed.length
        ? 'Synced — ' + changed.join(' and ') + ' now ' + r.app_status + (r.plan ? ' / ' + r.plan : '')
        : 'Already in sync (' + r.app_status + ')');
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
    ? '<span class="plan" style="color:var(--good)">Live</span>'
    : '<span class="plan plan-free">' + (i.expired ? 'Expired' : 'Used up') + '</span>';
  return '<tr><td><span class="mono" style="color:var(--ink);overflow-wrap:anywhere">' + rvEsc(url) + '</span>'
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
        + '<td class="dim mono">'
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
    + codes.map(c => '<tr><td class="mono" style="font-weight:600;color:var(--ember)">' + esc(c) + '</td>'
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
      inner += ss.map(s => '<div class="drl">'
        + '<span class="drl-when">' + crWhen(s.started_at) + '</span>'
        + '<span class="bar"><i style="width:' + (s.caught ? (s.approved/s.caught*100) : 0) + '%"></i></span>'
        + '<span class="num drl-n">'
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
    + '<span style="color:rgba(242,234,247,.18)">' + RV_STAR.repeat(5-n) + '</span></span>';
}

function rvRow(r){
  // Consent is the user's decision and is NOT overridable here. The publish
  // button only exists for reviews they agreed to publish; offering it
  // otherwise invites putting someone's words on the public site by accident.
  const act = r.publish_consent
    ? '<button class="btn rv-act" data-id="' + rvEsc(r.id) + '" data-on="' + (!r.approved) + '">'
      + (r.approved ? 'Unpublish' : 'Publish') + '</button>'
    : '<span class="dim mono">no consent</span>';
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

// ── phone layout ────────────────────────────────────────────────────────────
// Under 700px every table stacks into cards (see the stylesheet), and each
// cell shows its column's name in front of the value. The name is copied from
// the header here, after every render, so the seven render functions do not
// each have to know about it and a column added later is labelled for free.
// Observing childList only: setting an attribute does not fire it, so the
// labeller cannot trigger itself.
function labelCells(root){
  root.querySelectorAll('table').forEach(t => {
    const heads = Array.from(t.querySelectorAll('thead th'))
      .map(h => h.textContent.replace(/[▾▴]/g, '').trim());
    t.querySelectorAll('tbody tr').forEach(tr => {
      if(tr.classList.contains('drill')) return;
      Array.from(tr.children).forEach((td, i) => td.setAttribute('data-l', heads[i] || ''));
    });
  });
}
new MutationObserver(ms => {
  const seen = new Set();
  ms.forEach(m => {
    const tw = m.target.nodeType === 1 ? m.target.closest('.tw') : null;
    if(tw && !seen.has(tw)){ seen.add(tw); labelCells(tw); }
  });
}).observe(document.body, {childList:true, subtree:true});

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
  body{background:#0e0b11;color:#f2eaf7;font-family:Inter,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;text-align:center}
  body::before{content:'';position:fixed;inset:0;z-index:-1;background:radial-gradient(700px 400px at 50% 30%,rgba(184,106,220,.18),transparent 60%)}
  .wrap{padding:32px 24px}
  .code{font-size:44px;font-weight:800;letter-spacing:-.05em;background:linear-gradient(135deg,#f943ff 0%,#b86adc 52%,#7c6bff 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1}
  h1{font-size:24px;font-weight:700;margin:16px 0 8px;letter-spacing:-.02em}
  p{font-size:14px;color:#b9aec4;margin-bottom:24px}
  a{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);color:#f2eaf7;border-radius:12px;padding:12px 16px;font-size:12px;font-weight:600;text-decoration:none;transition:var(--dur-fast)}
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
@font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
@font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#000;color:#F2EAF7;font-family:'Sora',system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
  -webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
:focus-visible{outline:2px solid #F7A745;outline-offset:3px;border-radius:2px}
.card{background:#0A0A0C;border:1px solid rgba(242,234,247,.15);border-radius:8px;padding:32px;max-width:480px;width:100%;text-align:center}
.logo{font-family:'Plex',ui-monospace,Menlo,monospace;font-weight:600;font-size:14px;letter-spacing:.12em;text-transform:uppercase;color:#F2EAF7;margin-bottom:24px}
h1{font-weight:800;font-size:28px;letter-spacing:-.04em;line-height:1;margin-bottom:12px;color:#fff}
p{font-size:15px;color:#B9AEC4;line-height:1.55;margin-bottom:16px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:12px 24px;border-radius:3px;font-family:inherit;font-size:15px;font-weight:700;
  cursor:pointer;border:1px solid transparent;text-decoration:none;transition:background 150ms,border-color 150ms,color 150ms}
.btn-twitch{background:#9146ff;border-color:#9146ff;color:#fff}
.btn-twitch:hover{background:#7c39d4;border-color:#7c39d4}
.btn-confirm{background:#F7A745;border-color:#F7A745;color:#0A0A0C;width:100%}
.btn-confirm:hover{background:#FFB65A;border-color:#FFB65A}
.btn-back{background:transparent;border-color:rgba(255,255,255,.35);color:#F2EAF7;font-size:14px}
.btn-back:hover{border-color:#fff}
.avatar{width:72px;height:72px;border-radius:50%;object-fit:cover;margin:0 auto 16px;display:block;border:1px solid rgba(242,234,247,.15)}
.avatar-placeholder{width:72px;height:72px;border-radius:50%;background:rgba(242,234,247,.08);margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:30px}
.name{font-size:18px;font-weight:700;margin-bottom:4px;color:#fff}
.handle{font-family:'Plex',ui-monospace,Menlo,monospace;font-size:12px;letter-spacing:.06em;color:#9C90A6;margin-bottom:24px}
.warning{border-left:2px solid #F7A745;padding:8px 0 8px 16px;font-size:14px;color:#B9AEC4;margin-bottom:24px;text-align:left;line-height:1.5}
.warning strong{color:#fff}
.success-icon{font-size:44px;margin-bottom:16px}
.steps{text-align:left;margin-bottom:24px;list-style:none}
.steps li{font-size:14px;color:#B9AEC4;padding:8px 0 8px 24px;position:relative;line-height:1.5;border-top:1px solid rgba(242,234,247,.085)}
.steps li:last-child{border-bottom:1px solid rgba(242,234,247,.085)}
.steps li::before{content:'';position:absolute;left:0;top:16px;width:12px;height:2px;background:#F7A745}
.divider{height:1px;background:rgba(242,234,247,.085);margin:16px 0}
"""

_OPTOUT_LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Streamer Opt-Out — Highlightz</title>
<meta name="description" content="Any Twitch broadcaster can remove their channel from Highlightz here. Enter the channel name and confirm — no account needed, and it takes effect immediately.">
<link rel="icon" type="image/png" href="/static/icon.png">
<!--SOCIAL_OPTOUT-->
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
  <p style="font-size:12px;margin-bottom:20px">You must be the actual owner of the Twitch channel. We verify this through Twitch's official login — you cannot opt out someone else's channel.</p>
  <a href="/auth/twitch?intent=optout" class="btn btn-twitch">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714z"/></svg>
    Verify with Twitch
  </a>
  <div class="divider"></div>
  <p style="font-size:12px;color:#6b6b7b;margin-bottom:0">Already a Highlightz user? <a href="/login" style="color:#b86adc;text-decoration:none">Log in here</a></p>
</div>
</body>
</html>"""

_OPTOUT_LANDING_HTML = _OPTOUT_LANDING_HTML.replace("<!--SOCIAL_OPTOUT-->", _social_head(
    "/opt-out", "Streamer Opt-Out — Highlightz",
    "Any Twitch broadcaster can remove their channel from Highlightz here. "
    "Enter the channel name and confirm — no account needed."), 1)

_OPTOUT_CONFIRM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
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
<meta name="robots" content="noindex">
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
  <p style="font-size:13px">If you change your mind in the future, contact <a href="mailto:support@highlightz.app" style="color:#b86adc;text-decoration:none">support@highlightz.app</a> to be removed.</p>
</div>
</body>
</html>"""

# ── Admin sub-pages (feedback, opt-out registry) ─────────────────────────────
# The same instrument as /admin: the site's black, the three inks, mono
# labels, the ember as the one action colour, hairline panels. Shared here so
# the three admin screens cannot drift apart again — they used to be three
# hand-written sheets in three different fonts.
_ADMIN_SUB_STYLE = """
  @font-face{font-family:'Sora';font-style:normal;font-weight:100 900;font-display:swap;src:url(/static/fonts/sora-var.woff2) format('woff2')}
  @font-face{font-family:'Sora Fallback';font-style:normal;font-weight:100 900;
    src:local('Arial'),local('Helvetica'),local('Liberation Sans');
    size-adjust:114.4%;ascent-override:84.8%;descent-override:25.3%;line-gap-override:0%}
  @font-face{font-family:'Plex';font-style:normal;font-weight:400;font-display:swap;src:url(/static/fonts/plexmono-400.woff2) format('woff2')}
  @font-face{font-family:'Plex';font-style:normal;font-weight:600;font-display:swap;src:url(/static/fonts/plexmono-600.woff2) format('woff2')}
  :root{
    --bone:#0A0A0C; --wall:#151119;
    --ink:#F2EAF7; --ink-2:#B9AEC4; --ink-3:#9C90A6;
    --hair:rgba(242,234,247,.085); --hair-2:rgba(242,234,247,.15);
    --ember:#F7A745; --glow-ink:#C489E4; --good:#4ADE80; --bad:#FF7A8A;
    --mono:'Plex',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora','Sora Fallback',system-ui,sans-serif;
    --ease:cubic-bezier(.16,1,.3,1); --dur-fast:150ms; --dur-slow:400ms;
    --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:32px; --s-7:48px; --s-8:64px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{-webkit-text-size-adjust:100%}
  body{background:var(--bone);color:var(--ink);font-family:var(--sans);font-weight:400;
    font-size:14px;line-height:1.6;min-height:100vh;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
  a{text-decoration:none;color:inherit}
  :focus-visible{outline:2px solid var(--ember);outline-offset:2px;border-radius:3px}
  ::selection{background:rgba(247,167,69,.35)}
  button,input,select,textarea{font:inherit;color:inherit}
  .k{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3)}
  .topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:var(--s-4);
    padding:var(--s-3) var(--s-5);background:#09070C;border-bottom:1px solid var(--hair)}
  .logo{display:flex;align-items:center;gap:var(--s-2);flex-shrink:0}
  .logo img{height:22px;display:block}
  .logo span{font-family:var(--mono);font-weight:600;font-size:14px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink)}
  .badge{font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--ember);padding-left:var(--s-3);border-left:1px solid var(--hair-2);line-height:1.2}
  .topbar-right{margin-left:auto;display:flex;align-items:center;gap:var(--s-1);min-width:0}
  .tlink{font-family:var(--mono);font-size:12px;letter-spacing:.02em;color:var(--ink-3);
    padding:var(--s-2) var(--s-3);border-radius:3px;white-space:nowrap;
    transition:color var(--dur-fast),background var(--dur-fast)}
  .tlink:hover{color:var(--ink);background:rgba(242,234,247,.05)}
  .tlink.on{color:var(--ink)}
  .wrap{max-width:1240px;margin:0 auto;padding:var(--s-7) var(--s-5) var(--s-8)}
  h1{font-family:var(--sans);font-weight:800;letter-spacing:-.04em;line-height:.98;
    font-size:clamp(36px,5vw,56px);color:#fff;margin-top:var(--s-2)}
  .meta{font-size:14px;color:var(--ink-2);margin-top:var(--s-3);max-width:60ch;line-height:1.5}
  .btn{font-family:var(--sans);font-size:12px;font-weight:600;padding:var(--s-1) var(--s-3);border-radius:3px;
    cursor:pointer;border:1px solid var(--hair-2);background:transparent;color:var(--ink-2);
    line-height:1.5;white-space:nowrap;
    transition:color var(--dur-fast),background var(--dur-fast),border-color var(--dur-fast)}
  .btn:hover{color:#fff;border-color:rgba(242,234,247,.45)}
  .btn:active{transform:translateY(1px)}
  .btn:disabled{opacity:.5;cursor:default}
  .btn-key{background:var(--ember);border-color:var(--ember);color:var(--bone)}
  .btn-key:hover{background:#FFB65A;border-color:#FFB65A;color:var(--bone)}
  .btn-bad{color:var(--bad);border-color:rgba(255,122,138,.3)}
  .btn-bad:hover{color:var(--bad);border-color:var(--bad)}
  .field{width:100%;background:var(--wall);border:1px solid var(--hair-2);color:var(--ink);border-radius:3px;
    padding:var(--s-2) var(--s-3);font-size:14px;font-family:var(--sans);min-width:0;
    transition:border-color var(--dur-fast)}
  .field::placeholder{color:var(--ink-3)}
  .field:focus{outline:none;border-color:var(--ember)}
  .chips{display:flex;gap:var(--s-1);flex-wrap:wrap}
  .chip{font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ink-3);background:none;border:1px solid var(--hair);border-radius:3px;
    padding:var(--s-1) var(--s-3);cursor:pointer;
    transition:color var(--dur-fast),background var(--dur-fast),border-color var(--dur-fast)}
  .chip:hover{color:var(--ink);border-color:var(--hair-2)}
  .chip.on{color:var(--bone);background:var(--ember);border-color:var(--ember);font-weight:600}
  .empty,.loading{padding:var(--s-6) 0;color:var(--ink-3);font-size:14px;font-family:var(--mono);letter-spacing:.02em}
  .dim{color:var(--ink-3)}
  .mono{font-family:var(--mono);font-size:12px;letter-spacing:.02em}
  .toast{position:fixed;bottom:var(--s-5);right:var(--s-5);background:var(--wall);border:1px solid var(--hair-2);
    border-radius:3px;padding:var(--s-3) var(--s-4);font-size:14px;font-weight:600;color:var(--ink);opacity:0;transform:translateY(6px);
    max-width:min(420px,calc(100vw - 48px));overflow-wrap:anywhere;
    transition:opacity var(--dur-slow) var(--ease),transform var(--dur-slow) var(--ease);pointer-events:none;z-index:999}
  .toast.show{opacity:1;transform:none}
  @media(max-width:700px){
    .wrap{padding:var(--s-5) var(--s-4) var(--s-8)}
    .topbar{padding:var(--s-2) var(--s-4);flex-wrap:wrap;row-gap:var(--s-1);gap:var(--s-3)}
    .topbar-right{margin-left:0;width:100%;gap:0;flex-wrap:wrap}
    .tlink{padding:var(--s-1) var(--s-2) var(--s-1) 0}
    .tlink + .tlink{padding-left:var(--s-2)}
    .toast{left:var(--s-4);right:var(--s-4);bottom:var(--s-4);max-width:none}
  }
"""


def _admin_nav(current: str) -> str:
    """The admin bar with the current screen marked. One source for the three
    admin pages so a link added to one appears on all of them."""
    links = [("optout", "/admin/optout", "Opt-out registry"),
             ("feedback", "/admin/feedback-page", "Feedback"),
             ("overview", "/admin", "Overview")]
    out = []
    for key, href, label in links:
        on = ' on' if key == current else ''
        out.append(f'    <a href="{href}" class="tlink{on}">{label}</a>')
    out.append('    <a href="/" class="tlink">&#8592; Dashboard</a>')
    return ('<div class="topbar">\n  <div class="logo">\n'
            '    <img src="/static/logo-mark.png" alt="Highlightz">\n    <span>Highlightz</span>\n  </div>\n'
            '  <span class="badge">Admin</span>\n  <div class="topbar-right">\n'
            + "\n".join(out) + '\n  </div>\n</div>')


_ADMIN_FEEDBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Feedback — Highlightz Admin</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>""" + _ADMIN_SUB_STYLE + """
  /* ── Compose: starting a thread with somebody who has not written in ── */
  .compose{border:1px solid var(--hair-2);border-radius:3px;padding:var(--s-5);margin:var(--s-6) 0 var(--s-7);
    max-width:820px}
  .compose h2{font-family:var(--sans);font-weight:800;letter-spacing:-.03em;font-size:24px;color:#fff;margin-bottom:var(--s-1)}
  .compose .hint{font-size:14px;color:var(--ink-2);margin-bottom:var(--s-4);line-height:1.5;max-width:60ch}
  .cmp-label{display:block;margin-bottom:var(--s-2)}
  .cmp-chips{margin:var(--s-2) 0}
  /* Scrolls rather than growing: the whole point is that this sits above the
     feedback list, and a hundred users would push it off the screen. */
  .cmp-people{max-height:240px;overflow-y:auto;border:1px solid var(--hair);border-radius:3px;
    padding:var(--s-1);display:flex;flex-direction:column;gap:0}
  .cmp-person{display:flex;align-items:center;gap:var(--s-2);padding:var(--s-2) var(--s-2);border-radius:3px;
    cursor:pointer;font-size:14px;min-width:0;transition:background var(--dur-fast)}
  .cmp-person:hover{background:rgba(242,234,247,.04)}
  .cmp-person.on{background:rgba(247,167,69,.1)}
  .cmp-person input{accent-color:var(--ember);cursor:pointer;flex-shrink:0}
  .cmp-nm{font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
  .cmp-login{font-family:var(--mono);font-size:12px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
  /* Truncates with an ellipsis rather than being sliced mid-word by the row
     edge: "Signed up, never opened ch" reads as a rendering fault. */
  .cmp-meta{font-family:var(--mono);font-size:12px;letter-spacing:.02em;color:var(--ink-3);margin-left:auto;
    text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex-shrink:1}
  .cmp-none{padding:var(--s-4);color:var(--ink-3);font-family:var(--mono);font-size:12px}
  .cmp-foot{display:flex;align-items:center;gap:var(--s-2);margin-top:var(--s-3);flex-wrap:wrap}
  .cmp-count{font-family:var(--mono);font-size:12px;color:var(--ink-3);letter-spacing:.02em;margin-left:var(--s-1)}
  .cmp-count b{color:var(--ember)}
  textarea.field{resize:vertical;line-height:1.5;display:block}

  /* ── Threads ── */
  .block-head{display:flex;align-items:baseline;gap:var(--s-3);flex-wrap:wrap;margin-bottom:var(--s-4)}
  .block-head h2{font-family:var(--sans);font-weight:800;letter-spacing:-.03em;font-size:24px;color:#fff}
  .fb-list{display:flex;flex-direction:column;gap:var(--s-3);max-width:820px}
  .fb-item{border:1px solid var(--hair);border-left:2px solid var(--hair);border-radius:3px;padding:var(--s-4);
    transition:border-color var(--dur-fast)}
  .fb-item.unread{border-left-color:var(--ember);background:rgba(247,167,69,.04)}
  .fb-meta{display:flex;align-items:center;gap:var(--s-2);margin-bottom:var(--s-2);flex-wrap:wrap}
  .fb-user{font-weight:600;font-size:14px;color:#fff;overflow-wrap:anywhere}
  .fb-cat,.fb-started{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;
    color:var(--ink-3);border:1px solid var(--hair);padding:0 var(--s-1);border-radius:2px;line-height:1.7}
  .fb-started{color:var(--ember);border-color:rgba(247,167,69,.35)}
  .fb-time{font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-left:auto;letter-spacing:.02em}
  .fb-msg{font-size:14px;color:var(--ink);line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
  .fb-actions{display:flex;gap:var(--s-2);margin-top:var(--s-3);flex-wrap:wrap}
  .new-dot{width:6px;height:6px;border-radius:50%;background:var(--ember);box-shadow:0 0 7px rgba(247,167,69,.6);flex-shrink:0}
  /* Our replies carry the ember rule; a reply FROM the user reads as inbound
     on a plain hairline. The same colour both ways makes a thread unreadable
     at a glance. */
  .fb-reply{margin:var(--s-2) 0 0;padding:var(--s-2) var(--s-3);border-left:2px solid var(--ember);
    background:rgba(247,167,69,.05);font-size:14px;line-height:1.5;overflow-wrap:anywhere}
  .fb-reply b{display:block;font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--ember);margin-bottom:var(--s-1)}
  .fb-reply .fb-time{display:block;margin-left:0;margin-top:var(--s-1)}
  .fb-reply.from-user{border-left-color:var(--hair-2);background:rgba(242,234,247,.03)}
  .fb-reply.from-user b{color:var(--ink-3)}
  .fb-replybox{display:flex;gap:var(--s-2);margin-top:var(--s-3);align-items:flex-start}
  .fb-replybox textarea{flex:1;min-width:0;resize:vertical;font:inherit;font-size:14px;line-height:1.5;
    padding:var(--s-2) var(--s-3);border-radius:3px;border:1px solid var(--hair-2);
    background:var(--wall);color:inherit;transition:border-color var(--dur-fast)}
  .fb-replybox textarea::placeholder{color:var(--ink-3)}
  .fb-replybox textarea:focus{outline:none;border-color:var(--ember)}
  .btn-reply{background:var(--ember);border-color:var(--ember);color:var(--bone)}
  .btn-reply:hover{background:#FFB65A;border-color:#FFB65A;color:var(--bone)}
  @media(max-width:700px){
    .compose{padding:var(--s-4);margin:var(--s-5) 0 var(--s-6)}
    .fb-replybox{flex-wrap:wrap}
    .fb-replybox textarea{flex-basis:100%}
    .cmp-person{flex-wrap:wrap}
    .cmp-meta{margin-left:0;flex-basis:100%;text-align:left;padding-left:var(--s-5)}
  }
</style>
</head>
<body>
""" + _admin_nav("feedback") + """

<div class="wrap">
<div class="k">Control room</div>
<h1>Feedback</h1>
<p class="meta">What users wrote in, the threads you opened, and a composer for reaching the people who never write.
  <span class="fb-started" id="unread-badge" style="display:none;margin-left:8px"></span></p>

<div class="compose">
  <h2>Send a message</h2>
  <div class="hint">Starts a thread in their Feedback tab — they get it live, with a badge, and
    can reply straight back to you here. Not email: most accounts sign in with Twitch and never
    give us an address, so this is the channel that actually reaches everybody.</div>
  <label class="k cmp-label" for="cmp-q">To</label>
  <input id="cmp-q" class="field" placeholder="Search by name, Twitch login or email…" autocomplete="off">
  <div class="chips cmp-chips" id="cmp-chips">
    <button class="chip cmp-chip on" data-f="all">Everyone</button>
    <button class="chip cmp-chip" data-f="paying">Paying</button>
    <button class="chip cmp-chip" data-f="trialing">On trial</button>
    <button class="chip cmp-chip" data-f="stalled">Stopped at the paywall</button>
    <button class="chip cmp-chip" data-f="lapsed">Lapsed</button>
    <button class="chip cmp-chip" data-f="selected">Selected</button>
  </div>
  <div class="cmp-people" id="cmp-people"><div class="cmp-none">Loading people…</div></div>
  <div class="cmp-foot">
    <button class="btn" id="cmp-all">Select all shown</button>
    <button class="btn" id="cmp-clear">Clear</button>
    <span class="cmp-count" id="cmp-count">No one selected</span>
  </div>
  <label class="k cmp-label" for="cmp-msg" style="margin-top:16px">Message</label>
  <textarea id="cmp-msg" rows="4" maxlength="2000" class="field"
    placeholder="Write your message — they see it in the app, and can reply."></textarea>
  <div class="cmp-foot">
    <button class="btn btn-reply" id="cmp-send">Send message</button>
    <span class="cmp-count" id="cmp-left">0/2000</span>
  </div>
</div>

<div class="block-head"><h2>Threads</h2><span class="mono dim" id="fb-c"></span></div>
<div class="fb-list" id="list"><p class="empty">Loading…</p></div>
</div>
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
    if(unread>0){badge.textContent=unread+' unread';badge.style.display='inline-block';}
    else badge.style.display='none';
    document.getElementById('fb-c').textContent=items.length+(items.length===1?' thread':' threads');
    list.innerHTML=items.map(f=>`
      <div class="fb-item${f.read?'':' unread'}" id="fb-${f.id}">
        <div class="fb-meta">
          ${f.read?'':'<span class="new-dot"></span>'}
          <span class="fb-user">${esc(f.username||f.user_id)}</span>
          ${f.from_admin_start
            ? '<span class="fb-started">You started this</span>'
            : `<span class="fb-cat">${esc(f.category||'general')}</span>`}
          <span class="fb-time">${fmt(f.created_at)}</span>
        </div>
        ${f.from_admin_start ? '' : `<div class="fb-msg">${esc(f.message)}</div>`}
        ${(f.replies||[]).map((r,ri)=>`
          <div class="fb-reply${r.from_admin===false?' from-user':''}">
            <b>${r.from_admin===false
                  ? esc(f.username||'They')+' replied'
                  /* The first thing in a thread WE opened is not a reply — it
                     is the message. Calling it "You replied" made the panel
                     read as though the user had said something first. */
                  : (f.from_admin_start && ri===0 ? 'You wrote' : 'You replied')}</b>
            <div>${esc(r.message)}</div>
            <span class="fb-time">${fmt(r.at)}</span></div>`).join('')}
        <div class="fb-replybox">
          <textarea id="rp-${f.id}" rows="2" placeholder="Write a reply — they see it in the app"></textarea>
          <button class="btn btn-reply" onclick="reply('${f.id}')">Send reply</button>
        </div>
        <div class="fb-actions">
          ${f.read?'':`<button class="btn" onclick="markRead('${f.id}')">Mark read</button>`}
          <button class="btn btn-bad" onclick="del('${f.id}')">Delete</button>
        </div>
      </div>`).join('');
  }
  function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
  async function markRead(id){
    try{await api('/admin/feedback/'+id+'/read','POST');items.find(f=>f.id===id).read=true;render();toast('Marked as read');}
    catch{toast('Error');}
  }
  async function reply(id){
    const box=document.getElementById('rp-'+id);
    const msg=(box.value||'').trim();
    if(!msg){toast('Write something first');return;}
    try{
      const r=await fetch('/admin/feedback/'+id+'/reply',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})});
      if(!r.ok)throw new Error(r.status);
      const f=items.find(x=>x.id===id);
      if(f){ f.replies=(f.replies||[]).concat([{message:msg,at:Date.now()/1000}]); f.read=true; }
      box.value='';
      render();
      toast('Reply sent — they will see it in the app');
    }catch{toast('Error sending reply');}
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

  // ── Compose ────────────────────────────────────────────────────────────────
  // Everything above this line is REACTIVE: it can only answer a thread the
  // user opened. So the people you most need to reach — somebody who stopped at
  // the paywall, a trial about to lapse — were the ones there was no way to
  // contact, because not writing in was the whole problem.
  let PEOPLE=[], SEL=new Set(), CMP_Q='', CMP_F='all';
  const STALLED = <!--STALLED-->;

  function inFilter(u){
    if(CMP_F==='selected') return SEL.has(u.id);
    if(CMP_F==='paying')   return u.funnel_stage==='paying';
    if(CMP_F==='trialing') return u.funnel_stage==='trialing';
    if(CMP_F==='stalled')  return STALLED.indexOf(u.funnel_stage)!==-1;
    if(CMP_F==='lapsed')   return u.funnel_stage==='lapsed'||u.funnel_stage==='past_due';
    return true;
  }
  function inSearch(u){
    if(!CMP_Q) return true;
    return [u.username,u.twitch_login,u.email].some(
      v => (v||'').toLowerCase().indexOf(CMP_Q)!==-1);
  }
  function shownPeople(){ return PEOPLE.filter(u=>inFilter(u)&&inSearch(u)); }

  function renderPeople(){
    const box=document.getElementById('cmp-people');
    const list=shownPeople();
    if(!PEOPLE.length){ box.innerHTML='<div class="cmp-none">No users yet.</div>'; }
    else if(!list.length){ box.innerHTML='<div class="cmp-none">Nobody matches that.</div>'; }
    else box.innerHTML=list.map(u=>`
      <label class="cmp-person${SEL.has(u.id)?' on':''}">
        <input type="checkbox" data-id="${esc(u.id)}"${SEL.has(u.id)?' checked':''}>
        <span class="cmp-nm">${esc(u.username||u.id)}</span>
        <span class="cmp-login">${u.twitch_login?'@'+esc(u.twitch_login):''}</span>
        <span class="cmp-meta">${esc(u.funnel_label||u.plan_label||u.plan||'')}</span>
      </label>`).join('');
    // Counted over EVERYONE, not over the visible list: a selection made under
    // one filter is still a selection after you switch to another, and a count
    // that dropped when you changed tabs would look like it had lost people.
    const n=SEL.size;
    document.getElementById('cmp-count').innerHTML =
      n ? '<b>'+n+'</b> '+(n===1?'person':'people')+' selected' : 'No one selected';
  }

  document.getElementById('cmp-people').addEventListener('change', e => {
    const cb=e.target.closest('input[type=checkbox]'); if(!cb) return;
    if(cb.checked) SEL.add(cb.dataset.id); else SEL.delete(cb.dataset.id);
    renderPeople();
  });
  document.getElementById('cmp-q').addEventListener('input', e => {
    CMP_Q=(e.target.value||'').toLowerCase().trim(); renderPeople();
  });
  document.getElementById('cmp-chips').addEventListener('click', e => {
    const c=e.target.closest('.cmp-chip'); if(!c) return;
    CMP_F=c.dataset.f;
    document.querySelectorAll('#cmp-chips .cmp-chip').forEach(x=>x.classList.toggle('on',x===c));
    renderPeople();
  });
  document.getElementById('cmp-all').addEventListener('click', () => {
    shownPeople().forEach(u=>SEL.add(u.id)); renderPeople();
  });
  document.getElementById('cmp-clear').addEventListener('click', () => {
    SEL.clear(); renderPeople();
  });
  document.getElementById('cmp-msg').addEventListener('input', e => {
    document.getElementById('cmp-left').textContent=(e.target.value||'').length+'/2000';
  });

  document.getElementById('cmp-send').addEventListener('click', async () => {
    const box=document.getElementById('cmp-msg');
    const msg=(box.value||'').trim();
    const ids=Array.from(SEL);
    if(!ids.length){ toast('Pick who it goes to'); return; }
    if(!msg){ toast('Write something first'); return; }
    // Sending to a group is not undoable and lands in a real person's app, so
    // the count is confirmed out loud before it goes.
    if(ids.length>1 && !confirm('Send this to '+ids.length+' people?')) return;
    const btn=document.getElementById('cmp-send');
    btn.disabled=true; btn.textContent='Sending…';
    try{
      const r=await fetch('/admin/feedback/new',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({user_ids:ids,message:msg})});
      if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail||r.status); }
      const res=await r.json();
      box.value=''; SEL.clear();
      document.getElementById('cmp-left').textContent='0/2000';
      renderPeople();
      // The new threads are now the top of the list, so reload it rather than
      // leaving the admin looking at a list that does not contain what they
      // just sent.
      await load();
      const skipped=(res.skipped||[]).length;
      toast('Sent to '+res.sent+(res.sent===1?' person':' people')
        + (skipped ? ' — '+skipped+' skipped' : ''));
    }catch(e){ toast('Could not send: '+(e.message||'error')); }
    btn.disabled=false; btn.textContent='Send message';
  });

  async function loadPeople(){
    try{ PEOPLE=await api('/admin/users'); }
    catch{ document.getElementById('cmp-people').innerHTML=
      '<div class="cmp-none">Could not load the user list.</div>'; return; }
    // Most recently seen first: outreach is nearly always about somebody who
    // was just here, and joined-order buries them under everyone who ever was.
    PEOPLE.sort((a,b)=>(b.last_active_at||0)-(a.last_active_at||0));
    // Arriving from a user's row in the admin panel, with that person already
    // picked — the panel is where you decide somebody needs a message, and
    // making you find them again in a second list is how you message the wrong
    // person.
    const to=new URLSearchParams(location.search).get('to');
    if(to && PEOPLE.some(u=>u.id===to)){
      SEL.add(to);
      const who=PEOPLE.find(u=>u.id===to);
      CMP_F='selected';
      document.querySelectorAll('#cmp-chips .cmp-chip').forEach(
        x=>x.classList.toggle('on', x.dataset.f==='selected'));
      document.getElementById('cmp-msg').focus();
      toast('Composing to ' + (who.username||to));
    }
    renderPeople();
  }
  loadPeople();
</script>
</body>
</html>"""

# Filled after both literals close — the ordering rule this file already
# follows for every other placeholder. One source, two script blocks: these
# were two hand-typed copies of the same list, which is how a change to one
# silently leaves the other behind.
_STALLED_JSON = json.dumps(list(_plans.FUNNEL_STALLED))
ADMIN_HTML = ADMIN_HTML.replace("<!--STALLED-->", _STALLED_JSON, 1)
_ADMIN_FEEDBACK_HTML = _ADMIN_FEEDBACK_HTML.replace("<!--STALLED-->", _STALLED_JSON, 1)

_ADMIN_OPTOUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Opt-Out Registry — Highlightz Admin</title>
<link rel="icon" type="image/png" href="/static/icon.png">
<style>""" + _ADMIN_SUB_STYLE + """
  .tw{overflow-x:auto;margin-top:var(--s-6);max-width:960px}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-family:var(--mono);font-size:12px;font-weight:600;color:var(--ink-3);
    text-transform:uppercase;letter-spacing:.14em;padding:0 var(--s-3) var(--s-3) 0;border-bottom:1px solid var(--hair-2);white-space:nowrap}
  td{padding:var(--s-3) var(--s-3) var(--s-3) 0;font-size:14px;border-bottom:1px solid var(--hair);vertical-align:middle}
  td:last-child{padding-right:0;text-align:right}
  tbody tr{transition:background var(--dur-fast)}
  tbody tr:hover{background:rgba(242,234,247,.03)}
  .who b{color:#fff;font-weight:600}
  .who .sub{display:block;font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-top:var(--s-1);letter-spacing:.02em}
  @media(max-width:700px){
    /* Stacks into cards, the same way every table on /admin does. */
    .tw{overflow-x:visible}
    .tw table,.tw tbody,.tw tr,.tw td{display:block}
    .tw thead{display:none}
    .tw tbody tr{border:1px solid var(--hair);border-radius:3px;padding:var(--s-2) var(--s-3);
      margin-bottom:var(--s-2);background:rgba(242,234,247,.02)}
    .tw td{display:grid;grid-template-columns:96px minmax(0,1fr);gap:var(--s-1) var(--s-3);
      align-items:start;padding:var(--s-1) 0;border-bottom:none;text-align:left}
    .tw td::before{content:attr(data-l);font-family:var(--mono);font-size:12px;font-weight:600;
      letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);line-height:1.8}
    .tw td > *{grid-column:2}
    .tw td:first-child{display:block;padding:var(--s-1) 0 var(--s-2);margin-bottom:var(--s-1);border-bottom:1px solid var(--hair)}
    .tw td:first-child::before,.tw td[data-l=""]::before{display:none}
    .tw td[data-l=""]{grid-template-columns:minmax(0,1fr)}
    .tw td:last-child{text-align:left}
  }
</style>
</head>
<body>
""" + _admin_nav("optout") + """
<div class="wrap">
<div class="k">Control room</div>
<h1>Opt-out registry</h1>
<p class="meta">Streamers who verified with Twitch and asked not to be clipped on Highlightz. Removing someone makes their channel clippable again.</p>
<div class="tw"><div id="wrap"><div class="empty">Loading...</div></div></div>
</div>
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
  const rows=items.map(i=>'<tr><td class="who"><b>'+esc(i.display_name)+'</b><span class="sub">@'+esc(i.twitch_login)+'</span></td><td class="mono dim" data-l="Twitch ID">'+esc(i.twitch_id)+'</td><td data-l="Opted out">'+fmt(i.opted_out_at)+'</td><td data-l=""><button class="btn btn-bad" onclick="remove('+JSON.stringify(i.twitch_id)+','+JSON.stringify(i.twitch_login)+')">Remove</button></td></tr>').join('');
  document.getElementById('wrap').innerHTML='<table><thead><tr><th>Streamer</th><th>Twitch ID</th><th>Opted out</th><th></th></tr></thead><tbody>'+rows+'</tbody></table>';
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
