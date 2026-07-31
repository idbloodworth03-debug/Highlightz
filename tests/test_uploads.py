"""
Clip Upload library.

This is the only feature in Highlightz that accepts attacker-controlled bytes
and writes them to the production disk, so the tests here are mostly about the
ways that goes wrong rather than about the happy path:

  * a full disk is not an "uploads are broken" event — it stops clipping,
    billing writes and the dashboard too, so the caps have to hold under a
    lying client;
  * filenames come from a browser and must never reach a path;
  * Content-Type is set by the client and proves nothing about the bytes;
  * one user must never read or delete another user's file.
"""

import asyncio
import json

import pytest

from src.uploads import library as lib
from config.settings import settings


# Real container magic. sniff_container reads the first 12 bytes.
MP4  = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
MOV  = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 72
EXE  = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00" + b"\x00" * 64


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the library at a scratch dir so tests never touch real storage."""
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    yield tmp_path
    lib._uploads.clear()


async def _agen(data: bytes, chunk: int = 8192):
    for i in range(0, len(data), chunk):
        yield data[i:i + chunk]


def save(user, data, name="clip.mp4"):
    return asyncio.run(lib.save_stream(user, name, _agen(data)))


# ── Container sniffing ────────────────────────────────────────────────────────

def test_sniff_recognises_real_containers_and_rejects_everything_else():
    assert lib.sniff_container(MP4) == "mp4"
    assert lib.sniff_container(MOV) == "mov"
    assert lib.sniff_container(WEBM) == "webm"
    assert lib.sniff_container(EXE) is None
    assert lib.sniff_container(b"") is None
    assert lib.sniff_container(b"short") is None          # too few bytes to judge
    # 'ftyp' has to be at the box offset, not merely present somewhere.
    assert lib.sniff_container(b"ftyp" + b"\x00" * 20) is None


def test_upload_is_judged_by_bytes_not_by_the_name_or_content_type():
    """A browser will happily label anything video/mp4, and a user can name a
    payload clip.mp4. Neither may be what decides the file is a video."""
    with pytest.raises(lib.UploadError) as e:
        save("u1", EXE, name="totally_a_clip.mp4")
    assert "video" in str(e.value).lower()
    assert lib.for_user("u1") == []


def test_empty_file_is_rejected():
    with pytest.raises(lib.UploadError):
        save("u1", b"")


# ── Paths are never built from client input ───────────────────────────────────

def test_traversal_in_the_filename_cannot_escape_the_upload_dir(isolated_store):
    """The stored path is a server UUID; the client name is display text only.

    If this ever regressed to joining the client name onto a directory, a name
    like ../../../etc/cron.d/x would be a remote write primitive.
    """
    up = save("u1", MP4, name="../../../../etc/passwd")
    path = lib.path_for(up)
    root = (isolated_store / "uploads").resolve()
    assert root in path.resolve().parents, f"{path} escaped {root}"
    assert path.name == f"{up.id}.mp4"          # UUID, not the client's name
    assert "/" not in up.filename and ".." not in up.filename


def test_display_name_is_stripped_of_markup_and_control_characters():
    up = save("u1", MP4, name='<img src=x onerror=alert(1)>.mp4')
    assert "<" not in up.filename and ">" not in up.filename
    # Something human-readable survives rather than becoming empty.
    assert up.filename


def test_display_name_never_ends_up_empty():
    up = save("u1", MP4, name="???")
    assert up.filename == "clip"


# ── Caps ──────────────────────────────────────────────────────────────────────

def test_oversized_file_is_cut_off_mid_stream_and_leaves_no_disk_behind(monkeypatch, isolated_store):
    """The size check runs DURING the copy, not after it.

    Checking afterwards means the bytes have already landed — on a 50 GB disk
    shared with clipping and billing, "reject it once it's written" is not a
    cap at all. Also asserts the partial file is cleaned up, which matters
    precisely because the likely reason for rejection is being near a cap.
    """
    monkeypatch.setattr(settings, "upload_max_file_mb", 1)
    big = MP4 + b"\x00" * (2 * lib.MB)

    consumed = 0

    async def counting():
        nonlocal consumed
        async for c in _agen(big, chunk=64 * 1024):
            consumed += len(c)
            yield c

    with pytest.raises(lib.UploadError) as e:
        asyncio.run(lib.save_stream("u1", "big.mp4", counting()))
    assert e.value.status == 413
    # Stopped early rather than draining the whole 2 MB body.
    assert consumed < len(big), "cap only applied after the full read"
    # No partial file, and nothing recorded.
    leftovers = list((isolated_store / "uploads").rglob("*")) if (isolated_store / "uploads").exists() else []
    assert [p for p in leftovers if p.is_file()] == []
    assert lib.for_user("u1") == []


def test_per_user_quota_blocks_the_next_upload(monkeypatch):
    save("u1", MP4)
    monkeypatch.setattr(settings, "upload_max_user_mb", 0)   # no room left
    with pytest.raises(lib.UploadError) as e:
        save("u1", MP4)
    assert e.value.status == 507
    # ...and it is per-user, not global: a different user is unaffected by u1.
    monkeypatch.setattr(settings, "upload_max_user_mb", 2048)
    assert save("u2", MP4)


def test_global_cap_stops_everyone(monkeypatch):
    save("u1", MP4)
    monkeypatch.setattr(settings, "upload_max_total_mb", 0)
    with pytest.raises(lib.UploadError) as e:
        save("u2", MP4)
    assert e.value.status == 507
    assert "full" in str(e.value).lower()


def test_quota_reports_usage_and_frees_it_on_delete():
    up = save("u1", MP4)
    q = lib.quota("u1")
    assert q["used"] == len(MP4) and q["count"] == 1
    assert q["remaining"] == q["limit"] - len(MP4)
    lib.delete(up.id, "u1")
    assert lib.quota("u1")["used"] == 0


# ── Ownership ─────────────────────────────────────────────────────────────────

def test_one_user_cannot_read_or_delete_anothers_upload():
    up = save("owner", MP4)
    assert lib.get(up.id, "owner") is not None
    # A wrong owner is indistinguishable from a missing id, so the endpoint
    # cannot be used to probe which ids exist.
    assert lib.get(up.id, "attacker") is None
    assert lib.delete(up.id, "attacker") is None
    assert lib.get(up.id, "owner") is not None, "attacker's delete took effect"


def test_public_shape_never_leaks_the_owner():
    up = save("u1", MP4)
    d = up.public()
    assert "user_id" not in d
    assert d["url"] == f"/uploads/{up.id}/file"


def test_for_user_is_scoped_and_newest_first():
    a = save("u1", MP4)
    b = save("u1", WEBM)
    save("u2", MP4)
    ids = [u.id for u in lib.for_user("u1")]
    assert set(ids) == {a.id, b.id}
    assert ids[0] == b.id          # newest first


def test_account_deletion_removes_the_files_not_just_the_records():
    up = save("u1", MP4)
    path = lib.path_for(up)
    assert path.exists()
    save("u2", MP4)
    assert lib.delete_all_for_user("u1") == 1
    assert not path.exists(), "video outlived the account that owned it"
    assert lib.for_user("u1") == []
    assert len(lib.for_user("u2")) == 1


# ── Persistence ───────────────────────────────────────────────────────────────

def test_index_survives_a_restart(isolated_store):
    up = save("u1", MP4)
    lib.load()                      # simulate a process restart
    again = lib.get(up.id, "u1")
    assert again is not None and again.size == len(MP4)


def test_a_corrupt_index_does_not_take_the_process_down(isolated_store):
    """A truncated write (disk full, kill -9 mid-deploy) must degrade to an
    empty library, not an exception at import time that breaks the dashboard."""
    (isolated_store / "uploads.json").write_text("{not json")
    lib.load()
    assert lib.for_user("u1") == []


def test_index_write_is_atomic(isolated_store):
    save("u1", MP4)
    idx = isolated_store / "uploads.json"
    assert json.loads(idx.read_text())          # complete, parseable JSON
    # The temp file used for the atomic replace is not left behind.
    assert not (isolated_store / "uploads.json.tmp").exists()


# ── HTTP layer ────────────────────────────────────────────────────────────────
#
# The library enforces ownership and quota, but that only helps if the routes
# actually call it. These drive the real FastAPI app through its auth
# middleware so a route that forgot the plan gate or the owner check fails.

@pytest.fixture
def client(monkeypatch, isolated_store):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    people = {
        "pro_user":     {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
        "starter_user": {"id": "starter_user", "subscription_status": "active", "plan": "starter"},
        "other_pro":    {"id": "other_pro", "subscription_status": "active", "plan": "pro"},
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    # Broadcasts need a running loop and real sockets; the realtime contract is
    # covered by test_realtime_contract.py, so stub it out here.
    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    c = TestClient(api.app)

    def login(uid):
        # Mint a session cookie the same way the app does, rather than
        # bypassing the middleware — so the gate under test really runs.
        c.cookies.clear()
        with c as cc:
            pass
        from itsdangerous import TimestampSigner
        import base64, json as _j
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid,
            "subscription_status": people[uid]["subscription_status"],
        }).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    return c


def test_starter_plan_is_refused_at_every_upload_route(client):
    c = client.login("starter_user")
    assert c.get("/uploads").status_code == 403
    r = c.post("/uploads", files={"file": ("c.mp4", MP4, "video/mp4")})
    assert r.status_code == 403
    assert "Pro" in r.json()["detail"]


def test_pro_plan_can_upload_list_fetch_and_delete(client):
    c = client.login("pro_user")
    r = c.post("/uploads", files={"file": ("clip.mp4", MP4, "video/mp4")})
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["kind"] == "mp4" and up["size"] == len(MP4)
    assert "user_id" not in up

    listed = c.get("/uploads").json()
    assert [u["id"] for u in listed["uploads"]] == [up["id"]]
    assert listed["quota"]["used"] == len(MP4)

    got = c.get(f"/uploads/{up['id']}/file")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("video/mp4")
    # Served inline for playback, and the browser is told not to re-sniff it
    # into something else.
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.content == MP4

    assert c.delete(f"/uploads/{up['id']}").status_code == 200
    assert c.get("/uploads").json()["uploads"] == []


def test_a_non_video_is_refused_over_http_despite_a_video_content_type(client):
    c = client.login("pro_user")
    r = c.post("/uploads", files={"file": ("payload.mp4", EXE, "video/mp4")})
    assert r.status_code == 400
    assert c.get("/uploads").json()["uploads"] == []


def test_another_users_upload_is_a_404_not_a_download(client):
    c = client.login("pro_user")
    up = c.post("/uploads", files={"file": ("mine.mp4", MP4, "video/mp4")}).json()

    c2 = client.login("other_pro")
    assert c2.get(f"/uploads/{up['id']}/file").status_code == 404
    assert c2.delete(f"/uploads/{up['id']}").status_code == 404
    assert c2.get("/uploads").json()["uploads"] == []

    # The owner still has it — the other user's DELETE did nothing.
    c3 = client.login("pro_user")
    assert c3.get(f"/uploads/{up['id']}/file").status_code == 200


def test_unauthenticated_requests_never_reach_the_library(client):
    client.cookies.clear()
    assert client.get("/uploads", headers={"accept": "application/json"}).status_code == 401


def test_playback_supports_range_requests(client):
    """Browsers seek video with Range requests. Without a 206 the user cannot
    scrub, and every seek re-downloads the whole file — which on a 300 MB clip
    is both a bad experience and real bandwidth off the droplet."""
    c = client.login("pro_user")
    up = c.post("/uploads", files={"file": ("clip.mp4", MP4, "video/mp4")}).json()
    r = c.get(f"/uploads/{up['id']}/file", headers={"Range": "bytes=4-11"})
    assert r.status_code == 206, "no partial-content support — video cannot be scrubbed"
    assert r.content == MP4[4:12]
    assert r.headers["content-range"] == f"bytes 4-11/{len(MP4)}"
