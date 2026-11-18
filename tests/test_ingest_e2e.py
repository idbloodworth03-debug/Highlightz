"""THE SPLIT, stage 2: channels watched in worker processes, end to end.

docs/HANDOFF.md, "THE SPLIT". The web side here is the REAL dashboard module
(its `_streams`, its live-slot gate, its `watchers_of`), the workers are REAL
processes started by the real Pool, and everything between them is the real
wire: src/dashboard/ingest.py <-> bridge.Link <-> worker_main / remote / host
/ workers.spawn_worker. The one stand-in is the stream worker itself, which
needs Twitch or Kick: a scripted one makes every call a real one makes
through host, in the order a real one makes them.

Held here:
  * a worker is sent its own channels and nobody else's, and never a paused one;
  * what it reports reaches the real record and the user's tabs;
  * a live slot is granted by the web process's gate, and refused when full;
  * a paused channel stays paused whatever its worker says on the way out;
  * a worker that dies gives its slots back, its channels say so, and they
    come back when its replacement is up, with the other worker untouched;
  * a worker speaks only for its own channels;
  * a worker's error reaches the owner's alert tally.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time

import pytest

from src.dashboard import api, ingest as ingest_mod
from src.ingestion import pool as pool_mod
from src.ingestion.bridge import owner_of

REPO = os.getcwd()

FAKE_WORKER = '''
import asyncio, sys
sys.path.insert(0, %(repo)r)
from src.ingestion import host, worker_main, workers


class Scripted:
    """Every call a real StreamWorker makes through host, in its order."""
    def __init__(self, config, platform, queue, shared_buffers, on_score):
        self.c, self.on_score, self._stop = config, on_score, asyncio.Event()

    async def start(self):
        c = self.c
        key = c.user_id + ":" + c.channel
        try:
            host.set_status(key, "live")
            await host.broadcast({"event": "stream_status", "channel": c.channel, "status": "live"},
                                 user_id=c.user_id)
            if not await host.acquire_live_slot(key):
                host.set_status(key, "queued")
                await host.broadcast({"event": "stream_status", "channel": c.channel, "status": "queued"},
                                     user_id=c.user_id)
                await self._stop.wait()
                return
            await self.on_score(c.channel, 42.0, {"CHAT_VELOCITY": 0.5})
            who = await host.watchers_of(c.channel)
            used, room = await host.suggestion_room(c.user_id)
            await host.broadcast({"event": "test_asked", "channel": c.channel, "watchers": who,
                                  "room": [used, room], "preset": host.streams()[key]["preset"]},
                                 user_id=c.user_id)
            if c.channel.startswith("clip"):
                await host.notify_clip_ready({"id": "id-" + c.channel, "user_id": c.user_id,
                                              "channel": c.channel})
                await host.on_clip_file_ready("id-" + c.channel)
            if c.channel.startswith("boom"):
                import structlog
                structlog.get_logger("scripted").error("worker_blew_up", channel=c.channel, n=3)
            await self._stop.wait()
        except asyncio.CancelledError:
            return
        finally:
            host.release_live_slot(key)

    def stop(self):
        self._stop.set()


workers.StreamWorker = Scripted
sys.exit(worker_main.main())
'''


def _names(prefix: str, index: int, n: int, count: int = 2) -> list[str]:
    """`n` channel names that belong to worker `index` of `count`."""
    out, i = [], 0
    while len(out) < n:
        name = f"{prefix}{i}"
        if owner_of(name, count) == index:
            out.append(name)
        i += 1
    return out


async def _until(cond, timeout=20.0, every=0.03):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(every)
    return False


@pytest.fixture
def web(tmp_path, monkeypatch):
    """The dashboard's state for one test, and a place to start workers from."""
    (tmp_path / "fakeingest.py").write_text(FAKE_WORKER % {"repo": REPO})
    monkeypatch.setattr(pool_mod, "_ROOT", tmp_path)
    monkeypatch.setattr(pool_mod, "BACKOFF_S", (0.05,) * 5)
    sent, delivered, noted = [], [], []

    async def bcast(event, user_id=None):
        sent.append((event.get("event"), user_id, event))

    async def clip_ready(meta):
        delivered.append(("ready", meta["id"]))

    async def file_ready(clip_id):
        delivered.append(("file", clip_id))

    from src import alerts
    from src.auth import users as user_store
    monkeypatch.setattr(alerts, "note", lambda event, level, fields=None, now=None: noted.append((event, level, fields)))
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {"id": uid} if uid else None)
    monkeypatch.setattr("src.billing.plans.uid_priority_level", lambda uid: 0)
    monkeypatch.setattr("src.billing.plans.uid_has_priority", lambda uid: False)
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "notify_clip_ready", clip_ready)
    monkeypatch.setattr(api, "on_clip_file_ready", file_ready)
    monkeypatch.setattr(api, "suggestion_room", lambda uid: (2, 5))
    monkeypatch.setattr(api.settings, "live_auto_base", 0)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 50)

    def add(channel, uid="u1", status="offline", preset="default"):
        api._streams[f"{uid}:{channel}"] = {"channel": channel, "platform": "twitch", "preset": preset,
                                            "status": status, "user_id": uid, "added_at": 1.0}

    def events(kind, channel=None):
        return [e for k, _u, e in sent if k == kind
                and (channel is None or e.get("channel") == channel
                     or (e.get("stream") or {}).get("channel") == channel)]

    return type("Web", (), {"add": staticmethod(add), "sent": sent, "delivered": delivered,
                            "noted": noted, "events": staticmethod(events)})


