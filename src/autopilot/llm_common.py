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
                    "framing": {"type": "string", "enum": list(P.FRAMINGS)},
                    "layout": {"type": "string", "enum": list(P.LAYOUTS)},
                    "why": {"type": "string"},
                },
                "required": ["clip_id", "start", "end", "zoom", "framing",
                             "layout", "why"],
                "additionalProperties": False,
            },
        },
        "transition": {"type": "string", "enum": list(P.TRANSITIONS)},
        # Burnt-in subtitles. Timed in SOURCE seconds against the clip they
        # belong to, because that is the clock the transcript the model is
        # reading uses. `coerce` converts them to the finished timeline.
        "captions": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["clip_id", "start", "end", "text"],
                "additionalProperties": False,
            },
        },
        "thumbnail": {
            "type": "object",
            "properties": {
                "clip_id": {"type": "string"},
                "at": {"type": "number"},
                "text": {"type": "string"},
            },
            "required": ["clip_id", "at", "text"],
            "additionalProperties": False,
        },
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
    "required": ["segments", "transition", "captions", "thumbnail", "sfx",
                 "title", "caption", "hashtags"],
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

THE CAMERA (per segment). A candidate marked `has_camera: true` has a known camera position on the source, so you may use a camera layout on it. On one marked false you MUST use "none" — there is no camera position, and anything else would crop a piece of gameplay and present it as somebody's face.
- stack: camera across the top, gameplay under it. The streamer-clip standard. Use it whenever there is a camera and the moment is about the person — a reaction, a shout, a laugh. This is what makes a clip look edited rather than cropped.
- corner: gameplay fills the frame with the camera blown up over the top-left. Use it when the gameplay is the moment and the face is the garnish.
- none: no camera treatment. The only option without a camera, and the right one even with a camera when nothing about the moment is the person.
A camera layout replaces the framing choice below, so do not expect blur and stack together.

FRAMING (per segment — the source is 16:9 and the post is 9:16)
- fill: crop to fill the frame. The biggest picture, and the default. What is outside a tall slice of the MIDDLE is gone.
- blur: the whole frame, letterboxed over a blurred blow-up of itself. Nothing is lost, the picture is smaller. Use it when what matters sits at the edge of the shot — a killfeed, a scoreboard, a second player, anything the transcript implies is off to one side.

MOVEMENT
- punch: lands zoomed in and settles. Use it on the opening shot.
- drift / pull: a slow move across the shot. Use them on later shots so the whole thing is not one repeated move.
- none: only when movement would hurt, e.g. on-screen text the viewer has to read.

CAPTIONS (burnt onto the video — most of these are watched on mute)
- Only when you were given a transcript. Never invent a line; if there is no transcript, return an empty list.
- Do NOT transcribe verbatim. Break what was said into SHORT on-screen phrases — three to six words each, the way a caption pops on a clip, not a subtitle track.
- Timed in seconds against THAT CLIP'S OWN FILE, the same clock as `start` and `end`, using the transcript's timings. A cue outside the window you kept is dropped, so do not caption a line you trimmed out.
- Cover the moment itself and the reaction. Silence, filler and dead air get nothing.

