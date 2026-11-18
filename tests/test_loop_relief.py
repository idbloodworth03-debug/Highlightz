"""Taking housekeeping off the app's one loop (2026-11-18).

Measured on production with 44 channels live (scripts/loop_profile.py): the
loop was 63% busy, and about 40 points of that were not the live channels:

  17.7%  the hourly billing check, in 236 ms freezes (a blocking Stripe call
         inside an `async def`);
   8.1%  one web request holding the loop for 1.6 s (the admin page re-reading
         the whole lifetime clip ledger, every 30 to 60 seconds while open);
  ~13%   each channel's video-buffer clean-up listing files on the loop every
         5 s, AND walking every other channel's folder to total them: N
         recorders reading N folders.

That, not the size of the server, is what held the site near 46 live
channels. What these hold (the billing half is in test_admin_stripe_sync):

  1. the clean-up's disk work runs off the loop and deletes what it always did;
  2. the whole-store total is measured once for everybody, not once per
     channel, and a failed measurement is retried;
  3. the cap still stops capture when it is really exceeded;
  4. the ledger is parsed off the loop, once for all callers, and each caller
     gets its own rows;
  5. the admin user list counts clips in one pass;
  6. the profiler shows a per-channel job as one row with its total.
"""

import asyncio
import inspect
import json
import threading
import time

import pytest

from config.settings import settings
from src.ingestion import clip_recorder as rec
from src.maintenance import loop_profile as lp
from src.stats import stream_stats


