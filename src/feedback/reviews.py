"""Ask users what they think, once they have something to think about.

WHEN TO ASK. A review prompt at signup gets a shrug — the user has not seen the
product work yet. The trigger here is 25 APPROVED clips, which is the point
where someone has watched the detector do its job twenty-five times and has a
real opinion, good or bad.

ASK EVERYONE, NOT JUST THE HAPPY ONES. It is tempting to show this only to
users whose approval rate is high. Do not. Selectively soliciting positive
reviews ("review gating") is prohibited by Google and by Trustpilot, and
Trustpilot removes profiles for it. The trigger is a count of clips, never a
measure of sentiment, and that is deliberate.

DISMISSAL IS REAL. "Not now" snoozes for weeks and the prompt returns at a
later milestone; "don't ask again" is permanent. A prompt that reappears after
someone said no is the fastest way to make people hate the product, and it
would poison the reviews you do get.

PUBLICATION IS OPT-IN AND SEPARATE. A user rating the product is not the same
as a user agreeing their words appear on a marketing page. Consent is its own
field, the display name is theirs to choose, and an admin still has to approve
before anything is public — three gates, because putting someone's name on a
public page without them meaning it is not recoverable by apologising.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "reviews.json"

# Milestones, in approved clips. The first is the real one; the later ones only
# matter for someone who dismissed earlier and has since stuck around.
MILESTONES = (25, 150, 500)
SNOOZE_S = 30 * 24 * 3600        # "not now" lasts a month
COMMENT_MAX = 1500
NAME_MAX = 60


@dataclass
class Review:
    id: str
    user_id: str
    username: str            # who actually wrote it, for the admin view
    stars: int               # 1-5
    comment: str
    # Consent to appear publicly. Separate from the rating on purpose: rating
    # the product is not agreeing to be quoted on a landing page.
    publish_consent: bool = False
    display_name: str = ""   # what they want shown, if anything
    approved: bool = False   # an admin still has to say yes
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        """Only what a visitor may see. Never the user id or username — the
        display name is the only identity the user consented to."""
        return {"id": self.id, "stars": self.stars, "comment": self.comment,
                "name": self.display_name or "Highlightz user",
                "created_at": self.created_at}

    def admin(self) -> dict:
        return asdict(self)


_reviews: dict[str, Review] = {}
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
        log.warning("reviews_index_unreadable", path=str(_INDEX))
        return
    for r in raw:
        try:
            _reviews[r["id"]] = Review(**r)
        except (TypeError, KeyError):
            continue


def _save() -> None:
    _INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(r) for r in _reviews.values()], indent=2),
                   encoding="utf-8")
    os.replace(tmp, _INDEX)


def add(user_id: str, username: str, stars: int, comment: str,
        publish_consent: bool = False, display_name: str = "") -> Review:
    _load()
    if not 1 <= int(stars) <= 5:
        raise ValueError("Pick a rating from 1 to 5 stars.")
    comment = (comment or "").strip()
    if len(comment) > COMMENT_MAX:
        raise ValueError(f"Please keep it under {COMMENT_MAX} characters.")
    name = (display_name or "").strip()[:NAME_MAX]

    r = Review(id=uuid.uuid4().hex, user_id=user_id, username=username,
               stars=int(stars), comment=comment,
               publish_consent=bool(publish_consent),
               display_name=name if publish_consent else "")
    _reviews[r.id] = r
    _save()
    log.info("review_submitted", user_id=user_id, stars=r.stars,
             consent=r.publish_consent)
    return r


def for_user(user_id: str) -> list[Review]:
    _load()
    return [r for r in _reviews.values() if r.user_id == user_id]


def all_reviews() -> list[Review]:
    _load()
    return sorted(_reviews.values(), key=lambda r: -r.created_at)


def published() -> list[Review]:
    """What may appear on the marketing page: consented AND admin-approved."""
    return [r for r in all_reviews() if r.publish_consent and r.approved]


def set_approved(review_id: str, approved: bool) -> Review | None:
    _load()
    r = _reviews.get(review_id)
    if not r:
        return None
    r.approved = bool(approved)
    _save()
    return r


def remove(review_id: str) -> bool:
    _load()
    if review_id not in _reviews:
        return False
    _reviews.pop(review_id)
    _save()
    return True


def delete_all_for_user(user_id: str) -> int:
    gone = [r.id for r in for_user(user_id)]
    for rid in gone:
        _reviews.pop(rid, None)
    if gone:
        _save()
    return len(gone)


def aggregate() -> dict:
    """Average and count over PUBLISHED reviews only.

    This number is what would go into schema.org aggregateRating, and it has to
    describe exactly the reviews a visitor can actually read on the page.
    Averaging over private ones would be inventing a rating, which is the thing
    structured-data penalties exist for.
    """
    pub = published()
    if not pub:
        return {"count": 0, "average": 0.0}
    return {"count": len(pub),
            "average": round(sum(r.stars for r in pub) / len(pub), 1)}


# ── when to ask ───────────────────────────────────────────────────────────────

def should_prompt(user: dict, approved_clips: int, now: float | None = None) -> bool:
    """Has this user hit a milestone, and are they open to being asked?

    Takes the user record rather than reading storage so the caller controls
    persistence, and takes the count rather than computing it so this stays a
    pure decision that can be tested without clips on disk.
    """
    now = time.time() if now is None else now
    state = (user or {}).get("review_prompt") or {}
    if state.get("never"):
        return False
    if state.get("submitted_at"):
        return False            # they already told us; do not ask twice
    if now < (state.get("snooze_until") or 0):
        return False

    reached = [m for m in MILESTONES if approved_clips >= m]
    if not reached:
        return False
    # Only re-ask at a milestone they have not already been asked at, so
    # someone who snoozed at 25 is asked again at 150 rather than the moment
    # the snooze expires.
    return max(reached) > (state.get("last_milestone") or 0)


def mark_shown(user: dict, approved_clips: int) -> dict:
    reached = [m for m in MILESTONES if approved_clips >= m]
    state = dict((user or {}).get("review_prompt") or {})
    state["last_milestone"] = max(reached) if reached else 0
    state["last_shown"] = time.time()
    return state


def mark_snoozed(user: dict, approved_clips: int,
                 now: float | None = None) -> dict:
    """Snooze, AND record which milestone was being dismissed.

    Recording it here rather than trusting the caller to have called
    mark_shown() first is deliberate: the prompt can reach a user through two
    paths (the live broadcast, and the flag on /me for a tab opened later) and
    only one of them marks it shown. Without this, someone who saw it via /me
    and pressed "not now" would be asked again at the SAME milestone once the
    snooze expired — the exact behaviour that makes people resent a product.
    """
    now = time.time() if now is None else now
    state = mark_shown(user, approved_clips)
    state["snooze_until"] = now + SNOOZE_S
    return state


def mark_never(user: dict) -> dict:
    state = dict((user or {}).get("review_prompt") or {})
    state["never"] = True
    return state


def mark_submitted(user: dict, now: float | None = None) -> dict:
    state = dict((user or {}).get("review_prompt") or {})
    state["submitted_at"] = now if now is not None else time.time()
    return state