def _run(web, body, count=2):
    async def go():
        ing = ingest_mod.Ingest(count, module="fakeingest")
        await ing.start()
        try:
            return await body(ing)
        finally:
            await ing.stop()
    return asyncio.run(go())


# ── the whole path ───────────────────────────────────────────────────────────

def test_each_worker_watches_its_own_channels_and_the_dashboard_hears_everything(web):
    a, b = _names("alpha", 0, 2), _names("beta", 1, 2)
    for ch in a + b:
        web.add(ch, preset="fps")
    web.add(_names("gone", 0, 1)[0], status="paused")
    paused = _names("gone", 0, 1)[0]
    web.add(a[0], uid="u2")                              # a second person on the same channel

    async def body(ing):
        keys = [f"u1:{ch}" for ch in a + b] + [f"u2:{a[0]}"]
        assert await _until(lambda: all(api._streams[k]["status"] == "live" for k in keys)), \
            {k: api._streams[k]["status"] for k in keys}
        assert await _until(lambda: len(web.events("test_asked")) == 5)
        probes = [await w.link.call("probe") for w in ing.pool.workers]
        return probes, set(api._live_slots), ing.status()
    probes, slots, status = _run(web, body)

    # Its own channels, nobody else's, and not the paused one.
    assert probes[0]["watching"] == sorted([f"u1:{ch}" for ch in a] + [f"u2:{a[0]}"])
    assert probes[1]["watching"] == sorted(f"u1:{ch}" for ch in b)
    assert all(p["remote"] is True and p["dashboard_loaded"] is False for p in probes)
    assert api._streams[f"u1:{paused}"]["status"] == "paused" and not web.events("stream_status", paused)
    # The slots are the web process's, granted by its gate.
    assert slots == {f"u1:{ch}" for ch in a + b} | {f"u2:{a[0]}"}
    # Reports reached the right person's tabs.
    live = [(u, e["channel"]) for k, u, e in web.sent if k == "stream_status" and e["status"] == "live"]
    assert sorted(live) == sorted([("u1", ch) for ch in a + b] + [("u2", a[0])])
    score = web.events("score_update", b[0])[0]
    assert score["score"] == 42.0 and score["breakdown"] == {"CHAT_VELOCITY": 0.5}
    # Questions were answered from the dashboard's own data.
    asked = web.events("test_asked", a[0])
    assert sorted(asked[0]["watchers"]) == ["u1", "u2"] and asked[0]["room"] == [2, 5]
    assert asked[0]["preset"] == "fps"                   # the worker's mirror of its record
    assert [w["up"] for w in status] == [True, True] and [w["restarts"] for w in status] == [0, 0]


