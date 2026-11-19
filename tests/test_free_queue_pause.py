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

LATER THE SAME DAY (owner): "make it so that even free users with tabs open
get the streams offline 15 min after their at capacity and have not accepted
or rejected anything. Also I had some users that had multiple tabs of
highlightz open before make sure there are no loop holes with getting more
clips on the free tier with that". Section 6 holds that:

  6. FIFTEEN MINUTES AT CAPACITY with nothing accepted or rejected pauses a
     Free account whatever tabs are open. The clock is the account's (when
     the queue filled, and the last clip it decided), so tabs, reconnects and
     requests do not move it. And a channel paused for a full queue STAYS
     paused until the queue has room: a request does not restart it, which
     is what would otherwise make the rule decoration for anybody with a tab
     open.
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
JUST_FILLED = NOW - 120            # the queue's newest clip landed two minutes ago
LONG_FULL = NOW - 1000             # ...or more than fifteen minutes ago
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
    monkeypatch.setattr(api, "_queue_held", set())
    monkeypatch.setattr(api, "_last_review_at", {})
    monkeypatch.setattr(api, "_queue_hold_checked", {})
    monkeypatch.setattr(api, "_PROCESS_STARTED_AT", NOW - 86400)     # up since yesterday
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "activity.json")
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._live_slots.clear()

    def person(uid, status="none", plan=None, last=AWAY, channels=("chan",), pending=FREE_CAP,
               suggested=0, tab=False, filled=JUST_FILLED, **extra):
        users[uid] = {"id": uid, "username": uid, "subscription_status": status,
                      **({"plan": plan} if plan else {}), **extra}
        api._user_last_active[uid] = last
        for i, ch in enumerate(channels):
            key = f"{uid}:{ch}"
            api._streams[key] = {"channel": ch, "platform": "twitch", "preset": "default",
                                 "status": "live", "user_id": uid, "added_at": 1.0 + i}
            api._live_slots.add(key)
        for i in range(pending):
            # The newest (i == 0) is the one that filled the queue.
            api._clips[f"{uid}-p{i}"] = {"id": f"{uid}-p{i}", "user_id": uid, "status": "pending",
                                         "created_at": filled - 60 * i}
        for i in range(suggested):
            api._clips[f"{uid}-s{i}"] = {"id": f"{uid}-s{i}", "user_id": uid, "status": "pending",
                                         "suggested": True}
        if tab:
            api._ws_clients[uid] = {object() for _ in range(tab if isinstance(tab, int) and tab > 1 else 1)}
            api._user_last_active[uid] = time.time()

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


def test_with_the_tab_open_a_queue_that_has_just_filled_is_left_alone(scene):
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
    return asyncio.run(api._pause_if_queue_full(uid, scene.users[uid], time.time()))


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

def _decide_one(uid):
    """They accepted or rejected a clip: one fewer waiting."""
    api._clips.pop(next(k for k, c in api._clips.items()
                        if c["user_id"] == uid and c["status"] == "pending" and not c.get("suggested")))


def test_back_with_room_in_the_queue_restarts_the_channel_and_takes_the_reason_off(scene):
    scene.person("free1")
    scene.tick()
    _decide_one("free1")
    assert asyncio.run(api._resume_user_streams("free1")) == 1
    rec = scene.rec("free1")
    assert rec["status"] == "starting" and "hold" not in rec and "paused_at" not in rec
    assert scene.started == [("chan", "free1")]
    assert "free1" not in api._paused_users and "free1" not in api._queue_held


def test_back_with_the_queue_still_full_it_waits_for_a_decision(scene):
    """It used to restart on the visit and pause again when the tab closed.
    With nowhere to put a clip there is nothing a restarted channel can do."""
    scene.person("free1")
    scene.tick()
    assert asyncio.run(api._resume_user_streams("free1")) == 0
    rec = scene.rec("free1")
    assert rec["status"] == "paused" and rec["hold"] == "queue_full"
    assert scene.started == []
    assert "free1" in api._paused_users, "nothing would ever ask again"
    assert "free1" in api._queue_held