THUMBNAIL (the cover frame)
- `clip_id` and `at` name a moment in that clip's own file, inside a window you kept. Pick the peak — the frame that makes somebody stop scrolling, not the first frame.
- `text` is three or four huge words across the middle, or empty. It is read at the size of a phone tile. It is not the title again.

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
            *, target_s: float = P.TARGET_S, mode: str = "clipper",
            facecams: dict | None = None) -> tuple[P.EditPlan, dict]:
    """The deterministic plan, with the reason the model did not supply one.

    Every failure path in every provider ends here. A post is never missed
    because a model was slow, down, or not configured.

    `mode` and `facecams` are carried through rather than defaulted, because
    a fallback that forgot them would quietly change the product: a streamer
    whose model call timed out would get three of somebody's clips welded
    together, and a channel with a known camera would lose its stacked
    layout on exactly the posts where something already went wrong.
    """
    return (P.build(clips, sources, target_s=target_s, mode=mode,
                    facecams=facecams),
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


def brief(clips: list[dict], sources: dict, transcripts: dict | None = None,
          facecams: dict | None = None) -> dict:
    """What the model is told, as plain data so a test can read it.

    Ranked the way the product ranks things, and capped — a model given
    twenty candidates spends its budget comparing them instead of cutting.

    NOTE WHAT IS NOT IN HERE: no file path (the model returns a clip id and
    the builder resolves the path), no account identifier, no email. Whatever
    this dict holds is what leaves the box.
    """
    transcripts = transcripts or {}
    facecams = facecams or {}
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
            # Whether a camera layout is even available for this channel.
            # The position itself is never sent: it is a fact about the
            # channel, not a judgement, and the model does not need it to
            # decide whether the moment is about the person.
            "has_camera": (c.get("channel") or "") in facecams,
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
           *, target_s: float = P.TARGET_S, mode: str = "clipper",
           facecams: dict | None = None) -> tuple[P.EditPlan, list[str]]:
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
    facecams = facecams or {}
    _target, max_segments = P.limits_for(mode)
    segments: list[P.Segment] = []
    budget = target_s

    for raw in (data.get("segments") or [])[:max_segments]:
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
        framing = raw.get("framing") if raw.get("framing") in P.FRAMINGS else "fill"
        channel = by_id.get(cid, {}).get("channel") or ""
        cam = facecams.get(channel)
        layout = raw.get("layout") if raw.get("layout") in P.LAYOUTS else "none"
        if layout != "none" and cam is None:
            # It asked for a camera on a channel with no camera position.
            # Refusing the layout rather than the plan: the cut is still
            # good, it just gets framed instead of stacked.
            notes.append(f"{layout!r} asked for on {channel!r}, which has no camera")
            layout = "none"
        segments.append(P.Segment(
            src=str(path), start=round(start, 3), end=round(end, 3), zoom=zoom,
            framing=framing, layout=layout, facecam=cam if layout != "none" else None,
            clip_id=cid, channel=channel))
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

    plan.captions = _captions_onto_timeline(data, plan, notes)
    _thumbnail_onto_timeline(data, plan, notes)
    return plan, notes


def _segment_for(plan: P.EditPlan, clip_id: str, source_t: float) -> int:
    """Which segment shows that moment of that clip, or -1.

    Matched on the WINDOW as well as the id, because one clip can appear as
    two segments — a plan that opens and closes on the same clip is a normal
    thing to want, and matching on the id alone would put every caption on
    whichever one happened to come first.
    """
    for i, seg in enumerate(plan.segments):
        if seg.clip_id == clip_id and seg.start <= source_t <= seg.end:
            return i
    return -1


def _captions_onto_timeline(data: dict, plan: P.EditPlan, notes: list) -> list:
    """Caption cues in source time, converted to where they actually land.

    THE BUG THIS EXISTS TO NOT HAVE: the renderer shows a caption with
    `enable=between(t,...)`, where `t` is the finished video's clock. A cue
    left in source time would be right on the first segment and wrong by one
    transition more on each one after it — words appearing over the wrong
    moment, getting worse down the video. `plan.timeline_time` does the
    conversion, from the same arithmetic the joins use.
    """
    out = []
    for raw in (data.get("captions") or [])[:40]:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        try:
            src_start = float(raw.get("start"))
            src_end = float(raw.get("end"))
        except (TypeError, ValueError):
            notes.append("caption with no usable timing")
            continue
        cid = str(raw.get("clip_id") or "")
        idx = _segment_for(plan, cid, src_start)
        if idx < 0:
            # Almost always a line the model captioned out of a part of the
            # clip it chose not to keep.
            notes.append("dropped a caption outside every kept window")
            continue
        start = P.timeline_time(plan, idx, src_start)
        # The out-point is clamped into the same segment: a cue whose end ran
        # past the cut would otherwise be dropped whole, losing the caption
        # for a line that IS on screen.
        end = P.timeline_time(plan, idx, min(src_end, plan.segments[idx].end))
        if start < 0 or end < 0 or end <= start:
            notes.append("dropped a caption with an inside-out window")
            continue
        out.append({"start": start, "end": end, "text": text[:120]})
    return out


def _thumbnail_onto_timeline(data: dict, plan: P.EditPlan, notes: list) -> None:
    """The cover frame, likewise converted. Left at -1 if it cannot be
    placed, which the renderer reads as "pick one for me"."""
    raw = data.get("thumbnail")
    if not isinstance(raw, dict):
        return
    plan.thumb_text = str(raw.get("text") or "").strip()[:40]
    try:
        source_t = float(raw.get("at"))
    except (TypeError, ValueError):
        return
    idx = _segment_for(plan, str(raw.get("clip_id") or ""), source_t)
    if idx < 0:
        notes.append("thumbnail moment is not in the cut; picking one")
        return
    plan.thumb_at = P.timeline_time(plan, idx, source_t)


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
