"""Marketing reels: a V6-format ad made from each captured clip of a chosen channel.

Pinned here:
  * the score peak's place in a captured file (from the END, by the cut's
    tail) for caught, manual, fetched and crowd-suggested clips;
  * the hook lands on the loudest second BEFORE the peak, 8s, inside the
    clip, and falls back to the score peak when the audio is flat;
  * the timeline: 1x replay starts 2s before the moment, the fast-forward
    covers at most 20s up to it, sections stay inside the clip;
  * every drawtext ffmpeg will see says exactly what it should (the repo's
    port of both filtergraph parsers), including a channel with a quote;
  * only an ADMIN's own clip of a chosen channel is ever rendered, once,
    under a daily cap, and the reel lands in that same account's Library;
  * the owner is told (bell + Library) and every event has a frontend handler;
  * a real ffmpeg render produces a reel of the planned length (skipped
    without ffmpeg).
"""

import asyncio
import inspect
import pathlib
import shutil
import subprocess

import pytest

from src.dashboard import api
from src.marketing import auto as A
from src.marketing import peak as H
from src.marketing import reel as R
from tests import ffparse as F

FRONTEND = pathlib.Path(api.__file__).resolve().parent / "aurora_html.py"


def _curve(duration, loud=None, base=-40.0, peak_db=-8.0):
    out, t = [], 0.0
    while t < duration:
        out.append((round(t, 2), peak_db if loud and loud[0] <= t < loud[1] else base))
        t += H.STEP_S
    return out


def _caught(**kw):
    return {"id": "c1", "channel": "duxxion", "platform": "twitch",
            "trigger_signals": [{"type": "chat_velocity", "value": 0.9}], **kw}


# ── the peak ─────────────────────────────────────────────────────────────────

def test_a_caught_clip_peaks_the_presets_tail_before_the_end():
    from src.trigger.rules import get_rules
    assert H.score_peak_at(_caught(), 44.0, "fps") == pytest.approx(44.0 - get_rules("x", "fps").post_roll)
    assert H.score_peak_at(_caught(), 65.0) == pytest.approx(65.0 - get_rules("x", "default").post_roll)


def test_manual_fetched_and_suggested_clips():
    assert H.tail_for(_caught(trigger_signals=[{"type": "MANUAL"}]), 70.0) == H.MANUAL_TAIL_S
    assert H.tail_for(_caught(), 29.9) == H.TWITCH_CLIP_TAIL_S
    assert H.tail_for(_caught(platform="kick"), 29.9) != H.TWITCH_CLIP_TAIL_S
    assert H.score_peak_at(_caught(suggested=True), 30.0) is None


def test_the_hook_is_built_around_the_yell_before_the_peak():
    hook = H.pick(_curve(65.0, loud=(34.0, 36.5)), 65.0, 40.0)
    assert hook.source == "audio" and hook.start <= 34.0 and hook.end >= 36.5
    assert hook.end - hook.start == pytest.approx(H.HOOK_S)


def test_a_spike_long_before_the_peak_is_some_other_moment():
    hook = H.pick(_curve(65.0, loud=(5.0, 7.0)), 65.0, 40.0)
    assert hook.source == "score" and hook.start > 20.0


def test_flat_or_missing_audio_falls_back_to_the_score_peak():
    for curve in (_curve(65.0), []):
        hook = H.pick(curve, 65.0, 40.0)
        assert hook.source == "score"
        assert hook.end == pytest.approx(40.0 + H.LOOKAHEAD_S)


def test_the_hook_stays_inside_the_clip():
    assert H.pick(_curve(30.0, loud=(0.5, 1.5)), 30.0, 3.0).start == 0.0
    assert H.pick(_curve(30.0, loud=(29.0, 30.0)), 30.0, 29.5).end == pytest.approx(30.0)
    assert H.pick(_curve(4.0, loud=(1.0, 2.0)), 4.0, 2.0) is None


def test_loudness_parsing_floors_silence():
    text = ("frame:0    pts:0       pts_time:0\nlavfi.astats.Overall.RMS_level=-inf\n"
            "frame:1    pts:1600    pts_time:0.1\nlavfi.astats.Overall.RMS_level=-21.5\n")
    assert H.parse_loudness(text) == [(0.0, H.SILENCE_DB), (0.1, -21.5)]


# ── the timeline ─────────────────────────────────────────────────────────────

