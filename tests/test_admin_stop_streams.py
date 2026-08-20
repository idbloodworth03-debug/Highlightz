"""Admin can stop a user's streams from the admin page.

WHY IT EXISTS: the box is CPU-bound before it is slot-bound — measured at
roughly six concurrent live streams on one vCPU — so the owner needs a way to
shed load without waiting for someone to notice.

STOPPING IS NOT BANNING, and most of these tests are about that line. The user
keeps their plan, their clips, and the right to start the channel again. Taking
that away is `revoke`, which is a different button with a different
confirmation. Anything here that quietly did more than free the slot would make
a load-shedding tool into a punishment nobody could see being applied.
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

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_save_clips", lambda: None)

    sent, unpublished = [], []

    async def _capture(msg, user_id=None):
        sent.append((msg, user_id))
    monkeypatch.setattr(api, "broadcast", _capture)

    async def _unpub(channel, uid):
        unpublished.append((channel, uid))
    monkeypatch.setattr(api, "_publish_remove_stream", _unpub)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "adm", "username": "admin", "is_admin": True,
         "subscription_status": "active", "created_at": now},
        {"id": "u1", "username": "one", "subscription_status": "active",
         "plan": "pro", "created_at": now},
    ]))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid, is_admin=False):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": is_admin, "subscription_status": "active"}).encode())).decode())
        return c

    def stream(channel, uid="u1", **extra):
        api._streams[f"{uid}:{channel}"] = {
            "channel": channel, "user_id": uid, "platform": "twitch",
            "preset": "default", "status": "live", "added_at": now, **extra}

    c.login = login
    c.stream = stream
    c.sent = sent
    c.unpublished = unpublished
    c.api = api
    c.store = user_store
    yield c


def _events(client):
    return [m.get("event") for m, _ in client.sent]


# ── it works ─────────────────────────────────────────────────────────────────

def test_an_admin_can_stop_one_stream(client):
    client.stream("lacy")
    client.stream("jynxzi")
    r = client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    assert r.status_code == 200
    assert "u1:lacy" not in client.api._streams
    assert "u1:jynxzi" in client.api._streams, "stopping one stopped another"


def test_an_admin_can_stop_every_stream_at_once(client):
    for ch in ("lacy", "jynxzi", "aceu"):
        client.stream(ch)
    r = client.login("adm", True).delete("/admin/users/u1/streams")
    assert r.status_code == 200 and r.json()["stopped"] == 3
    assert client.api._streams == {}


def test_the_worker_is_actually_torn_down(client):
    """Dropping the record without unpublishing leaves the worker running —
    the row disappears and the CPU cost does not, which is the one thing this
    feature exists to reclaim."""
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    assert ("lacy", "u1") in client.unpublished


def test_stop_all_tears_down_every_worker(client):
    for ch in ("lacy", "jynxzi"):
        client.stream(ch)
    client.login("adm", True).delete("/admin/users/u1/streams")
    assert sorted(client.unpublished) == [("jynxzi", "u1"), ("lacy", "u1")]


def test_it_returns_a_json_body_not_204(client):
    """The admin page's api() helper ends in `return r.json()`, so a 204 makes
    a successful stop surface as an error toast. Every endpoint that helper
    calls returns a body; the 204 ones are called with a raw fetch."""
    client.stream("lacy")
    r = client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    assert r.status_code == 200
    assert r.json()["stopped"] == 1        # would raise on an empty body


# ── it is only stopping ──────────────────────────────────────────────────────

def test_stopping_does_not_touch_their_subscription(client):
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams")
    u = client.store.get_by_id("u1")
    assert u["subscription_status"] == "active"
    assert u.get("plan") == "pro"


def test_stopping_does_not_touch_their_clips(client):
    client.api._clips["c1"] = {"id": "c1", "user_id": "u1", "status": "pending",
                               "channel": "lacy", "created_at": time.time()}
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams")
    assert "c1" in client.api._clips, "stopping a stream deleted their clips"


def test_the_user_can_start_the_channel_again(client, monkeypatch):
    """The whole point of stop-not-ban. Nothing may be left behind that
    refuses the next add."""
    from src.dashboard import api

    async def _preset(ch):
        return "default"
    monkeypatch.setattr(api, "_auto_preset_for", _preset)
    monkeypatch.setattr(api, "_check_server_capacity", lambda uid: None)

    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    r = client.login("u1").post("/streams", json={"channel": "lacy",
                                                  "platform": "twitch",
                                                  "preset": "default"})
    assert r.status_code == 201, f"the user could not restart it: {r.text}"


def test_no_subscription_expired_event_is_sent(client):
    """That event makes the dashboard show a lapsed-account state. Sending it
    for a capacity stop would tell a paying customer they had been cut off."""
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams")
    assert "subscription_expired" not in _events(client)


# ── the user is told ─────────────────────────────────────────────────────────

def test_the_user_is_told_which_stream_stopped(client):
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    notice = [m for m, _ in client.sent if m.get("event") == "streams_stopped_by_admin"]
    assert notice and notice[0]["channel"] == "lacy"


def test_the_row_disappears_live(client):
    """stream_removed is what clears it from an open tab. Without it the user
    sees a stream that is no longer running until they reload."""
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    assert "stream_removed" in _events(client)


def test_every_event_is_scoped_to_the_affected_user(client):
    client.stream("lacy")
    client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    assert {uid for _, uid in client.sent} == {"u1"}, \
        "an admin action leaked onto another user's sockets"


def test_stop_all_sends_one_notice_not_one_per_stream(client):
    """Each stop already clears its own row. A toast per channel would bury the
    screen in duplicates of the same news."""
    for ch in ("lacy", "jynxzi", "aceu"):
        client.stream(ch)
    client.login("adm", True).delete("/admin/users/u1/streams")
    assert _events(client).count("streams_stopped_by_admin") == 1
    assert _events(client).count("stream_removed") == 3


def test_stopping_nothing_sends_no_notice(client):
    """A user with no streams must not be told their streams were stopped."""
    r = client.login("adm", True).delete("/admin/users/u1/streams")
    assert r.json()["stopped"] == 0
    assert "streams_stopped_by_admin" not in _events(client)


def test_the_notice_has_a_frontend_handler():
    """CLAUDE.md rule 2: an event with no branch is silently dropped, which is
    the same as not sending it — and here that means the stream vanishes with
    no explanation, which reads as a bug to the person it happens to."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "msg.event==='streams_stopped_by_admin'" in DASHBOARD_HTML
    assert "start it again" in DASHBOARD_HTML, \
        "the notice does not tell the user they can restart it"


