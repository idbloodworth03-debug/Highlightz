"""Reaching out to somebody who has NOT written in.

THE GAP THIS FILLS. Every support path was reactive: the admin could only
answer a thread the user opened. So the people most worth contacting — someone
who stopped at the paywall, a trial about to lapse, a user whose streams had to
be stopped — were exactly the ones with no channel at all, because not writing
in was the whole problem.

NOT EMAIL, and that is the constraint the whole design turns on. Twitch OAuth
hands us no address unless the user signs in again under the new scope, so the
only emails on file are Stripe's — i.e. people who have already paid. An in-app
message reaches everybody and rides the socket that is already there.

THE SHAPE, AND WHY IT MATTERS. An admin-started thread is a normal feedback
entry with an EMPTY opening message and the admin's text as the first reply.
That reuses every renderer that already exists, on both sides, with the right
attribution. Putting the admin's words in `message` instead would have made
every existing reader of that field — the admin list, the user's own thread
view, any export — show the admin's words as if the USER had written them.
Several tests below exist only to hold that down.
"""

import base64
import json as _j
import re
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
    monkeypatch.setattr(api, "_feedback_last_submit", {})

    saves = []
    monkeypatch.setattr(api, "_save_feedback", lambda: saves.append(1))

    sent = []

    async def _bc(msg, **kw):
        sent.append((msg, kw.get("user_id")))
    monkeypatch.setattr(api, "broadcast", _bc)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "boss", "username": "boss", "is_admin": True,
         "subscription_status": "active", "created_at": now},
        {"id": "u1", "username": "nova", "twitch_login": "nova_tv",
         "subscription_status": "trialing", "created_at": now},
        {"id": "u2", "username": "kestrel", "subscription_status": "none",
         "created_at": now},
    ]))

    def client_for(uid, admin):
        c = TestClient(api.app)
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": admin, "subscription_status": "active"}).encode())).decode())
        return c

    return {"api": api, "admin": client_for("boss", True),
            "u1": client_for("u1", False), "u2": client_for("u2", False),
            "sent": sent, "saves": saves}


def _send(env, ids, msg="Saw you stopped at the card form — anything I can help with?"):
    return env["admin"].post("/admin/feedback/new",
                             json={"user_ids": ids, "message": msg})


# ── it reaches somebody who never wrote in ───────────────────────────────────

def test_a_message_reaches_a_user_who_has_never_sent_feedback(env):
    assert env["u1"].get("/feedback/mine").json() == []

    r = _send(env, ["u1"])
    assert r.status_code == 201
    assert r.json()["sent"] == 1

    mine = env["u1"].get("/feedback/mine").json()
    assert len(mine) == 1
    assert mine[0]["replies"][0]["message"].startswith("Saw you stopped")


def test_the_message_is_attributed_to_us_and_not_to_them(env):
    """THE ONE THAT MATTERS MOST. Every renderer on both sides reads `message`
    as the user's own words. If our text landed there, the user would open
    their tab and find a message they never wrote, signed as themselves."""
    _send(env, ["u1"], "Your trial ends tomorrow.")
    t = env["u1"].get("/feedback/mine").json()[0]

    assert t["message"] == "", "our words were stored as if the user wrote them"
    assert t["from_admin_start"] is True
    assert len(t["replies"]) == 1
    assert t["replies"][0]["from_admin"] is True, \
        "our own message renders as if it came from the user"
    assert t["replies"][0]["message"] == "Your trial ends tomorrow."


def test_the_screen_is_told_not_to_draw_an_empty_bubble(env):
    """`from_admin_start` has to survive the serialiser, or the tab renders an
    empty quote block above every message we send."""
    _send(env, ["u1"])
    assert "from_admin_start" in env["u1"].get("/feedback/mine").json()[0]


def test_it_lights_the_recipients_badge(env):
    assert env["u1"].get("/feedback/unread-count").json()["count"] == 0
    _send(env, ["u1"])
    assert env["u1"].get("/feedback/unread-count").json()["count"] == 1


