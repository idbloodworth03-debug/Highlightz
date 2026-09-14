"""Telling the USER that Twitch is refusing to clip their channel.

WHY THIS MOVED OUT OF THE ADMIN PANEL. When Twitch refuses a channel the user
gets an empty review queue and no reason for it, and the reasonable conclusion
is that the product is broken. The cause was visible the whole time — on a page
only the operator could open. Every refusal here has a cause that is neither
ours nor the user's, so saying it is the difference between a bug and an
explanation.

THE TWO THINGS THAT CAN GO WRONG, and what guards each:

  * LEAKING. The admin list is every refusing channel on the platform, which is
    a list of what other customers monitor. Serving that to a logged-in user
    hands them the customer list. The scope filter is the boundary, and it is
    the first thing tested.

  * A NOTICE THAT CANNOT BE CLOSED, or one that closes forever. Dismissal is
    per channel and recorded as a TIMESTAMP, so closing it holds across tabs
    and reloads but the warning still returns when the channel refuses again.
    Both halves are tested, because either one alone is a bug.
"""

import base64
import json as _j
import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import api
from src.stats import clip_refusals as cr

FRONTEND = __import__("pathlib").Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Per-test tally file. Resolved from settings at import time, so without
    this a test writes to the real one under clips/."""
    monkeypatch.setattr(cr, "_FILE", tmp_path / "clip_refusals.json")
    return cr


PEOPLE: dict = {}


def _client(uid="mine"):
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(_j.dumps({
        "auth": True, "user_id": uid, "subscription_status": "active",
    }).encode())
    c.cookies.set("session", signer.sign(data).decode())
    return c


@pytest.fixture
def client(monkeypatch):
    PEOPLE.clear()
    PEOPLE.update({
        "mine":   {"id": "mine", "username": "me", "plan": "free"},
        "theirs": {"id": "theirs", "username": "someone-else", "plan": "free"},
        "boss":   {"id": "boss", "username": "admin", "is_admin": True},
    })
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))

    def _dismiss(uid, channel, when):
        PEOPLE[uid].setdefault("refusals_dismissed", {})[channel.lower()] = when

    monkeypatch.setattr(user_store, "set_refusal_dismissed", _dismiss)
    return _client()


# ── the store ────────────────────────────────────────────────────────────────

def test_a_refusal_is_attributed_to_the_user_it_happened_to():
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    assert [r["channel"] for r in cr.rows_for_user("mine")] == ["kaicenat"]


def test_one_users_refusals_are_not_anothers():
    """The scope filter IS the privacy boundary — see the module docstring."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    cr.record("xqc", cr.CLASSIFICATION, "theirs")
    assert [r["channel"] for r in cr.rows_for_user("mine")] == ["kaicenat"]
    assert [r["channel"] for r in cr.rows_for_user("theirs")] == ["xqc"]


def test_two_watchers_of_one_channel_both_get_told():
    """A channel can be monitored by several accounts and the refusal hits all
    of them. Attributing only the last one would silently drop the rest."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "theirs")
    assert cr.rows_for_user("mine") and cr.rows_for_user("theirs")
    assert len(cr.all_rows()) == 1, "one channel is still one row"


def test_the_same_user_refused_twice_is_listed_once():
    """The list is 'is this mine', not an event log. Duplicates would grow it
    without bound on a channel that refuses every trigger."""
    for _ in range(5):
        cr.record("kaicenat", cr.CLASSIFICATION, "mine")
    assert cr.users_for("kaicenat") == ["mine"]
    assert cr.all_rows()[0]["count"] == 5, "the tally still counts every refusal"


def test_the_affected_list_is_bounded():
    """A streamer watched by every account would otherwise grow one row without
    limit."""
    for i in range(cr._MAX_USERS + 40):
        cr.record("kaicenat", cr.CLASSIFICATION, f"u{i}")
    assert len(cr.users_for("kaicenat")) == cr._MAX_USERS


def test_a_refusal_without_a_user_still_counts_but_reaches_nobody():
    """We cannot tell someone a channel is theirs if we do not know that it is.
    Counting it for the operator is right; guessing an owner is not."""
    cr.record("kaicenat", cr.CLASSIFICATION)
    assert cr.all_rows()[0]["count"] == 1
    assert cr.rows_for_user("mine") == []


def test_clearing_returns_who_was_warned():
    """The row is the only record of who saw the notice, and clear() deletes
    it — so it has to hand them back or the banner stays up until a reload."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "theirs")
    assert sorted(cr.clear("kaicenat")) == ["mine", "theirs"]
    assert cr.rows_for_user("mine") == []


