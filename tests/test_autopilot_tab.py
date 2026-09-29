"""Autopilot's own screen, the Auto Edit template, and "switch it on and it
goes through what you already accepted".

Owner, 2026-09-28: "Autopilot has no spot that it sits in. We need a spot for
it … where people can activate it and it will pull the clips that you already
have accepted and start going through them, auto editing and adding them to
the scheduler. Also I need the auto edit to have its own settings spot … where
the user can choose what template they want … or just use the suggested one
… And remove the hook part … I want it to be fully auto after a user accepts a
clip."
"""

import asyncio
import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import autopilot as ap
from src.autopilot import auto_edit, plan as P, runner


def _run(coro):
    return asyncio.run(coro)


# ── the template setting ─────────────────────────────────────────────────────

def test_the_suggested_template_is_the_default_and_junk_falls_back_to_it():
    assert ap.normalize({})["edit_template"] == "suggested"
    assert ap.normalize({"edit_template": "fill"})["edit_template"] == "fill"
    assert ap.normalize({"edit_template": "sparkle-explosion"})["edit_template"] == "suggested"
    assert ap.EDIT_TEMPLATES[0] == "suggested"


def _plan():
    return P.build([{"id": "c1", "channel": "lacy"}], {"c1": ("/x.mp4", 30.0)})


def test_suggested_is_the_formula_as_designed():
    p = auto_edit.apply_template(_plan(), "suggested")
    assert {s.framing for s in p.segments} == {"blur"}
    assert p.slide_in and p.slide_out and p.sfx
    assert P.valid(p)[0]


def test_fill_crops_to_fill_but_keeps_the_slides():
    p = auto_edit.apply_template(_plan(), "fill")
    assert {s.framing for s in p.segments} == {"fill"}
    assert p.slide_in and p.slide_out and p.sfx
    assert P.valid(p)[0]


def test_clean_has_no_slides_and_no_sound():
    p = auto_edit.apply_template(_plan(), "clean")
    assert {s.framing for s in p.segments} == {"blur"}
    assert not p.slide_in and not p.slide_out and p.sfx == []
    assert P.valid(p)[0]


# ── switching it on ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    people = {"pro": {"id": "pro", "subscription_status": "active", "plan": "pro"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "_load", lambda: list(people.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)

    async def _bcast(msg, user_id=None): pass
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    kicked = []
    monkeypatch.setattr(runner, "kick", lambda coro: (kicked.append(coro), coro.close()))

    async def fake_run_now(uid, *, force=True):
        return 0
    monkeypatch.setattr(runner, "run_now", fake_run_now)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "pro", "subscription_status": "active"}).encode())).decode())
    c.kicked = kicked
    return c


def test_switching_autopilot_on_starts_on_the_accepted_clips(client):
    assert client.put("/autopilot", json={"enabled": True}).status_code == 200
    assert len(client.kicked) == 1, "switching it on did not start going through the accepted clips"


def test_saving_a_setting_while_it_is_on_does_not_restart_the_pass(client):
    client.put("/autopilot", json={"enabled": True})
    client.kicked.clear()
    client.put("/autopilot", json={"enabled": True, "edit_template": "clean"})
    client.put("/autopilot", json={"enabled": True, "timing": "daily"})
    assert client.kicked == [], "every setting change restarted the pass"


def test_switching_it_off_starts_nothing(client):
    client.put("/autopilot", json={"enabled": True})
    client.kicked.clear()
    client.put("/autopilot", json={"enabled": False})
    assert client.kicked == []


def test_the_template_is_saved_with_the_settings(client):
    r = client.put("/autopilot", json={"enabled": False, "edit_template": "clean"})
    assert r.json()["config"]["edit_template"] == "clean"


# ── the pass itself ──────────────────────────────────────────────────────────

@pytest.fixture
def backlog(monkeypatch, tmp_path):
    from src.dashboard import api
    from src.auth import users as user_store
    from src.clips import files as clip_files
    from src.publish import connections
    now = time.time()
    api._clips.clear()
    for i in range(3):
        api._clips[f"c{i}"] = {"id": f"c{i}", "user_id": "pro", "status": "approved",
                               "approved_at": now - 1000 + i, "created_at": now - 2000}
    f = tmp_path / "x.mp4"; f.write_bytes(b"x")
    monkeypatch.setattr(clip_files, "path_for", lambda cid: f)
    monkeypatch.setattr(connections, "connected_platforms", lambda uid: set())
    state = {"enabled": True, "done": []}
    monkeypatch.setattr(user_store, "autopilot_for",
                        lambda uid: ap.normalize({"enabled": state["enabled"]}))
    runner._backlogs.clear()
    yield state
    api._clips.clear(); runner._backlogs.clear()


