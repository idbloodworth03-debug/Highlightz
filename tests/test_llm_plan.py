"""The model as the builder — asserted on the parts that can hurt somebody.

Nothing here calls Anthropic. A test that hit the API would cost money on
every run and would pass or fail on somebody else's uptime, which is the
opposite of a test. `anthropic` is not even installed in this container, so
the module is stubbed and what is checked is OUR half of the contract:

  * a model cannot name a file — it returns a clip id and we resolve the path;
  * every number it returns is clamped to the real file before it is rendered;
  * every failure it can produce lands on the deterministic plan, because a
    post must never be missed because a model was busy.

The last one is the reason this feature is allowed to exist at all, so it is
asserted once per failure mode rather than once in general.
"""

import json
import sys
import types

import pytest

from src.autopilot import llm_plan as L
from src.autopilot import plan as P


# ── fixtures ────────────────────────────────────────────────────────────────

def clips(n=3):
    return [{"id": f"c{i}", "channel": "novafps", "stream_title": "ranked grind",
             "game": "Apex Legends", "suggested": i == 0,
             "virality_score": 50 - i, "created_at": 1700 + i}
            for i in range(n)]


def sources(n=3, duration=40.0):
    return {f"c{i}": (f"/clips/c{i}.mp4", duration) for i in range(n)}


def answer(**over):
    data = {
        "segments": [
            {"clip_id": "c0", "start": 5.0, "end": 35.0, "zoom": "punch", "why": "x"},
            {"clip_id": "c1", "start": 8.0, "end": 38.5, "zoom": "drift", "why": "y"},
        ],
        "transition": "slideleft",
        "sfx": [{"at": 0.0, "kind": "riser", "gain": 0.5},
                {"at": 29.5, "kind": "whoosh", "gain": 0.55}],
        "title": "he did not see it coming",
        "caption": "no chance",
        "hashtags": ["apexlegends", "novafps", "clips"],
    }
    data.update(over)
    return data


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, data, stop_reason="end_turn"):
        self.content = [_Block(json.dumps(data))]
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=100, output_tokens=200)


class _FakeAPIError(Exception):
    pass


@pytest.fixture
def stub(monkeypatch):
    """Install a fake `anthropic` and turn the feature on.

    Returns a dict the test fills in: `reply` is what messages.create returns,
    `raise_` is what it raises instead, and `seen` collects the kwargs so a
    test can assert what was actually sent.
    """
    box = {"reply": None, "raise_": None, "seen": {}}

    class _Messages:
        async def create(self, **kw):
            box["seen"] = kw
            if box["raise_"] is not None:
                raise box["raise_"]
            return box["reply"]

    class _Client:
        def __init__(self, **_kw):
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.AsyncAnthropic = _Client
    mod.APIError = _FakeAPIError
    monkeypatch.setitem(sys.modules, "anthropic", mod)

    from config.settings import settings
    monkeypatch.setattr(settings, "autopilot_llm", True, raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings, "llm_model", "claude-opus-5", raising=False)
    monkeypatch.setattr(settings, "llm_timeout_s", 5.0, raising=False)
    return box


# ── the model never names a file ────────────────────────────────────────────

def test_a_segment_naming_a_path_instead_of_a_clip_is_dropped():
    """The schema asks for a clip id and this resolves the path itself. If a
    returned string were ever used as a path, a model could name any file on
    the box and ffmpeg would read it."""
    plan, notes = L._coerce(answer(segments=[
        {"clip_id": "/etc/passwd", "start": 0, "end": 30, "zoom": "punch", "why": ""},
        {"clip_id": "c0", "start": 0, "end": 30, "zoom": "punch", "why": ""}]),
        sources(), clips(), target_s=60.0)
    assert [s.src for s in plan.segments] == ["/clips/c0.mp4"]
    assert any("unknown clip" in n for n in notes)


def test_every_source_comes_from_the_caller_not_the_answer():
    plan, _ = L._coerce(answer(), sources(), clips(), target_s=60.0)
    assert {s.src for s in plan.segments} <= {p for p, _d in sources().values()}


# ── the numbers are clamped to the real file ────────────────────────────────

