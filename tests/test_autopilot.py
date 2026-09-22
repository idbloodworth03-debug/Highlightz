"""Autopilot (owner, 2026-09-15): "an autopilot thing for people with pro
that can auto grab accepted clips, edit them and post."

What these defend:

  * the config is normalised — a bad value never reaches the renderer or
    the scheduler, and unknown keys are dropped;
  * the timing rule is pure and tested against fixed clocks: "now",
    "spaced" (after the last Autopilot post) and "daily" (the user's local
    hour, one per day);
  * the ffmpeg graph is built, not run: each template's shape, the title
    and captions as drawtext with proper escaping, fades, no text when the
    font is missing;
  * the runner writes the clip's `autopilot` record at every step, queues a
    schedule item with source="autopilot" on the connected platforms only,
    and turns a render failure into a readable failed record — never an
    exception out of a hook;
  * the routes are Pro-gated like the rest of the Scheduler, and an
    approval kicks the runner.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from src import autopilot as ap
from src.autopilot import render as ap_render, runner
from src.publish import schedule as sched
from src.uploads import library as lib

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── config ───────────────────────────────────────────────────────────────────

def test_normalize_fills_defaults_and_clamps_everything():
    c = ap.normalize(None)
    assert c == ap.DEFAULT
    c = ap.normalize({"enabled": 1, "template": "nope", "platforms": ["youtube", "myspace", "tiktok"],
                      "timing": "weekly", "spacing_h": 900, "daily_at": "25:99", "tz_offset_min": 99999,
                      "caption_text": "x" * 5000, "title": 0, "captions": "yes", "evil": True})
    assert c["enabled"] is True and c["template"] == "full"
    assert c["platforms"] == ["youtube", "tiktok"]
    assert c["timing"] == "spaced" and c["spacing_h"] == 48 and c["daily_at"] == "18:00"
    assert c["tz_offset_min"] == 840 and len(c["caption_text"]) == ap.CAPTION_MAX
    assert c["title"] is False and c["captions"] is True and "evil" not in c


def test_next_due_now_spaced_and_daily():
    now = 1_700_000_000.0                          # 2023-11-14 22:13:20 UTC
    assert ap.next_due(ap.normalize({"timing": "now"}), now, 0) == now + 60
    sp = ap.normalize({"timing": "spaced", "spacing_h": 4})
    assert ap.next_due(sp, now, 0) == now + 60, "first spaced post waits for nothing"
    assert ap.next_due(sp, now, now - 3600) == now - 3600 + 4 * 3600, "four hours after the last one"
    assert ap.next_due(sp, now, now - 90000) == now + 60, "an old last post does not push into the past"
    # Daily at 18:00 in UTC-4: the user's 18:00 is 22:00 UTC. It is 22:13 UTC
    # now, so today's slot is gone → tomorrow 22:00 UTC.
    dy = ap.normalize({"timing": "daily", "daily_at": "18:00", "tz_offset_min": -240})
    t = ap.next_due(dy, now, 0)
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) == "2023-11-15 22:00"
    # One per day: with tomorrow taken, the day after.
    t2 = ap.next_due(dy, now, t)
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(t2)) == "2023-11-16 22:00"


def test_caption_and_title_come_from_the_clip():
    cfg = ap.normalize({"caption_text": "{title} by {channel} · {game} {nope} #clips"})
    clip = {"clip_title": "Insane clutch", "channel": "lacy", "game": "Just Chatting"}
    assert ap.caption_for(cfg, clip) == "Insane clutch by lacy · JustChatting #clips"
    assert ap.caption_for(ap.normalize({"caption_text": "{"}), clip) == "{"
    assert ap.title_for(cfg, {"stream_title": "x" * 100}) == "x" * ap.TITLE_MAX
    assert ap.title_for(ap.normalize({"title": False}), clip) == ""


# ── the ffmpeg graph ─────────────────────────────────────────────────────────

def test_each_template_has_its_own_shape():
    full = ap_render.video_filter("full")
    assert full.startswith("[0:v]scale=-2:1920,crop=1080:1920") and full.endswith("format=yuv420p[v]")
    punch = ap_render.video_filter("punch")
    assert "scale=-2:2400,crop=1080:1920" in punch
    blur = ap_render.video_filter("blur")
    assert "split=2[bg][fg]" in blur and "boxblur" in blur and "overlay=(W-w)/2:(H-h)/2" in blur
    assert "drawtext" not in full, "text drawn with no font"


def test_title_and_captions_are_drawtext_with_escaping_and_timing():
    vf = ap_render.video_filter("hook", title="He's back: 100%", font="/f.ttf",
                                captions=[(0, 1.5, "hello, world"), (2, 1, "bad cue"), (3, 4, "")],
                                duration=10)
    assert "text='He'\\''s back: 100%'" in vf and "fontsize=76" in vf and "box=1" in vf
    assert "text='HELLO, WORLD'" in vf and "enable='between(t,0.00,1.50)'" in vf
    assert vf.count("drawtext=") == 2, "a bad or empty cue was drawn"
    assert "fade=t=in:st=0:d=0.4" in vf and "fade=t=out:st=9.60:d=0.4" in vf
    # A plain title on another template is outlined, not boxed, and smaller.
    vf2 = ap_render.video_filter("full", title="t", font="/f.ttf")
    assert "borderw=6" in vf2 and "fontsize=64" in vf2


def test_build_command_encodes_for_phones(tmp_path):
    cmd = ap_render.build_command(tmp_path / "in.mp4", tmp_path / "out.mp4", "full", duration=8)
    assert cmd[0] == ap_render.settings.ffmpeg_path and cmd[-1] == str(tmp_path / "out.mp4")
    for flag in ("-filter_complex", "-map", "[v]", "0:a?", "libx264", "+faststart", "aac", "-r", "30"):
        assert flag in cmd, flag
    assert "afade=t=out:st=7.60" in cmd[cmd.index("-af") + 1]


def test_a_missing_font_skips_text_rather_than_failing(monkeypatch):
    monkeypatch.setattr(ap_render.settings, "autopilot_font", "/nope/none.ttf")
    assert ap_render.font_path() == ""


def test_the_renderer_turns_a_bad_ffmpeg_into_a_readable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ap_render.settings, "ffmpeg_path", "/bin/false")
    monkeypatch.setattr(ap_render.settings, "autopilot_font", "")
    with pytest.raises(ap_render.RenderError) as e:
        _run(ap_render.render(tmp_path / "in.mp4", tmp_path / "out.mp4", "full"))
    assert "could not render" in str(e.value)
    assert not (tmp_path / "out.mp4").exists()


# ── the runner ───────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path, monkeypatch):
    """An isolated clip store, upload library and queue, a fake render that
    writes a valid MP4, and a notify that records events."""
    from src.dashboard import api
    from src.clips import files as clip_files
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(ap_render.settings, "local_storage_path", str(tmp_path))
    src_dir = tmp_path / "clipfiles"; src_dir.mkdir()
    monkeypatch.setattr(clip_files, "path_for", lambda cid: src_dir / f"{cid}.mp4")
    rendered = []

    async def fake_render(src, dst, template, *, title="", captions=None, duration=0.0):
        rendered.append({"src": src, "template": template, "title": title, "captions": captions})
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4); return dst
    monkeypatch.setattr(ap_render, "render", fake_render)
    events = []

    async def notify(msg, uid):
        events.append((msg["event"], uid, msg))
    runner._inflight.clear()

    class W: pass
    w = W(); w.api = api; w.src_dir = src_dir; w.rendered = rendered; w.events = events; w.notify = notify
    yield w
    sched._items.clear(); sched._loaded = False; lib._uploads.clear()


def _clip(w, cid="c1", **kw):
    c = {"id": cid, "user_id": "u1", "status": "approved", "channel": "lacy", "platform": "twitch",
         "clip_title": "Insane clutch", "game": "VALORANT", "duration_seconds": 30.0,
         "approved_at": time.time(), "created_at": time.time()}
    c.update(kw); w.api._clips[cid] = c
    return c


def test_process_clip_renders_saves_and_schedules_on_connected_platforms_only(world):
    w = world
    c = _clip(w); (w.src_dir / "c1.mp4").write_bytes(MP4)
    cfg = ap.normalize({"enabled": True, "template": "hook", "platforms": ["youtube", "tiktok"],
                        "timing": "now", "caption_text": "{title} #{channel}"})
    rec = _run(runner.process_clip(c, cfg, w.notify, connected={"youtube"}))
    assert rec["status"] == "scheduled" and rec["platforms"] == ["youtube"]
    assert w.rendered[0]["template"] == "hook" and w.rendered[0]["title"] == "Insane clutch"
    items = sched.for_user("u1")
    assert len(items) == 1
    it = items[0]
    assert it.id == rec["item_id"] and it.source == "autopilot" and it.platforms == ["youtube"]
    assert it.caption == "Insane clutch #lacy" and it.ratio == "9:16" and it.fmt == "mp4"
    assert it.duration_s == 30.0 and it.due_at == rec["due_at"] and it.due_at > time.time()
    up = lib.get(it.upload_id, "u1")
    assert up and up.source == "render" and up.filename.endswith("-9x16.mp4")
    assert lib.path_for(up).read_bytes() == MP4
    kinds = [e[0] for e in w.events]
    assert kinds[0] == "clip_updated" and "schedule_added" in kinds and "upload_added" in kinds
    assert kinds[-1] == "clip_updated" and all(e[1] == "u1" for e in w.events)
    # The temp render is gone; the library copy is what remains.
    assert not list((Path(ap_render.settings.local_storage_path) / "autopilot").glob("*.mp4"))


def test_a_clip_with_no_file_yet_waits_and_is_picked_up_later(world):
    w = world
    c = _clip(w)
    cfg = ap.normalize({"enabled": True})
    rec = _run(runner.process_clip(c, cfg, w.notify, connected=set()))
    assert rec["status"] == "waiting_file" and sched.for_user("u1") == []
    assert runner.eligible(c, cfg), "a waiting clip must stay eligible for the file-arrived hook"
    (w.src_dir / "c1.mp4").write_bytes(MP4)
    rec = _run(runner.process_clip(c, cfg, w.notify, connected=set()))
    assert rec["status"] == "scheduled" and sched.for_user("u1")[0].platforms == []
    assert not runner.eligible(c, cfg), "a scheduled clip must not be run twice"


def test_a_render_failure_is_a_readable_failed_record(world, monkeypatch):
    w = world
    c = _clip(w); (w.src_dir / "c1.mp4").write_bytes(MP4)

    async def boom(*a, **k): raise ap_render.RenderError("ffmpeg could not render this clip: no libx264")
    monkeypatch.setattr(ap_render, "render", boom)
    rec = _run(runner.process_clip(c, ap.normalize({"enabled": True}), w.notify, connected=set()))
    assert rec["status"] == "failed" and "libx264" in rec["error"]
    assert sched.for_user("u1") == [] and lib.for_user("u1") == []


def test_spaced_posts_line_up_after_the_last_autopilot_post(world):
    w = world
    for i in (1, 2, 3):
        (w.src_dir / f"c{i}.mp4").write_bytes(MP4)
    cfg = ap.normalize({"enabled": True, "timing": "spaced", "spacing_h": 6})
    dues = [_run(runner.process_clip(_clip(w, f"c{i}"), cfg, w.notify, connected=set()))["due_at"]
            for i in (1, 2, 3)]
    assert abs((dues[1] - dues[0]) - 6 * 3600) < 2 and abs((dues[2] - dues[1]) - 6 * 3600) < 2


def test_maybe_run_is_quiet_when_off_not_pro_or_already_handled(world, monkeypatch):
    w = world
    from src.auth import users as user_store
    people = {"u1": {"id": "u1", "plan": "pro", "subscription_status": "active",
                     "autopilot": {"enabled": True}},
              "u2": {"id": "u2", "plan": "starter", "subscription_status": "active",
                     "autopilot": {"enabled": True}}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "autopilot_for", lambda uid: ap.normalize(people[uid]["autopilot"]))
    monkeypatch.setattr(w.api, "broadcast", w.notify)
    (w.src_dir / "c1.mp4").write_bytes(MP4); (w.src_dir / "c2.mp4").write_bytes(MP4)
    _run(runner.maybe_run(_clip(w, "c2", user_id="u2")))
    assert w.rendered == [], "a Starter account got Autopilot"
    people["u1"]["autopilot"]["enabled"] = False
    _run(runner.maybe_run(_clip(w, "c1")))
    assert w.rendered == [], "ran while switched off"
    people["u1"]["autopilot"]["enabled"] = True
    _run(runner.maybe_run_by_id("c1"))
    assert len(w.rendered) == 1
    _run(runner.maybe_run_by_id("c1"))
    assert len(w.rendered) == 1, "a scheduled clip was rendered again"


def test_run_now_takes_recent_approved_clips_with_files_and_retries_failures(world, monkeypatch):
    w = world
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "autopilot_for", lambda uid: ap.normalize({"enabled": False}))
    monkeypatch.setattr(w.api, "broadcast", w.notify)
    _clip(w, "fresh"); (w.src_dir / "fresh.mp4").write_bytes(MP4)
    _clip(w, "failed", autopilot={"status": "failed", "error": "x"}); (w.src_dir / "failed.mp4").write_bytes(MP4)
    _clip(w, "done", autopilot={"status": "scheduled"}); (w.src_dir / "done.mp4").write_bytes(MP4)
    _clip(w, "nofile")
    _clip(w, "old", approved_at=time.time() - 30 * 86400); (w.src_dir / "old.mp4").write_bytes(MP4)
    _clip(w, "pending", status="pending"); (w.src_dir / "pending.mp4").write_bytes(MP4)
    _clip(w, "theirs", user_id="u9"); (w.src_dir / "theirs.mp4").write_bytes(MP4)
    n = _run(runner.run_now("u1"))
    assert n == 2 and {r["src"].stem for r in w.rendered} == {"fresh", "failed"}


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    people = {"pro_user": {"id": "pro_user", "subscription_status": "active", "plan": "pro"},
              "starter_user": {"id": "starter_user", "subscription_status": "active", "plan": "starter"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "_load", lambda: list(people.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    sent = []

    async def _bcast(msg, user_id=None): sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    c = TestClient(api.app, base_url="https://testserver")

    def login(uid):
        c.cookies.clear()
        from itsdangerous import TimestampSigner
        import base64
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(json.dumps({"auth": True, "user_id": uid,
                                            "subscription_status": "active"}).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c
    c.login = login; c.sent = sent; c.people = people
    return c


def test_autopilot_routes_are_pro_only_and_persist(client, monkeypatch):
    assert client.login("starter_user").get("/autopilot").status_code == 403
    c = client.login("pro_user")
    r = c.get("/autopilot")
    assert r.status_code == 200 and r.json()["config"]["enabled"] is False
    assert "font_ok" in r.json() and "captions_available" in r.json()
    r = c.put("/autopilot", json={"enabled": True, "template": "blur", "platforms": ["youtube"],
                                  "timing": "daily", "daily_at": "19:30", "tz_offset_min": -300, "junk": 1})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["enabled"] and cfg["template"] == "blur" and cfg["daily_at"] == "19:30" and "junk" not in cfg
    assert client.people["pro_user"]["autopilot"] == cfg, "not written to the user record"
    assert ("autopilot_changed", "pro_user") in [(e, u) for e, u, _ in client.sent]
    assert c.get("/autopilot").json()["config"] == cfg
    assert c.put("/autopilot", json=[1]).status_code == 400
    started = []
    monkeypatch.setattr(runner, "kick", lambda coro: (started.append(coro), coro.close()))
    assert c.post("/autopilot/run").status_code == 202 and len(started) == 1
    assert client.login("starter_user").post("/autopilot/run").status_code == 403


def test_every_autopilot_route_is_behind_the_pro_gate():
    import inspect
    from src.dashboard import api
    routes = [r for r in api.app.routes if getattr(r, "path", "").startswith("/autopilot")]
    assert len(routes) == 3
    for r in routes:
        assert "_require_upload_access(" in inspect.getsource(r.endpoint), r.path


def test_an_approval_kicks_the_runner_and_the_file_hooks_call_it():
    import inspect
    from src.dashboard import api
    from src.ingestion import stream_worker
    assert "_autopilot.kick(_autopilot.maybe_run(clip))" in inspect.getsource(api.approve_clip)
    assert "await on_clip_file_ready(clip_id)" in inspect.getsource(api._fetch_and_announce)
    assert "await dashboard_api.on_clip_file_ready(clip_id)" in \
        inspect.getsource(stream_worker.StreamWorker._cut_local_file)
    assert "maybe_run_by_id" in inspect.getsource(api.on_clip_file_ready)


# ── the dashboard ────────────────────────────────────────────────────────────

def test_the_scheduler_has_the_autopilot_card_and_the_clip_card_shows_its_state():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "function AutopilotCard(" in h
    screen = h[h.index("function ScheduleScreen("):h.index("function UploadScreen(")]
    assert "<AutopilotCard" in screen
    card = h[h.index("function AutopilotCard("):h.index("function ScheduleScreen(")]
    assert "tz_offset_min: -new Date().getTimezoneOffset()" in card, "daily_at would be in server time"
    assert "role=\"switch\"" in card and "fetch('/autopilot/run', {method:'POST'})" in card
    for k in ("['now','Right away']", "['spaced','Spread out']", "['daily','Once a day']"):
        assert k in card
    # Realtime: pulled in refetchAll and updated by its event.
    assert "refetchAutopilot();" in h[h.index("const refetchAll"):h.index("const wsBootstrapped")]
    assert "msg.event==='autopilot_changed'" in h
    # The clip card says what Autopilot did with it.
    rd = h[h.index("function RdClip("):h.index("function ClipModal(")]
    assert "rd-apbadge" in rd and "clip.autopilot.status" in rd
