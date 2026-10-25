"""Who has joined which campaign (owner, 2026-10-25: "make it so people click
"join" in the campaign page and then they can see the full details").

Joining is how a clipper opts in. Until then an upcoming or live campaign
shows them its overview: the picture, the prize and how it splits, the dates,
how the winners are picked, the streamers and the platforms. Joining shows
the rest, which the server sends only from then on: the rules, the hashtags,
mentions and caption a post needs, the leaderboard, and the Post button.
Posting for a campaign needs it. Anyone with an entry already counts as
joined (results.entrant_ids), so nobody who posted before joining existed
loses their page. Once posting has closed there is nothing to join and the
whole page is open to everyone.

STORE. campaign_joins.json: {campaign id: {user id: joined at}}. Written by
clippers, so kept apart from the admin-written campaigns, and apart from the
results, whose leaderboard cache a join has no reason to throw away.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from config.settings import settings
from src.auth._jsonstore import atomic_write_json

log = structlog.get_logger(__name__)

_INDEX = Path(settings.local_storage_path) / "campaign_joins.json"

_joins: dict[str, dict[str, float]] = {}
_loaded = False


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
        log.error("campaign_joins_file_unreadable", path=str(_INDEX))
        return
    if isinstance(data, dict):
        _joins.update({k: v for k, v in data.items() if isinstance(v, dict)})


def _save() -> None:
    atomic_write_json(_INDEX, _joins)


def join(campaign_id: str, user_id: str) -> bool:
    """True if this joined them, False if they already had."""
    _load()
    members = _joins.setdefault(campaign_id, {})
    if user_id in members:
        return False
    members[user_id] = time.time()
    _save()
    return True


def has(campaign_id: str, user_id: str) -> bool:
    _load()
    return user_id in (_joins.get(campaign_id) or {})


def count(campaign_id: str) -> int:
    _load()
    return len(_joins.get(campaign_id) or {})


def drop(campaign_id: str) -> None:
    """A deleted campaign's joins."""
    _load()
    if _joins.pop(campaign_id, None) is not None:
        _save()


def forget_user(user_id: str) -> None:
    """An account being deleted: which campaigns it joined goes with it."""
    _load()
    changed = False
    for members in _joins.values():
        if members.pop(user_id, None) is not None:
            changed = True
    if changed:
        _save()
