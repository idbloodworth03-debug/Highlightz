"""Campaign results: the leaderboard, the winners and their payouts.

Owner, 2026-10-23: "lets add 3 and 4", that is a live leaderboard for every
campaign, and the tools to pick winners and pay them.

WHERE THE NUMBERS COME FROM. An entry is a clip posted for the campaign
through Highlightz, one per platform. The poster records each one here the
moment it lands (record_entry), so it stays an entry when its owner tidies the
posting queue or deletes the clip. Posts still on their way (and any that
landed before entries were recorded) are read from the queue: the schedule
items carrying the campaign's id (src/publish/schedule.for_campaign). Its
views, likes and comments are entered by an admin from the post itself, until
the platforms' own numbers are wired in (YouTube can be read with an API key;
TikTok needs the video.list permission and Instagram views the insights one,
both app reviews). An entry nobody has counted yet scores nothing.

WHAT COUNTS. An entry counts when it was posted, on a platform the campaign
names, inside its dates, and has not been disqualified. A clipper's score
follows the campaign's judging rule:
  top_clip_views     the views of their best clip
  best_n_views       the views of their best N clips, added up
  best_n_engagement  views + likes + comments of their best N clips
  custom             no rule: ordered by total views as a guide, and the
                     winners are chosen by hand
A tie goes to whoever posted first.

WINNERS. Once a campaign has ended an admin starts from the standings (one
clipper per prize place, in order), changes any place, and confirms. The
confirmed list is a snapshot: names and amounts stay even if an entry or an
account goes later. Places left empty are the unwon prizes the Terms (4A)
promise back to the streamer. Counts are frozen while winners stand; reopening
them (only before any prize is marked paid) unfreezes them.

PAYOUTS. Each winner says where to be paid (PayPal, Venmo, Cash App, a bank
transfer or other) on the campaign page; an admin marks each prize paid with
the method and a reference. The money moves outside Highlightz.

PRIVACY. Clippers see names and scores, never another clipper's links, id or
payout details. The streamer sees the top clips with their links: it is their
campaign. Payout details are seen by the winner and admins only. An account's
entries, counts and payout details go with it; confirmed winners stay, as the
record of what was won and paid.

STORE. campaign_results.json: {campaign id: {"entries": {entry key: {...}},
"stats": {entry key: {...}}, "winners": [...], "payees": {user id: {...}},
"confirmed_at", "confirmed_by", "refund": {...}}}; an entry key is
"<schedule item id>:<platform>".
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path

import structlog

from config.settings import settings
from src import campaigns as camp
from src.auth._jsonstore import atomic_write_json

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "campaign_results.json"

PAYOUT_METHODS = {"paypal": "PayPal", "venmo": "Venmo", "cashapp": "Cash App",
                  "bank": "Bank transfer", "other": "Other"}
MAX_COUNT = 10 ** 12
NOTE_MAX, PAYEE_MAX, REF_MAX = 300, 200, 200

_results: dict[str, dict] = {}
_loaded = False


class ResultsError(camp.CampaignError):
    """Something the admin or the winner has to fix; shown to them as it is."""


# ── load / save ──────────────────────────────────────────────────────────────

def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_INDEX.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception:
        log.error("campaign_results_file_unreadable", path=str(_INDEX))
        return
    if isinstance(data, dict):
        _results.update({k: v for k, v in data.items() if isinstance(v, dict)})


def _save() -> None:
    atomic_write_json(_INDEX, _results)


def _peek(campaign_id: str) -> dict:
    """The campaign's record, or an empty one. Never creates one."""
    _load()
    return _results.get(campaign_id) or {}


def _rec(campaign_id: str) -> dict:
    """The campaign's record, created if missing. For writers only."""
    _load()
    rec = _results.setdefault(campaign_id, {})
    for k, v in (("entries", {}), ("stats", {}), ("winners", []), ("payees", {}), ("confirmed_at", 0),
                 ("confirmed_by", ""), ("refund", {})):
        rec.setdefault(k, v)
    return rec


def _who(user_id: str) -> str | None:
    """The name a clipper is shown by, or None for an account that is gone."""
    from src.auth import users as user_store
    u = user_store.get_by_id(user_id)
    if not u:
        return None
    return u.get("twitch_login") or u.get("kick_slug") or u.get("username") or "a clipper"


def _name(user_id: str) -> str:
    return _who(user_id) or "a clipper"


# ── entries and standings ────────────────────────────────────────────────────

