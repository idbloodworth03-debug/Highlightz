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


class _FakeBadRequest(_FakeAPIError):
    pass


@pytest.fixture
def stub(monkeypatch):
    """Install a fake `anthropic` and turn the feature on.

    Returns a dict the test fills in: `reply` is what messages.create returns,
    `raise_` is what it raises instead, and `seen` collects the kwargs so a
    test can assert what was actually sent.
    """
    box = {"reply": None, "raise_": None, "seen": {},
           "caps_raise": None, "caps_asked": [], "max_tokens": 128000,
           # Opus-shaped by default: adaptive thinking, effort, structured out.
           "caps": {"structured_outputs": {"supported": True},
                    "effort": {"supported": True, "medium": {"supported": True}},
                    "thinking": {"supported": True,
                                 "types": {"adaptive": {"supported": True}}}}}

    class _Messages:
        async def create(self, **kw):
            box["seen"] = kw
            if box["raise_"] is not None:
                raise box["raise_"]
            return box["reply"]

    class _Models:
        async def retrieve(self, model):
            if box["caps_raise"] is not None:
                raise box["caps_raise"]
            box["caps_asked"].append(model)
            return types.SimpleNamespace(id=model, max_tokens=box["max_tokens"],
                                         capabilities=box["caps"])

    class _Client:
        def __init__(self, **_kw):
            self.messages = _Messages()
            self.models = _Models()

    mod = types.ModuleType("anthropic")
    mod.AsyncAnthropic = _Client
    mod.APIError = _FakeAPIError
    mod.BadRequestError = _FakeBadRequest
    monkeypatch.setitem(sys.modules, "anthropic", mod)

    from config.settings import settings
    monkeypatch.setattr(settings, "llm_provider", "anthropic", raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings, "llm_model", "claude-opus-5", raising=False)
    monkeypatch.setattr(settings, "llm_timeout_s", 5.0, raising=False)
    L._CAPS.clear()
    return box


# ── the model never names a file ────────────────────────────────────────────

def test_a_segment_naming_a_path_instead_of_a_clip_is_dropped():
    """The schema asks for a clip id and this resolves the path itself. If a
    returned string were ever used as a path, a model could name any file on
    the box and ffmpeg would read it."""
    plan, notes = L.coerce(answer(segments=[
        {"clip_id": "/etc/passwd", "start": 0, "end": 30, "zoom": "punch", "why": ""},
        {"clip_id": "c0", "start": 0, "end": 30, "zoom": "punch", "why": ""}]),
        sources(), clips(), target_s=60.0)
    assert [s.src for s in plan.segments] == ["/clips/c0.mp4"]
    assert any("unknown clip" in n for n in notes)


def test_every_source_comes_from_the_caller_not_the_answer():
    plan, _ = L.coerce(answer(), sources(), clips(), target_s=60.0)
    assert {s.src for s in plan.segments} <= {p for p, _d in sources().values()}


# ── the numbers are clamped to the real file ────────────────────────────────

def test_an_out_point_past_the_end_of_the_file_is_pulled_back():
    """ffmpeg does not fail on this — it renders a short segment and the
    xfade offsets computed from the plan are then wrong for the rest of the
    video. It has to be caught here."""
    plan, _ = L.coerce(answer(segments=[
        {"clip_id": "c0", "start": 10.0, "end": 900.0, "zoom": "punch", "why": ""}]),
        sources(duration=40.0), clips(), target_s=60.0)
    assert plan.segments[0].end == 40.0


def test_a_negative_in_point_becomes_zero():
    plan, _ = L.coerce(answer(segments=[
        {"clip_id": "c0", "start": -12.0, "end": 30.0, "zoom": "punch", "why": ""}]),
        sources(), clips(), target_s=60.0)
    assert plan.segments[0].start == 0.0


def test_an_inside_out_window_is_opened_rather_than_rendered():
    plan, _ = L.coerce(answer(segments=[
        {"clip_id": "c0", "start": 30.0, "end": 12.0, "zoom": "punch", "why": ""}]),
        sources(), clips(), target_s=60.0)
    seg = plan.segments[0]
    assert seg.end > seg.start and seg.length >= P.MIN_SEGMENT_S
    assert P.valid(plan)[0]