def test_a_channel_held_for_a_failing_card_is_still_not_restarted_by_a_visit(scene):
    scene.person("free1", channels=("one", "two"))
    api._streams["free1:two"].update(status="paused", hold="billing")
    assert _apply(scene, "free1") is True
    assert scene.notes[0][1]["title"].endswith("paused one")      # only what it paused
    _decide_one("free1")
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
    assert "It starts again as soon as you accept or reject a clip, which makes room for the next one." in body
    assert ("That is how the Free plan works: it keeps watching, with the dashboard open or "
            "closed, until your queue is full.") in body
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
    # Back with the queue still full: told what starts it, not that it is starting.
    assert one["back_full"].startswith("Welcome back.") and "starting again now" not in one["back_full"]
    assert "Accept or reject a clip and it starts again." in one["back_full"]
    # Paused with the dashboard open: it does not say Highlightz was closed.
    here = api._queue_pause_words(["xqc"], 20, {"subscription_status": "none"}, "unreviewed")
    assert "held its 20 clips for 15 minutes without one being accepted or rejected" in here["body"]
    assert "while Highlightz was closed" not in here["body"]
    assert here["paused"] == ("Your review queue has been full for 15 minutes, so xqc was paused. "
                              "Accept or reject a clip and it starts again.")
    assert here["title"] == one["title"], "the once-a-day check reads the title"


def test_the_note_is_left_at_most_once_a_day(scene):
    def back_and_gone_again(n):
        clip = dict(next(c for c in api._clips.values() if c["user_id"] == "free1"))
        _decide_one("free1")                                  # back, and a clip decided...
        assert asyncio.run(api._resume_user_streams("free1")) == 1
        api._streams["free1:chan"]["status"] = "live"
        api._clips[f"refill{n}"] = {**clip, "id": f"refill{n}"}   # ...the next one fills it again...
        api._user_last_active["free1"] = AWAY                 # ...and they are gone
    scene.person("free1")
    scene.tick()
    back_and_gone_again(1)
    scene.tick()
    assert scene.rec("free1")["status"] == "paused"
    assert len(scene.notes) == 1, "the same note twice in a day"
    scene.users["free1"]["notices"][0]["at"] = time.time() - api._QUEUE_PAUSE_NOTICE_GAP - 60
    back_and_gone_again(2)
    scene.tick()
    assert scene.rec("free1")["status"] == "paused" and len(scene.notes) == 2


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
    assert msg["message"] == ("Your review queue is full, so chan was paused. "
                              "Accept or reject a clip and it starts again.")
    assert api._queue_pause_welcome["free1"]["event"] == "queue_pause"
    assert api._queue_pause_welcome["free1"]["message"].startswith("Welcome back.")
    assert "Accept or reject a clip" in api._queue_pause_welcome["free1"]["full"]


def test_the_dashboard_handles_the_event_and_names_the_reason():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "else if(msg.event==='queue_pause'){if(msg.message)flash(msg.message);}" in page
    assert page.count("'paused · review queue full'") == 2          # the card and the detail head
    assert "Accept or reject one and it starts again." in page
    assert "It starts again by itself when you are back." not in page, "the card promises what no longer happens"
    # Said BEFORE it happens, in the notice a full queue already shows.
    assert ("If it stays full for 15 minutes with nothing accepted or rejected, or you close "
            "Highlightz, your channel pauses until you make room.") in page
    assert "keeps watching only with this tab open" not in page
    assert "me.plan_limits.pauses_when_idle" in page


def test_the_account_is_told_whether_its_plan_pauses():
    src = inspect.getsource(api)
    assert '"pauses_when_idle": bool(limits.get("pauses_when_idle")),' in src
    assert plans.limits_for({"subscription_status": "none"})["pauses_when_idle"] is True
    assert plans.limits_for({"subscription_status": "active", "plan": "pro"})["pauses_when_idle"] is False


def test_the_faq_and_the_brief_say_it():
    from fastapi.testclient import TestClient
    faq = api.LANDING_HTML
    assert "On Free it keeps watching, with the tab open or closed, until your review" in faq
    assert "A full queue pauses the channel until you accept or reject a clip" in faq
    assert "after 15 minutes without a clip accepted or rejected" in faq
    assert "It starts again as soon as you accept or reject one" in faq
    assert "Pro and Studio keep watching either way." in faq
    assert "it starts again when you open Highlightz" not in faq
    brief = TestClient(api.app).get("/llms.txt").text
    assert "on Free, monitoring continues, with the dashboard open or closed, until the" in brief
    assert "(it resumes once a clip is accepted or rejected)" in brief


def test_the_reaper_asks_before_the_eight_hour_rule():
    src = inspect.getsource(api.idle_stream_reaper)
    assert src.index("_pause_if_queue_full(uid, db_user, now, queues.get(uid") < src.index("_IDLE_STREAM_TIMEOUT")
    # ...and only for accounts whose plan pauses at all.
    assert src.index("if not _plans.pauses_when_idle(db_user):") < src.index("_pause_if_queue_full(")
    # Every account's queue is counted once for the pass, not once an account.
    assert src.count("_queue_snapshot()") == 1 and "pending_room(" not in src