def record_entry(item, platform: str) -> None:
    """A post that landed for its campaign (called by the poster)."""
    r = (item.results or {}).get(platform) or {}
    rec = _rec(item.campaign_id)
    rec["entries"][f"{item.id}:{platform}"] = {
        "item_id": item.id, "user_id": item.user_id, "platform": platform,
        "url": r.get("url") or "", "remote_id": r.get("remote_id") or "",
        "at": float(r.get("at") or time.time())}
    _save()


def entries(c: dict) -> list[dict]:
    """Every clip posted (or on its way) for the campaign, with its counts and
    whether it counts. Oldest first."""
    from src.publish import schedule as sched
    rec = _peek(c["id"])
    stats, kept = rec.get("stats") or {}, rec.get("entries") or {}
    found = [(key, e["item_id"], e["user_id"], e["platform"],
              {"status": sched.R_POSTED, "url": e.get("url"), "at": e.get("at")})
             for key, e in kept.items()]
    for it in sched.for_campaign(c["id"]):
        for plat in it.platforms:
            key = f"{it.id}:{plat}"
            r = (it.results or {}).get(plat) or {}
            # Recorded already, or posted for another campaign before this
            # item was moved to this one.
            if key in kept or r.get("campaign_id", c["id"]) != c["id"]:
                continue
            found.append((key, it.id, it.user_id, plat, r))
    names: dict[str, str | None] = {}
    out = []
    for key, item_id, uid, plat, r in found:
        if uid not in names:
            names[uid] = _who(uid)
        if names[uid] is None:
            continue                # the account is gone, and its entries with it
        s = stats.get(key) or {}
        posted = r.get("status") == sched.R_POSTED
        at = float(r.get("at") or 0) if posted else 0.0
        in_dates = posted and c["start_at"] <= at <= c["end_at"]
        on_platform = plat in (c.get("platforms") or [])
        dq = bool(s.get("disqualified"))
        why = ("not posted" if not posted else "posted outside the campaign dates" if not in_dates
               else "platform not in this campaign" if not on_platform
               else "disqualified" + (": " + s["reason"] if s.get("reason") else "") if dq else "")
        out.append({
            "key": key, "item_id": item_id, "user_id": uid, "name": names[uid],
            "platform": plat, "url": r.get("url") or "", "posted_at": at,
            "views": s.get("views"), "likes": s.get("likes"), "comments": s.get("comments"),
            "counted": s.get("views") is not None, "counted_at": s.get("at", 0),
            "disqualified": dq, "reason": s.get("reason", ""),
            "eligible": not why, "why_not": why,
        })
    out.sort(key=lambda e: (e["posted_at"] or float("inf"), e["key"]))
    return out


def metric(c: dict) -> str:
    return "engagement" if c.get("judging") == "best_n_engagement" else "views"


def _score(c: dict, e: dict) -> int:
    v = int(e["views"] or 0)
    if metric(c) == "engagement":
        v += int(e["likes"] or 0) + int(e["comments"] or 0)
    return v