# ── authorisation ────────────────────────────────────────────────────────────

def test_a_normal_user_cannot_stop_anyone_streams(client):
    client.stream("lacy")
    r = client.login("u1").delete("/admin/users/u1/streams/lacy")
    assert r.status_code in (401, 403)
    assert "u1:lacy" in client.api._streams, "a non-admin stopped a stream"


def test_a_normal_user_cannot_stop_all(client):
    client.stream("lacy")
    r = client.login("u1").delete("/admin/users/u1/streams")
    assert r.status_code in (401, 403)
    assert "u1:lacy" in client.api._streams


def test_a_signed_out_visitor_cannot_stop_anything(client):
    """Asserted on the EFFECT, not the status code.

    AuthMiddleware answers an unauthenticated request by serving the sign-in
    page with a 200, rather than a 401 or a redirect — so a status assertion
    here reads as "allowed" while the request never reached the endpoint at
    all. The first version of this test failed for exactly that reason and
    looked like an auth hole. What actually matters is that the stream is
    still running and no stop was performed.
    """
    client.stream("lacy")
    client.cookies.clear()
    r = client.delete("/admin/users/u1/streams/lacy")
    assert "u1:lacy" in client.api._streams, "a signed-out request stopped a stream"
    assert "stopped" not in r.text[:200], "the endpoint answered a signed-out caller"
    assert client.unpublished == [], "a worker was torn down for a signed-out caller"


# ── edges ────────────────────────────────────────────────────────────────────

def test_stopping_a_stream_that_is_not_there_is_a_404(client):
    r = client.login("adm", True).delete("/admin/users/u1/streams/ghost")
    assert r.status_code == 404