def test_an_out_point_past_the_end_of_the_file_is_pulled_back():
    """ffmpeg does not fail on this — it renders a short segment and the
    xfade offsets computed from the plan are then wrong for the rest of the
    video. It has to be caught here."""
    plan, _ = L._coerce(answer(segments=[
        {"clip_id": "c0", "start": 10.0, "end": 900.0, "zoom": "punch", "why": ""}]),
        sources(duration=40.0), clips(), target_s=60.0)
    assert plan.segments[0].end == 40.0


def test_a_negative_in_point_becomes_zero():
    plan, _ = L._coerce(answer(segments=[
        {"clip_id": "c0", "start": -12.0, "end": 30.0, "zoom": "punch", "why": ""}]),
        sources(), clips(), target_s=60.0)
    assert plan.segments[0].start == 0.0


def test_an_inside_out_window_is_opened_rather_than_rendered():
    plan, _ = L._coerce(answer(segments=[
        {"clip_id": "c0", "start": 30.0, "end": 12.0, "zoom": "punch", "why": ""}]),
        sources(), clips(), target_s=60.0)
    seg = plan.segments[0]
    assert seg.end > seg.start and seg.length >= P.MIN_SEGMENT_S
    assert P.valid(plan)[0]


def test_a_start_too_close_to_the_end_still_leaves_a_renderable_shot():
    plan, _ = L._coerce(answer(segments=[
        {"clip_id": "c0", "start": 39.0, "end": 40.0, "zoom": "punch", "why": ""}]),
        sources(duration=40.0), clips(), target_s=60.0)
    assert plan.segments[0].length >= P.MIN_SEGMENT_S


def test_the_cut_never_runs_longer_than_the_target():
    """Four 40-second segments is 158.5s of finished video. TikTok would
    take it; the owner asked for sixty."""
    plan, notes = L._coerce(answer(segments=[
        {"clip_id": f"c{i}", "start": 0.0, "end": 40.0, "zoom": "punch", "why": ""}
        for i in range(3)]), sources(), clips(), target_s=60.0)
    assert P.plan_duration(plan) <= 60.0 + 0.01
    assert any("trimmed" in n or "not fit" in n for n in notes)


def test_a_clip_shorter_than_a_shot_is_skipped():
    plan, _ = L._coerce(answer(segments=[
        {"clip_id": "c0", "start": 0.0, "end": 4.0, "zoom": "punch", "why": ""},
        {"clip_id": "c1", "start": 0.0, "end": 30.0, "zoom": "drift", "why": ""}]),
        {"c0": ("/clips/c0.mp4", 4.0), "c1": ("/clips/c1.mp4", 40.0)},
        clips(), target_s=60.0)
    assert [s.clip_id for s in plan.segments] == ["c1"]


def test_an_invented_zoom_or_transition_falls_back_to_a_real_one():
    plan, notes = L._coerce(answer(transition="starwipe", segments=[
        {"clip_id": "c0", "start": 0, "end": 30, "zoom": "kenburns", "why": ""}]),
        sources(), clips(), target_s=60.0)
    assert plan.transition in P.TRANSITIONS
    assert plan.segments[0].zoom in P.ZOOMS
    assert any("unknown transition" in n for n in notes)


def test_a_cue_past_the_end_of_the_video_is_dropped():
    plan, notes = L._coerce(answer(sfx=[{"at": 400.0, "kind": "hit", "gain": 0.5},
                                        {"at": 1.0, "kind": "pop", "gain": 0.5}]),
                            sources(), clips(), target_s=60.0)
    assert [c.kind for c in plan.sfx] == ["pop"]
    assert any("outside the video" in n for n in notes)


def test_a_cue_loud_enough_to_bury_the_speech_is_turned_down():
    plan, _ = L._coerce(answer(sfx=[{"at": 1.0, "kind": "hit", "gain": 9.0}]),
                        sources(), clips(), target_s=60.0)
    assert plan.sfx[0].gain <= 0.9


def test_a_silent_answer_gets_the_formulas_sound_rather_than_none():
    """Silence is the single thing this whole feature exists to fix — the
    old renderer posted clips with no sound design at all."""
    plan, notes = L._coerce(answer(sfx=[]), sources(), clips(), target_s=60.0)
    assert plan.sfx and any(c.kind == "riser" for c in plan.sfx)
    assert any("fell back" in n for n in notes)


