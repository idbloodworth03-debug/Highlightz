"""
Clip capture — the rolling video buffer.

This is the feature that crossed the product's oldest architectural line: for
most of its life Highlightz held no stream video at all. Crossing it was a
deliberate decision (the user needs a FILE to download, edit and schedule), so
what these tests pin is not "does it record" but the four properties that stop
recording from being reckless:

  * it never records a broadcaster who has opted out;
  * it never runs at all while the release flag is off;
  * the buffer is a bounded ring, not an archive;
  * two users watching one channel share one recorder, not two pulls.

The window arithmetic is tested directly rather than through ffmpeg. The part
that silently goes wrong is which segments get chosen and where the cut starts
inside them; the subprocess call either works or fails loudly.
"""

import asyncio
from pathlib import Path

import pytest

from config.settings import settings
from src.ingestion import clip_recorder as rec


@pytest.fixture(autouse=True)
def isolated_capture(tmp_path, monkeypatch):
    """Point the ring at a scratch dir and switch capture on for the test."""
    monkeypatch.setattr(rec, "_ROOT", tmp_path / "capture")
    monkeypatch.setattr(settings, "clip_capture_enabled", True)
    monkeypatch.setattr(settings, "clip_capture_segment_s", 2)
    monkeypatch.setattr(settings, "clip_capture_buffer_s", 60)
    rec._recorders.clear()
    rec._refs.clear()
    yield tmp_path
    rec._recorders.clear()
    rec._refs.clear()


def _segs(count: int, first_end: float = 1000.0, step: float = 2.0):
    """Fake ring contents: (path, finished_at) oldest first."""
    return [(Path(f"/seg/{i}.ts"), first_end + i * step) for i in range(count)]


# ── the window arithmetic ────────────────────────────────────────────────────

def test_the_cut_takes_every_overlapping_segment_plus_one_in_front():
    """A copy can only start at a keyframe. Starting from the segment BEFORE
    the wanted moment is what guarantees the moment is inside the copied range
    rather than a fraction before its first keyframe."""
    segs = _segs(10)                       # ends at 1000,1002,...,1018
    picked, offset = rec.select_segments(segs, 1007.0, 1011.0, 2.0)
    # Overlapping: the segments ending 1008, 1010, 1012. Plus the one before.
    assert [p.name for p in picked] == ["3.ts", "4.ts", "5.ts", "6.ts"]
    # First chosen spans (1004, 1006]; the moment sits 3s into it.
    assert offset == pytest.approx(3.0)


def test_a_window_older_than_the_ring_selects_nothing():
    """The ring is short by design. Asking for something that has already been
    pruned must come back empty rather than silently returning the oldest
    thing on disk, which would be a clip of the wrong moment."""
    picked, offset = rec.select_segments(_segs(5), 100.0, 110.0, 2.0)
    assert picked == [] and offset == 0.0


def test_a_window_newer_than_the_ring_selects_nothing():
    picked, _ = rec.select_segments(_segs(5), 5000.0, 5010.0, 2.0)
    assert picked == []


def test_the_offset_never_goes_negative():
    """A window starting before the first available segment clamps to its
    start. A negative -ss would make ffmpeg reject the cut outright."""
    segs = _segs(4, first_end=1000.0)      # first spans (998, 1000]
    picked, offset = rec.select_segments(segs, 990.0, 1003.0, 2.0)
    assert picked and offset == 0.0


def test_an_empty_ring_selects_nothing():
    assert rec.select_segments([], 10.0, 20.0, 2.0) == ([], 0.0)


# ── the ring is bounded ──────────────────────────────────────────────────────

def test_the_newest_segment_is_never_offered_to_a_cut(isolated_capture):
    """ffmpeg is still appending to the newest file, so its mtime is not its
    end time and its tail is not readable. Splicing it produces a truncated
    clip, which is worse than a slightly shorter one."""
    r = rec.ClipRecorder("novafps", "https://twitch.tv/novafps")
    r.dir.mkdir(parents=True)
    for i in range(3):
        p = r.dir / f"seg-{i:05d}.ts"
        p.write_bytes(b"x" * 100)
    names = [p.name for p, _ in r.segments()]
    assert len(names) == 2, "the in-flight segment is being handed to cuts"
    assert "seg-00002.ts" not in names


def test_pruning_keeps_the_ring_at_the_configured_window(isolated_capture, monkeypatch):
    monkeypatch.setattr(settings, "clip_capture_buffer_s", 20)   # 10 segments at 2s
    r = rec.ClipRecorder("novafps", "https://twitch.tv/novafps")
    r.dir.mkdir(parents=True)
    for i in range(60):
        (r.dir / f"seg-{i:05d}.ts").write_bytes(b"x" * 10)
    r._prune_once()
    left = sorted(p.name for p in r.dir.glob("*.ts"))
    assert len(left) <= 20, f"ring is unbounded: {len(left)} segments kept"
    # What survives is the NEWEST end of the ring — a clip is cut from the
    # recent past, so dropping the new end would make the feature useless.
    assert left[-1] == "seg-00059.ts"


