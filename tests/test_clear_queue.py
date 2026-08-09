"""Clearing the review queue without teaching the formula anything.

THE POINT OF THIS FEATURE IS WHAT IT DOES NOT DO. A user with a backlog they
do not want to read should be able to wipe it and get fresh clips. If that
wipe went through /reject it would, for every clip:

  * raise the channel's trigger threshold and trim that clip's signal weights,
  * write a REJECTED training example into the learning dataset,
  * count against the keep rate shown to streamers.

None of which the user meant. They did not watch the clips; they skipped them.
Bulk impatience is the single most likely action to be taken at scale, so
letting it teach the detector would corrupt the formula faster than any amount
of careful reviewing could fix.

So every test below is a test that something did NOT happen.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(stream_stats, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    sent = []
    async def _capture(msg, user_id=None):
        sent.append((msg, user_id))
    monkeypatch.setattr(api, "broadcast", _capture)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "u1", "username": "one", "subscription_status": "active", "created_at": now},
        {"id": "u2", "username": "two", "subscription_status": "active", "created_at": now},
    ]))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": False, "subscription_status": "active"}).encode())).decode())
        return c

    def add(cid, uid="u1", status="pending", channel="novaplays", **extra):
        api._clips[cid] = {
            "id": cid, "user_id": uid, "status": status, "channel": channel,
            "created_at": now - 60, "trigger_score": 80.0,
            "trigger_signals": ["CHAT_VELOCITY"], **extra}

    c.login = login
    c.add = add
    c.sent = sent
    c.api = api
    c.stats_file = tmp_path / "stats.jsonl"
    yield c


def _events(client):
    if not client.stats_file.exists():
        return []
    return [_j.loads(l) for l in client.stats_file.read_text().splitlines() if l.strip()]


# ── it does the job ──────────────────────────────────────────────────────────

def test_it_empties_the_pending_queue(client):
    for i in range(5):
        client.add("p%d" % i)
    r = client.login("u1").post("/clips/clear-pending")
    assert r.status_code == 200 and r.json()["removed"] == 5
    assert not [c for c in client.api._clips.values() if c["status"] == "pending"]


def test_it_never_touches_the_approved_library(client):
    """"Clear the queue" must not reach into work the user already kept."""
    client.add("p1")
    client.add("keep1", status="approved")
    client.add("keep2", status="approved")
    client.login("u1").post("/clips/clear-pending")
    left = sorted(client.api._clips)
    assert left == ["keep1", "keep2"], left


def test_it_only_clears_your_own_clips(client):
    client.add("mine")
    client.add("theirs", uid="u2")
    client.login("u1").post("/clips/clear-pending")
    assert "theirs" in client.api._clips
    assert "mine" not in client.api._clips


def test_an_empty_queue_is_a_no_op_not_an_error(client):
    r = client.login("u1").post("/clips/clear-pending")
    assert r.status_code == 200 and r.json()["removed"] == 0


def test_it_requires_a_session(client):
    client.cookies.clear()
    r = client.post("/clips/clear-pending", follow_redirects=False)
    assert r.status_code in (302, 401), r.status_code


# ── and, crucially, what it does not do ──────────────────────────────────────

def test_it_does_not_teach_the_channel_profile(client, monkeypatch):
    """The formula must not move. If clearing recorded outcomes, a user tidying
    up would drag their own threshold around without ever judging a clip."""
    from src.profiles import manager
    touched = []
    monkeypatch.setattr(manager, "get_profile_manager",
                        lambda uid: (_ for _ in ()).throw(
                            AssertionError("clearing loaded a profile manager")))
    for i in range(3):
        client.add("p%d" % i)
    r = client.login("u1").post("/clips/clear-pending")
    assert r.status_code == 200
    assert not touched


def test_it_writes_no_training_examples(client, monkeypatch):
    """A skipped clip is not a labelled example. Feeding the dataset here would
    fill it with judgements nobody made."""
    from src.profiles import training_log
    logged = []
    monkeypatch.setattr(training_log, "log_outcome",
                        lambda clip, outcome: logged.append(outcome))
    for i in range(3):
        client.add("p%d" % i)
    client.login("u1").post("/clips/clear-pending")
    assert not logged, f"clearing wrote training examples: {logged}"


def test_it_records_no_rejections(client):
    """The keep rate is kept/(kept+rejected). A REJECTED row here would make a
    streamer's headline number collapse every time they skipped a backlog."""
    for i in range(4):
        client.add("p%d" % i)
    client.login("u1").post("/clips/clear-pending")
    kinds = [e["event"] for e in _events(client)]
    assert "rejected" not in kinds, kinds