def test_the_timeline_replays_the_moment_after_fast_forwarding_up_to_it():
    tl = R.timeline(65.0, 31.5, 39.5, 34.5)
    assert tl.hook == (31.5, 39.5)
    assert tl.react[0] == pytest.approx(34.5 - R.REACT_LEAD_S)
    assert tl.react[1] - tl.react[0] == pytest.approx(R.REACT_S)
    assert tl.ff == (pytest.approx(tl.react[0] - R.FF_SRC_MAX_S), pytest.approx(tl.react[0]))
    assert tl.ff_speed == pytest.approx(R.FF_SRC_MAX_S / R.FF_S)


def test_a_moment_near_the_start_has_no_fast_forward():
    tl = R.timeline(30.0, 0.0, 8.0, 3.0)
    assert tl.ff is None and tl.react[0] >= 0.0


def test_a_moment_near_the_end_slides_the_replay_back():
    tl = R.timeline(30.0, 22.0, 30.0, 29.5)
    assert tl.react[1] == pytest.approx(30.0)
    assert tl.react[1] - tl.react[0] == pytest.approx(R.REACT_S)


@pytest.mark.parametrize("duration,moment", [(65, 34.5), (30, 3), (30, 29.5), (12, 6), (170, 120)])
def test_every_section_is_inside_the_clip(duration, moment):
    h0 = max(0.0, min(moment - 3, duration - 8))
    tl = R.timeline(float(duration), h0, h0 + 8, float(moment))
    for w in (tl.hook, tl.react) + ((tl.ff,) if tl.ff else ()):
        assert 0.0 <= w[0] < w[1] <= duration + 1e-6


# ── the ffmpeg command ───────────────────────────────────────────────────────

def _cmd(tmp_path, channel="duxxion", ff=True):
    for name in (R.AD, R.FF_SOUND, R.LOGO):
        (tmp_path / name).write_bytes(b"x")
    tl = R.timeline(65.0, 31.5, 39.5, 34.5) if ff else R.timeline(30.0, 0.0, 8.0, 3.0)
    return R.build_command(tmp_path / "in.mp4", tmp_path / "out.mp4", tl, channel=channel,
                           font="/f/DejaVu Sans.ttf", assets=tmp_path)


def test_every_drawtext_says_what_it_should(tmp_path):
    cmd = _cmd(tmp_path)
    texts = [d["text"] for d in F.drawtexts(cmd[cmd.index("-filter_complex") + 1])]
    assert texts[0] == "CLIP OF DUXXION · TWITCH"
    assert "FAST-FORWARD ▶▶" in texts and "0:12" in texts and "0:32" in texts
    assert texts[-1] == "▶ 1×"
    clock = [t for t in texts if t.startswith("%{eif")]
    assert clock == ["%{eif:floor((12.500+t*8.53333)/60):d}:%{eif:mod(floor(12.500+t*8.53333),60):d:2}"]


def test_a_channel_with_quotes_and_colons_cannot_break_the_graph(tmp_path):
    cmd = _cmd(tmp_path, channel="it's:a,[test];")
    texts = [d["text"] for d in F.drawtexts(cmd[cmd.index("-filter_complex") + 1])]
    assert texts[0] == "CLIP OF IT'S:A,[TEST]; · TWITCH"


def test_without_a_fast_forward_there_are_three_sections(tmp_path):
    cmd = _cmd(tmp_path, ff=False)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=3:v=1:a=1" in graph and "FAST-FORWARD" not in graph
    assert str(tmp_path / R.LOGO) not in cmd


def test_the_ad_is_last_and_untouched_except_for_size(tmp_path):
    cmd = _cmd(tmp_path)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.index("[vad][aad]") > graph.index("[vr][ar]") > graph.index("[vf][af]") > graph.index("[vh][ah]")
    ad_audio = [p for p in graph.split(";") if p.startswith("[3:a]")][0]
    assert "loudnorm" not in ad_audio, "the approved mix must not be re-levelled"


# ── who gets a reel ──────────────────────────────────────────────────────────

def _clip(**kw):
    return {"id": "k1", "user_id": "boss", "channel": "Duxxion", "status": "pending",
            "clip_title": "HE DID WHAT", **kw}