def test_stopping_a_recorder_wipes_its_buffer(isolated_capture):
    """The buffer's only justification is serving a clip from a stream being
    monitored right now. Once monitoring stops it is video of somebody we have
    no reason to hold, so it goes immediately rather than aging out."""
    r = rec.ClipRecorder("novafps", "https://twitch.tv/novafps")
    r.dir.mkdir(parents=True)
    (r.dir / "seg-00000.ts").write_bytes(b"x" * 10)
    asyncio.run(r.stop())
    assert not r.dir.exists()


# ── the guards on ever starting ──────────────────────────────────────────────

def test_capture_does_not_run_while_the_release_flag_is_off(monkeypatch):
    monkeypatch.setattr(settings, "clip_capture_enabled", False)
    assert asyncio.run(rec.ensure("novafps", "https://twitch.tv/novafps")) is None
    assert rec._recorders == {}


def test_an_opted_out_broadcaster_is_never_recorded(monkeypatch):
    """Checked BEFORE the first byte, not at serve time. A check that only
    hides the result still means their video sat on our disk."""
    from src.auth import optout
    monkeypatch.setattr(optout, "is_opted_out", lambda ch: ch == "privatestreamer")
    assert asyncio.run(rec.ensure("privatestreamer", "https://twitch.tv/x")) is None
    assert rec._recorders == {}


def test_the_optout_check_runs_before_the_pipeline_is_built():
    """Pins the ORDER, not just the presence of a check: an opt-out consulted
    after start() would already have pulled video."""
    import inspect
    src = inspect.getsource(rec.ensure)
    assert src.index("is_opted_out") < src.index("rec.start()"), \
        "the opt-out is consulted after the recorder starts"


# ── one channel, one pull ────────────────────────────────────────────────────

def test_two_users_on_one_channel_share_a_single_recorder(monkeypatch):
    """Two people monitoring the same streamer must not mean two pulls of the
    same video — that doubles the bandwidth bill for no gain."""
    started = []
    monkeypatch.setattr(rec.ClipRecorder, "start",
                        lambda self: started.append(self.channel) or asyncio.sleep(0))

    async def go():
        a = await rec.ensure("novafps", "https://twitch.tv/novafps")
        b = await rec.ensure("novafps", "https://twitch.tv/novafps")
        return a, b

    a, b = asyncio.run(go())
    assert a is b, "a second viewer got their own recorder"
    assert started == ["novafps"], f"the stream was pulled {len(started)} times"
    assert rec._refs["novafps"] == 2


def test_the_recorder_stops_only_when_the_last_holder_leaves(monkeypatch):
    stopped = []
    monkeypatch.setattr(rec.ClipRecorder, "start", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(rec.ClipRecorder, "stop",
                        lambda self, wipe=True: stopped.append(self.channel) or asyncio.sleep(0))

    async def go():
        await rec.ensure("novafps", "https://twitch.tv/novafps")
        await rec.ensure("novafps", "https://twitch.tv/novafps")
        await rec.release("novafps")
        first = list(stopped)
        await rec.release("novafps")
        return first, list(stopped)

    after_one, after_both = asyncio.run(go())
    assert after_one == [], "the recorder stopped while someone was still watching"
    assert after_both == ["novafps"]
    assert rec._recorders == {}


# ── paths are never built from a channel name ────────────────────────────────

@pytest.mark.parametrize("nasty", [
    "../../etc/cron.d/x", "..", "/absolute", "a/b", "chan;rm -rf /", "chan\x00",
])
def test_a_channel_name_cannot_walk_out_of_the_capture_root(isolated_capture, nasty):
    """Channel names arrive from an 'add this stream' request, so they are
    attacker-controlled. If one ever became a path component, capture would be
    a remote write primitive."""
    d = rec.buffer_dir(nasty)
    assert rec._ROOT.resolve() in d.resolve().parents or d.resolve().parent == rec._ROOT.resolve()
    assert ".." not in d.parts


def test_an_empty_channel_name_still_yields_a_safe_directory(isolated_capture):
    assert rec.buffer_dir("").parent == rec._ROOT


# ── what we actually hand ffmpeg ─────────────────────────────────────────────
#
# The cut itself is one subprocess call, so what can go wrong in OUR code is
# the command line: the wrong segments, the wrong offset, a transcode slipped
# in, or a failure that leaves rubbish behind. A stub binary records argv and
# lets all of that be asserted without a video pipeline — which also keeps the
# suite hermetic, since ffmpeg is not guaranteed to exist wherever this runs.

def _stub_ffmpeg(tmp_path, exit_code=0, write_output=True):
    """A fake ffmpeg that records its argv and optionally writes its output."""
    argv_log = tmp_path / "argv.txt"
    script = tmp_path / "fake-ffmpeg"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, pathlib\n"
        f"pathlib.Path({str(argv_log)!r}).write_text(chr(10).join(sys.argv[1:]))\n"
        + (f"open(sys.argv[-1],'wb').write(b'0'*2048)\n" if write_output else "")
        + f"sys.exit({exit_code})\n"
    )
    script.chmod(0o755)
    return script, argv_log


