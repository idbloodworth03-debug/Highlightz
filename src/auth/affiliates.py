"""
Affiliate codes, and the one account each belongs to.

WHAT THIS ADDS TO WHAT ALREADY EXISTED. `src/auth/referrals.py` resolves a
code arriving on a URL into a key stored on the new user, and the admin page
totals those keys up. Both of those work, and neither answers the question an
affiliate has: *how am I doing?* That needs two things the old design has no
room for — a code that belongs to somebody, and somebody who can sign in and
be shown only their own numbers.

WHY THE CODE LIVES ON THE USER RECORD rather than in its own table. An
affiliate is already an account: they sign in through the same Twitch OAuth as
everybody else, and there is no second password to issue, forget or leak. So
"who owns TOMMY" is a property of Tommy's account, and the portal is just the
signed-in user asking about their own field. A separate mapping file would be
a second thing to keep in step with the user store, with nothing to show for
it — and it would be the thing that goes stale when an account is deleted.

TWO PROPERTIES WORTH STATING PLAINLY:

* **A code is unique across accounts.** Two people sharing one code would make
  the portal a lie for both of them and the attribution unusable, so assigning
  a code somebody else holds is refused rather than silently reassigned.
* **The hardcoded referrers in referrals.py still work and are untouched.**
  Those predate this and are not owned by anyone; they keep resolving and keep
  appearing in the admin totals. This is additive.
"""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

# Deliberately narrower than referrals.MAX_LEN. These get typed into DMs, read
# off phone screens and said out loud, so they are letters, digits, hyphen and
# underscore — and short enough to not be mistyped.
_CODE_RE = re.compile(r"^[a-z0-9_-]{3,24}$")

# Codes nobody may hold, because they collide with something that already means
# something else in a URL or in the attribution table.
_RESERVED = {"direct", "admin", "portal", "none", "null", "undefined",
             "api", "static", "login", "logout", "auth", "me"}


def normalise_code(raw: str | None) -> str | None:
    """A typed or pasted code -> its canonical form, or None if unusable.

    Case and surrounding whitespace are forgiven because these are copied by
    hand. Anything that is not a legal code returns None rather than a cleaned
    approximation of itself: silently turning "Tommy's Code!" into "tommyscode"
    would hand somebody a code they did not ask for and cannot predict.
    """
    if not raw:
        return None
    c = str(raw).strip().lower()
    if not _CODE_RE.match(c) or c in _RESERVED:
        return None
    return c


def owner_of(code: str | None) -> dict | None:
    """The account holding this code, or None."""
    c = normalise_code(code)
    if not c:
        return None
    from src.auth import users as user_store
    return next((u for u in user_store.get_all()
                 if (u.get("affiliate_code") or "") == c), None)


def code_for(user_id: str) -> str | None:
    """The code this account holds, or None if they are not an affiliate."""
    from src.auth import users as user_store
    u = user_store.get_by_id(user_id)
    return (u or {}).get("affiliate_code") or None


def assign(user_id: str, code: str | None) -> tuple[bool, str]:
    """Give an account a code, or take it away with code=None.

    Returns (ok, message). The message is written to be shown to an admin, so
    a refusal says which of the three things went wrong rather than just
    failing.
    """
    from src.auth import users as user_store

    target = user_store.get_by_id(user_id)
    if not target:
        return False, "No such account."

    if code is None or str(code).strip() == "":
        user_store.set_affiliate_code(user_id, None)
        log.info("affiliate_code_cleared", user_id=user_id)
        return True, "Affiliate code removed."

    c = normalise_code(code)
    if not c:
        return False, ("Codes are 3-24 characters, letters, numbers, hyphen or "
                       "underscore, and cannot be a reserved word.")

    # Uniqueness, checked against every account rather than a cache: this runs
    # once per assignment and being wrong here corrupts attribution for two
    # people at once.
    holder = owner_of(c)
    if holder and holder.get("id") != user_id:
        return False, f"That code already belongs to {holder.get('username') or 'another account'}."

    # A BUILT-IN REFERRER KEY IS DELIBERATELY ALLOWED, and it is the whole
    # point for the four people who already have one. Those keys predate the
    # portal and already have signups attributed to them; handing the matching
    # account that same code is how somebody gets a portal that shows their
    # real history rather than starting from zero.
    #
    # There is no ambiguity to guard against: normalise() checks the built-ins
    # first and resolves the key to ITSELF, which is the identical string this
    # stores — so attribution lands in the same bucket by either route. The
    # uniqueness check above is what stops the wrong person claiming one.
    user_store.set_affiliate_code(user_id, c)
    log.info("affiliate_code_assigned", user_id=user_id, code=c)
    return True, f"Code '{c}' assigned."


def all_affiliates() -> list[dict]:
    """Every account holding a code, for the admin list."""
    from src.auth import users as user_store
    out = [u for u in user_store.get_all() if u.get("affiliate_code")]
    return sorted(out, key=lambda u: u.get("affiliate_code") or "")


def stats_for(code: str, users: list[dict] | None = None,
              last_active: dict | None = None, now: float | None = None) -> dict:
    """How this code is doing.

    THE SAME ARITHMETIC THE ADMIN REFERRAL TABLE USES, deliberately — an
    affiliate looking at their portal and the owner looking at the admin page
    must never see different numbers for the same code. `users` and
    `last_active` are injected so the caller can pass what it already loaded
    instead of re-reading the user store, and so this is testable without one.
    """
    import time as _time
    from src.auth import users as user_store

    now = now or _time.time()
    WEEK = 7 * 86400
    if users is None:
        users = user_store.get_all()
    last_active = last_active or {}

    from src.billing.plans import is_paid
    mine = [u for u in users if (u.get("ref") or "") == code]

    signups = len(mine)
    connected = sum(1 for u in mine if u.get("twitch_id"))
    paid = sum(1 for u in mine if is_paid(u))
    # Week-2 retention counts only accounts old enough to have a week 2. A user
    # who joined yesterday is not yet a datapoint, so they are excluded from
    # both sides rather than counted as churned — otherwise a good week of
    # fresh signups makes an affiliate's retention look like it collapsed.
    eligible = [u for u in mine if (u.get("created_at") or 0)
                and now - u["created_at"] >= WEEK]
    retained = sum(1 for u in eligible
                   if last_active.get(u["id"], 0)
                   and now - last_active[u["id"]] < WEEK)

    # Recent signups, for the "is my latest post working?" question that a
    # lifetime total cannot answer.
    def since(days: float) -> int:
        cut = now - days * 86400
        return sum(1 for u in mine if (u.get("created_at") or 0) >= cut)

    return {
        "code": code,
        "signups": signups,
        "connected": connected,
        "paid": paid,
        "retained_wk2": retained,
        "retention_eligible": len(eligible),
        "last_7": since(7),
        "last_30": since(30),
        "link": f"https://highlightz.app/?ref={code}",
    }