def test_only_an_admins_own_clip_of_a_chosen_channel(monkeypatch):
    monkeypatch.setattr(A.settings, "marketing_reel_channels", "duxxion, other")
    ok = dict(is_admin=True, has_file=True, made=0)
    assert A.why_not(_clip(), **ok) == ""
    assert A.why_not(_clip(channel="jynxzi"), **ok) == "channel"
    assert A.why_not(_clip(marketing_reel={"status": "ready"}), **ok) == "already"
    assert A.why_not(_clip(status="rejected"), **ok) == "rejected"
    assert A.why_not(_clip(), **{**ok, "is_admin": False}) == "not_admin"
    assert A.why_not(_clip(), **{**ok, "has_file": False}) == "no_file"
    assert A.why_not(_clip(), **{**ok, "made": A.settings.marketing_reel_daily_cap}) == "daily_cap"
    assert A.why_not(None, **ok) == "no_record"


def test_empty_setting_switches_it_off(monkeypatch):
    monkeypatch.setattr(A.settings, "marketing_reel_channels", " ")
    assert A.channels() == set()
    assert A.why_not(_clip(), is_admin=True, has_file=True, made=0) == "channel"


def test_the_daily_count_is_per_account_and_per_utc_day():
    now = 1_790_000_000.0
    clips = [{"user_id": "boss", "marketing_reel": {"at": now - 60}},
             {"user_id": "boss", "marketing_reel": {"at": now - 86400 * 2}},
             {"user_id": "other", "marketing_reel": {"at": now - 60}},
             {"user_id": "boss"}]
    assert A.made_today(clips, "boss", now) == 1


def test_reel_names_are_safe_filenames():
    name = A.reel_name({"id": "x", "channel": "duxxion", "clip_title": "../../etc HE DID WHAT?!"})
    assert name.startswith("duxxion-marketing-") and name.endswith("-9x16.mp4") and "/" not in name


# ── the automatic path (render stubbed) ──────────────────────────────────────

class _Up:
    id = "up1"

    def public(self):
        return {"id": self.id}