def test_anything_it_produces_is_a_plan_the_renderer_accepts():
    for over in ({}, {"sfx": []}, {"transition": "nope"},
                 {"segments": [{"clip_id": "c0", "start": 39.9, "end": 40.0,
                                "zoom": "none", "why": ""}]}):
        plan, _ = L._coerce(answer(**over), sources(), clips(), target_s=60.0)
        ok, why = P.valid(plan)
        assert ok, f"{over} -> {why}"


def test_the_plan_says_the_model_made_it():
    """The admin view and any later complaint about a bad cut both need to
    know which builder produced it."""
    plan, _ = L._coerce(answer(), sources(), clips(), target_s=60.0)
    assert plan.source == "llm"
    assert P.build(clips(), sources()).source == "formula"


# ── the brief ───────────────────────────────────────────────────────────────

def test_the_brief_carries_no_path_and_no_account():
    """Whatever is in this dict is what leaves the box. A file path is not
    useful to the model and an account identifier is nobody's business."""
    b = L.brief(clips(), sources(), {"c0": [{"start": 1, "end": 2, "text": "hi"}]})
    blob = json.dumps(b)
    assert "/clips/" not in blob
    for key in ("user_id", "email", "@"):
        assert key not in blob


def test_the_brief_only_offers_clips_that_have_a_file():
    b = L.brief(clips(3), {"c1": ("/clips/c1.mp4", 40.0)})
    assert [c["clip_id"] for c in b["candidates"]] == ["c1"]


def test_the_brief_leads_with_the_highlight():
    b = L.brief(clips(), sources())
    assert b["candidates"][0]["clip_id"] == "c0"
    assert b["candidates"][0]["highlight"] is True


def test_a_transcript_is_passed_through_in_either_shape():
    """runner.py hands captions around as tuples, the caption store keeps
    dicts. Both already exist in this codebase, so both have to work."""
    dicts = L.brief(clips(1), sources(1),
                    {"c0": [{"start": 1.0, "end": 2.0, "text": "oh my god"}]})
    tuples = L.brief(clips(1), sources(1), {"c0": [(1.0, 2.0, "oh my god")]})
    assert dicts["candidates"][0]["transcript"] == [[1.0, 2.0, "oh my god"]]
    assert tuples["candidates"][0]["transcript"] == [[1.0, 2.0, "oh my god"]]


def test_a_malformed_transcript_line_is_skipped_not_raised():
    b = L.brief(clips(1), sources(1),
                {"c0": [{"text": "no times"}, {"start": 1, "end": 2, "text": "ok"}]})
    assert b["candidates"][0]["transcript"] == [[1.0, 2.0, "ok"]]


def test_the_brief_is_bounded():
    b = L.brief(clips(30), sources(30), {"c0": [(i, i + 1, "x") for i in range(500)]})
    assert len(b["candidates"]) <= L.MAX_CANDIDATES
    assert len(b["candidates"][0]["transcript"]) <= L.MAX_TRANSCRIPT_LINES


# ── the prompt and the schema agree with plan.py ────────────────────────────

def test_the_schema_offers_exactly_the_words_the_renderer_understands():
    """A schema listing a transition the renderer does not have would be a
    plan that validates and then fails inside ffmpeg."""
    props = L.SCHEMA["properties"]
    assert props["transition"]["enum"] == list(P.TRANSITIONS)
    assert props["segments"]["items"]["properties"]["zoom"]["enum"] == list(P.ZOOMS)
    assert props["sfx"]["items"]["properties"]["kind"]["enum"] == list(P.SFX_KINDS)
    assert props["segments"]["maxItems"] == P.MAX_SEGMENTS


def test_the_schema_refuses_anything_it_did_not_ask_for():
    assert L.SCHEMA["additionalProperties"] is False
    assert L.SCHEMA["properties"]["segments"]["items"]["additionalProperties"] is False


def test_the_prompt_states_that_joins_subtract():
    """The one arithmetic fact a builder cannot get wrong: xfade overlaps, so
    two 30s segments make 59.5s. A model told to 'add up to 60' returns a
    plan that is half a second short every single time."""
    assert "OVERLAP" in L.SYSTEM
    assert "59.5" in L.SYSTEM


