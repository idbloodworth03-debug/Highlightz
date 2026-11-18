"""scripts/stream_cost.py: the measurement the live ceiling is decided from.

The owner resized the droplet on 2026-11-13 and the next question is "how many
live channels can it carry". This script answers it from the running box, and
the answer was wrong in a way that only mattered now:

  * It counted LIVE channels as "streamlink processes running". Since video
    capture, a live channel runs TWO (the audio meter's and the recorder's),
    so it reported twice the channels at half the cost each, and a ceiling
    twice what the box could take.
  * It had no idea that the app's own work (chat scoring, Kick webhooks,
    sockets, the dashboard) is ONE thread on ONE core. More cores carry more
    streamlink and ffmpeg processes and do nothing for that thread, so on a
    bigger box it is the limit that binds, and it was not measured at all.
"""

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "stream_cost.py"
spec = importlib.util.spec_from_file_location("stream_cost", _PATH)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

MB = 1048576
PY = "/opt/highlightz/venv/bin/python"
URL = "https://playlist.example/hls/x.m3u8?token=SECRET"

METER_SL = [PY, "/opt/highlightz/venv/bin/streamlink", "--stdout", "--loglevel",
            "warning", URL, "audio_only,worst", ""]
REC_SL = [PY, "/opt/highlightz/venv/bin/streamlink", "--stdout", "--loglevel",
          "warning", URL, "720p60,720p,best", ""]
METER_FF = ["ffmpeg", "-hide_banner", "-nostats", "-i", "pipe:0", "-vn", "-ac", "1",
            "-ar", "8000", "-f", "s16le", "pipe:1", ""]
REC_FF = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-i", "pipe:0",
          "-c", "copy", "-f", "segment", "-segment_time", "2", "seg-%d.ts", ""]
VOD_SL = [PY, "/opt/highlightz/venv/bin/streamlink", "--stdout", "--loglevel", "error",
          "https://www.twitch.tv/videos/123", "audio_only,worst", ""]
FETCH_SL = [PY, "/opt/highlightz/venv/bin/streamlink", "--loglevel", "warning", "--force",
            "-o", "/opt/highlightz/clips/clipfiles/x.fetch.part", URL, "best", ""]
CUT_FF = ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", ""]


@pytest.mark.parametrize("name, argv, kind", [
    ("streamlink", METER_SL, "meter_streamlink"),
    ("streamlink", REC_SL, "recorder_streamlink"),
    ("python3.11", METER_SL, "meter_streamlink"),      # comm can be the interpreter
    ("ffmpeg", METER_FF, "meter_ffmpeg"),
    ("ffmpeg", REC_FF, "recorder_ffmpeg"),
    ("ffmpeg", CUT_FF, "other_ffmpeg"),                 # a cut or a render is not a live channel
    ("streamlink", VOD_SL, "other_streamlink"),          # a VOD scan is not a live channel
    ("streamlink", FETCH_SL, "other_streamlink"),        # nor is fetching a clip's video
    ("streamlink", [], "other_streamlink"),              # unreadable: never guessed to be live
    ("python", [PY, "-m", "src.main", ""], "app"),
    ("python", [PY, "scripts/stream_cost.py", ""], "other_python"),
    ("nginx", ["nginx: worker process", ""], "other"),
    ("redis-server", [], "other"),
])
def test_each_process_is_told_apart_by_what_it_was_started_to_do(name, argv, kind):
    assert C.classify(name, argv) == kind


def test_a_vod_scan_does_not_add_a_live_channel():
    """Nine live and one scan running is nine live."""
    tot = dict(_box(9), other_streamlink=(1, 40 * MB, 6.0))
    assert C.live_channels(tot) == 9
    assert C.per_live(tot, 9) == C.per_live(_box(9), 9)


