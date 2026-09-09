"""
Sending a captured clip into the Clip Editor.

THIS ROUTE IS WHERE THE PAYWALL ACTUALLY SITS, and that split is the thing
worth pinning. `GET /clips/{id}/file` serves the clip on every plan including
free, because a clip the product caught for you is yours to keep. Editing it
is the Pro feature. So the free plan must be able to download the very same
clip this endpoint refuses it — if those two ever agree, one of them is wrong.

The rest is the shape of a server-side copy: it goes through the upload
library so it inherits every disk cap rather than growing a second writer that
forgets them, it is idempotent so a double click does not spend the quota
twice, and it never copies a file the caller does not own.
"""

import base64
import json as _j

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.clips import files as clip_files
from src.dashboard import api
from src.uploads import library as upload_lib


# `plan` alone does not decide access — get_plan reads subscription_status
# first — so these carry both, in the combinations that actually occur.
PEOPLE = {
    "free_user":    {"id": "free_user", "subscription_status": "none", "plan": "free"},
    "starter_user": {"id": "starter_user", "subscription_status": "active", "plan": "starter"},
    "pro_user":     {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
    "other_pro":    {"id": "other_pro", "subscription_status": "active", "plan": "pro"},
}

CUT = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 8


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)

    # Both stores land in scratch dirs, never the real ones.
    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()
    monkeypatch.setattr(upload_lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(upload_lib, "_INDEX", tmp_path / "uploads.json")
    upload_lib.load()

    # The editor is behind a release flag as well as a plan. Tests are about
    # the plan gate, so the flag is on unless a test says otherwise.
    monkeypatch.setattr(api.settings, "uploads_enabled", True)

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
    upload_lib._uploads.clear()


def _clip(uid, clip_id="clip-1", **extra):
    rec = {"id": clip_id, "user_id": uid, "channel": "novafps",
           "clip_title": "1v5 clutch", "status": "approved",
           "platform": "twitch", "created_at": 1000.0, **extra}
    api._clips[clip_id] = rec
    return rec


def _capture(clip_id="clip-1", data=CUT):
    p = clip_files.path_for(clip_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ── the plan line ────────────────────────────────────────────────────────────

def test_pro_can_send_a_captured_clip_to_the_editor(client):
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    r = c.post("/clips/clip-1/to-editor")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "mp4"
    assert body["size"] == len(CUT)
    # The editor opens uploads by this URL, so it has to come back on the
    # record rather than being assembled by the caller.
    assert body["url"] == f"/uploads/{body['id']}/file"


@pytest.mark.parametrize("who", ["free_user", "starter_user"])
def test_the_editor_is_pro_only(client, who):
    c = client.login(who)
    _clip(who)
    _capture()
    r = c.post("/clips/clip-1/to-editor")
    assert r.status_code == 403
    assert upload_lib.for_user(who) == []


def test_the_plan_that_cannot_edit_can_still_download_the_same_clip(client):
    """THE PRODUCT DECISION, stated as one test rather than two halves that
    could drift apart. The paywall is on editing, not on having the clip."""
    c = client.login("free_user")
    _clip("free_user")
    _capture()
    assert c.post("/clips/clip-1/to-editor").status_code == 403
    got = c.get("/clips/clip-1/file?download=1")
    assert got.status_code == 200
    assert got.content == CUT


def test_the_release_flag_refuses_even_a_pro_subscriber(client, monkeypatch):
    """The Editor is unreleased. The API has to say so too — otherwise a direct
    POST still writes to the shared disk while nobody can reach the tab."""
    monkeypatch.setattr(api.settings, "uploads_enabled", False)
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    assert c.post("/clips/clip-1/to-editor").status_code == 503


# ── the copy itself ──────────────────────────────────────────────────────────

def test_the_copy_is_byte_identical_to_the_capture(client):
    """It is a copy, not a re-encode: the whole cost argument for capture is
    that nothing on this box ever transcodes."""
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    up_id = c.post("/clips/clip-1/to-editor").json()["id"]
    up = upload_lib.get(up_id, "pro_user")
    assert upload_lib.path_for(up).read_bytes() == CUT


def test_the_captured_clip_is_still_there_afterwards(client):
    """A move would break the download button on a clip the user just edited,
    on every plan, and the retention sweep is what is supposed to free it."""
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    c.post("/clips/clip-1/to-editor")
    assert clip_files.exists("clip-1")


def test_the_library_card_says_where_it_came_from(client):
    """Distinguishable from a file the user picked off their desktop — the two
    have different provenance and the library shows both."""
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    assert c.post("/clips/clip-1/to-editor").json()["source"] == "clip"


def test_it_is_named_after_the_clip_not_the_uuid(client):
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    name = c.post("/clips/clip-1/to-editor").json()["filename"]
    assert "novafps" in name and "1v5 clutch" in name


def test_a_hostile_title_cannot_reach_the_filename(client):
    """Channel names and clip titles come from the platform, not from us."""
    c = client.login("pro_user")
    _clip("pro_user", clip_title="../../etc/passwd <script>")
    _capture()
    up = c.post("/clips/clip-1/to-editor").json()
    assert "/" not in up["filename"] and "<" not in up["filename"]


# ── clicking twice ───────────────────────────────────────────────────────────

def test_sending_the_same_clip_twice_makes_one_copy(client):
    """Without this, a double click silently spends the user's upload quota
    twice on identical bytes and leaves two library cards nothing tells apart."""
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    first  = c.post("/clips/clip-1/to-editor").json()
    second = c.post("/clips/clip-1/to-editor")
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]
    assert len(upload_lib.for_user("pro_user")) == 1


def test_deleting_the_editor_copy_lets_it_be_sent_again(client):
    """The link is a shortcut, not a tombstone: if the user cleared the copy
    out of their library, asking for it again has to work."""
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    first = c.post("/clips/clip-1/to-editor").json()["id"]
    assert c.delete(f"/uploads/{first}").status_code == 200
    again = c.post("/clips/clip-1/to-editor")
    assert again.status_code == 201
    assert again.json()["id"] != first


def test_another_users_copy_is_not_handed_over_by_the_link(client):
    """The stored id is looked up scoped to the caller. If it were trusted as a
    plain id, a clip record carrying someone else's upload id would return
    their file."""
    _clip("pro_user")
    _capture()
    api._clips["clip-1"]["editor_upload_id"] = "someone-elses-upload"
    c = client.login("pro_user")
    r = c.post("/clips/clip-1/to-editor")
    assert r.status_code == 201
    assert r.json()["id"] != "someone-elses-upload"


# ── ownership and missing files ──────────────────────────────────────────────

def test_another_users_clip_is_a_404(client):
    _clip("pro_user")
    _capture()
    c = client.login("other_pro")
    assert c.post("/clips/clip-1/to-editor").status_code == 404
    assert upload_lib.for_user("other_pro") == []


def test_a_clip_that_was_never_captured_cannot_be_edited(client):
    c = client.login("pro_user")
    _clip("pro_user")
    assert c.post("/clips/clip-1/to-editor").status_code == 404


def test_an_unknown_clip_is_a_404(client):
    c = client.login("pro_user")
    assert c.post("/clips/nope/to-editor").status_code == 404


def test_a_damaged_capture_does_not_blame_the_user_for_it(client):
    """The upload library's rejection is worded for somebody who picked a file
    off their desktop. Here the file is ours, so 'upload an MP4' is both wrong
    and impossible to act on."""
    c = client.login("pro_user")
    _clip("pro_user")
    _capture(data=b"not a video at all, twelve+")
    r = c.post("/clips/clip-1/to-editor")
    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "upload" not in detail
    assert upload_lib.for_user("pro_user") == []


# ── the caps still apply ─────────────────────────────────────────────────────

def test_a_full_library_refuses_the_copy_rather_than_the_disk_deciding(client, monkeypatch):
    """It routes through save_stream precisely so it inherits this. A second
    writer into the uploads directory would be a second place to forget it."""
    monkeypatch.setattr(api.settings, "upload_max_user_mb", 0)
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    r = c.post("/clips/clip-1/to-editor")
    assert r.status_code == 507
    assert upload_lib.for_user("pro_user") == []


def test_a_refused_copy_leaves_no_partial_file_behind(client, monkeypatch):
    """The likeliest reason to refuse is that a disk cap is already close, so
    leaving the bytes behind is the worst possible response to it."""
    monkeypatch.setattr(api.settings, "upload_max_file_mb", 0)
    c = client.login("pro_user")
    _clip("pro_user")
    _capture()
    assert c.post("/clips/clip-1/to-editor").status_code == 413
    leftovers = list((upload_lib._ROOT / "pro_user").glob("*")) \
        if (upload_lib._ROOT / "pro_user").exists() else []
    assert leftovers == []


# ── the library's own allowlist ──────────────────────────────────────────────

def test_the_source_field_is_allowlisted_not_passed_through(client):
    """It arrives on POST /uploads as a query string and is rendered in the
    library, so an arbitrary value must not be storable."""
    import asyncio

    async def _one(data):
        yield data

    up = asyncio.run(upload_lib.save_stream(
        "pro_user", "x.mp4", _one(CUT), source="<script>alert(1)</script>"))
    assert up.source == "upload"
    assert "clip" in upload_lib.SOURCES