def test_the_pass_goes_through_every_accepted_clip(backlog, monkeypatch):
    async def fake(clip, cfg, notify, *, connected):
        backlog["done"].append(clip["id"]); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 3
    assert backlog["done"] == ["c0", "c1", "c2"]


def test_switching_it_off_mid_pass_stops_it_after_the_clip_in_hand(backlog, monkeypatch):
    async def fake(clip, cfg, notify, *, connected):
        backlog["done"].append(clip["id"])
        backlog["enabled"] = False              # the user flips it off during clip 1
        return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 1
    assert backlog["done"] == ["c0"]


def test_the_button_still_runs_when_autopilot_is_off(backlog, monkeypatch):
    backlog["enabled"] = False
    async def fake(clip, cfg, notify, *, connected):
        backlog["done"].append(clip["id"]); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro")) == 3


def test_a_second_pass_does_not_start_while_one_is_running(backlog, monkeypatch):
    runner._backlogs.add("pro")
    async def fake(*a, **k): raise AssertionError("a second pass started")
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 0


def test_a_pass_keeps_going_past_one_batch_until_every_clip_is_done(backlog, monkeypatch):
    """54 accepted clips were still untouched after a night: a pass stopped at
    25. It now works in batches until nothing is left."""
    from src.dashboard import api
    now = time.time()
    for i in range(3, 60):
        api._clips[f"c{i}"] = {"id": f"c{i}", "user_id": "pro", "status": "approved",
                               "approved_at": now - 1000 + i, "created_at": now - 2000}
    async def fake(clip, cfg, notify, *, connected):
        clip["autopilot"] = {"status": "scheduled"}; backlog["done"].append(clip["id"]); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 60
    assert len(set(backlog["done"])) == 60


def test_a_clip_that_keeps_failing_is_tried_once_per_pass_not_forever(backlog, monkeypatch):
    calls = []
    async def fake(clip, cfg, notify, *, connected):
        calls.append(clip["id"]); clip["autopilot"] = {"status": "failed", "error": "x"}; return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    assert _run(runner.run_now("pro", force=False)) == 3
    assert sorted(calls) == ["c0", "c1", "c2"], "a failing clip was retried within one pass"


# ── x out a clip, and clear the queue ────────────────────────────────────────
#
# Owner, 2026-09-29: "a way to basically clear the autopilot so a user can x
# out clips they changed their mind about and clear the queue fully."

@pytest.fixture
def qenv(monkeypatch, tmp_path):
    """A user with clips at every stage, the runner's real remove/clear code,
    and a Scheduler holding what Autopilot queued."""
    from src.dashboard import api
    from src.auth import users as user_store
    from src.clips import files as clip_files
    from src.publish import schedule as sched
    from src.uploads import library as lib
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json"); sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads"); monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    monkeypatch.setattr(user_store, "autopilot_for", lambda uid: ap.normalize({"enabled": True}))
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_clip_out", lambda c: dict(c))
    f = tmp_path / "x.mp4"; f.write_bytes(b"x")
    monkeypatch.setattr(clip_files, "path_for", lambda cid: f)
    sent = []

    async def notify(msg, uid): sent.append(msg["event"])
    now = time.time()
    api._clips.clear()

    async def make_upload(name):
        async def gen(): yield b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
        return await lib.save_stream("pro", name, gen(), source="render")

    def clip(cid, **kw):
        api._clips[cid] = {"id": cid, "user_id": "pro", "status": "approved", "approved_at": now - 100,
                           "created_at": now - 200, **kw}
        return api._clips[cid]

    class E: pass
    e = E(); e.api, e.sched, e.lib, e.notify, e.sent, e.clip, e.upload, e.now = api, sched, lib, notify, sent, clip, make_upload, now
    runner._inflight.clear(); runner._cancelled.clear(); runner._stops.clear(); runner._backlogs.clear()
    yield e
    api._clips.clear(); sched._items.clear(); lib._uploads.clear()
    runner._inflight.clear(); runner._cancelled.clear(); runner._stops.clear(); runner._backlogs.clear()