def test_the_four_places_that_start_a_streamlink_still_look_the_way_this_reads_them():
    """The script tells them apart by their arguments. If one of these call
    sites changes, the measurement silently miscounts, so the sources are held
    to what `classify` relies on."""
    import re
    root = _PATH.parent.parent / "src"

    def call(path):
        src = (root / path).read_text()
        at = src.index("settings.streamlink_path")
        return re.sub(r"\s+", " ", src[at:at + 260])

    meter, recorder = call("ingestion/audio_meter.py"), call("ingestion/clip_recorder.py")
    vod, fetch = call("vod/vod_audio.py"), call("clips/fetch.py")
    assert '"--stdout", "--loglevel", "warning", self.stream_url, "audio_only,worst"' in meter
    assert '"--stdout", "--loglevel", "warning", self.stream_url, settings.clip_capture_quality' in recorder
    assert '"--stdout", "--loglevel", "error", vod_url, "audio_only,worst"' in vod
    assert '"--stdout"' not in fetch and '"-o"' in fetch
    # ...and nothing else in src/ starts one.
    starters = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                      if "settings.streamlink_path" in p.read_text())
    assert starters == ["clips/fetch.py", "ingestion/audio_meter.py",
                        "ingestion/clip_recorder.py", "vod/vod_audio.py"]


def _box(live, *, capture=True, sl=30 * MB, ff=12 * MB):
    """What `totals` returns on a box with `live` channels live."""
    tot = {"app": (1, 400 * MB, 18.0),
           "meter_streamlink": (live, live * sl, live * 1.0),
           "meter_ffmpeg": (live, live * ff, live * 0.5)}
    if capture:
        tot["recorder_streamlink"] = (live, live * sl, live * 2.0)
        tot["recorder_ffmpeg"] = (live, live * ff, live * 0.5)
    return tot


def test_nine_live_channels_with_capture_on_are_nine_not_eighteen():
    """THE BUG. 9 live = 18 streamlink processes; the old count said 18."""
    tot = _box(9)
    assert tot["meter_streamlink"][0] + tot["recorder_streamlink"][0] == 18
    assert C.live_channels(tot) == 9


def test_a_live_channel_is_charged_for_all_four_of_its_processes():
    mem_each, cpu_each = C.per_live(_box(9), 9)
    assert mem_each == 2 * 30 * MB + 2 * 12 * MB          # two streamlinks, two ffmpegs
    assert cpu_each == pytest.approx(4.0)
    # A render running at the same time is not a live channel's cost.
    with_render = dict(_box(9), other_ffmpeg=(1, 300 * MB, 380.0))
    assert C.per_live(with_render, 9) == (mem_each, cpu_each)


def test_capture_off_still_counts_and_nothing_live_measures_nothing():
    assert C.live_channels(_box(5, capture=False)) == 5
    assert C.live_channels({"app": (1, 400 * MB, 3.0)}) == 0
    assert C.per_live({"app": (1, 400 * MB, 3.0)}, 0) == (0.0, 0.0)
    only_rec = {"recorder_streamlink": (4, 1, 1.0), "recorder_ffmpeg": (4, 1, 1.0)}
    assert C.live_channels(only_rec) == 4


def _room(**kw):
    base = dict(live=10, cores=8, mem_available=12 * 1024 * MB, mem_total=16 * 1024 * MB,
                mem_each=84 * MB, cpu_each=4.0, app_cpu=20.0, loop_cpu=15.0)
    base.update(kw)
    return C.room(**base)


def test_more_cores_raise_the_cpu_room_and_not_the_loops():
    """The reason a 16 GB box is not simply "four times the channels"."""
    small, big = _room(cores=2), _room(cores=8)
    assert big["by_cpu"] == 4 * small["by_cpu"]
    assert big["by_loop"] == small["by_loop"] == int(10 * 70 / 15)        # 46
    assert small["binding"] == "CPU" and small["safe"] == 25              # 150 / (4 + 2)
    assert big["binding"] == "the app's one loop" and big["safe"] == 46


def test_the_tightest_limit_is_the_answer():
    r = _room(mem_available=2 * 1024 * MB)            # 2 GB free of 16
    assert r["binding"] == "memory"
    assert r["safe"] == r["by_mem"] == 10 + int((2048 - 1638.4) * MB / (84 * MB))
    assert r["safe"] <= r["by_cpu"] and r["safe"] <= r["by_loop"]


def test_an_idle_loop_is_not_scaled_into_a_limit():
    """1% of a core over ten seconds is noise. Scaling it would print a made-up
    number in either direction, so it is left out and the others decide."""
    r = _room(loop_cpu=1.0)
    assert r["by_loop"] is None and r["binding"] in ("CPU", "memory")


