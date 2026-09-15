"""Fetching from Twitch as the API and the clip pipeline see it.

Three things are wired here and each has a way to go quietly wrong:

  * the PAYLOAD — `fetchable` is what turns "no file" from an explanation into
    a Download button, and `fetching` is what stops the button being pressed
    twice;
  * the ENDPOINT and the EDITOR path — on demand, owner-only, every plan for
    the download, and the editor fetches inline so Edit stays one click;
  * the FALLBACK — every NEW clip ends with a file, but capture gets first go,
    so the fetch must wait out the capture window and then stand down if a
    file appeared.

The download itself is faked throughout; tests/test_clip_fetch.py owns it.
"""

import asyncio
import base64
import inspect
import json as _j

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.clips import fetch as clip_fetch
from src.clips import files as clip_files
from src.dashboard import api
from src.uploads import library as upload_lib

PEOPLE = {
    "free_user": {"id": "free_user", "subscription_status": "none", "plan": "free"},
    "pro_user":  {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
    "other":     {"id": "other", "subscription_status": "active", "plan": "pro"},
}
CUT = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 8


def _clip(cid="c1", uid="pro_user", **over):
    base = {"id": cid, "user_id": uid, "channel": "aceu", "platform": "twitch",
            "status": "pending", "created_at": 1_000_000.0,
            "twitch_clip_id": "AwkwardSlug", "clip_title": "t"}
    base.update(over)
    return base


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    sent = []

    async def _record(event, user_id=None):
        sent.append((event, user_id))
    monkeypatch.setattr(api, "broadcast", _record)
    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()
    clip_files._forget_scan()
    monkeypatch.setattr(upload_lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(upload_lib, "_INDEX", tmp_path / "uploads.json")
    upload_lib.load()
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    monkeypatch.setattr(clip_fetch.settings, "clip_fetch_enabled", True)
    monkeypatch.setattr(clip_fetch, "_inflight", {})
    monkeypatch.setattr(clip_fetch, "_slot", asyncio.Semaphore(1))
    api._clips.clear()
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    c = TestClient(api.app)

    def login(uid):
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid,
            "subscription_status": PEOPLE[uid]["subscription_status"]}).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c
    c.login = login
    c.sent = sent
    yield c
    api._clips.clear()
    upload_lib._uploads.clear()


def _fake_fetch(monkeypatch, *, fail=False, calls=None):
    """Stand in for the download: writes the file, or raises."""
    calls = calls if calls is not None else []

    async def fake(clip_id, slug):
        calls.append((clip_id, slug))
        if fail:
            raise clip_fetch.FetchFailed("twitch said no")
        p = clip_files.path_for(clip_id)
        p.write_bytes(CUT)
        clip_files._forget_scan()
        return p
    monkeypatch.setattr(clip_fetch, "fetch", fake)
    return calls


# ── the payload ──────────────────────────────────────────────────────────────

def test_a_twitch_clip_with_no_file_is_fetchable(client):
    api._clips["c1"] = _clip()
    row = client.login("pro_user").get("/clips").json()[0]
    assert row["file_state"] != "ready" and row["fetchable"] is True


def test_fetchable_is_false_when_the_feature_is_off(client, monkeypatch):
    monkeypatch.setattr(clip_fetch.settings, "clip_fetch_enabled", False)
    api._clips["c1"] = _clip()
    assert client.login("pro_user").get("/clips").json()[0]["fetchable"] is False


def test_a_clip_that_already_has_a_file_is_not_fetchable(client):
    api._clips["c1"] = _clip()
    clip_files.path_for("c1").write_bytes(CUT)
    row = client.login("pro_user").get("/clips").json()[0]
    assert row["has_file"] is True and row["fetchable"] is False


def test_a_non_twitch_clip_is_not_fetchable(client):
    api._clips["c1"] = _clip(platform="kick")
    assert client.login("pro_user").get("/clips").json()[0]["fetchable"] is False


def test_a_clip_being_fetched_says_so(client, monkeypatch):
    """Supersedes the other no-file reasons: whatever left it without a file,
    one is on its way, and the button must not be pressable twice."""
    api._clips["c1"] = _clip()
    monkeypatch.setattr(clip_fetch, "in_flight", lambda cid: cid == "c1")
    assert client.login("pro_user").get("/clips").json()[0]["file_state"] == "fetching"


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_fetch_is_on_every_plan_like_the_download(client, monkeypatch):
    started = []
    monkeypatch.setattr(api, "_start_fetch", lambda clip: started.append(clip["id"]))
    api._clips["c1"] = _clip(uid="free_user")
    r = client.login("free_user").post("/clips/c1/fetch")
    assert r.status_code == 202 and r.json()["file_state"] == "fetching"
    assert started == ["c1"]


def test_someone_elses_clip_is_a_404_not_a_fetch(client, monkeypatch):
    started = []
    monkeypatch.setattr(api, "_start_fetch", lambda clip: started.append(clip["id"]))
    api._clips["c1"] = _clip(uid="pro_user")
    assert client.login("other").post("/clips/c1/fetch").status_code == 404
    assert started == []


def test_a_clip_with_a_file_is_reported_ready_and_not_refetched(client, monkeypatch):
    started = []
    monkeypatch.setattr(api, "_start_fetch", lambda clip: started.append(clip["id"]))
    api._clips["c1"] = _clip()
    clip_files.path_for("c1").write_bytes(CUT)
    r = client.login("pro_user").post("/clips/c1/fetch")
    assert r.json()["file_state"] == "ready" and started == []