def test_a_start_too_close_to_the_end_still_leaves_a_renderable_shot():
    plan, _ = L.coerce(answer(segments=[
        {"clip_id": "c0", "start": 39.0, "end": 40.0, "zoom": "punch", "why": ""}]),
        sources(duration=40.0), clips(), target_s=60.0)
    assert plan.segments[0].length >= P.MIN_SEGMENT_S


def test_the_model_can_only_cut_one_clip():
    """Owner, 2026-09-23: "keep it only to one clip". A model that returns
    three clips gets its first usable one."""
    plan, _ = L.coerce(answer(segments=[
        {"clip_id": f"c{i}", "start": 0.0, "end": 40.0, "zoom": "punch", "why": ""}
        for i in range(3)]), sources(), clips(), target_s=60.0)
    assert [s.clip_id for s in plan.segments] == ["c0"]
    assert P.plan_duration(plan) <= 40.0 + 0.01


def test_a_clip_shorter_than_a_shot_is_skipped():
    plan, _ = L.coerce(answer(segments=[
        {"clip_id": "c0", "start": 0.0, "end": 4.0, "zoom": "punch", "why": ""},
        {"clip_id": "c1", "start": 0.0, "end": 30.0, "zoom": "drift", "why": ""}]),
        {"c0": ("/clips/c0.mp4", 4.0), "c1": ("/clips/c1.mp4", 40.0)},
        clips(), target_s=60.0)
    assert [s.clip_id for s in plan.segments] == ["c1"]


def test_an_invented_zoom_or_transition_falls_back_to_a_real_one():
    plan, notes = L.coerce(answer(transition="starwipe", segments=[
        {"clip_id": "c0", "start": 0, "end": 30, "zoom": "kenburns", "why": ""}]),
        sources(), clips(), target_s=60.0)
    assert plan.transition in P.TRANSITIONS
    assert plan.segments[0].zoom in P.ZOOMS
    assert any("unknown transition" in n for n in notes)


def test_a_cue_past_the_end_of_the_video_is_dropped():
    plan, notes = L.coerce(answer(sfx=[{"at": 400.0, "kind": "hit", "gain": 0.5},
                                        {"at": 1.0, "kind": "pop", "gain": 0.5}]),
                            sources(), clips(), target_s=60.0)
    assert [c.kind for c in plan.sfx] == ["pop"]
    assert any("outside the video" in n for n in notes)


def test_a_cue_loud_enough_to_bury_the_speech_is_turned_down():
    plan, _ = L.coerce(answer(sfx=[{"at": 1.0, "kind": "hit", "gain": 9.0}]),
                        sources(), clips(), target_s=60.0)
    assert plan.sfx[0].gain <= 0.9


def test_a_silent_answer_gets_the_formulas_sound_rather_than_none():
    """Silence is the single thing this whole feature exists to fix — the
    old renderer posted clips with no sound design at all."""
    plan, notes = L.coerce(answer(sfx=[]), sources(), clips(), target_s=60.0)
    assert plan.slide_in and plan.slide_out
    assert [c.at for c in plan.sfx if c.kind == "whoosh"][0] == 0.0, "the open is silent"
    assert any("fell back" in n for n in notes)


def test_a_one_clip_model_plan_with_no_cues_is_still_not_silent():
    """No cuts means no transition whoosh; the slide in and out still sound."""
    one = [{"clip_id": "c0", "start": 0.0, "end": 30.0, "zoom": "none",
            "framing": "blur", "layout": "none", "why": ""}]
    plan, _ = L.coerce(answer(sfx=[], segments=one), sources(), clips(), target_s=60.0)
    assert len(plan.segments) == 1 and len(plan.sfx) == 2


