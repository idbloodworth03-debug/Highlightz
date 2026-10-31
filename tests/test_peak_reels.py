"""Peak reels: hooks placed at the score peak, and the batch that makes them.

What is pinned here:
  * where the score peak is in a captured file (measured from the END, by
    the cut's tail), for caught, manual, fetched and crowd-suggested clips;
  * the hook lands on the loudest second BEFORE the peak, 5-10s, inside the
    clip, and falls back to the score peak when the audio is flat;
  * every hook it makes is one `plan.hook_window` accepts — a hook the
    renderer would silently drop is the bug that looks like the feature;
  * the batch takes only the account's OWN clips with a file, never a
    rejected one, approved before pending, and skips clips already made;
  * the endpoints are admin-only and refuse what they cannot do with a reason;
  * every event the batch emits has a frontend handler, and the panel
    re-reads on reconnect (CLAUDE.md realtime contract);
  * the real ffmpeg loudness read finds a real spike (skipped without ffmpeg).
"""

import asyncio
import base64
import json as _j
import pathlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.autopilot import peak_hook as H
from src.autopilot import peak_reels as R
from src.autopilot import plan as P
from src.dashboard import api

FRONTEND = pathlib.Path(api.__file__).resolve().parent / "aurora_html.py"


def _curve(duration: float, loud: tuple[float, float] | None = None,
           base: float = -40.0, peak_db: float = -8.0):
    """A made-up loudness curve: `base` dB everywhere, `peak_db` inside `loud`."""
    out, t = [], 0.0
    while t < duration:
        db = peak_db if loud and loud[0] <= t < loud[1] else base
        out.append((round(t, 2), db))
        t += H.STEP_S
    return out


def _caught(**kw):
    return {"id": "c1", "channel": "duxxion", "platform": "twitch",
            "trigger_signals": [{"type": "chat_velocity", "value": 0.9}], **kw}


# ── where the score peaked ───────────────────────────────────────────────────

def test_a_caught_clip_peaks_the_presets_tail_before_the_end():
    from src.trigger.rules import get_rules
    tail = get_rules("duxxion", "fps").post_roll
    assert H.score_peak_at(_caught(), 44.0, "fps") == pytest.approx(44.0 - tail)
    # default preset when the profile does not say
    d = get_rules("duxxion", "default").post_roll
    assert H.score_peak_at(_caught(), 65.0) == pytest.approx(65.0 - d)


def test_a_manual_clip_peaks_ten_seconds_before_the_end():
    clip = _caught(trigger_signals=[{"type": "MANUAL", "value": 1.0}])
    assert H.tail_for(clip, 70.0) == H.MANUAL_TAIL_S


def test_a_fetched_twitch_clip_peaks_near_its_end():
    """Capture missed and the file is Twitch's own ~30s clip."""
    assert H.tail_for(_caught(), 29.9) == H.TWITCH_CLIP_TAIL_S
    # a Kick file of the same length is still a capture cut
    assert H.tail_for(_caught(platform="kick"), 29.9) != H.TWITCH_CLIP_TAIL_S


def test_a_crowd_suggestion_has_no_score_peak():
    assert H.score_peak_at(_caught(suggested=True), 30.0) is None


# ── placing the hook ─────────────────────────────────────────────────────────

def test_the_hook_is_built_around_the_yell_before_the_peak():
    peak = 40.0
    hook = H.pick(_curve(65.0, loud=(34.0, 36.5)), 65.0, peak)
    assert hook.source == "audio"
    assert hook.start <= 34.0 and hook.end >= 36.5, "the yell is not in the hook"
    assert hook.end - hook.start == pytest.approx(H.HOOK_S)
    assert hook.moment_at - hook.start == pytest.approx(H.LEAD_S)


def test_a_spike_outside_the_window_before_the_peak_is_ignored():
    """A loud moment long before the peak is some other moment."""
    hook = H.pick(_curve(65.0, loud=(5.0, 7.0)), 65.0, 40.0)
    assert hook.source == "score"
    assert hook.start > 20.0


def test_flat_audio_places_the_hook_on_the_score_peak():
    peak = 40.0
    hook = H.pick(_curve(65.0), 65.0, peak)
    assert hook.source == "score"
    assert hook.start == pytest.approx(peak - (H.HOOK_S - H.LOOKAHEAD_S))
    assert hook.end == pytest.approx(peak + H.LOOKAHEAD_S)


def test_no_audio_at_all_still_gives_a_hook():
    hook = H.pick([], 65.0, 40.0)
    assert hook and hook.source == "score"


