"""A picture for every Kick clip (owner, 2026-10-15): "Fix the thumbnails for
Kick I need those to not be the ugly brown images".

Kick has no clip API, so a Kick clip arrives with no thumbnail and every card
fell back to a gradient hashed from the channel name — dark brown for much of
the colour wheel. The video is on our disk, so the picture is a frame of it.

What these pin:
  1. one ffmpeg run per picture: keyframe seek, one frame, 640 wide, MJPEG;
     from the middle of the clip, from the start if that fails; cached beside
     the MP4; never more than two at once, and one per clip however many ask;
  2. it is real: a frame cut by the real ffmpeg is a JPEG 640 wide (skipped
     where ffmpeg is not installed);
  3. it outlives the MP4 under the size cap and goes with the clip: delete(),
     and the sweep's orphan half; and never outlives the retention the Terms
     state — it takes its video's age, and the sweep's age half takes it;
  4. the endpoint serves it to the owner only, and the clip list says which
     clips have one;
  5. a video landing asks for the picture of a clip that has no platform one;
  6. the dashboard shows it, falls back to it when a Twitch thumbnail fails,
     uses it as the player's poster, and the placeholder that remains for
     clips with no video at all is never brown.
"""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time

import pytest

from src.clips import files as clip_files
from src.clips import thumbs

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 2000


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    clip_files._forget_scan()
    monkeypatch.setattr(thumbs, "_inflight", {})
    monkeypatch.setattr(thumbs, "_gate", None)
    monkeypatch.setattr(thumbs, "_gate_loop", None)
    yield root
    clip_files._forget_scan()


def _video(root, cid, data=b"v" * 4096):
    p = root / f"{cid}.mp4"
    p.write_bytes(data)
    clip_files._forget_scan()
    return p


class _Proc:
    def __init__(self, out, delay=0.0, rc=0):
        self.out, self.delay, self.returncode = out, delay, rc

    async def communicate(self):
        await asyncio.sleep(self.delay)
        return self.out, b""

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


@pytest.fixture
def ffmpeg(monkeypatch):
    """A stand-in ffmpeg: records every run, answers with a JPEG (or nothing
    for the seek points listed in `empty_at`)."""
    runs, live, peak = [], [0], [0]
    cfg = {"delay": 0.0, "empty_at": set()}

    async def fake(*cmd, **kw):
        runs.append(cmd)
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        t = float(cmd[cmd.index("-ss") + 1])

        class P(_Proc):
            async def communicate(self_inner):
                try:
                    return await _Proc.communicate(self_inner)
                finally:
                    live[0] -= 1
        return P(b"" if t in cfg["empty_at"] else JPEG, cfg["delay"])
    monkeypatch.setattr(thumbs.asyncio, "create_subprocess_exec", fake)
    return type("F", (), {"runs": runs, "peak": peak, "cfg": cfg})


# ── 1. making one ────────────────────────────────────────────────────────────

