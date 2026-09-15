"""The Kick OAuth flow is gone, and the legal pages may say so.

WHY THESE EXIST. Both the Terms and the Privacy Policy stated that no Kick
credentials are requested or stored, while a Connect button in the Account
screen ran a full OAuth flow and wrote the access AND refresh token onto the
user record. The flow was removed rather than the sentence. These tests keep it
that way: if someone re-adds a Kick login without also rewriting the two
documents, they fail.

The purge test is the one that matters most. Deleting code does not delete
data — the claim stays false for every user who already pressed Connect until
the script has actually run, so the script is tested against a populated store
rather than trusted.
"""

import importlib
import json
import re
import sys

import pytest

from src.dashboard import api

def _routes() -> set[str]:
    return {getattr(r, "path", "") for r in api.app.routes}


def test_no_kick_oauth_route_is_registered():
    kick_routes = sorted(p for p in _routes() if p.startswith("/auth/kick"))
    assert kick_routes == [], f"a Kick OAuth route is back: {kick_routes}"


def test_the_dashboard_offers_no_way_to_connect_kick():
    from src.dashboard.aurora_html import DASHBOARD_HTML
    # Strip comments first: the code carries a note explaining why the flow was
    # removed, and matching that note instead of a link would make this vacuous.
    body = re.sub(r"/\*.*?\*/", "", DASHBOARD_HTML, flags=re.S)
    assert "/auth/kick" not in body, "the Account screen links a Kick OAuth flow again"


def test_nothing_can_write_kick_credentials():
    """The four functions that wrote the token fields are gone."""
    from src.auth import users as user_store
    for name in ("link_kick_to_user", "upsert_kick_user",
                 "_store_refreshed_kick_tokens", "get_kick_token"):
        assert not hasattr(user_store, name), \
            f"users.{name} is back — it writes or refreshes Kick credentials"


def test_kick_credentials_are_still_redacted_from_api_responses():
    """Until the purge has run, legacy records still hold the fields.

    _public() is the only thing keeping them out of every response in the
    meantime, so removing them from _SECRET_FIELDS before the store is clean
    would leak them.
    """
    from src.auth import users as user_store
    legacy = {"id": "u1", "username": "nova",
              "kick_access": "enc-secret", "kick_refresh": "enc-secret",
              "kick_id": "77", "kick_slug": "nova"}
    pub = user_store._public(legacy)
    assert "kick_access" not in pub and "kick_refresh" not in pub
    # Identity is not a credential and is deliberately left alone.
    assert pub["kick_id"] == "77"


def test_a_kick_clip_job_without_capture_fails_loudly_rather_than_half_working(monkeypatch):
    """Kick went live on 2026-09-15 with FILE-ONLY clips (no Kick clip API).
    With live capture off there is nothing behind the record, so the branch
    still refuses loudly instead of producing a card that can never play.
    tests/test_kick_live.py covers the capture-on path."""
    import asyncio
    from config.settings import settings
    from src.processor.clip_processor import ClipProcessor
    monkeypatch.setattr(settings, "clip_capture_enabled", False)
    proc = ClipProcessor.__new__(ClipProcessor)
    with pytest.raises(RuntimeError, match="live capture"):
        asyncio.run(proc._process_kick(None, None, "somechannel"))


# ── the purge script ─────────────────────────────────────────────────────────

@pytest.fixture()
def populated_store(tmp_path, monkeypatch):
    from src.auth import users as user_store
    store = tmp_path / "users.json"
    store.write_text(json.dumps([
        {"id": "u1", "username": "nova", "kick_access": "enc-a",
         "kick_refresh": "enc-r", "kick_expires_at": 123.0,
         "kick_id": "77", "kick_slug": "nova", "tw_access": "enc-tw"},
        {"id": "u2", "username": "lacy"},                      # never linked Kick
        {"id": "u3", "username": "aceu", "kick_access": "enc-a"},  # partial
    ]))
    monkeypatch.setattr(user_store, "_USERS_FILE", store)
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.bak.json")
    return store


def test_the_purge_reports_without_writing_by_default(populated_store, monkeypatch):
    import scripts.purge_kick_credentials as purge
    importlib.reload(purge)
    before = populated_store.read_text()
    monkeypatch.setattr(sys, "argv", ["purge_kick_credentials.py"])
    assert purge.main() == 0
    assert populated_store.read_text() == before, "a dry run wrote to the store"


def test_the_purge_strips_credentials_but_keeps_identity(populated_store, monkeypatch):
    import scripts.purge_kick_credentials as purge
    importlib.reload(purge)
    monkeypatch.setattr(sys, "argv", ["purge_kick_credentials.py", "--apply"])
    assert purge.main() == 0

    users = json.loads(populated_store.read_text())
    by_id = {u["id"]: u for u in users}
    assert len(users) == 3, "the purge dropped or duplicated a record"

    for uid in ("u1", "u3"):
        for f in ("kick_access", "kick_refresh", "kick_expires_at"):
            assert f not in by_id[uid], f"{uid} still holds {f}"

    # Identity survives — it is not a credential, and prefix-matching "kick_"
    # would have taken it. Unrelated secrets survive too.
    assert by_id["u1"]["kick_id"] == "77"
    assert by_id["u1"]["kick_slug"] == "nova"
    assert by_id["u1"]["tw_access"] == "enc-tw", "the purge ate the Twitch token"
    assert by_id["u2"] == {"id": "u2", "username": "lacy"}


def test_the_purge_is_safe_to_run_twice(populated_store, monkeypatch):
    import scripts.purge_kick_credentials as purge
    importlib.reload(purge)
    monkeypatch.setattr(sys, "argv", ["purge_kick_credentials.py", "--apply"])
    assert purge.main() == 0
    once = populated_store.read_text()
    assert purge.main() == 0
    assert populated_store.read_text() == once, "the second run changed the store"