# ── 6. fifteen minutes at capacity, tab or no tab; and no way round it ───────

def _watch(now=None):
    return asyncio.run(api.queue_full_watch_once(now))


def test_fifteen_minutes_at_capacity_with_nothing_decided_pauses_it_with_the_tab_open(scene):
    scene.person("free1", tab=True, filled=LONG_FULL)
    assert api._away_seconds("free1") == 0.0                  # they are "there"
    assert _watch() == 1
    rec = scene.rec("free1")
    assert rec["status"] == "paused" and rec["hold"] == "queue_full"
    assert "free1:chan" not in api._live_slots and scene.stopped == [("chan", "free1")]
    assert "free1" in api._queue_held
    # Told live, in the tab that is open, with the way back; and a bell note.
    msg = next(m for e, u, m in scene.sent if e == "queue_pause")
    assert msg["message"] == ("Your review queue has been full for 15 minutes, so chan was paused. "
                              "Accept or reject a clip and it starts again.")
    ev = _events(scene, "free1")
    assert ev.index("stream_updated") < ev.index("queue_pause") < ev.index("inbox_changed")
    [(_uid, note)] = scene.notes
    assert "for 15 minutes without one being accepted or rejected" in note["body"]
    # They were not away: no "welcome back" is owed to the next tab.
    assert "free1" not in api._queue_pause_welcome


@pytest.mark.parametrize("ago, paused", [(14 * 60, False), (15 * 60 + 5, True)])
def test_fifteen_means_fifteen(scene, ago, paused):
    scene.person("free1", tab=True, filled=time.time() - ago)
    _watch()
    assert (scene.rec("free1")["status"] == "paused") is paused
    assert api._QUEUE_FULL_UNREVIEWED == 900 and api._QUEUE_WATCH_EVERY <= 60


def test_accepting_or_rejecting_is_what_restarts_the_fifteen_minutes(scene):
    """Full for a long while, but a clip was decided five minutes ago (and
    the queue is full again): they are at their queue. Fifteen minutes after
    the last decision with it still full, it pauses."""
    scene.person("free1", tab=True, filled=LONG_FULL)
    api._last_review_at["free1"] = time.time() - 300
    assert _watch() == 0 and scene.rec("free1")["status"] == "live"
    api._last_review_at["free1"] = time.time() - 901
    assert _watch() == 1 and scene.rec("free1")["status"] == "paused"


def test_a_new_clip_filling_the_queue_starts_the_clock_again(scene):
    """They decided one twenty minutes ago; the queue filled again two
    minutes ago. Fifteen minutes run from when it FILLED."""
    scene.person("free1", tab=True, filled=LONG_FULL)
    api._clips["free1-p0"]["created_at"] = time.time() - 120
    assert _watch() == 0 and scene.rec("free1")["status"] == "live"


def test_not_at_capacity_is_never_this(scene):
    scene.person("free1", tab=True, pending=FREE_CAP - 1, filled=LONG_FULL)
    assert _watch() == 0 and scene.rec("free1")["status"] == "live"


@pytest.mark.parametrize("kw", [
    {"status": "active", "plan": "pro", "pending": 200},
    {"status": "active", "plan": "starter", "pending": 50},
    {"status": "trialing", "trial_ends_at": NOW + 86400, "pending": 200},
    {"status": "none", "is_admin": True, "pending": 60},
])
def test_a_paying_plan_at_its_own_capacity_is_never_paused_for_it(scene, kw):
    scene.person("p", tab=True, filled=LONG_FULL, **kw)
    assert _watch() == 0 and scene.rec("p")["status"] == "live"


def test_more_tabs_buy_nothing(scene):
    """The clock is the account's. Eight tabs, all connected a second ago,
    and a request a second ago: the queue has still been full for sixteen
    minutes with nothing decided."""
    scene.person("free1", tab=8, filled=LONG_FULL)
    api._ws_last_seen["free1"] = api._user_last_active["free1"] = time.time()
    assert len(api._ws_clients["free1"]) == 8
    assert _watch() == 1 and scene.rec("free1")["status"] == "paused"


def test_a_request_does_not_restart_a_channel_paused_for_a_full_queue(scene):
    """THE LOOPHOLE. A paused channel restarted on the account's next
    request. With a tab open there is always a next request, from that tab or
    any other, so the pause would have lasted seconds."""
    scene.person("free1", tab=3, filled=LONG_FULL)
    _watch()
    for _ in range(40):                                       # a page load in every tab
        assert api._may_resume_now("free1") is False
    api._queue_hold_checked.clear()                           # and again a minute later
    assert api._may_resume_now("free1") is False
    assert asyncio.run(api._resume_user_streams("free1")) == 0
    assert scene.rec("free1")["status"] == "paused" and scene.started == []
    # The request that finds them asks through this, and only this.
    src = inspect.getsource(api)
    assert "if uid in _paused_users and _may_resume_now(uid):" in src
    assert src.count("asyncio.create_task(_resume_user_streams(uid))") == 1


