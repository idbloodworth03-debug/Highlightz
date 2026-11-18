"""Pro goes ahead too.

Owner, 2026-11-18: "can we make pro get priority now too".

One order everywhere a line forms when the server is busy: Studio, then Pro,
then everybody else. What these hold:

  * `priority` ("front of the line") is still Studio's alone, so the thing
    Studio is sold on is still true of exactly Studio; `ahead` is Pro's and
    Studio's;
  * live slots, with a fixed limit: a Pro channel that is waiting gets the
    next slot that frees up, before any free account's channel;
  * live slots, with the measured limit: three sets of lines, so there is
    room for Pro when the server is full for Free, and for Studio when it is
    full for Pro;
  * clips are cut Studio's first, then Pro's, then the rest, and renders the
    same;
  * nobody already live is put out for anybody, and with nobody paying in
    the line a free account loses nothing.
"""

from __future__ import annotations

import asyncio
import pathlib
import time

import pytest

from src.billing import plans
from src.dashboard import api
from src.ingestion import headroom as hr

LEVELS = {"sd": 2, "pro": 1, "pro2": 1}


@pytest.fixture(autouse=True)
def _world(monkeypatch, tmp_path):
    hr.forget()
    monkeypatch.setattr(hr.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(hr, "_cores", lambda: 4)
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api.settings, "live_auto_base", 0)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 4)
    monkeypatch.setattr(plans, "uid_has_priority", lambda uid: LEVELS.get(uid) == 2)
    monkeypatch.setattr(plans, "uid_priority_level", lambda uid: LEVELS.get(uid, 0))
    yield
    hr.forget()


def _listed(key, status):
    uid, ch = key.split(":", 1)
    api._streams[key] = {"user_id": uid, "channel": ch, "status": status}


def _measured(loop, cpu=20.0, lag=0.0):
    hr.forget()
    now = time.monotonic()
    for i in (2, 1, 0):
        hr.record(loop, cpu, 12000.0, lag, 2.0, now - 2.0 * i, 16000.0)


# ── the plans ───────────────────────────────────────────────────────────────

def test_the_front_of_the_line_is_still_studios_alone():
    L = plans.PLAN_LIMITS
    assert [k for k, v in L.items() if v["priority"]] == ["studio"]
    assert [k for k, v in L.items() if v["ahead"]] == ["pro", "studio"]
    assert not (L["free"]["ahead"] or L["starter"]["ahead"] or L["locked"]["ahead"])


def test_the_place_in_the_line_follows_the_plan(monkeypatch):
    monkeypatch.undo()                                 # the real functions
    from src.auth import users as store
    people = {"s": {"id": "s", "subscription_status": "active", "plan": "studio"},
              "p": {"id": "p", "subscription_status": "active", "plan": "pro"},
              "t": {"id": "t", "subscription_status": "active", "plan": "starter"},
              "f": {"id": "f", "subscription_status": "none"}}
    monkeypatch.setattr(store, "get_by_id", lambda uid: people.get(uid))
    assert [plans.uid_priority_level(u) for u in "sptf"] == [2, 1, 0, 0]
    assert plans.uid_priority_level("") == 0
    assert plans.uid_priority_level("nobody") == 0


def test_a_lookup_that_fails_is_the_back_of_the_line(monkeypatch):
    monkeypatch.undo()
    from src.auth import users as store

    def boom(uid):
        raise RuntimeError("store unreadable")
    monkeypatch.setattr(store, "get_by_id", boom)
    assert plans.uid_priority_level("p") == 0


# ── live slots, fixed limit ─────────────────────────────────────────────────

def test_with_nobody_paying_in_the_line_a_free_account_loses_nothing():
    """Pro channels that are merely LISTED (offline, which is nearly all of
    them, nearly always) hold nothing back."""
    for i in range(6):
        _listed(f"pro:ch{i}", "offline")
    assert [api.acquire_live_slot(f"u{i}:c") for i in range(5)] == [True] * 4 + [False]


def test_a_waiting_pro_channel_gets_the_next_slot():
    for i in range(4):
        assert api.acquire_live_slot(f"u{i}:c")
    assert api.acquire_live_slot("pro:a") is False     # full: nobody is put out for it
    _listed("pro:a", "queued")
    api.release_live_slot("u0:c")                      # one streamer goes to bed
    assert api.acquire_live_slot("u9:new") is False, "a free channel took the slot Pro was waiting for"
    assert api.acquire_live_slot("pro:a") is True
    api._streams["pro:a"]["status"] = "live"
    api.release_live_slot("u1:c")
    assert api.acquire_live_slot("u9:new") is True     # nobody is waiting now