def test_it_does_not_look_like_the_queue_overflowed(client):
    """evictions_since() counts EXPIRED to decide whether to tell the user they
    lost clips to a full queue. Reusing that event would accuse the product of
    losing work every time someone tidied up on purpose."""
    from src.stats import stream_stats
    for i in range(4):
        client.add("p%d" % i)
    before = time.time() - 1
    client.login("u1").post("/clips/clear-pending")
    assert stream_stats.evictions_since("u1", before) == 0
    assert "expired" not in [e["event"] for e in _events(client)]


def test_it_still_records_the_clips_as_cleared(client):
    """Recorded, just not blamed on anyone — otherwise CAUGHT stops reconciling
    against the sum of its outcomes and the admin ledger quietly loses clips."""
    from src.stats import stream_stats
    for i in range(3):
        client.add("p%d" % i)
    client.login("u1").post("/clips/clear-pending")
    kinds = [e["event"] for e in _events(client)]
    assert kinds.count(stream_stats.CLEARED) == 3, kinds


def test_a_grabbed_clip_is_removed_but_kept_out_of_the_channel_ledger(client):
    """Same carve-out as approve and reject: a grabbed clip was not produced by
    the formula, so it does not belong in that channel's outcome history."""
    client.add("g1", source="grabbed")
    client.add("p1")
    client.login("u1").post("/clips/clear-pending")
    assert not client.api._clips
    assert len(_events(client)) == 1, "the grabbed clip was written to the ledger"


# ── realtime contract ────────────────────────────────────────────────────────

def test_every_cleared_clip_is_broadcast_so_open_tabs_update_live(client):
    """The dashboard is a long-lived SPA. Without a per-clip broadcast the queue
    would still look full in every other tab until a manual refresh."""
    for i in range(3):
        client.add("p%d" % i)
    client.login("u1").post("/clips/clear-pending")
    removed = [m for m, uid in client.sent if m.get("event") == "clip_removed"]
    assert len(removed) == 3
    assert {m["clip_id"] for m in removed} == {"p0", "p1", "p2"}


def test_the_broadcast_is_scoped_to_the_owner(client):
    """user_id=None would push another person's queue changes to every socket."""
    client.add("p1")
    client.login("u1").post("/clips/clear-pending")
    assert all(uid == "u1" for _, uid in client.sent), client.sent


def test_the_frontend_handles_the_event_it_is_sent():
    """An event with no handler is the same as not sending it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "clip_removed" in DASHBOARD_HTML


# ── the button ───────────────────────────────────────────────────────────────

def test_the_button_exists_and_is_wired_to_the_endpoint():
    """An endpoint with no way to reach it is indistinguishable from not having
    built one."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "ClearQueueButton" in html
    assert "/clips/clear-pending" in html
    assert "Clear queue" in html


def test_the_button_asks_before_wiping_the_queue():
    """It is destructive and irreversible, and it sits one click from Approve."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "Yes, clear" in html and "Cancel" in html


def test_the_button_does_not_claim_to_reject_anything():
    """The label has to match the semantics, or a user will avoid it believing
    it will hurt their detector."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    i = html.index("ClearQueueButton")
    block = html[i:i + 2000]
    assert "without rejecting" in block
