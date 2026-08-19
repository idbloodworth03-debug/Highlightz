"""Clearing channels out of the add-stream box's "Recently monitored" list.

THE THING THIS FEATURE MUST NOT DO. The recent list is not a history log — it
is derived from the user's profile files, and a profile carries the learned
signal_weights and the approve/reject record for that channel. So the obvious
implementation, deleting the profile to drop the row, would quietly destroy
every correction the user ever made for that streamer, and they would find out
weeks later by noticing the scoring had gone stupid. Clearing therefore writes
a dismissal and leaves the profile alone.

Most of what follows checks that separation holds, in both directions: the row
goes away, and the learning does not.
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
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_HIDDEN_SUGG_FILE", tmp_path / "hidden_suggestions.json")
    monkeypatch.setattr(api.settings, "local_storage_path", str(tmp_path))
    # Suggestions call Helix for the "popular right now" half. Stubbed so these
    # tests are about the recent list and never touch the network.
    monkeypatch.setattr(api, "_popular_streams_cache", (time.time(), []))

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

    def profile(channel, uid="u1", **fields):
        """A real profile file, with learned state in it so its survival is
        observable rather than merely asserted about the filename."""
        pdir = tmp_path / "profiles" / uid
        pdir.mkdir(parents=True, exist_ok=True)
        body = {"channel": channel, "total_clips": 42, "approved_clips": 30,
                "signal_weights": {"CHAT_VELOCITY": 1.37}, **fields}
        (pdir / (channel + ".json")).write_text(_j.dumps(body))
        return pdir / (channel + ".json")

    c.login = login
    c.profile = profile
    c.sent = sent
    c.api = api
    c.tmp = tmp_path
    yield c


def _recent(client, uid="u1"):
    return client.login(uid).get("/streams/suggest").json()["recent"]


# ── it does the job ──────────────────────────────────────────────────────────

def test_a_channel_shows_up_in_recent_before_it_is_cleared(client):
    """Precondition. Without this the clearing tests could pass by the list
    being empty for some unrelated reason."""
    client.profile("lacy")
    assert "lacy" in _recent(client)


def test_clearing_one_channel_removes_it_from_the_list(client):
    client.profile("lacy")
    client.profile("jynxzi")
    r = client.login("u1").delete("/streams/suggest/recent/lacy")
    assert r.status_code == 204
    left = _recent(client)
    assert "lacy" not in left
    assert "jynxzi" in left, "clearing one channel removed another"


def test_clearing_all_empties_the_list(client):
    for ch in ("lacy", "jynxzi", "aceu"):
        client.profile(ch)
    r = client.login("u1").delete("/streams/suggest/recent")
    assert r.status_code == 204
    assert _recent(client) == []


def test_clear_all_clears_beyond_the_visible_eight(client):
    """The list is sliced to 8. Hiding only what was on screen would leave the
    next batch sliding up to replace it, which is not what clear means."""
    for i in range(20):
        client.profile("chan%02d" % i)
    assert len(_recent(client)) == 8
    client.login("u1").delete("/streams/suggest/recent")
    assert _recent(client) == []


def test_the_dismissal_survives_a_restart(client):
    """It is written to disk, not held in memory — otherwise the list comes
    back on the next deploy, which is the complaint this feature exists to
    fix, returning on a timer."""
    client.profile("lacy")
    client.login("u1").delete("/streams/suggest/recent/lacy")
    assert client.api._hidden_for("u1") == {"lacy"}
    assert (client.tmp / "hidden_suggestions.json").exists()
    # Re-read from disk with no cached state involved.
    assert "lacy" in _j.loads((client.tmp / "hidden_suggestions.json").read_text())["u1"]


# ── what it must not touch ───────────────────────────────────────────────────

def test_clearing_does_not_delete_the_learned_profile(client):
    """THE one that matters. A cleared row must cost the user nothing."""
    p = client.profile("lacy")
    client.login("u1").delete("/streams/suggest/recent/lacy")
    assert p.exists(), "clearing a suggestion deleted the channel's profile"
    body = _j.loads(p.read_text())
    assert body["total_clips"] == 42
    assert body["approved_clips"] == 30
    assert body["signal_weights"]["CHAT_VELOCITY"] == 1.37, \
        "the learned weights were altered by clearing a suggestion"


def test_clear_all_does_not_delete_any_profile(client):
    paths = [client.profile(ch) for ch in ("lacy", "jynxzi", "aceu")]
    client.login("u1").delete("/streams/suggest/recent")
    assert all(p.exists() for p in paths), "clear-all deleted learned profiles"


def test_one_user_cannot_clear_another_users_list(client):
    client.profile("lacy", uid="u1")
    client.profile("lacy", uid="u2")
    client.login("u2").delete("/streams/suggest/recent")
    assert _recent(client, "u2") == []
    assert "lacy" in _recent(client, "u1"), "u2 cleared u1's suggestions"


def test_clearing_does_not_stop_a_running_stream(client):
    """Monitored channels are filtered out of the list anyway, so clearing must
    be a display action only and never reach into _streams."""
    client.api._streams["u1:lacy"] = {"channel": "lacy", "user_id": "u1",
                                      "status": "running", "platform": "twitch"}
    client.login("u1").delete("/streams/suggest/recent")
    assert "u1:lacy" in client.api._streams, "clearing suggestions stopped a stream"


# ── coming back to a channel ─────────────────────────────────────────────────

def test_monitoring_a_cleared_channel_again_un_hides_it(client, monkeypatch):
    """A dismissal must not outlive the intent behind it. Going back to a
    channel is a clearer statement than the old clearing was."""
    from src.dashboard import api

    async def _preset(ch):
        return "default"
    monkeypatch.setattr(api, "_auto_preset_for", _preset)
    monkeypatch.setattr(api, "_check_server_capacity", lambda uid: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "start_stream_worker", _noop, raising=False)

    client.profile("lacy")
    client.login("u1").delete("/streams/suggest/recent/lacy")
    assert client.api._hidden_for("u1") == {"lacy"}

    client.login("u1").post("/streams", json={"channel": "lacy", "platform": "twitch",
                                              "preset": "default"})
    assert "lacy" not in client.api._hidden_for("u1"), \
        "re-monitoring a cleared channel left it dismissed forever"


# ── the realtime contract (CLAUDE.md) ────────────────────────────────────────

def test_clearing_broadcasts_scoped_to_the_user(client):
    """Rule 1: a user-visible mutation broadcasts, scoped by user_id. Another
    tab showing a list this one just cleared is the bug."""
    client.profile("lacy")
    client.login("u1").delete("/streams/suggest/recent/lacy")
    assert ({"event": "suggestions_cleared"}, "u1") in client.sent


def test_clear_all_broadcasts_too(client):
    client.profile("lacy")
    client.login("u1").delete("/streams/suggest/recent")
    assert ({"event": "suggestions_cleared"}, "u1") in client.sent


def test_the_event_has_a_frontend_handler():
    """Rule 2: an event with no branch in ws.onmessage is silently dropped,
    which is the same as not sending it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "msg.event==='suggestions_cleared'" in DASHBOARD_HTML, \
        "the backend emits suggestions_cleared and nothing handles it"
    assert "hz_suggestions_cleared" in DASHBOARD_HTML, \
        "the handler does not reach the panel that owns the list"


