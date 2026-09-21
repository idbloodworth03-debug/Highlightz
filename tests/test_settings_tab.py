"""The Settings tab does things (owner, 2026-09-16: "this settings tab
basically does nothing").

Three knobs, each pinned end to end:
- per-channel preset and sensitivity (PATCH /streams/{channel}) — the dial
  is read at fire time in trigger/engine.py as a multiplier on the learned
  threshold, and 0 (every existing profile) changes nothing;
- account preferences (GET/PUT /prefs): default preset for new channels,
  approved clips → editor, browser notifications, reduce motion;
- the dashboard renders them from App state and follows the broadcasts.
"""

import asyncio
import base64
import json as _j
import re

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import api


PEOPLE = {"u1": {"id": "u1", "subscription_status": "active", "plan": "pro"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.auth import users as user_store
    from src.profiles import manager as pm_mod
    store = tmp_path / "users.json"
    store.write_text(_j.dumps([{"id": "u1", "username": "nova", "subscription_status": "active", "plan": "pro"}]))
    monkeypatch.setattr(user_store, "_USERS_FILE", store)
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.bak.json")
    monkeypatch.setattr(api.settings, "local_storage_path", str(tmp_path))
    pm_mod._user_profile_managers.clear()
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    sent, started, stopped = [], [], []

    async def _bcast(msg, user_id=None): sent.append((msg.get("event"), user_id, msg))
    async def _new(channel, platform, preset, uid=""): started.append((channel, platform, preset, uid))
    async def _rm(channel, uid=""): stopped.append((channel, uid))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)

    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(_j.dumps({"auth": True, "user_id": "u1", "username": "nova",
                                       "subscription_status": "active"}).encode())
    c.cookies.set("session", signer.sign(data).decode())
    c.sent, c.started, c.stopped, c.store = sent, started, stopped, store
    api._streams["u1:novafps"] = {"channel": "novafps", "platform": "twitch", "preset": "default",
                                  "status": "running", "user_id": "u1", "added_at": 1.0}
    yield c
    pm_mod._user_profile_managers.clear()


# ── per-channel tuning ───────────────────────────────────────────────────────

def test_sensitivity_lands_on_the_profile_and_broadcasts(client):
    r = client.patch("/streams/novafps", json={"sensitivity": 2})
    assert r.status_code == 200, r.text
    assert r.json()["profile"]["sensitivity"] == 2
    assert ("profile_updated", "u1") in [(e, u) for e, u, _ in client.sent]
    assert client.get("/profiles/novafps").json()["sensitivity"] == 2
    assert client.started == [] and client.stopped == [], "a sensitivity change restarted the worker"


def test_sensitivity_is_bounded(client):
    assert client.patch("/streams/novafps", json={"sensitivity": 4}).status_code == 422
    assert client.patch("/streams/novafps", json={"sensitivity": -4}).status_code == 422


def test_a_preset_change_rewrites_the_record_and_restarts_the_worker(client):
    r = client.patch("/streams/novafps", json={"preset": "fps"})
    assert r.status_code == 200, r.text
    assert r.json()["stream"]["preset"] == "fps"
    assert api._streams["u1:novafps"]["preset"] == "fps"
    assert client.get("/profiles/novafps").json()["preset"] == "fps"
    assert client.stopped == [("novafps", "u1")]
    assert client.started == [("novafps", "twitch", "fps", "u1")]
    events = [e for e, _, _ in client.sent]
    assert "stream_updated" in events and "profile_updated" in events


def test_the_same_preset_again_is_a_no_op(client):
    r = client.patch("/streams/novafps", json={"preset": "default"})
    assert r.status_code == 200
    assert client.started == [] and client.stopped == []


def test_an_unknown_preset_or_channel_is_refused(client):
    assert client.patch("/streams/novafps", json={"preset": "turbo"}).status_code == 400
    assert client.patch("/streams/nobody", json={"sensitivity": 1}).status_code == 404


def test_the_engine_moves_the_bar_by_eight_percent_a_step_and_not_at_zero():
    """The trigger-code guard from CLAUDE.md: on a healthy stream (dial at 0)
    the threshold is exactly what it was."""
    import inspect
    from src.trigger import engine
    src = inspect.getsource(engine)
    assert 'getattr(self.profile, "sensitivity", 0)' in src
    assert "threshold = threshold * (1.0 - 0.08 * sens)" in src
    assert "if sens:" in src, "the multiplier must be skipped at 0"
    assert "max(-3, min(3, sens))" in src
    from src.profiles.profile import StreamerProfile
    p = StreamerProfile(channel="x")
    assert p.sensitivity == 0
    # Older profile files have no field; from_dict must still load them.
    d = p.to_dict(); d.pop("sensitivity")
    assert StreamerProfile.from_dict(d).sensitivity == 0


# ── account preferences ──────────────────────────────────────────────────────

def test_prefs_have_defaults_and_partial_updates_merge(client):
    from src.auth.users import PREF_DEFAULTS
    d = client.get("/prefs").json()
    # Against the defaults rather than a literal: a new pref is a normal
    # thing to add, and this test is about MERGING, not about the roster.
    assert d == PREF_DEFAULTS
    r = client.put("/prefs", json={"default_preset": "chess", "junk": 1})
    assert r.status_code == 200
    assert r.json()["default_preset"] == "chess" and "junk" not in r.json()
    r = client.put("/prefs", json={"auto_editor": False})
    # One key changed, one key kept from the previous call, the rest default:
    # that is the whole contract of a partial update.
    assert r.json() == {**PREF_DEFAULTS, "default_preset": "chess",
                        "auto_editor": False}
    assert ("prefs_changed", "u1") in [(e, u) for e, u, _ in client.sent]
    assert client.get("/me").json()["prefs"]["default_preset"] == "chess"
    assert client.put("/prefs", json={"default_preset": "turbo"}).json()["default_preset"] == "default"
    assert client.put("/prefs", json=[1]).status_code == 400


def test_auto_editor_off_stops_the_approval_copy(client, monkeypatch):
    """The approval hook honours the switch; the Edit button path is untouched."""
    import inspect
    src = inspect.getsource(api.library_copy_if_approved)
    assert 'prefs_for(uid)["auto_editor"]' in src
    assert "auto_editor" not in inspect.getsource(api.send_clip_to_editor)


# ── the dashboard ────────────────────────────────────────────────────────────

def test_the_settings_screen_has_the_controls_and_follows_the_events():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    scr = h[h.index("function SettingsScreen("):h.index("function tutBold(")]
    assert "fetch('/streams/' + encodeURIComponent(channel), {method: 'PATCH'" in scr
    assert "fetch('/prefs', {method: 'PUT'" in scr
    for k in ("{preset: e.target.value}", "{sensitivity: +e.target.value}", "{default_preset: e.target.value}",
              "auto_editor", "reduce_motion", "notify_clips", "Notification.requestPermission()"):
        assert k in scr, f"Settings has no control for {k}"
    assert 'type="range" min="-3" max="3"' in scr
    # Reference card stays, folded.
    assert "What each preset does" in scr and "<details" in scr
    # Realtime contract: the App feeds it live state and handles the events.
    assert "screen=<SettingsScreen {...{streams,profiles,me,activePlatform}}/>" in h
    assert "msg.event==='prefs_changed'" in h and "msg.event==='stream_updated'" in h
    assert "msg.event==='profile_updated'" in h
    # Notifications fire from the clip_ready handler, background tabs only.
    assert "prefsRef.current.notify_clips && document.hidden" in h
    # Reduce motion covers the sweep and the wake animation.
    assert "|| !!prefsRef.current.reduce_motion" in h
    assert "reduceMotion={!!(me&&me.prefs&&me.prefs.reduce_motion)}" in h
    # The add box starts on the default preset until the user picks one.
    assert "if(defaultPreset && !presetTouched.current) setPreset(defaultPreset)" in h
    assert re.search(r"settings:\['Settings','Tune each channel", h)