def test_the_command_is_one_frame_by_keyframe_seek(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ffmpeg_path", "/opt/ff/bin/ffmpeg")
    cmd = thumbs.command(clip_files.path_for("c1"), 10.0)
    assert cmd[0] == "/opt/ff/bin/ffmpeg", "not the binary the capture is configured with"
    assert cmd.index("-ss") < cmd.index("-i"), "seek after -i decodes from the start"
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "scale=640:-2" in cmd and "mjpeg" in cmd and cmd[-1] == "-"
    assert cmd[cmd.index("-threads") + 1] == "1"


def test_a_picture_is_cut_from_the_middle_and_cached(store, ffmpeg):
    _video(store, "c1")
    p = asyncio.run(thumbs.ensure("c1", 10.0))
    assert p == store / "c1.jpg" and p.read_bytes() == JPEG
    assert float(ffmpeg.runs[0][ffmpeg.runs[0].index("-ss") + 1]) == 10.0
    assert asyncio.run(thumbs.ensure("c1", 10.0)) == p
    assert len(ffmpeg.runs) == 1, "a cached picture was cut again"


def test_a_failed_seek_falls_back_to_the_first_frame(store, ffmpeg):
    _video(store, "c1")
    ffmpeg.cfg["empty_at"].add(30.0)                 # past the end of a short cut
    assert asyncio.run(thumbs.ensure("c1", 30.0)) is not None
    assert [float(r[r.index("-ss") + 1]) for r in ffmpeg.runs] == [30.0, 0.0]


def test_no_video_means_no_picture_and_no_ffmpeg(store, ffmpeg):
    assert asyncio.run(thumbs.ensure("nothing", 5.0)) is None
    assert ffmpeg.runs == []


def test_many_asks_for_one_clip_share_one_run(store, ffmpeg):
    _video(store, "c1")
    ffmpeg.cfg["delay"] = 0.05

    async def go():
        return await asyncio.gather(*(thumbs.ensure("c1", 1.0) for _ in range(6)))
    assert all(p == store / "c1.jpg" for p in asyncio.run(go()))
    assert len(ffmpeg.runs) == 1


def test_never_more_than_two_ffmpegs_at_once(store, ffmpeg):
    for i in range(6):
        _video(store, f"c{i}")
    ffmpeg.cfg["delay"] = 0.05

    async def go():
        return await asyncio.gather(*(thumbs.ensure(f"c{i}", 1.0) for i in range(6)))
    assert all(asyncio.run(go()))
    assert len(ffmpeg.runs) == 6 and ffmpeg.peak[0] == 2


# ── 2. the real thing ────────────────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed here")
def test_a_real_frame_is_a_640_wide_jpeg(store):
    src = store / "real.mp4"
    made = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                           "testsrc=duration=4:size=1280x720:rate=30", "-pix_fmt", "yuv420p",
                           "-c:v", "libx264", str(src)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot encode a test video")
    clip_files._forget_scan()
    p = asyncio.run(thumbs.ensure("real", 2.0))
    data = p.read_bytes()
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                            "-of", "json", str(p)], capture_output=True, text=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (640, 360)


# ── 3. lifetime ──────────────────────────────────────────────────────────────

def test_deleting_the_clip_deletes_its_picture(store):
    _video(store, "c1")
    (store / "c1.jpg").write_bytes(JPEG)
    clip_files.delete("c1")
    assert not (store / "c1.jpg").exists() and not (store / "c1.mp4").exists()


def test_the_picture_outlives_its_video_under_the_cap_but_not_its_clip(store, monkeypatch):
    from config.settings import settings
    old = _video(store, "kept", b"v" * 3 * 1024 * 1024)
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    (store / "kept.jpg").write_bytes(JPEG)
    (store / "gone.jpg").write_bytes(JPEG)                  # its record was deleted
    monkeypatch.setattr(settings, "clip_file_max_total_mb", 1)
    assert clip_files.trim_to_cap(keep_ids=set()) == 1
    assert not old.exists() and (store / "kept.jpg").exists(), "the cap took the picture too"
    clip_files.sweep(live_ids={"kept"})
    assert (store / "kept.jpg").exists() and not (store / "gone.jpg").exists()


def test_a_picture_takes_its_videos_age(store, ffmpeg):
    v = _video(store, "c1")
    ten_days_ago = time.time() - 10 * 86400
    os.utime(v, (ten_days_ago, ten_days_ago))
    p = asyncio.run(thumbs.ensure("c1", 1.0))
    assert abs(p.stat().st_mtime - ten_days_ago) < 1, \
        "a frame of an old recording started a new retention clock"


def test_no_picture_outlives_the_retention_the_terms_state(store, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "clip_file_max_age_days", 30)
    aged = time.time() - 31 * 86400
    for cid in ("old", "young"):
        (store / f"{cid}.jpg").write_bytes(JPEG)
    os.utime(store / "old.jpg", (aged, aged))
    clip_files.sweep(live_ids={"old", "young"})      # a live clip does not protect it
    assert not (store / "old.jpg").exists() and (store / "young.jpg").exists()
    (store / "old2.jpg").write_bytes(JPEG)
    os.utime(store / "old2.jpg", (aged, aged))
    clip_files.sweep(live_ids=None)                  # no record list: the age half still runs
    assert not (store / "old2.jpg").exists() and (store / "young.jpg").exists()
    assert clip_files.thumb_ids() == frozenset({"young"})


def test_the_listing_knows_which_clips_have_a_picture(store):
    _video(store, "v1")
    (store / "p1.jpg").write_bytes(JPEG)
    (store / "empty.jpg").write_bytes(b"")
    assert clip_files.thumb_ids() == frozenset({"p1"})
    assert clip_files.existing_ids() == frozenset({"v1"})


# ── 4. the endpoint and the list ─────────────────────────────────────────────

