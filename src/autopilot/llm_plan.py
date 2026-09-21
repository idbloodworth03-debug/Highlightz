"""The Anthropic builder: an EditPlan decided by Claude.

WHERE THIS SITS. plan.py said it plainly — "a BUILDER decides what the video
should be -> EditPlan; the RENDERER turns that into one ffmpeg run" — and
that an LLM builder could be added later without touching the thing that
makes video. This is one of two such builders; `ollama_plan.py` is the other,
and `builder.py` picks between them. graph.py does not know which one ran.

EVERYTHING PROVIDER-NEUTRAL LIVES IN llm_common: the brief, the schema, the
clamps that make a hallucinated answer safe, and the formula fallback. What
is left here is the HTTP call and the things that are true only of Anthropic
— adaptive thinking, `output_config`, the error types.

PRIVACY. This sends clip metadata and, when they exist, transcripts to
Anthropic. That is a third party the Privacy Policy names (Section 3). It
never sends the video, the user's email, or anything identifying the account.
Off by default: LLM_PROVIDER=anthropic to turn it on.
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog

from config.settings import settings
from src.autopilot import plan as P
from src.autopilot.llm_common import (MAX_CANDIDATES, MAX_TRANSCRIPT_LINES,
                                      SCHEMA, SYSTEM, brief, coerce, copy_for,
                                      formula)

log = structlog.get_logger(__name__)

# Small on purpose. The output is a few hundred tokens of JSON; the headroom
# is for adaptive thinking, and staying well under ~21k keeps the
# non-streaming request inside its own timeout.
MAX_TOKENS = 8000
# "medium" is the judgement level this needs. It is picking a window and
# writing a line of copy, not proving anything.
EFFORT = "medium"

# What each model accepts, from the Models API, remembered for the life of
# the process. See `_capabilities` for why this is asked rather than assumed.
_CAPS: dict[str, dict] = {}


def _supports(caps: dict, *path: str) -> bool:
    """Walk a capability path, False at the first thing that is missing.

    The Models API documents a full tree with `supported` at every leaf, so
    bracket access would be safe — but this runs against whatever version
    the account is served, and a KeyError here would fail a post over a
    metadata lookup. Missing means "do not send it", which is always safe.
    """
    node = caps
    for key in path:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return bool(isinstance(node, dict) and node.get("supported"))


async def _capabilities(client, model: str) -> dict:
    """What this model actually accepts.

    WHY THIS IS ASKED AND NOT HARDCODED. Thinking and effort are not
    universal: adaptive thinking is a 4.6-and-later feature, `budget_tokens`
    is a 400 on the newest models, and `effort` was behind a beta header
    before it went GA. A table of which-model-takes-what is wrong the moment
    a model is released, and the failure it produces is the nastiest kind —
    every call 400s, every clip silently falls back to the formula, and the
    logs say "API error" rather than "you sent a parameter this model does
    not have". Asking costs one cheap metadata call per model per process.

    A lookup that fails returns {}, which builds the most conservative
    request available: structured output only, no thinking, no effort. That
    shape works on every current model.
    """
    if model in _CAPS:
        return _CAPS[model]
    try:
        info = await client.models.retrieve(model)
        caps = dict(getattr(info, "capabilities", None) or {})
        # The model's own output ceiling, when it is lower than ours: Haiku
        # 4.5 caps at 64K where the Opus models take 128K.
        top = getattr(info, "max_tokens", None)
        if isinstance(top, int) and top > 0:
            caps["_max_tokens"] = top
    except Exception as exc:
        # Deliberately broad: this is a best-effort lookup whose only job is
        # to make the REAL call better shaped. Anything that goes wrong here
        # means "send the conservative request", never "lose the post".
        log.warning("llm_caps_lookup_failed", model=model, error=str(exc)[:200])
        caps = {}
    _CAPS[model] = caps
    return caps


def _request(model: str, caps: dict, payload_json: str) -> dict:
    """The kwargs for one messages.create, shaped to what the model takes."""
    top = caps.get("_max_tokens")
    kw: dict = {
        "model": model,
        "max_tokens": min(MAX_TOKENS, top) if isinstance(top, int) else MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": payload_json}],
    }
    output_config: dict = {}
    if _supports(caps, "structured_outputs"):
        output_config["format"] = {"type": "json_schema", "schema": SCHEMA}
    if _supports(caps, "effort") and _supports(caps, "effort", EFFORT):
        output_config["effort"] = EFFORT
    if output_config:
        kw["output_config"] = output_config
    if _supports(caps, "thinking", "types", "adaptive"):
        # Adaptive only. `budget_tokens` is a 400 on the current models and
        # this task does not need deep reasoning on the ones where it is not.
        kw["thinking"] = {"type": "adaptive"}
    return kw

__all__ = ["MAX_CANDIDATES", "MAX_TRANSCRIPT_LINES", "SCHEMA", "SYSTEM",
           "brief", "coerce", "copy_for", "formula", "configured", "build"]


def configured() -> bool:
    """Whether an Anthropic plan can even be attempted on this box.

    Both halves matter: deploys do not run `pip install`, so the package can
    be missing on a box whose .env has the key.
    """
    if not settings.anthropic_api_key:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


async def build(clips: list[dict], sources: dict, *,
                transcripts: dict | None = None,
                target_s: float = P.TARGET_S) -> tuple[P.EditPlan, dict]:
    """An EditPlan from Claude, or the formula's if anything goes wrong.

    Returns (plan, meta). `meta["source"]` is "llm" or "formula" and
    `meta["reason"]` says why when it is the latter.
    """
    if not configured():
        return formula(clips, sources, "Anthropic not configured", target_s=target_s)
    if not any(c.get("id") in sources for c in clips):
        return formula(clips, sources, "no clip has a file", target_s=target_s)

    import anthropic

    payload = brief(clips, sources, transcripts)
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    model = settings.llm_model
    t0 = time.time()
    try:
        caps = await _capabilities(client, model)
        kwargs = _request(model, caps,
                          json.dumps(payload, separators=(",", ":")))
        resp = await asyncio.wait_for(client.messages.create(**kwargs),
                                      timeout=settings.llm_timeout_s)
    except asyncio.TimeoutError:
        log.warning("llm_plan_timeout", seconds=settings.llm_timeout_s)
        return formula(clips, sources,
                       f"model did not answer in {settings.llm_timeout_s:.0f}s",
                       target_s=target_s)
    except anthropic.BadRequestError as exc:
        # Said separately from the rest because it means WE built a bad
        # request — a parameter this model does not take, or a model id that
        # does not exist. Every clip would fail the same way, so the reason
        # has to name the model rather than say "API error".
        log.warning("llm_plan_bad_request", model=model, error=str(exc)[:300])
        return formula(clips, sources,
                       f"{model} rejected the request: {str(exc)[:120]}",
                       target_s=target_s)
    except anthropic.APIError as exc:
        # Covers connection, status and rate-limit errors alike; every one of
        # them means the same thing here, which is: use the formula.
        log.warning("llm_plan_api_error", error=str(exc)[:300])
        return formula(clips, sources, f"API error: {str(exc)[:120]}",
                       target_s=target_s)

    if resp.stop_reason == "refusal":
        log.warning("llm_plan_refused")
        return formula(clips, sources, "model declined the clip", target_s=target_s)

    try:
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
    except (StopIteration, AttributeError, json.JSONDecodeError) as exc:
        log.warning("llm_plan_unreadable", error=str(exc)[:200])
        return formula(clips, sources, "model returned nothing readable",
                       target_s=target_s)

    plan, notes = coerce(data, sources, clips, target_s=target_s)
    ok, why = P.valid(plan)
    if not ok:
        log.warning("llm_plan_invalid", reason=why, notes=notes)
        return formula(clips, sources, f"plan invalid: {why}", target_s=target_s)

    usage = getattr(resp, "usage", None)
    log.info("llm_plan_built", provider="anthropic", model=model,
             segments=len(plan.segments),
             duration=round(P.plan_duration(plan), 2), notes=notes,
             took=round(time.time() - t0, 2),
             input_tokens=getattr(usage, "input_tokens", None),
             output_tokens=getattr(usage, "output_tokens", None))
    return plan, {"source": "llm", "provider": "anthropic", "reason": "",
                  "copy": copy_for(data, plan), "notes": notes}
