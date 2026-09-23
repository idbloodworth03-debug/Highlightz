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

# Clipper or streamer. Both are ONE clip now (owner, 2026-09-23: "instead of
# combining clips just keep it only to one clip"); the modes are kept because
# onboarding stores one and the Autopilot config carries it, and a later
# difference between them has somewhere to live.
MODES = ("clipper", "streamer")

# JUST OVER A MINUTE, not 60 (owner, 2026-09-23). TikTok's Creator Rewards
# pays only for videos LONGER than one minute, and a 60.00s video is not
# longer than one minute — so every formula video missed the line by
# nothing. 62 leaves two seconds so frame timing and container rounding
# cannot push a finished file back to 59.9x, while staying "about a minute"
# ("do not make clips too long either"). Length is necessary for Creator
# Rewards, not sufficient: originality is judged separately.
TARGET_S = 62.0
# TikTok pays only for videos LONGER than 60s. How it rounds a 60.4s file is
# not something to find out on a user's account, so the builder treats
# anything under 61s as not over the line.
SAFELY_OVER_S = 61.0
TRANS_DUR = 0.5
MIN_SEGMENT_S = 6.0      # shorter than this and a transition eats the shot
# The absolute ceiling `valid()` enforces on ANY plan, a model's included.
# The builders use far fewer: one clip, plus the hook when there is one.
MAX_SEGMENTS = 4
WHOOSH_GAIN = 0.6

# THE HOOK (owner, 2026-09-23). The user picks 5-10 seconds of the clip — the
# hype moment or the controversial line — and the video OPENS on it as bait,
# slides across to the clip playing from its own start, and slides out at
# the end:
#
#     [slide in] HOOK (5-10s) [slide + whoosh] WHOLE CLIP from 0 [slide out]
#
# It is the one manual step in an otherwise automatic edit: nothing reading
# metadata can tell which five seconds are the moment, and the person who
# approved the clip can. A hook is the only segment allowed under
# MIN_SEGMENT_S, because it is the only one chosen by a human for its length.
HOOK_MIN_S = 5.0
HOOK_MAX_S = 10.0
# The clip after the hook plays whole, from its start. Capped only so a long
# upload cannot turn into a video past YouTube Shorts' three minutes; stream
# clips are about a minute and never reach it.
MAX_MAIN_S = 170.0


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
    # The open and the close (owner, 2026-09-23: "one at the beginning like it
    # sliding into frame with a whoosh sound and then one at the end with it
    # sliding out and the same sound"). slide_in: the first frame slides in
    # from the right over black; slide_out: the last slides off to the left.
    # Both take `trans_dur` and neither changes the video's length. Off by
    # default, so a model's plan (and every older plan) keeps its fades.
    slide_in: bool = False
    slide_out: bool = False
    # Segment 0 is the HOOK: a 5-10s replay of part of segment 1, shown first
    # as bait before segment 1 plays the clip from its start. `valid()` holds
    # the shape to exactly that, so a plan cannot claim a hook it does not
    # have.
    hook: bool = False
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
    if plan.hook:
        ok, why = _hook_shape(plan)
        if not ok:
            return False, why
    for k, s in enumerate(plan.segments):
        # THE WINDOW BEFORE THE LENGTH, because `length` clamps at zero: an
        # inside-out window (end before start) would otherwise be reported as
        # "shorter than 6s" and send the reader looking at the wrong thing.
        # These messages are read when a model produced the plan.
        if s.start < 0 or s.end <= s.start:
            return False, "segment window is inside out"
        # The hook is the one shot a person chose the length of; it has its
        # own bounds, checked in _hook_shape.
        if s.length < MIN_SEGMENT_S and not (plan.hook and k == 0):
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
    if (plan.slide_in or plan.slide_out) and total <= 2 * plan.trans_dur:
        return False, "too short to slide in and out"
    for c in plan.sfx:
        if c.kind not in SFX_KINDS:
            return False, f"unknown sfx {c.kind!r}"
        if c.at < 0 or c.at > total:
            return False, "sfx lands outside the video"
    # -1 is "nobody chose"; anything else has to be a frame that exists.
    if plan.thumb_at > total:
        return False, "thumbnail lands outside the video"
    return True, ""


def _hook_shape(plan: EditPlan) -> tuple[bool, str]:
    """A hooked plan is exactly: the hook, then the clip it was taken from.

    The hook has to be a window of THE SAME FILE the second segment plays,
    inside what that segment shows — it is a preview of this clip, and a
    "hook" from some other clip would promise a moment the video never
    delivers."""
    if len(plan.segments) != 2:
        return False, "a hooked plan is the hook and one clip"
    hook, main = plan.segments
    if hook.src != main.src:
        return False, "the hook is not from the clip it opens"
    if not (HOOK_MIN_S - 1e-6 <= hook.length <= HOOK_MAX_S + 1e-6):
        return False, f"hook must be {HOOK_MIN_S:g}-{HOOK_MAX_S:g}s"
    if hook.start < main.start - 1e-6 or hook.end > main.end + 1e-6:
        return False, "the hook is outside the part of the clip that plays"
    return True, ""