@pytest.fixture
def app(monkeypatch):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.auth import users as user_store
    from src.dashboard import api
    people = {"u1": {"id": "u1", "username": "u1"}, "u2": {"id": "u2", "username": "u2"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(api, "_clips", {
        "k1": {"id": "k1", "user_id": "u1", "platform": "kick", "channel": "lacobraaa",
               "thumbnail_url": "", "duration_seconds": 20.0, "status": "pending",
               "created_at": time.time() - 3600},
        "k2": {"id": "k2", "user_id": "u1", "platform": "kick", "channel": "lacobraaa",
               "thumbnail_url": "", "duration_seconds": 20.0, "status": "pending",
               "created_at": time.time() - 3600}})
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def as_(uid):
        c = TestClient(api.app, base_url="https://testserver")
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid}).encode())).decode())
        return c
    return as_, api


def test_the_owner_gets_the_picture_and_nobody_else_does(store, app):
    as_, api = app
    (store / "k1.jpg").write_bytes(JPEG)
    clip_files._forget_scan()
    r = as_("u1").get("/clips/k1/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG and "max-age" in r.headers.get("cache-control", "")
    assert as_("u2").get("/clips/k1/thumb").status_code == 404
    assert as_("u1").get("/clips/k2/thumb").status_code == 404, "no video, no picture"


def test_the_clip_list_says_which_clips_have_one(store, app):
    as_, api = app
    (store / "k1.jpg").write_bytes(JPEG)
    clip_files._forget_scan()
    listing = clip_files.existing_ids()
    assert api._clip_out(api._clips["k1"], listing)["has_thumb"] is True
    assert api._clip_out(api._clips["k2"], listing)["has_thumb"] is False
    assert api._clip_out(api._clips["k1"])["has_thumb"] is True


# ── 5. made when the video lands ─────────────────────────────────────────────

def test_a_video_landing_asks_for_a_picture_only_when_the_platform_gave_none(app, monkeypatch):
    as_, api = app
    from src.autopilot import runner
    asked = []
    monkeypatch.setattr(thumbs, "make_soon", lambda cid, at=0.0: asked.append((cid, at)))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "library_copy_if_approved", _noop)
    monkeypatch.setattr(runner, "maybe_run_by_id", lambda cid: _noop())
    monkeypatch.setattr(runner, "kick", lambda coro: coro.close())
    api._clips["tw"] = {"id": "tw", "user_id": "u1", "platform": "twitch",
                        "thumbnail_url": "https://clips-media/x-preview-480x272.jpg"}
    asyncio.run(api.on_clip_file_ready("k1"))
    asyncio.run(api.on_clip_file_ready("tw"))
    assert asked == [("k1", 10.0)]


# ── 6. the dashboard ─────────────────────────────────────────────────────────

SRC = (__import__("pathlib").Path(__file__).resolve().parent.parent
       / "src/dashboard/aurora_html.py").read_text()


def test_cards_and_the_modal_use_the_clips_own_picture():
    assert "const clipPic    = c => (c && c.id && (c.has_file || c.has_thumb)) ? '/clips/' + c.id + '/thumb' : '';" in SRC
    i = SRC.index("function RdClip(")
    card = SRC[i:SRC.index("\nfunction ", i + 10)]
    assert "const thumb = clip.thumbnail_url || pic;" in card
    assert "data-local={pic && pic!==thumb ? pic : ''}" in card
    modal = SRC[SRC.index("function ClipModal("):]
    modal = modal[:modal.index("\nfunction ")]
    assert "const thumb = clip.thumbnail_url || pic;" in modal
    assert "poster={pic || undefined}" in modal


def test_a_failed_twitch_thumbnail_falls_back_to_our_frame_before_the_gradient():
    fb = SRC[SRC.index("function thumbFallback("):]
    fb = fb[:fb.index("\n}\n")]
    assert fb.index("data-orig") < fb.index("data-local") < fb.index("thumbFor(channel)")


def test_the_placeholder_is_never_brown():
    assert "const hue=235+h%85;" in SRC
    assert "hsl(${hue} 52% 26%),hsl(${hue+30} 58% 12%)" in SRC
    hues = {235 + h % 85 for h in range(360)} | {235 + h % 85 + 30 for h in range(360)}
    # Blue through violet to magenta: nothing in the reds, oranges, yellows and
    # greens that read as brown at this darkness.
    assert min(hues) >= 235 and max(hues) <= 349