def test_opening_the_tab_clears_the_badge(env):
    _send(env, ["u1"])
    env["u1"].post("/feedback/mark-read")
    assert env["u1"].get("/feedback/unread-count").json()["count"] == 0


def test_it_does_not_light_the_admins_own_queue(env):
    """A thread the admin just wrote has nothing for the admin to action.
    Leaving it unread would put a permanent number on their own nav badge."""
    _send(env, ["u1"])
    assert env["admin"].get("/feedback/unread-count").json()["count"] == 0
    assert env["api"]._feedback[0]["read"] is True


# ── it goes to the right person and nobody else ──────────────────────────────

def test_it_does_not_reach_anyone_it_was_not_addressed_to(env):
    _send(env, ["u1"])
    assert env["u2"].get("/feedback/mine").json() == []
    assert env["u2"].get("/feedback/unread-count").json()["count"] == 0


def test_the_socket_event_is_scoped_to_each_recipient(env):
    """Realtime contract: `broadcast(..., user_id=None)` goes to EVERY socket.
    A support message on every user's screen is the worst version of this
    feature working."""
    _send(env, ["u1", "u2"])
    assert len(env["sent"]) == 2
    for msg, uid in env["sent"]:
        assert uid in ("u1", "u2"), "a private message was broadcast to everyone"
        assert uid is not None
    assert {uid for _, uid in env["sent"]} == {"u1", "u2"}


def test_the_event_is_not_the_reply_event(env):
    """Reusing `feedback_reply` would tell somebody who has never contacted us
    that they have "a reply to your feedback" — which reads as our bug, not as
    our message."""
    _send(env, ["u1"])
    assert env["sent"][0][0]["event"] == "feedback_message"


def test_the_event_carries_the_thread_it_belongs_to(env):
    _send(env, ["u1"])
    evt = env["sent"][0][0]
    assert evt["feedback_id"] == env["api"]._feedback[0]["id"]


# ── more than one person at a time ───────────────────────────────────────────

def test_one_send_reaches_several_people_with_a_thread_each(env):
    r = _send(env, ["u1", "u2"])
    assert r.json()["sent"] == 2
    assert len(env["u1"].get("/feedback/mine").json()) == 1
    assert len(env["u2"].get("/feedback/mine").json()) == 1
    ids = {f["id"] for f in env["api"]._feedback}
    assert len(ids) == 2, "two recipients shared one thread"


def test_the_same_person_twice_gets_one_message_not_two(env):
    """Selecting somebody under two different filters is one person."""
    r = _send(env, ["u1", "u1", "u1"])
    assert r.json()["sent"] == 1
    assert len(env["u1"].get("/feedback/mine").json()) == 1


def test_an_unknown_id_is_reported_and_does_not_sink_the_whole_send(env):
    """A stale tab holding a deleted user must not cost everyone else their
    message."""
    r = _send(env, ["u1", "ghost", "u2"])
    body = r.json()
    assert body["sent"] == 2
    assert [s["user_id"] for s in body["skipped"]] == ["ghost"]
    assert len(env["u1"].get("/feedback/mine").json()) == 1


def test_the_batch_is_written_once_not_once_per_person(env):
    """_feedback is held in memory and the whole file is rewritten on save."""
    _send(env, ["u1", "u2"])
    assert len(env["saves"]) == 1


def test_a_send_that_reaches_nobody_writes_nothing(env):
    r = _send(env, ["ghost1", "ghost2"])
    assert r.json()["sent"] == 0
    assert env["saves"] == []
    assert env["sent"] == []


# ── they can answer, and it comes back to us ─────────────────────────────────

def test_the_user_can_reply_on_a_thread_they_did_not_start(env):
    """Otherwise this is an announcement system, not a way to reach people —
    and the reply endpoint checks ownership, which an admin-started thread has
    to satisfy."""
    _send(env, ["u1"])
    tid = env["u1"].get("/feedback/mine").json()[0]["id"]
    r = env["u1"].post("/feedback/" + tid + "/reply", json={"message": "Yes — the card was declined."})
    assert r.status_code == 201
    thread = env["u1"].get("/feedback/mine").json()[0]
    assert [x["from_admin"] for x in thread["replies"]] == [True, False]