def test_a_full_box_refuses_the_slot_and_the_channel_waits(web, monkeypatch):
    chans = _names("solo", 0, 3, count=1)
    for ch in chans:
        web.add(ch)
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 2)

    async def body(ing):
        def settled():
            st = sorted(api._streams[f"u1:{c}"]["status"] for c in chans)
            return st == ["live", "live", "queued"]
        assert await _until(settled), [api._streams[f"u1:{c}"]["status"] for c in chans]
        return len(api._live_slots)
    assert _run(web, body, count=1) == 2


def test_clips_are_handed_over_and_a_workers_error_reaches_the_owner(web):
    clip, boom = _names("clip", 0, 1, count=1)[0], _names("boom", 0, 1, count=1)[0]
    web.add(clip)
    web.add(boom)

    async def body(ing):
        assert await _until(lambda: len(web.delivered) == 2 and
                            any(e == "worker_blew_up" for e, _l, _f in web.noted)), (web.delivered, web.noted)
    _run(web, body, count=1)
    assert web.delivered == [("ready", "id-" + clip), ("file", "id-" + clip)]
    event, level, fields = next(n for n in web.noted if n[0] == "worker_blew_up")
    assert level == "error" and fields["channel"] == boom and fields["n"] == 3 and fields["ingest"] == 0


def test_adding_and_removing_a_channel_reaches_the_worker_that_owns_it(web):
    first, later = _names("one", 1, 1)[0], _names("two", 0, 1)[0]
    web.add(first)

    async def body(ing):
        k1, k2 = f"u1:{first}", f"u1:{later}"
        assert await _until(lambda: api._streams[k1]["status"] == "live")
        web.add(later, status="starting")
        await ing.publish_new(later, "twitch", "default", "u1")
        assert await _until(lambda: api._streams[k2]["status"] == "live" and k2 in api._live_slots)
        watching = [(await w.link.call("probe"))["watching"] for w in ing.pool.workers]
        # Removed: the dashboard drops the record first, then says stop.
        api._streams.pop(k1)
        await ing.publish_remove(first, "u1")
        assert await _until(lambda: k1 not in api._live_slots)
        gone = [(await w.link.call("probe"))["watching"] for w in ing.pool.workers]
        return watching, gone
    watching, gone = _run(web, body)
    assert watching == [[f"u1:{later}"], [f"u1:{first}"]]
    assert gone == [[f"u1:{later}"], []]
    assert not web.events("stream_updated", first), "a removed channel was announced again"


def test_a_paused_channel_stays_paused_whatever_its_worker_says_on_the_way_out(web):
    ch = _names("idle", 0, 1, count=1)[0]
    web.add(ch)

    async def body(ing):
        key = f"u1:{ch}"
        assert await _until(lambda: api._streams[key]["status"] == "live" and key in api._live_slots)
        # What _pause_user_streams does: the record first, then the stop.
        api._streams[key]["status"] = "paused"
        api.release_live_slot(key)
        await ing.publish_remove(ch, "u1")
        assert await _until(lambda: bool(web.events("stream_updated", ch)))   # the worker's last word
        await asyncio.sleep(0.2)
        return dict(api._streams[key]), key in api._live_slots, web.events("stream_updated", ch)[-1]
    rec, held, last = _run(web, body, count=1)
    assert rec["status"] == "paused" and held is False
    assert last["stream"]["status"] == "paused", "the tab was told offline for a paused channel"