@pytest.fixture
def site(tmp_path, monkeypatch):
    from src.auth import users
    from src.autopilot import runner
    from src.clips import files as clip_files
    from src.uploads import library
    monkeypatch.setattr(A.settings, "marketing_reel_channels", "duxxion")
    monkeypatch.setattr(A.settings, "local_storage_path", str(tmp_path))
    people = {"boss": {"id": "boss", "is_admin": True}, "u1": {"id": "u1"}}
    monkeypatch.setattr(users, "get_by_id", lambda uid: people.get(uid))
    notes = []
    monkeypatch.setattr(users, "add_notices", lambda n: notes.append(n) or len(n))
    clips = {"k1": _clip(), "k2": _clip(id="k2", user_id="u1"), "k3": _clip(id="k3", channel="jynxzi")}
    monkeypatch.setattr(api, "_clips", clips)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_clip_out", lambda c: dict(c))
    sent = []

    async def _bc(ev, user_id=None):
        sent.append((ev["event"], user_id))
    monkeypatch.setattr(api, "broadcast", _bc)
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(clip_files, "path_for", lambda cid: src)
    monkeypatch.setattr(R, "assets_ready", lambda: True)
    made = []

    async def _make(clip, s, dst, preset="default"):
        made.append(clip["id"])
        if clip.get("_fail"):
            raise R.ReelError("ffmpeg could not render the reel.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"mp4")
        return {"hook": {"start": 31.5, "end": 39.5, "peak_at": 40.0, "moment_at": 34.5,
                         "source": "audio"}, "timeline": {}, "seconds": 33.1}
    monkeypatch.setattr(R, "make", _make)
    saved = []

    async def _save(uid, clip, dst, name=None):
        saved.append((uid, name))
        return _Up()
    monkeypatch.setattr(runner, "save_render", _save)
    monkeypatch.setattr(library, "quota", lambda uid: {})
    monkeypatch.setattr(A, "RECORD_POLL_S", 0.01)
    monkeypatch.setattr(A, "RECORD_WAIT_S", 0.05)
    return clips, made, saved, notes, sent


def test_an_admins_duxxion_clip_becomes_a_reel_in_their_library(site):
    clips, made, saved, notes, sent = site
    asyncio.run(A.consider("k1"))
    assert made == ["k1"]
    assert saved == [("boss", A.reel_name(clips["k1"]))]
    assert clips["k1"]["marketing_reel"]["status"] == "ready"
    assert clips["k1"]["marketing_reel"]["upload_id"] == "up1"
    assert list(notes[0]) == ["boss"] and "0:31–0:39" in notes[0]["boss"]["body"]
    assert {e for e, _ in sent} == {"clip_updated", "upload_added", "inbox_changed"}
    assert all(uid == "boss" for _, uid in sent)


def test_it_happens_once_per_clip(site):
    clips, made, *_ = site
    asyncio.run(A.consider("k1"))
    asyncio.run(A.consider("k1"))
    assert made == ["k1"]


def test_another_accounts_clip_is_never_read(site):
    clips, made, saved, notes, _ = site
    asyncio.run(A.consider("k2"))
    asyncio.run(A.consider("k3"))
    assert made == [] and saved == [] and notes == []
    assert "marketing_reel" not in clips["k2"]


def test_a_failed_render_is_recorded_and_nobody_is_told_it_worked(site):
    clips, made, saved, notes, sent = site
    clips["k1"]["_fail"] = True
    asyncio.run(A.consider("k1"))
    assert clips["k1"]["marketing_reel"]["status"] == "failed"
    assert "ffmpeg" in clips["k1"]["marketing_reel"]["error"]
    assert saved == [] and notes == [] and [e for e, _ in sent] == ["clip_updated"]


def test_a_clip_whose_record_comes_later_is_still_picked_up(site, monkeypatch):
    clips, made, *_ = site
    late = clips.pop("k1")
    monkeypatch.setattr(A, "RECORD_WAIT_S", 1.0)

    async def go():
        async def arrive():
            await asyncio.sleep(0.05)
            clips["k1"] = late
        await asyncio.gather(A.consider("k1"), arrive())
    asyncio.run(go())
    assert made == ["k1"]


def test_without_the_ad_asset_nothing_is_attempted(site, monkeypatch):
    clips, made, *_ = site
    monkeypatch.setattr(R, "assets_ready", lambda: False)
    asyncio.run(A.consider("k1"))
    assert made == [] and "marketing_reel" not in clips["k1"]


def test_the_capture_hook_starts_it_in_the_background():
    src = inspect.getsource(api.on_clip_file_ready)
    assert "_marketing.consider(clip_id)" in src and "_autopilot.kick(_marketing.consider" in src


def test_every_event_it_emits_has_a_frontend_handler():
    import re
    emitted = set(re.findall(r'"event":\s*"([a-z_]+)"', inspect.getsource(A.make_for)))
    assert emitted == {"clip_updated", "upload_added", "inbox_changed"}
    page = FRONTEND.read_text()
    for ev in emitted:
        assert f"'{ev}'" in page, ev


def test_it_adds_nothing_to_the_app():
    page = FRONTEND.read_text()
    assert "marketing_reel" not in page and "/marketing" not in page
    assert not [r for r in api.app.routes if "marketing" in getattr(r, "path", "")]


# ── the real render ──────────────────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed here")
def test_a_real_render_has_the_planned_length(tmp_path, monkeypatch):
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if not pathlib.Path(font).is_file():
        pytest.skip("no DejaVu font")
    monkeypatch.setattr(R.settings, "ffmpeg_path", shutil.which("ffmpeg"))
    monkeypatch.setattr(R.settings, "autopilot_font", font)
    monkeypatch.setattr(R.settings, "marketing_reel_assets", str(tmp_path / "assets"))
    assets = tmp_path / "assets"
    assets.mkdir()
    run = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True, timeout=120)
    run("-f", "lavfi", "-i", "color=c=navy:s=320x568:r=30:d=2", "-f", "lavfi",
        "-i", "sine=f=330:d=2:sample_rate=48000", "-shortest", "-c:v", "libx264", "-preset",
        "ultrafast", "-c:a", "aac", "-ac", "2", str(assets / R.AD))
    run("-f", "lavfi", "-i", "anoisesrc=d=2.4:a=0.05:r=48000", "-ac", "2", str(assets / R.FF_SOUND))
    shutil.copy(pathlib.Path(__file__).resolve().parent.parent / "promo/public/brand/logo-mark.png",
                assets / R.LOGO)
    src = tmp_path / "clip.mp4"
    run("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=40", "-f", "lavfi", "-i",
        "aevalsrc=0.02*(random(0)*2-1)+between(t\\,12\\,14)*0.7*sin(2*PI*440*t):s=48000:d=40",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src))
    clip = {"id": "t1", "channel": "duxxion", "platform": "twitch", "trigger_signals": []}
    out = tmp_path / "reel.mp4"
    info = asyncio.run(R.make(clip, src, out))
    assert info["hook"]["start"] <= 12.0 < info["hook"]["end"]
    d = asyncio.run(R.probe(out))
    assert d["audio"] and d["duration"] == pytest.approx(info["seconds"], abs=0.15)
