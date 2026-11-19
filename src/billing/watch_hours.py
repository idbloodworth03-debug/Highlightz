"""Free's weekly watching allowance, and the hours people buy on top of it.

Owner, 2026-11-19: "lets get started with the free tiers only getting 5
hours a week and they can pay for more and lets have it be 1$ an hour. Also
make sure we can track this in the admin page and see who buys it etc."

WHAT IS COUNTED. Time a channel holds a LIVE SLOT: the channel is live and
being watched (api.acquire_live_slot / release_live_slot). That is what costs
the server; a listed channel that is offline, paused or waiting for a slot
costs nothing and counts nothing. Several channels live at once count each.
Counted to the second: from when the slot was taken, to when it is given
back, folded in once a minute while it is held (api.watch_hours_task) so a
restart loses at most a minute of it.

THE WEEK. Monday 00:00 UTC to the next. A fixed reset is the one people can
plan around ("4 hours left, resets Monday"); the dashboard shows the moment in
the viewer's own clock.

THE ORDER. The week's free hours first, then bought hours. Bought hours do not
reset and do not expire: they are a balance, used only once the week's free
hours are gone, and the next week starts on free hours again.

WHO. An account whose plan meters watching (plans: Free) while
FREE_WATCH_HOURS_WEEK is above 0. Paid plans, trials and staff are never
counted. Setting it to 0 switches the whole thing off; nothing is lost, the
balances stay on disk.

THE PRICE. PRICE_CENTS a hour, sold in PACKS. Not one hour at a time: a $1
card charge loses about a third to the card fee (2.9% + 30 cents), and $1
checkouts are what card testers use. Every pack is still $1 an hour.

FILES (in LOCAL_STORAGE_PATH):
  watch_hours.json            {user id: {week, free_used_s, paid_left_s,
                               paid_used_s, bought_s}}: balances, rewritten
                               when they change (at most once a minute);
  watch_hour_purchases.jsonl  one line per purchase, appended once: the
                               ledger the admin page reads, and what makes a
                               payment credit hours exactly once.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json, read_json

log = structlog.get_logger(__name__)

PRICE_CENTS = 100                       # $1 an hour (owner, 2026-11-19)
PACKS = (5, 10, 25)                     # hours a checkout may buy; each is $1 an hour
WEEK_S = 7 * 86400
_EPOCH_MONDAY = 345600                  # 1970-01-05 00:00 UTC, a Monday

_FILE = Path(settings.local_storage_path) / "watch_hours.json"
_LEDGER = Path(settings.local_storage_path) / "watch_hour_purchases.jsonl"

_lock = threading.Lock()
_rows: dict[str, dict] | None = None
_dirty = False


# ── who, and how much ───────────────────────────────────────────────────────

def allowance_s() -> int:
    """The week's free watching, in seconds. 0 = the meter is off."""
    try:
        return max(0, int(settings.free_watch_hours_week)) * 3600
    except (TypeError, ValueError, AttributeError):
        return 0


def metered(user: dict | None) -> bool:
    """Is this account's watching counted against an allowance?"""
    if not user or user.get("is_admin") or allowance_s() <= 0:
        return False
    from src.billing.plans import limits_for
    return bool(limits_for(user).get("meters_watching"))


def price_words() -> str:
    """"$1": the price of one hour, as the pages write it."""
    return "$" + format(PRICE_CENTS / 100, "g")


def public_words(form: str = "short") -> str:
    """Free's time allowance as every public page states it. ONE place, so
    FREE_WATCH_HOURS_WEEK=0 puts every page back to "no time limit" at once
    instead of leaving one promising a limit that is not there (or the
    reverse, which is what the pages did the day this shipped).
      short: "5 live hours a week"                       (a strap line)
      long:  "5 hours of live watching a week, then $1 an hour"
    """
    h = allowance_s() // 3600
    if h <= 0:
        return "no time limit"
    unit = "hour" if h == 1 else "hours"
    if form == "short":
        return f"{h} live {unit} a week"
    return f"{h} {unit} of live watching a week, then {price_words()} an hour"