def test_memory_keeps_a_reserve_that_grows_with_the_box():
    assert _room(mem_total=3891 * MB)["reserve"] == 400 * MB              # the old droplet
    assert _room()["reserve"] == pytest.approx(0.10 * 16 * 1024 * MB)     # 1.6 GB of 16


def test_nothing_measurable_gives_no_number_at_all():
    for kw in (dict(live=0), dict(cpu_each=0.0), dict(mem_each=0.0)):
        r = _room(**kw)
        assert r["safe"] is None and r["binding"] is None


@pytest.mark.parametrize("kw, starts, also", [
    (dict(live=30, cap=48, safe=60, production=False), "NOT PRODUCTION", None),
    (dict(live=4, cap=48, safe=200, production=True), "TOO THIN TO DECIDE", None),
    (dict(live=30, cap=48, safe=60, production=True), "RAISING IT LOOKS SUPPORTED. 48 -> 60", None),
    (dict(live=20, cap=48, safe=33, production=True), "THE LIMIT IS HIGHER THAN THIS SAMPLE SUPPORTS",
     "MAX_CONCURRENT_STREAMS=33"),
    (dict(live=40, cap=48, safe=33, production=True), "THE LIMIT IS HIGHER THAN THIS SAMPLE SUPPORTS",
     "40 ARE LIVE NOW"),
    (dict(live=30, cap=48, safe=48, production=True), "THE LIMIT FITS", None),
])
def test_the_verdict(kw, starts, also):
    text = "\n".join(C.verdict(**kw))
    assert text.startswith(starts)
    if also:
        assert also in text


def test_a_thin_or_foreign_sample_never_recommends_a_number():
    """The two refusals outrank everything: neither may name a new limit."""
    for kw in (dict(live=2, cap=12, safe=90, production=True),
               dict(live=30, cap=12, safe=90, production=False)):
        text = "\n".join(C.verdict(**kw))
        assert "90" not in text and "MAX_CONCURRENT_STREAMS" not in text


def test_cpu_is_a_rate_between_two_readings():
    ticks = C.TICKS
    a = {1: ("app", 400 * MB, 1000, 900), 2: ("meter_streamlink", 30 * MB, 500, 0),
         3: ("meter_ffmpeg", 12 * MB, 100, 0)}
    b = {1: ("app", 410 * MB, 1000 + 3 * ticks, 900 + 2 * ticks),        # 30% / 20% over 10 s
         2: ("meter_streamlink", 30 * MB, 500 + ticks, 0),               # 10%
         3: ("other_ffmpeg", 12 * MB, 5, 0),                             # pid reused: not a rate
         4: ("recorder_ffmpeg", 12 * MB, 9999, 0)}                       # started mid-sample
    tot, loop = C.totals(a, b, 10.0)
    assert tot["app"] == (1, 410 * MB, pytest.approx(30.0))
    assert loop == pytest.approx(20.0), "the loop is the main thread, not every thread"
    assert tot["meter_streamlink"][2] == pytest.approx(10.0)
    assert "other_ffmpeg" not in tot and "recorder_ffmpeg" not in tot


def test_it_reads_this_machine_without_falling_over():
    """/proc for real: this test process is a python that is not the app."""
    import os
    seen = C.procs()
    assert os.getpid() in seen and seen[os.getpid()][0] == "other_python"
    assert seen[os.getpid()][1] > 0


def test_a_stream_url_can_never_reach_the_output():
    """A playlist URL carries a signed token. The script classifies command
    lines and prints only counts."""
    src = _PATH.read_text()
    printing = src[src.index("def main("):src.index('if __name__ == "__main__":')]
    assert "print(" in printing
    assert "argv" not in printing and "cmdline" not in printing


# ── THE SPLIT: several loops, each found and measured (2026-11-18) ───────────
# With INGEST_WORKERS the app is a web process and N ingest workers, each with
# a loop on a core of its own. Before this the script filed the workers under
# "other python" and measured one loop, the idle one.

WORKER = [PY, "-m", "src.ingestion.worker_main", "--fd", "7", "--index", "2", "--count", "4", ""]


