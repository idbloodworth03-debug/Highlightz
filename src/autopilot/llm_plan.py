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
    t0 = time.time()
    try:
        resp = await asyncio.wait_for(
            client.messages.create(
                model=settings.llm_model,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT,
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                system=SYSTEM,
                messages=[{"role": "user",
                           "content": json.dumps(payload, separators=(",", ":"))}],
            ),
            timeout=settings.llm_timeout_s)
    except asyncio.TimeoutError:
        log.warning("llm_plan_timeout", seconds=settings.llm_timeout_s)
        return formula(clips, sources,
                       f"model did not answer in {settings.llm_timeout_s:.0f}s",
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
    log.info("llm_plan_built", provider="anthropic", segments=len(plan.segments),
             duration=round(P.plan_duration(plan), 2), notes=notes,
             took=round(time.time() - t0, 2),
             input_tokens=getattr(usage, "input_tokens", None),
             output_tokens=getattr(usage, "output_tokens", None))
    return plan, {"source": "llm", "provider": "anthropic", "reason": "",
                  "copy": copy_for(data, plan), "notes": notes}
