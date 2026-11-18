"""What the app's one loop is busy with (src/maintenance/loop_profile.py).

Measured on production 2026-11-18 with 33 channels live: the server was 21%
busy and the one thread that runs everything was at 44% of its single core,
which is what caps the site near 52 live channels on a server of any size.
This tool says where that 44% goes, by timing every step the loop takes for a
few seconds. What these hold:

  1. a step is filed under the right name, and a name never carries an account
     or a channel;
  2. the numbers are RIGHT against a loop doing a known amount of work (the
     first version sampled from a second thread and read a half-busy loop as
     idle, which is why this one times instead);
  3. the wrapper always comes off again, whatever happens;
  4. it is asked for with a file, answered once, and the task that watches for
     it can never take the product down;
  5. the report is made of names only.
"""

import asyncio
import functools
import inspect
import pathlib
import time

import pytest

from src.maintenance import loop_profile as lp


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(lp.settings, "local_storage_path", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _wrapper_is_off():
    """Whatever a test does, asyncio's Handle._run is its own again after."""
    orig = asyncio.events.Handle._run
    yield
    assert asyncio.events.Handle._run is orig, "the timing wrapper was left on"
    if lp._running.acquire(blocking=False):
        lp._running.release()
    else:
        lp._running.release()


# ── 1. names ─────────────────────────────────────────────────────────────────

def test_a_task_name_loses_the_account_and_the_channel():
    assert lp.task_kind("worker-9f2c:xqc") == "worker"
    assert lp.task_kind("post-abc123") == "post"
    assert lp.task_kind("fetch-fallback-clipid") == "fetch-fallback"
    assert lp.task_kind("clip-processor") == "clip-processor"       # a fixed name stays whole
    assert lp.task_kind("Task-4821") == "Task"


def test_a_step_is_filed_under_what_it_belongs_to():
    async def go():
        async def score_chat():
            await asyncio.sleep(10)
        named = asyncio.create_task(score_chat(), name="worker-user9:somechannel")
        plain = asyncio.create_task(score_chat())
        await asyncio.sleep(0)
        got = {
            # What asyncio really schedules for a task: its bound step/wakeup.
            "named": lp.label(named.cancel),
            "plain": lp.label(plain.cancel),
            "partial": lp.label(functools.partial(functools.partial(plain.cancel))),
            "method": lp.label(asyncio.get_running_loop().stop),
            "function": lp.label(time.sleep),
        }
        named.cancel(); plain.cancel()
        return got
    got = asyncio.run(go())
    assert got["named"] == "task: worker"
    assert "somechannel" not in str(got) and "user9" not in str(got)
    assert got["plain"].startswith("task: ") and got["plain"].endswith("score_chat")
    assert got["partial"] == got["plain"]
    assert got["method"].startswith("callback: ") and got["method"].endswith(".stop")
    assert got["function"] == "callback: sleep"
    assert lp.label(object()) == "callback: object"           # never an error
    assert lp.label(None) == "callback: (cancelled)"


# ── 2. the numbers are right ─────────────────────────────────────────────────

def _spin(ms):
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        pass


def test_a_half_busy_loop_reads_as_half_busy_not_as_idle():
    """THE bug of the sampling version: thousands of short steps, and a second
    thread only ever caught the loop resting between them."""
    async def go():
        async def burn():
            while True:
                _spin(2)                       # 2 ms of work...
                await asyncio.sleep(0.002)     # ...then about 2 ms of rest
        t = asyncio.create_task(burn(), name="worker-u:c")
        timing = lp.Timing()
        timing.install()
        await asyncio.sleep(0.6)
        res = timing.finish()
        t.cancel()
        return res
    res = asyncio.run(go())
    secs, steps, longest = res["rows"]["task: worker"][:3]
    share = secs / res["seconds"]
    assert 0.30 < share < 0.75, f"a loop about half busy read as {share:.0%}"
    assert steps > 50 and 0.0015 < secs / steps < 0.02
    assert longest >= 0.002


def test_a_step_that_blocks_is_seen_as_blocking_not_as_work():
    async def go():
        async def blocks():
            await asyncio.sleep(0.02)
            time.sleep(0.12)                   # holds the loop, uses no CPU
        timing = lp.Timing()
        timing.install()
        await asyncio.create_task(blocks())
        return timing.finish()
    res = asyncio.run(go())
    row = next(v for k, v in res["rows"].items() if k.endswith("blocks"))
    assert row[2] >= 0.1
    out = lp.render(res)
    assert "STEPS THAT HELD THE LOOP FOR 50ms OR MORE" in out
    held = out.split("STEPS THAT HELD THE LOOP")[1]
    assert "blocks" in held and "none in this window" not in held
    if res["cpu"]:                             # blocked time is not CPU time
        assert res["cpu"] < sum(v[0] for v in res["rows"].values())


# ── 3. the wrapper always comes off ──────────────────────────────────────────

def test_the_wrapper_comes_off_and_a_step_that_raises_is_still_timed():
    orig = asyncio.events.Handle._run

    async def go():
        async def dies():
            raise RuntimeError("a task failing is not the timer's business")
        timing = lp.Timing()
        timing.install()
        assert asyncio.events.Handle._run is not orig
        t = asyncio.create_task(dies())
        await asyncio.sleep(0.01)
        assert isinstance(t.exception(), RuntimeError)
        res = timing.finish()
        timing.finish()                        # twice is harmless
        return res
    res = asyncio.run(go())
    assert asyncio.events.Handle._run is orig
    assert any(k.endswith("dies") for k in res["rows"])


def test_the_bookkeeping_can_never_raise_into_the_loop(monkeypatch):
    def boom(_cb):
        raise RuntimeError("label broke")
    monkeypatch.setattr(lp, "label", boom)

    async def go():
        timing = lp.Timing()
        timing.install()
        hits = []
        asyncio.get_running_loop().call_soon(hits.append, 1)
        await asyncio.sleep(0.01)
        timing.finish()
        return hits
    assert asyncio.run(go()) == [1]            # the step itself still ran


def test_a_whole_window_writes_the_report_and_only_one_runs_at_a_time(store):
    async def go():
        loop = asyncio.get_running_loop()
        assert lp.start(loop, 0.15, lambda: 3) is True
        assert lp.start(loop, 0.15) is False                  # one at a time
        await asyncio.sleep(0.4)
        assert lp.start(loop, 0.05) is True                   # and again once it is done
        await asyncio.sleep(0.2)
    asyncio.run(go())
    text = (store / lp.REPORT).read_text()
    assert "WHERE THE APP'S ONE LOOP SPENDS ITS TIME" in text and "every step timed" in text


def test_a_report_that_cannot_be_written_still_takes_the_wrapper_off(store, monkeypatch):
    def nope(_text):
        raise OSError("disk full")
    monkeypatch.setattr(lp, "_write", nope)

    async def go():
        assert lp.start(asyncio.get_running_loop(), 0.05) is True
        await asyncio.sleep(0.2)
    asyncio.run(go())                          # the autouse fixture checks the rest


# ── 4. asked for with a file ─────────────────────────────────────────────────

def test_how_long_to_watch_is_kept_within_bounds():
    assert lp._seconds_asked("30") == 30 and lp._seconds_asked("") == lp.DEFAULT_S
    assert lp._seconds_asked("9999") == lp.MAX_S and lp._seconds_asked("0.1") == lp.MIN_S
    assert lp._seconds_asked("rm -rf /") == lp.DEFAULT_S


def test_a_request_is_answered_once_and_removed(store, monkeypatch):
    started = []
    monkeypatch.setattr(lp, "start", lambda loop, seconds, live=None: started.append(seconds) or True)

    async def go():
        t = asyncio.create_task(lp.watch_task(every=0.01))
        await asyncio.sleep(0.05)
        assert started == []                               # nothing asked, nothing done
        (store / lp.REQUEST).write_text("12")
        await asyncio.sleep(0.1)
        t.cancel()
    asyncio.run(go())
    assert started == [12.0]
    assert not (store / lp.REQUEST).exists()


def test_the_watcher_survives_anything_but_being_cancelled(monkeypatch):
    """main() gathers it with the website and the stream workers: an exception
    out of it would stop the whole product."""
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(lp, "_store", boom)
    slept = []
    real = asyncio.sleep

    async def fast(s):
        slept.append(s)
        await real(0)
    monkeypatch.setattr(lp.asyncio, "sleep", fast)

    async def go():
        t = asyncio.create_task(lp.watch_task(every=0.01))
        for _ in range(30):
            await real(0)
        assert not t.done(), "the watcher died"
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert calls["n"] >= 2 and 30 in slept            # it backed off and tried again


def test_it_is_started_with_the_site_and_asked_by_file_never_by_signal():
    main = pathlib.Path("src/main.py").read_text()
    assert "asyncio.create_task(_loop_profile.watch_task(" in main
    assert 'name="loop-profile-watch"' in main
    script = pathlib.Path("scripts/loop_profile.py").read_text()
    code = inspect.getsource(lp)
    # A signal sent to a site still running older code would kill it.
    for banned in ("os.kill", "signal.signal", "SIGUSR", "setitimer"):
        assert banned not in script and banned not in code, banned
    assert "req.write_text(str(seconds))" in script
    assert "did not pick the request up" in script
    # And nothing new to install: a deploy never runs pip.
    for mod in ("psutil", "pyinstrument", "py_spy", "yappi", "cProfile"):
        assert "import " + mod not in code and "import " + mod not in script, mod


# ── 5. the report ────────────────────────────────────────────────────────────

RESULT = {"seconds": 20.0, "cpu": 8.0, "rows": {
    "task: worker": (6.6, 80000, 0.004),
    "task: RequestResponseCycle.run_asgi": (2.2, 4000, 0.09),
    "callback: _UnixReadPipeTransport._read_ready": (0.2, 20000, 0.0001)}}


def test_the_report_adds_up_and_says_what_each_channel_costs():
    out = lp.render(RESULT, live=33)
    assert "busy                   : 45.0% of the one core it can use" in out
    assert "waiting for work       : 55.0%" in out
    assert "CPU the thread used    : 40.0%   <- 5.0% of the busy time was the loop BLOCKED" in out
    assert "channels live          : 33   (~1.36% each)" in out
    assert out.index("task: worker") < out.index("task: RequestResponseCycle.run_asgi")   # biggest first
    line = next(l for l in out.splitlines() if l.rstrip().endswith("task: worker"))
    assert "33.0%" in line and "4,000" in line and "82us" in line and "4.0ms" in line
    # The one step over 50 ms is called out on its own.
    held = out.split("STEPS THAT HELD THE LOOP")[1]
    assert "90ms   task: RequestResponseCycle.run_asgi" in held and "task: worker" not in held


def test_cpu_a_little_above_busy_is_not_called_blocking():
    """The thread's clock also counts the loop's housekeeping, so on a quiet
    site it reads slightly over busy. That is not the loop being blocked."""
    out = lp.render({"seconds": 10.0, "cpu": 0.67, "rows": {"task: x": (0.63, 100, 0.001)}})
    assert "CPU the thread used    : 6.7%" in out and "BLOCKED" not in out.split("BY WHAT WAS RUNNING")[0]


def test_nothing_timed_and_a_long_tail_are_both_readable():
    empty = lp.render({"seconds": 5.0, "cpu": 0.0, "rows": {}}, live=0)
    assert "nothing was timed" in empty and "channels live          : 0" in empty
    assert "none in this window" in empty
    many = {f"task: f{i}": (0.01, 10, 0.001) for i in range(40)}
    out = lp.render({"seconds": 5.0, "cpu": 0.0, "rows": many}, top=10)
    assert "(30 smaller ones)" in out and out.count("task: f") == 10


def test_it_reads_names_and_nothing_else():
    """It must be safe to paste: a stream URL carries a signed token and a
    request carries a session, and both are values, not names."""
    code = inspect.getsource(lp.label) + inspect.getsource(lp.Timing)
    for banned in ("f_locals", "f_globals", "_args", "argv", "cmdline", "repr(", "str(h"):
        assert banned not in code, banned
    assert "Names of tasks and functions only." in lp.render(RESULT)
    assert "/" not in "".join(RESULT["rows"]) and "/" not in lp.label(time.sleep)


# ── computing or waiting, and memory tidying (2026-11-18) ────────────────────
# The first report said the loop was "blocked" for 18 of its 57 points and
# could not say where. Each step now has its own CPU clock.

def _window(body, seconds=0.5):
    async def go():
        t = asyncio.create_task(body(), name="worker-u:c")
        timing = lp.Timing()
        timing.install()
        await asyncio.sleep(seconds)
        res = timing.finish()
        t.cancel()
        return res
    return asyncio.run(go())


def test_a_step_that_sleeps_is_waiting_not_computing():
    async def stalls():
        while True:
            time.sleep(0.06)                   # a disk or a blocking call
            await asyncio.sleep(0.01)
    res = _window(stalls)
    secs, steps, longest, cpu, cpu_longest = res["rows"]["task: worker"]
    assert secs > 0.25 and cpu < 0.2 * secs, (secs, cpu)
    assert cpu_longest < 0.5 * longest
    text = lp.render(res)
    assert "WAITING INSIDE A STEP" in text
    after = text.split("WAITING INSIDE A STEP", 1)[1]
    assert "task: worker" in after.split("STEPS THAT HELD", 1)[0]
    assert "(computing for " in text


def test_a_step_that_computes_is_not_called_waiting():
    async def burns():
        while True:
            _spin(20)
            await asyncio.sleep(0.005)
    res = _window(burns)
    secs, steps, longest, cpu, _ = res["rows"]["task: worker"]
    assert cpu > 0.8 * secs, (secs, cpu)
    section = lp.render(res).split("WAITING INSIDE A STEP")
    assert len(section) == 1 or "task: worker" not in section[1].split("STEPS THAT HELD", 1)[0]


def test_memory_tidying_is_timed_and_says_whose_thread_it_ran_on():
    import gc
    import threading

    async def tidies():
        while True:
            gc.collect()                       # on the loop's thread
            th = threading.Thread(target=gc.collect)
            th.start()
            while th.is_alive():
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
    res = _window(tidies)
    assert (2, True) in res["gc"] and (2, False) in res["gc"], res["gc"].keys()
    runs, secs, longest = res["gc"][(2, True)]
    assert runs >= 1 and secs >= longest > 0
    text = lp.render(res)
    assert "PYTHON TIDYING MEMORY" in text
    assert "on the loop's thread" in text and "on another thread (the loop waits for it)" in text


def test_the_collector_is_left_as_it_was_found():
    import gc
    before = list(gc.callbacks)
    timing = lp.Timing()

    async def go():
        timing.install()
        assert len(gc.callbacks) == len(before) + 1
        timing.finish()
    asyncio.run(go())
    assert gc.callbacks == before
    timing.finish()                            # twice is harmless
    assert gc.callbacks == before


def test_a_report_from_rows_without_the_cpu_clock_still_prints():
    """Rows of three fields (the first version, and a machine that does not
    offer the thread clock) print with the waiting column blank."""
    text = lp.render({"seconds": 10.0, "cpu": 0.0,
                      "rows": {"task: engine": (2.0, 100, 0.2), "task: a-x": (1.0, 5, 0.3),
                               "task: a-y": (1.0, 5, 0.1)}})
    assert "task: engine" in text and "a-*  (2 of them)" in text
    assert "WAITING INSIDE A STEP" not in text and "computing for" not in text


def test_grouped_rows_add_their_cpu():
    got = lp.grouped({"task: z-a": (1.0, 2, 0.5, 0.25, 0.1), "task: z-b": (2.0, 3, 0.9, 0.5, 0.4)})
    assert got["task: z-*  (2 of them)"] == (3.0, 5, 0.9, 0.75, 0.4)


# ── THE SPLIT: more than one loop in the report (2026-11-18) ─────────────────
# With INGEST_WORKERS each ingest worker has a loop of its own. A window is
# opened in each at the same moment; the report is the web process's table and
# then one per worker.

def test_a_result_survives_the_trip_between_processes():
    import json
    result = {"seconds": 20.0, "cpu": 9.5,
              "rows": {"task: worker": (4.0, 900, 0.2, 3.5, 0.01), "callback: old": (1.0, 5, 0.3)},
              "gc": {(2, True): (3, 0.4, 0.2), (0, False): (50, 0.01, 0.001)}}
    back = lp.from_wire(json.loads(json.dumps(lp.to_wire(result))))
    assert back == result
    assert lp.render(back, 4) == lp.render(result, 4)


def test_something_malformed_from_another_process_is_left_out_not_raised():
    back = lp.from_wire({"seconds": "x", "rows": {"ok": [1, 2, 3, 4, 5], "bad": ["a"], "worse": 7},
                         "gc": [[2, True, 1, 0.1, 0.1], ["x"], None]})
    assert back["rows"] == {"ok": (1.0, 2, 3.0, 4.0, 5.0)}
    assert back["gc"] == {(2, True): (1, 0.1, 0.1)} and back["seconds"] > 0
    assert "nothing was timed" in lp.render(lp.from_wire(None))


def test_a_worker_times_its_own_loop_and_leaves_it_as_it_found_it():
    async def go():
        async def busy():
            end = time.monotonic() + 0.25
            while time.monotonic() < end:
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < 0.004:
                    pass
                await asyncio.sleep(0.004)
        t = asyncio.create_task(busy(), name="worker-u1:somebody")
        first = asyncio.create_task(lp.measure(0.3))
        await asyncio.sleep(0.05)
        with pytest.raises(RuntimeError):
            await lp.measure(0.1)                          # one window at a time, here too
        out = await first
        await t
        return out
    data = asyncio.run(go())
    assert isinstance(data["gc"], list) and isinstance(data["rows"]["task: worker"], list)
    res = lp.from_wire(data)
    assert 25.0 < 100.0 * res["rows"]["task: worker"][0] / res["seconds"] < 75.0
    assert not any("somebody" in k for k in res["rows"])


def test_the_title_says_whose_loop_it_is():
    res = {"seconds": 10.0, "cpu": 0.0, "rows": {"task: worker": (1.0, 10, 0.1, 0.9, 0.1)}, "gc": {}}
    assert lp.TITLE in lp.render(res)
    text = lp.render(res, 3, title="INGEST WORKER 1: WHERE ITS LOOP SPENDS ITS TIME")
    assert "INGEST WORKER 1" in text and lp.TITLE not in text


def _split_window(store, others, seconds=0.1, wait=0.6):
    """One window with other loops to wait for. Returns (was the report there
    just after THIS process's window closed, could another window be opened
    then, the report in the end)."""
    async def go():
        loop = asyncio.get_running_loop()
        assert lp.start(loop, seconds, lambda: 2, others) is True
        await asyncio.sleep(seconds + 0.03)
        early = (store / lp.REPORT).exists()
        free = lp._running.acquire(blocking=False)
        if free:
            lp._running.release()
        await asyncio.sleep(wait)
        return early, free
    early, free = asyncio.run(go())
    return early, free, (store / lp.REPORT).read_text() if (store / lp.REPORT).exists() else ""


def test_the_report_waits_for_the_other_loops_and_has_a_table_each(store):
    asked = []

    async def others(seconds):
        asked.append(seconds)
        await asyncio.sleep(0.25)                           # in after this process's own window
        return ["WORKER ZERO TABLE\n", "WORKER ONE TABLE\n"]

    early, free, text = _split_window(store, others)
    assert asked == [0.1]
    assert early is False, "written before the workers' tables were in"
    assert free is False, "a second window could open while the first was unwritten"
    assert text.index(lp.WEB_TITLE) < text.index("WORKER ZERO TABLE") < text.index("WORKER ONE TABLE")
    assert lp.TITLE not in text and "channels live          : 2" in text
    assert lp._running.acquire(blocking=False)             # and the lock came back
    lp._running.release()


def test_other_loops_that_cannot_be_read_do_not_cost_this_ones_table(store):
    async def broken(seconds):
        raise ConnectionError("link closed")
    _early, _free, text = _split_window(store, broken)
    assert lp.WEB_TITLE in text and "COULD NOT BE READ (ConnectionError)" in text
    assert lp._running.acquire(blocking=False)
    lp._running.release()


def test_other_loops_that_never_answer_are_not_waited_for_for_ever(store, monkeypatch):
    monkeypatch.setattr(lp, "OTHERS_WAIT_S", 0.1)

    async def silent(seconds):
        await asyncio.sleep(30)
    _early, _free, text = _split_window(store, silent)
    assert lp.WEB_TITLE in text and "COULD NOT BE READ (TimeoutError)" in text
    assert lp._running.acquire(blocking=False)
    lp._running.release()


def test_a_report_that_cannot_be_written_gives_the_lock_back_with_others_too(store, monkeypatch):
    def nope(_text):
        raise OSError("disk full")
    monkeypatch.setattr(lp, "_write", nope)

    async def others(seconds):
        return ["X\n"]
    _split_window(store, others)
    assert lp._running.acquire(blocking=False)
    lp._running.release()


def test_the_watcher_hands_the_other_loops_on_only_when_there_are_some(store, monkeypatch):
    calls = []

    def fake(loop, seconds, live=None, *rest):
        calls.append(rest)
        return True
    monkeypatch.setattr(lp, "start", fake)

    async def others(seconds):
        return []

    async def go(o):
        t = asyncio.create_task(lp.watch_task(every=0.01, others=o))
        (store / lp.REQUEST).write_text("12")
        await asyncio.sleep(0.1)
        t.cancel()
    asyncio.run(go(None))
    asyncio.run(go(others))
    assert calls == [(), (others,)]


def test_main_gives_the_watcher_the_workers_and_counts_only_its_own_channels():
    main = pathlib.Path("src/main.py").read_text()
    assert "others=ingest.loop_profiles if ingest is not None else None)," in main
    assert 'and not (ingest is not None and ingest.handles(s.get("channel") or ""))),' in main
