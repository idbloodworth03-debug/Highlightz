"""
Approved clips populate the Clip Editor on their own.

Owner (2026-09-16): "make it so auto accepted clips populate in the editor".
Before this, a clip reached the editor library only when the user pressed
Edit on its card. Now the approval itself copies the file into the library
(and the file-arrived hook does it for a clip approved before its capture
landed), so the Clip Editor tab already holds every kept clip.

The hook is quiet on every reason not to: the plan cannot use the editor, the
clip is not approved, there is no file yet, it is already there, or the
library is full. It never breaks the approve click, and the Edit button on
the card still works and explains itself in every one of those cases.
"""

import asyncio
import base64
import inspect
import json as _j

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.clips import files as clip_files
from src.dashboard import api
from src.uploads import library as upload_lib


PEOPLE = {
    "free_user": {"id": "free_user", "subscription_status": "none", "plan": "free"},
    "pro_user":  {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
}

CUT = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 8


@pytest.fixture
def scene(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _bcast)

    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()
    monkeypatch.setattr(upload_lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(upload_lib, "_INDEX", tmp_path / "uploads.json")
    upload_lib.load()
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    api._clips.clear()
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    yield sent
    api._clips.clear()
    upload_lib._uploads.clear()


def _clip(uid, clip_id="clip-1", status="approved", **extra):
    rec = {"id": clip_id, "user_id": uid, "channel": "novafps",
           "clip_title": "1v5 clutch", "status": status,
           "platform": "twitch", "created_at": 1000.0, **extra}
    api._clips[clip_id] = rec
    return rec


def _capture(clip_id="clip-1", data=CUT):
    p = clip_files.path_for(clip_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _run(clip_id="clip-1"):
    asyncio.run(api.library_copy_if_approved(clip_id))


# ── the happy path ───────────────────────────────────────────────────────────

def test_an_approved_clip_with_a_file_lands_in_the_editor_library(scene):
    _clip("pro_user")
    _capture()
    _run()
    ups = upload_lib.for_user("pro_user")
    assert len(ups) == 1
    assert ups[0].source == "clip"
    assert "novafps" in ups[0].filename
    assert upload_lib.path_for(ups[0]).read_bytes() == CUT
    # Linked, so the card's Edit button reuses this copy instead of a second.
    assert api._clips["clip-1"]["editor_upload_id"] == ups[0].id


def test_the_library_card_arrives_live_in_every_open_tab(scene):
    """Realtime contract: the same upload_added event the upload path emits,
    scoped to this user, plus the clip's refreshed record."""
    _clip("pro_user")
    _capture()
    _run()
    events = [(e, u) for e, u, _ in scene]
    assert ("upload_added", "pro_user") in events
    assert ("clip_updated", "pro_user") in events


def test_running_it_twice_makes_one_copy(scene):
    """It runs from the approve endpoint AND the file-arrived hook, so both
    firing for the same clip must not spend the quota twice."""
    _clip("pro_user")
    _capture()
    _run()
    _run()
    assert len(upload_lib.for_user("pro_user")) == 1


def test_the_edit_button_then_opens_the_same_copy(scene):
    """The card's Edit still works, and returns the auto-made copy."""
    _clip("pro_user")
    _capture()
    _run()
    auto = upload_lib.for_user("pro_user")[0]
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(_j.dumps({"auth": True, "user_id": "pro_user",
                                      "subscription_status": "active"}).encode())
    c.cookies.set("session", signer.sign(data).decode())
    r = c.post("/clips/clip-1/to-editor")
    assert r.status_code == 201
    assert r.json()["id"] == auto.id
    assert len(upload_lib.for_user("pro_user")) == 1


# ── the quiet cases ──────────────────────────────────────────────────────────

def test_a_clip_still_in_review_is_not_copied(scene):
    _clip("pro_user", status="pending")
    _capture()
    _run()
    assert upload_lib.for_user("pro_user") == []


def test_a_plan_without_the_editor_gets_nothing_copied(scene):
    """The editor is Pro. Copying for a free account would fill a library
    that account cannot open."""
    _clip("free_user")
    _capture()
    _run()
    assert upload_lib.for_user("free_user") == []


def test_the_release_flag_holds_it_back_too(scene, monkeypatch):
    monkeypatch.setattr(api.settings, "uploads_enabled", False)
    _clip("pro_user")
    _capture()
    _run()
    assert upload_lib.for_user("pro_user") == []


def test_no_file_yet_means_wait_for_the_file_hook(scene):
    """Approval can come before the capture or fetch lands. Nothing is copied
    now; on_clip_file_ready re-enters this when the file exists."""
    _clip("pro_user")
    _run()
    assert upload_lib.for_user("pro_user") == []
    _capture()
    asyncio.run(api.on_clip_file_ready("clip-1"))
    assert len(upload_lib.for_user("pro_user")) == 1


def test_a_full_library_is_a_log_line_not_a_failed_approval(scene, monkeypatch):
    monkeypatch.setattr(api.settings, "upload_max_user_mb", 0)
    _clip("pro_user")
    _capture()
    _run()                                    # must not raise
    assert upload_lib.for_user("pro_user") == []
    assert "editor_upload_id" not in api._clips["clip-1"]


def test_a_deleted_editor_copy_is_made_again_on_the_next_hook(scene):
    """The link is a shortcut, not a tombstone (same rule as the Edit button)."""
    _clip("pro_user")
    _capture()
    _run()
    first = upload_lib.for_user("pro_user")[0]
    assert upload_lib.delete(first.id, "pro_user") is not None
    _run()
    again = upload_lib.for_user("pro_user")
    assert len(again) == 1 and again[0].id != first.id


# ── the wiring ───────────────────────────────────────────────────────────────

def test_the_approve_endpoint_and_the_file_hook_both_call_it():
    assert "_autopilot.kick(library_copy_if_approved(clip_id))" in inspect.getsource(api.approve_clip)
    assert "await library_copy_if_approved(clip_id)" in inspect.getsource(api.on_clip_file_ready)


def test_the_edit_button_shares_the_copy_code_rather_than_duplicating_it():
    """One writer into the library from clips, so the caps, the link-back and
    the broadcasts cannot drift between the button and the hook."""
    assert "_copy_clip_into_library(" in inspect.getsource(api.send_clip_to_editor)
    assert "_copy_clip_into_library(" in inspect.getsource(api.library_copy_if_approved)
