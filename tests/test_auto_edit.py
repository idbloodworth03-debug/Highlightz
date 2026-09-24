"""The auto-edit, in the app for admins (owner, 2026-09-23).

"I like the changes I want this to be implemented to the admins right now so
we can test it out. Remember that we need that option to add the intro hook
bait thing also."

Pinned here: who can pick a hook and make an edit (an admin, on their own
clip only), that each step reaches an open tab live, that an admin's
Autopilot renders the new edit while everyone else keeps the old one, and
that a render interrupted by a restart does not sit on "rendering" forever.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from src.autopilot import auto_edit, render as ap_render, runner
from src.autopilot import plan as P
from src.publish import schedule as sched
from src.uploads import library as lib

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.clips import files as clip_files
    people = {"admin": {"id": "admin", "is_admin": True, "subscription_status": "active", "plan": "pro"},
              "pro": {"id": "pro", "subscription_status": "active", "plan": "pro"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "_load", lambda: list(people.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    monkeypatch.setattr(api.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    src_dir = tmp_path / "clipfiles"; src_dir.mkdir()
    monkeypatch.setattr(clip_files, "path_for", lambda cid: src_dir / f"{cid}.mp4")
    sent = []

    async def _bcast(msg, user_id=None): sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._auto_edit_running.clear()
    # The endpoint hands the render to runner.kick (fire and forget). Here it
    # is collected, and `drain()` runs it to completion after the request.
    kicked = []
    monkeypatch.setattr(runner, "kick", kicked.append)

    def drain():
        while kicked:
            asyncio.run(kicked.pop(0))
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

    def clip(cid="c1", user_id="admin", file=True, **kw):
        rec = {"id": cid, "user_id": user_id, "status": "approved", "channel": "lacy",
               "platform": "twitch", "clip_title": "Insane", "duration_seconds": 35.0,
               "created_at": time.time()}
        rec.update(kw)
        api._clips[cid] = rec
        if file:
            (src_dir / f"{cid}.mp4").write_bytes(MP4)
        return rec

    class E: pass
    e = E(); e.api = api; e.c = c; e.login = login; e.clip = clip; e.sent = sent; e.tmp = tmp_path
    e.drain = drain
    yield e
    lib._uploads.clear()


# ── the hook ─────────────────────────────────────────────────────────────────

def test_an_admin_saves_a_hook_and_the_tab_hears_about_it(app_env):
    rec = app_env.clip()
    r = app_env.login("admin").post("/clips/c1/hook", json={"start": 18, "end": 26})
    assert r.status_code == 200, r.text
    assert rec["hook"] == {"start": 18.0, "end": 26.0}
    assert r.json()["hook"] == {"start": 18.0, "end": 26.0}
    ev = [(e, u) for e, u, _ in app_env.sent]
    assert ("clip_updated", "admin") in ev, "the open tab would not see the hook"


def test_a_hook_can_be_cleared(app_env):
    rec = app_env.clip(hook={"start": 1, "end": 7})
    assert app_env.login("admin").post("/clips/c1/hook", json={"clear": True}).status_code == 200
    assert "hook" not in rec


@pytest.mark.parametrize("body", [
    {"start": 18, "end": 22},       # 4s
    {"start": 18, "end": 29},       # 11s
    {"start": -1, "end": 6},
    {"start": 30, "end": 38},       # past the 35s clip
    {"start": "x", "end": 20},
    {},
])
def test_a_bad_hook_is_refused_with_a_reason(app_env, body):
    rec = app_env.clip()
    r = app_env.login("admin").post("/clips/c1/hook", json=body)
    assert r.status_code == 400 and r.json()["detail"]
    assert "hook" not in rec


def test_only_admins_while_it_is_tested(app_env):
    app_env.clip(user_id="pro")
    c = app_env.login("pro")
    assert c.post("/clips/c1/hook", json={"start": 1, "end": 7}).status_code == 403
    assert c.post("/clips/c1/auto-edit", json={}).status_code == 403


def test_an_admin_cannot_touch_somebody_elses_clip(app_env):
    """Rendering a clip reads its file, and a clip's file belongs to its
    account — admin or not (Privacy Policy)."""
    app_env.clip(user_id="pro")
    c = app_env.login("admin")
    assert c.post("/clips/c1/hook", json={"start": 1, "end": 7}).status_code == 404
    assert c.post("/clips/c1/auto-edit", json={}).status_code == 404


# ── making the edit ──────────────────────────────────────────────────────────

def test_make_auto_edit_renders_into_the_library_and_posts_nothing(app_env, monkeypatch):
    rec = app_env.clip(hook={"start": 18, "end": 26})
    made = {}

    async def fake_make(clip, dst, *, captions, mode="clipper"):
        made["hook"] = clip.get("hook"); made["captions"] = captions
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4)
        plan = P.build([clip], {clip["id"]: ("/x.mp4", 35.0)})
        return plan
    monkeypatch.setattr(auto_edit, "make", fake_make)
    r = app_env.login("admin").post("/clips/c1/auto-edit", json={"captions": True})
    assert r.status_code == 202, r.text
    assert rec["auto_edit"]["status"] == "rendering" and rec["auto_edit"]["hook"] is True
    app_env.drain()
    ae = rec["auto_edit"]
    assert ae["status"] == "ready", ae
    assert ae["hook"] is True and ae["seconds"] == pytest.approx(42.5, abs=0.1)
    assert lib.get(ae["upload_id"], "admin"), "not in the library"
    assert made == {"hook": {"start": 18, "end": 26}, "captions": True}
    events = [e for e, _, _ in app_env.sent]
    assert events.count("clip_updated") >= 2, "rendering and ready must both reach the tab"
    assert "upload_added" in events
    assert "schedule_added" not in events, "the test button must never schedule a post"
    assert not sched._items, "nothing may be scheduled"


def test_a_failed_render_says_why_and_frees_the_button(app_env, monkeypatch):
    rec = app_env.clip()

    async def boom(clip, dst, *, captions, mode="clipper"):
        raise ap_render.RenderError("The server ran out of memory rendering this edit.")
    monkeypatch.setattr(auto_edit, "make", boom)
    assert app_env.login("admin").post("/clips/c1/auto-edit", json={}).status_code == 202
    app_env.drain()
    assert rec["auto_edit"]["status"] == "failed"
    assert "memory" in rec["auto_edit"]["error"]
    assert "c1" not in app_env.api._auto_edit_running
    assert app_env.sent[-1][0] == "clip_updated"


def test_a_clip_with_no_file_cannot_be_edited(app_env):
    app_env.clip(file=False)
    r = app_env.login("admin").post("/clips/c1/auto-edit", json={})
    assert r.status_code == 409 and "no video file" in r.json()["detail"]


def test_one_render_per_clip_at_a_time(app_env):
    app_env.clip()
    app_env.api._auto_edit_running.add("c1")
    r = app_env.login("admin").post("/clips/c1/auto-edit", json={})
    assert r.status_code == 409


def test_a_render_interrupted_by_a_restart_does_not_sit_on_rendering(tmp_path, monkeypatch):
    from src.dashboard import api
    f = tmp_path / "clips.json"
    f.write_text(json.dumps([{"id": "c1", "auto_edit": {"status": "rendering", "at": 1}},
                             {"id": "c2", "auto_edit": {"status": "ready", "upload_id": "u"}}]))
    monkeypatch.setattr(api, "_CLIPS_FILE", f)
    loaded = api._load_clips()
    assert loaded["c1"]["auto_edit"]["status"] == "failed"
    assert "restart" in loaded["c1"]["auto_edit"]["error"]
    assert loaded["c2"]["auto_edit"]["status"] == "ready"


# ── Autopilot: admins get the new edit, everyone else the old one ───────────

@pytest.fixture
def world(tmp_path, monkeypatch):
    from src.dashboard import api
    from src.clips import files as clip_files
    from src.auth import users as user_store
    people = {"admin": {"id": "admin", "is_admin": True}, "u1": {"id": "u1"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
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
    calls = []

    async def old(src, dst, template, **kw):
        calls.append("old"); dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4)
    monkeypatch.setattr(ap_render, "render", old)

    async def new(clip, dst, *, captions, mode="clipper"):
        calls.append(("new", clip.get("hook"), mode))
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4)
        return P.build([clip], {clip["id"]: ("/x.mp4", 30.0)})
    monkeypatch.setattr(auto_edit, "make", new)
    runner._inflight.clear()

    async def notify(msg, uid): pass

    def clip(cid, uid, **kw):
        rec = {"id": cid, "user_id": uid, "status": "approved", "channel": "lacy",
               "duration_seconds": 30.0, "approved_at": time.time()}
        rec.update(kw); api._clips[cid] = rec; (src_dir / f"{cid}.mp4").write_bytes(MP4)
        return rec
    yield calls, clip, notify
    sched._items.clear(); sched._loaded = False; lib._uploads.clear()


def _cfg(**kw):
    from src import autopilot as ap
    return ap.normalize({"enabled": True, "platforms": [], **kw})


def test_an_admins_autopilot_renders_the_new_edit_with_the_hook(world):
    calls, clip, notify = world
    rec = clip("a1", "admin", hook={"start": 5, "end": 12})
    out = _run(runner.process_clip(rec, _cfg(mode="streamer"), notify, connected=set()))
    assert out["status"] == "scheduled", out
    assert calls == [("new", {"start": 5, "end": 12}, "streamer")]


def test_everyone_elses_autopilot_is_untouched(world):
    calls, clip, notify = world
    rec = clip("u", "u1")
    out = _run(runner.process_clip(rec, _cfg(), notify, connected=set()))
    assert out["status"] == "scheduled", out
    assert calls == ["old"]


# ── the renderer's guard rails ───────────────────────────────────────────────

def test_the_render_allowance_scales_with_the_video():
    """228s for a 42.5s edit was measured on prod; a flat 300s would kill a
    70s one. The allowance is at least 10x the video."""
    assert auto_edit.RENDER_X_REALTIME * 70 > 300


def test_the_panel_is_admin_only_and_live():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "{isAdmin && clip.has_file && <AutoEditPanel clip={clip}/>}" in page
    body = page[page.index("function AutoEditPanel("):page.index("function ClipModal(")]
    assert "/clips/' + clip.id + '/hook'" in body and "/auto-edit'" in body
    # It keeps no copy of the server's state: the hook and the render status
    # are read off `clip`, which clip_updated replaces.
    assert "clip.auto_edit" in body and "clip.hook" in body
    assert "msg.event==='clip_updated'" in page
    assert "refresh" not in body.lower()


def test_build_plan_honours_the_hook_through_the_real_builder(monkeypatch, tmp_path):
    src = tmp_path / "c1.mp4"; src.write_bytes(MP4)

    async def dur(path): return 35.0
    monkeypatch.setattr(auto_edit, "probe_duration", dur)
    clip = {"id": "c1", "channel": "lacy", "hook": {"start": 18, "end": 26}}
    plan, meta = _run(auto_edit.build_plan(clip, src, captions=False))
    assert plan.hook and (plan.segments[0].start, plan.segments[0].end) == (18.0, 26.0)
    assert (plan.segments[1].start, plan.segments[1].end) == (0.0, 35.0)
    assert {s.zoom for s in plan.segments} == {"none"}


def test_an_oom_kill_is_reported_as_memory_not_as_a_broken_edit(monkeypatch, tmp_path):
    class Proc:
        returncode = -9
        async def communicate(self): return b"", b""
        def kill(self): pass

    async def fake_exec(*a, **k): return Proc()
    async def no_sfx(): return {}
    monkeypatch.setattr(auto_edit.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(auto_edit.S, "ensure", no_sfx)
    plan = P.build([{"id": "c1", "channel": "x"}], {"c1": ("/x.mp4", 30.0)})
    with pytest.raises(ap_render.RenderError, match="out of memory"):
        _run(auto_edit.render_plan(plan, tmp_path / "o.mp4"))


# ── Auto Edit as a style in the Clip Editor (owner, 2026-09-24) ─────────────

def _upload(app_env, uid="admin", name="jynxzi - bro mad.mp4"):
    import asyncio as _a

    async def chunks():
        yield MP4
    up = _a.run(lib.save_stream(uid, name, chunks()))
    return up


def test_editor_auto_edit_renders_an_upload_with_its_hook(app_env, monkeypatch):
    up = _upload(app_env)
    seen = {}

    async def dur(path): return 58.3

    async def fake_make_from(src, rec, dst, *, captions, mode="clipper"):
        seen["hook"] = rec.get("hook"); seen["src"] = src
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4)
        return P.build([rec], {rec["id"]: ("/x.mp4", 58.3)})
    monkeypatch.setattr(auto_edit, "probe_duration", dur)
    monkeypatch.setattr(auto_edit, "make_from", fake_make_from)
    c = app_env.login("admin")
    r = c.post(f"/uploads/{up.id}/auto-edit", json={"hook": {"start": 12, "end": 20}})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "rendering"
    assert c.get(f"/uploads/{up.id}/auto-edit").json()["status"] == "rendering"
    app_env.drain()
    job = c.get(f"/uploads/{up.id}/auto-edit").json()
    assert job["status"] == "ready", job
    assert seen["hook"] == {"start": 12.0, "end": 20.0}
    assert job["seconds"] == pytest.approx(8 + 58.3 - 0.5, abs=0.1)
    assert job["result"]["filename"].endswith("-auto-edit-9x16.mp4")
    assert lib.get(job["result"]["id"], "admin"), "the result is not in the library"
    ev = [e for e, _, _ in app_env.sent]
    assert ev.count("upload_auto_edit") == 2 and "upload_added" in ev
    assert not sched._items, "Make auto-edit must never schedule a post"


def test_editor_auto_edit_without_a_hook(app_env, monkeypatch):
    up = _upload(app_env)
    seen = {}

    async def fake_make_from(src, rec, dst, *, captions, mode="clipper"):
        seen["hook"] = rec.get("hook")
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4)
        return P.build([rec], {rec["id"]: ("/x.mp4", 30.0)})
    monkeypatch.setattr(auto_edit, "make_from", fake_make_from)
    c = app_env.login("admin")
    assert c.post(f"/uploads/{up.id}/auto-edit", json={"hook": None}).status_code == 202
    app_env.drain()
    assert seen["hook"] is None
    assert c.get(f"/uploads/{up.id}/auto-edit").json()["status"] == "ready"


@pytest.mark.parametrize("hook", [{"start": 10, "end": 14}, {"start": 10, "end": 21},
                                  {"start": 55, "end": 62}, {"start": "a", "end": 5}])
def test_editor_auto_edit_refuses_a_bad_hook(app_env, monkeypatch, hook):
    up = _upload(app_env)

    async def dur(path): return 58.3
    monkeypatch.setattr(auto_edit, "probe_duration", dur)
    r = app_env.login("admin").post(f"/uploads/{up.id}/auto-edit", json={"hook": hook})
    assert r.status_code == 400 and r.json()["detail"]


def test_editor_auto_edit_is_admin_and_owner_only(app_env):
    theirs = _upload(app_env, uid="pro")
    mine = _upload(app_env, uid="admin")
    assert app_env.login("pro").post(f"/uploads/{theirs.id}/auto-edit", json={}).status_code == 403
    assert app_env.login("admin").post(f"/uploads/{theirs.id}/auto-edit", json={}).status_code == 404
    assert app_env.login("pro").get(f"/uploads/{mine.id}/auto-edit").status_code == 403


def test_the_editor_follows_the_job_live_and_after_a_reconnect():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "msg.event==='upload_auto_edit'" in page, "the event would be dropped"
    a = page.index("function ClipEditor(")
    ed = page[a:page.index("/* ── Scheduler", a)]
    assert "fetch('/uploads/'+clip.id+'/auto-edit')" in ed, "no read-back on open"
    assert "window.addEventListener('hz_refetch', load);" in ed
    assert "m.event === 'upload_auto_edit' && m.upload_id === clip.id" in ed
    assert "autoEditOn && TEMPLATES.filter(t => t.server).map(" in ed, "Auto Edit shown to everyone"
    assert "TEMPLATES.filter(t => !t.server).map(" in ed, "Auto Edit also in the ordinary grid"
    assert "ed-tpl-feat" in ed and ".ed-tpl-feat{" in page, "Auto Edit is not the featured card"
    assert "autoEditOn={!!(me && me.is_admin)}" in page


def test_the_hook_is_picked_on_the_timeline_not_with_a_form():
    """Owner, 2026-09-24: "I need it easier for the person to pick out the
    hook it is way too confusing right now." The hook is a pink box on the
    filmstrip: drag it, drag its edge for the length, it plays on release,
    and it lands pre-placed so leaving it alone is a real choice."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    tl = page[page.index("function EdTimeline("):page.index("function ClipEditor(")]
    assert 'className="ed-hook" data-h="hook"' in tl and 'data-h="hooklen"' in tl
    assert "onHookDone()" in tl, "letting go of the box does not play the hook"
    assert "Math.max(5, Math.min(10," in tl, "the edge can stretch the hook past 5-10s"
    a = page.index("function ClipEditor(")
    ed = page[a:page.index("/* ── Scheduler", a)]
    assert "hookDefault(hookLen, dur)" in ed, "the box does not land pre-placed"
    assert "whole={tpl === 'auto'}" in ed, "trim handles still shown for Auto Edit"
    assert "{tpl !== 'auto' && <span className=\"ed-cut\">" in ed, "Set start/Set end still shown"
    side = page[page.index("function AutoEditSide("):page.index("function capWrap(")]
    assert "Start with a hook" in side and "Just the clip" in side
    assert "Hook starts here" not in side and 'type="range"' not in side, "the old form is back"


def test_the_hook_can_be_picked_while_the_video_plays():
    """Owner, 2026-09-24: "I need the user to be able to play the video while
    selecting the hook so they can see where to place it." Play and "Hook
    here" sit together in the panel; Hook here drops the box at the playhead
    (a second early) without pausing; a scrub in Auto Edit resumes playing."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    side = page[page.index("function AutoEditSide("):page.index("function capWrap(")]
    assert "onClick={onTogglePlay}" in side and "onClick={onHookHere}" in side
    a = page.index("function ClipEditor(")
    ed = page[a:page.index("/* ── Scheduler", a)]
    hh = ed[ed.index("const hookHere = () => {"):ed.index("const changeHookLen")]
    assert "v.currentTime - 1" in hh and "pause()" not in hh, "Hook here stops the video"
    assert "resumeAfterDrag.current = latest.current.tpl === 'auto';" in ed
    assert "fireSfx, tpl };" in ed, "the drag handler cannot see which style is on"