def test_a_bogus_channel_name_is_refused(client):
    r = client.login("adm", True).delete("/admin/users/u1/streams/..%2F..%2Fetc")
    assert r.status_code in (400, 404)


def test_stopping_one_users_stream_leaves_another_users_alone(client):
    """Streams are keyed uid:channel, so two users can monitor the same
    channel. Stopping by channel name alone would take out both."""
    client.stream("lacy", uid="u1")
    client.stream("lacy", uid="u2")
    client.login("adm", True).delete("/admin/users/u1/streams/lacy")
    assert "u2:lacy" in client.api._streams, "another user's stream was stopped"


def test_stop_all_only_touches_the_named_user(client):
    client.stream("lacy", uid="u1")
    client.stream("jynxzi", uid="u2")
    r = client.login("adm", True).delete("/admin/users/u1/streams")
    assert r.json()["stopped"] == 1
    assert "u2:jynxzi" in client.api._streams


# ── the admin page wiring ────────────────────────────────────────────────────

def test_the_buttons_are_in_the_admin_page():
    from src.dashboard.api import ADMIN_HTML
    assert "dr-stopone" in ADMIN_HTML and "dr-stopall" in ADMIN_HTML


def test_both_buttons_confirm_first():
    """Stopping someone else's stream is not undoable by the admin — only the
    user can restart it — so it must not be a single stray click."""
    from src.dashboard.api import ADMIN_HTML
    # Anchored on the HANDLER BRANCH, not on the bare class name: the class
    # also appears in the delegation selector, and indexing on it found that
    # instead and reported a confirm-less button that confirms fine.
    for cls in ("dr-stopone", "dr-stopall"):
        at = ADMIN_HTML.index("classList.contains('%s')" % cls)
        assert "confirm(" in ADMIN_HTML[at:at + 400], \
            "the %s button does not confirm before acting" % cls


def test_the_stop_button_carries_the_channel_not_an_index():
    """A positional index goes stale the moment the list re-renders, which it
    does after every stop — the second click would hit the wrong stream."""
    from src.dashboard.api import ADMIN_HTML
    assert 'data-ch="' in ADMIN_HTML
    assert "getAttribute('data-ch')" in ADMIN_HTML


def test_every_drawer_button_is_in_the_delegation_selector():
    """THE bug this file nearly shipped.

    The drawer's click handler is delegated through
    `e.target.closest('.dr-grant, .dr-revoke, ...')` — an explicit allowlist.
    A dr-* button missing from it is INERT: the click lands, closest() returns
    null, the handler returns before reaching the branch that would have acted.
    Nothing throws and nothing logs. Both Stop buttons rendered, both had
    confirm() in the source, all the endpoint tests passed, and clicking them
    did nothing at all — only driving the page in a browser showed it.

    So the two sides are compared directly rather than trusted.
    """
    import re
    from src.dashboard.api import ADMIN_HTML

    sel = re.search(r"e\.target\.closest\('([^']*dr-[^']*)'\)", ADMIN_HTML)
    assert sel, "could not find the drawer's delegation selector"
    handled = {c.strip().lstrip(".") for c in sel.group(1).split(",")}

    # BUTTONS ONLY. dr-plan and dr-days are <select> controls that the dr-grant
    # branch READS when its button is clicked — they are inputs, not actions,
    # and belong outside the selector. Matching every dr-* class flagged them
    # and turned a real check into a false alarm.
    rendered = set(re.findall(r'<button[^>]*class="[^"]*?(dr-[a-z]+)"', ADMIN_HTML))
    assert rendered, "no drawer buttons found — has the markup changed shape?"
    missing = sorted(rendered - handled)
    assert missing == [], \
        f"these drawer buttons are rendered but not in the click selector, so " \
        f"they do nothing when clicked: {missing}"


def test_the_drawer_reopens_rather_than_closing():
    """Shedding load means stopping several in a row; closing the drawer each
    time would make the admin re-find the same user every stop."""
    from src.dashboard.api import ADMIN_HTML
    seg = ADMIN_HTML[ADMIN_HTML.index("classList.contains('dr-stopone')"):]
    seg = seg[:seg.index("classList.contains('dr-stopall')")]
    assert "openUser(u)" in seg and "closeDrawer()" not in seg
