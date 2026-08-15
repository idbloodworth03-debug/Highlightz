"""Answering feedback, in the app.

NOT email. Twitch OAuth hands us no address, so the only emails we hold are the
ones Stripe gave us for people who have PAID — which excludes trial users, who
are exactly the people most likely to write in. An in-app reply reaches
everyone and needs no mail service.

The quiet failure to guard against is a reply nobody ever sees: answered in the
admin panel, never surfaced to the user, and no way for either side to tell.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(api, "_feedback", [])
    monkeypatch.setattr(api, "_save_feedback", lambda: None)
    # Module-level and NOT reset between requests in production, which is the
    # point of it — but it would otherwise carry one test's cooldown into the
    # next and every second test would see a spurious 429.
    monkeypatch.setattr(api, "_feedback_last_submit", {})

    sent = []

    async def _bc(msg, **kw): sent.append((msg, kw.get("user_id")))
    monkeypatch.setattr(api, "broadcast", _bc)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "boss", "username": "boss", "is_admin": True,
         "subscription_status": "active", "created_at": now},
        {"id": "u1", "username": "nova", "subscription_status": "trialing",
         "created_at": now},
    ]))

    def client_for(uid, admin):
        c = TestClient(api.app)
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": admin, "subscription_status": "active"}).encode())).decode())
        return c

    api._feedback.append({
        "id": "fb1", "user_id": "u1", "username": "nova", "category": "Question",
        "message": "Does it work on small channels?", "created_at": now, "read": False,
    })
    return {"api": api, "admin": client_for("boss", True),
            "user": client_for("u1", False), "sent": sent}


# ── the reply reaches the person who wrote in ────────────────────────────────

def test_a_reply_is_stored_on_the_thread(env):
    r = env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes — that is what it is for."})
    assert r.status_code == 200
    entry = env["api"]._feedback[0]
    assert entry["replies"][0]["message"] == "Yes — that is what it is for."


def test_the_user_can_read_their_own_thread(env):
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    body = env["user"].get("/feedback/mine").json()
    assert len(body) == 1
    assert body[0]["replies"][0]["message"] == "Yes."


def test_the_reply_reaches_the_open_tab_live(env):
    """Realtime contract. A reply that only appears on the next page load is
    the same as no reply for someone sitting in the app."""
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    events = [(m.get("event"), uid) for m, uid in env["sent"]]
    assert ("feedback_reply", "u1") in events, "the reply was never broadcast to its recipient"


def test_the_reply_is_broadcast_only_to_its_recipient(env):
    """user_id=None would push someone else's private answer to every socket."""
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    for msg, uid in env["sent"]:
        if msg.get("event") == "feedback_reply":
            assert uid == "u1", "the reply was broadcast unscoped"


# ── the badge tells each side what it means ──────────────────────────────────

def test_the_user_is_told_they_have_a_reply(env):
    """This endpoint used to return 0 for anyone who is not an admin, so a user
    could be answered and never find out."""
    assert env["user"].get("/feedback/unread-count").json()["count"] == 0
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    assert env["user"].get("/feedback/unread-count").json()["count"] == 1


def test_the_admin_badge_still_counts_unanswered_feedback(env):
    assert env["admin"].get("/feedback/unread-count").json()["count"] == 1
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    # Answering it IS reading it — leaving it lit would nag about a done job.
    assert env["admin"].get("/feedback/unread-count").json()["count"] == 0


def test_opening_the_tab_clears_the_users_badge(env):
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    env["user"].post("/feedback/mark-read")
    assert env["user"].get("/feedback/unread-count").json()["count"] == 0


# ── scoping ──────────────────────────────────────────────────────────────────

def test_one_user_cannot_read_anothers_feedback(env):
    env["api"]._feedback.append({
        "id": "fb2", "user_id": "someone_else", "username": "other",
        "category": "General", "message": "private", "created_at": time.time(),
        "read": False})
    ids = [t["id"] for t in env["user"].get("/feedback/mine").json()]
    assert ids == ["fb1"], "another user's feedback leaked into the thread list"


def test_marking_read_only_touches_your_own(env):
    env["api"]._feedback.append({
        "id": "fb2", "user_id": "someone_else", "message": "x",
        "created_at": time.time(), "read": False, "reply_unread": True})
    env["user"].post("/feedback/mark-read")
    other = next(f for f in env["api"]._feedback if f["id"] == "fb2")
    assert other["reply_unread"] is True, "cleared someone else's unread marker"


def test_only_an_admin_can_reply(env):
    r = env["user"].post("/admin/feedback/fb1/reply", json={"message": "hi"})
    assert r.status_code in (401, 403)


