"""Campaigns: the clipping marketplace (owner, 2026-09-30).

"I basically want it showcase all of these things and have a whole marketplace
for users on the site" — every campaign that is running or coming up, with its
dates, rules, streamers, prize pool and how the prize is split, how entries are
judged, and the posting rules (required hashtags, caption) so a clip can be
posted for it straight from Highlightz already following them.

Created and edited from the admin page ("make it so that inside the admin page
we can create new campaigns … add a picture and insert all the rules").

WHO SEES IT. Admins always; everyone else only once CAMPAIGNS_ENABLED is on, and
then only PUBLISHED campaigns. A draft is visible to admins alone.

STORE. One JSON file (campaigns.json) and one image per campaign under
campaign_images/. Small, admin-written, read on every marketplace load, so it is
held in memory and written atomically on change.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "campaigns.json"
_IMAGES = Path(settings.local_storage_path) / "campaign_images"

PLATFORMS = ("tiktok", "instagram", "youtube")
STREAM_PLATFORMS = ("twitch", "kick")
# How entries are judged. Chosen from a list so the page can say it plainly and
# a later leaderboard can compute it; "custom" is judged by hand from the note.
JUDGING = {
    "top_clip_views":   "Most views on a single clip",
    "best_n_views":     "Most total views across your best {n} clips",
    "best_n_engagement": "Most engagement (views + likes + comments) across your best {n} clips",
    "custom":           "Judged by the organisers",
}
TITLE_MAX, SUMMARY_MAX, RULES_MAX, CAPTION_MAX = 120, 300, 20000, 2200
MAX_STREAMERS, MAX_PAYOUTS, MAX_TAGS = 20, 50, 30
IMAGE_MAX = 5 * 1024 * 1024
_IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}
_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_]{1,60}$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.]{1,60}$")

_campaigns: dict[str, dict] = {}
_loaded = False


class CampaignError(ValueError):
    """A field the admin has to fix; the message is shown to them."""


# ── load / save ──────────────────────────────────────────────────────────────

def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        rows = json.loads(_INDEX.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception:
        log.error("campaigns_file_unreadable", path=str(_INDEX))
        return
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and _ID_RE.match(str(r.get("id", ""))):
            _campaigns[r["id"]] = r


def _save() -> None:
    atomic_write_json(_INDEX, list(_campaigns.values()))


# ── cleaning what the admin typed ────────────────────────────────────────────

def _text(v, limit: int) -> str:
    return str(v or "").strip()[:limit]


def _ts(v, what: str) -> float:
    try:
        t = float(v)
    except (TypeError, ValueError):
        raise CampaignError(f"Pick a {what}.")
    if t <= 0:
        raise CampaignError(f"Pick a {what}.")
    return t


def _money(v) -> float:
    try:
        m = round(float(v), 2)
    except (TypeError, ValueError):
        raise CampaignError("Prize amounts must be numbers.")
    if m < 0 or m > 10_000_000:
        raise CampaignError("Prize amounts must be between 0 and 10,000,000.")
    return m


def _tags(v) -> list[str]:
    raw = v if isinstance(v, list) else re.split(r"[\s,]+", str(v or ""))
    out = []
    for t in raw:
        t = str(t).strip().lstrip("#")
        if not t:
            continue
        if not _TAG_RE.match(t):
            raise CampaignError(f"#{t} is not a valid hashtag (letters, numbers and _ only).")
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:MAX_TAGS]


def _handles(v) -> list[str]:
    raw = v if isinstance(v, list) else re.split(r"[\s,]+", str(v or ""))
    out = []
    for h in raw:
        h = str(h).strip().lstrip("@")
        if not h:
            continue
        if not _HANDLE_RE.match(h):
            raise CampaignError(f"@{h} is not a valid handle.")
        if h not in out:
            out.append(h)
    return out[:MAX_TAGS]


def _streamers(v) -> list[dict]:
    out = []
    for s in (v or [])[:MAX_STREAMERS]:
        if not isinstance(s, dict):
            continue
        name = _text(s.get("name"), 60).lstrip("@")
        if not name:
            continue
        plat = s.get("platform") if s.get("platform") in STREAM_PLATFORMS else "twitch"
        if not _HANDLE_RE.match(name):
            raise CampaignError(f"Streamer {name!r}: use their channel name (letters, numbers, _ and .).")
        base = "https://www.twitch.tv/" if plat == "twitch" else "https://kick.com/"
        out.append({"name": name, "platform": plat, "url": base + name})
    return out


def clean(raw: dict, existing: dict | None = None) -> dict:
    """A whole campaign from the admin form, validated. Raises CampaignError."""
    if not isinstance(raw, dict):
        raise CampaignError("Send the campaign as an object.")
    title = _text(raw.get("title"), TITLE_MAX)
    if not title:
        raise CampaignError("Give the campaign a title.")
    start, end = _ts(raw.get("start_at"), "start date"), _ts(raw.get("end_at"), "end date")
    if end <= start:
        raise CampaignError("The end date must be after the start date.")
    payouts = [_money(x) for x in (raw.get("payouts") or [])][:MAX_PAYOUTS]
    payouts = [p for p in payouts if p > 0]
    pool = _money(raw.get("prize_pool") or sum(payouts))
    if payouts and round(sum(payouts), 2) > pool:
        raise CampaignError(f"The places add up to ${sum(payouts):,.2f}, more than the "
                            f"${pool:,.2f} prize pool.")
    method = raw.get("judging") if raw.get("judging") in JUDGING else "top_clip_views"
    try:
        n = int(raw.get("judging_n") or 3)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(20, n))
    note = _text(raw.get("judging_note"), 1000)
    if method == "custom" and not note:
        raise CampaignError("Describe how entries are judged.")
    platforms = [p for p in (raw.get("platforms") or []) if p in PLATFORMS]
    if not platforms:
        raise CampaignError("Pick at least one platform clips can be posted to.")
    now = time.time()
    return {
        "id": (existing or {}).get("id") or uuid.uuid4().hex,
        "title": title,
        "summary": _text(raw.get("summary"), SUMMARY_MAX),
        "rules": _text(raw.get("rules"), RULES_MAX),
        "start_at": start, "end_at": end,
        "streamers": _streamers(raw.get("streamers")),
        "prize_pool": pool,
        "currency": "USD",
        "payouts": payouts,
        "judging": method, "judging_n": n, "judging_note": note,
        "platforms": platforms,
        "hashtags": _tags(raw.get("hashtags")),
        "mentions": _handles(raw.get("mentions")),
        "caption_template": _text(raw.get("caption_template"), CAPTION_MAX),
        "published": bool(raw.get("published")),
        "image": (existing or {}).get("image", ""),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }


# ── reading ──────────────────────────────────────────────────────────────────

def status_of(c: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    if now < c["start_at"]:
        return "upcoming"
    return "live" if now < c["end_at"] else "ended"


def judging_text(c: dict) -> str:
    return JUDGING[c["judging"]].format(n=c.get("judging_n") or 3)


def public(c: dict, now: float | None = None) -> dict:
    d = {k: v for k, v in c.items() if k != "image"}
    d["status"] = status_of(c, now)
    d["judging_text"] = judging_text(c)
    d["winners"] = len(c.get("payouts") or [])
    d["image_url"] = f"/campaigns/{c['id']}/image?v={int(c.get('updated_at') or 0)}" if c.get("image") else ""
    return d


_ORDER = {"live": 0, "upcoming": 1, "ended": 2}


def listing(*, include_drafts: bool, now: float | None = None) -> list[dict]:
    """Live first (ending soonest), then upcoming (starting soonest), then ended
    (most recent first)."""
    _load()
    now = time.time() if now is None else now
    rows = [public(c, now) for c in _campaigns.values() if include_drafts or c.get("published")]

    def key(d):
        s = d["status"]
        return (_ORDER[s], d["end_at"] if s == "live" else d["start_at"] if s == "upcoming" else -d["end_at"])
    return sorted(rows, key=key)


def get(campaign_id: str) -> dict | None:
    _load()
    return _campaigns.get(campaign_id or "")


# ── writing ──────────────────────────────────────────────────────────────────

def create(raw: dict) -> dict:
    _load()
    c = clean(raw)
    _campaigns[c["id"]] = c
    _save()
    return c


def update(campaign_id: str, raw: dict) -> dict | None:
    _load()
    old = _campaigns.get(campaign_id)
    if not old:
        return None
    c = clean(raw, existing=old)
    _campaigns[campaign_id] = c
    _save()
    return c


def delete(campaign_id: str) -> bool:
    _load()
    c = _campaigns.pop(campaign_id, None)
    if not c:
        return False
    if c.get("image"):
        (_IMAGES / c["image"]).unlink(missing_ok=True)
    _save()
    return True


def set_image(campaign_id: str, data: bytes) -> dict | None:
    """Store the campaign's picture. PNG or JPEG, sniffed from the bytes (not
    the filename the browser claims), up to 5 MB."""
    _load()
    c = _campaigns.get(campaign_id)
    if not c:
        return None
    if not data:
        raise CampaignError("That file is empty.")
    if len(data) > IMAGE_MAX:
        raise CampaignError("The picture must be 5 MB or smaller.")
    ext = next((e for magic, e in _IMAGE_TYPES.items() if data.startswith(magic)), None)
    if ext is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    if ext is None:
        raise CampaignError("Use a PNG, JPEG or WebP picture.")
    _IMAGES.mkdir(parents=True, exist_ok=True)
    if c.get("image"):
        (_IMAGES / c["image"]).unlink(missing_ok=True)
    name = f"{campaign_id}.{ext}"
    tmp = _IMAGES / (name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(_IMAGES / name)
    c["image"] = name
    c["updated_at"] = time.time()
    _save()
    return c


def image_path(campaign_id: str) -> Path | None:
    c = get(campaign_id)
    if not c or not c.get("image"):
        return None
    p = _IMAGES / c["image"]
    return p if p.is_file() else None


# ── posting for a campaign ───────────────────────────────────────────────────

def caption_problems(c: dict, caption: str) -> list[str]:
    """What a caption is missing to count for this campaign. The poster refuses
    a campaign post with any of these, so an entry cannot miss a required tag."""
    low = (caption or "").lower()
    missing = [f"#{t}" for t in c.get("hashtags") or []
               if not re.search(r"#" + re.escape(t.lower()) + r"(?![a-z0-9_])", low)]
    missing += [f"@{h}" for h in c.get("mentions") or []
                if not re.search(r"@" + re.escape(h.lower()) + r"(?![a-z0-9_.])", low)]
    return missing