def test_two_waiting_pro_channels_hold_two(monkeypatch):
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 6)
    for i in range(6):
        assert api.acquire_live_slot(f"u{i}:c")
    _listed("pro:a", "queued")
    _listed("pro2:b", "queued")
    api.release_live_slot("u0:c")
    api.release_live_slot("u1:c")
    assert api.acquire_live_slot("u9:new") is False
    assert api.acquire_live_slot("pro:a") and api.acquire_live_slot("pro2:b")


def test_one_pro_channel_does_not_wait_behind_another():
    for i in range(3):
        assert api.acquire_live_slot(f"u{i}:c")
    _listed("pro:a", "queued")
    assert api.acquire_live_slot("pro2:b") is True     # the line is by plan, not by channel


def test_the_last_slot_is_never_held_from_everybody(monkeypatch):
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 1)
    _listed("pro:a", "queued")
    _listed("pro2:b", "queued")
    assert api.acquire_live_slot("u1:c") is True


def test_studio_is_still_ahead_of_pro():
    """Studio's two held-back slots are held from Pro as they are from
    everybody else."""
    _listed("sd:a", "starting")
    _listed("sd:b", "starting")
    assert api.acquire_live_slot("pro:x") and api.acquire_live_slot("u1:y")
    assert api.acquire_live_slot("pro:z") is False, "Pro took a slot held for Studio"
    assert api.acquire_live_slot("sd:a") and api.acquire_live_slot("sd:b")


def test_a_pro_channel_holding_a_slot_is_not_counted_as_waiting():
    for i in range(3):
        assert api.acquire_live_slot(f"u{i}:c")
    assert api.acquire_live_slot("pro:a")
    _listed("pro:a", "queued")                         # a stale label; it holds a slot
    api.release_live_slot("u0:c")
    assert api.acquire_live_slot("u9:new") is True


# ── live slots, measured limit ──────────────────────────────────────────────

def _auto(monkeypatch, n_live=40):
    monkeypatch.setattr(api.settings, "live_auto_base", 5)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 200)
    for i in range(n_live):
        api._live_slots.add(f"x{i}:c")


@pytest.mark.parametrize("loop, cpu, free, pro, studio", [
    (50.0, 20.0, True, True, True),
    (66.0, 20.0, False, True, True),        # full for Free, room for Pro
    (71.0, 20.0, False, False, True),       # full for Pro, room for Studio
    (76.0, 20.0, False, False, False),      # full
    (20.0, 76.0, False, True, True),        # the same three steps on the processors
    (20.0, 81.0, False, False, True),
    (20.0, 86.0, False, False, False),
])
def test_three_sets_of_lines(monkeypatch, loop, cpu, free, pro, studio):
    _auto(monkeypatch)
    got = []
    for uid in ("u", "pro", "sd"):
        _measured(loop, cpu)
        got.append(api.acquire_live_slot(f"{uid}:new"))
        api._live_slots.discard(f"{uid}:new")
    assert got == [free, pro, studio]


def test_the_lines_are_in_order_and_inside_the_alert():
    assert hr.LOOP_LINE < hr.LOOP_LINE_AHEAD < hr.LOOP_LINE_PRIORITY < hr.OVERLOAD_LINE
    assert hr.CPU_LINE < hr.CPU_LINE_AHEAD < hr.CPU_LINE_PRIORITY < 100
    # Pro's line is the one scripts/stream_cost.py plans to (70% of the loop).
    assert hr.LOOP_LINE_AHEAD == 70.0


def test_a_held_up_loop_stops_pro_as_it_stops_everybody_but_studio():
    _measured(20.0, 20.0, lag=hr.LAG_LINE_S + 0.2)
    assert hr.allow(40, priority=1)[0] is False
    assert hr.allow(40, priority=2)[0] is True


def test_a_free_channel_cannot_take_the_place_a_pro_channel_left(monkeypatch):
    """80 live at 68%: full for Free, room for Pro. A Pro channel's streamer
    goes offline. The next free channel is measured and waits; the next Pro
    channel gets in."""
    monkeypatch.setattr(api.settings, "live_auto_base", 5)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 200)
    for i in range(79):
        api._live_slots.add(f"x{i}:c")
    api._live_slots.add("pro:old")
    _measured(68.0, 40.0)
    hr.note_carried(80)
    api.release_live_slot("pro:old")
    assert api.acquire_live_slot("u:new") is False
    assert api.acquire_live_slot("pro2:new") is True