def hook_window(clip: dict, duration: float) -> tuple[float, float] | None:
    """The user's hook for this clip, as a window that can be rendered, or
    None.

    Stored on the clip as {"hook": {"start": s, "end": e}} in the clip's own
    seconds. Anything unusable — missing, non-numeric, off the end of the
    file, or outside 5-10s — is None rather than an error: the clip still
    gets its edit, just without the opener.
    """
    raw = clip.get("hook")
    if not isinstance(raw, dict):
        return None
    try:
        start = float(raw.get("start"))
        end = float(raw.get("end"))
    except (TypeError, ValueError):
        return None
    if start < 0 or end > duration + 0.05 or end <= start:
        return None
    end = min(end, duration)
    if not (HOOK_MIN_S - 1e-6 <= end - start <= HOOK_MAX_S + 1e-6):
        return None
    return round(start, 3), round(end, 3)


# ── the deterministic builder ────────────────────────────────────────────────

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
    """One sound, the same whoosh, wherever the picture slides — and nowhere
    else (owner, 2026-09-23: a riser, hits, pops, a ding and a different
    transition on every cut were "not going to work"):

        the open    as the first frame slides in       (if slide_in)
        each cut    as one clip slides out and the next slides in
        the close   as the last frame slides out       (if slide_out)

    Timed against the finished video. A cut's whoosh sits where its
    transition STARTS, which is found by walking the timeline — summing
    segment lengths instead puts every cut one transition late, the bug this
    function exists to not have.
    """
    cues: list[Sfx] = []
    if not plan.segments:
        return cues
    d = plan.trans_dur
    if plan.slide_in:
        cues.append(Sfx(at=0.0, kind="whoosh", gain=WHOOSH_GAIN))
    t = 0.0
    for seg in plan.segments[:-1]:
        t += seg.length - d
        cues.append(Sfx(at=round(max(0.0, t), 3), kind="whoosh", gain=WHOOSH_GAIN))
    if plan.slide_out:
        total = plan_duration(plan)
        cues.append(Sfx(at=round(max(0.0, total - d), 3), kind="whoosh", gain=WHOOSH_GAIN))
    return cues


def limits_for(mode: str) -> tuple[float, int]:
    """(target seconds, how many clips may be used) for a mode.

    ONE CLIP for every mode (owner, 2026-09-23: "instead of combining clips
    just keep it only to one clip"). Stitching up to four clips to reach
    just over a minute was the rule until that day; the hook replaced it as
    the thing that makes the edit more than a repost. Kept as a function of
    the mode so a future difference has one place to go.
    """
    return TARGET_S, 1


def build(clips: list[dict], sources: dict, *, target_s: float = TARGET_S,
          title: str = "", captions: list | None = None,
          transition: str = "slideleft", mode: str = "clipper",
          facecams: dict | None = None) -> EditPlan:
    """The edit plan for the highest-ranked clip that has a file.

    `clips` are candidate clip records; `sources` maps clip id to the path of
    its video and its duration, `{clip_id: (path, duration_s)}` — the caller
    owns the filesystem, this module stays testable without one.

    ONE CLIP (owner, 2026-09-23), played whole from its start. If the user
    picked a hook on it (`clip["hook"]`, see `hook_window`), the video opens
    on that 5-10s first and slides across to the clip:

        [slide in] HOOK [slide + whoosh] CLIP from 0 [slide out]

    Without a hook it is the clip alone, sliding in and out. `target_s` no
    longer trims anything — the clip is as long as it is — and is kept in
    the signature so every caller, and both model builders, still pass the
    same arguments.
    """
    facecams = facecams or {}
    for c in rank(clips):
        if c.get("id") not in sources:
            continue
        path, duration = sources[c["id"]]
        duration = float(duration)
        if duration < MIN_SEGMENT_S:
            continue
        break
    else:
        return EditPlan(segments=[], transition=transition, title=title,
                        captions=list(captions or []), source="formula")

    channel = c.get("channel", "")
    cam = facecams.get(channel)

    def shot(start: float, end: float, zoom: str) -> Segment:
        return Segment(
            src=str(path), start=round(start, 3), end=round(end, 3), zoom=zoom,
            # BLUR, not fill (owner, 2026-09-22, having watched the first real
            # render): "I only see half of the clip… I would rather just have
            # it the entire clip with the blurr on the top and the bottom."
            #
            # A 16:9 stream cropped to fill a 9:16 frame keeps a tall slice of
            # the MIDDLE and throws the rest away — which on a gameplay clip
            # means the camera, the killfeed and half the scoreboard are gone.
            # Blur keeps the whole frame at full width over a blurred blow-up
            # of itself, so nothing is lost.
            framing="blur",
            # The formula stacks whenever it knows where the camera is: a
            # clip with a visible streamer reads as edited, and a 16:9 crop
            # that cuts the camera off reads as a repost.
            layout="stack" if cam else "none", facecam=cam,
            clip_id=c.get("id", ""), channel=channel,
        )

    main_end = min(duration, MAX_MAIN_S)
    hook = hook_window(c, main_end)
    segments: list[Segment] = []
    if hook:
        # The opener punches in to grab attention; the clip after it drifts,
        # so the two shots are not one repeated move.
        segments.append(shot(hook[0], hook[1], "punch"))
        segments.append(shot(0.0, main_end, "drift"))
    else:
        segments.append(shot(0.0, main_end, "punch"))

    # The video slides in at the start and out at the end, and the hook
    # slides across to the clip — the same move and the same whoosh
    # throughout (owner, 2026-09-23). `slideleft` in xfade is a push: the
    # outgoing shot leaves to the left as the next enters from the right, so
    # the whole video moves one way, like a feed.
    plan = EditPlan(segments=segments, transition=transition, trans_dur=TRANS_DUR,
                    slide_in=True, slide_out=True, hook=bool(hook),
                    title=title, captions=list(captions or []), source="formula")
    plan.sfx = _sfx_for(plan)
    return plan