def test_the_room_is_counted_once_for_a_burst_of_requests(scene, monkeypatch):
    scene.person("free1", tab=True, filled=LONG_FULL)
    _watch()
    counted = []
    real = api.pending_room
    monkeypatch.setattr(api, "pending_room", lambda uid: counted.append(uid) or real(uid))
    for _ in range(25):
        api._may_resume_now("free1")
    assert len(counted) == 1, "every request of a page load read every clip"


def test_deciding_a_clip_restarts_it_at_once(scene):
    scene.person("free1", tab=True, filled=LONG_FULL)
    _watch()
    scene.sent.clear()

    async def go():
        _decide_one("free1")
        api._reviewed("free1")                                # what approve and reject call
        await asyncio.gather(*list(api._review_tasks))
    asyncio.run(go())
    rec = scene.rec("free1")
    assert rec["status"] == "starting" and "hold" not in rec
    assert scene.started == [("chan", "free1")]
    assert "stream_updated" in _events(scene, "free1"), "the open tab was not told it restarted"
    assert "free1" not in api._queue_held and "free1" not in api._paused_users
    assert api._last_review_at["free1"] > NOW - 5


def test_taking_a_decision_back_does_not_restart_a_full_queue(scene):
    """Undo puts the clip back: the queue is as full as it was."""
    scene.person("free1", tab=True, filled=LONG_FULL)
    _watch()

    async def go():
        api._reviewed("free1")
        await asyncio.gather(*list(api._review_tasks))
    asyncio.run(go())
    assert scene.rec("free1")["status"] == "paused"


def test_every_button_that_decides_a_clip_counts(scene):
    src = inspect.getsource(api)
    for fn in (api.approve_clip, api.reject_clip, api.delete_clip_endpoint,
               api.clear_pending_clips, api.bulk_cull_clips, api.undo_last):
        assert "_reviewed(uid)" in inspect.getsource(fn), fn.__name__
    # Nothing else stamps it: not a socket, not a request.
    assert src.count("_last_review_at[uid] = ") == 1
    ws = inspect.getsource(api.websocket_endpoint)
    assert "_last_review_at" not in ws and "_reviewed" not in ws


def test_a_channel_held_for_the_queue_is_let_go_when_the_plan_stops_pausing(scene):
    """Paused on Free with a full queue, then they subscribe: Pro is never
    kept paused for this, whatever is in the queue."""
    scene.person("free1", tab=True, filled=LONG_FULL)
    _watch()
    scene.users["free1"].update(subscription_status="active", plan="pro", stripe_customer_id="cus_1")
    assert api._may_resume_now("free1") is True
    assert asyncio.run(api._resume_user_streams("free1")) == 1
    assert scene.rec("free1")["status"] == "starting"


def test_removing_the_held_channel_does_not_leave_the_account_stuck(scene):
    scene.person("free1", tab=True, filled=LONG_FULL)
    _watch()
    api._streams.pop("free1:chan")
    assert api._may_resume_now("free1") is True and "free1" not in api._queue_held


def test_a_restart_does_not_pause_somebody_in_the_middle_of_their_queue(scene, monkeypatch):
    """Who decided a clip a minute ago is in memory and a deploy forgets it.
    So the fifteen minutes are never counted from before the process started;
    the first rule's own grace still applies to whoever is not there."""
    scene.person("free1", tab=True, filled=LONG_FULL)
    monkeypatch.setattr(api, "_PROCESS_STARTED_AT", time.time() - 60)
    assert _watch() == 0 and scene.rec("free1")["status"] == "live"
    monkeypatch.setattr(api, "_PROCESS_STARTED_AT", time.time() - 901)
    assert _watch() == 1


def test_a_restart_does_not_turn_the_hold_into_one_a_request_ends():
    """The set is rebuilt from the saved records, like _paused_users."""
    src = inspect.getsource(api)
    block = src[src.index("_queue_held: set[str] = {"):src.index("_review_tasks: set = set()")]
    assert 's.get("status") == "paused" and s.get("hold") == "queue_full"' in block
    assert "for s in _streams.values()" in block