def week_start(now: float | None = None) -> float:
    now = time.time() if now is None else now
    return _EPOCH_MONDAY + ((int(now) - _EPOCH_MONDAY) // WEEK_S) * WEEK_S


# ── the store ───────────────────────────────────────────────────────────────

def _load() -> dict[str, dict]:
    global _rows
    if _rows is None:
        raw = read_json(_FILE, {})
        _rows = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    return _rows


def _row(uid: str, now: float, store: bool = True) -> dict:
    """This account's balances, with the week rolled over if it has turned.
    store=False (every read) hands back a blank row for an account never
    counted WITHOUT keeping it: the admin table reads every Free account
    every 30 seconds, and that must not write a row for each of them."""
    global _dirty
    rows = _load()
    r = rows.get(uid)
    start = week_start(now)
    if r is None:
        r = {"week": start, "free_used_s": 0.0, "paid_left_s": 0.0,
             "paid_used_s": 0.0, "bought_s": 0.0}
        if not store:
            return r
        rows[uid] = r
        _dirty = True
    elif float(r.get("week") or 0) != start:
        r["week"], r["free_used_s"] = start, 0.0
        _dirty = True
    for k in ("free_used_s", "paid_left_s", "paid_used_s", "bought_s"):
        try:
            r[k] = max(0.0, float(r.get(k) or 0))
        except (TypeError, ValueError):
            r[k] = 0.0
    return r


def save_if_dirty() -> bool:
    """Write the balances if anything changed. True if written."""
    global _dirty
    with _lock:
        if not _dirty or _rows is None:
            return False
        try:
            atomic_write_json(_FILE, _rows)
        except Exception as exc:                 # bookkeeping must never stop watching
            log.warning("watch_hours_save_failed", error=str(exc)[:160])
            return False
        _dirty = False
        return True


def left_s(uid: str, user: dict | None, now: float | None = None) -> float:
    """Seconds this account may still watch. A large number when it is not
    metered at all."""
    if not metered(user):
        return float("inf")
    now = time.time() if now is None else now
    with _lock:
        r = _row(uid, now, store=False)
        return max(0.0, allowance_s() - r["free_used_s"]) + r["paid_left_s"]


def consume(uid: str, user: dict | None, seconds: float, now: float | None = None) -> float:
    """Count `seconds` of watching. Free hours first, then bought ones.
    Returns what is left. Nothing happens for an account that is not metered."""
    global _dirty
    if not metered(user) or seconds <= 0:
        return left_s(uid, user, now)
    now = time.time() if now is None else now
    with _lock:
        r = _row(uid, now)
        free_room = max(0.0, allowance_s() - r["free_used_s"])
        from_free = min(free_room, seconds)
        r["free_used_s"] += from_free
        rest = seconds - from_free
        if rest > 0:
            from_paid = min(r["paid_left_s"], rest)
            r["paid_left_s"] -= from_paid
            r["paid_used_s"] += from_paid
        _dirty = True
        return max(0.0, allowance_s() - r["free_used_s"]) + r["paid_left_s"]


def snapshot(uid: str, user: dict | None, now: float | None = None) -> dict | None:
    """What the dashboard shows. None for an account that is not metered."""
    if not metered(user):
        return None
    now = time.time() if now is None else now
    with _lock:
        r = _row(uid, now, store=False)
        free_left = max(0.0, allowance_s() - r["free_used_s"])
        return {"free_s": allowance_s(), "free_left_s": round(free_left),
                "paid_left_s": round(r["paid_left_s"]), "left_s": round(free_left + r["paid_left_s"]),
                "used_week_s": round(r["free_used_s"]), "resets_at": week_start(now) + WEEK_S,
                "price_cents": PRICE_CENTS, "packs": list(PACKS)}


def all_rows(now: float | None = None) -> dict[str, dict]:
    """Every account's balances, rolled to this week (the admin page)."""
    now = time.time() if now is None else now
    with _lock:
        return {uid: dict(_row(uid, now)) for uid in list(_load())}


# ── buying ──────────────────────────────────────────────────────────────────

def valid_pack(hours) -> int | None:
    try:
        h = int(hours)
    except (TypeError, ValueError):
        return None
    return h if h in PACKS else None


def purchases() -> list[dict]:
    """Every purchase, oldest first."""
    out = []
    try:
        with open(_LEDGER, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("session_id"):
                    out.append(d)
    except OSError:
        pass
    return out


def credit(uid: str, hours: int, *, session_id: str, amount_cents: int,
           now: float | None = None, via: str = "") -> bool:
    """Add bought hours, ONCE per Checkout session: the webhook and the
    redirect back both call this, and either may come first or twice.
    True if the hours were added now, False if this session already had."""
    global _dirty
    if not uid or not session_id or int(hours) <= 0:
        return False
    now = time.time() if now is None else now
    with _lock:
        if any(p.get("session_id") == session_id for p in purchases()):
            return False
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(_LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now, "user_id": uid, "hours": int(hours),
                                "amount_cents": int(amount_cents), "session_id": session_id,
                                "via": via}) + "\n")
        r = _row(uid, now)
        r["paid_left_s"] += int(hours) * 3600
        r["bought_s"] += int(hours) * 3600
        _dirty = True
    save_if_dirty()
    log.info("watch_hours_bought", user_id=uid, hours=int(hours), amount_cents=int(amount_cents), via=via)
    return True


def forget_user(uid: str) -> bool:
    """Account deleted: its balance goes. The purchase lines stay, as the
    payment record (Privacy Policy, like a paid campaign). True if a row went."""
    global _dirty
    with _lock:
        if _load().pop(str(uid), None) is None:
            return False
        _dirty = True
    save_if_dirty()
    return True


def _reset_for_tests(root: Path | None = None) -> None:
    global _rows, _dirty, _FILE, _LEDGER
    _rows, _dirty = None, False
    if root is not None:
        _FILE, _LEDGER = root / "watch_hours.json", root / "watch_hour_purchases.jsonl"