def test_their_answer_puts_the_thread_back_in_the_admin_queue(env):
    _send(env, ["u1"])
    tid = env["u1"].get("/feedback/mine").json()[0]["id"]
    env["u1"].post("/feedback/" + tid + "/reply", json={"message": "Yes."})
    assert env["api"]._feedback[0]["read"] is False
    assert env["admin"].get("/feedback/unread-count").json()["count"] == 1


def test_somebody_else_still_cannot_reply_on_that_thread(env):
    _send(env, ["u1"])
    tid = env["u1"].get("/feedback/mine").json()[0]["id"]
    assert env["u2"].post("/feedback/" + tid + "/reply",
                          json={"message": "not mine"}).status_code == 404


def test_the_admin_can_keep_replying_on_a_thread_they_started(env):
    _send(env, ["u1"])
    tid = env["api"]._feedback[0]["id"]
    assert env["admin"].post("/admin/feedback/" + tid + "/reply",
                             json={"message": "Following up."}).status_code == 200
    assert len(env["u1"].get("/feedback/mine").json()[0]["replies"]) == 2


# ── who is allowed to do this ────────────────────────────────────────────────

def test_a_normal_user_cannot_send_messages_as_us(env):
    r = env["u1"].post("/admin/feedback/new",
                       json={"user_ids": ["u2"], "message": "hi from 'support'"})
    assert r.status_code in (401, 403)
    assert env["u2"].get("/feedback/mine").json() == []


def test_signed_out_is_refused(env):
    """Not followed, on purpose: the middleware answers with a 302 to /login,
    and a client that follows it lands on the sign-in page with a 200. Reading
    that 200 as success is how an auth test passes while proving nothing."""
    from fastapi.testclient import TestClient
    r = TestClient(env["api"].app).post(
        "/admin/feedback/new", json={"user_ids": ["u1"], "message": "hi"},
        follow_redirects=False)
    assert r.status_code in (401, 403, 302, 307)
    assert env["api"]._feedback == [], "a signed-out request wrote a message"


# ── the input has to be real ─────────────────────────────────────────────────

@pytest.mark.parametrize("msg", ["", "   ", "\t\n "])
def test_an_empty_message_is_refused(env, msg):
    r = _send(env, ["u1"], msg)
    assert r.status_code in (400, 422)
    assert env["api"]._feedback == []


def test_a_message_with_no_recipients_is_refused(env):
    assert _send(env, []).status_code == 422
    assert env["api"]._feedback == []


def test_blank_recipient_ids_are_not_a_way_past_the_check(env):
    r = _send(env, ["", "  "])
    assert r.status_code == 400
    assert env["api"]._feedback == []


def test_an_overlong_message_is_refused(env):
    assert _send(env, ["u1"], "x" * 2001).status_code == 422


def test_one_request_cannot_fan_out_without_limit(env):
    from src.dashboard.api import _MAX_MESSAGE_RECIPIENTS
    r = _send(env, ["u%d" % i for i in range(_MAX_MESSAGE_RECIPIENTS + 1)])
    assert r.status_code == 422


def test_a_users_thread_count_is_still_capped(env):
    """Disk is the reason the cap exists, and a thread costs the same whichever
    side opened it."""
    from src.dashboard.api import _MAX_THREADS_PER_USER
    api = env["api"]
    api._feedback.extend({"id": "old%d" % i, "user_id": "u1", "message": "x",
                          "created_at": 0} for i in range(_MAX_THREADS_PER_USER))
    r = _send(env, ["u1", "u2"])
    body = r.json()
    assert body["sent"] == 1, "the cap did not hold"
    assert body["skipped"][0]["user_id"] == "u1"
    assert len(env["u2"].get("/feedback/mine").json()) == 1, \
        "one capped recipient blocked everyone else"


