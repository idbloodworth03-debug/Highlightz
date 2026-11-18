"""Free keeps watching while you are away until the review queue is full.

Owner, 2026-11-18: "Free tier users need to have their lives turned off after
they hit their clip cap and dont have the tab open ... the free tier will clip
till capacity when off the tab and then the stream will not be monitored after
that. Also notify them that this is the case only because they are free and
they should upgrade if they want more but say it a better way".

A channel whose owner's queue is full cannot clip anything; with nobody at the
dashboard it is a live slot spent on clips that cannot be made. These run real
ticks of idle_stream_reaper against a scene of accounts and hold:

  1. WHO: a Free account, away, with a full queue is paused. Not full, or a
     tab open, or just stepped out, or a paying plan: left alone.
  2. A DEPLOY IS NOT EVERYBODY LEAVING: it closes every socket at once, and
     nobody is "away" until the grace has passed since the process started.
  3. THEY COME BACK: the channel restarts, the reason comes off the record,
     and the first tab they open is told, once.
  4. THEY ARE TOLD, in the owner's tone: what happened, why, that nothing was
     lost, and what the next plan does, as a fact with its price.
  5. The realtime contract, and the site saying what the code does.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time

import pytest

from src.billing import plans
from src.dashboard import api

NOW = time.time()
AWAY = NOW - 3600                  # an hour ago: well past the grace
FREE_CAP = plans.PLAN_LIMITS["free"]["max_pending"]


class _Stop(Exception):
    pass


@pytest.fixture
def scene(tmp_path, monkeypatch):
    users, started, stopped, sent, notes = {}, [], [], [], []

    async def _new(channel, platform, preset, uid=""):
        started.append((channel, uid))

    async def _rm(channel, uid=""):
        stopped.append((channel, uid))

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))

    from src.auth import users as user_store

    def _add_notices(batch):
        for uid, note in batch.items():
            notes.append((uid, note))
            users[uid].setdefault("notices", []).append({**note, "at": time.time(), "id": "n"})
        return len(batch)

    monkeypatch.setattr(user_store, "get_by_id", lambda uid: users.get(uid))
    monkeypatch.setattr(user_store, "add_notices", _add_notices)
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_paused_users", set())
    monkeypatch.setattr(api, "_user_last_active", {})
    monkeypatch.setattr(api, "_ws_clients", {})
    monkeypatch.setattr(api, "_ws_last_seen", {})
    monkeypatch.setattr(api, "_queue_pause_welcome", {})
    monkeypatch.setattr(api, "_PROCESS_STARTED_AT", NOW - 86400)     # up since yesterday
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "activity.json")
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._live_slots.clear()

    def person(uid, status="none", plan=None, last=AWAY, channels=("chan",), pending=FREE_CAP,
               suggested=0, tab=False, **extra):
        users[uid] = {"id": uid, "username": uid, "subscription_status": status,
                      **({"plan": plan} if plan else {}), **extra}
        api._user_last_active[uid] = last
        for i, ch in enumerate(channels):
            key = f"{uid}:{ch}"
            api._streams[key] = {"channel": ch, "platform": "twitch", "preset": "default",
                                 "status": "live", "user_id": uid, "added_at": 1.0 + i}
            api._live_slots.add(key)
        for i in range(pending):
            api._clips[f"{uid}-p{i}"] = {"id": f"{uid}-p{i}", "user_id": uid, "status": "pending"}
        for i in range(suggested):
            api._clips[f"{uid}-s{i}"] = {"id": f"{uid}-s{i}", "user_id": uid, "status": "pending",
                                         "suggested": True}
        if tab:
            api._ws_clients[uid] = {object()}

    def tick():
        calls = {"n": 0}

        async def fake_sleep(_s):
            calls["n"] += 1
            if calls["n"] > 1:
                raise _Stop()
        monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
        with pytest.raises(_Stop):
            asyncio.run(api.idle_stream_reaper())

    def rec(uid, ch="chan"):
        return api._streams.get(f"{uid}:{ch}")

    return type("Scene", (), {"person": staticmethod(person), "tick": staticmethod(tick),
                              "rec": staticmethod(rec), "users": users, "started": started,
                              "stopped": stopped, "sent": sent, "notes": notes})


def _events(scene, uid):
    return [e for e, u, _ in scene.sent if u == uid]


# ── 1. who ───────────────────────────────────────────────────────────────────

def test_a_free_account_away_with_a_full_queue_is_paused(scene):
    scene.person("free1")
    scene.tick()
    rec = scene.rec("free1")
    assert rec["status"] == "paused" and rec["hold"] == "queue_full" and rec["paused_at"]
    assert "free1:chan" not in api._live_slots, "the live slot was not given back"
    assert scene.stopped == [("chan", "free1")]
    assert "free1" in api._paused_users


def test_it_clips_until_the_queue_is_full(scene):
    """One short of the cap: away or not, it keeps watching."""
    scene.person("free1", pending=FREE_CAP - 1)
    scene.tick()
    assert scene.rec("free1")["status"] == "live" and not scene.stopped


def test_with_the_tab_open_nothing_changes_full_or_not(scene):
    scene.person("free1", tab=True)
    scene.tick()
    assert scene.rec("free1")["status"] == "live"
    assert api._away_seconds("free1") == 0.0


def test_stepping_out_for_a_minute_is_not_being_away(scene):
    """A refresh, a closed lid, a dropped connection: the tab was there a
    minute ago. Past the grace, it counts."""
    scene.person("free1")
    api._ws_last_seen["free1"] = time.time() - 60
    scene.tick()
    assert scene.rec("free1")["status"] == "live"
    api._ws_last_seen["free1"] = time.time() - api._QUEUE_PAUSE_GRACE - 5
    scene.tick()
    assert scene.rec("free1")["status"] == "paused"


def test_using_the_site_without_a_socket_is_not_being_away(scene):
    """A network that blocks websockets must not make somebody who is clicking
    around look absent."""
    scene.person("free1", last=time.time() - 30)
    scene.tick()
    assert scene.rec("free1")["status"] == "live"


@pytest.mark.parametrize("kw", [
    {"status": "active", "plan": "pro"},
    {"status": "active", "plan": "starter"},
    {"status": "active", "plan": "studio"},
    {"status": "trialing", "trial_ends_at": NOW + 86400},
    {"status": "none", "is_admin": True},
])
def test_a_plan_that_pays_for_watching_is_never_paused_for_this(scene, kw):
    scene.person("p", **kw)
    scene.tick()
    assert scene.rec("p")["status"] == "live" and not scene.stopped


def test_highlight_clips_do_not_fill_the_queue(scene):
    """The count is the one that refuses a clip (pending_room): Highlights
    have their own allowance and never occupy a slot a clip wanted."""
    scene.person("free1", pending=FREE_CAP - 1, suggested=10)
    scene.tick()
    assert scene.rec("free1")["status"] == "live"


def _apply(scene, uid):
    """The rule alone, without the reaper trimming the account to one channel
    first (Free has one; these are about an account holding more)."""
    return asyncio.run(api._pause_if_queue_full_and_away(uid, scene.users[uid], time.time()))


def test_every_channel_of_the_account_is_paused_live_or_waiting(scene):
    scene.person("free1", channels=("one", "two"))
    api._streams["free1:two"]["status"] = "offline"
    assert _apply(scene, "free1") is True
    assert scene.rec("free1", "one")["status"] == scene.rec("free1", "two")["status"] == "paused"
    assert scene.notes[0][1]["title"].endswith("one and two")
    assert _apply(scene, "free1") is False                # nothing left to pause, nothing said
    assert len(scene.notes) == 1


def test_a_plan_with_no_queue_at_all_is_not_a_full_queue(scene, monkeypatch):
    scene.person("free1", pending=0)
    monkeypatch.setattr(api, "pending_room", lambda uid: (0, 0))
    scene.tick()
    assert scene.rec("free1")["status"] == "live"


# ── 2. a deploy is not everybody leaving ─────────────────────────────────────

def test_right_after_a_restart_nobody_is_away(scene, monkeypatch):
    """Every socket closed ten seconds ago, with the process. The accounts
    were last active an hour ago by the clock that survives a restart. Nobody
    is paused until the tabs have had the grace to come back."""
    monkeypatch.setattr(api, "_PROCESS_STARTED_AT", time.time() - 10)
    scene.person("free1")
    scene.person("free2")
    scene.tick()
    assert scene.rec("free1")["status"] == scene.rec("free2")["status"] == "live"
    assert api._away_seconds("free1") < 15
    monkeypatch.setattr(api, "_PROCESS_STARTED_AT", time.time() - api._QUEUE_PAUSE_GRACE - 5)
    api._ws_clients["free2"] = {object()}                 # this one's tab reconnected
    scene.tick()
    assert scene.rec("free1")["status"] == "paused" and scene.rec("free2")["status"] == "live"


# ── 3. they come back ────────────────────────────────────────────────────────

def test_coming_back_restarts_the_channel_and_takes_the_reason_off(scene):
    scene.person("free1")
    scene.tick()
    assert asyncio.run(api._resume_user_streams("free1")) == 1
    rec = scene.rec("free1")
    assert rec["status"] == "starting" and "hold" not in rec and "paused_at" not in rec
    assert scene.started == [("chan", "free1")]
    assert "free1" not in api._paused_users


def test_a_channel_held_for_a_failing_card_is_still_not_restarted_by_a_visit(scene):
    scene.person("free1", channels=("one", "two"))
    api._streams["free1:two"].update(status="paused", hold="billing")
    assert _apply(scene, "free1") is True
    assert scene.notes[0][1]["title"].endswith("paused one")      # only what it paused
    asyncio.run(api._resume_user_streams("free1"))
    assert scene.rec("free1", "one")["status"] == "starting"
    assert scene.rec("free1", "two")["status"] == "paused" and scene.rec("free1", "two")["hold"] == "billing"


def test_the_eight_hour_pause_does_not_announce_it_a_second_time(scene):
    """Already paused for the queue; hours later the idle rule finds nothing
    left to pause, so there is no second toast."""
    scene.person("free1", last=NOW - 9 * 3600)
    scene.tick()
    scene.sent.clear()
    scene.tick()
    assert _events(scene, "free1") == []
    assert scene.rec("free1")["hold"] == "queue_full"


def test_the_eight_hour_pause_still_works_without_a_full_queue(scene):
    scene.person("free1", last=NOW - 9 * 3600, pending=3)
    scene.tick()
    rec = scene.rec("free1")
    assert rec["status"] == "paused" and "hold" not in rec
    assert "streams_paused_idle" in _events(scene, "free1")


def test_the_first_tab_back_is_told_once(monkeypatch):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.auth import users as user_store
    person = {"id": "fan", "username": "fan", "subscription_status": "none"}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: person if uid == "fan" else None)
    monkeypatch.setattr(user_store, "_load", lambda: [person])
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(api, "_ws_clients", {})
    monkeypatch.setattr(api, "_ws_last_seen", {})
    monkeypatch.setattr(api, "_queue_pause_welcome",
                        {"fan": {"event": "queue_pause", "message": "Welcome back. It is starting again now."}})
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "fan", "username": "fan", "is_admin": False,
         "subscription_status": "none"}).encode())).decode())
    with c.websocket_connect("/ws") as ws:
        got = json.loads(ws.receive_text())
        assert got == {"event": "queue_pause", "message": "Welcome back. It is starting again now."}
        assert api._ws_last_seen["fan"] > NOW - 5
    assert "fan" not in api._queue_pause_welcome          # once
    assert api._ws_last_seen["fan"] > NOW - 5            # and the clock starts when it closes


# ── 4. they are told ─────────────────────────────────────────────────────────

def test_the_bell_note_says_what_why_and_what_pro_does(scene):
    scene.person("free1")
    scene.tick()
    [(uid, note)] = scene.notes
    assert uid == "free1"
    assert note["title"] == "Your review queue filled up, so we paused chan"
    body = note["body"]
    assert f"reached its {FREE_CAP} clips while Highlightz was closed" in body
    assert "nothing you had already caught was touched" in body
    assert "starts again by itself the next time you open Highlightz" in body
    assert "That is how the Free plan works" in body
    pro = plans.PLAN_LIMITS["pro"]
    assert f"Pro keeps watching around the clock, with or without the tab open, and holds {pro['max_pending']} clips" in body
    assert f"It is ${pro['price']} a month if you ever want that" in body
    # Said as a fact, not as a push.
    for pushy in ("Upgrade now", "upgrade now", "!", "only because", "Don't miss", "limited"):
        assert pushy not in body and pushy not in note["title"]


def test_the_words_fit_one_channel_or_several():
    one = api._queue_pause_words(["xqc"], 20, {"subscription_status": "none"})
    assert "xqc was paused" in one["body"] and "xqc was paused" in one["back"]
    two = api._queue_pause_words(["xqc", "lacy"], 20, {"subscription_status": "none"})
    assert "xqc and lacy were paused" in two["body"]
    assert two["title"].endswith("xqc and lacy")
    assert api._name_channels([]) == "your channel"
    assert api._name_channels(["a", "b", "c"]) == "a, b and c"
    assert one["back"].startswith("Welcome back.") and "It is starting again now." in one["back"]
    assert "Pro keeps watching around the clock and holds 200 clips." in one["back"]


def test_the_note_is_left_at_most_once_a_day(scene):
    scene.person("free1")
    scene.tick()
    asyncio.run(api._resume_user_streams("free1"))        # back...
    api._streams["free1:chan"]["status"] = "live"
    api._user_last_active["free1"] = AWAY                 # ...and gone again, still full
    scene.tick()
    assert scene.rec("free1")["status"] == "paused"
    assert len(scene.notes) == 1, "the same note twice in a day"
    scene.users["free1"]["notices"][0]["at"] = time.time() - api._QUEUE_PAUSE_NOTICE_GAP - 60
    asyncio.run(api._resume_user_streams("free1"))
    api._streams["free1:chan"]["status"] = "live"
    scene.tick()
    assert len(scene.notes) == 2


def test_a_note_that_cannot_be_saved_does_not_stop_the_pause(scene, monkeypatch):
    from src.auth import users as user_store

    def boom(batch):
        raise OSError("disk full")
    monkeypatch.setattr(user_store, "add_notices", boom)
    scene.person("free1")
    scene.tick()
    assert scene.rec("free1")["status"] == "paused"
    assert "queue_pause" in _events(scene, "free1")


# ── 5. realtime, and the site saying what the code does ──────────────────────

def test_the_pause_reaches_any_tab_live_and_queues_the_welcome(scene):
    scene.person("free1")
    scene.tick()
    ev = _events(scene, "free1")
    assert ev.index("stream_updated") < ev.index("queue_pause") < ev.index("inbox_changed")
    msg = next(m for e, u, m in scene.sent if e == "queue_pause")
    assert msg["message"] == "Your review queue is full, so chan was paused until you are back at Highlightz."
    assert api._queue_pause_welcome["free1"]["event"] == "queue_pause"
    assert api._queue_pause_welcome["free1"]["message"].startswith("Welcome back.")


def test_the_dashboard_handles_the_event_and_names_the_reason():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "else if(msg.event==='queue_pause'){if(msg.message)flash(msg.message);}" in page
    assert page.count("'paused · review queue full'") == 2          # the card and the detail head
    assert "It starts again by itself when you are back." in page
    # Said BEFORE it happens, in the notice a full queue already shows.
    assert "Highlightz keeps watching only with this tab open; close it and your channel pauses until you are back." in page
    assert "me.plan_limits.pauses_when_idle" in page


def test_the_account_is_told_whether_its_plan_pauses():
    src = inspect.getsource(api)
    assert '"pauses_when_idle": bool(limits.get("pauses_when_idle")),' in src
    assert plans.limits_for({"subscription_status": "none"})["pauses_when_idle"] is True
    assert plans.limits_for({"subscription_status": "active", "plan": "pro"})["pauses_when_idle"] is False


def test_the_faq_and_the_brief_say_it():
    from fastapi.testclient import TestClient
    faq = api.LANDING_HTML
    assert ("On Free it keeps watching with the tab closed until your review queue is full or 8 "
            "hours have passed without you opening the dashboard, whichever comes first") in faq
    assert "a queue that fills while the dashboard is closed pauses the channel" in faq
    assert "Pro and Studio keep watching either way." in faq
    brief = TestClient(api.app).get("/llms.txt").text
    assert "on Free, monitoring continues with the dashboard closed until the review" in brief


def test_the_reaper_asks_before_the_eight_hour_rule():
    src = inspect.getsource(api.idle_stream_reaper)
    assert src.index("_pause_if_queue_full_and_away(uid, db_user, now)") < src.index("_IDLE_STREAM_TIMEOUT")
    # ...and only for accounts whose plan pauses at all.
    assert src.index("if not _plans.pauses_when_idle(db_user):") < src.index("_pause_if_queue_full_and_away(")
