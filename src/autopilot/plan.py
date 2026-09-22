"""The edit plan: what the finished video is, described before it is made.

WHY A PLAN AND NOT A FUNCTION. `render.py` used to take a template name and
do one fixed thing to one clip: a static crop, a title, a fade at each end.
That is not an edit, and a clipper posting it is posting somebody else's
video with a border on it.

What the owner asked for (2026-09-19) is a real cut — around sixty seconds,
sound effects, transitions throughout, framing that moves — and, eventually,
a model deciding all of it. So the renderer is being split in two:

    a BUILDER decides what the video should be  ->  EditPlan
    the RENDERER turns that into one ffmpeg run

This module is the plan and the deterministic builder. The renderer executes
the plan and asks no questions about where it came from, so an LLM builder
can be added later without touching the thing that makes video — and the
deterministic builder stays as the fallback for when the model is slow,
down, or not configured. A post is never missed because a model was busy.

THE DURATION ARITHMETIC IS THE POINT, because "about sixty seconds" is a
hard requirement and not a preference. `xfade` OVERLAPS its two inputs, so
joining segments does not add their lengths:

    total = sum(segment lengths) - (number of joins) x (transition duration)

Three 22-second segments joined by 0.5s transitions is 66 - 1 = 65 seconds,
not 66. Every length here is computed through `plan_duration` so the builder
and the renderer cannot disagree about how long the file will be.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

# The five the browser editor synthesizes (SFX in aurora_html.py). Server-side
# they are pre-rendered WAVs mixed in; the names are shared so a plan means
# the same thing in both places.
SFX_KINDS = ("whoosh", "hit", "pop", "riser", "ding")

# xfade transition names. Kept to ones that read as deliberate on vertical
# video — a dissolve between two gameplay clips looks like a mistake, a wipe
# or a slide reads as a cut somebody made.
TRANSITIONS = ("fade", "slideleft", "slideright", "wipeleft", "circleopen", "dissolve")

# How the picture moves inside a segment. Static framing is what makes a clip
# look like a repost, so "none" exists only as an escape hatch.
ZOOMS = ("none", "punch", "drift", "pull")

# How 16:9 source is fitted into a 9:16 frame — what render.py called a
# "template". Of its four, only these two are really different: `punch` is now
# the per-segment zoom above, and `hook` differed only in how the title was
# drawn. What is left is the actual decision:
#   fill  crop to fill the frame. Biggest picture, but the sides are GONE —
#         wrong when the thing that matters is at the edge (a killfeed, a
#         scoreboard, a second player).
#   blur  the whole frame, letterboxed over a blurred blow-up of itself.
#         Nothing is lost; the picture is smaller.
FRAMINGS = ("fill", "blur")

# What to do with the streamer's camera. The source is one 16:9 frame with a
# camera box somewhere in it; these are the ways that becomes a 9:16 post.
#   none    the camera is not treated specially. Whatever a crop keeps, it
#           keeps — usually nothing, because cameras sit in corners.
#   stack   camera across the top, gameplay under it. The streamer-clip
#           standard, and the same thing the browser editor calls "Cam +
#           game". SPLIT_TOP there is 0.4, and it is 0.4 here.
#   corner  gameplay fills the frame, camera blown up over it.
LAYOUTS = ("none", "stack", "corner")
SPLIT_TOP = 0.4          # must match SPLIT_TOP in aurora_html.py

# Clipper or streamer, which is one question — do you stitch? (owner,
# 2026-09-21: "I need it to be a minute long for clippers. Streamers it does
# not really matter for.")
#   clipper   fill to 60s, stitching up to MAX_SEGMENTS clips together.
#             A clipper is making content, and the length is the format.
#   streamer  one clip, edited well, whatever length it is. A streamer
#             posting their own moment does not want it welded to two others.
MODES = ("clipper", "streamer")

TARGET_S = 60.0          # TikTok and Shorts both treat 60s as the ceiling
TRANS_DUR = 0.5
MIN_SEGMENT_S = 6.0      # shorter than this and a transition eats the shot
MAX_SEGMENTS = 4


@dataclass
class Facecam:
    """Where the camera is in the source, as the browser editor already says it.

    NOT a box. `aurora_html.py`'s split layout parameterises the camera
    window as an OFFSET FROM CENTRE plus a tightness, and these are the same
    three numbers with the same meanings — so a position a user dragged in
    the editor can be stored once and reused by the server verbatim, with no
    conversion to get wrong.

    off_x/off_y are fractions of the source frame away from its centre, so
    (0, 0) is the middle and (-0.38, 0.32) is down and to the left, where a
    camera usually sits. `zoom` is how tight the window is: 2.4 keeps about
    a 40% wide slice. All three are resolution-independent, which is why the
    filtergraph can be built without probing the file.
    """
    off_x: float = 0.0
    off_y: float = 0.0
    zoom: float = 2.4


@dataclass
class Segment:
    """One shot: a window of one source file, and how the frame moves in it."""
    src: str
    start: float
    end: float
    zoom: str = "punch"
    framing: str = "fill"
    layout: str = "none"
    # Where this channel's camera is. None means nobody ever said, and that
    # is exactly why `valid` refuses a camera layout without one: guessing
    # would crop a piece of gameplay and present it as somebody's face.
    facecam: Facecam | None = None
    # Carried for the caption writer and for debugging a bad cut, never used
    # by the renderer.
    clip_id: str = ""
    channel: str = ""

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Sfx:
    """A sound effect, timed against the FINISHED video rather than against
    the segment it belongs to. The renderer mixes into one timeline, and a
    cue expressed relative to a segment would have to be re-based every time
    a transition length changed."""
    at: float
    kind: str = "whoosh"
    gain: float = 0.6


@dataclass
class EditPlan:
    segments: list[Segment] = field(default_factory=list)
    sfx: list[Sfx] = field(default_factory=list)
    transition: str = "slideleft"
    trans_dur: float = TRANS_DUR
    title: str = ""
    # [{start, end, text}] in FINISHED-TIMELINE seconds, which is what the
    # renderer's `enable=between(t,...)` measures. A cue written against the
    # source clip it came from would drift by one transition per join, so
    # anything expressed in source time is converted through
    # `timeline_time()` before it lands here.
    captions: list = field(default_factory=list)
    # The cover frame, in finished-timeline seconds. -1 means nobody chose,
    # so the caller picks; `thumb_text` is burned on if a font exists.
    thumb_at: float = -1.0
    thumb_text: str = ""
    # What produced this plan: "formula" or "llm". Recorded so a bad edit can
    # be traced to its author, and so the admin view can tell them apart.
    source: str = "formula"

    def to_dict(self) -> dict:
        return asdict(self)


def plan_duration(plan: EditPlan) -> float:
    """How long the rendered file will be. See the module docstring: xfade
    overlaps, so joins SUBTRACT."""
    if not plan.segments:
        return 0.0
    total = sum(s.length for s in plan.segments)
    joins = len(plan.segments) - 1
    return max(0.0, total - joins * plan.trans_dur)


def segment_starts(plan: EditPlan) -> list[float]:
    """Where each segment begins on the FINISHED timeline.

    Segment 0 starts at 0. Every one after it starts where its transition
    begins, which is one transition earlier than the raw sum of the lengths
    before it — because xfade OVERLAPS:

        start_k = sum(L0..L(k-1)) - k * d

    Three 22s segments at 0.5s give [0, 21.5, 43.0], and the last ends at
    65.0, which is `plan_duration`. graph.py's join offsets are this list
    without its first entry, and captions in source time are converted with
    `timeline_time`. ONE piece of arithmetic, used by all three — three
    copies is three chances to be one transition out.
    """
    out: list[float] = []
    running = 0.0
    for k, seg in enumerate(plan.segments):
        out.append(round(running - k * plan.trans_dur, 3))
        running += seg.length
    return out


def timeline_time(plan: EditPlan, seg_index: int, source_t: float) -> float:
    """A moment inside a source clip, as a moment in the finished video.

    `source_t` is measured in the ORIGINAL file (the same clock as
    `Segment.start`), because that is the clock a transcript is written in.
    Returns -1 for a time outside the segment's own window: a caption for a
    line that was trimmed out has nowhere to go, and placing it anyway would
    put words on screen over a different moment.
    """
    if not (0 <= seg_index < len(plan.segments)):
        return -1.0
    seg = plan.segments[seg_index]
    if source_t < seg.start or source_t > seg.end:
        return -1.0
    return round(segment_starts(plan)[seg_index] + (source_t - seg.start), 3)


def valid(plan: EditPlan) -> tuple[bool, str]:
    """Whether this plan can be rendered at all. Used on every plan including
    one a model produced, because a hallucinated out-point past the end of the
    file is an ffmpeg error at the end of a three-minute render."""
    if not plan.segments:
        return False, "no segments"
    if len(plan.segments) > MAX_SEGMENTS:
        return False, f"{len(plan.segments)} segments, max {MAX_SEGMENTS}"
    for s in plan.segments:
        # THE WINDOW BEFORE THE LENGTH, because `length` clamps at zero: an
        # inside-out window (end before start) would otherwise be reported as
        # "shorter than 6s" and send the reader looking at the wrong thing.
        # These messages are read when a model produced the plan.
        if s.start < 0 or s.end <= s.start:
            return False, "segment window is inside out"
        if s.length < MIN_SEGMENT_S:
            return False, f"segment shorter than {MIN_SEGMENT_S}s"
        if s.zoom not in ZOOMS:
            return False, f"unknown zoom {s.zoom!r}"
        if s.framing not in FRAMINGS:
            return False, f"unknown framing {s.framing!r}"
        if s.layout not in LAYOUTS:
            return False, f"unknown layout {s.layout!r}"
        # THE RULE THAT MATTERS: a camera layout needs a camera position.
        # Without one the crop would take whatever happens to be at the
        # default offset — a patch of gameplay, shown to a viewer as the
        # streamer's face, on every clip from that channel.
        if s.layout != "none" and s.facecam is None:
            return False, f"layout {s.layout!r} with no facecam position"
    if plan.transition not in TRANSITIONS:
        return False, f"unknown transition {plan.transition!r}"
    # A transition longer than the shot it joins would consume the whole shot.
    if plan.trans_dur <= 0 or plan.trans_dur > MIN_SEGMENT_S / 2:
        return False, "transition duration out of range"
    total = plan_duration(plan)
    for c in plan.sfx:
        if c.kind not in SFX_KINDS:
            return False, f"unknown sfx {c.kind!r}"
        if c.at < 0 or c.at > total:
            return False, "sfx lands outside the video"
    # -1 is "nobody chose"; anything else has to be a frame that exists.
    if plan.thumb_at > total:
        return False, "thumbnail lands outside the video"
    return True, ""


# ── the deterministic builder ────────────────────────────────────────────────

def _window(duration: float, want: float) -> tuple[float, float]:
    """The best `want` seconds of a clip, without knowing what is in it.

    THE ASSUMPTION, written down because an LLM builder will replace it: the
    clip was cut as [trigger - pre_roll, trigger + post_roll] and the presets
    put pre_roll above post_roll, so the moment itself sits around 55-65% of
    the way in and the reaction follows it to the end. Keeping the TAIL is
    therefore right far more often than keeping the head, which is mostly
    lead-in nobody watches.

    Three seconds of run-up before the moment, then everything after it.
    """
    if duration <= want:
        return 0.0, duration
    start = max(0.0, duration - want)
    return start, duration


def rank(clips: list[dict]) -> list[dict]:
    """Highlights first, then by virality (owner, 2026-09-19).

    A highlight is a moment a crowd of viewers clipped themselves, which is
    the strongest signal in the product — stronger than any score the formula
    produces, because it is other people voting. Within each group, virality
    then trigger score, then newest.
    """
    def key(c: dict):
        return (
            0 if c.get("suggested") else 1,
            -float(c.get("virality_score") or 0),
            -float(c.get("score") or c.get("trigger_score") or 0),
            -float(c.get("approved_at") or c.get("created_at") or 0),
        )
    return sorted(clips, key=key)


def _sfx_for(plan: EditPlan) -> list[Sfx]:
    """Sound on every cut, a riser into the first one, and a ding on the way
    out — the whole five-kind palette (SFX_KINDS), not just three of them.

    Timed against the finished video. The first segment's visible length is
    its own length minus half the transition it runs into, so a cue placed at
    a join has to be computed by walking the timeline rather than by summing
    segment lengths — which is the bug this function exists to not have.
    """
    cues: list[Sfx] = []
    if not plan.segments:
        return cues
    # The open: a riser landing on the first frame sells the cut before
    # anything has happened yet.
    cues.append(Sfx(at=0.0, kind="riser", gain=0.5))
    t = 0.0
    for i, seg in enumerate(plan.segments[:-1]):
        t += seg.length - plan.trans_dur
        # The cut itself: a whoosh under the transition, a hit on the landing.
        cues.append(Sfx(at=max(0.0, t), kind="whoosh", gain=0.55))
        cues.append(Sfx(at=max(0.0, t + plan.trans_dur), kind="hit", gain=0.5))
    # The close: a ding a beat before the last frame caps the video off,
    # the way a notification or a "nailed it" sting would. `ding` rings for
    # 0.7s (sfx.py's own recipe), so it lands well inside the video rather
    # than getting clipped by the 0.4s fade-out at the very end.
    total = plan_duration(plan)
    if total > 1.0:
        cues.append(Sfx(at=round(max(0.0, total - 0.6), 3), kind="ding", gain=0.4))
    return cues


def limits_for(mode: str) -> tuple[float, int]:
    """(target seconds, how many clips may be stitched) for a mode.

    The whole clipper/streamer difference is here. A clipper is making
    content and sixty seconds is the format, so stitch up to four. A
    streamer is posting their own moment and does not want it welded to two
    others, so one clip, at whatever length it is — TARGET_S still caps it,
    because nothing posts longer than a minute.
    """
    if mode == "streamer":
        return TARGET_S, 1
    return TARGET_S, MAX_SEGMENTS


def build(clips: list[dict], sources: dict, *, target_s: float = TARGET_S,
          title: str = "", captions: list | None = None,
          transition: str = "slideleft", mode: str = "clipper",
          facecams: dict | None = None) -> EditPlan:
    """An edit plan from the highest-ranked clips that have a file.

    `clips` are candidate clip records; `sources` maps clip id to the path of
    its video and its duration, `{clip_id: (path, duration_s)}` — the caller
    owns the filesystem, this module stays testable without one.

    It fills to `target_s` with as few segments as will reach it, because
    every extra join costs a transition and a shot. One long clip that
    already runs sixty seconds is a perfectly good edit and gets left alone.
    """
    ranked = [c for c in rank(clips) if c.get("id") in sources]
    facecams = facecams or {}
    _target, max_segments = limits_for(mode)
    segments: list[Segment] = []
    remaining = target_s

    for c in ranked:
        if len(segments) >= max_segments or remaining < MIN_SEGMENT_S:
            break
        path, duration = sources[c["id"]]
        if duration < MIN_SEGMENT_S:
            continue
        # Each join gives back trans_dur of runtime, so ask for that much more.
        want = remaining + (TRANS_DUR if segments else 0.0)
        start, end = _window(duration, min(want, duration))
        seg_len = end - start
        if seg_len < MIN_SEGMENT_S:
            continue
        channel = c.get("channel", "")
        cam = facecams.get(channel)
        segments.append(Segment(
            src=str(path), start=round(start, 3), end=round(end, 3),
            # The opener punches in to grab attention; later shots drift so
            # the whole thing is not one repeated move.
            zoom="punch" if not segments else ("drift" if len(segments) % 2 else "pull"),
            # BLUR, not fill (owner, 2026-09-22, having watched the first real
            # render): "I only see half of the clip… I would rather just have
            # it the entire clip with the blurr on the top and the bottom."
            #
            # A 16:9 stream cropped to fill a 9:16 frame keeps a tall slice of
            # the MIDDLE and throws the rest away — which on a gameplay clip
            # means the camera, the killfeed and half the scoreboard are gone.
            # Blur keeps the whole frame at full width over a blurred blow-up
            # of itself, so nothing is lost. The picture is smaller; that is
            # the trade, and it is the one the owner chose after seeing both.
            framing="blur",
            # The formula stacks whenever it knows where the camera is: a
            # clip with a visible streamer reads as edited, and a 16:9 crop
            # that cuts the camera off reads as a repost.
            layout="stack" if cam else "none", facecam=cam,
            clip_id=c.get("id", ""), channel=channel,
        ))
        remaining = target_s - plan_duration(EditPlan(segments=segments,
                                                     trans_dur=TRANS_DUR))

    plan = EditPlan(segments=segments, transition=transition, trans_dur=TRANS_DUR,
                    title=title, captions=list(captions or []), source="formula")
    plan.sfx = _sfx_for(plan)
    return plan
