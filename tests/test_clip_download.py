"""
Downloading a captured clip.

THE PRODUCT DECISION THESE PIN. The editor and the scheduler are Pro features,
but the clip file itself is not: a clip the product caught for you is yours to
keep on every plan, including free. Putting the download behind the paywall
would turn the free plan into a demo of a clip you may look at and not have,
which is a different and much worse offer than the one advertised.

The rest is the usual shape of anything that serves bytes off our disk: one
user's clip id is a 404 to another, the path never comes from the request, and
a clip that has no file says so in words rather than 500ing.
"""

import base64
import json as _j

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.clips import files as clip_files
from src.dashboard import api


PEOPLE = {
    "free_user": {"id": "free_user", "subscription_status": "none", "plan": "free"},
    "pro_user":  {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
    "other":     {"id": "other", "subscription_status": "active", "plan": "pro"},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)

    # Files land in a scratch dir, never the real store.
    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()

    api._clips.clear()
    monkeypatch.setattr(api, "_save_clips", lambda: None)

    c = TestClient(api.app)

    def login(uid):
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid,
            "subscription_status": PEOPLE[uid]["subscription_status"],
        }).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    yield c
    api._clips.clear()


def _clip(uid, clip_id="clip-1", **extra):
    rec = {"id": clip_id, "user_id": uid, "channel": "novafps",
           "clip_title": "1v5 clutch", "status": "approved",
           "platform": "twitch", "created_at": 1000.0, **extra}
    api._clips[clip_id] = rec
    return rec