def test_a_dead_worker_gives_its_slots_back_and_its_channels_return(web):
    mine, other = _names("dies", 0, 2), _names("lives", 1, 2)
    for ch in mine + other:
        web.add(ch)

    async def body(ing):
        keys0, keys1 = [f"u1:{c}" for c in mine], [f"u1:{c}" for c in other]
        assert await _until(lambda: all(api._streams[k]["status"] == "live" for k in keys0 + keys1))
        pid0, pid1 = ing.pool.workers[0].proc.pid, ing.pool.workers[1].proc.pid
        web.sent.clear()
        os.kill(pid0, signal.SIGKILL)
        assert await _until(lambda: len([e for e in web.events("stream_updated")
                                         if e["stream"]["status"] == "reconnecting"]) == 2), web.sent
        between = {k: api._streams[k]["status"] for k in keys0 + keys1}
        slots_between = set(api._live_slots) & set(keys0)
        assert await _until(lambda: all(api._streams[k]["status"] == "live" and k in api._live_slots
                                        for k in keys0)), {k: api._streams[k]["status"] for k in keys0}
        return between, slots_between, pid0, ing.pool.workers[0].proc.pid, pid1, ing.pool.workers[1].proc.pid, keys0, keys1
    between, slots_between, pid0, new0, pid1, still1, keys0, keys1 = _run(web, body)
    assert new0 != pid0 and still1 == pid1
    assert all(between[k] in ("reconnecting", "live") for k in keys0)
    assert all(between[k] == "live" for k in keys1), "the other worker's channels were disturbed"
    # No report about the surviving worker's channels at all while the other was down.
    assert not [e for e in web.events("stream_updated") if e["stream"]["channel"] in
                [k.split(":")[1] for k in keys1]]


# ── the web process stays the authority (no processes needed) ────────────────

@pytest.fixture
def ing(web, monkeypatch):
    return ingest_mod.Ingest(2, module="fakeingest")


def test_a_worker_speaks_only_for_its_own_channels(web, ing):
    mine, theirs = _names("x", 0, 1)[0], _names("y", 1, 1)[0]
    web.add(mine, status="starting")
    web.add(theirs, status="starting")

    async def go():
        await ing.h_broadcast(0, {"event": "stream_status", "channel": theirs, "status": "live"}, "u1")
        await ing.h_broadcast(0, {"event": "stream_updated",
                                  "stream": {"channel": theirs, "status": "offline", "preset": "moba"}}, "u1")
        await ing.h_broadcast(0, {"event": "stream_status", "channel": mine, "status": "live"}, "u1")
    asyncio.run(go())
    assert api._streams[f"u1:{theirs}"]["status"] == "starting" and api._streams[f"u1:{theirs}"]["preset"] == "default"
    assert api._streams[f"u1:{mine}"]["status"] == "live"
    assert [e["channel"] for e in web.events("stream_status")] == [mine]
    assert ing.h_acquire(0, f"u1:{theirs}") is False and not api._live_slots
    ing.h_release(0, f"u1:{theirs}")                     # ignored, and harmless
    assert ing.h_acquire(0, f"u1:{mine}") is True


def test_the_tab_gets_the_real_record_and_a_worker_can_change_only_status_and_preset(web, ing):
    ch = _names("x", 0, 1)[0]
    web.add(ch, status="starting")
    api._streams[f"u1:{ch}"]["added_at"] = 123.0

    async def go():
        await ing.h_broadcast(0, {"event": "stream_updated", "stream": {
            "channel": ch, "status": "live", "preset": "fps", "user_id": "someone-else",
            "added_at": 999.0, "hold": "billing"}}, "u1")
    asyncio.run(go())
    rec = api._streams[f"u1:{ch}"]
    assert (rec["status"], rec["preset"], rec["user_id"], rec["added_at"]) == ("live", "fps", "u1", 123.0)
    assert "hold" not in rec
    assert web.events("stream_updated", ch)[0]["stream"] is rec


def test_a_paused_or_removed_channel_is_never_given_a_slot(web, ing):
    ch = _names("x", 0, 1)[0]
    web.add(ch, status="paused")
    assert ing.h_acquire(0, f"u1:{ch}") is False
    assert ing.h_acquire(0, "u1:" + _names("never", 0, 1)[0]) is False
    assert not api._live_slots

    async def go():
        await ing.h_broadcast(0, {"event": "stream_status", "channel": ch, "status": "live"}, "u1")
        await ing.h_broadcast(0, {"event": "stream_status", "channel": "removed-" + ch, "status": "live"}, "u1")
    asyncio.run(go())
    assert api._streams[f"u1:{ch}"]["status"] == "paused" and web.sent == []


