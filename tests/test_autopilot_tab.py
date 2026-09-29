"""Autopilot's own screen, the Auto Edit template, and "switch it on and it
goes through what you already accepted".

Owner, 2026-09-28: "Autopilot has no spot that it sits in. We need a spot for
it … where people can activate it and it will pull the clips that you already
have accepted and start going through them, auto editing and adding them to
the scheduler. Also I need the auto edit to have its own settings spot … where
the user can choose what template they want … or just use the suggested one
… And remove the hook part … I want it to be fully auto after a user accepts a
clip."
"""

import asyncio
import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import autopilot as ap
from src.autopilot import auto_edit, plan as P, runner


def _run(coro):
    return asyncio.run(coro)


# ── the template setting ─────────────────────────────────────────────────────

def test_the_suggested_template_is_the_default_and_junk_falls_back_to_it():
    assert ap.normalize({})["edit_template"] == "suggested"
    assert ap.normalize({"edit_template": "fill"})["edit_template"] == "fill"
    assert ap.normalize({"edit_template": "sparkle-explosion"})["edit_template"] == "suggested"
    assert ap.EDIT_TEMPLATES[0] == "suggested"


def _plan():
    return P.build([{"id": "c1", "channel": "lacy"}], {"c1": ("/x.mp4", 30.0)})


def test_suggested_is_the_formula_as_designed():
    p = auto_edit.apply_template(_plan(), "suggested")
    assert {s.framing for s in p.segments} == {"blur"}
    assert p.slide_in and p.slide_out and p.sfx
    assert P.valid(p)[0]


def test_fill_crops_to_fill_but_keeps_the_slides():
    p = auto_edit.apply_template(_plan(), "fill")
    assert {s.framing for s in p.segments} == {"fill"}
    assert p.slide_in and p.slide_out and p.sfx
    assert P.valid(p)[0]


def test_clean_has_no_slides_and_no_sound():
    p = auto_edit.apply_template(_plan(), "clean")
    assert {s.framing for s in p.segments} == {"blur"}
    assert not p.slide_in and not p.slide_out and p.sfx == []
    assert P.valid(p)[0]


# ── switching it on ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    people = {"pro": {"id": "pro", "subscription_status": "active", "plan": "pro"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "_load", lambda: list(people.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)

    async def _bcast(msg, user_id=None): pass
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    kicked = []
    monkeypatch.setattr(runner, "kick", lambda coro: (kicked.append(coro), coro.close()))

    async def fake_run_now(uid, *, force=True):
        return 0
    monkeypatch.setattr(runner, "run_now", fake_run_now)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "pro", "subscription_status": "active"}).encode())).decode())
    c.kicked = kicked
    return c


def test_switching_autopilot_on_starts_on_the_accepted_clips(client):
    assert client.put("/autopilot", json={"enabled": True}).status_code == 200
    assert len(client.kicked) == 1, "switching it on did not start going through the accepted clips"


def test_saving_a_setting_while_it_is_on_does_not_restart_the_pass(client):
    client.put("/autopilot", json={"enabled": True})
    client.kicked.clear()
    client.put("/autopilot", json={"enabled": True, "edit_template": "clean"})
    client.put("/autopilot", json={"enabled": True, "timing": "daily"})
    assert client.kicked == [], "every setting change restarted the pass"


def test_switching_it_off_starts_nothing(client):
    client.put("/autopilot", json={"enabled": True})
    client.kicked.clear()
    client.put("/autopilot", json={"enabled": False})
    assert client.kicked == []


def test_the_template_is_saved_with_the_settings(client):
    r = client.put("/autopilot", json={"enabled": False, "edit_template": "clean"})
    assert r.json()["config"]["edit_template"] == "clean"


# ── the pass itself ──────────────────────────────────────────────────────────

@pytest.fixture
def backlog(monkeypatch, tmp_path):
    from src.dashboard import api
    from src.auth import users as user_store
    from src.clips import files as clip_files
    from src.publish import connections
    now = time.time()
    api._clips.clear()
    for i in range(3):
        api._clips[f"c{i}"] = {"id": f"c{i}", "user_id": "pro", "status": "approved",
                               "approved_at": now - 1000 + i, "created_at": now - 2000}
    f = tmp_path / "x.mp4"; f.write_bytes(b"x")
    monkeypatch.setattr(clip_files, "path_for", lambda cid: f)
    monkeypatch.setattr(connections, "connected_platforms", lambda uid: set())
    state = {"enabled": True, "done": []}
    monkeypatch.setattr(user_store, "autopilot_for",
                        lambda uid: ap.normalize({"enabled": state["enabled"]}))
    runner._backlogs.clear()
    yield state
    api._clips.clear(); runner._backlogs.clear()


def test_the_pass_goes_through_every_accepted_clip(backlog, monkeypatch):
    async def fake(clip, cfg, notify, *, connected):
        backlog["done"].append(clip["id"]); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 3
    assert backlog["done"] == ["c0", "c1", "c2"]


def test_switching_it_off_mid_pass_stops_it_after_the_clip_in_hand(backlog, monkeypatch):
    async def fake(clip, cfg, notify, *, connected):
        backlog["done"].append(clip["id"])
        backlog["enabled"] = False              # the user flips it off during clip 1
        return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 1
    assert backlog["done"] == ["c0"]


def test_the_button_still_runs_when_autopilot_is_off(backlog, monkeypatch):
    backlog["enabled"] = False
    async def fake(clip, cfg, notify, *, connected):
        backlog["done"].append(clip["id"]); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro")) == 3


def test_a_second_pass_does_not_start_while_one_is_running(backlog, monkeypatch):
    runner._backlogs.add("pro")
    async def fake(*a, **k): raise AssertionError("a second pass started")
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 0