@pytest.fixture
def capture(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_ROOT", tmp_path / "capture")
    monkeypatch.setattr(rec, "_total", {"at": 0.0, "bytes": 0})
    monkeypatch.setattr(settings, "clip_capture_buffer_s", 20)        # 10 segments at 2 s
    monkeypatch.setattr(settings, "clip_capture_segment_s", 2)
    return tmp_path / "capture"


def _ring(channel: str, n: int = 40, size: int = 10):
    r = rec.ClipRecorder(channel, "u")
    r.dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (r.dir / f"seg-{i:05d}.ts").write_bytes(b"x" * size)
    return r


async def _one_pass(r, monkeypatch):
    """Run the recorder's prune loop for exactly one turn."""
    real = asyncio.sleep

    async def stop_after(_s):
        r._running = False
        await real(0)
    monkeypatch.setattr(rec.asyncio, "sleep", stop_after)
    r._running = True
    await r._prune_loop()


# ── 1. the clean-up runs off the loop ────────────────────────────────────────

def test_the_prune_runs_on_a_worker_thread_and_keeps_the_same_segments(capture, monkeypatch):
    r = _ring("novafps", 60)
    where = []
    real = r._prune_once
    monkeypatch.setattr(r, "_prune_once", lambda: (where.append(threading.current_thread().name), real())[1])
    asyncio.run(_one_pass(r, monkeypatch))
    assert where and where[0].startswith("recorder-io"), where
    left = sorted(p.name for p in r.dir.glob("*.ts"))
    # buffer / segment + 4 complete ones, plus the one ffmpeg is writing.
    assert len(left) == 15 and left[-1] == "seg-00059.ts" and left[0] == "seg-00045.ts"


def test_the_loop_itself_no_longer_lists_files():
    src = inspect.getsource(rec.ClipRecorder._prune_loop)
    assert "await _off_loop(self._prune_once)" in src
    assert "total_bytes_shared()" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "total_bytes()" not in code and "self._prune_once()" not in code
    # A pool of its own: the default one also runs caption transcription.
    assert rec._IO._max_workers == 2 and rec._IO._thread_name_prefix == "recorder-io"


# ── 2. one measurement for everybody ─────────────────────────────────────────

def test_many_recorders_share_one_walk_of_the_store(capture, monkeypatch):
    """It was one walk of EVERY folder per recorder per prune: 22 recorders,
    22 walks of 22 folders, every 5 seconds."""
    rings = [_ring(f"chan{i}", 12, 100) for i in range(8)]
    walks = []
    real = rec.total_bytes
    monkeypatch.setattr(rec, "total_bytes", lambda: (walks.append(threading.current_thread().name), real())[1])

    async def go():
        got = await asyncio.gather(*[rec.total_bytes_shared() for _ in rings])
        again = await rec.total_bytes_shared()
        return got, again
    got, again = asyncio.run(go())
    assert len(walks) == 1 and walks[0].startswith("recorder-io")
    assert again == 8 * 12 * 100                      # the real figure, once measured
    assert set(got) <= {0, again}                     # the others used the last figure meanwhile


def test_the_total_is_measured_again_when_it_is_old_or_failed(capture, monkeypatch):
    _ring("a", 5, 10)
    n = {"walks": 0}

    def flaky():
        n["walks"] += 1
        if n["walks"] == 1:
            raise OSError("disk hiccup")
        return 50
    monkeypatch.setattr(rec, "total_bytes", flaky)

    async def go():
        first = await rec.total_bytes_shared()        # fails: nothing measured
        second = await rec.total_bytes_shared()       # so it asks again at once
        third = await rec.total_bytes_shared()        # fresh: no walk
        rec._total["at"] = time.monotonic() - rec._TOTAL_MAX_AGE - 1
        fourth = await rec.total_bytes_shared()       # old: walk again
        return first, second, third, fourth
    assert asyncio.run(go()) == (0, 50, 50, 50)
    assert n["walks"] == 3


# ── 3. the cap still stops capture ───────────────────────────────────────────

def test_a_store_really_over_its_cap_still_stops_the_recorder(capture, monkeypatch):
    r = _ring("fat", 20, 1024 * 1024)                  # 20 MB on disk
    monkeypatch.setattr(settings, "clip_capture_max_total_mb", 1)
    stopped = []

    async def _stop():
        stopped.append(True)
        r._running = False
    monkeypatch.setattr(r, "stop", _stop)
    asyncio.run(_one_pass(r, monkeypatch))
    assert stopped == [True]


def test_under_the_cap_nothing_is_stopped(capture, monkeypatch):
    r = _ring("thin", 20, 10)
    monkeypatch.setattr(settings, "clip_capture_max_total_mb", 100)
    stopped = []

    async def _stop():
        stopped.append(True)
    monkeypatch.setattr(r, "stop", _stop)
    asyncio.run(_one_pass(r, monkeypatch))
    assert stopped == []


# ── 4. the ledger ────────────────────────────────────────────────────────────

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    f = tmp_path / "stream_stats.jsonl"
    monkeypatch.setattr(stream_stats, "_LOG_FILE", f)
    now = time.time()
    rows = [{"event": stream_stats.CAUGHT, "user_id": "u1", "channel": "xqc", "clip_id": f"c{i}", "ts": now - i}
            for i in range(6)]
    rows.append({"event": stream_stats.APPROVED, "user_id": "u1", "channel": "xqc", "clip_id": "c0", "ts": now})
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return f


def test_the_ledger_is_parsed_off_the_loop_once_for_every_caller(ledger, monkeypatch):
    where = []
    real = stream_stats.all_rows
    monkeypatch.setattr(stream_stats, "all_rows",
                        lambda: (where.append(threading.current_thread() is threading.main_thread()), real())[1])

    async def go():
        return await asyncio.gather(*[stream_stats.all_rows_shared() for _ in range(5)])
    got = asyncio.run(go())
    assert where == [False], "parsed on the loop, or parsed once per caller"
    assert all(g and g[0]["caught"] == 6 for g in got)
    # Each caller's rows are its own: one of them writes a username on each.
    got[0][0]["username"] = "nova"
    assert "username" not in got[1][0]


def test_a_second_ask_within_the_minute_does_not_parse_again(ledger, monkeypatch):
    n = {"parses": 0}
    real = stream_stats.totals_by_user
    monkeypatch.setattr(stream_stats, "totals_by_user", lambda: (n.update(parses=n["parses"] + 1), real())[1])

    async def go():
        a = await stream_stats.totals_by_user_shared()
        b = await stream_stats.totals_by_user_shared()
        return a, b
    a, b = asyncio.run(go())
    assert n["parses"] == 1 and a["u1"]["caught"] == 6 and a is b
    stream_stats.forget_scans()
    asyncio.run(stream_stats.totals_by_user_shared())
    assert n["parses"] == 2


def test_deleting_an_account_is_not_hidden_behind_the_kept_read(ledger):
    assert asyncio.run(stream_stats.totals_by_user_shared())["u1"]["caught"] == 6
    assert stream_stats.delete_all_for_user("u1") == 7
    assert asyncio.run(stream_stats.totals_by_user_shared()) == {}


def test_what_a_user_sees_still_reads_the_file_directly():
    """Only the admin pages may be a minute behind."""
    for fn in (stream_stats.missed_since, stream_stats.for_user, stream_stats.evictions_since):
        assert "_shared" not in inspect.getsource(fn)


# ── 5. the admin endpoints ───────────────────────────────────────────────────

def test_the_admin_endpoints_use_the_shared_reads_and_count_in_one_pass():
    from src.dashboard import api
    users = inspect.getsource(api.admin_list_users)
    code = "\n".join(l for l in users.splitlines() if not l.strip().startswith("#"))
    assert "await stream_stats.totals_by_user_shared()" in code
    assert "stream_stats.totals_by_user()" not in code
    # Not once per account any more.
    assert "for c in _clips.values() if c.get(\"user_id\") == uid" not in code
    assert "_c.Counter(c.get(\"user_id\") for c in list(_clips.values()))" in code
    overview = "\n".join(l for l in inspect.getsource(api.admin_overview).splitlines()
                         if not l.strip().startswith("#"))
    assert "await stream_stats.all_rows_shared()" in overview and "stream_stats.all_rows()" not in overview


def test_the_admin_user_list_still_counts_right(ledger, monkeypatch):
    from src.auth import users as user_store
    from src.dashboard import api
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    monkeypatch.setattr(user_store, "get_all", lambda: [
        {"id": "u1", "username": "nova", "subscription_status": "none"},
        {"id": "u2", "username": "kai", "subscription_status": "none"}])
    monkeypatch.setattr(api, "_streams", {"u1:a": {"user_id": "u1"}, "u1:b": {"user_id": "u1"},
                                         "u2:a": {"user_id": "u2"}})
    monkeypatch.setattr(api, "_clips", {f"c{i}": {"user_id": "u1"} for i in range(5)})
    rows = {u["id"]: u for u in asyncio.run(api.admin_list_users(None))}
    assert (rows["u1"]["stream_count"], rows["u1"]["clip_count"], rows["u1"]["clips_caught"]) == (2, 5, 6)
    assert (rows["u2"]["stream_count"], rows["u2"]["clip_count"], rows["u2"]["clips_caught"]) == (1, 0, 0)
    assert rows["u1"]["clips_approved"] == 1


# ── 6. the profiler ──────────────────────────────────────────────────────────

def test_a_per_channel_job_is_one_row_with_its_total():
    for name in ("recorder-prune-xqc", "audiometer-reader-lacy", "engine-xqc", "profile-lacy",
                 "finish-clip-fca1bde6"):
        kind = lp.task_kind(name)
        assert kind in ("recorder-prune", "audiometer-reader", "engine", "profile", "finish-clip")
        assert "xqc" not in kind and "lacy" not in kind and "fca1" not in kind
    # A name nobody told it about is still summed, and still nameless.
    rows = {"task: newjob-xqc": (1.0, 10, 0.2), "task: newjob-lacy": (2.0, 30, 0.5),
            "task: clip-processor": (0.5, 5, 0.1), "task: lonely-thing": (0.1, 1, 0.1)}
    g = lp.grouped(rows)
    # Seconds, steps, longest; rows without a CPU clock carry none onward.
    assert g["task: newjob-*  (2 of them)"] == (3.0, 40, 0.5, None, None)
    assert "task: newjob-xqc" not in g and g["task: lonely-thing"] == (0.1, 1, 0.1)
    out = lp.render({"seconds": 10.0, "cpu": 0.0, "rows": rows})
    assert "xqc" not in out and "lacy" not in out
    assert " 30.0%" in out and "task: newjob-*  (2 of them)" in out


# ── the audio level: the same number, computed off a Python loop ─────────────
# Prod, 2026-11-18, 57 live: `audiometer-reader` was 8.1% of the one core, and
# most of what the reader does per chunk was squaring samples one at a time in
# a generator. The replacement is exact integer arithmetic, so the dB figure
# is bit for bit what it was. These hold that, on both paths.

def _old_rms_db(data: bytes) -> float:
    """The function as it was, kept here as the reference."""
    import math
    import struct
    n = len(data) // 2
    if n == 0:
        return -100.0
    samples = struct.unpack_from(f"<{n}h", data[:n * 2])
    mean_sq = sum(s * s for s in samples) / n
    if mean_sq < 1.0:
        return -100.0
    return max(-100.0, 20.0 * math.log10(math.sqrt(mean_sq) / 32768.0))


def _pcm_cases():
    import random
    import struct
    rnd = random.Random(20261118)
    cases = [b"", b"\x01", b"\x00\x00", b"\x01\x00", b"\xff\xff", b"\xff\x7f" * 1600,
             b"\x00\x80" * 1600, b"\x00" * 3200, b"\x01\x00" + b"\x00" * 3198]
    for _ in range(1500):
        n = rnd.choice([1, 2, 3, 7, 127, 341, 471, 940, 941, 1600, 3199, 3200, 6400])
        amp = rnd.choice([0, 1, 2, 5, 50, 800, 12000, 32767])
        lo = -32768 if amp == 32767 else -amp
        cases.append(b"".join(struct.pack("<h", rnd.randint(lo, amp)) for _ in range(n // 2))
                     + (b"\x05" if n % 2 else b""))
    return cases


@pytest.mark.parametrize("with_numpy", [True, False])
def test_the_audio_level_is_bit_for_bit_what_it_was(monkeypatch, with_numpy):
    from src.ingestion import audio_meter as am
    if with_numpy:
        if am._np is None:
            pytest.skip("numpy is not installed here")
    else:
        monkeypatch.setattr(am, "_np", None)
    for data in _pcm_cases():
        assert am._rms_db(data) == _old_rms_db(data), (len(data), data[:8])


def test_a_silent_feed_still_reads_exactly_minus_100():
    """CLAUDE.md's trap: -100 is what a WORKING feed returns for genuine
    silence, and things downstream compare against it. It must stay exactly
    that, not -99.99 or -inf."""
    from src.ingestion import audio_meter as am
    assert am._rms_db(b"\x00" * 3200) == -100.0 == am._SILENCE_DB
    assert am._rms_db(b"") == -100.0 and am._rms_db(b"\x7f") == -100.0
    # One count of noise in 1,600 samples is a mean square under 1: silence.
    assert am._rms_db(b"\x01\x00" + b"\x00" * 3198) == -100.0
    # Full scale is 0 dB, give or take the asymmetry of 16 bits.
    assert abs(am._rms_db(b"\xff\x7f" * 1600)) < 0.001


def test_the_sum_of_squares_cannot_overflow_a_chunk():
    from src.ingestion import audio_meter as am
    loudest = b"\x00\x80" * 100_000                    # 12.5 s of full-scale negative
    assert am._sum_squares(loudest, 100_000) == 100_000 * 32768 * 32768


# ── the ledger scans, in a process of their own ──────────────────────────────
# Prod, 2026-11-18, 59 live. The scans had been moved to a thread and the loop
# still lost 13 of its 57 busy points waiting: Python runs one thread at a
# time, so a busy thread makes the loop wait after every socket read. A
# process has its own interpreter. These hold that the answer is the same one,
# that one run answers both readings, and that every way it can fail ends in
# the thread doing the job as before.

def _ledger(path, rows=400):
    with path.open("w", encoding="utf-8") as f:
        for i in range(rows):
            uid, ch = f"u{i % 7}", f"c{i % 3}"
            ev = ("caught", "approved", "rejected", "expired", "missed", "undone", "cleared")[i % 7]
            f.write(json.dumps({"ts": 1.76e9 + i * 37.5, "event": ev, "user_id": uid,
                                "channel": ch, "clip_id": f"clip{i // 2}",
                                "clip_at": round(1.76e9 + i * 37.5 - 12.3, 1)}) + "\n")
        f.write("this line is not json\n")


@pytest.fixture
def big_ledger(tmp_path, monkeypatch):
    path = tmp_path / "stream_stats.jsonl"
    _ledger(path)
    monkeypatch.setattr(stream_stats, "_LOG_FILE", path)
    monkeypatch.setattr(stream_stats, "_CHILD_MIN_BYTES", 1)
    stream_stats.forget_scans()
    return path


def test_the_process_gives_the_answer_the_functions_give(big_ledger):
    want_rows, want_totals = stream_stats.all_rows(), stream_stats.totals_by_user()
    assert len(want_rows) == 21 and len(want_totals) == 7

    async def go():
        return await stream_stats.all_rows_shared(), await stream_stats.totals_by_user_shared()
    rows, totals = asyncio.run(go())
    # Exactly, through JSON: every number, every session, the same order.
    assert rows == json.loads(json.dumps(want_rows))
    assert totals == json.loads(json.dumps(want_totals))
    assert [r["user_id"] + r["channel"] for r in rows] == [r["user_id"] + r["channel"] for r in want_rows]


def test_the_parse_does_not_run_in_the_apps_own_process(big_ledger, monkeypatch):
    def not_here():
        raise AssertionError("the ledger was parsed in the app's process")
    monkeypatch.setattr(stream_stats, "all_rows", not_here)
    monkeypatch.setattr(stream_stats, "totals_by_user", not_here)
    rows = asyncio.run(stream_stats.all_rows_shared())
    assert len(rows) == 21


def test_one_run_answers_both_readings_and_everyone_waiting(big_ledger, monkeypatch):
    runs = []
    real = stream_stats._scan_in_child

    async def counted():
        runs.append(1)
        return await real()
    monkeypatch.setattr(stream_stats, "_scan_in_child", counted)

    async def go():
        got = await asyncio.gather(stream_stats.all_rows_shared(), stream_stats.totals_by_user_shared(),
                                   stream_stats.all_rows_shared(), stream_stats.totals_by_user_shared())
        again = await stream_stats.totals_by_user_shared()
        return got, again
    got, again = asyncio.run(go())
    assert runs == [1]
    assert len(got[0]) == 21 and len(got[1]) == 7 and again == got[1]


def test_a_small_ledger_is_not_worth_a_process(tmp_path, monkeypatch):
    path = tmp_path / "stream_stats.jsonl"
    _ledger(path, rows=30)
    monkeypatch.setattr(stream_stats, "_LOG_FILE", path)
    assert path.stat().st_size < stream_stats._CHILD_MIN_BYTES

    async def never():
        raise AssertionError("a process was started for a few kilobytes")
    monkeypatch.setattr(stream_stats, "_scan_in_child", never)
    stream_stats.forget_scans()
    assert len(asyncio.run(stream_stats.all_rows_shared())) == 21


@pytest.mark.parametrize("breakage", ["exits 3", "prints nonsense", "cannot start", "too slow"])
def test_however_the_process_fails_the_thread_does_the_job(big_ledger, monkeypatch, breakage):
    import sys
    code = {"exits 3": "import sys; sys.exit(3)",
            "prints nonsense": "print('@@ledger-scan@@{not json')",
            "cannot start": "",
            "too slow": "import time; time.sleep(30)"}[breakage]
    real = asyncio.create_subprocess_exec
    started = []

    async def fake(*args, **kw):
        if breakage == "cannot start":
            raise FileNotFoundError("no interpreter")
        proc = await real(sys.executable, "-c", code, **{k: v for k, v in kw.items() if k != "cwd"})
        started.append(proc)
        return proc
    monkeypatch.setattr(stream_stats.asyncio, "create_subprocess_exec", fake)
    monkeypatch.setattr(stream_stats, "_CHILD_TIMEOUT_S", 0.5)
    said = []
    monkeypatch.setattr(stream_stats.log, "warning", lambda ev, **kw: said.append(ev))

    async def go():
        rows = await stream_stats.all_rows_shared()
        for p in started:                               # nothing is left running
            await asyncio.wait_for(p.wait(), 5)
        return rows
    rows = asyncio.run(go())
    assert len(rows) == 21
    assert said == ["ledger_scan_child_failed"]


def test_the_scan_script_prints_one_answer_after_its_marker(big_ledger):
    import subprocess
    import sys
    from src.stats import ledger_scan
    out = subprocess.run([sys.executable, "-m", "src.stats.ledger_scan", str(big_ledger)],
                         capture_output=True, timeout=60, check=True).stdout.decode()
    assert out.count(ledger_scan.MARKER) == 1
    got = json.loads(out.split(ledger_scan.MARKER, 1)[1])
    assert set(got) == {"rows", "totals"} and len(got["rows"]) == 21
    # It imports the ledger and nothing of the app.
    src = inspect.getsource(ledger_scan)
    assert "dashboard" not in src.split('"""', 2)[2] and "src.main" not in src


def test_it_refuses_to_guess_which_file():
    from src.stats import ledger_scan
    assert ledger_scan.main([]) == 2


# ── the sentiment word list is read once, not per channel per pass ───────────

def test_the_shared_analyzer_scores_exactly_as_a_fresh_one():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    from src.ingestion import stream_worker as sw
    msgs = ["LETS GOOOO that was insane", "lol", "no way he hit that", "boring stream tbh",
            "W", "clip it clip it!!!", "", "😂😂😂", "I hate this so much", "not bad :)",
            "KEKW OMEGALUL", "that was NOT good", "best play i have ever seen!!!"]
    shared = sw._sentiment()
    assert sw._sentiment() is shared                    # one for the process
    for _ in range(2):                                  # and using it changes nothing
        fresh = SentimentIntensityAnalyzer()
        for m in msgs:
            assert shared.polarity_scores(m) == fresh.polarity_scores(m), m


def test_the_profile_loop_does_not_build_one_per_pass():
    from src.ingestion import stream_worker as sw
    loop_src = inspect.getsource(sw.StreamWorker._profile_update_loop)
    assert "SentimentIntensityAnalyzer(" not in loop_src and "_sentiment()" in loop_src
