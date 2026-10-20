"""
Streamer opt-out registry.

Streamers who do not want to be clipped on Highlightz can verify their Twitch
or Kick identity and add themselves to the blacklist. The blacklist is checked
when any user attempts to add a new stream to monitor; opted-out channels are
blocked.

TWO KINDS OF ENTRY. A Twitch entry is {twitch_id, twitch_login, ...} — every
entry written before Kick opt-outs existed, so an entry with no `platform` IS
a Twitch one. A Kick entry is {platform: "kick", kick_id, kick_slugs, ...}:
the Kick user id (the broadcaster id of their channel) and every slug their
channel is known by — the one derived from the username at sign-in and the
one Kick's API reports for that id, which can differ.

Owner, 2026-10-20 ("kick opt-out option a"): the Terms say ANY broadcaster
can opt out at /opt-out, and until then only a Twitch sign-in could.
"""

import json
import os
import tempfile
import time
from pathlib import Path

from config.settings import settings

_OPTOUT_FILE = Path(settings.local_storage_path) / "optout.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_OPTOUT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


def _save(entries: list[dict]) -> None:
    _OPTOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_OPTOUT_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, _OPTOUT_FILE)
        try:
            os.chmod(_OPTOUT_FILE, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _platform(e: dict) -> str:
    return e.get("platform") or "twitch"


def is_opted_out(channel_login: str, platform: str | None = None) -> bool:
    """Whether this channel has opted out.

    With a platform, only that platform's opt-outs count. With none, a name
    match on EITHER platform does — the safe answer for a caller that knows
    only a name (the recorder, whose channels are keyed by name alone): it may
    refuse a same-named channel on the other platform, never record one that
    said no."""
    name = (channel_login or "").strip().lower()
    if not name:
        return False
    for e in _load():
        p = _platform(e)
        if platform and p != platform:
            continue
        if p == "twitch" and str(e.get("twitch_login") or "").lower() == name:
            return True
        if p == "kick" and name in (str(s).lower() for s in e.get("kick_slugs") or []):
            return True
    return False


def kick_user_opted_out(broadcaster_id) -> bool:
    """Whether the Kick user who owns this channel has opted out — by id, so it
    holds even when the channel is known by a slug the opt-out did not see."""
    bid = str(broadcaster_id or "")
    return bool(bid) and any(_platform(e) == "kick" and str(e.get("kick_id")) == bid
                             for e in _load())


def opt_out_kick(kick_id: str, slugs, display_name: str = "") -> None:
    """Record a Kick opt-out. `slugs` is every slug the channel is known by."""
    kick_id = str(kick_id)
    names = sorted({str(s).strip().lower() for s in slugs if str(s or "").strip()})
    entries = _load()
    for e in entries:
        if _platform(e) == "kick" and str(e.get("kick_id")) == kick_id:
            e["kick_slugs"] = sorted(set(e.get("kick_slugs") or []) | set(names))
            e["display_name"] = display_name or e.get("display_name") or (names[0] if names else "")
            e["opted_out_at"] = time.time()
            _save(entries)
            return
    entries.append({
        "platform":     "kick",
        "kick_id":      kick_id,
        "kick_slugs":   names,
        "display_name": display_name or (names[0] if names else ""),
        "opted_out_at": time.time(),
    })
    _save(entries)


def opt_out(twitch_id: str, twitch_login: str, display_name: str = "") -> None:
    entries = _load()
    for e in entries:
        if _platform(e) == "twitch" and e.get("twitch_id") == twitch_id:
            e["twitch_login"]  = twitch_login.lower()
            e["display_name"]  = display_name or twitch_login
            e["opted_out_at"]  = time.time()
            _save(entries)
            return
    entries.append({
        "twitch_id":    twitch_id,
        "twitch_login": twitch_login.lower(),
        "display_name": display_name or twitch_login,
        "opted_out_at": time.time(),
    })
    _save(entries)


def remove_opt_out(entry_id: str, platform: str = "twitch") -> bool:
    """Drop an opt-out by the platform's user id (an admin action)."""
    key = "kick_id" if platform == "kick" else "twitch_id"
    entries = _load()
    filtered = [e for e in entries
                if not (_platform(e) == platform and str(e.get(key)) == str(entry_id))]
    if len(filtered) == len(entries):
        return False
    _save(filtered)
    return True


def get_all() -> list[dict]:
    return _load()