def _ring(r, count, first_end, step=2.0):
    r.dir.mkdir(parents=True, exist_ok=True)
    import os
    for i in range(count):
        p = r.dir / f"seg-{i:05d}.ts"
        p.write_bytes(b"x" * 512)
        end = first_end + i * step
        os.utime(p, (end, end))


def test_the_cut_command_splices_the_right_segments_and_never_transcodes(
        isolated_capture, monkeypatch):
    tmp = isolated_capture
    script, argv_log = _stub_ffmpeg(tmp)
    monkeypatch.setattr(settings, "ffmpeg_path", str(script))
    r = rec.ClipRecorder("novafps", "u")
    _ring(r, 10, first_end=1000.0)          # ends 1000..1018, newest excluded

    out = tmp / "clip.mp4"
    got = asyncio.run(r.cut(1007.0, 1011.0, out))
    assert got == out and out.exists()

    argv = argv_log.read_text().splitlines()
    # Stream copy, both tracks, no encoder anywhere.
    assert "copy" in argv, "the cut is transcoding — that would starve detection"
    assert not any(a.startswith("libx264") or a == "-crf" for a in argv)
    # The window, biased to start early (see select_segments).
    assert argv[argv.index("-ss") + 1] == "3.000"
    assert argv[argv.index("-t") + 1] == "4.000"
    # Faststart, because the file goes to a phone and a social upload.
    assert "+faststart" in argv


def test_the_concat_list_names_the_chosen_segments_in_order(isolated_capture, monkeypatch):
    tmp = isolated_capture
    seen = tmp / "seen.txt"
    script = tmp / "peek-ffmpeg"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, pathlib\n"
        "i = sys.argv.index('-i')\n"
        f"pathlib.Path({str(seen)!r}).write_text(pathlib.Path(sys.argv[i+1]).read_text())\n"
        "open(sys.argv[-1],'wb').write(b'0'*2048)\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(settings, "ffmpeg_path", str(script))
    r = rec.ClipRecorder("novafps", "u")
    _ring(r, 10, first_end=1000.0)

    asyncio.run(r.cut(1007.0, 1011.0, tmp / "clip.mp4"))
    listed = [l.split("/")[-1].rstrip("'") for l in seen.read_text().splitlines()]
    assert listed == ["seg-00003.ts", "seg-00004.ts", "seg-00005.ts", "seg-00006.ts"]


def test_a_failed_cut_returns_none_and_leaves_nothing_behind(isolated_capture, monkeypatch):
    """A half-written file that no clip record points at is disk we never free."""
    tmp = isolated_capture
    script, _ = _stub_ffmpeg(tmp, exit_code=1, write_output=True)
    monkeypatch.setattr(settings, "ffmpeg_path", str(script))
    r = rec.ClipRecorder("novafps", "u")
    _ring(r, 10, first_end=1000.0)

    out = tmp / "clip.mp4"
    assert asyncio.run(r.cut(1007.0, 1011.0, out)) is None
    assert not out.exists(), "a failed cut left its output on disk"
    assert not list(tmp.glob("*.concat.txt")), "the concat list was not cleaned up"


def test_an_empty_output_counts_as_failure(isolated_capture, monkeypatch):
    """ffmpeg can exit 0 having written nothing when the input runs dry. A
    zero-byte clip in the library is worse than no clip."""
    tmp = isolated_capture
    script = tmp / "empty-ffmpeg"
    script.write_text("#!/usr/bin/env python3\n"
                      "import sys; open(sys.argv[-1],'wb').close(); sys.exit(0)\n")
    script.chmod(0o755)
    monkeypatch.setattr(settings, "ffmpeg_path", str(script))
    r = rec.ClipRecorder("novafps", "u")
    _ring(r, 10, first_end=1000.0)
    assert asyncio.run(r.cut(1007.0, 1011.0, tmp / "clip.mp4")) is None


def test_the_concat_list_is_removed_even_when_the_cut_succeeds(isolated_capture, monkeypatch):
    tmp = isolated_capture
    script, _ = _stub_ffmpeg(tmp)
    monkeypatch.setattr(settings, "ffmpeg_path", str(script))
    r = rec.ClipRecorder("novafps", "u")
    _ring(r, 10, first_end=1000.0)
    asyncio.run(r.cut(1007.0, 1011.0, tmp / "clip.mp4"))
    assert not list(tmp.glob("*.concat.txt"))