def test_the_hook_stays_inside_the_clip_at_both_ends():
    start_side = H.pick(_curve(30.0, loud=(0.5, 1.5)), 30.0, 3.0)
    assert start_side.start == 0.0 and start_side.end == pytest.approx(H.HOOK_S)
    end_side = H.pick(_curve(30.0, loud=(29.0, 30.0)), 30.0, 29.5)
    assert end_side.end == pytest.approx(30.0) and end_side.start == pytest.approx(30.0 - H.HOOK_S)


def test_a_crowd_suggestion_uses_the_loudest_moment_in_the_clip():
    hook = H.pick(_curve(30.0, loud=(20.0, 22.0)), 30.0, None)
    assert hook.source == "audio-only" and hook.start <= 20.0 < hook.end


def test_a_clip_shorter_than_any_hook_gets_none():
    assert H.pick(_curve(4.0, loud=(1.0, 2.0)), 4.0, 2.0) is None


@pytest.mark.parametrize("duration,loud,peak", [
    (65.0, (34.0, 36.5), 40.0), (65.0, None, 40.0), (30.0, (0.0, 1.0), 2.0),
    (30.0, (29.0, 30.0), 29.9), (8.0, (3.0, 4.0), 5.0), (5.0, None, 3.0),
    (170.0, (100.0, 103.0), 110.0), (44.0, (10.0, 12.0), None),
])
def test_every_hook_is_one_the_renderer_accepts(duration, loud, peak):
    hook = H.pick(_curve(duration, loud=loud), duration, peak)
    assert hook is not None
    assert P.hook_window({"hook": hook.as_clip_hook()}, duration) is not None, hook.as_dict()


def test_loudness_parsing_floors_silence():
    text = ("frame:0    pts:0       pts_time:0\n"
            "lavfi.astats.Overall.RMS_level=-inf\n"
            "frame:1    pts:1600    pts_time:0.1\n"
            "lavfi.astats.Overall.RMS_level=-21.5\n")
    assert H.parse_loudness(text) == [(0.0, H.SILENCE_DB), (0.1, -21.5)]


# ── choosing the clips ───────────────────────────────────────────────────────

def _clips():
    return [
        {"id": "a", "user_id": "me", "channel": "Duxxion", "status": "pending", "virality_score": 90},
        {"id": "b", "user_id": "me", "channel": "duxxion", "status": "approved", "virality_score": 10},
        {"id": "c", "user_id": "me", "channel": "duxxion", "status": "rejected", "virality_score": 99},
        {"id": "d", "user_id": "me", "channel": "duxxion", "status": "approved", "virality_score": 50},
        {"id": "e", "user_id": "other", "channel": "duxxion", "status": "approved", "virality_score": 99},
        {"id": "f", "user_id": "me", "channel": "duxxion", "status": "approved", "virality_score": 70},
        {"id": "g", "user_id": "me", "channel": "jynxzi", "status": "approved", "virality_score": 70},
        {"id": "h", "user_id": "me", "channel": "duxxion", "status": "approved", "virality_score": 80,
         "peak_reel": {"upload_id": "u1", "at": 1.0}},
    ]


HAVE = frozenset("abcdegh")     # f has no file


def test_the_batch_takes_own_approved_clips_first_and_never_a_rejected_one():
    got = [c["id"] for c in R.pick(_clips(), "me", "DUXXION", 10, HAVE)]
    assert got == ["d", "b", "a"]          # approved by virality, then pending
    assert "c" not in got and "e" not in got and "f" not in got and "g" not in got
    assert "h" not in got, "a clip that already has a reel was redone"
    redo = [c["id"] for c in R.pick(_clips(), "me", "duxxion", 10, HAVE, skip_made=False)]
    assert redo[0] == "h"


def test_the_count_is_honoured_and_capped():
    assert len(R.pick(_clips(), "me", "duxxion", 1, HAVE)) == 1
    assert len(R.pick(_clips() * 20, "me", "duxxion", 99, HAVE)) == R.MAX_COUNT


def test_channels_lists_only_own_clips_with_files():
    rows = R.channels(_clips(), "me", HAVE)
    assert rows[0] == {"channel": "duxxion", "clips": 4, "made": 1}
    assert {r["channel"] for r in rows} == {"duxxion", "jynxzi"}


def test_reel_names_are_safe_filenames():
    name = R.reel_name({"id": "x1", "channel": "Duxxion", "clip_title": "HE DID WHAT?! ../../etc"})
    assert name.startswith("duxxion-reel-") and name.endswith("-9x16.mp4") and "/" not in name


def test_the_preset_comes_from_the_accounts_own_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(R.settings, "local_storage_path", str(tmp_path))
    d = tmp_path / "profiles" / "me"
    d.mkdir(parents=True)
    (d / "duxxion.json").write_text(_j.dumps({"preset": "fps"}))
    assert R.preset_for("me", "duxxion") == "fps"
    assert R.preset_for("me", "nobody") == "default"
    assert R.preset_for("me", "../../me/duxxion") == "default"


