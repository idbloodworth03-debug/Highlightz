"""Paid plans keep watching; only Free pauses when the account goes quiet
(owner, 2026-10-15).

From a Pro customer's ticket: their three Kick channels sat "paused" because
they had not opened Highlightz for eight hours, so the streamer they follow
went live with nobody watching. The owner chose: Starter and Pro keep
monitoring while the person is away; Free still pauses after 8 hours.

Reading the reaper to make that change showed the rule had been running
BACKWARDS since the free tier landed (2026-08-03): the early exit for
non-paying accounts sat above the idle check, so free accounts never paused
and paying ones always did.

These run ONE real tick of idle_stream_reaper against a scene of accounts:
  1. an idle free account is paused (the inversion fixed);
  2. idle Pro, Starter, trialing and admin accounts are not;
  3. a paid account an earlier pause left paused is resumed on the tick, live;
  4. a recently active free account is left alone, and a lapsed one is
     trimmed to the free allowance AND then idle-checked;
  5. the per-plan switch, and the copy that describes it.
"""

import asyncio
import time

import pytest

from src.billing import plans
from src.dashboard import api

NOW = time.time()
IDLE = NOW - 9 * 3600          # past the 8-hour line
RECENT = NOW - 3600


class _Stop(Exception):
    pass


@pytest.fixture
def scene(tmp_path, monkeypatch):
    users, started, stopped, sent = {}, [], [], []

    async def _new(channel, platform, preset, uid=""):
        started.append((channel, uid))

    async def _rm(channel, uid=""):
        stopped.append((channel, uid))

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))

    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: users.get(uid))
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_paused_users", set())
    monkeypatch.setattr(api, "_user_last_active", {})
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "activity.json")
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._live_slots.clear()

    def person(uid, status="none", plan=None, last=IDLE, channels=("chan",), stream_status="live", **extra):
        users[uid] = {"id": uid, "username": uid, "subscription_status": status,
                      **({"plan": plan} if plan else {}), **extra}
        api._user_last_active[uid] = last
        for i, ch in enumerate(channels):
            key = f"{uid}:{ch}"
            api._streams[key] = {"channel": ch, "platform": "kick", "preset": "default",
                                 "status": stream_status, "user_id": uid, "added_at": 1.0 + i}
            if stream_status == "paused":
                api._paused_users.add(uid)

    def tick():
        calls = {"n": 0}

        async def fake_sleep(_s):
            calls["n"] += 1
            if calls["n"] > 1:
                raise _Stop()
        monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
        with pytest.raises(_Stop):
            asyncio.run(api.idle_stream_reaper())

    def status(uid, ch="chan"):
        rec = api._streams.get(f"{uid}:{ch}")
        return rec and rec["status"]

    return type("Scene", (), {"person": staticmethod(person), "tick": staticmethod(tick),
                              "status": staticmethod(status), "started": started,
                              "stopped": stopped, "sent": sent})


# ── 1 & 2. who pauses ────────────────────────────────────────────────────────

def test_an_idle_free_account_is_paused_which_it_never_used_to_be(scene):
    scene.person("free1")
    scene.tick()
    assert scene.status("free1") == "paused", "a free account still never pauses"
    assert ("streams_paused_idle", "free1") in scene.sent


@pytest.mark.parametrize("who", [
    dict(status="active", plan="pro"),
    dict(status="active", plan="starter"),
    dict(status="trialing", trial_ends_at=NOW + 86400),
    dict(status="none", is_admin=True),
])
def test_idle_paid_trial_and_staff_accounts_keep_watching(scene, who):
    scene.person("payer", **who)
    scene.tick()
    assert scene.status("payer") == "live", f"{who} was paused"
    assert ("streams_paused_idle", "payer") not in scene.sent
    assert scene.stopped == []


# ── 3. paid accounts an old pause caught come back ───────────────────────────

def test_a_paid_account_left_paused_is_resumed_on_the_tick(scene):
    scene.person("luclips", status="active", plan="pro", stream_status="paused",
                 channels=("lacobraaa", "westcol"))
    scene.tick()
    assert scene.status("luclips", "lacobraaa") == "starting"
    assert scene.status("luclips", "westcol") == "starting"
    assert sorted(scene.started) == [("lacobraaa", "luclips"), ("westcol", "luclips")]
    assert "luclips" not in api._paused_users
    # Realtime: the open tab sees each channel come back.
    assert scene.sent.count(("stream_updated", "luclips")) == 2


def test_a_paused_free_account_is_not_resumed_by_the_tick(scene):
    scene.person("free2", stream_status="paused")
    scene.tick()
    assert scene.status("free2") == "paused" and scene.started == []


# ── 4. the rest of the free path ─────────────────────────────────────────────

def test_a_recently_active_free_account_is_left_alone(scene):
    scene.person("free3", last=RECENT)
    scene.tick()
    assert scene.status("free3") == "live"


def test_a_lapsed_account_is_trimmed_and_then_idle_checked(scene):
    scene.person("lapsed", status="canceled", channels=("first", "second", "third"))
    scene.tick()
    # Trimmed to the free allowance (one channel, the oldest kept) ...
    assert set(k for k in api._streams if k.startswith("lapsed:")) == {"lapsed:first"}
    # ... and, idle, that one is paused rather than skipped.
    assert scene.status("lapsed", "first") == "paused"


# ── 5. the switch and the words ──────────────────────────────────────────────

def test_the_per_plan_switch():
    assert plans.PLAN_LIMITS["free"]["pauses_when_idle"] is True
    assert plans.PLAN_LIMITS["locked"]["pauses_when_idle"] is True
    assert plans.PLAN_LIMITS["starter"]["pauses_when_idle"] is False
    assert plans.PLAN_LIMITS["pro"]["pauses_when_idle"] is False
    assert plans.pauses_when_idle({"subscription_status": "none"}) is True
    assert plans.pauses_when_idle({"subscription_status": "active", "plan": "starter"}) is False
    assert plans.pauses_when_idle({"subscription_status": "trialing",
                                   "trial_ends_at": NOW - 10}) is True, "an ENDED trial keeps watching"
    assert plans.pauses_when_idle(None) is True


def test_the_faq_the_brief_and_the_toast_say_free_only():
    from fastapi.testclient import TestClient
    from src.dashboard.aurora_html import DASHBOARD_HTML
    faq = api.LANDING_HTML
    assert "On Starter and Pro it keeps watching" in faq
    assert "On Free, monitoring pauses after 8 hours" in faq
    assert "Monitoring stops after 8 hours without you opening the dashboard, so a" not in faq
    brief = TestClient(api.app).get("/llms.txt").text
    assert "Starter and Pro keep watching while you are away;" in brief
    assert "monitoring stops after 8 hours" not in brief
    assert "Starter and Pro keep watching while you are away." in DASHBOARD_HTML