def test_other_events_pass_straight_through(web, ing):
    async def go():
        await ing.h_broadcast(1, {"event": "profile_updated", "profile": {"channel": "c"}}, "u1")
        await ing.h_broadcast(1, {"event": "clip_file_ready", "clip_id": "c1"}, "u1")
        await ing.h_broadcast(1, "not an event", "u1")
    asyncio.run(go())
    assert [k for k, _u, _e in web.sent] == ["profile_updated", "clip_file_ready"]


def test_the_switch_is_off_unless_asked_for():
    import pathlib
    from config.settings import Settings
    assert Settings.model_fields["ingest_workers"].default == 0
    src = pathlib.Path("src/main.py").read_text()
    assert "if settings.ingest_workers > 0:" in src
    assert src.index("if settings.ingest_workers > 0:") < src.index("ingest = Ingest(settings.ingest_workers,")
    # Off: exactly the old publisher and the old restore.
    assert "dashboard_api.set_stream_publisher(_publish_new_stream, _publish_remove_stream)" in src
    assert "await restore_streams()" in src


# ── stage 4: the live limit judges the loop the channel will land on ─────────

from src.ingestion import headroom as hr     # noqa: E402


@pytest.fixture
def measured(monkeypatch):
    """The web process's own meter: its loop nearly idle (with the split it
    carries no channels), the box and memory comfortable."""
    hr.forget()
    monkeypatch.setattr(hr, "_cores", lambda: 8)
    for i in (2, 1, 0):
        hr.record(6.0, 20.0, 12000.0, 0.0, 2.0, 1000.0 - 2.0 * i, 16000.0)
    yield
    hr.forget()


def _reading(pct, live=40, age=1.0, lag=0.0):
    return {"name": "worker 2", "pct": pct, "live": live, "age_s": age, "lag_s": lag}


def test_the_workers_loop_decides_not_the_web_processes(measured):
    """The web loop reads 6%. A channel bound for a worker at 64% is refused
    all the same, and one bound for a worker at 30% gets in."""
    assert hr.allow(160, now=1000.0)[0] is True                    # one process: by the web loop
    ok, why = hr.allow(160, now=1000.0, loop=_reading(64.0))
    assert not ok and why == "worker 2 is at 64% of the one core it can use"
    assert hr.allow(160, now=1000.0, loop=_reading(30.0))[0] is True


def test_the_plans_lines_apply_to_the_workers_loop(measured):
    assert [hr.allow(160, priority=p, now=1000.0, loop=_reading(66.0))[0] for p in (0, 1, 2)] == [False, True, True]
    assert [hr.allow(160, priority=p, now=1000.0, loop=_reading(71.0))[0] for p in (0, 1, 2)] == [False, False, True]
    assert [hr.allow(160, priority=p, now=1000.0, loop=_reading(76.0))[0] for p in (0, 1, 2)] == [False, False, False]


def test_one_more_channel_is_costed_from_that_workers_own_channels(measured):
    """10 channels at 60% is 6% each: no room for another under 65. The same
    60% spread over 80 channels is under 1% each, and there is."""
    assert hr.allow(100, now=1000.0, loop=_reading(60.0, live=10))[0] is False
    assert hr.allow(100, now=1000.0, loop=_reading(60.0, live=80))[0] is True


def test_a_worker_that_has_not_reported_gets_no_new_channels(measured):
    ok, why = hr.allow(100, now=1000.0, loop=_reading(5.0, age=hr.STALE_S + 1))
    assert not ok and "worker 2 has not reported its load" in why
    assert hr.allow(100, now=1000.0, loop=_reading(5.0, age=hr.STALE_S - 1))[0] is True
    ok, why = hr.allow(100, now=1000.0, loop=_reading(5.0, lag=hr.LAG_LINE_S + 0.1))
    assert not ok and "worker 2 is being held up" in why
    assert hr.allow(100, priority=2, now=1000.0, loop=_reading(5.0, lag=hr.LAG_LINE_S + 0.1))[0] is True