def test_an_ingest_worker_is_the_app_not_some_other_python():
    assert C.classify("python", WORKER) == "worker"
    assert C.worker_index(WORKER) == 2
    assert C.worker_index([PY, "-m", "src.ingestion.worker_main", ""]) == -1
    assert C.worker_index([PY, "-m", "src.ingestion.worker_main", "--index"]) == -1


def _snap(shift=0):
    """A web process (pid 1), workers 0 and 1 (pids 10, 11). Worker 0 runs
    three channels, worker 1 one (started through a shell, pid 50), the web
    process one. Every channel also has a recorder."""
    t = C.TICKS
    rows = {1: ("app", 400 * MB, 1000 + 2 * t * shift, 900 + 1 * t * shift, 0, -1),
            10: ("worker", 90 * MB, 500 + 5 * t * shift, 400 + 4 * t * shift, 1, 0),
            11: ("worker", 70 * MB, 300 + 2 * t * shift, 200 + 1 * t * shift, 1, 1),
            50: ("other", MB, 0, 0, 11, -1)}
    pid = 100
    for parent, n in ((10, 3), (50, 1), (1, 1)):
        for _ in range(n):
            rows[pid] = ("meter_streamlink", 30 * MB, 10, 0, parent, -1)
            rows[pid + 1] = ("recorder_streamlink", 30 * MB, 10, 0, parent, -1)
            rows[pid + 2] = ("meter_ffmpeg", 12 * MB, 10, 0, parent, -1)
            pid += 3
    return rows


def test_each_loop_is_measured_with_the_channels_it_is_running():
    found = C.loops(_snap(0), _snap(1), 10.0)
    assert [(v["kind"], v["index"]) for v in found] == [("app", -1), ("worker", 0), ("worker", 1)]
    web, w0, w1 = found
    assert (web["loop"], w0["loop"], w1["loop"]) == pytest.approx((10.0, 40.0, 10.0))
    assert (web["cpu"], w0["cpu"]) == pytest.approx((20.0, 50.0))      # every thread, not the loop
    # A channel is its meter's streamlink: never the meter and the recorder
    # added together, and the one started through a shell is still worker 1's.
    assert (web["live"], w0["live"], w1["live"]) == (1, 3, 1)
    assert w0["rss"] == 90 * MB


def test_a_box_without_the_meter_counts_recorders_and_a_stray_is_nobodys():
    a, b = _snap(0), _snap(1)
    for snap in (a, b):
        for pid in [p for p, r in snap.items() if r[0] == "meter_streamlink"]:
            del snap[pid]
        snap[900] = ("recorder_streamlink", MB, 1, 0, 4242, -1)        # parent not in the list
    assert [v["live"] for v in C.loops(a, b, 10.0)] == [1, 3, 1]


def test_old_four_field_rows_still_total():
    """`totals` reads the first four fields, whichever length the row is."""
    tot, loop = C.totals(_snap(0), _snap(1), 10.0)
    assert tot["app"][2] == pytest.approx(20.0) and loop == pytest.approx(10.0)
    assert tot["worker"] == (2, 160 * MB, pytest.approx(70.0))


def _loops(web, *workers):
    return ([{"kind": "app", "index": -1, "live": web[0], "loop": web[1]}]
            + [{"kind": "worker", "index": i, "live": n, "loop": pct}
               for i, (n, pct) in enumerate(workers)])


def test_one_process_is_the_figure_it_always_was():
    assert C.loop_room(_loops((10, 15.0)), 10) == (46, "the app's one loop")
    assert C.loop_room(_loops((10, 1.0)), 10) == (None, "")


def test_with_every_channel_in_a_worker_the_busiest_worker_is_the_limit():
    """Four workers, channels dealt about evenly: the site is full when the
    busiest is. 20 channels at 40% carries 35; four of those is 140."""
    found = _loops((0, 3.0), (20, 40.0), (18, 30.0), (22, 33.0), (19, 31.0))
    assert C.loop_room(found, 79) == (140, "the busiest worker's loop")
    # One more worker is one more loop: the same channels over five.
    assert C.loop_room(found + [{"kind": "worker", "index": 4, "live": 0, "loop": 0.5}], 79)[0] == 175


