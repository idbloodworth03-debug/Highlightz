"""Autopilot keeps itself going (owner, 2026-09-30: "I just need the auto
pilot to work").

Production had 21 accepted clips with no Autopilot record on an account with
Autopilot ON: nothing had re-triggered them after a restart or a missed hook.
What these defend:

  1. the sweeper starts a pass for an account with Autopilot on and work to
     do, and never for one that is off, not on a plan with uploads, or
     already mid-pass;
  2. it does not retry failed clips (the button does), so a clip that always
     fails cannot hog the render slot every two minutes;
  3. accepted clips with no file get a fetch asked for, a few per sweep,
     never twice within the retry window;
  4. a clip removed while it is being edited stops before the render.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.autopilot import runner

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from src.dashboard import api
    from src.auth import users as user_store
    from src.clips import files as clip_files, fetch as clip_fetch
    monkeypatch.setattr(api, "_clips", {})
    src_dir = tmp_path / "clipfiles"; src_dir.mkdir()
    monkeypatch.setattr(clip_files, "path_for", lambda cid: src_dir / f"{cid}.mp4")
    people = [
        {"id": "on", "is_admin": True, "autopilot": {"enabled": True}},
        {"id": "off", "is_admin": True, "autopilot": {"enabled": False}},
        {"id": "free", "subscription_status": "none", "autopilot": {"enabled": True}},
    ]
    monkeypatch.setattr(user_store, "_load", lambda: people)
    kicked, fetched = [], []
    monkeypatch.setattr(runner, "kick", lambda coro: (kicked.append(coro.cr_frame.f_locals.get("uid")), coro.close()))
    monkeypatch.setattr(api, "_start_fetch", lambda c: fetched.append(c["id"]))
    monkeypatch.setattr(clip_fetch, "fetchable", lambda c: True)
    monkeypatch.setattr(clip_fetch, "in_flight", lambda cid: False)
    runner._backlogs.clear(); runner._fetch_tried.clear(); runner._cancelled.clear()

    def clip(cid, uid="on", file=True, **kw):
        c = {"id": cid, "user_id": uid, "status": "approved", "approved_at": time.time(), **kw}
        api._clips[cid] = c
        if file:
            (src_dir / f"{cid}.mp4").write_bytes(MP4)
        return c

    class E: pass
    e = E(); e.clip = clip; e.kicked = kicked; e.fetched = fetched
    yield e
    runner._backlogs.clear(); runner._fetch_tried.clear()


def test_a_pass_starts_for_an_account_with_autopilot_on_and_work_waiting(env):
    env.clip("a"); env.clip("b", uid="off"); env.clip("c", uid="free")
    assert _run(runner.sweep_once()) == ["on"]
    assert env.kicked == ["on"]


def test_nothing_starts_when_there_is_nothing_to_do(env):
    env.clip("done", autopilot={"status": "scheduled"})
    env.clip("gone", autopilot={"status": "skipped"})
    assert _run(runner.sweep_once()) == []


def test_failed_clips_are_left_for_the_button(env):
    env.clip("bad", autopilot={"status": "failed", "error": "x"})
    assert _run(runner.sweep_once()) == []


def test_no_second_pass_while_one_is_running(env):
    env.clip("a")
    runner._backlogs.add("on")
    assert _run(runner.sweep_once()) == []


def test_clips_without_a_file_are_fetched_a_few_at_a_time_and_not_twice(env):
    for i in range(5):
        env.clip(f"n{i}", file=False)
    _run(runner.sweep_once())
    assert len(env.fetched) == runner.FETCH_PER_SWEEP
    _run(runner.sweep_once())
    assert len(env.fetched) == 5, "the rest are asked for on the next sweep"
    _run(runner.sweep_once())
    assert len(env.fetched) == 5, "nothing is asked for twice inside the retry window"


def test_the_sweeper_is_started_with_the_app():
    import inspect
    from src import main
    assert "_autopilot_runner.sweep_task()" in inspect.getsource(main)


def test_a_clip_removed_mid_edit_stops_before_the_render(env, monkeypatch):
    from src.dashboard import api
    from src.autopilot import auto_edit, render as ap_render
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(ap_render.settings, "local_storage_path", str(env.__dict__.get("tmp", "/tmp")))
    monkeypatch.setattr(auto_edit, "uses_new_edit", lambda u: True)
    rendered = []

    async def fake_make(clip, dst, *, on_stage=None, **kw):
        await on_stage("captions")
        runner._cancelled.add(clip["id"])      # the X pressed while captions ran
        await on_stage("render")
        rendered.append(clip["id"])
    monkeypatch.setattr(auto_edit, "make", fake_make)

    async def notify(msg, uid): pass
    c = env.clip("x")
    _run(runner.process_clip(c, {"enabled": True, "platforms": []}, notify, connected=set()))
    assert rendered == [], "rendered a clip the user had removed"
    assert "x" not in runner._inflight