def test_removing_a_waiting_clip_keeps_a_pass_from_taking_it(qenv, monkeypatch):
    c = qenv.clip("w1")
    assert _run(runner.remove_clip("pro", c, qenv.notify)) == "removed"
    assert c["autopilot"]["status"] == "skipped" and "clip_updated" in qenv.sent
    taken = []
    async def fake(clip, cfg, notify, *, connected): taken.append(clip["id"]); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    _run(runner.run_now("pro", force=False))
    assert taken == [], "a removed clip was picked up again"


def test_removing_a_scheduled_clip_takes_its_queued_post_and_its_render_with_it(qenv):
    up = _run(qenv.upload("a-9x16.mp4"))
    item = qenv.sched.add("pro", up.id, up.filename, "cap", ["tiktok"], time.time() + 3600, source="autopilot")
    c = qenv.clip("s1", autopilot={"status": "scheduled", "item_id": item.id})
    assert _run(runner.remove_clip("pro", c, qenv.notify)) == "removed"
    assert qenv.sched.get(item.id, "pro") is None, "the queued post is still there"
    assert qenv.lib.get(up.id, "pro") is None, "the render still holds upload quota"
    assert "schedule_removed" in qenv.sent and "upload_removed" in qenv.sent


def test_a_clip_that_has_posted_is_never_touched(qenv):
    up = _run(qenv.upload("b-9x16.mp4"))
    item = qenv.sched.add("pro", up.id, up.filename, "cap", ["tiktok"], 0, source="autopilot")
    qenv.sched.mark_result(item.id, "pro", "tiktok", qenv.sched.R_POSTED, url="https://t/1")
    c = qenv.clip("p1", autopilot={"status": "scheduled", "item_id": item.id})
    assert _run(runner.remove_clip("pro", c, qenv.notify)) == "posted"
    assert c["autopilot"]["status"] == "scheduled" and qenv.sched.get(item.id, "pro") is not None
    assert qenv.lib.get(up.id, "pro") is not None


def test_removing_a_clip_that_is_being_edited_throws_its_result_away(qenv, monkeypatch, tmp_path):
    from src.autopilot import auto_edit
    from src.dashboard import api
    from src.auth import users as user_store
    from src.publish import connections
    monkeypatch.setattr(connections, "connected_platforms", lambda uid: set())
    monkeypatch.setattr(runner.settings, "local_storage_path", str(tmp_path))
    c = qenv.clip("r1")
    release = asyncio.Event()
    saved = []

    async def slow_make(clip, dst, **kw):
        await release.wait()
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(b"x")
        return P.build([clip], {clip["id"]: ("/x.mp4", 30.0)})

    async def spy_save(uid, clip, dst, name=None): saved.append(clip["id"])
    monkeypatch.setattr(auto_edit, "uses_new_edit", lambda u: True)
    monkeypatch.setattr(auto_edit, "make", slow_make)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {"id": uid, "is_admin": True})
    monkeypatch.setattr(runner, "save_render", spy_save)

    async def scenario():
        job = asyncio.create_task(runner.process_clip(c, ap.normalize({"enabled": True}), qenv.notify, connected=set()))
        await asyncio.sleep(0.05)
        assert await runner.remove_clip("pro", c, qenv.notify) == "removed"
        release.set(); await job
    _run(scenario())
    assert saved == [], "the result of a removed clip was saved anyway"
    assert c["autopilot"]["status"] == "skipped", "a later stage overwrote the removal"
    assert not qenv.sched._items and "c1" not in runner._cancelled


