"""Why an Autopilot edit has no captions, and where they sit.

Owner, 2026-09-28: "The auto captions are being held up and it is stuck. No
words are coming out. Also make the auto pilot format … have the auto captions
on the bottom of the screen and not the top."

Three things were wrong or missing:
  * with captions unavailable the edit simply had none, and nothing said why —
    that is what "stuck, no words" looks like from outside;
  * the wait for the one transcription slot had no limit of its own;
  * the clip's TITLE, drawn at the top of the frame, was switched on by default
    for the plan-based edit (the legacy `title` default), so the only words at
    the top were the title, not captions.
"""

import asyncio
import time

import pytest

from config.settings import settings
from src.autopilot import auto_edit, graph as G, plan as P


def _run(coro):
    return asyncio.run(coro)


def test_captions_off_on_the_server_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "captions_enabled", False)
    segs, why = _run(auto_edit._transcript(tmp_path / "c.mp4"))
    assert segs == [] and "CAPTIONS_ENABLED" in why


def test_a_transcription_that_fails_says_why_and_does_not_cost_the_edit(monkeypatch, tmp_path):
    from src.captions import transcribe as cap
    monkeypatch.setattr(settings, "captions_enabled", True)
    monkeypatch.setattr(cap, "load", lambda p: None)

    async def boom(p, on_progress=None): raise RuntimeError("the model would not load")
    monkeypatch.setattr(cap, "transcribe", boom)
    segs, why = _run(auto_edit._transcript(tmp_path / "c.mp4"))
    assert segs == [] and "would not load" in why


def test_a_stuck_caption_step_is_given_up_on_instead_of_holding_the_edit(monkeypatch, tmp_path):
    from src.captions import transcribe as cap
    monkeypatch.setattr(settings, "captions_enabled", True)
    monkeypatch.setattr(settings, "captions_timeout_s", 0.05)
    monkeypatch.setattr(auto_edit, "CAPTION_STEP_EXTRA_S", 0.05)
    monkeypatch.setattr(cap, "load", lambda p: None)

    async def hang(p, on_progress=None): await asyncio.sleep(30)
    monkeypatch.setattr(cap, "transcribe", hang)
    t0 = time.time()
    segs, why = _run(auto_edit._transcript(tmp_path / "c.mp4"))
    assert time.time() - t0 < 5, "the edit waited on captions indefinitely"
    assert segs == [] and "too long" in why


def test_a_clip_with_no_speech_says_so(monkeypatch, tmp_path):
    from src.captions import transcribe as cap
    monkeypatch.setattr(settings, "captions_enabled", True)
    monkeypatch.setattr(cap, "load", lambda p: {"segments": []})
    segs, why = _run(auto_edit._transcript(tmp_path / "c.mp4"))
    assert segs == [] and "No speech" in why


def test_the_note_travels_with_the_plan(monkeypatch, tmp_path):
    src = tmp_path / "c.mp4"; src.write_bytes(b"x")
    monkeypatch.setattr(settings, "captions_enabled", False)

    async def dur(p): return 30.0
    monkeypatch.setattr(auto_edit, "probe_duration", dur)
    plan, meta = _run(auto_edit.build_plan({"id": "c1", "channel": "lacy"}, src, captions=True))
    assert "CAPTIONS_ENABLED" in meta["caption_note"] and plan.captions == []


def test_captions_are_drawn_in_the_bottom_of_the_frame_and_the_title_at_the_top():
    """The two are different things in different places: captions ride at 78%
    of the height (the blurred band under the picture), the title at 12%."""
    assert G.CAPTION_Y > 0.5
    cmd = G.caption_filters("/f.ttf", "WHAT A PLAY", 1.0, 2.0, 0)
    assert f"y=h*{G.CAPTION_Y}" in cmd


def test_the_suggested_edit_puts_nothing_at_the_top_by_default():
    plan = P.build([{"id": "c1", "channel": "lacy"}], {"c1": ("/x.mp4", 30.0)})
    plan.captions = [{"start": 1.0, "end": 2.0, "text": "hello"}]
    plan = auto_edit.apply_template(plan, "suggested")
    assert plan.title == ""
    cmd = " ".join(G.build_command(plan, "/o.mp4", {}, font="/f.ttf"))
    assert "y=h*0.12" not in cmd, "a title is drawn at the top"
    assert f"y=h*{G.CAPTION_Y}" in cmd


def test_the_screens_show_the_stage_and_the_reason():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "Writing captions…" in page and "Rendering…" in page
    act = page[page.index("function AutopilotScreen("):page.index("function ScheduleScreen(")]
    assert "aptLabel(c)" in act and "c.autopilot.note" in act
    # Admins' switches are the edit_* ones; captions are labelled as bottom, title as top.
    assert "edit_captions" in act and "edit_title" in act
    assert "Auto-captions at the bottom" in act and "Title at the top" in act
    assert "ap.captions_available === false" in act, "no warning when the server has captions off"
    panel = page[page.index("function AutoEditPanel("):page.index("function ClipModal(")]
    assert "ae.note" in panel
