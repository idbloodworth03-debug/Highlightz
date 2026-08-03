"""Who sent this user? Independent of Stripe, because free signups never
touch it.

The existing promo attribution fires from the Stripe webhook at checkout, which
means it records nothing at all for a free signup — and the whole growth plan
is free signups. This is the parallel path: a code arrives on the URL or is
typed in, rides the session through the Twitch OAuth round-trip, and is written
onto the user the moment the account is created.

THREE RULES THAT DECIDE WHETHER THE NUMBERS ARE WORTH ANYTHING:

1. **First touch wins, permanently.** Once a user has a ref it is never
   overwritten. Someone who arrives through Tommy's link, comes back a week
   later through Ian's, and subscribes must still count as Tommy's — otherwise
   whoever posts most recently harvests everyone else's work and the weekly
   table stops telling you which lane actually produces users.

2. **A code and a link are the same thing.** `?ref=tommy` from a bio and a
   typed `TOMMY` from a DM resolve identically, because they are the same
   person doing the same outreach in two places. Splitting them would undercount
   every lane that uses both.

3. **Unknown codes are dropped, not stored.** Storing a typo means a row in the
   report attributed to nobody, which reads as a real lane that produced users.
"""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

# The people running outreach. Keys are what gets stored; every alias resolves
# to the key. Add someone here and both their link and their code work.
REFERRERS: dict[str, dict] = {
    "ian":    {"label": "Ian",    "aliases": ()},
    "andrew": {"label": "Andrew", "aliases": ()},
    "tommy":  {"label": "Tommy",  "aliases": ()},
    "thomas": {"label": "Thomas", "aliases": ()},
}

_ALIASES = {a.lower(): key
            for key, v in REFERRERS.items()
            for a in (key,) + tuple(v.get("aliases", ()))}

_CLEAN = re.compile(r"[^a-z0-9_-]")
MAX_LEN = 32


def normalise(raw: str | None) -> str | None:
    """A URL param or a typed code -> a known referrer key, or None.

    Case and stray punctuation are forgiven because these are typed by hand
    into DMs and read off screenshots. Unknown values return None so they are
    never stored.
    """
    if not raw:
        return None
    cleaned = _CLEAN.sub("", str(raw).strip().lower())[:MAX_LEN]
    if not cleaned:
        return None
    return _ALIASES.get(cleaned)


def label(key: str | None) -> str:
    if not key:
        return "Direct"
    return REFERRERS.get(key, {}).get("label", key)


def all_keys() -> list[str]:
    return list(REFERRERS)
