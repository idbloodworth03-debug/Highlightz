"""Announcements: one message from the operator, in front of every user.

WHAT THIS IS FOR. The owner needs to tell everyone something — a new feature,
a change, an outage explained — and a toast that scrolls away or an email that
lands in spam is not "telling everyone". So an announcement is a modal that
sits in front of the dashboard until the person reading it dismisses it, and
it reaches them two ways: live over the socket if their tab is open, and on
the next open if it is not. Both matter; a message only the people online at
that second saw would miss most of the accounts it was written for.

DELIBERATELY SMALL. A title, a body, when it was sent, when it stops showing,
and whether it is an announcement or a product update (`kind`, owner
2026-10-14 — it labels the popup and the row in the notifications bell).
Per-user dismissal lives on the user record (users.py), so an announcement
somebody closed stays closed on every device. Retiring one deletes it — there
is no archive to maintain and nothing to un-retire; send it again.

THE BELL. Every announcement still showing is also listed in the dashboard's
notifications bell (GET /inbox), dismissed or not, so a popup somebody closed
can be read again until it expires or is retired.

A CAMPAIGN BUTTON (owner, 2026-10-25: "a mass announcement sent out to all
users about the clipping campaign"). An announcement can carry a campaign id:
its popup and its row in the bell then have a "See the campaign" button that
opens that campaign's page. It stops showing when posting for the campaign
closes (`until`), so nobody is sent to join something that has closed.

BOUNDED. A title and body have hard ceilings, an announcement always expires,
and the list is pruned of expired rows on every write — nothing here grows
without something else deleting it.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json, read_json

log = structlog.get_logger(__name__)

_FILE = Path(settings.local_storage_path) / "announcements.json"

MAX_TITLE = 80
MAX_BODY = 1000
MAX_DAYS = 90
DEFAULT_DAYS = 14
KINDS = ("announcement", "update")


def _load() -> list[dict]:
    data = read_json(_FILE, [])
    return data if isinstance(data, list) else []


def _save(rows: list[dict]) -> None:
    try:
        atomic_write_json(_FILE, rows)
    except Exception as exc:
        # An announcement must never be the reason something else fails.
        log.warning("announcements_save_failed", error=str(exc))


def _live(rows: list[dict], now: float) -> list[dict]:
    return [r for r in rows if float(r.get("expires_at") or 0) > now]


def kind_of(row: dict) -> str:
    """Rows sent before `kind` existed are announcements."""
    k = row.get("kind")
    return k if k in KINDS else "announcement"


def create(title: str, body: str, days: int | None = None, by: str = "",
           kind: str = "announcement", campaign_id: str = "", until: float | None = None) -> dict:
    """Send an announcement. Raises ValueError for an empty or oversized one —
    the endpoint turns that into a 400 with the message. An unknown `kind` is
    an announcement. `campaign_id` gives it a "See the campaign" button (the
    endpoint checks the campaign); `until`, if sooner, is when it stops."""
    title = (title or "").strip()
    body = (body or "").strip()
    if not title:
        raise ValueError("An announcement needs a title.")
    if not body:
        raise ValueError("An announcement needs a message.")
    if len(title) > MAX_TITLE:
        raise ValueError(f"Title is over {MAX_TITLE} characters.")
    if len(body) > MAX_BODY:
        raise ValueError(f"Message is over {MAX_BODY} characters.")
    try:
        days = int(days if days is not None else DEFAULT_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    days = max(1, min(MAX_DAYS, days))
    now = time.time()
    expires = now + days * 86400
    if until:
        expires = min(expires, float(until))
    row = {"id": uuid.uuid4().hex[:12], "title": title, "body": body,
           "kind": kind if kind in KINDS else "announcement",
           "created_at": now, "expires_at": expires, "by": by or "",
           "campaign_id": str(campaign_id or "")[:32]}
    rows = _live(_load(), now)
    rows.append(row)
    _save(rows)
    log.info("announcement_sent", id=row["id"], by=by, days=days, kind=row["kind"])
    return row


def retire(aid: str) -> bool:
    rows = _load()
    kept = [r for r in rows if r.get("id") != aid]
    if len(kept) == len(rows):
        return False
    _save(_live(kept, time.time()))
    log.info("announcement_retired", id=aid)
    return True


def get(aid: str) -> dict | None:
    return next((r for r in _load() if r.get("id") == aid), None)


def active(now: float | None = None) -> list[dict]:
    """Every announcement still showing, newest first."""
    rows = _live(_load(), now if now is not None else time.time())
    return sorted(rows, key=lambda r: r.get("created_at", 0), reverse=True)


def for_user(user: dict | None, now: float | None = None) -> list[dict]:
    """What THIS person should still see: active, minus what they dismissed.
    Oldest first, so the modal shows announcements in the order they were
    sent when more than one is waiting."""
    seen = set((user or {}).get("announcements_seen") or [])
    rows = [r for r in active(now) if r.get("id") not in seen]
    return sorted(rows, key=lambda r: r.get("created_at", 0))