def test_the_box_is_still_judged_whichever_loop_it_is(monkeypatch):
    hr.forget()
    monkeypatch.setattr(hr, "_cores", lambda: 8)
    for i in (2, 1, 0):
        hr.record(6.0, 74.9, 12000.0, 0.0, 2.0, 1000.0 - 2.0 * i, 16000.0)
    assert "processors" in hr.allow(100, now=1000.0, loop=_reading(10.0, live=50))[1]
    hr.forget()
    for i in (2, 1, 0):
        hr.record(6.0, 20.0, 1650.0, 0.0, 2.0, 1000.0 - 2.0 * i, 16000.0)
    assert "memory" in hr.allow(100, now=1000.0, loop=_reading(10.0, live=50))[1]
    hr.forget()


def test_the_slot_gate_passes_the_reading_only_when_there_is_one(web, monkeypatch):
    seen = []

    def fake_allow(live, priority=0, now=None, loop="NOT PASSED"):
        seen.append(loop)
        return True, ""
    monkeypatch.setattr(hr, "allow", fake_allow)
    monkeypatch.setattr(api.settings, "live_auto_base", 1)
    api._live_slots.add("x:held")                                   # at the base: the gate is asked
    monkeypatch.setattr(api, "_loop_reader", None)
    assert api.acquire_live_slot("u1:one") is True
    monkeypatch.setattr(api, "_loop_reader", lambda key: {"name": "worker 0", "pct": 12.0, "key": key})
    assert api.acquire_live_slot("u1:two") is True
    assert seen == ["NOT PASSED", {"name": "worker 0", "pct": 12.0, "key": "u1:two"}]


def test_the_reading_is_the_owning_workers_and_counts_its_channels(web, ing):
    mine, theirs = _names("x", 0, 3), _names("y", 1, 2)
    api._live_slots.update(f"u1:{c}" for c in mine + theirs)
    w0, w1 = ing.pool.workers
    w0.ready, w0.beat_at, w0.loop_avg, w0.lag_avg = True, time.monotonic() - 1.5, 41.0, 0.01
    got = ing.loop_for(f"u9:{mine[0]}")
    assert got["name"] == "worker 0" and got["pct"] == 41.0 and got["live"] == 3
    assert 1.0 < got["age_s"] < 5.0 and got["lag_s"] == 0.01
    # The other worker is down: its reading is too old for the gate to accept.
    assert ing.loop_for(f"u9:{theirs[0]}")["age_s"] > hr.STALE_S
    assert ing.loop_for(f"u9:{theirs[0]}")["live"] == 2


def test_the_admin_page_is_given_a_line_per_worker(web, monkeypatch):
    import pathlib
    monkeypatch.setattr(api, "_ingest_status", lambda: [
        {"index": 0, "up": True, "loop_avg": 33.4, "streams": 21, "restarts": 0, "rss_mb": 180.0, "pid": 5},
        {"index": 1, "up": False, "restarts": 2}])
    snap = api._headroom_snapshot()
    assert snap["workers"] == [
        {"index": 0, "up": True, "loop_pct": 33.4, "streams": 21, "restarts": 0, "rss_mb": 180.0},
        {"index": 1, "up": False, "loop_pct": 0.0, "streams": 0, "restarts": 2, "rss_mb": 0}]
    monkeypatch.setattr(api, "_ingest_status", None)
    assert "workers" not in api._headroom_snapshot()                # one process: as it was
    src = pathlib.Path("src/dashboard/api.py").read_text()
    assert "if((hr.workers || []).length){" in src and "w.up ? Math.round(w.loop_pct) + '%' : 'down'" in src
    assert "dashboard_api.set_ingest_readers(ingest.loop_for, ingest.status)" in pathlib.Path("src/main.py").read_text()


def test_a_real_worker_reports_and_its_average_reaches_the_gate(web):
    ch = _names("beat", 0, 1, count=1)[0]
    web.add(ch)

    async def body(ing):
        assert await _until(lambda: ing.pool.workers[0].beat_at > 0, timeout=12)
        return ing.loop_for(f"u1:{ch}"), ing.status()[0]
    reading, status = _run(web, body, count=1)
    assert reading["age_s"] < hr.STALE_S and 0.0 <= reading["pct"] < 60.0
    assert status["loop_avg"] == round(reading["pct"], 1) and status["up"] is True


