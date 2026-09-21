"""The Ollama builder, and the guard that keeps it off the droplet.

Nothing here starts a model. httpx is stubbed and what is asserted is our
half: that the request is shaped the way Ollama's API documents, that every
way a self-hosted model can fail lands on the deterministic plan, and that a
config pointing inference at the production box says so out loud.

The clamps themselves are tested once, in test_llm_plan.py, because both
providers share `llm_common.coerce`. What is checked here is that this
provider actually RUNS them — a builder that returned the model's raw JSON
would pass every test in that file and still hand ffmpeg a bad window.
"""

import json
import types

import httpx
import pytest

from src.autopilot import builder as B
from src.autopilot import llm_common as C
from src.autopilot import ollama_plan as O
from src.autopilot import plan as P


def clips(n=3):
    return [{"id": f"c{i}", "channel": "novafps", "stream_title": "ranked grind",
             "game": "Apex Legends", "suggested": i == 0, "virality_score": 50 - i}
            for i in range(n)]


def sources(n=3, duration=40.0):
    return {f"c{i}": (f"/clips/c{i}.mp4", duration) for i in range(n)}


def answer(**over):
    data = {
        "segments": [
            {"clip_id": "c0", "start": 5.0, "end": 35.0, "zoom": "punch", "why": "x"},
            {"clip_id": "c1", "start": 8.0, "end": 38.5, "zoom": "drift", "why": "y"}],
        "transition": "slideleft",
        "sfx": [{"at": 0.0, "kind": "riser", "gain": 0.5}],
        "title": "he did not see it coming",
        "caption": "no chance",
        "hashtags": ["apexlegends", "novafps"],
    }
    data.update(over)
    return data


def envelope(data):
    """What /api/chat returns: the text lands in message.content."""
    return {"model": "llama3.1:8b", "done": True,
            "message": {"role": "assistant", "content": json.dumps(data)},
            "prompt_eval_count": 900, "eval_count": 180}


@pytest.fixture
def ollama(monkeypatch):
    """Stub httpx and point the settings at a REMOTE Ollama.

    Remote on purpose: `is_local()` is true of the default 127.0.0.1, and a
    test suite that ran against the local default would never exercise the
    normal path.
    """
    box = {"reply": envelope(answer()), "raise_": None, "seen": {},
           "tags": [{"name": "llama3.1:8b", "size": 4_900_000_000,
                     "details": {"parameter_size": "8.0B"}}]}

    class _R:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, **kw):
            box["timeout"] = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None):
            box["seen"] = {"url": url, "body": json}
            if box["raise_"] is not None:
                raise box["raise_"]
            return _R(box["reply"])

        async def get(self, url):
            return _R({"models": box["tags"]})

    monkeypatch.setattr(O.httpx, "AsyncClient", _Client)

    from config.settings import settings
    monkeypatch.setattr(settings, "llm_provider", "ollama", raising=False)
    monkeypatch.setattr(settings, "ollama_base_url", "http://10.0.0.7:11434",
                        raising=False)
    monkeypatch.setattr(settings, "ollama_model", "llama3.1:8b", raising=False)
    monkeypatch.setattr(settings, "ollama_timeout_s", 180.0, raising=False)
    return box


# ── the request matches Ollama's documented API ─────────────────────────────

@pytest.mark.asyncio
async def test_it_posts_a_chat_completion_with_the_schema_attached(ollama):
    """Ollama constrains the decoder with `format`, which is what makes the
    SHAPE reliable even from a small model. Sending format:"json" instead
    would get valid JSON of the wrong shape."""
    await O.build(clips(), sources())
    sent = ollama["seen"]
    assert sent["url"] == "http://10.0.0.7:11434/api/chat"
    assert sent["body"]["format"] is C.SCHEMA
    assert sent["body"]["stream"] is False
    assert sent["body"]["model"] == "llama3.1:8b"


@pytest.mark.asyncio
async def test_the_system_prompt_and_the_brief_are_the_two_messages(ollama):
    await O.build(clips(), sources())
    msgs = ollama["seen"]["body"]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == C.SYSTEM
    assert json.loads(msgs[1]["content"]) == C.brief(clips(), sources(), None)


@pytest.mark.asyncio
async def test_the_context_window_is_big_enough_for_a_brief_with_transcripts(ollama):
    """Ollama TRUNCATES the prompt to num_ctx and answers anyway — no error.
    A brief whose transcripts fall off the end produces a confident plan
    based on half the evidence, which is the worst failure mode available."""
    await O.build(clips(), sources())
    assert ollama["seen"]["body"]["options"]["num_ctx"] >= 8192