def test_one_pass_reads_the_clips_once_and_looks_up_only_who_could_be_owed_it(scene, monkeypatch):
    """It runs every minute on the app's one loop."""
    from src.auth import users as user_store
    scene.person("full", tab=True, filled=LONG_FULL)
    scene.person("light", tab=True, pending=3)                 # watching, nowhere near full
    scene.person("nostream", pending=FREE_CAP, channels=())    # full, watching nothing
    scene.person("already", filled=LONG_FULL)
    api._streams["already:chan"].update(status="paused", hold="queue_full")
    looked = []
    real = user_store.get_by_id
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: looked.append(uid) or real(uid))
    snaps = []
    real_snap = api._queue_snapshot
    monkeypatch.setattr(api, "_queue_snapshot", lambda: snaps.append(1) or real_snap())
    monkeypatch.setattr(api, "pending_room", lambda uid: (_ for _ in ()).throw(AssertionError("per-account count")))
    assert _watch() == 1
    assert looked == ["full"] and len(snaps) == 1


def test_the_count_is_the_one_that_refuses_a_clip(scene):
    scene.person("a", pending=4, suggested=7, filled=NOW - 50)
    api._clips["a-approved"] = {"id": "x", "user_id": "a", "status": "approved", "created_at": NOW}
    api._clips["nobody"] = {"id": "y", "status": "pending", "created_at": NOW}
    snap = api._queue_snapshot()
    assert snap == {"a": (4, NOW - 50)}
    assert snap["a"][0] == api.pending_room("a")[0]


def test_one_account_failing_does_not_stop_the_pass(scene, monkeypatch):
    scene.person("bad", tab=True, filled=LONG_FULL)
    scene.person("good", tab=True, filled=LONG_FULL)
    real = api._pause_if_queue_full

    async def flaky(uid, *a, **kw):
        if uid == "bad":
            raise RuntimeError("boom")
        return await real(uid, *a, **kw)
    monkeypatch.setattr(api, "_pause_if_queue_full", flaky)
    assert _watch() == 1 and scene.rec("good")["status"] == "paused"


def test_the_first_tab_back_to_a_queue_that_is_still_full_is_told_what_starts_it(monkeypatch):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.auth import users as user_store
    person = {"id": "fan", "username": "fan", "subscription_status": "none"}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: person if uid == "fan" else None)
    monkeypatch.setattr(user_store, "_load", lambda: [person])
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(api, "_ws_clients", {})
    monkeypatch.setattr(api, "_ws_last_seen", {})
    monkeypatch.setattr(api, "_queue_held", {"fan"})
    monkeypatch.setattr(api, "_queue_pause_welcome",
                        {"fan": {"event": "queue_pause", "message": "starting again now",
                                 "full": "accept or reject a clip"}})
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "fan", "username": "fan", "is_admin": False,
         "subscription_status": "none"}).encode())).decode())
    with c.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text()) == {"event": "queue_pause", "message": "accept or reject a clip"}
    assert "fan" not in api._queue_pause_welcome


def test_the_site_runs_the_minute_pass():
    import src.main as main
    assert 'asyncio.create_task(dashboard_api.queue_full_watch(), name="queue-full-watch")' in inspect.getsource(main)
    src = inspect.getsource(api.queue_full_watch)
    assert "await asyncio.sleep(_QUEUE_WATCH_EVERY)" in src and "queue_full_watch_once()" in src


def test_the_review_screen_does_not_call_a_paused_channel_live():
    """It counted every listed channel as "live". A channel can now be paused
    while this screen is open, and this is the screen that ends the pause."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "const watchedN = streamsArr.filter(s=>s.status!=='paused').length;" in page
    assert "const heldN = streamsArr.filter(s=>s.status==='paused'&&s.hold==='queue_full').length;" in page
    assert "{watchedN} live" in page and "{heldN} paused · queue full" in page
    assert "{streamsArr.length} live" not in page


def test_the_eight_hour_rule_logs_a_pause_only_when_it_pauses(scene, monkeypatch):
    """Production, 2026-11-19: 985 "idle_stream_reaper_pausing" lines in three
    hours, for two accounts this rule had paused. The line was written before
    asking, on every pass, for every account already paused and idle."""
    logged = []
    monkeypatch.setattr(api.log, "info", lambda event, **kw: logged.append(event))
    scene.person("free1", last=NOW - 9 * 3600, pending=3)
    scene.tick()
    assert scene.rec("free1")["status"] == "paused"
    assert logged.count("idle_stream_reaper_pausing") == 1
    scene.tick()
    scene.tick()
    assert logged.count("idle_stream_reaper_pausing") == 1, "an account already paused was 'paused' again"
