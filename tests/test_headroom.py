"""The live limit that follows what the server measures of itself.

Owner, 2026-11-18: "I want to maximize live monitored streams as much as
possible without breaking the server."

What these hold (src/ingestion/headroom.py and api.acquire_live_slot):

  * with LIVE_AUTO_BASE unset, nothing changes: the limit is the fixed number;
  * it can only say NO: never past the hard maximum, never under the base;
  * with nothing measured, it stops at the base;
  * channels that just went live count as if they already cost a full share,
    and only a few may be in that state at once;
  * a restart, or everybody reconnecting, is not new load: what the box was
    measured carrying well is let straight back in;
  * a Studio account is held to wider lines;
  * the task that takes the readings can never take the product down.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time

import pytest

from src.dashboard import api
from src.ingestion import headroom as hr


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    hr.forget()
    monkeypatch.setattr(hr.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(hr, "_cores", lambda: 4)
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr("src.billing.plans.uid_has_priority", lambda uid: uid == "studio")
    monkeypatch.setattr("src.billing.plans.uid_priority_level",
                        lambda uid: {"studio": 2, "pro": 1}.get(uid, 0))
    yield
    hr.forget()


def _measured(loop=20.0, cpu=20.0, mem_mb=12000.0, lag=0.0, now=1000.0, total=16000.0):
    """Three readings ending at `now`, so the figures count as measured."""
    hr.forget()
    for i in (2, 1, 0):
        hr.record(loop, cpu, mem_mb, lag, 2.0, now - 2.0 * i, total)


def _auto(monkeypatch, base=10, cap=100):
    monkeypatch.setattr(api.settings, "live_auto_base", base)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", cap)


def _fill(n, uid="u"):
    for i in range(n):
        api._live_slots.add(f"{uid}:c{i}")


# ── off by default ──────────────────────────────────────────────────────────

def test_it_is_off_until_a_base_is_set():
    from config.settings import Settings
    assert Settings.model_fields["live_auto_base"].default == 0
    assert hr.enabled() is False


def test_off_means_the_fixed_number_and_nothing_else(monkeypatch):
    """Nothing measured, the lines far exceeded: with LIVE_AUTO_BASE unset the
    gate is never asked, so the limit is exactly MAX_CONCURRENT_STREAMS."""
    monkeypatch.setattr(api.settings, "live_auto_base", 0)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 5)
    monkeypatch.setattr(hr, "allow", lambda *a, **k: pytest.fail("asked while off"))
    assert [api.acquire_live_slot(f"u:c{i}") for i in range(6)] == [True] * 5 + [False]


# ── the gate's arithmetic ───────────────────────────────────────────────────

def test_room_is_measured_room():
    _measured(loop=40.0, cpu=30.0)
    ok, why = hr.allow(40, now=1000.0)
    assert ok and why == ""


def test_nothing_measured_means_no():
    ok, why = hr.allow(40, now=1000.0)
    assert not ok and "not been measured" in why


def test_readings_that_stopped_arriving_mean_no():
    _measured(now=1000.0)
    assert hr.allow(40, now=1000.0 + hr.STALE_S - 1)[0]
    ok, why = hr.allow(40, now=1000.0 + hr.STALE_S + 1)
    assert not ok and "not been measured" in why


def test_the_loop_line():
    """40 live at 64%: one more is 1.6% each, which crosses 65."""
    _measured(loop=64.0)
    ok, why = hr.allow(40, now=1000.0)
    assert not ok and "main thread" in why and "64%" in why


def test_the_processor_line():
    _measured(loop=20.0, cpu=74.5)
    ok, why = hr.allow(40, now=1000.0)
    assert not ok and "processors" in why


def test_the_memory_line_keeps_a_tenth_of_the_box_back():
    """16 GB box: 1.6 GB is kept. 1.7 GB free leaves no room for a 170 MB
    channel; and on a small box the reserve is never under 1.5 GB."""
    _measured(mem_mb=1700.0, total=16000.0)
    ok, why = hr.allow(40, now=1000.0)
    assert not ok and "memory" in why
    _measured(mem_mb=1700.0, total=4000.0)
    assert hr._reserve_mb() == hr.MEM_RESERVE_MB
    assert hr.allow(40, now=1000.0)[0] is False        # 1700 - 170 < 1536
    _measured(mem_mb=1800.0, total=4000.0)
    assert hr.allow(40, now=1000.0)[0] is True


def test_a_held_up_loop_means_no_except_for_studio():
    _measured(lag=hr.LAG_LINE_S + 0.2)
    ok, why = hr.allow(40, now=1000.0)
    assert not ok and "held up" in why
    assert hr.allow(40, priority=True, now=1000.0)[0]


def test_one_more_channel_never_costs_less_than_the_floor():
    """Three channels on an idle box: the average says 0.1% each. The floor
    says 1%, so 64.5% of the loop is full whatever the average claims."""
    _measured(loop=64.5, cpu=1.0)
    assert hr._per_channel(900, 0)[0] == hr.PER_LOOP_FLOOR
    assert hr.allow(900, now=1000.0)[0] is False


def test_studio_is_held_to_wider_lines():
    _measured(loop=66.0, cpu=76.0)
    assert hr.allow(60, now=1000.0)[0] is False
    assert hr.allow(60, priority=True, now=1000.0)[0] is True
    _measured(loop=75.0)
    assert hr.allow(60, priority=True, now=1000.0)[0] is False


# ── channels whose cost has not arrived ─────────────────────────────────────

def test_a_channel_just_let_in_counts_as_a_full_share():
    """40 settled at 60%: 1.5% each. Room for three more (64.5), not a fourth:
    the readings still say 60, and saying yes to each on its own would not
    stop."""
    _measured(loop=60.0)
    said = []
    for i in range(5):
        ok, _ = hr.allow(40 + i, now=1000.0)
        said.append(ok)
        if ok:
            hr.note_admitted(now=1000.0)
    assert said == [True, True, True, False, False]


def test_only_a_few_unmeasured_channels_at_once():
    _measured(loop=5.0, cpu=5.0)
    n = 0
    while hr.allow(40 + n, now=1000.0)[0]:
        hr.note_admitted(f"u:new{n}", 1000.0)
        n += 1
        assert n < 50
    assert n == hr.STEP
    assert "still being measured" in hr.allow(40 + n, now=1000.0)[1]


def test_they_stop_counting_once_the_readings_have_caught_up():
    _measured(loop=5.0, cpu=5.0, now=1000.0)
    for _ in range(hr.STEP):
        hr.note_admitted(now=1000.0)
    later = 1000.0 + hr.PENDING_S + 1
    for i in (2, 1, 0):
        hr.record(6.0, 6.0, 12000.0, 0.0, 2.0, later - 2.0 * i, 16000.0)
    assert hr.allow(46, now=later)[0] is True


# ── the slot gate ───────────────────────────────────────────────────────────

def test_up_to_the_base_nobody_is_asked(monkeypatch):
    """Nothing measured at all: the base is let in exactly as before, and the
    next one waits."""
    _auto(monkeypatch, base=5, cap=50)
    assert [api.acquire_live_slot(f"u:c{i}") for i in range(7)] == [True] * 5 + [False, False]
    assert api.live_stream_count() == 5


def test_past_the_base_it_is_the_measurement(monkeypatch):
    _auto(monkeypatch, base=5, cap=50)
    _fill(40)
    _measured(loop=30.0, now=time.monotonic())
    assert api.acquire_live_slot("u:new") is True
    _measured(loop=64.5, now=time.monotonic())
    assert api.acquire_live_slot("u:another") is False
    assert "u:another" not in api._live_slots


def test_never_past_the_hard_maximum(monkeypatch):
    """An idle box with all the room in the world still stops at the number in
    MAX_CONCURRENT_STREAMS."""
    _auto(monkeypatch, base=5, cap=12)
    _fill(12)
    _measured(loop=1.0, cpu=1.0, now=time.monotonic())
    assert api.acquire_live_slot("u:new") is False


def test_a_channel_already_holding_a_slot_is_not_asked_again(monkeypatch):
    _auto(monkeypatch, base=1, cap=50)
    _fill(20)
    api._live_slots.add("u:mine")
    assert api.acquire_live_slot("u:mine") is True        # nothing measured, still yes


def test_studio_gets_in_where_everyone_else_waits(monkeypatch):
    _auto(monkeypatch, base=5, cap=50)
    _fill(40)
    _measured(loop=66.0, now=time.monotonic())
    assert api.acquire_live_slot("u:new") is False
    assert api.acquire_live_slot("studio:new") is True


def test_every_admission_is_counted_under_the_base_too(monkeypatch):
    """After a restart the base goes live in one second. The next decision has
    to know that none of them has shown its cost yet."""
    _auto(monkeypatch, base=8, cap=50)
    _measured(loop=5.0, cpu=5.0, now=time.monotonic())
    assert all(api.acquire_live_slot(f"u:c{i}") for i in range(8))
    assert api.acquire_live_slot("u:ninth") is False
    assert "still being measured" in hr.snapshot(8)["full_because"]


def test_the_refusal_says_what_ran_out(monkeypatch):
    seen = []
    monkeypatch.setattr(api.log, "warning", lambda ev, **kw: seen.append((ev, kw)))
    _auto(monkeypatch, base=5, cap=50)
    _fill(40)
    _measured(loop=64.5, now=time.monotonic())
    api.acquire_live_slot("u:new")
    ev, kw = seen[-1]
    assert ev == "live_slots_full" and "main thread" in kw["measured"] and kw["cap"] == 50


# ── a restart is not new load ───────────────────────────────────────────────

def test_what_was_carried_well_is_let_straight_back_in(monkeypatch):
    """80 live inside the lines, noted. Every feed drops and asks again
    (Twitch hiccuping): all 80 are back at once, and an 81st, which was not
    running, is asked."""
    _auto(monkeypatch, base=10, cap=200)
    t = time.monotonic()
    _fill(80)
    _measured(loop=50.0, cpu=40.0, now=t)
    assert hr.note_carried(80, t) == 80
    for i in range(80):
        api.release_live_slot(f"u:c{i}")
    assert api.live_stream_count() == 0
    assert hr.floor("u:c5") == 80 and hr.floor("u:c80") == 10
    assert all(api.acquire_live_slot(f"u:c{i}") for i in range(80))
    assert api.acquire_live_slot("u:c80") is False


def test_a_new_channel_cannot_walk_into_a_place_somebody_left(monkeypatch):
    """80 live at 68%: over the line for a new channel. One leaves for good.
    The place it held is for a channel that was running, not for whoever
    asks next: the newcomer is measured, and waits."""
    _auto(monkeypatch, base=10, cap=200)
    t = time.monotonic()
    _fill(80)
    _measured(loop=68.0, cpu=40.0, now=t)
    hr.note_carried(80, t)
    api.release_live_slot("u:c3")
    assert api.live_stream_count() == 79
    assert api.acquire_live_slot("u:brand-new") is False


def test_a_feed_that_drops_for_a_moment_does_not_lose_its_place(monkeypatch):
    """80 live at 68% of the loop: two points past the line for NEW channels,
    so nobody new gets in. One channel's feed drops and it asks again half a
    minute later. It was running a moment ago; it is let back in."""
    _auto(monkeypatch, base=10, cap=200)
    t = time.monotonic()
    _fill(80)
    _measured(loop=68.0, cpu=40.0, now=t)
    hr.note_carried(80, t)
    assert api.acquire_live_slot("u:brand-new") is False
    api.release_live_slot("u:c7")
    assert api.live_stream_count() == 79
    assert api.acquire_live_slot("u:c7") is True
    assert api.acquire_live_slot("u:brand-new") is False


def test_reconnecting_channels_do_not_hold_up_the_climb(monkeypatch):
    """Ten channels drop and come straight back. Their cost still counts as
    not-yet-measured in the arithmetic, but they are not NEW, so a new channel
    is not told "still being measured" on their account."""
    _auto(monkeypatch, base=10, cap=200)
    t = time.monotonic()
    _fill(60)
    _measured(loop=30.0, cpu=30.0, now=t)
    hr.note_carried(60, t)
    for i in range(10):
        api.release_live_slot(f"u:c{i}")
    assert all(api.acquire_live_slot(f"u:c{i}") for i in range(10))
    assert hr._pending(time.monotonic()) == 10 and hr._pending_new(time.monotonic()) == 0
    assert api.acquire_live_slot("u:brand-new") is True
    assert hr._pending_new(time.monotonic()) == 1


def test_only_a_slot_that_was_held_counts_as_given_back():
    """Every waiting channel calls release every 30 seconds without ever
    having held a slot. That must not make it "reconnecting" when it does go
    live, and must not grow a table."""
    api.release_live_slot("u:never-live")
    assert "u:never-live" not in hr._released
    hr.note_released("u:gone", 1000.0)
    hr.note_admitted("u:gone", 1000.0 + hr.RETURNING_S + 5)       # came back much later: new
    assert hr._pending_new(1000.0 + hr.RETURNING_S + 5) == 1
    assert "u:gone" not in hr._released


def test_the_note_goes_stale(monkeypatch):
    _auto(monkeypatch, base=10, cap=200)
    _measured(loop=50.0, now=1000.0)
    hr.note_carried(80, 1000.0)
    hr.note_released("u:x", 1000.0)
    assert hr.floor("u:x", 1000.0 + hr.CARRY_FOR_S - 1) == 80
    assert hr.floor("u:x", 1000.0 + hr.CARRY_FOR_S + 1) == 10


def test_an_overload_is_never_carried(monkeypatch):
    """80 live at 90% of the loop is past even the wider line: what goes back
    in after a restart is what would sit ON that line, not what was there."""
    _auto(monkeypatch, base=10, cap=200)
    _measured(loop=90.0, cpu=30.0, now=1000.0)
    assert hr.carryable(80, 1000.0) == int(80 * 75.0 / 90.0) == 66
    _measured(loop=30.0, cpu=95.0, now=1000.0)
    assert hr.carryable(80, 1000.0) == int(80 * 85.0 / 95.0) == 71
    _measured(loop=30.0, cpu=30.0, mem_mb=900.0, now=1000.0)
    assert hr.carryable(80, 1000.0) == 0              # short of memory: note nothing


def test_unmeasured_channels_are_not_carried():
    _measured(loop=30.0, now=1000.0)
    for _ in range(5):
        hr.note_admitted(now=1000.0)
    assert hr.carryable(45, 1000.0) == 40
    hr.forget()
    assert hr.carryable(45, 1000.0) == 0              # nothing measured


def test_a_restart_reads_the_note_back(monkeypatch, tmp_path):
    _auto(monkeypatch, base=10, cap=200)
    hr._write_carry(72, ["u:b", "u:a"])
    note = json.loads((tmp_path / hr.CARRY_FILE).read_text())
    assert note["live"] == 72 and note["keys"] == ["u:a", "u:b"]
    hr.forget()                                       # the new process knows nothing
    assert hr.load_carry(500.0) == 72
    assert hr.floor("u:a", 500.0) == 72               # it was running: straight back in
    assert hr.floor("u:stranger", 500.0) == 10        # it was not: measured like anybody
    assert hr.floor("u:a", 500.0 + hr.CARRY_FOR_S + 1) == 10


def test_a_note_from_before_it_listed_channels_still_counts(monkeypatch, tmp_path):
    """The first version wrote only the number. Read once, on the deploy that
    replaces it: everybody asking in the first minutes is taken as returning,
    which is what that version did for every restart."""
    _auto(monkeypatch, base=10, cap=200)
    (tmp_path / hr.CARRY_FILE).write_text(json.dumps({"live": 60, "at": time.time()}))
    assert hr.load_carry(500.0) == 60
    assert hr.floor("u:anyone", 500.0) == 60
    assert hr.floor("u:anyone", 500.0 + hr.CARRY_FOR_S + 1) == 10


@pytest.mark.parametrize("content", [
    "", "not json", "[]", '{"live": "many", "at": 1}', '{"live": 50}',
    '{"live": -4, "at": %d}' % int(time.time()),
    '{"live": 50, "at": %d}' % int(time.time() - 3600),          # an hour old
    '{"live": 50, "at": %d}' % int(time.time() + 3600),          # from the future
])
def test_a_bad_or_old_note_carries_nothing(monkeypatch, tmp_path, content):
    _auto(monkeypatch, base=10, cap=200)
    (tmp_path / hr.CARRY_FILE).write_text(content)
    assert hr.load_carry(500.0) == 0
    assert hr.floor("u:c1", 500.0) == 10


def test_no_note_at_all_carries_nothing(monkeypatch):
    _auto(monkeypatch, base=10, cap=200)
    assert hr.load_carry(500.0) == 0 and hr.floor("u:c1", 500.0) == 10


# ── the readings themselves ─────────────────────────────────────────────────

def test_the_readings_are_averages_not_the_last_blip():
    _measured(loop=20.0, now=1000.0)
    hr.record(100.0, 20.0, 12000.0, 0.0, 2.0, 1002.0, 16000.0)
    assert 20.0 < hr._state["loop"] < 35.0            # one loud reading moves it a little
    for i in range(1, 40):
        hr.record(100.0, 20.0, 12000.0, 0.0, 2.0, 1002.0 + 2.0 * i, 16000.0)
    assert hr._state["loop"] > 95.0                   # and a minute of them moves it all the way


def test_it_reads_this_machine():
    busy, total = hr._box_cpu()
    assert 0 <= busy <= total and total > 0
    free, whole = hr._mem_mb()
    assert 0 < free <= whole
    assert hr._thread_cpu() >= 0


def test_the_task_measures_a_busy_loop():
    """A loop kept half busy for a second and a half reads as busy, not idle
    (the trap scripts/loop_profile.py fell into first)."""
    async def go():
        task = asyncio.create_task(hr.run(lambda: [], every=0.1))
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            t = time.perf_counter()
            while time.perf_counter() - t < 0.02:
                pass
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert hr._state["samples"] >= 3
    assert hr._state["loop"] > 20.0
    assert hr._state["mem_mb"] > 0


def test_the_task_never_raises(monkeypatch):
    """main() gathers it: an exception out of it stops the whole product. A
    machine that cannot be read is logged, and the answer past the base is no."""
    def boom():
        raise OSError("no /proc here")
    monkeypatch.setattr(hr, "_box_cpu", boom)
    monkeypatch.setattr(hr, "_mem_mb", boom)
    slept = []
    real_sleep = asyncio.sleep

    async def quick(s):
        slept.append(s)
        await real_sleep(0)

    monkeypatch.setattr(hr.asyncio, "sleep", quick)

    async def go():
        task = asyncio.create_task(hr.run(lambda: [], every=0.01))
        for _ in range(50):
            await real_sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert hr._state["samples"] == 0
    assert hr.allow(40)[0] is False


def test_the_task_writes_the_note_a_restart_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(hr.settings, "live_auto_base", 10)
    monkeypatch.setattr(hr, "CARRY_EVERY_S", 0.2)

    async def go():
        task = asyncio.create_task(hr.run(lambda: [f"u:c{i}" for i in range(33)], every=0.05))
        for _ in range(60):
            await asyncio.sleep(0.05)
            if (tmp_path / hr.CARRY_FILE).exists():
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    note = json.loads((tmp_path / hr.CARRY_FILE).read_text())
    assert note["live"] == 33 and len(note["keys"]) == 33 and "u:c7" in note["keys"]


def test_off_means_no_note_is_written(monkeypatch, tmp_path):
    monkeypatch.setattr(hr.settings, "live_auto_base", 0)
    monkeypatch.setattr(hr, "CARRY_EVERY_S", 0.05)

    async def go():
        task = asyncio.create_task(hr.run(lambda: ["u:c1"], every=0.02))
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert hr._state["samples"] >= 3                   # still measuring, for the admin page
    assert not (tmp_path / hr.CARRY_FILE).exists()


# ── the owner is told when it stays overloaded ──────────────────────────────

def test_a_loop_that_stays_overloaded_emails_the_owner_once(monkeypatch):
    said = []
    monkeypatch.setattr(hr.log, "error", lambda ev, **kw: said.append((ev, kw)))
    _measured(loop=92.0, now=1000.0)
    hr._watch_overload(1000.0)
    hr._watch_overload(1000.0 + hr.OVERLOAD_AFTER_S - 1)
    assert said == []                                  # a spike is not an overload
    hr._watch_overload(1000.0 + hr.OVERLOAD_AFTER_S + 1)
    hr._watch_overload(1000.0 + hr.OVERLOAD_AFTER_S + 60)
    assert [ev for ev, _ in said] == ["app_loop_overloaded"]
    assert "MAX_CONCURRENT_STREAMS" in said[0][1]["fix"]
    _measured(loop=30.0, now=2000.0)
    hr._watch_overload(2000.0)
    assert hr._over["since"] is None                   # it recovered: the clock starts again


# ── what the admin page is told ─────────────────────────────────────────────

def test_the_admin_figures(monkeypatch):
    _auto(monkeypatch, base=10, cap=100)
    _fill(40)
    _measured(loop=45.0, cpu=30.0, mem_mb=12000.0, now=time.monotonic())
    got = api._headroom_snapshot()
    assert got["on"] is True and got["base"] == 10 and got["measured"] is True
    assert got["loop_pct"] == 45.0 and got["cpu_pct"] == 30.0 and got["mem_free_gb"] == 11.7
    # 20 points of loop at 1.125 each is 17; the processors and memory allow more.
    assert got["room"] == 17
    assert got["full_because"] == ""


def test_room_never_promises_past_the_hard_maximum(monkeypatch):
    _auto(monkeypatch, base=10, cap=42)
    _fill(40)
    _measured(loop=10.0, cpu=10.0, now=time.monotonic())
    assert api._headroom_snapshot()["room"] == 2


def test_with_nothing_measured_the_room_is_what_is_left_of_the_base(monkeypatch):
    _auto(monkeypatch, base=10, cap=100)
    _fill(4)
    got = api._headroom_snapshot()
    assert got["measured"] is False and got["room"] == 6


def test_the_overview_carries_them():
    src = pathlib.Path("src/dashboard/api.py").read_text()
    assert '"headroom": _headroom_snapshot(),' in src
    assert '"queued": sum(1 for s in _streams.values() if s.get("status") == "queued"),' in src
    # ...and the admin page reads every field it shows.
    for field in ("hr.on", "hr.measured", "hr.room", "hr.full_because", "hr.loop_pct",
                  "hr.cpu_pct", "hr.mem_free_gb", "hr.base", "s.queued"):
        assert field in src, field


def test_main_runs_the_meter():
    src = pathlib.Path("src/main.py").read_text()
    assert "asyncio.create_task(_headroom.run(" in src
    assert "lambda: list(dashboard_api._live_slots)," in src
    # With the split, the same task watches each worker's loop for the alarm.
    assert "loops=ingest.loop_readings if ingest is not None else None)," in src


# ── the listed ceiling and Twitch's budget ──────────────────────────────────

def test_the_listed_ceiling_stops_where_twitch_would(monkeypatch):
    """A hard maximum of 120 live would derive 480 listed, and 480 channels
    each asking Helix "is it live?" twice a minute is past the 800 a minute
    the whole app gets."""
    from config.settings import Settings, _MAX_DERIVED_REGISTERED
    monkeypatch.delenv("MAX_REGISTERED_STREAMS", raising=False)
    monkeypatch.setenv("MAX_CONCURRENT_STREAMS", "120")
    assert Settings().max_registered_streams == _MAX_DERIVED_REGISTERED == 300
    assert 2 * _MAX_DERIVED_REGISTERED <= 600           # everybody offline, 200 a minute to spare
    monkeypatch.setenv("MAX_CONCURRENT_STREAMS", "60")
    assert Settings().max_registered_streams == 240     # under it, x4 as it always was
    monkeypatch.setenv("MAX_REGISTERED_STREAMS", "500")
    assert Settings().max_registered_streams == 500     # a typed number still wins


def test_the_listed_ceiling_is_never_under_the_live_one(monkeypatch):
    from config.settings import Settings
    monkeypatch.delenv("MAX_REGISTERED_STREAMS", raising=False)
    monkeypatch.setenv("MAX_CONCURRENT_STREAMS", "350")
    assert Settings().max_registered_streams == 350


def test_the_base_is_read_from_env(monkeypatch):
    from config.settings import Settings, capacity_summary
    monkeypatch.setenv("LIVE_AUTO_BASE", "44")
    monkeypatch.setenv("MAX_CONCURRENT_STREAMS", "120")
    s = Settings()
    assert s.live_auto_base == 44
    assert capacity_summary(s)["live_auto_base"] == 44 and capacity_summary(s)["live"] == 120


# ── scripts/stream_cost.py does not argue with it ───────────────────────────

def test_the_cost_script_does_not_tell_the_owner_to_pin_a_number():
    import importlib.util
    spec = importlib.util.spec_from_file_location("stream_cost", "scripts/stream_cost.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    said = " ".join(mod.verdict(live=50, cap=120, safe=90, production=True, auto_base=44))
    assert "SETS ITS OWN LIVE LIMIT" in said and "Nothing to change" in said
    assert "Pin MAX_CONCURRENT_STREAMS" not in said
    low = " ".join(mod.verdict(live=50, cap=120, safe=38, production=True, auto_base=44))
    assert "LIVE_AUTO_BASE=38" in low
    more = " ".join(mod.verdict(live=50, cap=120, safe=150, production=True, auto_base=44))
    assert "raise" in more and "MAX_CONCURRENT_STREAMS" in more
    # ...and without the base it says exactly what it always said.
    old = " ".join(mod.verdict(live=50, cap=44, safe=60, production=True))
    assert "RAISING IT LOOKS SUPPORTED" in old