def test_the_cap_is_the_same_number_both_paths_use(env):
    """Two hardcoded 200s that could drift apart is how a limit becomes two
    different limits."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.submit_feedback)
    assert "_MAX_THREADS_PER_USER" in src
    assert re.search(r">=\s*200", src) is None, "a second, literal cap is back"


# ── the wiring on both screens ───────────────────────────────────────────────

def test_the_dashboard_has_a_handler_for_the_new_event():
    """Realtime contract rule 2: an event with no branch in ws.onmessage is
    silently dropped, which is the same as never sending it."""
    from src.dashboard import aurora_html
    src = aurora_html.__file__ and open(aurora_html.__file__).read()
    handler = re.search(r"ws\.onmessage = e=>\{(.*?)\n      \};", src, re.S)
    assert handler, "ws.onmessage not found"
    assert "msg.event==='feedback_message'" in handler.group(1), \
        "feedback_message is emitted by the server and handled by nobody"


def test_the_handler_does_not_call_it_a_reply_to_their_feedback():
    from src.dashboard import aurora_html
    src = open(aurora_html.__file__).read()
    branch = re.search(r"if\(msg\.event==='feedback_message'\)\{(.*?)\n        \}", src, re.S)
    assert branch, "the feedback_message branch moved"
    body = branch.group(1)
    assert "loadFbUnread()" in body, "the nav badge is never refreshed"
    assert "hz_fb_reply" in body, "an open Feedback tab never pulls the thread"
    assert "reply to your feedback" not in body


def test_the_thread_view_skips_the_opening_bubble_for_our_own_threads():
    from src.dashboard import aurora_html
    src = open(aurora_html.__file__).read()
    assert re.search(r"\{!t\.from_admin_start &&\s*\n\s*<div[^>]*>\{t\.message\}</div>\}", src), \
        "an admin-started thread still draws an empty bubble for t.message"


def test_the_admin_composer_posts_to_the_endpoint_that_exists():
    """The composer is plain JS in a Python string — no bundler, no types, and
    a wrong URL just 404s silently into a toast."""
    from src.dashboard import api
    page = api._ADMIN_FEEDBACK_HTML
    assert "/admin/feedback/new" in page
    assert "user_ids" in page and "message" in page
    # It needs a list of people to choose from, and that list is the admin one.
    assert "/admin/users" in page


def test_the_composer_confirms_before_a_group_send():
    """One click that lands in many real people's apps, with no undo."""
    from src.dashboard import api
    assert re.search(r"ids\.length>1 && !confirm\(", api._ADMIN_FEEDBACK_HTML)


def test_the_admin_panel_can_reach_the_composer_for_one_person():
    """Deciding somebody needs a message happens while you are looking at them.
    Making you find them again in a second list is how you message the wrong
    person."""
    from src.dashboard import api
    assert "/admin/feedback-page?to=" in api.ADMIN_HTML
    assert re.search(r"URLSearchParams\(location\.search\)\.get\('to'\)",
                     api._ADMIN_FEEDBACK_HTML), \
        "the composer ignores the person the panel sent it"


def test_the_admin_list_does_not_show_our_own_words_as_theirs():
    """Same trap as the user's screen, on the other side of the conversation."""
    from src.dashboard import api
    page = api._ADMIN_FEEDBACK_HTML
    assert re.search(r"\$\{f\.from_admin_start \? '' : `<div class=\"fb-msg\">", page), \
        "the admin list still renders the empty opening message"
    assert "You started this" in page


def test_the_opening_message_is_not_labelled_a_reply():
    """It said "You replied" on the first thing in a thread WE opened, which
    reads as though the user had said something first."""
    from src.dashboard import api
    assert re.search(r"f\.from_admin_start && ri===0 \? 'You wrote' : 'You replied'",
                     api._ADMIN_FEEDBACK_HTML), \
        "our opening message is still labelled as a reply"