@pytest.mark.asyncio
async def test_it_does_not_run_hot(ollama):
    """A small model at Ollama's default 0.8 invents events that are not in
    the transcript."""
    await O.build(clips(), sources())
    assert ollama["seen"]["body"]["options"]["temperature"] <= 0.5


@pytest.mark.asyncio
async def test_the_timeout_is_the_configured_one(ollama):
    await O.build(clips(), sources())
    assert ollama["timeout"] == 180.0


# ── every way a box you own can fail ────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreachable_ollama_is_the_formula(ollama):
    """The desktop is asleep, the tunnel is down, the firewall changed. This
    is the most likely failure of a self-hosted model and it must cost a
    plan, not a post."""
    ollama["raise_"] = httpx.ConnectError("connection refused")
    plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula"
    assert "unreachable" in meta["reason"] and "10.0.0.7" in meta["reason"]
    assert P.valid(plan)[0]


@pytest.mark.asyncio
async def test_a_timeout_is_the_formula(ollama):
    ollama["raise_"] = httpx.ReadTimeout("too slow")
    _plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula" and "did not answer" in meta["reason"]


@pytest.mark.asyncio
async def test_a_model_that_is_not_pulled_says_so_and_lists_what_is(ollama):
    """Ollama answers a missing model with a bare 404. "HTTP 404" would send
    somebody looking at the network; the model name is the actual problem."""
    ollama["raise_"] = httpx.HTTPStatusError(
        "not found", request=httpx.Request("POST", "http://x/api/chat"),
        response=httpx.Response(404))
    _plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula"
    assert "not found on that Ollama" in meta["reason"]
    assert "llama3.1:8b" in meta["reason"]


@pytest.mark.asyncio
async def test_a_server_error_is_the_formula(ollama):
    ollama["raise_"] = httpx.HTTPStatusError(
        "boom", request=httpx.Request("POST", "http://x/api/chat"),
        response=httpx.Response(500))
    _plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula" and "HTTP 500" in meta["reason"]


@pytest.mark.asyncio
async def test_an_older_ollama_that_ignores_the_schema_is_the_formula(ollama):
    """A version that does not understand a JSON-schema `format` free-runs
    and returns prose. The schema makes this unlikely, not impossible."""
    ollama["reply"] = {"message": {"role": "assistant",
                                   "content": "Sure! Here is a great edit:"}}
    _plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula" and "readable" in meta["reason"]


@pytest.mark.asyncio
async def test_json_that_is_not_an_object_is_the_formula(ollama):
    ollama["reply"] = {"message": {"content": "[1, 2, 3]"}}
    _plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula"


@pytest.mark.asyncio
async def test_an_empty_answer_is_the_formula(ollama):
    ollama["reply"] = {"message": {"content": ""}}
    _plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula"


@pytest.mark.asyncio
async def test_a_plan_with_no_usable_segment_is_the_formula(ollama):
    ollama["reply"] = envelope(answer(segments=[
        {"clip_id": "nope", "start": 0, "end": 30, "zoom": "punch", "why": ""}]))
    plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "formula" and "invalid" in meta["reason"]
    assert plan.segments, "the formula still produced a video"


@pytest.mark.asyncio
async def test_without_a_url_or_a_model_nothing_is_sent(ollama, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)
    _plan, meta = await O.build(clips(), sources())
    assert meta["reason"] == "Ollama not configured"
    assert ollama["seen"] == {}


# ── this provider really does run the clamps ────────────────────────────────

@pytest.mark.asyncio
async def test_a_window_past_the_end_of_the_file_is_clamped_here_too(ollama):
    """The whole point of sharing llm_common: a small model gets this wrong
    far more often than a big one, so the provider that talks to small models
    had better be running the same clamps."""
    ollama["reply"] = envelope(answer(segments=[
        {"clip_id": "c0", "start": 10.0, "end": 9000.0, "zoom": "punch", "why": ""}]))
    plan, meta = await O.build(clips(), sources(duration=40.0))
    assert meta["source"] == "llm"
    assert plan.segments[0].end == 40.0
    assert P.valid(plan)[0]


@pytest.mark.asyncio
async def test_a_returned_path_is_never_used_as_a_path(ollama):
    ollama["reply"] = envelope(answer(segments=[
        {"clip_id": "/etc/passwd", "start": 0, "end": 30, "zoom": "punch", "why": ""},
        {"clip_id": "c0", "start": 0, "end": 30, "zoom": "punch", "why": ""}]))
    plan, _meta = await O.build(clips(), sources())
    assert [s.src for s in plan.segments] == ["/clips/c0.mp4"]


