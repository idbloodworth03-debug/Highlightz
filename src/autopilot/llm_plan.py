"""The model as the builder: an EditPlan decided by Claude, not by a formula.

WHERE THIS SITS. plan.py said it plainly — "a BUILDER decides what the video
should be -> EditPlan; the RENDERER turns that into one ffmpeg run" — and
that an LLM builder could be added later without touching the thing that
makes video. This is that builder. graph.py does not know or care which one
produced the plan it renders.

WHAT THE MODEL CAN ACTUALLY SEE, because this is the whole design constraint:
it cannot watch the video. It gets what the product already knows in words —
the channel, the stream title, the game, how long the file is, whether the
moment was flagged as a highlight, its virality score, and, when captions are
switched on, the Whisper transcript with timestamps. The transcript is the
only thing in that list that says what HAPPENS, which is why the trim gets
markedly better with CAPTIONS_ENABLED=true and is a guess without it.

WHAT IT IS NOT ALLOWED TO DECIDE. It never sees or returns a file path: it
returns a clip id, and this module resolves the path from `sources`. A model
that could name a path could name /etc/passwd, and ffmpeg would read it.
Every number it returns is clamped to the real duration of the real file
before anything is rendered.

THE FALLBACK IS THE POINT. Every failure — no key, no package, a timeout, a
refusal, malformed JSON, a plan that does not validate — returns the
deterministic plan from plan.build() with a reason attached. A post is never
missed because a model was busy. That was true when plan.py was written and
it is still the contract.

PRIVACY. This sends clip metadata and, when they exist, transcripts to
Anthropic. That is a third party the Privacy Policy now names (Section 3).
It never sends the video, the user's email, or anything identifying the
account — the brief carries a channel name and words spoken on a public
broadcast, nothing else. Off by default: AUTOPILOT_LLM=true to turn it on.
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog

from config.settings import settings
from src.autopilot import plan as P

log = structlog.get_logger(__name__)

# Small on purpose. The output is a few hundred tokens of JSON; the headroom
# is for adaptive thinking, and staying well under ~21k keeps the
# non-streaming request inside its own timeout.
MAX_TOKENS = 8000
# "medium" is the judgement level this needs. It is picking a window and
# writing a line of copy, not proving anything.
EFFORT = "medium"
MAX_TRANSCRIPT_LINES = 60      # per clip; a 60s clip is nowhere near this
MAX_CANDIDATES = 8


# The shape the model must return. `additionalProperties: false` and the enums
# do the first pass of validation at the API; _coerce does the second, against
# the actual files, because a schema cannot know how long a clip is.
SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": P.MAX_SEGMENTS,
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "zoom": {"type": "string", "enum": list(P.ZOOMS)},
                    "why": {"type": "string"},
                },
                "required": ["clip_id", "start", "end", "zoom", "why"],
                "additionalProperties": False,
            },
        },
        "transition": {"type": "string", "enum": list(P.TRANSITIONS)},
        "sfx": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "at": {"type": "number"},
                    "kind": {"type": "string", "enum": list(P.SFX_KINDS)},
                    "gain": {"type": "number"},
                },
                "required": ["at", "kind", "gain"],
                "additionalProperties": False,
            },
        },
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
    },
    "required": ["segments", "transition", "sfx", "title", "caption", "hashtags"],
    "additionalProperties": False,
}


SYSTEM = f"""You are the editor for Highlightz, which turns moments from live streams into short vertical videos its users post to TikTok, Reels and Shorts.

You are given candidate clips with their metadata and, where available, a transcript with timestamps. You cannot see the video. Decide what the finished cut is and return it as JSON.

THE CUT
- Aim for {int(P.TARGET_S)} seconds of finished video. Joins OVERLAP: the finished length is the sum of the segment lengths minus (number of joins) x {P.TRANS_DUR} seconds. Two 30s segments make {2 * 30 - P.TRANS_DUR:.1f}s, not 60.
- At most {P.MAX_SEGMENTS} segments. No segment shorter than {int(P.MIN_SEGMENT_S)} seconds — a transition would eat it.
- Fewer, longer segments beat more, shorter ones. One clip that already carries 60 good seconds is a finished video; do not cut it up to look busy.
- Order them so the strongest moment is FIRST. The first two seconds decide whether the video is watched at all.
- `start` and `end` are seconds inside that clip's own file. Never exceed the duration you were given.

WHERE TO CUT
- With a transcript: start a few seconds before the line that sets the moment up, end on the reaction, and cut dead air.
- Without one: the clip was cut around the moment with more lead-in than tail, so the moment sits roughly 55-65% of the way in. Keep the back half.