def test_the_prompt_does_not_explain_how_highlights_are_found():
    """Product secrecy: how the moment is detected never appears in anything
    that leaves this box."""
    low = L.SYSTEM.lower()
    for leak in ("cluster", "viewer clip", "viewers clip", "how we detect"):
        assert leak not in low


# ── every failure lands on the formula ──────────────────────────────────────

@pytest.mark.asyncio
async def test_without_a_key_it_is_the_formula_and_nothing_is_sent(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "autopilot_llm", False, raising=False)
    plan, meta = await L.build(clips(), sources())
    assert plan.source == "formula" and meta["source"] == "formula"
    assert P.valid(plan)[0]


@pytest.mark.asyncio
async def test_with_the_package_missing_it_is_the_formula(monkeypatch, stub):
    """Deploys do not run pip install, so this is a real state a box can be
    in: the key is set and the package is not there."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setattr(L, "configured", lambda: False)
    plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula"


@pytest.mark.asyncio
async def test_an_api_error_is_the_formula(stub):
    stub["raise_"] = _FakeAPIError("overloaded")
    plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula" and "API error" in meta["reason"]
    assert P.valid(plan)[0]


@pytest.mark.asyncio
async def test_a_timeout_is_the_formula(stub, monkeypatch):
    import asyncio

    async def _slow(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError
    monkeypatch.setattr(L.asyncio, "wait_for", _slow)
    plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula" and "did not answer" in meta["reason"]


@pytest.mark.asyncio
async def test_a_refusal_is_the_formula(stub):
    stub["reply"] = _Resp(answer(), stop_reason="refusal")
    _plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula" and "declined" in meta["reason"]


@pytest.mark.asyncio
async def test_unreadable_json_is_the_formula(stub):
    stub["reply"] = _Resp(answer())
    stub["reply"].content = [_Block("{not json at all")]
    _plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula" and "readable" in meta["reason"]


@pytest.mark.asyncio
async def test_a_plan_that_cannot_render_is_the_formula(stub):
    """Belt and braces behind the clamps: if _coerce ever lets something
    through that valid() rejects, the post still goes out."""
    stub["reply"] = _Resp(answer(segments=[]))
    plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula" and "invalid" in meta["reason"]
    assert plan.segments


@pytest.mark.asyncio
async def test_no_clip_has_a_file_so_nothing_is_sent(stub):
    plan, meta = await L.build(clips(), {})
    assert meta["source"] == "formula" and meta["reason"] == "no clip has a file"
    assert stub["seen"] == {}


# ── the happy path ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_good_answer_becomes_a_renderable_plan_with_copy(stub):
    stub["reply"] = _Resp(answer())
    plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "llm" and plan.source == "llm"
    assert P.valid(plan)[0]
    assert 55.0 <= P.plan_duration(plan) <= 60.0
    assert meta["copy"]["title"] == "he did not see it coming"
    assert meta["copy"]["hashtags"] == ["apexlegends", "novafps", "clips"]


@pytest.mark.asyncio
async def test_hashtags_come_back_postable(stub):
    stub["reply"] = _Resp(answer(hashtags=["#Apex Legends!", "APEX", "apex", ""]))
    _plan, meta = await L.build(clips(), sources())
    assert meta["copy"]["hashtags"] == ["apexlegends", "apex"]


@pytest.mark.asyncio
async def test_the_call_is_shaped_the_way_this_model_wants(stub):
    """Adaptive thinking (budget_tokens is a 400 on this model), structured
    output so the answer is JSON without a prefill, and the schema attached."""
    stub["reply"] = _Resp(answer())
    await L.build(clips(), sources())
    kw = stub["seen"]
    assert kw["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(kw["thinking"])
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"] is L.SCHEMA
    assert [m["role"] for m in kw["messages"]] == ["user"], "no assistant prefill"
    assert kw["model"] == "claude-opus-5"


@pytest.mark.asyncio
async def test_the_user_turn_is_the_brief_and_only_the_brief(stub):
    stub["reply"] = _Resp(answer())
    await L.build(clips(), sources())
    sent = json.loads(stub["seen"]["messages"][0]["content"])
    assert sent == L.brief(clips(), sources(), None)