@pytest.mark.asyncio
async def test_a_good_answer_becomes_a_renderable_plan_with_copy(ollama):
    plan, meta = await O.build(clips(), sources())
    assert meta["source"] == "llm" and meta["provider"] == "ollama"
    assert plan.source == "llm"
    assert P.valid(plan)[0]
    assert 55.0 <= P.plan_duration(plan) <= 60.0
    assert meta["copy"]["title"] == "he did not see it coming"
    assert meta["copy"]["hashtags"] == ["apexlegends", "novafps"]


# ── the guard against running this on the droplet ───────────────────────────

@pytest.mark.parametrize("url,local", [
    ("http://127.0.0.1:11434", True),
    ("http://localhost:11434", True),
    ("http://0.0.0.0:11434", True),
    ("http://[::1]:11434", True),
    ("http://10.0.0.7:11434", False),
    ("https://ollama.example.com", False),
])
def test_it_knows_whether_inference_would_run_on_this_machine(url, local, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ollama_base_url", url, raising=False)
    assert O.is_local() is local


def test_pointing_it_at_this_machine_warns_and_says_why(monkeypatch):
    """Prod is 2 vCPU / 3.8 GiB with no swap. The failure mode there is not a
    slow clip, it is the OOM killer taking the largest process — the server.
    That has to be said where somebody configuring it will read it."""
    from config.settings import settings
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434",
                        raising=False)
    msg = O.warn_if_local()
    assert msg and "OOM" in msg and "swap" in msg


def test_a_remote_url_does_not_warn(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ollama_base_url", "http://10.0.0.7:11434",
                        raising=False)
    assert O.warn_if_local() == ""


def test_a_malformed_url_is_not_treated_as_remote(monkeypatch):
    """Failing open here would mean a typo silently disables the warning."""
    from config.settings import settings
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    assert O.is_local() is True


# ── the dispatcher ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_default_is_the_formula_and_touches_no_network(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "llm_provider", "none", raising=False)

    async def _boom(*_a, **_k):
        raise AssertionError("a provider was called with LLM_PROVIDER=none")
    monkeypatch.setattr(O, "build", _boom)
    plan, meta = await B.build(clips(), sources())
    assert meta["source"] == "formula" and plan.source == "formula"


@pytest.mark.asyncio
async def test_a_nonsense_provider_is_the_formula_not_a_crash(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "llm_provider", "gpt-9", raising=False)
    assert B.provider() == "none"
    _plan, meta = await B.build(clips(), sources())
    assert meta["source"] == "formula"


@pytest.mark.asyncio
async def test_ollama_is_reached_through_the_dispatcher(ollama):
    plan, meta = await B.build(clips(), sources())
    assert meta["provider"] == "ollama" and plan.source == "llm"


@pytest.mark.asyncio
async def test_the_dispatcher_hands_back_what_the_provider_returned(ollama):
    """It must not re-wrap or re-decide anything — a caller comparing
    meta["source"] has to see the provider's own verdict."""
    ollama["raise_"] = httpx.ConnectError("down")
    _plan, meta = await B.build(clips(), sources())
    assert meta["source"] == "formula" and "unreachable" in meta["reason"]


def test_status_reports_the_target_without_leaking_a_key(ollama, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-super-secret",
                        raising=False)
    st = B.status()
    assert st["provider"] == "ollama" and st["configured"] is True
    assert "10.0.0.7" in st["detail"]
    assert "sk-super-secret" not in json.dumps(st)


def test_status_of_a_local_ollama_carries_the_warning(ollama, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434",
                        raising=False)
    assert "OOM" in B.status()["warning"]


def test_the_formula_is_always_reported_as_working(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "llm_provider", "none", raising=False)
    st = B.status()
    assert st["configured"] is True and "plan.py" in st["detail"]


# ── both providers keep the same contract ───────────────────────────────────

def test_both_builders_take_and_return_the_same_things():
    """builder.py picks between them by name. A provider whose signature
    drifted would fail only at the moment somebody switched to it."""
    import inspect
    from src.autopilot import llm_plan
    a = inspect.signature(llm_plan.build)
    b = inspect.signature(O.build)
    assert list(a.parameters) == list(b.parameters)


@pytest.mark.asyncio
async def test_every_provider_returns_a_plan_the_renderer_accepts(ollama):
    """Whatever happens, the thing handed to graph.py renders."""
    for reply, raise_ in ((envelope(answer()), None),
                          (envelope(answer(segments=[])), None),
                          ({"message": {"content": "junk"}}, None),
                          (None, httpx.ConnectError("x"))):
        ollama["reply"], ollama["raise_"] = reply, raise_
        plan, _meta = await B.build(clips(), sources())
        ok, why = P.valid(plan)
        assert ok, why
