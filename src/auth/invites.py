"""Invite links: hand someone a membership without ever showing them a price.

WHY THIS EXISTS. Comping a user meant telling them to sign in and then granting
them from the admin panel — and the sign-in page they landed on advertised
"From $10/month, cancel anytime". Being promised free access and then shown a
price above a Connect-your-Twitch button reads as a scam, because that is the
exact shape of one. There was no way to say "here is your access" that did not
route the person past a payment message first.

An invite is a link. The recipient clicks it, signs in with Twitch, and is
already on the plan when the dashboard loads. Nothing about billing is shown at
any point, and the only thing the sender has to say is "here's your link".

DESIGN NOTES

  * SINGLE USE BY DEFAULT. A link that grants Pro forever to everyone who sees
    it is one screenshot away from being public. `max_uses` can be raised
    deliberately (a creator handing the same link to their mods), never by
    accident.
  * EXPIRES BY DEFAULT. A leaked link stops working. The invite's own expiry is
    separate from the membership DURATION it grants — a 7-day link can grant a
    permanent plan, and a link valid for a month can grant a week of Starter.
  * CODES ARE UNGUESSABLE. secrets.token_urlsafe, not a counter or a slug: this
    is a bearer credential for a paid product.
  * CLAIMS ARE RECORDED. Who redeemed it and when, so a link handed out in a DM
    can be traced back to the account it created.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "invites.json"

DEFAULT_TTL_DAYS = 30
CODE_BYTES = 9              # ~12 url-safe characters
NOTE_MAX = 80


@dataclass
class Invite:
    code: str
    plan: str                    # "starter" | "pro"
    days: int                    # 0 = no end date
    note: str = ""               # who it is for, for the sender's own memory
    max_uses: int = 1
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0      # 0 = never
    claims: list[dict] = field(default_factory=list)   # [{user_id, username, at}]

    def uses_left(self) -> int:
        return max(0, self.max_uses - len(self.claims))

    def is_expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.expires_at) and now >= self.expires_at

    def is_live(self, now: float | None = None) -> bool:
        return self.uses_left() > 0 and not self.is_expired(now)

    def public(self, now: float | None = None) -> dict:
        d = asdict(self)
        # Derived, never stored — a stored "is usable" flag goes wrong the
        # moment the clock passes expires_at with nothing running.
        d["uses_left"] = self.uses_left()
        d["expired"] = self.is_expired(now)
        d["live"] = self.is_live(now)
        return d


_invites: dict[str, Invite] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not _INDEX.exists():
        return
    try:
        raw = json.loads(_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("invites_index_unreadable", path=str(_INDEX))
        return
    for r in raw:
        try:
            _invites[r["code"]] = Invite(**r)
        except (TypeError, KeyError):
            continue        # a shape change must not take every invite down


def _save() -> None:
    _INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(i) for i in _invites.values()], indent=2),
                   encoding="utf-8")
    os.replace(tmp, _INDEX)      # atomic: never leave a half-written index


def create(plan: str, days: int = 0, note: str = "", max_uses: int = 1,
           ttl_days: int = DEFAULT_TTL_DAYS, created_by: str = "") -> Invite:
    _load()
    if max_uses < 1:
        raise ValueError("An invite has to be usable at least once.")
    if days < 0:
        raise ValueError("Duration cannot be negative.")
    inv = Invite(
        code=secrets.token_urlsafe(CODE_BYTES),
        plan=plan,
        days=int(days),
        note=(note or "").strip()[:NOTE_MAX],
        max_uses=int(max_uses),
        created_by=created_by,
        expires_at=time.time() + ttl_days * 86400 if ttl_days else 0.0,
    )
    _invites[inv.code] = inv
    _save()
    log.info("invite_created", code=inv.code, plan=plan, days=days,
             max_uses=max_uses, by=created_by)
    return inv


def get(code: str) -> Invite | None:
    _load()
    return _invites.get(code or "")


def all_invites() -> list[Invite]:
    _load()
    return sorted(_invites.values(), key=lambda i: -i.created_at)


def revoke(code: str) -> bool:
    _load()
    if code not in _invites:
        return False
    _invites.pop(code)
    _save()
    return True


def claim(code: str, user_id: str, username: str = "",
          now: float | None = None) -> Invite | None:
    """Spend one use. Returns the invite if the claim succeeded, else None.

    Re-claiming by the SAME user is a no-op that still succeeds: clicking the
    link twice, or having it reopened from browser history, must not burn a use
    or fail confusingly — but it also must not hand out a second membership.
    """
    _load()
    inv = _invites.get(code or "")
    if not inv:
        return None
    now = time.time() if now is None else now
    if any(c.get("user_id") == user_id for c in inv.claims):
        return inv                      # already theirs; nothing to spend
    if not inv.is_live(now):
        return None
    inv.claims.append({"user_id": user_id, "username": username, "at": now})
    _save()
    log.info("invite_claimed", code=code, user_id=user_id,
             plan=inv.plan, days=inv.days)
    return inv
