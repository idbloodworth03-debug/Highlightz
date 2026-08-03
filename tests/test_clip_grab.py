"""Admins copying each other's featured clips.

The feature itself is small. What matters is that a grabbed clip is INERT to
every learning and reporting path: it was not produced by our formula for the
person grabbing it, so counting it corrupts the numbers being shown to
streamers and drifts a channel's threshold on borrowed evidence.
"""

import base64
import json as _j

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.profiles import manager as pm
    from src.stats import stream_stats as ss
    from src.profiles import training_log

    people = {
        "boss":  {"id": "boss", "username": "boss", "is_admin": True,
                  "subscription_status": "active", "plan": "pro"},
        "mate":  {"id": "mate", "username": "mate", "is_admin": True,
                  "subscription_status": "active", "plan": "pro"},
        "punter": {"id": "punter", "username": "punter",
                   "subscription_status": "active", "plan": "pro"},
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(training_log, "_LOG_FILE", tmp_path / "train.jsonl")
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json", raising=False)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    recorded = []

    class _Profile:
        def record_clip(self, approved, signals): recorded.append(approved)
        def to_dict(self): return {}

    class _PM:
        async def load(self, ch): return _Profile()
        async def save(self, p): return None
    monkeypatch.setattr(pm, "get_profile_manager", lambda uid: _PM())

    api._clips.clear()
    api._clips["orig"] = {
        "id": "orig", "user_id": "mate", "platform": "twitch", "channel": "aceu",
        "clip_title": "insane flick", "twitch_url": "https://clips.twitch.tv/AAA",
        "embed_url": "https://e/AAA", "status": "approved", "created_at": 1000.0,
        "trigger_score": 88,
    }
    monkeypatch.setattr(api, "_load_showcase",
                        lambda: [api._showcase_entry(api._clips["orig"])])
    monkeypatch.setattr(api, "_save_showcase", lambda items: None)

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": people[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c

    c.login = login
    c.recorded = recorded
    c.stats = ss
    c.api = api
    yield c
    api._clips.clear()


def test_an_admin_can_grab_a_featured_clip(client):
    c = client.login("boss")
    r = c.post("/admin/showcase/orig/grab")
    assert r.status_code == 200, r.text
    copy = r.json()
    assert copy["user_id"] == "boss"
    assert copy["twitch_url"] == "https://clips.twitch.tv/AAA"
    assert copy["id"] != "orig", "the copy reused the original's id"
    assert copy["status"] == "approved", "grabbing IS the approval"
    assert copy["grabbed_from"] == "mate", "lost who found it"


def test_nothing_is_re_hosted_only_the_twitch_link_is_copied(client):
    """The compliance line: the video stays on Twitch, same as every other clip
    in the product."""
    copy = client.login("boss").post("/admin/showcase/orig/grab").json()
    assert copy["twitch_url"].startswith("https://clips.twitch.tv/")
    assert "clip_path" not in copy and "file" not in copy


def test_a_grabbed_clip_carries_no_trigger_score(client):
    """Our formula never scored this for them. A copied score would put a
    fabricated row in the training data and read as a real detection."""
    copy = client.login("boss").post("/admin/showcase/orig/grab").json()
    assert "trigger_score" not in copy
    assert "trigger_signals" not in copy


def test_grabbing_twice_is_refused(client):
    c = client.login("boss")
    assert c.post("/admin/showcase/orig/grab").status_code == 200
    r = c.post("/admin/showcase/orig/grab")
    assert r.status_code == 409, "duplicates stack in the library"


def test_you_cannot_grab_your_own_clip(client):
    r = client.login("mate").post("/admin/showcase/orig/grab")
    assert r.status_code == 400


def test_a_non_admin_cannot_grab(client):
    """Featured clips belong to other people's accounts. Grabbing is a team
    tool, not a way for any user to copy from the landing page."""
    assert client.login("punter").post("/admin/showcase/orig/grab").status_code == 403


def test_grabbing_something_not_featured_is_a_404(client):
    assert client.login("boss").post("/admin/showcase/nope/grab").status_code == 404


# ── the part that matters ────────────────────────────────────────────────────

def test_a_grabbed_clip_never_reaches_the_clip_record(client):
    """The clip record is what gets shown to a streamer as "we caught N". A
    grabbed clip has no matching 'caught', so counting the approval would
    produce a channel with more kept than caught."""
    c = client.login("boss")
    copy = c.post("/admin/showcase/orig/grab").json()
    assert c.stats.for_user("boss") == [], "grabbing wrote to the clip record"

    # ...and rejecting it later must not either.
    assert c.post(f"/clips/{copy['id']}/reject").status_code == 200
    rows = c.stats.for_user("boss")
    assert rows == [], f"rejecting a grabbed clip recorded a phantom outcome: {rows}"


def test_a_grabbed_clip_never_teaches_the_channel_profile(client):
    """The formula did not produce it, so the decision says nothing about
    whether the formula was right — it would drift aceu's threshold on
    borrowed evidence."""
    c = client.login("boss")
    copy = c.post("/admin/showcase/orig/grab").json()
    c.post(f"/clips/{copy['id']}/reject")
    assert c.recorded == [], f"profile learned from a grabbed clip: {c.recorded}"


def test_a_normal_clip_still_records_and_teaches(client):
    """The guard must be narrow. If it swallowed ordinary clips it would
    silently stop all learning, which is the expensive way to be wrong."""
    c = client.login("boss")
    c.api._clips["real"] = {
        "id": "real", "user_id": "boss", "platform": "twitch", "channel": "aceu",
        "twitch_url": "https://clips.twitch.tv/BBB", "status": "pending",
        "created_at": 2000.0, "trigger_signals": [],
    }
    assert c.post("/clips/real/approve").status_code == 200
    assert c.recorded == [True], "a normal approval stopped teaching the profile"
    rows = c.stats.for_user("boss")
    assert rows and rows[0]["approved"] == 1, "a normal approval left the clip record"