def test_the_web_loop_still_has_a_limit_of_its_own_when_it_relays_for_everybody():
    """It does a little for every live channel. If that is what fills first,
    more workers do not help, and the script has to say which it is."""
    found = _loops((0, 35.0), (20, 20.0), (20, 20.0))
    assert C.loop_room(found, 40) == (80, "the web process's loop")     # 40 x 70 / 35, under 2 x 70


def test_a_trial_on_a_few_channels_is_still_limited_by_the_web_loop():
    """INGEST_ONLY: new channels land in the web process, so its loop is the
    one that fills; the trial's own channels are added as they are."""
    assert C.loop_room(_loops((50, 40.0), (2, 3.0)), 52) == (87 + 2, "the web process's loop")
    assert C.loop_room(_loops((50, 1.0), (2, 3.0)), 52) == (None, "")


def test_idle_workers_are_not_scaled_into_a_limit():
    assert C.loop_room(_loops((0, 0.5), (1, 1.0), (0, 0.2)), 1) == (None, "")
    assert C.loop_room([{"kind": "worker", "index": 0, "live": 5, "loop": 50.0}], 5) == (None, "")


def test_the_answer_names_the_loop_that_fills_first():
    found = _loops((0, 3.0), (5, 60.0), (5, 20.0))
    r = _room(live=10, cores=32, mem_available=60 * 1024 * MB, mem_total=64 * 1024 * MB,
              loop_cpu=3.0, found=found)
    assert r["by_loop"] == 11                       # 2 workers x (5 x 70 / 60), rounded down
    assert r["binding"] == "the busiest worker's loop" and r["safe"] == r["by_loop"]
    # Without a worker in the list nothing about the old answer moves.
    plain = _room(loop_cpu=15.0, found=_loops((10, 15.0)))
    assert plain["by_loop"] == 46 and plain["loop_name"] == "the app's one loop"


def test_a_real_worker_and_its_channel_are_found_in_proc(tmp_path):
    """/proc for real: a process started as an ingest worker, with a child
    that looks like a meter's streamlink. Its index and its channel are read
    from what the kernel says, nothing else."""
    import os
    import subprocess
    import sys
    import time as _time
    fake_sl = tmp_path / "streamlink"
    os.symlink(sys.executable, fake_sl)
    child = ("import subprocess, sys, time\n"
             f"p = subprocess.Popen([{str(fake_sl)!r}, '-c', 'import time; time.sleep(30)',\n"
             "                      '--stdout', '--loglevel', 'warning', 'URL', 'audio_only,worst'])\n"
             "print('up', flush=True)\n"
             "time.sleep(30)\n")
    worker = subprocess.Popen([sys.executable, "-c", child, "src.ingestion.worker_main",
                               "--index", "3", "--count", "4"], stdout=subprocess.PIPE)
    try:
        assert worker.stdout.readline().strip() == b"up"
        end = _time.monotonic() + 10
        while _time.monotonic() < end:
            a = C.procs()
            mine = [r for p, r in a.items() if r[0] == "meter_streamlink" and r[4] == worker.pid]
            if mine:
                break
            _time.sleep(0.05)
        assert a[worker.pid][0] == "worker" and a[worker.pid][5] == 3
        assert a[worker.pid][4] == os.getpid()                          # its parent is this test
        found = [v for v in C.loops(a, C.procs(), 1.0) if v["index"] == 3]
        assert len(found) == 1 and found[0]["live"] == 1 and found[0]["rss"] > 0
    finally:
        subprocess.run(["pkill", "-P", str(worker.pid)], check=False)
        worker.kill()
        worker.wait()


def test_the_output_has_a_line_per_loop_and_still_no_command_line():
    src = _PATH.read_text()
    printing = src[src.index("def main("):src.index('if __name__ == "__main__":')]
    assert '("worker", "the app: workers")' in printing
    assert "THE APP'S LOOPS" in printing and 'f"worker {v[\'index\']}"' in printing
    assert "the fullest loop" in printing
    assert "MORE WORKERS RAISE THIS" in printing and "more workers do not raise this" in printing
    assert "argv" not in printing and "cmdline" not in printing