def test_clearing_a_channel_nobody_reported_is_harmless():
    assert cr.clear("never-seen") == []


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_user_endpoint_serves_only_their_own_channels(client):
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    cr.record("xqc", cr.CLASSIFICATION, "theirs")
    rows = client.get("/clip-refusals").json()["rows"]
    assert [r["channel"] for r in rows] == ["kaicenat"]
    assert "xqc" not in client.get("/clip-refusals").text, \
        "another customer's channel reached this user"


def test_the_user_endpoint_needs_a_login(client):
    """Asserted on the BODY, not the status. A browser navigation that is not
    signed in is answered with the sign-in page at 200 — the same shape /clips
    and every other data route returns — so a status check here would pass on
    a route that had no protection at all. What matters is that no refusal
    data comes back."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    anon = TestClient(api.app, base_url="https://testserver")
    body = anon.get("/clip-refusals").text
    assert "kaicenat" not in body
    assert "Sign In" in body or "sign in" in body.lower()


def test_the_reason_reaches_the_browser(client):
    """The banner picks its copy from this. Without it every refusal reads as
    the same unexplained failure."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    assert client.get("/clip-refusals").json()["rows"][0]["reason"] == "not_authorized"


def test_the_payload_carries_no_other_users(client):
    """The row holds user ids now. They are for scoping on the server and have
    no business in a response body."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "theirs")
    body = client.get("/clip-refusals").text
    assert "theirs" not in body and "users" not in body


# ── dismissing ───────────────────────────────────────────────────────────────

def test_dismissing_removes_it(client):
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    assert client.get("/clip-refusals").json()["rows"]
    client.post("/clip-refusals/kaicenat/dismiss")
    assert client.get("/clip-refusals").json()["rows"] == [], \
        "the X did not close the notice"


def test_dismissing_one_channel_does_not_hide_another(client):
    """One flag for the whole notice would mean closing today's warning also
    hides a different channel breaking tomorrow."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    cr.record("summit1g", cr.CLASSIFICATION, "mine")
    client.post("/clip-refusals/kaicenat/dismiss")
    assert [r["channel"] for r in client.get("/clip-refusals").json()["rows"]] \
        == ["summit1g"]


def test_a_later_refusal_brings_the_notice_back(client):
    """Dismissing must not silence a channel forever — only until it refuses
    again. This is why the dismissal is a timestamp and not a boolean."""
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    client.post("/clip-refusals/kaicenat/dismiss")
    assert client.get("/clip-refusals").json()["rows"] == []
    time.sleep(0.01)
    cr.record("kaicenat", cr.NOT_AUTHORIZED, "mine")
    assert [r["channel"] for r in client.get("/clip-refusals").json()["rows"]] \
        == ["kaicenat"], "the channel refused again and said nothing"


def test_dismissing_is_persisted_server_side_not_just_in_the_tab():
    """A tab-local dismissal comes straight back on the next page load."""
    import inspect
    from src.auth import users
    assert hasattr(users, "set_refusal_dismissed")
    assert "set_refusal_dismissed" in inspect.getsource(api.dismiss_clip_refusal)


