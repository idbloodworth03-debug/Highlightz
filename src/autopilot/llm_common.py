"""What every model builder shares: the brief, the schema, and the clamps.

WHY THIS IS ITS OWN MODULE. There are two builders that ask a model to
design an edit — Anthropic's (`llm_plan.py`) and a local Ollama
(`ollama_plan.py`) — and exactly one thing about them differs: the HTTP call.
What the model is told, what it is allowed to answer, and what is done to
that answer before anything is rendered are identical, and they are the parts
that keep a hallucination from reaching ffmpeg.

Duplicating `_coerce` per provider would mean two places to remember that an
out-point must be clamped to the real file. The safety rails live here once,
and a new provider is a function that returns a dict.

NOTHING HERE MAKES A NETWORK CALL, which is what lets the whole surface be
tested without a model, a key, or a GPU.
"""

from __future__ import annotations

from src.autopilot import plan as P

MAX_TRANSCRIPT_LINES = 60      # per clip; a 60s clip is nowhere near this
MAX_CANDIDATES = 8


# The shape a model must return. `additionalProperties: false` and the enums
# do the first pass of validation at the provider — Anthropic enforces it as
# `output_config.format`, Ollama as `format` — and `coerce` does the second,
# against the actual files, because a schema cannot know how long a clip is.
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


def formula(clips: list[dict], sources: dict, why: str,
            *, target_s: float = P.TARGET_S) -> tuple[P.EditPlan, dict]:
    """The deterministic plan, with the reason the model did not supply one.

    Every failure path in every provider ends here. A post is never missed
    because a model was slow, down, or not configured.
    """
    return (P.build(clips, sources, target_s=target_s),
            {"source": "formula", "reason": why, "copy": {}, "notes": []})


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

    NOTE WHAT IS NOT IN HERE: no file path (the model returns a clip id and
    the builder resolves the path), no account identifier, no email. Whatever
    this dict holds is what leaves the box.
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

def coerce(data: dict, sources: dict, clips: list[dict],
           *, target_s: float = P.TARGET_S) -> tuple[P.EditPlan, list[str]]:
    """The model's JSON as an EditPlan that cannot hurt anything.

    THE ONE RULE: the model names a clip id, never a path. This resolves the
    path from `sources`. A model that could name a path could name
    /etc/passwd, and ffmpeg would read it.

    Everything else here is a clamp rather than a rejection, because a plan
    that is two seconds long is still a usable video once it is trimmed, and
    throwing it away costs the whole call. The notes are returned so a bad
    habit shows up in the logs instead of being silently corrected forever —
    which matters more for a small local model than for a large hosted one.
    """
    notes: list[str] = []
    by_id = {c.get("id"): c for c in clips}
    segments: list[P.Segment] = []
    budget = target_s

    for raw in (data.get("segments") or [])[:P.MAX_SEGMENTS]:
        if not isinstance(raw, dict):
            notes.append("segment was not an object")
            continue
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
        if not isinstance(raw, dict):
            continue
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


def copy_for(data: dict, plan: P.EditPlan) -> dict:
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
