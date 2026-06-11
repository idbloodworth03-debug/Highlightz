"""
Streamer opt-out registry.

Streamers who do not want to be clipped on Highlightz can verify their Twitch
identity and add themselves to the blacklist. The blacklist is checked when any
user attempts to add a new stream to monitor; opted-out channels are blocked.
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


def is_opted_out(channel_login: str) -> bool:
    return any(e["twitch_login"].lower() == channel_login.lower() for e in _load())


def opt_out(twitch_id: str, twitch_login: str, display_name: str = "") -> None:
    entries = _load()
    for e in entries:
        if e["twitch_id"] == twitch_id:
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


def remove_opt_out(twitch_id: str) -> bool:
    entries = _load()
    filtered = [e for e in entries if e["twitch_id"] != twitch_id]
    if len(filtered) == len(entries):
        return False
    _save(filtered)
    return True


def get_all() -> list[dict]:
    return _load()