def test_the_dismissal_store_is_bounded(monkeypatch, tmp_path):
    """Rows are deleted from the tally the moment a channel recovers, so an old
    entry here can never be suppressing a live row — it is just weight."""
    from src.auth import users as user_store
    rec = {"id": "mine"}
    monkeypatch.setattr(user_store, "_load", lambda: [rec])
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    for i in range(80):
        user_store.set_refusal_dismissed("mine", f"ch{i}", 1000.0 + i)
    assert len(rec["refusals_dismissed"]) == 50
    assert "ch79" in rec["refusals_dismissed"], "the newest dismissal was dropped"


def test_an_admin_dismissal_does_not_blind_the_control_room(client):
    """The two views are deliberately not the same query. An operator closing
    the notice on their own account must not hide a channel that is still
    broken from the admin panel."""
    import inspect
    src = inspect.getsource(api.admin_clip_refusals)
    assert "all_rows" in src and "refusal_dismissed_at" not in src


# ── realtime (the CLAUDE.md contract) ────────────────────────────────────────

def test_the_notice_arrives_without_a_refresh():
    """Emitted by the clip processor on a refusal and on a recovery, and by the
    dismiss endpoint so the user's other tabs close it too."""
    import inspect
    from src import main
    assert "refusals_changed" in inspect.getsource(api.dismiss_clip_refusal)
    assert "refusals_changed" in inspect.getsource(main._tell_refused)
    assert "_tell_refused" in inspect.getsource(main.run_clip_processor), \
        "a refusal is recorded but never pushed to the user's tab"


def test_recovery_takes_the_notice_off_the_screen():
    """The channel clipping again is the good news, and it has to arrive the
    same way the bad news did."""
    import inspect
    src = inspect.getsource(__import__("src.main", fromlist=["x"]).run_clip_processor)
    assert "_refusals.clear(job.channel)" in src
    assert "refusals_changed" in src, \
        "the banner would sit there until the user reloaded"


def test_the_event_has_a_frontend_handler():
    """An event with no branch in ws.onmessage is silently dropped, which is
    the same as not sending it."""
    assert "msg.event==='refusals_changed'" in SRC


def test_it_is_refetched_on_reconnect():
    """refetchAll runs on mount and on every reconnect — a deploy drops every
    socket at once, and anything not pulled here goes stale afterwards."""
    start = SRC.index("const refetchAll")
    body = SRC[start:SRC.index("const wsBootstrapped")]
    assert "/clip-refusals" in body


def test_there_is_a_dismiss_control(client):
    review = SRC[SRC.index("function ReviewScreen("):SRC.index("function LandingScreen(")]
    assert "onDismissRefusal" in review, "the notice cannot be closed"
    assert "/clip-refusals/" in SRC and "dismiss" in SRC


# ── the wording ──────────────────────────────────────────────────────────────

def test_every_reason_has_copy_and_names_who_can_fix_it():
    """The value of the notice is 'this is not your fault and here is where the
    wall is'. A reason with no copy renders as a blank explanation."""
    copy = SRC[SRC.index("const REFUSAL_COPY"):SRC.index("function ReviewScreen(")]
    for reason in (cr.CLASSIFICATION, cr.TITLE_AUTOMOD, cr.NOT_AUTHORIZED):
        assert reason + ":" in copy, f"no user-facing copy for {reason}"
    assert copy.count("who:") == 3, "a reason does not say who can fix it"


def test_the_notice_says_the_account_is_fine():
    """Without this the user reads it as their problem and either cancels or
    opens a ticket. It is the single most important sentence in the banner."""
    review = SRC[SRC.index("function ReviewScreen("):SRC.index("function LandingScreen(")]
    assert "Nothing is wrong with your account" in review


def test_the_notice_does_not_leak_how_highlights_are_found():
    """Public-facing copy never explains the detector."""
    copy = SRC[SRC.index("const REFUSAL_COPY"):SRC.index("function ReviewScreen(")]
    for secret in ("viewer clip", "clustering", "cluster"):
        assert secret not in copy.lower()
