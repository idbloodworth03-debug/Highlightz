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