def test_the_endpoint_refuses_when_the_feature_is_off(client, monkeypatch):
    monkeypatch.setattr(clip_fetch.settings, "clip_fetch_enabled", False)
    api._clips["c1"] = _clip()
    r = client.login("pro_user").post("/clips/c1/fetch")
    assert r.status_code == 409 and "switched off" in r.json()["detail"]


def test_the_endpoint_refuses_a_clip_with_nothing_to_fetch(client):
    api._clips["c1"] = _clip(twitch_clip_id="")
    assert client.login("pro_user").post("/clips/c1/fetch").status_code == 409


# ── the editor path ──────────────────────────────────────────────────────────

def test_edit_on_a_clip_with_no_file_fetches_it_first(client, monkeypatch):
    """One click. A 202-and-come-back would make it two, separated by a toast."""
    calls = _fake_fetch(monkeypatch)
    api._clips["c1"] = _clip()
    r = client.login("pro_user").post("/clips/c1/to-editor")
    assert r.status_code == 201, r.text
    assert calls == [("c1", "AwkwardSlug")]
    assert any(e.get("event") == "clip_file_ready" and e.get("clip_id") == "c1"
               for e, _ in client.sent), "the clip card was not told it has a file now"


def test_edit_does_not_fetch_a_clip_that_already_has_a_file(client, monkeypatch):
    calls = _fake_fetch(monkeypatch)
    api._clips["c1"] = _clip()
    clip_files.path_for("c1").write_bytes(CUT)
    assert client.login("pro_user").post("/clips/c1/to-editor").status_code == 201
    assert calls == []


def test_edit_says_so_when_twitch_will_not_hand_over_the_clip(client, monkeypatch):
    _fake_fetch(monkeypatch, fail=True)
    api._clips["c1"] = _clip()
    r = client.login("pro_user").post("/clips/c1/to-editor")
    assert r.status_code == 502 and "Twitch" in r.json()["detail"]


def test_edit_on_an_unfetchable_clip_with_no_file_is_still_a_404(client, monkeypatch):
    calls = _fake_fetch(monkeypatch)
    api._clips["c1"] = _clip(platform="kick")
    assert client.login("pro_user").post("/clips/c1/to-editor").status_code == 404
    assert calls == []


# ── the fallback for new clips ───────────────────────────────────────────────

@pytest.fixture
def fallback(client, monkeypatch):
    """The fallback with its wait removed and its fetch recorded."""
    announced = []

    async def _no_sleep(_s):
        return None

    async def _announce(clip_id, uid, slug):
        announced.append((clip_id, uid, slug))
    monkeypatch.setattr(api.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(api, "_fetch_and_announce", _announce)
    return announced


def test_the_fallback_fetches_a_new_clip_capture_did_not_produce(fallback):
    api._clips["c1"] = _clip()
    asyncio.run(api._fetch_when_capture_misses(api._clips["c1"]))
    assert fallback == [("c1", "pro_user", "AwkwardSlug")]


def test_the_fallback_stands_down_when_capture_got_it(fallback):
    """Capture is primary: free, better quality, never touches Twitch."""
    api._clips["c1"] = _clip()
    clip_files.path_for("c1").write_bytes(CUT)
    asyncio.run(api._fetch_when_capture_misses(api._clips["c1"]))
    assert fallback == []


def test_the_fallback_stands_down_for_a_clip_reviewed_and_gone(fallback):
    clip = _clip()                          # never put in _clips
    asyncio.run(api._fetch_when_capture_misses(clip))
    assert fallback == []


def test_the_fallback_does_nothing_when_the_feature_is_off(fallback, monkeypatch):
    monkeypatch.setattr(clip_fetch.settings, "clip_fetch_enabled", False)
    api._clips["c1"] = _clip()
    asyncio.run(api._fetch_when_capture_misses(api._clips["c1"]))
    assert fallback == []


def test_the_fallback_waits_out_the_capture_window():
    """The cut cannot start until the post-roll and two segments have been
    broadcast; fetching before that would race the capture it exists to back
    up, and would ask Twitch for a clip it has not finished encoding."""
    src = inspect.getsource(api._fetch_when_capture_misses)
    assert "clip_post_roll_seconds" in src and "clip_capture_segment_s" in src
    assert src.index("sleep(") < src.index("exists(")


def test_every_new_clip_schedules_the_fallback():
    src = inspect.getsource(api.notify_clip_ready)
    tail = src[src.index('"clip_ready"'):]
    assert "_fetch_when_capture_misses" in tail, \
        "a new clip is announced but the file fallback is never scheduled"


# ── announcing the outcome ───────────────────────────────────────────────────

def test_success_reuses_the_capture_event(client, monkeypatch):
    """So a fetched file appears exactly the way a captured one does — the
    frontend handler already exists."""
    _fake_fetch(monkeypatch)
    asyncio.run(api._fetch_and_announce("c1", "pro_user", "AwkwardSlug"))
    assert client.sent == [({"event": "clip_file_ready", "clip_id": "c1"}, "pro_user")]


def test_failure_gets_its_own_event_scoped_to_the_owner(client, monkeypatch):
    _fake_fetch(monkeypatch, fail=True)
    asyncio.run(api._fetch_and_announce("c1", "pro_user", "AwkwardSlug"))
    (event, uid), = client.sent
    assert event["event"] == "clip_fetch_failed" and event["clip_id"] == "c1"
    assert "Twitch" in event["message"] and uid == "pro_user"


def test_disabled_announces_nothing(client, monkeypatch):
    monkeypatch.setattr(clip_fetch.settings, "clip_fetch_enabled", False)
    asyncio.run(api._fetch_and_announce("c1", "pro_user", "AwkwardSlug"))
    assert client.sent == []