def test_the_panel_listens_for_that_event():
    """The other half of rule 2: dispatching a CustomEvent nothing subscribes
    to is just as silent as an unhandled ws event."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "addEventListener('hz_suggestions_cleared'" in DASHBOARD_HTML
    assert "removeEventListener('hz_suggestions_cleared'" in DASHBOARD_HTML, \
        "the listener is never cleaned up — it leaks across remounts"


# ── routing and input ────────────────────────────────────────────────────────

def test_the_clear_routes_cannot_be_swallowed_by_the_delete_stream_route():
    """DELETE /streams/{channel} matches any SINGLE segment under /streams, so
    a clear route of /streams/suggest would be matched as channel="suggest"
    and delete a stream that does not exist instead of clearing anything.

    The protection is the path shape, not the declaration order — two segments
    cannot collide with one — so that is what is asserted. Ordering is checked
    too, since the comment in api.py promises it, but it is belt to the shape's
    braces. Both searches are anchored to line starts: the first version of
    this test matched the '@app.delete("/streams/{channel}")' written inside
    that very comment and reported the routes in the wrong order.
    """
    import re
    import pathlib
    src = pathlib.Path("src/dashboard/api.py").read_text()

    clear_paths = re.findall(r'^@app\.delete\("(/streams/suggest[^"]*)"', src, re.M)
    assert clear_paths, "no DELETE route for clearing suggestions"
    for p in clear_paths:
        segments = [s for s in p.split("/") if s][1:]      # drop "streams"
        assert len(segments) >= 2, (
            f"{p} is one segment under /streams and will be matched as "
            f'channel="{segments[0] if segments else ""}" by /streams/{{channel}}')

    clear_at  = min(m.start() for m in
                    re.finditer(r'^@app\.delete\("/streams/suggest', src, re.M))
    stream_at = next(m.start() for m in
                     re.finditer(r'^@app\.delete\("/streams/\{channel\}"', src, re.M))
    assert clear_at < stream_at, "the clear routes are declared after /streams/{channel}"


def test_a_bogus_channel_name_is_refused(client):
    """The login goes into a JSON key on disk. Same validation as everywhere
    else channel names are accepted."""
    r = client.login("u1").delete("/streams/suggest/recent/..%2F..%2Fetc")
    assert r.status_code in (400, 404)


def test_clearing_something_not_in_the_list_is_harmless(client):
    """Two tabs, one clicks twice, or the list was already stale. Must not 500."""
    client.profile("lacy")
    a = client.login("u1").delete("/streams/suggest/recent/lacy")
    b = client.login("u1").delete("/streams/suggest/recent/lacy")
    assert a.status_code == 204 and b.status_code == 204


def test_clearing_with_no_profiles_at_all_is_harmless(client):
    r = client.login("u1").delete("/streams/suggest/recent")
    assert r.status_code == 204
    assert _recent(client) == []


def test_a_corrupt_hidden_file_does_not_break_suggestions(client):
    """Fails open: a bad file loses the dismissals, it does not lose the
    dropdown. The list is a convenience and must never take the panel down."""
    client.profile("lacy")
    (client.tmp / "hidden_suggestions.json").write_text("{not json")
    assert "lacy" in _recent(client)


def test_search_results_are_unaffected_by_dismissals(client, monkeypatch):
    """Clearing a channel from recents must not make it unfindable — that
    would turn a tidy-up into a block."""
    from src.output import twitch_clips

    async def _search(q):
        return [{"login": "lacy", "name": "lacy", "is_live": True, "game": "", "avatar": ""}]
    monkeypatch.setattr(twitch_clips, "search_channels", _search)

    client.profile("lacy")
    client.login("u1").delete("/streams/suggest/recent/lacy")
    rows = client.login("u1").get("/streams/suggest?q=lac").json()["results"]
    assert [r["login"] for r in rows] == ["lacy"], \
        "a dismissed channel disappeared from search too"
