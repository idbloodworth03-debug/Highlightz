"""Undo for the destructive things a user can do to clips.

Reject, Cull and Clear queue all destroy work in one click, and two act in
bulk. Reject is the dangerous one: as well as removing the clip it teaches the
channel's profile that the moment was bad — raising the trigger threshold and
trimming the weights of whichever signals fired. So an accidental bulk reject
did not just lose clips, it made the detector measurably worse on that channel,
permanently, with no way back.

The hard part is not restoring the clip. It is un-teaching the profile, which
is why these tests care most about the threshold and the weights.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api, undo
    from src.auth import users as user_store
    from src.stats import stream_stats

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(stream_stats, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(undo, "_stacks", {})

    deleted: list[str] = []
    monkeypatch.setattr(api, "_delete_clip_file",
                        lambda c: deleted.append(c.get("storage_url", "")))

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "u1", "username": "one", "subscription_status": "active", "created_at": now},
    ]))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "u1", "username": "u1",
         "is_admin": False, "subscription_status": "active"}).encode())).decode())

    def add(cid, status="pending", channel="nova", score=80.0, **extra):
        api._clips[cid] = {
            "id": cid, "user_id": "u1", "status": status, "channel": channel,
            "created_at": now - 60, "trigger_score": score,
            "trigger_signals": [{"type": "CHAT_VELOCITY", "value": 0.9}], **extra}

    c.add = add
    c.api = api
    c.undo = undo
    c.deleted = deleted
    yield c


# ── restoring the clip ───────────────────────────────────────────────────────

def test_a_rejected_clip_comes_back(client):
    client.add("c1")
    client.post("/clips/c1/reject")
    assert "c1" not in client.api._clips
    r = client.post("/clips/undo")
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert "c1" in client.api._clips


def test_a_whole_cleared_queue_comes_back(client):
    for i in range(5):
        client.add(f"p{i}")
    client.post("/clips/clear-pending")
    assert not client.api._clips
    assert client.post("/clips/undo").json()["restored"] == 5
    assert len(client.api._clips) == 5


def test_a_cull_comes_back(client):
    client.add("lo1", score=10.0)
    client.add("lo2", score=12.0)
    client.add("hi", score=90.0)
    client.post("/clips/bulk-cull", json={"min_score": 50})
    assert set(client.api._clips) == {"hi"}
    client.post("/clips/undo")
    assert set(client.api._clips) == {"lo1", "lo2", "hi"}


def test_undo_is_last_action_first(client):
    client.add("a"); client.add("b")
    client.post("/clips/a/reject")
    client.post("/clips/b/reject")
    client.post("/clips/undo")
    assert "b" in client.api._clips and "a" not in client.api._clips


def test_undoing_twice_does_not_restore_the_same_clip_twice(client):
    client.add("c1")
    client.post("/clips/c1/reject")
    assert client.post("/clips/undo").json()["restored"] == 1
    assert client.post("/clips/undo").status_code == 404


def test_nothing_to_undo_is_a_404_not_a_crash(client):
    assert client.post("/clips/undo").status_code == 404


# ── the part that actually matters: un-teaching the formula ──────────────────

@pytest.mark.asyncio
async def test_undoing_a_reject_restores_the_channel_threshold(client, monkeypatch):
    """A reject raises the channel's trigger threshold. Left in place after an
    accidental reject, the channel quietly stops firing — the single most
    damaging part of the mistake, and invisible to the user."""
    from src.profiles.profile import StreamerProfile
    from src.profiles import manager

    profile = StreamerProfile(channel="nova")
    before_threshold = profile.trigger_threshold
    before_weights = dict(profile.signal_weights)

    class _PM:
        async def load(self, ch): return profile
        async def save(self, p): return None
    monkeypatch.setattr(manager, "get_profile_manager", lambda uid: _PM())

    client.add("c1")
    client.post("/clips/c1/reject")
    assert profile.trigger_threshold > before_threshold, "reject should raise the bar"
    assert profile.signal_weights["CHAT_VELOCITY"] < before_weights["CHAT_VELOCITY"]

    client.post("/clips/undo")
    assert profile.trigger_threshold == before_threshold
    assert profile.signal_weights == before_weights
    assert profile.rejected_clips == 0
    assert profile.total_clips == 0


def test_restore_is_a_snapshot_not_an_inverse():
    """record_clip clamps the threshold at a floor/ceiling and every weight into
    [0.75, 1.5], so subtracting the step back off does not always return you to
    where you started. Snapshot/restore is exact by construction."""
    from src.profiles.profile import StreamerProfile
    from src.dashboard import undo

    p = StreamerProfile(channel="nova")
    p.trigger_threshold = 79.6          # a hair under the ceiling
    snap = undo.snapshot_profile(p)
    for _ in range(5):                  # push it hard into the clamp
        p.record_clip(approved=False, signals=[{"type": "CHAT_VELOCITY", "value": 1.0}])
    undo.restore_profile(p, snap)
    assert p.trigger_threshold == 79.6
    assert p.signal_weights["CHAT_VELOCITY"] == snap["signal_weights"]["CHAT_VELOCITY"]


# ── the video file has to still be there ─────────────────────────────────────

def test_a_local_file_is_not_deleted_while_the_clip_is_still_undoable(client):
    """Unlinking the .mp4 at reject time would let undo restore a record that
    points at nothing — a clip that looks fine in the grid and plays silence."""
    client.add("c1", storage_url="/data/clips/c1.mp4")
    client.post("/clips/c1/reject")
    assert client.deleted == [], "the file was deleted before undo expired"
    client.post("/clips/undo")
    assert client.api._clips["c1"]["storage_url"] == "/data/clips/c1.mp4"


def test_the_file_is_deleted_once_the_undo_window_passes(client, monkeypatch):
    """Deferred, not skipped — otherwise rejecting would leak disk forever."""
    from src.dashboard import undo
    client.add("c1", storage_url="/data/clips/c1.mp4")
    client.post("/clips/c1/reject")
    monkeypatch.setattr(undo, "TTL_SECONDS", -1)      # everything is now expired
    client.get("/clips/undo")                          # any access sweeps
    assert client.deleted == ["/data/clips/c1.mp4"]


# ── the offer has to exist by the time the tab asks for it ───────────────────

@pytest.mark.parametrize("act", [
    lambda c: c.post("/clips/c1/reject"),
    lambda c: c.delete("/clips/c1"),
    lambda c: c.post("/clips/clear-pending"),
    lambda c: c.post("/clips/bulk-cull", json={"min_score": 99}),
])
def test_the_undo_entry_exists_before_clip_removed_goes_out(client, monkeypatch, act):
    """clip_removed is what makes the tab ask what it can undo. Broadcasting it
    before pushing the entry left a window — on reject, a profile load and save
    — in which the answer was "nothing" and the toast never appeared. Racy, so
    it looked like undo just did not work sometimes."""
    seen: list[bool] = []

    async def _spy(msg, **kw):
        if msg.get("event") == "clip_removed":
            seen.append(client.undo.peek("u1") is not None)
    monkeypatch.setattr(client.api, "broadcast", _spy)

    client.add("c1", score=10.0)
    act(client)
    assert seen, "no clip_removed was broadcast at all"
    assert all(seen), "clip_removed went out before the action was undoable"


# ── scoping and safety ───────────────────────────────────────────────────────

def test_a_stale_toast_cannot_undo_the_wrong_action(client):
    """Clicking an old toast after doing something else must do nothing, not
    silently restore an action the user had moved on from."""
    client.add("a"); client.add("b")
    client.post("/clips/a/reject")
    stale = client.get("/clips/undo").json()["id"]
    client.post("/clips/b/reject")
    r = client.post("/clips/undo", params={"entry_id": stale})
    assert r.status_code == 404
    assert "b" not in client.api._clips, "the newer action was undone by a stale id"


def test_the_undo_route_is_not_shadowed_by_the_clip_id_route():
    """FastAPI resolves in declaration order, so /clips/undo declared after
    /clips/{clip_id} is matched as clip_id="undo" and 404s."""
    from src.dashboard import api
    paths = [r.path for r in api.app.routes if getattr(r, "path", "").startswith("/clips")]
    assert paths.index("/clips/undo") < paths.index("/clips/{clip_id}")


def test_what_is_undoable_is_reported_for_the_toast(client):
    client.add("c1")
    client.post("/clips/c1/reject")
    body = client.get("/clips/undo").json()
    assert body["kind"] == "reject" and body["clips"] == 1
    assert "Rejected 1 clip" in body["label"]
    assert body["expires_at"] > time.time()


def test_the_buffer_is_bounded_so_a_long_session_cannot_grow_forever(client, monkeypatch):
    from src.dashboard import undo
    monkeypatch.setattr(undo, "MAX_PER_USER", 3)
    for i in range(10):
        client.add(f"c{i}")
        client.post(f"/clips/c{i}/reject")
    assert len(undo._stacks["u1"]) == 3


# ── the UI ───────────────────────────────────────────────────────────────────

def test_the_toast_hangs_off_clip_removed_so_every_action_gets_undo():
    """Rather than each destructive button remembering to offer undo, the toast
    keys off the one event all four already broadcast. Any future destructive
    action inherits undo instead of quietly shipping without it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "if(msg.event==='clip_removed') checkUndo();" in html


def test_the_undo_check_is_debounced():
    """Clearing a queue emits one clip_removed per clip. Without a debounce a
    50-clip clear would fire 50 identical requests for one buffer entry."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    i = html.index("const checkUndo")
    assert "setTimeout" in html[i:i + 400]


def test_the_toast_sends_the_entry_id_it_was_rendered_for():
    """Otherwise clicking a stale toast undoes whatever happened most recently,
    which is a different action than the one the user is looking at."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "entry_id=" in html


def test_the_toast_shows_the_remaining_window():
    """The offer expires server-side; a button that silently stops working is
    worse than one that shows a countdown."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "function UndoToast" in html and "expires_at" in html