def test_under_the_base_everybody_gets_in(monkeypatch):
    monkeypatch.setattr(api.settings, "live_auto_base", 5)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 200)
    assert all(api.acquire_live_slot(f"u{i}:c") for i in range(5))


# ── clips and renders ───────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.lists = {}

    async def rpush(self, key, v):
        self.lists.setdefault(key, []).append(v)

    async def blpop(self, keys, timeout=0):
        for k in keys:
            if self.lists.get(k):
                return k, self.lists[k].pop(0)
        return None

    async def llen(self, key):
        return len(self.lists.get(key, []))


def test_clips_are_cut_studio_then_pro_then_the_rest_each_in_order():
    from src.queue import job_queue as jq

    async def go():
        q = jq.JobQueue(priority_of=lambda uid: LEVELS.get(uid, 0))
        q._redis = _FakeRedis()
        for uid, ch in (("a", "one"), ("pro", "two"), ("sd", "three"), ("b", "four"),
                        ("pro2", "five"), ("sd", "six")):
            await q.push(jq.ClipJob(channel=ch, platform="twitch", trigger_score=1.0,
                                    trigger_signals=[], chat_snapshot=[], user_id=uid))
        assert await q.queue_length() == 6
        assert len(q._redis.lists[jq.AHEAD_KEY]) == 2
        return [(await q.pop()).channel for _ in range(6)]
    assert asyncio.run(go()) == ["three", "six", "two", "five", "one", "four"]


def test_a_clip_is_never_lost_to_a_lookup_that_fails():
    from src.queue import job_queue as jq

    def boom(uid):
        raise RuntimeError("store unreadable")

    async def go():
        q = jq.JobQueue(priority_of=boom)
        q._redis = _FakeRedis()
        await q.push(jq.ClipJob(channel="one", platform="twitch", trigger_score=1.0,
                                trigger_signals=[], chat_snapshot=[], user_id="pro"))
        return (await q.pop()).channel
    assert asyncio.run(go()) == "one"


def test_renders_go_studio_then_pro_then_the_rest():
    from src.autopilot import render

    async def go():
        slot = render._PrioritySlot()
        order = []
        await slot.acquire()

        async def wait(name, level):
            async with slot.hold(level):
                order.append(name)
        tasks = []
        for name, level in (("free", 0), ("pro", 1), ("free2", False), ("studio", 2),
                            ("pro2", 1), ("studio-old-style", True)):
            tasks.append(asyncio.create_task(wait(name, level)))
            await asyncio.sleep(0)
        slot.release()
        await asyncio.gather(*tasks)
        return order, slot._busy
    order, busy = asyncio.run(go())
    assert order == ["studio", "studio-old-style", "pro", "pro2", "free", "free2"] and busy is False


def test_every_line_is_given_the_place_not_a_yes_or_no():
    """The three callers pass uid_priority_level. Passing the old yes/no
    would put Pro at the back of exactly the lines this is about."""
    for path, needle in (("src/main.py", "JobQueue(priority_of=uid_priority_level)"),
                         ("src/autopilot/runner.py", "priority=uid_priority_level(uid))"),
                         ("src/autopilot/auto_edit.py", "priority=uid_priority_level(str(clip.get(\"user_id\") or \"\")))"),
                         ("src/dashboard/api.py", "priority = uid_priority_level(uid)")):
        assert needle in pathlib.Path(path).read_text(), path


# ── what the site says ──────────────────────────────────────────────────────

def test_the_account_is_told():
    src = pathlib.Path("src/dashboard/api.py").read_text()
    assert '"ahead": bool(limits.get("ahead")),' in src


def test_a_waiting_free_channel_is_told_who_goes_first():
    src = pathlib.Path("src/dashboard/aurora_html.py").read_text()
    assert "(ahead?'':' Channels on Pro and Studio go first.')" in src
    assert "ahead={!!(me && me.plan_limits && me.plan_limits.ahead)}" in src


def test_the_plan_tables_read_across():
    from src.dashboard import tutorial_content
    src = pathlib.Path("src/dashboard/api.py").read_text()
    assert "Ahead of Free accounts when the server is busy | {yn(f, 'ahead')} | {yn(pro, 'ahead')} | {yn(sd, 'ahead')} |" in src
    assert '("Ahead of Free when busy", "Yes" if limits.get("ahead") else "No"),' in src
    tsrc = pathlib.Path(tutorial_content.__file__).read_text()
    assert '("Ahead of Free when busy", yn(free, "ahead"), yn(pro, "ahead"),' in tsrc