MOVEMENT
- punch: lands zoomed in and settles. Use it on the opening shot.
- drift / pull: a slow move across the shot. Use them on later shots so the whole thing is not one repeated move.
- none: only when movement would hurt, e.g. on-screen text the viewer has to read.

SOUND
- whoosh under each transition, hit on the landing right after it, riser into the open, pop and ding as accents on a punchline or a number appearing.
- `at` is seconds on the FINISHED timeline, not inside a segment. Join k lands at (sum of the lengths of the segments up to and including k) - (k+1) x the transition duration.
- gain is 0.4-0.7. Louder than that and it buries the speech.

COPY
- title: under {60} characters, burned onto the video. It is a hook, not a summary. No quotation marks around it, no emoji, no clickbait that the clip does not pay off.
- caption: the post text. One or two short lines in the voice of somebody who made the clip.
- hashtags: 3-6, without the # character, lowercase, specific to the game and the streamer before anything generic.

Write for a viewer who has never heard of this streamer. Never invent something that happened — you only know what the metadata and transcript say."""


def configured() -> bool:
    """Whether an LLM plan can even be attempted on this box.

    Both halves matter: deploys do not run `pip install`, so the package can
    be missing on a box whose .env has the key.
    """
    if not (settings.autopilot_llm and settings.anthropic_api_key):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


# ── the brief ────────────────────────────────────────────────────────────────

def _lines(transcript: list | None) -> list:
    """Transcript as [start, end, text], rounded. Accepts either the shape
    captions are stored in ({"start","end","text"}) or the tuples runner.py
    passes around, because both already exist in this codebase."""
    out = []
    for row in (transcript or [])[:MAX_TRANSCRIPT_LINES]:
        try:
            if isinstance(row, dict):
                start, end, text = row["start"], row["end"], row.get("text") or ""
            else:
                start, end, text = row[0], row[1], row[2]
            text = str(text).strip()
            if text:
                out.append([round(float(start), 1), round(float(end), 1), text[:200]])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def brief(clips: list[dict], sources: dict, transcripts: dict | None = None) -> dict:
    """What the model is told, as plain data so a test can read it.

    Ranked the way the product ranks things, and capped — a model given
    twenty candidates spends its budget comparing them instead of cutting.
    """
    transcripts = transcripts or {}
    out = []
    for c in P.rank(clips):
        cid = c.get("id")
        if cid not in sources:
            continue
        _path, duration = sources[cid]
        row = {
            "clip_id": cid,
            "duration": round(float(duration), 2),
            "channel": c.get("channel") or "",
            "stream_title": (c.get("stream_title") or c.get("clip_title") or "")[:140],
            "game": c.get("game") or c.get("category") or "",
            "highlight": bool(c.get("suggested")),
            "virality": round(float(c.get("virality_score") or 0), 1),
        }
        lines = _lines(transcripts.get(cid))
        if lines:
            row["transcript"] = lines
        out.append(row)
        if len(out) >= MAX_CANDIDATES:
            break
    return {"target_seconds": P.TARGET_S, "transition_duration": P.TRANS_DUR,
            "candidates": out}


# ── the model's answer, made safe ────────────────────────────────────────────

def _coerce(data: dict, sources: dict, clips: list[dict],
            *, target_s: float) -> tuple[P.EditPlan, list[str]]:
    """The model's JSON as an EditPlan that cannot hurt anything.

    Everything here is a clamp rather than a rejection, because a plan that is
    two seconds long is still a usable video once it is trimmed, and throwing
    it away costs the whole call. The notes are returned so a bad habit shows
    up in the logs instead of being silently corrected forever.
    """
    notes: list[str] = []
    by_id = {c.get("id"): c for c in clips}
    segments: list[P.Segment] = []
    budget = target_s

    for raw in (data.get("segments") or [])[:P.MAX_SEGMENTS]:
        cid = str(raw.get("clip_id") or "")
        if cid not in sources:
            notes.append(f"unknown clip {cid!r}")
            continue
        path, duration = sources[cid]
        duration = float(duration)
        if duration < P.MIN_SEGMENT_S:
            continue
        try:
            start = float(raw.get("start", 0.0))
            end = float(raw.get("end", 0.0))
        except (TypeError, ValueError):
            notes.append("non-numeric window")
            continue
        # Into the file, in order, long enough, and not past the end.
        start = min(max(0.0, start), duration - P.MIN_SEGMENT_S)
        end = min(max(start + P.MIN_SEGMENT_S, end), duration)
        if end - start > duration:                      # belt and braces
            end = duration

        # A join gives back trans_dur of runtime, so the budget grows by one
        # transition for every segment after the first.
        room = budget + (P.TRANS_DUR if segments else 0.0)
        if room < P.MIN_SEGMENT_S:
            notes.append("dropped a segment that would not fit the target")
            break
        if end - start > room:
            end = start + room
            notes.append("trimmed a segment to the target length")

        zoom = raw.get("zoom") if raw.get("zoom") in P.ZOOMS else "punch"
        segments.append(P.Segment(
            src=str(path), start=round(start, 3), end=round(end, 3), zoom=zoom,
            clip_id=cid, channel=(by_id.get(cid, {}).get("channel") or "")))
        budget = target_s - P.plan_duration(
            P.EditPlan(segments=segments, trans_dur=P.TRANS_DUR))

    transition = data.get("transition")
    if transition not in P.TRANSITIONS:
        notes.append(f"unknown transition {transition!r}")
        transition = "slideleft"

    plan = P.EditPlan(segments=segments, transition=transition,
                      trans_dur=P.TRANS_DUR,
                      title=str(data.get("title") or "")[:60].strip(),
                      source="llm")

    total = P.plan_duration(plan)
    cues: list[P.Sfx] = []
    for raw in (data.get("sfx") or [])[:12]:
        kind = raw.get("kind")
        if kind not in P.SFX_KINDS:
            notes.append(f"unknown sfx {kind!r}")
            continue
        try:
            at = float(raw.get("at", 0.0))
            gain = float(raw.get("gain", 0.55))
        except (TypeError, ValueError):
            continue
        if at < 0 or at > total:
            notes.append("dropped a cue outside the video")
            continue
        cues.append(P.Sfx(at=round(at, 3), kind=kind,
                          gain=round(min(0.9, max(0.1, gain)), 2)))
    plan.sfx = cues
    if segments and not cues:
        # Silence is the one outcome this whole feature exists to avoid.
        plan.sfx = P._sfx_for(plan)
        notes.append("no usable cues, fell back to the formula's")
    return plan, notes


def _copy(data: dict, plan: P.EditPlan) -> dict:
    """Title, caption and hashtags for the post. Kept out of EditPlan: the
    plan is what the renderer needs, this is what the scheduler needs."""
    tags = []
    for t in (data.get("hashtags") or [])[:8]:
        t = "".join(ch for ch in str(t) if ch.isalnum() or ch == "_").lower()
        if t and t not in tags:
            tags.append(t)
    return {"title": plan.title,
            "caption": str(data.get("caption") or "")[:2200].strip(),
            "hashtags": tags}


# ── the call ─────────────────────────────────────────────────────────────────

async def build(clips: list[dict], sources: dict, *,
                transcripts: dict | None = None,
                target_s: float = P.TARGET_S) -> tuple[P.EditPlan, dict]:
    """An EditPlan from the model, or the formula's if anything goes wrong.

    Returns (plan, meta). `meta["source"]` is "llm" or "formula" and
    `meta["reason"]` says why when it is the latter — that string is what
    gets logged and, eventually, shown to the owner in the admin view.
    """
    fallback = lambda why: (                                   # noqa: E731
        P.build(clips, sources, target_s=target_s),
        {"source": "formula", "reason": why, "copy": {}, "notes": []})

    if not configured():
        return fallback("LLM not configured")
    if not any(c.get("id") in sources for c in clips):
        return fallback("no clip has a file")

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
        return fallback(f"model did not answer in {settings.llm_timeout_s:.0f}s")
    except anthropic.APIError as exc:
        # Covers connection, status and rate-limit errors alike; every one of
        # them means the same thing here, which is: use the formula.
        log.warning("llm_plan_api_error", error=str(exc)[:300])
        return fallback(f"API error: {str(exc)[:120]}")

    if resp.stop_reason == "refusal":
        log.warning("llm_plan_refused")
        return fallback("model declined the clip")

    try:
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
    except (StopIteration, AttributeError, json.JSONDecodeError) as exc:
        log.warning("llm_plan_unreadable", error=str(exc)[:200])
        return fallback("model returned nothing readable")

    plan, notes = _coerce(data, sources, clips, target_s=target_s)
    ok, why = P.valid(plan)
    if not ok:
        log.warning("llm_plan_invalid", reason=why, notes=notes)
        return fallback(f"plan invalid: {why}")

    usage = getattr(resp, "usage", None)
    log.info("llm_plan_built", segments=len(plan.segments),
             duration=round(P.plan_duration(plan), 2), notes=notes,
             took=round(time.time() - t0, 2),
             input_tokens=getattr(usage, "input_tokens", None),
             output_tokens=getattr(usage, "output_tokens", None))
    return plan, {"source": "llm", "reason": "", "copy": _copy(data, plan),
                  "notes": notes}