def _write_file(clip_id, data=b"\x00\x00\x00\x20ftypisom" + b"v" * 512):
    p = clip_files.path_for(clip_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ── the flag the UI draws the button from ────────────────────────────────────

def test_a_clip_with_no_capture_is_marked_as_having_no_file(client):
    c = client.login("free_user")
    _clip("free_user")
    listed = c.get("/clips").json()
    assert listed[0]["has_file"] is False


def test_a_captured_clip_is_marked_downloadable(client):
    c = client.login("free_user")
    _clip("free_user")
    _write_file("clip-1")
    assert c.get("/clips").json()[0]["has_file"] is True


def test_the_flag_survives_approving_the_clip(client):
    """THE BUG THIS CAUGHT. Approve returns the clip and the browser replaces
    its copy with that response. Without the flag on it, the Download button
    vanished the moment you approved — the exact point you most want it."""
    c = client.login("pro_user")
    _clip("pro_user", status="pending")
    _write_file("clip-1")
    r = c.post("/clips/clip-1/approve")
    assert r.status_code == 200, r.text
    assert r.json()["has_file"] is True


def test_an_empty_file_does_not_count_as_downloadable(client):
    """A zero-byte cut is a failed cut. Offering it is worse than offering
    nothing, because the user spends a click finding out."""
    c = client.login("free_user")
    _clip("free_user")
    _write_file("clip-1", data=b"")
    assert c.get("/clips").json()[0]["has_file"] is False


# ── serving the bytes ────────────────────────────────────────────────────────

def test_the_free_plan_can_download_its_own_clip(client):
    """The product decision. Free users pay nothing and still keep the clips
    the detector caught for them; only editing and scheduling are Pro."""
    c = client.login("free_user")
    _clip("free_user")
    body = _write_file("clip-1").read_bytes()
    r = c.get("/clips/clip-1/file?download=1")
    assert r.status_code == 200, r.text
    assert r.content == body
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith("attachment")
    # A name the user will recognise in their downloads folder.
    assert "novafps" in r.headers["content-disposition"]


def test_playback_is_inline_but_download_is_an_attachment(client):
    c = client.login("free_user")
    _clip("free_user")
    _write_file("clip-1")
    assert c.get("/clips/clip-1/file").headers["content-disposition"].startswith("inline")
    assert c.get("/clips/clip-1/file?download=1").headers["content-disposition"].startswith("attachment")


def test_the_response_refuses_to_be_sniffed_into_something_else(client):
    c = client.login("free_user")
    _clip("free_user")
    _write_file("clip-1")
    r = c.get("/clips/clip-1/file")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_another_users_clip_is_a_404_not_a_file(client):
    """Scoped by the clip RECORD's owner, so guessing an id gets you nothing."""
    _clip("pro_user")
    _write_file("clip-1")
    c = client.login("other")
    assert c.get("/clips/clip-1/file").status_code == 404


def test_a_clip_that_was_never_captured_is_a_clean_404(client):
    """Not an error page with a stack trace, and not a zero-byte download.

    The app has a global 404 handler that answers with the branded page (or a
    flat {"detail": "Not found"} for a JSON caller), so the route's own
    message does not reach the client. That is fine here rather than worth
    fighting: the Download button is gated on `has_file`, so reaching this at
    all means the file was swept between the page rendering and the click —
    rare, and the branded 404 is a reasonable answer to a dead link."""
    c = client.login("free_user")
    _clip("free_user")
    r = c.get("/clips/clip-1/file")
    assert r.status_code == 404
    r_json = c.get("/clips/clip-1/file", headers={"accept": "application/json"})
    assert r_json.status_code == 404 and "detail" in r_json.json()


def test_an_unknown_clip_is_a_404(client):
    c = client.login("free_user")
    assert c.get("/clips/nope/file").status_code == 404


@pytest.mark.parametrize("bad", ["../../etc/passwd", "..", "a/b"])
def test_a_clip_id_can_never_become_a_path(client, bad):
    """The id reaches path_for from the URL. It is ours today, but 'trusted
    because of where it comes from' is what stops being true in a refactor."""
    assert clip_files.path_for(bad) is None
    c = client.login("free_user")
    assert c.get(f"/clips/{bad}/file").status_code in (404, 405)


# ── the file goes when the clip goes ─────────────────────────────────────────

def test_deleting_a_clip_deletes_its_file(client):
    """Every removal path routes through _delete_clip_file, so this covers
    reject, delete, the dead-clip sweep and account deletion at once."""
    c = client.login("pro_user")
    _clip("pro_user")
    p = _write_file("clip-1")
    assert p.exists()
    api._delete_clip_file(api._clips["clip-1"])
    assert not p.exists()


def test_deleting_a_clip_that_never_had_a_file_is_harmless(client):
    _clip("pro_user")
    api._delete_clip_file(api._clips["clip-1"])       # must not raise


# ── retention ────────────────────────────────────────────────────────────────

def test_the_sweep_removes_files_no_clip_record_points_at(client, monkeypatch):
    """An orphan is disk nothing can free through the UI — the record was the
    only handle on it."""
    _clip("pro_user", clip_id="kept")
    _write_file("kept")
    _write_file("orphan")
    removed = clip_files.sweep(live_ids={"kept"})
    assert removed == 1
    assert clip_files.exists("kept") and not clip_files.exists("orphan")


def test_the_sweep_removes_files_past_the_retention_ceiling(client, monkeypatch):
    import os, time
    _write_file("old")
    p = clip_files.path_for("old")
    ancient = time.time() - (api.settings.clip_file_max_age_days + 1) * 86400
    os.utime(p, (ancient, ancient))
    assert clip_files.sweep(live_ids={"old"}) == 1
    assert not p.exists()


def test_the_sweep_leaves_everything_alone_when_it_cannot_enumerate_records(client):
    """Passing None means 'I could not list the records'. Treating that as
    'no records exist' would delete every file on the disk."""
    _write_file("a")
    _write_file("b")
    assert clip_files.sweep(live_ids=None) == 0
    assert clip_files.exists("a") and clip_files.exists("b")


def test_capture_stops_rather_than_filling_the_disk(client, monkeypatch):
    """The disk is shared with the clip store, the user database and billing
    writes, so a full one is not a 'capture is broken' event."""
    monkeypatch.setattr(api.settings, "clip_file_max_total_mb", 0.000001)
    _write_file("big", data=b"x" * 4096)
    assert clip_files.headroom_ok() is False