# ── the job loop (render stubbed) ────────────────────────────────────────────

class _Up:
    def __init__(self, i):
        self.id = f"up{i}"

    def public(self):
        return {"id": self.id}


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Two own clips with files; make_from writes a stub; save_render returns a fake upload."""
    from src.autopilot import auto_edit, runner
    from src.clips import files as clip_files
    monkeypatch.setattr(R.settings, "local_storage_path", str(tmp_path))
    clips = {cid: {"id": cid, "user_id": "me", "channel": "duxxion", "status": "approved",
                   "trigger_signals": [], "duration_seconds": 65.0} for cid in ("k1", "k2")}
    monkeypatch.setattr(api, "_clips", clips)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_clip_out", lambda c: dict(c))
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(clip_files, "path_for", lambda cid: src)

    async def _dur(p):
        return 65.0
    monkeypatch.setattr(auto_edit, "probe_duration", _dur)

    async def _loud(p):
        return _curve(65.0, loud=(34.0, 36.0))
    monkeypatch.setattr(H, "loudness", _loud)
    seen = []

    async def _make_from(src_, rec, dst, **kw):
        seen.append(rec.get("hook"))
        if rec["id"] == "k2" and getattr(_make_from, "fail", False):
            from src.autopilot.render import RenderError
            raise RenderError("ffmpeg could not render this edit.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"mp4")
        return P.EditPlan(segments=[P.Segment(src=str(src_), start=0, end=10, clip_id=rec["id"])],
                          hook=bool(rec.get("hook")))
    monkeypatch.setattr(auto_edit, "make_from", _make_from)
    n = iter(range(100))

    async def _save(uid, clip, dst, name=None):
        assert name.startswith("duxxion-reel-")
        return _Up(next(n))
    monkeypatch.setattr(runner, "save_render", _save)
    from src.uploads import library
    monkeypatch.setattr(library, "quota", lambda uid: {})
    from src.auth import users
    monkeypatch.setattr(users, "autopilot_for", lambda uid: {
        "edit_captions": False, "edit_template": "suggested", "edit_title": False, "title": False})
    R._jobs.clear(); R._running.clear(); R._stop.clear()
    return clips, seen, _make_from


def _run(job, sent):
    async def notify(ev):
        sent.append(_j.loads(_j.dumps(ev)))
    asyncio.run(R.run("me", job, notify))


def test_a_batch_renders_every_clip_with_its_peak_hook(batch):
    clips, seen, _ = batch
    job = R.new_job("me", "duxxion", list(clips.values()))
    sent = []
    _run(job, sent)
    assert [i["status"] for i in job["items"]] == ["ready", "ready"]
    assert all(h and h["start"] <= 34.0 and h["end"] >= 36.0 for h in seen), seen
    assert job["status"] == "done" and not R.running("me")
    assert clips["k1"]["peak_reel"]["upload_id"] == "up0"
    kinds = [e["event"] for e in sent]
    assert kinds.count("upload_added") == 2 and "clip_updated" in kinds
    assert kinds[-1] == "peak_reels" and sent[-1]["job"]["status"] == "done"


def test_a_failed_render_is_reported_and_the_batch_goes_on(batch):
    clips, _, make_from = batch
    make_from.fail = True
    job = R.new_job("me", "duxxion", list(clips.values()))
    _run(job, [])
    assert [i["status"] for i in job["items"]] == ["ready", "failed"]
    assert "ffmpeg" in job["items"][1]["error"]
    assert "peak_reel" not in clips["k2"] and job["status"] == "done"


def test_stop_finishes_the_clip_in_hand_and_skips_the_rest(batch):
    clips, _, _ = batch
    job = R.new_job("me", "duxxion", list(clips.values()))
    R.request_stop("me")
    _run(job, [])
    assert [i["status"] for i in job["items"]] == ["skipped", "skipped"]
    assert job["status"] == "stopped" and not R.running("me")


def test_a_clip_that_is_not_yours_is_never_rendered(batch):
    clips, seen, _ = batch
    clips["k2"]["user_id"] = "someone-else"
    job = R.new_job("me", "duxxion", list(clips.values()))
    _run(job, [])
    assert job["items"][1]["status"] == "failed" and len(seen) == 1


# ── the endpoints ────────────────────────────────────────────────────────────

PEOPLE: dict = {}


@pytest.fixture
def web(monkeypatch):
    from src.auth import users as user_store
    from src.autopilot import runner as ap_runner
    from src.clips import files as clip_files
    PEOPLE.clear()
    PEOPLE.update({"boss": {"id": "boss", "username": "owner", "is_admin": True},
                   "u1": {"id": "u1", "username": "one", "plan": "pro"}})
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(api, "_clips", {
        "x1": {"id": "x1", "user_id": "boss", "channel": "duxxion", "status": "approved"},
        "x2": {"id": "x2", "user_id": "u1", "channel": "duxxion", "status": "approved"}})
    monkeypatch.setattr(clip_files, "existing_ids", lambda: frozenset({"x1", "x2"}))
    kicked = []
    monkeypatch.setattr(ap_runner, "kick", lambda coro: (kicked.append(coro), coro.close()))
    R._jobs.clear(); R._running.clear(); R._stop.clear()
    return kicked


def _client(uid):
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(_j.dumps({
        "auth": True, "user_id": uid, "subscription_status": "active"}).encode())
    c.cookies.set("session", signer.sign(data).decode())
    return c


def test_peak_reels_are_admin_only(web):
    assert _client("u1").get("/reels/peak").status_code == 403
    assert _client("u1").post("/reels/peak", json={"channel": "duxxion"}).status_code == 403
    assert _client("u1").post("/reels/peak/stop").status_code == 403
    assert not web


def test_starting_a_batch_takes_only_your_own_clips(web):
    r = _client("boss").post("/reels/peak", json={"channel": "Duxxion", "count": 5})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert [i["clip_id"] for i in job["items"]] == ["x1"]
    assert len(web) == 1
    again = _client("boss").post("/reels/peak", json={"channel": "duxxion"})
    assert again.status_code == 409, "two batches at once"


def test_bad_requests_are_refused_with_a_reason(web):
    boss = _client("boss")
    assert boss.post("/reels/peak", json={"channel": "../etc"}).status_code == 400
    assert boss.post("/reels/peak", json={"channel": "duxxion", "count": 50}).status_code == 400
    r = boss.post("/reels/peak", json={"channel": "nobody"})
    assert r.status_code == 409 and "nobody" in r.json()["detail"]


def test_the_state_lists_your_channels(web):
    d = _client("boss").get("/reels/peak").json()
    assert d["channels"] == [{"channel": "duxxion", "clips": 1, "made": 0}]
    assert d["job"] is None and d["max"] == R.MAX_COUNT


# ── realtime contract (CLAUDE.md) ────────────────────────────────────────────

def test_every_event_the_batch_emits_has_a_frontend_handler():
    import inspect
    import re
    emitted = set(re.findall(r'"event":\s*"([a-z_]+)"', inspect.getsource(R.run)))
    emitted |= set(re.findall(r'"event":\s*"([a-z_]+)"', inspect.getsource(api.stop_peak_reels)))
    assert emitted == {"peak_reels", "upload_added", "clip_updated"}
    page = FRONTEND.read_text()
    assert "msg.event==='peak_reels'" in page
    for ev in ("upload_added", "clip_updated"):
        assert f"'{ev}'" in page


def test_the_panel_follows_the_socket_and_rereads_on_reconnect():
    page = FRONTEND.read_text()
    panel = page[page.index("function PeakReelsPanel("):]
    panel = panel[:panel.index("\nfunction ")]
    assert "window.addEventListener('hz_ws'" in panel
    assert "window.addEventListener('hz_refetch', load)" in panel
    assert "fetch('/reels/peak')" in panel
    assert "refresh" not in panel.lower(), "never tell the user to refresh"


def test_the_tab_is_admin_only_in_the_app():
    page = FRONTEND.read_text()
    assert "{isAdmin && <button className={tab==='reels'?'on':''}" in page
    assert "{tab === 'reels' && isAdmin && <PeakReelsPanel/>}" in page


# ── the real ffmpeg read ─────────────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed here")
def test_the_real_loudness_read_finds_a_real_spike(tmp_path, monkeypatch):
    monkeypatch.setattr(H.settings, "ffmpeg_path", shutil.which("ffmpeg"))
    src = tmp_path / "beep.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=15:d=20",
        "-f", "lavfi", "-i",
        "aevalsrc=0.02*(random(0)*2-1)+between(t\\,12\\,13.5)*0.7*sin(2*PI*440*t):s=48000:d=20",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)],
        check=True, timeout=60)
    curve = asyncio.run(H.loudness(src))
    assert len(curve) > 150
    t_loud = max(curve, key=lambda p: p[1])[0]
    assert 12.0 <= t_loud <= 13.6
    hook = asyncio.run(H.find({"id": "t", "channel": "x", "suggested": True}, src, 20.0))
    assert hook.start <= 12.0 < 13.5 <= hook.end
