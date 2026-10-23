"""Campaign requests: the streamer side of the marketplace (owner, 2026-10-23).

"lets start creating the other side of the marketplace where streamers can come
and submit campaigns for users to go and clip. I basically would charge a
streamer a flat fee to run their campaign on our platform and we would take a
small percent like 2-5% added on as a fee ... let the streamers be able to
request a campaign for their streams after they sign up with an account (which
they can do for free) ... they can also choose the amount of money they want to
put into the campaign."

THE LIFE OF A REQUEST

    submitted ──approve──> approved ──paid (Stripe)──> published
      │  ^                    │
      │  └─resubmit── changes_requested <─┘   (an admin, before payment)
      ├──decline──> declined                   (an admin)
      └──withdraw──> cancelled                 (the streamer, before payment)

NOBODY PAYS FOR A CAMPAIGN WE WOULD NOT RUN. Payment comes after an admin has
approved the request, so a declined one never needs a refund. Paying is what
publishes it: the request becomes an ordinary campaign (src/campaigns), shown
in the marketplace clippers already use from its start date. An admin can also
publish one without payment (a partner, a test), which is recorded as comped.

THE MONEY. The budget IS the prize pool and goes to clippers in full. The fee
is added on top: CAMPAIGN_FLAT_FEE_USD plus CAMPAIGN_FEE_PCT of the budget.
The quote is fixed when the request is submitted, so a fee change never moves
a price somebody has already seen; resubmitting after an edit re-quotes it.
Everything is computed in whole cents, and a Stripe payment whose amount is
not the quote's total to the cent does not publish anything.

STORE. One JSON file (campaign_requests.json) and an optional picture per
request under campaign_request_images/, held in memory and written atomically,
like campaigns.json beside it.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

import structlog

from config.settings import settings
from src import campaigns as camp
from src.auth._jsonstore import atomic_write_json

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "campaign_requests.json"
_IMAGES = Path(settings.local_storage_path) / "campaign_request_images"

SUBMITTED, CHANGES, APPROVED = "submitted", "changes_requested", "approved"
PUBLISHED, DECLINED, CANCELLED = "published", "declined", "cancelled"
OPEN = (SUBMITTED, CHANGES, APPROVED)          # still in play
EDITABLE = (SUBMITTED, CHANGES)                # the streamer may change it

MAX_OPEN_PER_USER = 5
# Every request emails the owner, so sending and withdrawing in a loop must
# not be a way to flood the inbox.
MAX_SENT_PER_DAY = 10
MAX_STREAMERS = 5
MAX_PLACES = 20
MIN_PLACE = 1.00                               # dollars: no 10-cent prizes
BRIEF_MAX, RULES_MAX, NOTE_MAX = 4000, 8000, 2000
DAYS_MIN, DAYS_MAX = 1, 60
START_LEAD_MAX_DAYS = 180
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")

_requests: dict[str, dict] = {}
_loaded = False


class RequestError(camp.CampaignError):
    """Something the person has to fix, or an action this request's status does
    not allow. The message is shown to them as it is."""


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
        log.error("campaign_requests_file_unreadable", path=str(_INDEX))
        return
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and re.match(r"^[a-f0-9]{32}$", str(r.get("id", ""))):
            _requests[r["id"]] = r


def _save() -> None:
    atomic_write_json(_INDEX, list(_requests.values()))


# ── the price ────────────────────────────────────────────────────────────────

def _cents(v) -> int:
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        raise RequestError("The budget must be a number.")


def pricing() -> dict:
    """What the request form and /promote need to show a price."""
    return {"flat_fee": round(settings.campaign_flat_fee_usd, 2),
            "fee_pct": settings.campaign_fee_pct,
            "min_budget": round(settings.campaign_min_budget_usd, 2),
            "max_budget": round(settings.campaign_max_budget_usd, 2)}


def quote(budget) -> dict:
    """Budget + flat fee + percentage fee, in cents and in dollars. The
    percentage is rounded half up to the cent, from whole cents."""
    b = _cents(budget)
    flat = max(0, int(round(settings.campaign_flat_fee_usd * 100)))
    bp = max(0, int(round(settings.campaign_fee_pct * 100)))       # basis points
    pct = (b * bp + 5000) // 10000
    total = b + flat + pct
    return {"budget": b / 100, "flat_fee": flat / 100, "fee_pct": settings.campaign_fee_pct,
            "percent_fee": pct / 100, "fees": (flat + pct) / 100, "total": total / 100,
            "budget_cents": b, "flat_fee_cents": flat, "percent_fee_cents": pct,
            "total_cents": total}


# ── cleaning what the streamer typed ─────────────────────────────────────────

def _text(v, limit: int) -> str:
    return str(v or "").strip()[:limit]


def _owns(user: dict, streamers: list[dict]) -> bool:
    """Whether the account signed in with the channel it is asking about —
    shown to the admin as Verified owner. Not required: a manager or an agency
    may ask for a streamer."""
    mine = {("twitch", str(user.get("twitch_login") or "").lower()),
            ("kick", str(user.get("kick_slug") or "").lower())}
    return any((s["platform"], s["name"].lower()) in mine for s in streamers)


def clean(raw: dict, user: dict, now: float | None = None) -> dict:
    """The request's fields from the form, validated. Raises RequestError."""
    now = time.time() if now is None else now
    if not isinstance(raw, dict):
        raise RequestError("Send the request as an object.")
    try:
        streamers = camp._streamers(raw.get("streamers"))
    except camp.CampaignError as exc:
        raise RequestError(str(exc))
    if not streamers:
        raise RequestError("Name the channel you want clipped.")
    if len(streamers) > MAX_STREAMERS:
        raise RequestError(f"Up to {MAX_STREAMERS} channels in one campaign.")

    title = _text(raw.get("title"), camp.TITLE_MAX)
    if not title:
        raise RequestError("Give the campaign a name.")
    summary = _text(raw.get("summary"), camp.SUMMARY_MAX)
    if not summary:
        raise RequestError("Add a one-line description for the campaign card.")
    brief = _text(raw.get("brief"), BRIEF_MAX)
    if len(brief) < 20:
        raise RequestError("Tell clippers what to look for: a few sentences at least.")
    rules = _text(raw.get("rules"), RULES_MAX)

    p = pricing()
    b = _cents(raw.get("budget"))
    if b < round(p["min_budget"] * 100):
        raise RequestError(f"The smallest budget is ${p['min_budget']:,.2f}.")
    if b > round(p["max_budget"] * 100):
        raise RequestError(f"The largest budget is ${p['max_budget']:,.2f}. "
                           "Get in touch for a bigger campaign.")
    places = [_cents(x) for x in (raw.get("payouts") or [])]
    if not places:
        raise RequestError("Choose how the prize money is split.")
    if len(places) > MAX_PLACES:
        raise RequestError(f"Up to {MAX_PLACES} winning places.")
    if any(x < MIN_PLACE * 100 for x in places):
        raise RequestError(f"Every prize must be at least ${MIN_PLACE:,.2f}.")
    if sum(places) != b:
        raise RequestError(f"The prizes add up to ${sum(places) / 100:,.2f}, not the "
                           f"${b / 100:,.2f} budget. All of the budget goes to winners.")

    judging = raw.get("judging") if raw.get("judging") in camp.JUDGING else "top_clip_views"
    try:
        n = max(1, min(20, int(raw.get("judging_n") or 3)))
    except (TypeError, ValueError):
        n = 3
    note = _text(raw.get("judging_note"), 1000)
    if judging == "custom" and not note:
        raise RequestError("Describe how you want the winners picked.")

    start_mode = "date" if raw.get("start_mode") == "date" else "asap"
    start_at = 0.0
    if start_mode == "date":
        try:
            start_at = float(raw.get("start_at") or 0)
        except (TypeError, ValueError):
            start_at = 0.0
        if start_at <= now:
            raise RequestError("Pick a start date in the future, or start as soon as it is approved.")
        if start_at > now + START_LEAD_MAX_DAYS * 86400:
            raise RequestError(f"Start within the next {START_LEAD_MAX_DAYS} days.")
    try:
        days = int(raw.get("duration_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if not DAYS_MIN <= days <= DAYS_MAX:
        raise RequestError(f"Run it for {DAYS_MIN} to {DAYS_MAX} days.")

    platforms = [x for x in (raw.get("platforms") or []) if x in camp.PLATFORMS]
    if not platforms:
        raise RequestError("Pick at least one place clips get posted.")
    try:
        hashtags, mentions = camp._tags(raw.get("hashtags")), camp._handles(raw.get("mentions"))
    except camp.CampaignError as exc:
        raise RequestError(str(exc))

    email = _text(raw.get("contact_email"), 254) or str(user.get("email") or "").strip()
    if not _EMAIL_RE.match(email):
        raise RequestError("Add an email address we can reach you at.")
    if not raw.get("agree"):
        raise RequestError("Confirm that you can promote this channel and agree to the campaign terms.")

    return {
        "streamers": streamers, "title": title, "summary": summary, "brief": brief, "rules": rules,
        "budget": b / 100, "payouts": [x / 100 for x in places],
        "judging": judging, "judging_n": n, "judging_note": note,
        "start_mode": start_mode, "start_at": start_at, "duration_days": days,
        "platforms": platforms, "hashtags": hashtags, "mentions": mentions,
        "caption_template": _text(raw.get("caption_template"), camp.CAPTION_MAX),
        "contact_email": email, "contact_discord": _text(raw.get("contact_discord"), 64),
    }


# ── reading ──────────────────────────────────────────────────────────────────

def get(request_id: str) -> dict | None:
    _load()
    return _requests.get(request_id or "")


def _newest(rows) -> list[dict]:
    return sorted(rows, key=lambda r: r.get("created_at", 0), reverse=True)


def for_user(user_id: str) -> list[dict]:
    _load()
    return _newest(r for r in _requests.values() if r.get("user_id") == user_id)


def all_rows() -> list[dict]:
    _load()
    return _newest(_requests.values())


_ADMIN_ONLY = ("image", "history", "reviewed_by", "checkout_session_id")


def public(r: dict, now: float | None = None, admin: bool = False) -> dict:
    """A request as its owner sees it; `admin` adds who reviewed it, the
    Stripe session and the history."""
    d = {k: v for k, v in r.items() if k not in _ADMIN_ONLY}
    if admin:
        d.update(history=history(r), reviewed_by=r.get("reviewed_by", ""),
                 checkout_session_id=r.get("checkout_session_id", ""))
    d["image_url"] = (f"/campaign-requests/{r['id']}/image?v={int(r.get('updated_at') or 0)}"
                      if r.get("image") else "")
    d["judging_text"] = camp.JUDGING.get(r.get("judging"), "").format(n=r.get("judging_n") or 3)
    c = camp.get(r.get("campaign_id") or "")
    d["campaign"] = ({"id": c["id"], "status": camp.status_of(c, now), "start_at": c["start_at"],
                      "end_at": c["end_at"], "published": bool(c.get("published"))} if c else None)
    return d


def history(r: dict) -> list[dict]:
    return list(r.get("history") or [])


# ── writing ──────────────────────────────────────────────────────────────────

def _log(r: dict, status: str, by: str, note: str = "") -> None:
    now = time.time()
    r["status"], r["updated_at"] = status, now
    r.setdefault("history", []).append({"at": now, "status": status, "by": by, "note": note[:NOTE_MAX]})


def _mine(request_id: str, user_id: str) -> dict:
    r = get(request_id)
    if not r or r.get("user_id") != user_id:
        raise KeyError(request_id)
    return r


def submit(user: dict, raw: dict) -> dict:
    _load()
    open_now = [r for r in _requests.values()
                if r.get("user_id") == user["id"] and r.get("status") in OPEN]
    if len(open_now) >= MAX_OPEN_PER_USER:
        raise RequestError(f"You have {len(open_now)} requests waiting already. Finish or "
                           "withdraw one before asking for another.")
    now = time.time()
    sent_today = sum(1 for r in _requests.values()
                     if r.get("user_id") == user["id"] and r.get("created_at", 0) > now - 86400)
    if sent_today >= MAX_SENT_PER_DAY:
        raise RequestError("That is a lot of requests for one day. Edit one you have "
                           "already sent, or try again tomorrow.")
    fields = clean(raw, user)
    r = {"id": uuid.uuid4().hex, "user_id": user["id"], "status": SUBMITTED,
         "created_at": now, "updated_at": now, "submitted_at": now, **fields,
         "quote": quote(fields["budget"]), "verified_owner": _owns(user, fields["streamers"]),
         "image": "", "admin_note": "", "reviewed_at": 0, "reviewed_by": "",
         "checkout_session_id": "", "paid_at": 0, "amount_paid_cents": 0, "payment": "",
         "campaign_id": "", "history": []}
    _log(r, SUBMITTED, user["id"])
    _requests[r["id"]] = r
    _save()
    return r


def edit(request_id: str, user: dict, raw: dict) -> dict:
    r = _mine(request_id, user["id"])
    if r["status"] not in EDITABLE:
        raise RequestError("This request can no longer be changed.")
    fields = clean(raw, user)
    r.update(fields)
    r["quote"] = quote(fields["budget"])
    r["verified_owner"] = _owns(user, fields["streamers"])
    r["submitted_at"] = time.time()
    _log(r, SUBMITTED, user["id"], "edited")
    _save()
    return r


def withdraw(request_id: str, user_id: str) -> dict:
    r = _mine(request_id, user_id)
    if r["status"] not in OPEN:
        raise RequestError("This request can no longer be withdrawn.")
    _log(r, CANCELLED, user_id)
    _save()
    return r


def _admin(request_id: str, allowed: tuple, status: str, admin_id: str, note: str,
           need_note: bool) -> dict:
    r = get(request_id)
    if not r:
        raise KeyError(request_id)
    note = _text(note, NOTE_MAX)
    if r["status"] not in allowed:
        raise RequestError(f"This request is {r['status'].replace('_', ' ')}; that cannot be done now.")
    if need_note and not note:
        raise RequestError("Say why, so the streamer knows what to do.")
    r["admin_note"], r["reviewed_at"], r["reviewed_by"] = note, time.time(), admin_id
    _log(r, status, admin_id, note)
    _save()
    return r


def approve(request_id: str, admin_id: str, note: str = "") -> dict:
    return _admin(request_id, (SUBMITTED, CHANGES), APPROVED, admin_id, note, False)


def request_changes(request_id: str, admin_id: str, note: str) -> dict:
    return _admin(request_id, (SUBMITTED, APPROVED), CHANGES, admin_id, note, True)


def decline(request_id: str, admin_id: str, note: str) -> dict:
    return _admin(request_id, OPEN, DECLINED, admin_id, note, True)


def set_checkout(request_id: str, session_id: str) -> dict:
    r = get(request_id)
    if not r:
        raise KeyError(request_id)
    r["checkout_session_id"] = session_id
    r["updated_at"] = time.time()
    _save()
    return r


def campaign_form(r: dict, now: float | None = None) -> dict:
    """The campaign this request becomes, in the shape campaigns.create takes.
    "As soon as it is approved" starts it now, and so does a start date that
    has already passed while it waited for review or payment; the length
    asked for is kept either way."""
    now = time.time() if now is None else now
    start = now if r.get("start_mode") != "date" or float(r.get("start_at") or 0) <= now \
        else float(r["start_at"])
    parts = ["What to clip\n" + r["brief"]]
    if r.get("rules"):
        parts.append("Rules\n" + r["rules"])
    parts.append("Prizes\nThe whole prize pool is paid out to the winners by Highlightz after "
                 "the campaign ends, by the method shown on this page.")
    return {
        "title": r["title"], "summary": r["summary"], "rules": "\n\n".join(parts),
        "start_at": start, "end_at": start + int(r["duration_days"]) * 86400,
        "streamers": [{"name": s["name"], "platform": s["platform"]} for s in r["streamers"]],
        "prize_pool": r["budget"], "payouts": list(r["payouts"]),
        "judging": r["judging"], "judging_n": r["judging_n"], "judging_note": r["judging_note"],
        "platforms": list(r["platforms"]), "hashtags": list(r["hashtags"]),
        "mentions": list(r["mentions"]), "caption_template": r.get("caption_template", ""),
        "published": True, "request_id": r["id"],
    }


def publish(request_id: str, *, payment: str, by: str = "", amount_cents: int = 0,
            session_id: str = "") -> tuple[dict, dict | None]:
    """Make the campaign. `payment` is "stripe" (an approved request, paid the
    quote's total to the cent) or "comped" (an admin, no payment). Returns
    (request, campaign); publishing one already published returns it as it is,
    so the webhook and the redirect back from Stripe can both arrive."""
    r = get(request_id)
    if not r:
        raise KeyError(request_id)
    if r["status"] == PUBLISHED:
        return r, camp.get(r.get("campaign_id") or "")
    if payment == "stripe":
        if r["status"] != APPROVED:
            raise RequestError(f"Paid while {r['status']}: not published. Check it by hand.")
        if int(amount_cents) != int(r["quote"]["total_cents"]):
            raise RequestError(f"Paid {int(amount_cents)} cents against a quote of "
                               f"{r['quote']['total_cents']}: not published. Check it by hand.")
    elif payment == "comped":
        if r["status"] not in OPEN:
            raise RequestError("Only an open request can be published.")
    else:
        raise ValueError(payment)
    c = camp.create(campaign_form(r))
    if r.get("image"):
        try:
            camp.set_image(c["id"], (_IMAGES / r["image"]).read_bytes())
            c = camp.get(c["id"])
        except (OSError, camp.CampaignError) as exc:
            log.warning("campaign_request_image_not_copied", request=r["id"], error=str(exc))
    now = time.time()
    r.update(campaign_id=c["id"], payment=payment, paid_at=now if payment == "stripe" else 0,
             amount_paid_cents=int(amount_cents) if payment == "stripe" else 0)
    if session_id:
        r["checkout_session_id"] = session_id
    _log(r, PUBLISHED, by or r["user_id"], payment)
    _save()
    return r, c


def delete_unpaid_for_user(user_id: str) -> list[str]:
    """An account being deleted: every request of theirs that was never
    published goes, picture and all. A published one stays — it is the record
    of a payment and of prizes still owed — and so do its contact details, for
    that alone (Privacy Policy, Sections 1 and 4). Returns the Checkout sessions
    of the requests removed, for the caller to close."""
    _load()
    gone = [r for r in _requests.values()
            if r.get("user_id") == user_id and r.get("status") != PUBLISHED]
    for r in gone:
        if r.get("image"):
            (_IMAGES / r["image"]).unlink(missing_ok=True)
        del _requests[r["id"]]
    if gone:
        _save()
    return [r["checkout_session_id"] for r in gone if r.get("checkout_session_id")]


# ── the picture ──────────────────────────────────────────────────────────────

def set_image(request_id: str, user_id: str, data: bytes) -> dict:
    r = _mine(request_id, user_id)
    if r["status"] not in EDITABLE:
        raise RequestError("This request can no longer be changed.")
    try:
        ext = camp.image_ext(data)
    except camp.CampaignError as exc:
        raise RequestError(str(exc))
    _IMAGES.mkdir(parents=True, exist_ok=True)
    if r.get("image"):
        (_IMAGES / r["image"]).unlink(missing_ok=True)
    name = f"{request_id}.{ext}"
    tmp = _IMAGES / (name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(_IMAGES / name)
    r["image"], r["updated_at"] = name, time.time()
    _save()
    return r


def image_path(request_id: str) -> Path | None:
    r = get(request_id)
    if not r or not r.get("image"):
        return None
    p = _IMAGES / r["image"]
    return p if p.is_file() else None