def test_clear_queue_clears_what_is_unfinished_and_leaves_what_posted(qenv):
    up1 = _run(qenv.upload("q1-9x16.mp4")); up2 = _run(qenv.upload("q2-9x16.mp4"))
    queued = qenv.sched.add("pro", up1.id, up1.filename, "c", ["tiktok"], time.time() + 3600, source="autopilot")
    gone = qenv.sched.add("pro", up2.id, up2.filename, "c", ["tiktok"], 0, source="autopilot")
    qenv.sched.mark_result(gone.id, "pro", "tiktok", qenv.sched.R_POSTED, url="u")
    qenv.clip("wait1"); qenv.clip("wait2")
    qenv.clip("fail1", autopilot={"status": "failed", "error": "x"})
    qenv.clip("q", autopilot={"status": "scheduled", "item_id": queued.id})
    qenv.clip("posted", autopilot={"status": "scheduled", "item_id": gone.id})
    out = _run(runner.clear_queue("pro", qenv.notify))
    assert out == {"removed": 4, "kept_posted": 1}
    st = {cid: (c.get("autopilot") or {}).get("status") for cid, c in qenv.api._clips.items()}
    assert st == {"wait1": "skipped", "wait2": "skipped", "fail1": "skipped", "q": "skipped", "posted": "scheduled"}
    assert qenv.sched.get(queued.id, "pro") is None and qenv.sched.get(gone.id, "pro") is not None
    assert "pro" in runner._stops, "a pass that is mid-way is not told to stop"


def test_clear_queue_only_touches_clips_a_pass_would_have_taken(qenv, monkeypatch):
    from src.clips import files as clip_files
    qenv.clip("old", approved_at=time.time() - 60 * 86400)
    qenv.clip("theirs", user_id="u9")
    qenv.clip("pending", status="pending")
    assert _run(runner.clear_queue("pro", qenv.notify)) == {"removed": 0, "kept_posted": 0}
    assert not qenv.api._clips["old"].get("autopilot") and not qenv.api._clips["theirs"].get("autopilot")


def test_a_cleared_pass_stops_after_the_clip_in_hand(qenv, monkeypatch):
    for i in range(3): qenv.clip(f"c{i}", approved_at=qenv.now - 500 + i)
    taken = []

    async def fake(clip, cfg, notify, *, connected):
        taken.append(clip["id"]); runner._stops.add("pro"); return {}
    monkeypatch.setattr(runner, "process_clip", fake)
    from src.publish import connections
    monkeypatch.setattr(connections, "connected_platforms", lambda uid: set())
    assert _run(runner.run_now("pro", force=False)) == 1 and taken == ["c0"]


def test_restore_puts_a_removed_clip_back(qenv, monkeypatch):
    c = qenv.clip("x1", autopilot={"status": "skipped"})
    ran = []
    async def fake_maybe(clip): ran.append(clip["id"])
    monkeypatch.setattr(runner, "maybe_run", fake_maybe)
    _run(runner.restore_clip("pro", c, qenv.notify))
    assert c["autopilot"] == {} and ran == ["x1"]


def test_the_routes_are_owner_only_and_refuse_a_posted_clip(client, monkeypatch):
    from src.autopilot import runner as rn
    from src.dashboard import api
    api._clips["mine"] = {"id": "mine", "user_id": "pro", "status": "approved"}
    api._clips["theirs"] = {"id": "theirs", "user_id": "other", "status": "approved"}
    seen = []

    async def fake_remove(uid, clip, notify): seen.append(clip["id"]); return "posted" if clip["id"] == "mine" else "removed"
    monkeypatch.setattr(rn, "remove_clip", fake_remove)
    c = client
    assert c.post("/autopilot/clips/theirs/remove").status_code == 404, "removed somebody else's clip"
    r = c.post("/autopilot/clips/mine/remove")
    assert r.status_code == 409 and "already been posted" in r.json()["detail"]
    assert c.post("/autopilot/clips/nope/restore").status_code == 404
    api._clips.pop("mine"); api._clips.pop("theirs")


def test_the_screen_has_the_x_add_back_and_clear_controls():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    scr = page[page.index("function AutopilotScreen("):page.index("function ScheduleScreen(")]
    assert "/autopilot/clips/' + c.id + '/remove'" in scr and "/restore'" in scr and "fetch(url, {method: 'POST'})" in scr
    assert "'/autopilot/clear'" in scr and "Clear queue" in scr and "role=\"alertdialog\"" in scr
    assert "Add back" in scr and "aria-label=\"Remove this clip from Autopilot\"" in scr
    # A posted clip gets no X, and the state it reads is the App's live queue.
    assert "sentOut(c)" in scr and "queue={queue}" in page
    assert "...waiting.slice()" in scr, "waiting clips cannot be X'd out"