def test_anything_it_produces_is_a_plan_the_renderer_accepts():
    for over in ({}, {"sfx": []}, {"transition": "nope"},
                 {"segments": [{"clip_id": "c0", "start": 39.9, "end": 40.0,
                                "zoom": "none", "why": ""}]}):
        plan, _ = L.coerce(answer(**over), sources(), clips(), target_s=60.0)
        ok, why = P.valid(plan)
        assert ok, f"{over} -> {why}"


def test_the_plan_says_the_model_made_it():
    """The admin view and any later complaint about a bad cut both need to
    know which builder produced it."""
    plan, _ = L.coerce(answer(), sources(), clips(), target_s=60.0)
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


def test_the_prompt_asks_for_one_clip_and_no_length():
    """The model is told the rule the builder enforces: one segment from one
    clip. Telling it to reach a minute would be telling it to stitch."""
    assert "ONE segment, from ONE clip" in L.SYSTEM
    assert "Never join clips" in L.SYSTEM
    assert "59.5" not in L.SYSTEM and "LONGER than one minute" not in L.SYSTEM


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
    monkeypatch.setattr(settings, "llm_provider", "none", raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
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
    assert len(plan.segments) == 1 and P.plan_duration(plan) == pytest.approx(30.0)
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


# ── the request is shaped to the MODEL, not to one model ────────────────────
#
# These exist because of a real bug. The builder sent thinking:{"adaptive"}
# and output_config.effort unconditionally, and Haiku 4.5 — the model
# recommended as the cheap option, five times cheaper per token — takes
# neither: adaptive thinking and GA effort are 4.6-and-later. Every call
# would have 400'd, and because every failure falls back to the formula, the
# symptom would have been no symptom at all. Switching LLM_MODEL to save
# money would have silently turned the feature off.

@pytest.mark.asyncio
async def test_a_model_without_adaptive_thinking_is_not_sent_it(stub):
    """Haiku 4.5's shape: structured output and nothing else."""
    stub["caps"] = {"structured_outputs": {"supported": True},
                    "effort": {"supported": False},
                    "thinking": {"supported": False,
                                 "types": {"adaptive": {"supported": False}}}}
    stub["reply"] = _Resp(answer())
    _plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "llm", "a cheaper model must still work"
    assert "thinking" not in stub["seen"]
    assert "effort" not in stub["seen"].get("output_config", {})
    assert stub["seen"]["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_a_model_with_adaptive_thinking_still_gets_it(stub):
    stub["reply"] = _Resp(answer())
    await L.build(clips(), sources())
    assert stub["seen"]["thinking"] == {"type": "adaptive"}
    assert stub["seen"]["output_config"]["effort"] == "medium"


@pytest.mark.asyncio
async def test_budget_tokens_is_never_sent_to_anything(stub):
    """It is a 400 on the current models. There is no configuration of this
    builder that should produce it."""
    for caps in ({"thinking": {"types": {"adaptive": {"supported": False}}}},
                 {"thinking": {"types": {"adaptive": {"supported": True}}}}):
        stub["caps"], stub["reply"] = caps, _Resp(answer())
        L._CAPS.clear()
        await L.build(clips(), sources())
        assert "budget_tokens" not in json.dumps(stub["seen"].get("thinking") or {})


@pytest.mark.asyncio
async def test_an_effort_level_the_model_lacks_is_not_sent(stub):
    """`max` errors on Haiku 4.5 and Sonnet 4.5. The level is checked, not
    just whether effort exists at all."""
    stub["caps"] = {"structured_outputs": {"supported": True},
                    "effort": {"supported": True, "medium": {"supported": False}}}
    stub["reply"] = _Resp(answer())
    await L.build(clips(), sources())
    assert "effort" not in stub["seen"].get("output_config", {})


@pytest.mark.asyncio
async def test_max_tokens_respects_a_models_lower_ceiling(stub):
    """Haiku 4.5 caps output at 64K where the Opus models take 128K."""
    stub["max_tokens"], stub["reply"] = 4000, _Resp(answer())
    await L.build(clips(), sources())
    assert stub["seen"]["max_tokens"] == 4000


@pytest.mark.asyncio
async def test_a_failed_capability_lookup_sends_the_conservative_request(stub):
    """The lookup is best-effort. If it cannot be done, send the shape that
    works everywhere rather than losing the post."""
    stub["caps_raise"] = RuntimeError("models endpoint unavailable")
    stub["reply"] = _Resp(answer())
    _plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "llm"
    assert "thinking" not in stub["seen"]
    assert "effort" not in stub["seen"].get("output_config", {})


@pytest.mark.asyncio
async def test_capabilities_are_asked_once_per_model_not_once_per_clip(stub):
    """This is a metadata call on the path of every clip. Asking each time
    would add a round trip to every post for information that never changes."""
    stub["reply"] = _Resp(answer())
    for _ in range(3):
        await L.build(clips(), sources())
    assert stub["caps_asked"] == ["claude-opus-5"]


@pytest.mark.asyncio
async def test_a_rejected_request_names_the_model_in_the_reason(stub):
    """A 400 means we built a bad request, so every clip fails identically.
    "API error" would send somebody looking at the network; the model name
    is the thing that changed."""
    stub["raise_"] = _FakeBadRequest("unexpected parameter: thinking")
    _plan, meta = await L.build(clips(), sources())
    assert meta["source"] == "formula"
    assert "claude-opus-5" in meta["reason"] and "rejected" in meta["reason"]


def test_a_missing_capability_tree_is_read_as_unsupported(stub):
    """Never send a parameter on the strength of a dict that did not say
    yes — that is exactly how the original bug shipped."""
    assert L._supports({}, "thinking", "types", "adaptive") is False
    assert L._supports({"thinking": None}, "thinking", "types") is False
    assert L._supports({"thinking": {"types": {}}}, "thinking", "types", "adaptive") is False
    assert L._supports({"a": {"supported": True}}, "a") is True
    assert L._supports({"a": {"supported": False}}, "a") is False


# ── the user's hook beats the model (owner, 2026-09-23) ─────────────────────

@pytest.mark.asyncio
async def test_a_clip_with_a_hook_is_cut_by_the_formula_not_the_model(monkeypatch):
    """The user watched the clip and chose what it opens on; a model reading
    metadata cannot see the video. With a model configured, a hooked clip
    still renders exactly the hook that was picked — and nothing is sent."""
    from src.autopilot import builder, llm_plan
    monkeypatch.setattr(builder, "provider", lambda: "anthropic")

    async def boom(*a, **k):
        raise AssertionError("the model was asked to cut a clip the user already cut")
    monkeypatch.setattr(llm_plan, "build", boom)

    c = {"id": "c0", "channel": "novafps", "hook": {"start": 12.0, "end": 20.0}}
    plan, meta = await builder.build([c], {"c0": ("/clips/c0.mp4", 40.0)})
    assert plan.hook and (plan.segments[0].start, plan.segments[0].end) == (12.0, 20.0)
    assert meta["source"] == "formula" and meta["reason"] == "the user picked a hook"


@pytest.mark.asyncio
async def test_a_clip_without_a_hook_still_goes_to_the_model(monkeypatch):
    from src.autopilot import builder, llm_plan
    monkeypatch.setattr(builder, "provider", lambda: "anthropic")
    seen = {}

    async def fake(clips, sources, **k):
        seen["called"] = True
        return P.build(clips, sources), {"source": "llm", "reason": "", "copy": {}, "notes": []}
    monkeypatch.setattr(llm_plan, "build", fake)
    await builder.build([{"id": "c0", "channel": "n"}], {"c0": ("/clips/c0.mp4", 40.0)})
    assert seen.get("called")


def test_a_model_asking_for_a_zoom_gets_a_static_shot():
    """Same reason as the formula: a zoom crops, softens, and green-lines
    the edge. The model is told so, and a model that asks anyway is noted."""
    plan, notes = L.coerce(answer(), sources(), clips(), target_s=60.0)
    assert {s.zoom for s in plan.segments} == {"none"}
    assert any("zoom is not used" in n for n in notes)
    assert 'Always "none"' in L.SYSTEM