def standings(c: dict, ents: list[dict] | None = None) -> list[dict]:
    """One row per clipper with an entry that counts, best first."""
    ents = entries(c) if ents is None else ents
    by_user: dict[str, list[dict]] = {}
    for e in ents:
        if e["eligible"]:
            by_user.setdefault(e["user_id"], []).append(e)
    method = c.get("judging")
    n = (1 if method == "top_clip_views" else len(ents) if method == "custom"
         else max(1, int(c.get("judging_n") or 3)))
    rows = []
    for uid, es in by_user.items():
        es.sort(key=lambda e: (-_score(c, e), e["posted_at"]))
        best = es[:n]
        rows.append({"user_id": uid, "name": es[0]["name"],
                     "score": sum(_score(c, e) for e in best),
                     "clips": len(es), "counted": sum(1 for e in es if e["counted"]),
                     "first_at": min(e["posted_at"] for e in best),
                     "entries": [e["key"] for e in best]})
    rows.sort(key=lambda r: (-r["score"], r["first_at"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def propose(c: dict, rows: list[dict] | None = None) -> list[dict]:
    """The winners the standings would give: one clipper per prize place."""
    rows = standings(c) if rows is None else rows
    out = []
    for i, amount in enumerate(c.get("payouts") or []):
        r = rows[i] if i < len(rows) else None
        out.append({"place": i + 1, "amount": amount, "user_id": r["user_id"] if r else "",
                    "name": r["name"] if r else "", "score": r["score"] if r else 0})
    return out


# ── counting (admin) ─────────────────────────────────────────────────────────

def _count(v, what: str):
    """An entered count: blank is "not counted", otherwise a whole number
    (commas allowed)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        n = int(str(v).replace(",", "").strip())
    except ValueError:
        raise ResultsError(f"{what} must be a whole number.")
    if not 0 <= n <= MAX_COUNT:
        raise ResultsError(f"{what} must be between 0 and {MAX_COUNT:,}.")
    return n


def save_stats(c: dict, rows: list[dict], admin_id: str) -> int:
    """Counts and disqualifications for many entries at once. Returns how many
    entries changed. Refused while winners stand."""
    if not isinstance(rows, list):
        raise ResultsError("Send the counts as a list.")
    label = {e["key"]: f'{e["name"]} on {e["platform"]}' for e in entries(c)}
    if _peek(c["id"]).get("confirmed_at"):
        raise ResultsError("The winners are confirmed. Reopen them to change the counts.")
    # Every row is checked before any is stored: one bad number must not
    # leave the rows before it half-saved.
    parsed = []
    for row in rows:
        row = row if isinstance(row, dict) else {}
        key = str(row.get("key") or "")
        if key not in label:
            raise ResultsError("That entry is not part of this campaign.")
        try:
            new = {f: _count(row.get(f), f.capitalize()) for f in ("views", "likes", "comments")}
        except ResultsError as exc:
            raise ResultsError(f"{label[key]}: {exc}")
        dq = bool(row.get("disqualified"))
        reason = str(row.get("reason") or "").strip()[:NOTE_MAX] if dq else ""
        if dq and not reason:
            raise ResultsError(f"{label[key]}: say why it is disqualified.")
        parsed.append((key, new, dq, reason))
    rec, changed, now = _rec(c["id"]), 0, time.time()
    for key, new, dq, reason in parsed:
        old = rec["stats"].get(key) or {}
        cur = {f: old.get(f) for f in ("views", "likes", "comments")}
        if cur == new and bool(old.get("disqualified")) == dq and old.get("reason", "") == reason:
            continue
        rec["stats"][key] = {**{f: v for f, v in new.items() if v is not None},
                             **({"disqualified": True, "reason": reason} if dq else {}),
                             "at": now, "by": admin_id}
        changed += 1
    if changed:
        _save()
    return changed


# ── winners and payouts (admin) ──────────────────────────────────────────────

def confirm(c: dict, picks: list[dict], admin_id: str) -> dict:
    """The winners, one clipper per place. A place with no clipper is unwon."""
    places = list(c.get("payouts") or [])
    if not places:
        raise ResultsError("This campaign has no prize places.")
    if camp.status_of(c) != "ended":
        raise ResultsError("Winners are picked once the campaign has ended. To close it early, "
                           "move its end date to now.")
    rec = _rec(c["id"])
    if rec.get("confirmed_at"):
        raise ResultsError("The winners are already confirmed.")
    can_win = {r["user_id"]: r for r in standings(c)}
    taken_places, taken_users, winners = set(), set(), []
    for p in picks if isinstance(picks, list) else []:
        try:
            place = int((p or {}).get("place") or 0)
        except (TypeError, ValueError):
            place = 0
        uid = str((p or {}).get("user_id") or "")
        if not 1 <= place <= len(places):
            raise ResultsError(f"There is no place {place}.")
        if not uid:
            continue
        if place in taken_places:
            raise ResultsError(f"Place {place} has two winners.")
        if uid in taken_users:
            raise ResultsError(f"{_name(uid)} can win one place only.")
        if uid not in can_win:
            raise ResultsError(f"{_name(uid)} has no entry that counts in this campaign.")
        taken_places.add(place)
        taken_users.add(uid)
        r = can_win[uid]
        winners.append({"place": place, "user_id": uid, "name": r["name"], "amount": places[place - 1],
                        "score": r["score"], "entries": list(r["entries"]), "paid": {}})
    winners.sort(key=lambda w: w["place"])
    unwon = round(sum(places) - sum(w["amount"] for w in winners), 2)
    # Owed back only to a streamer who paid for it (Terms 4A). A campaign an
    # admin made, or a request published without payment, owes nobody.
    from src.campaigns import requests as creq
    req = creq.get(c.get("request_id") or "")
    owed = bool(req and req.get("payment") == "stripe")
    rec.update(winners=winners, confirmed_at=time.time(), confirmed_by=admin_id,
               refund={"amount": unwon, "status": "due" if unwon > 0 and owed else "none",
                       "reference": "", "at": 0, "by": ""})
    _save()
    return rec


def reopen(c: dict, admin_id: str) -> dict:
    rec = _rec(c["id"])
    if not rec.get("confirmed_at"):
        raise ResultsError("The winners are not confirmed yet.")
    if any(w.get("paid") for w in rec["winners"]) or (rec.get("refund") or {}).get("status") == "refunded":
        raise ResultsError("A prize or the refund is already marked as sent. Undo that first.")
    rec.update(winners=[], confirmed_at=0, confirmed_by="", refund={})
    _save()
    log.info("campaign_winners_reopened", campaign=c["id"], by=admin_id)
    return rec


def _winner(rec: dict, place: int) -> dict:
    w = next((w for w in rec.get("winners") or [] if w["place"] == place), None)
    if not w or not rec.get("confirmed_at"):
        raise ResultsError(f"No confirmed winner in place {place}.")
    return w


def mark_paid(c: dict, place: int, paid: bool, admin_id: str, method: str = "",
              reference: str = "") -> dict:
    rec = _rec(c["id"])
    w = _winner(rec, place)
    if paid:
        if method not in PAYOUT_METHODS:
            raise ResultsError("Say how the prize was sent.")
        w["paid"] = {"at": time.time(), "by": admin_id, "method": method,
                     "reference": str(reference or "").strip()[:REF_MAX]}
    else:
        w["paid"] = {}
    _save()
    return w


def mark_refunded(c: dict, refunded: bool, admin_id: str, reference: str = "") -> dict:
    rec = _rec(c["id"])
    ref = rec.get("refund") or {}
    if not rec.get("confirmed_at") or ref.get("status") not in ("due", "refunded"):
        raise ResultsError("There is nothing unwon to refund.")
    ref.update(status="refunded" if refunded else "due", at=time.time() if refunded else 0,
               by=admin_id if refunded else "",
               reference=str(reference or "").strip()[:REF_MAX] if refunded else "")
    rec["refund"] = ref
    _save()
    return ref


# ── payout details (the winner) ──────────────────────────────────────────────

def set_payee(c: dict, user_id: str, method: str, address: str) -> dict:
    rec = _peek(c["id"])
    if not rec.get("confirmed_at") or not any(w["user_id"] == user_id for w in rec.get("winners") or []):
        raise ResultsError("Only a winner can say where to send a prize.")
    if method not in PAYOUT_METHODS:
        raise ResultsError("Pick how you want to be paid.")
    address = re.sub(r"\s+", " ", str(address or "")).strip()[:PAYEE_MAX]
    if len(address) < 3:
        raise ResultsError("Add where to send it: your PayPal email, your handle or how to reach you.")
    rec = _rec(c["id"])
    rec["payees"][user_id] = {"method": method, "address": address, "at": time.time()}
    _save()
    return rec["payees"][user_id]


def has_winners(campaign_id: str) -> bool:
    return bool(_peek(campaign_id).get("confirmed_at"))


def drop(campaign_id: str) -> None:
    """A deleted campaign's entries, counts and payout details. The API refuses
    to delete one whose winners are confirmed: that is the record of its prizes."""
    _load()
    if _results.pop(campaign_id, None) is not None:
        _save()


def forget_user(user_id: str) -> bool:
    """An account being deleted: its entries, their counts and its payout
    details go. Confirmed winners stay as the record of what was won and paid.
    True if any campaign's entries changed (its leaderboard is then out of date)."""
    _load()
    changed = boards = False
    for rec in _results.values():
        if (rec.get("payees") or {}).pop(user_id, None) is not None:
            changed = True
        kept = rec.get("entries") or {}
        for key in [k for k, e in kept.items() if e.get("user_id") == user_id]:
            kept.pop(key)
            (rec.get("stats") or {}).pop(key, None)
            changed = boards = True
    if changed:
        _save()
    return boards


# ── what each person sees ────────────────────────────────────────────────────

def _public_winners(rec: dict) -> list[dict]:
    if not rec.get("confirmed_at"):
        return []
    return [{"place": w["place"], "name": w["name"], "amount": w["amount"], "paid": bool(w.get("paid"))}
            for w in rec.get("winners") or []]


def board(c: dict, viewer_id: str, top: int = 10) -> dict:
    """The leaderboard as a clipper sees it: names and scores, their own row,
    and, once winners stand, the winners and the viewer's own prize."""
    rows = standings(c)
    rec = _peek(c["id"])
    stats = rec.get("stats") or {}
    me = next((r for r in rows if r["user_id"] == viewer_id), None)
    prize = None
    mine = next((w for w in rec.get("winners") or [] if w["user_id"] == viewer_id), None) \
        if rec.get("confirmed_at") else None
    if mine:
        paid = mine.get("paid") or {}
        prize = {"place": mine["place"], "amount": mine["amount"],
                 "payee": (rec.get("payees") or {}).get(viewer_id),
                 "paid": {"at": paid["at"], "method": PAYOUT_METHODS.get(paid.get("method"), "")} if paid else None}
    return {
        "metric": metric(c), "custom": c.get("judging") == "custom",
        "updated_at": max((s.get("at", 0) for s in stats.values()), default=0),
        "entrants": len(rows),
        "rows": [{"rank": r["rank"], "name": r["name"], "score": r["score"], "clips": r["clips"],
                  "counted": r["counted"], "you": r["user_id"] == viewer_id} for r in rows[:top]],
        "you": {"rank": me["rank"], "name": me["name"], "score": me["score"], "clips": me["clips"],
                "counted": me["counted"]} if me else None,
        "confirmed": bool(rec.get("confirmed_at")),
        "winners": _public_winners(rec),
        "prize": prize,
    }


def summary(c: dict, top: int = 5) -> dict:
    """For the streamer who funded it (and the admin list): how it is doing,
    its best clips with their links, the winners and any refund due."""
    ents = entries(c)
    ok = [e for e in ents if e["eligible"]]
    rec = _peek(c["id"])
    best = sorted((e for e in ok if e["counted"]), key=lambda e: -int(e["views"] or 0))[:top]
    refund = rec.get("refund") or {}
    owed = bool(rec.get("confirmed_at")) and refund.get("status") in ("due", "refunded")
    return {"entries": len(ok), "clippers": len({e["user_id"] for e in ok}),
            "views": sum(int(e["views"] or 0) for e in ok), "counted": sum(1 for e in ok if e["counted"]),
            "top": [{"name": e["name"], "platform": e["platform"], "url": e["url"], "views": int(e["views"] or 0)}
                    for e in best],
            "confirmed": bool(rec.get("confirmed_at")), "winners": _public_winners(rec),
            "unwon": refund.get("amount", 0) if owed else 0,
            "refund_status": refund.get("status", "") if owed else ""}


def admin_view(c: dict) -> dict:
    ents = entries(c)
    rows = standings(c, ents)
    rec = _peek(c["id"])
    payees = rec.get("payees") or {}
    winners = []
    for w in rec.get("winners") or [] if rec.get("confirmed_at") else []:
        paid = w.get("paid") or {}
        winners.append({**{k: w[k] for k in ("place", "user_id", "name", "amount", "score")},
                        "payee": payees.get(w["user_id"]),
                        "paid": {**paid, "method_label": PAYOUT_METHODS.get(paid.get("method"), "")} if paid else None})
    return {"status": camp.status_of(c), "metric": metric(c), "judging_text": camp.judging_text(c),
            "entries": ents, "standings": rows,
            "proposal": propose(c, rows) if not rec.get("confirmed_at") else [],
            "confirmed_at": rec.get("confirmed_at", 0), "winners": winners,
            "refund": rec.get("refund") or {}, "payout_methods": PAYOUT_METHODS,
            "request_id": c.get("request_id", "")}


def csv_text(c: dict) -> str:
    """Every entry with its counts, then the winners: for a spreadsheet."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["entry", "clipper", "platform", "link", "posted (UTC)", "views", "likes", "comments",
                "counts", "why not"])
    for e in entries(c):
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["posted_at"])) if e["posted_at"] else ""
        w.writerow([e["key"], e["name"], e["platform"], e["url"], when,
                    "" if e["views"] is None else e["views"], "" if e["likes"] is None else e["likes"],
                    "" if e["comments"] is None else e["comments"], "yes" if e["eligible"] else "no", e["why_not"]])
    view = admin_view(c)
    if view["winners"]:
        w.writerow([])
        w.writerow(["place", "winner", "prize (USD)", "paid to", "paid", "method", "reference"])
        for x in view["winners"]:
            payee, paid = x["payee"] or {}, x["paid"] or {}
            w.writerow([x["place"], x["name"], f"{x['amount']:.2f}",
                        (PAYOUT_METHODS.get(payee.get("method"), "") + " " + payee.get("address", "")).strip(),
                        time.strftime("%Y-%m-%d", time.gmtime(paid["at"])) if paid else "",
                        paid.get("method_label", ""), paid.get("reference", "")])
    return buf.getvalue()