def test_replying_to_something_that_does_not_exist_is_a_404(env):
    r = env["admin"].post("/admin/feedback/nope/reply", json={"message": "hi"})
    assert r.status_code == 404


def test_an_empty_reply_is_refused(env):
    r = env["admin"].post("/admin/feedback/fb1/reply", json={"message": "   "})
    assert r.status_code == 400


# ── the user can answer back: it is a thread, not a one-way reply ────────────

def test_a_user_can_reply_on_their_own_thread(env):
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    r = env["user"].post("/feedback/fb1/reply", json={"message": "Great, thanks!"})
    assert r.status_code == 201
    replies = env["api"]._feedback[0]["replies"]
    assert [x["message"] for x in replies] == ["Yes.", "Great, thanks!"]
    assert replies[0]["from_admin"] is True and replies[1]["from_admin"] is False


def test_a_user_reply_reopens_the_thread_for_the_admin(env):
    """The quiet failure: someone answers a question, the item stays marked
    read, and their reply is filed as dealt-with and never seen again."""
    env["admin"].post("/admin/feedback/fb1/reply", json={"message": "Yes."})
    assert env["admin"].get("/feedback/unread-count").json()["count"] == 0
    env["user"].post("/feedback/fb1/reply", json={"message": "One more thing…"})
    assert env["admin"].get("/feedback/unread-count").json()["count"] == 1


def test_a_user_reply_reaches_the_admins_open_tab(env):
    """Realtime contract. Without this the admin only learns about it on their
    next reload."""
    env["user"].post("/feedback/fb1/reply", json={"message": "Hello?"})
    events = [(m.get("event"), uid) for m, uid in env["sent"]]
    assert ("feedback_new", "boss") in events, "no admin was notified"


def test_the_admin_notification_is_not_broadcast_to_everyone(env):
    """user_id=None here would put one customer's support thread on every
    socket in the product."""
    env["user"].post("/feedback/fb1/reply", json={"message": "Hello?"})
    for msg, uid in env["sent"]:
        if msg.get("event") == "feedback_new":
            assert uid is not None, "the notification went to every connected client"
            assert uid == "boss"


def test_a_user_cannot_reply_on_someone_elses_thread(env):
    env["api"]._feedback.append({
        "id": "fb2", "user_id": "someone_else", "username": "other",
        "category": "General", "message": "private", "created_at": time.time(),
        "read": True})
    r = env["user"].post("/feedback/fb2/reply", json={"message": "sneaking in"})
    assert r.status_code == 404, "replied onto another user's thread"
    other = next(f for f in env["api"]._feedback if f["id"] == "fb2")
    assert not other.get("replies")


def test_a_missing_thread_and_someone_elses_look_identical(env):
    """Different codes would confirm whether an id belongs to a real thread."""
    env["api"]._feedback.append({
        "id": "fb2", "user_id": "someone_else", "message": "x",
        "created_at": time.time(), "read": True})
    assert env["user"].post("/feedback/fb2/reply", json={"message": "hi"}).status_code == \
           env["user"].post("/feedback/nope/reply", json={"message": "hi"}).status_code == 404


def test_a_thread_cannot_grow_without_limit(env, monkeypatch):
    """_feedback is held in memory and rewritten whole on every save. This is a
    support conversation, not a chat room."""
    from src.dashboard import api
    monkeypatch.setattr(api, "_FEEDBACK_COOLDOWN", 0)
    env["api"]._feedback[0]["replies"] = [
        {"message": "x", "at": 0, "from_admin": False} for _ in range(api._MAX_THREAD_REPLIES)]
    r = env["user"].post("/feedback/fb1/reply", json={"message": "one more"})
    assert r.status_code == 429


def test_the_reply_rate_limit_is_shared_with_new_feedback(env):
    """Otherwise the cooldown on /feedback is trivially sidestepped by replying
    on an existing thread instead."""
    from src.dashboard import api
    assert env["user"].post("/feedback/fb1/reply", json={"message": "one"}).status_code == 201
    assert env["user"].post("/feedback/fb1/reply", json={"message": "two"}).status_code == 429
    assert env["user"].post("/feedback", json={"message": "three"}).status_code == 429


def test_both_sides_of_the_thread_are_labelled(env):
    """A thread where you cannot tell who said what is worse than no thread."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "r.from_admin===false?'You':'Highlightz'" in html
    from src.dashboard.api import _ADMIN_FEEDBACK_HTML as admin
    assert "from-user" in admin, "the admin panel renders both directions identically"
    assert "r.from_admin===false?' from-user':''" in admin, \
        "the class is defined but never applied — both directions render the same"