# ── a trial on a few channels (INGEST_ONLY) ──────────────────────────────────

def test_with_no_list_every_channel_is_the_workers(web):
    assert ingest_mod.Ingest(2, module="fakeingest").handles("anything") is True
    assert ingest_mod.Ingest(2, module="fakeingest", only="  ,  ").only is None


def test_only_the_named_channels_run_in_a_worker_and_the_rest_are_not_touched(web):
    """One named channel goes to a worker. The other is left for the web
    process to run as it always has: not sent, not reported on, not measured
    against a worker, not cleaned up after."""
    trial, other = _names("trial", 0, 1, count=1)[0], _names("other", 0, 1, count=1)[0]
    web.add(trial)
    web.add(other, status="live")                       # as if the web process is running it
    api._live_slots.add(f"u1:{other}")

    async def body(ing):
        assert ing.handles(trial.upper()) and not ing.handles(other)
        assert await _until(lambda: api._streams[f"u1:{trial}"]["status"] == "live")
        watching = (await ing.pool.workers[0].link.call("probe"))["watching"]
        reading_trial, reading_other = ing.loop_for(f"u1:{trial}"), ing.loop_for(f"u1:{other}")
        assert ing.owns("u1", trial) and not ing.owns("u1", other)
        assert ing.h_acquire(0, f"u1:{other}") is False              # a worker may not speak for it
        os.kill(ing.pool.workers[0].proc.pid, signal.SIGKILL)       # and losing the worker...
        assert await _until(lambda: any(e["stream"]["channel"] == trial and
                                        e["stream"]["status"] == "reconnecting"
                                        for e in web.events("stream_updated")))
        return watching, reading_trial, reading_other, dict(api._streams[f"u1:{other}"]), set(api._live_slots)
    async def go():
        ing = ingest_mod.Ingest(1, module="fakeingest", only=f" {trial.upper()} , someone-else")
        await ing.start()
        try:
            return await body(ing)
        finally:
            await ing.stop()
    watching, reading_trial, reading_other, other_rec, slots = asyncio.run(go())
    assert watching == [f"u1:{trial}"]
    assert reading_trial is not None and reading_other is None      # the web loop is judged for it
    assert other_rec["status"] == "live" and f"u1:{other}" in slots, "...did not disturb the other channel"
    assert not web.events("stream_updated", other) and not web.events("stream_status", other)


def test_the_web_process_restores_only_what_the_workers_do_not_handle(monkeypatch):
    from src import main
    spawned = []

    async def _spawn(channel, platform, user_id="", preset="default"):
        spawned.append(channel)
    monkeypatch.setattr(main, "spawn_worker", _spawn)
    monkeypatch.setattr(api, "_streams", {
        "u:a": {"channel": "a", "status": "offline", "user_id": "u"},
        "u:b": {"channel": "b", "status": "offline", "user_id": "u"},
        "u:c": {"channel": "c", "status": "paused", "user_id": "u"}})
    assert asyncio.run(main.restore_streams(skip=lambda ch: ch == "a")) == 1 and spawned == ["b"]
    spawned.clear()
    assert asyncio.run(main.restore_streams()) == 2 and spawned == ["a", "b"]      # no split: as before


def test_main_routes_by_channel_and_the_setting_is_empty_by_default():
    import pathlib
    from config.settings import Settings
    assert Settings.model_fields["ingest_only"].default == ""
    src = pathlib.Path("src/main.py").read_text()
    assert "Ingest(settings.ingest_workers, only=settings.ingest_only)" in src
    assert "dashboard_api.set_stream_publisher(_route_new, _route_remove)" in src
    assert "await restore_streams(skip=ingest.handles)" in src
    route = src[src.index("async def _route_new("):src.index("dashboard_api.set_stream_publisher(_route_new")]
    assert route.count("if ingest.handles(channel):") == 2
    assert "await _publish_new_stream(channel, platform, preset, user_id)" in route
    assert "await _publish_remove_stream(channel, user_id)" in route
