"""An EditPlan as one ffmpeg command.

WHY THIS IS A SEPARATE MODULE FROM render.py. The graph is a long string and
nothing in this container can run it — ffmpeg is not installed here, only on
production. A twelve-filter graph that is wrong fails as one opaque ffmpeg
error after a three-minute render, so the only defence available is to build
it as pure data and assert its shape in tests. Keeping it out of the module
that spawns processes is what makes that possible.

THE SHAPE OF THE COMMAND

    -ss/-t per input      each segment is trimmed by its INPUT, not by a trim
                          filter: ffmpeg seeks instead of decoding and
                          discarding, which on a 1-vCPU box is the difference
                          between a render and a timeout
    scale+crop            16:9 source to 1080x1920, filling rather than boxing
    zoompan               the frame moves; a static crop is what makes a clip
                          look like a repost
    xfade                 joins, which OVERLAP (see plan.plan_duration)
    acrossfade            the same overlap on audio, or the sound drifts one
                          transition further out of step at every cut
    adelay + amix         sound effects onto the finished timeline
    drawtext              title and captions, as before

EVERY OFFSET IS COMPUTED ONCE, in `_offsets`, and used for video, audio and
sound effects alike. Three places computing "where does the second shot
start" is three places to get it wrong by one transition.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

from src.autopilot.plan import (SPLIT_TOP, EditPlan, Facecam, Segment,
                                plan_duration, segment_starts)

FPS = 30
W, H = 1080, 1920

# How far each move travels. Small on purpose: a 1.2x punch reads as a camera
# move, a 1.6x punch reads as a mistake and softens the picture.
ZOOM_MAX = 1.20
PUNCH_SETTLE_S = 0.8

# ── burnt-in captions ────────────────────────────────────────────────────────
# Owner, 2026-09-23: the boxed 54px white-on-black captions were "bland black
# and white". The look now is the browser Clip Editor's (`drawCaption` in
# aurora_html.py) as far as drawtext can take it: big uppercase type, a black
# outline and a soft drop shadow instead of a plate, and the editor's amber
# accent (CAP_ACCENT, #F7A745) on every other cue so the captions have a
# pulse instead of sitting there.
#
# What drawtext CANNOT do is the editor's active-word highlight: one drawtext
# is one colour, and laying a second one over a single word needs the pixel
# widths of the words before it. That needs libass (the `ass` filter), not a
# cleverer drawtext.
CAPTION_COLOURS = ("white", "0xF7A745")
CAPTION_Y = 0.78            # centre of the block — the editor's default, and
                            # with blur framing it sits in the blurred band,
                            # below the picture rather than over the gameplay
CAPTION_MAX_PX = 100        # the editor's default is 0.055 x 1920 = 105
CAPTION_SPLIT_BELOW = 88    # one line smaller than this: wrap instead. The
                            # editor never shrinks a caption, it wraps at one
                            # size; this keeps every cue within ~12% of that
                            # instead of jumping between 72 and 100
CAPTION_MAX_LINES = 3
CAPTION_FLOOR_PX = 24
CAPTION_WIDTH = W * 0.86    # the share of the frame the editor wraps at

# How wide each character is, in thousandths of the font size, in DejaVu Sans
# Bold — the font on the box (`FONT:` in edit_preview's output). drawtext
# does NOT wrap: a line sized past the frame just runs off both edges, with
# no error, so these numbers are the only thing keeping a caption on screen.
#
# A TABLE, NOT AN AVERAGE. The first version used one average width per
# character (0.75, the widest line of a real transcript) and a test that
# measured real lines caught "WOW WOW WOW WOW" running 1104px on a 1080px
# frame: W is 1.10 of the font size, `I` is 0.37, and a line of wide letters
# is ordinary stream chat. Summing real advances is an upper bound on the
# drawn width (kerning only ever tightens a line), so a line that fits here
# fits on screen. Measured with Pillow at 1000px — Pillow is test-only
# (requirements.txt), so the numbers are baked in rather than measured on
# the box. Anything outside printable ASCII counts as a full em: accented
# letters come out a little small rather than off the edge.
_ADVANCE_CHARS = "".join(chr(c) for c in range(32, 127))
_ADVANCE_MILLI = (
    348, 456, 521, 838, 696, 1002, 872, 306, 457, 457, 523, 838, 380, 415, 380,
    365, 696, 696, 696, 696, 696, 696, 696, 696, 696, 696, 400, 400, 838, 838,
    838, 580, 1000, 774, 762, 734, 830, 683, 683, 821, 837, 372, 372, 775, 637,
    995, 837, 850, 733, 850, 770, 720, 682, 812, 774, 1103, 771, 724, 725, 457,
    365, 457, 838, 500, 500, 675, 716, 593, 716, 678, 435, 716, 712, 343, 343,
    665, 343, 1042, 712, 687, 716, 716, 493, 595, 478, 712, 652, 924, 645, 652,
    582, 712, 365, 712, 838)
CAPTION_ADVANCE = dict(zip(_ADVANCE_CHARS, (m / 1000 for m in _ADVANCE_MILLI)))


def _esc(text: str) -> str:
    """Escape `text` for an UNQUOTED `drawtext=...:text=<here>:...`.

    A filtergraph is parsed TWICE, and each pass consumes one layer of
    quotes and backslashes (ffmpeg-filters(1), "Notes on filtergraph
    escaping"):

      1. the graph parser cuts out each filter's options, stopping at an
         unescaped [ ] , ;
      2. the filter's option parser cuts those into key=value pairs,
         stopping at an unescaped :

    So the text is escaped for pass 2 first (\\ ' :), then that result is
    escaped again for pass 1 (\\ ' [ ] , ;). This reproduces the docs' own
    worked example character for character — pinned in
    tests/test_edit_graph.py, which also runs every drawtext this module
    builds through `tests/ffparse.py`, a port of both parsers.

    TWO LIVE FAILURES, BOTH ON AN APOSTROPHE, BOTH FROM HANDLING ONE PASS:
      2026-09-22  `text='It\\'s ...'` — inside pass 1's quotes a backslash
                  is literal, so the quote closed early and the rest of the
                  graph became garbage: "Filter not found".
      2026-09-23  `text='I'\\''M GONNA'` (close, escape, reopen) survived
                  pass 1, but handed pass 2 a bare `'` — which opened a
                  quote that swallowed every option after it: colour, size,
                  position and timing all became caption text. "Error
                  initializing complex filters: Invalid argument".
    The note that shipped with the first fix also claimed escaping `:` and
    `,` had been pointless — it was not; those escapes were aimed at pass 2.

    `%` and a backslash are the third layer, drawtext's own text expansion,
    and are switched off with `expansion=none` rather than escaped: a
    caption is speech, never a template.
    """
    once = "".join("\\" + c if c in "\\':" else c for c in text)
    return "".join("\\" + c if c in "\\'[],;" else c for c in once)


def _ems(line: str) -> float:
    """How wide `line` is, in multiples of the font size."""
    return sum(CAPTION_ADVANCE.get(ch, 1.0) for ch in line)


def _caption_px(line: str) -> int:
    """The biggest font size at which `line` stays inside CAPTION_WIDTH."""
    return int(CAPTION_WIDTH / max(_ems(line), 0.5))


def _split(words: list[str], k: int) -> list[str]:
    """`words` as `k` lines, balanced so the widest line is as narrow as it
    can be — the widest line is what sets the font size. Balanced on real
    width, not character count: "WWW" is wider than "IIIIII"."""
    if k <= 1 or len(words) < k:
        return [" ".join(words)]
    best: list[str] = []
    for cuts in combinations(range(1, len(words)), k - 1):
        edges = (0, *cuts, len(words))
        lines = [" ".join(words[a:b]) for a, b in zip(edges, edges[1:])]
        if not best or max(map(_ems, lines)) < max(map(_ems, best)):
            best = lines
    return best


def caption_layout(text: str) -> tuple[list[str], int]:
    """(lines, font size) for one cue: the fewest lines that let it be big.

    "WHAT THE FUCK?" stays one line at 94px rather than being broken up for
    a few more pixels; "IT'S DEFINITELY COUNTING THE" becomes two lines at
    100px instead of one at 52. If no number of lines reaches
    CAPTION_SPLIT_BELOW, the biggest layout found wins — never one that is
    smaller just because it came last.
    """
    words = text.split()
    best: tuple[list[str], int] = ([], 0)
    for k in range(1, min(CAPTION_MAX_LINES, len(words)) + 1):
        lines = _split(words, k)
        size = min(CAPTION_MAX_PX, min(_caption_px(ln) for ln in lines))
        if size > best[1]:
            best = (lines, size)
        if size >= CAPTION_SPLIT_BELOW:
            break
    lines, size = best
    return lines, (max(CAPTION_FLOOR_PX, size) if lines else 0)


def caption_filters(font: str, text: str, start: float, end: float, n: int) -> str:
    """One caption cue as drawtext filters, one per line, ready to join into
    a filter chain. `n` is the cue's position in the video, which picks its
    colour. Shared by this graph and render.py so there is one caption look.

    Each line is its own drawtext centred on its own `text_w`, which is how a
    two-line cue gets both lines centred without drawtext's `text_align`
    (ffmpeg 6.1+; not something to assume about the box).
    """
    shown = text.strip()
    shown = shown.upper() if len(shown) < 40 else shown
    lines, size = caption_layout(shown)
    colour = CAPTION_COLOURS[n % len(CAPTION_COLOURS)]
    out = []
    for i, line in enumerate(lines):
        off = (i - (len(lines) - 1) / 2) * size * 1.2
        out.append(
            f"drawtext=fontfile={_esc(font)}:expansion=none:text={_esc(line)}:"
            f"fontcolor={colour}:fontsize={size}:"
            f"borderw={max(3, round(size * 0.1))}:bordercolor=black@0.95:"
            f"shadowcolor=black@0.5:shadowx=0:shadowy={max(2, round(size * 0.06))}:"
            f"x=(w-text_w)/2:y=h*{CAPTION_Y}+({off:.0f})-text_h/2:"
            f"enable='between(t,{start:.2f},{end:.2f})'")
    return ",".join(out)


def _offsets(plan: EditPlan) -> list[float]:
    """Where each join sits on the finished timeline.

    Join k happens once every shot before it has played, minus the transitions
    already spent: offset_k = sum(L0..Lk) - (k+1) * d. For one join that is
    simply L0 - d, which is the case worth holding in your head.

    This is exactly where each segment AFTER the first begins, so it is
    `plan.segment_starts` without its leading zero rather than a second
    implementation — captions convert source time through the same list, and
    two copies of this would be two chances to be one transition out.
    """
    return segment_starts(plan)[1:]


def _zoom_expr(zoom: str, length_s: float) -> str:
    """A zoompan `z` expression, in output frames (`on`).

    zoompan on video wants d=1 — one output frame per input frame — with the
    movement expressed against `on`. The alternative (d=frames) holds on a
    single frame and is how you accidentally render a slideshow.
    """
    frames = max(1, int(length_s * FPS))
    if zoom == "none":
        return "1"
    if zoom == "punch":
        # Lands hard, settles in PUNCH_SETTLE_S, then holds.
        per = (ZOOM_MAX - 1.0) / max(1, int(PUNCH_SETTLE_S * FPS))
        return f"max({ZOOM_MAX:.3f}-{per:.5f}*on,1.0)"
    if zoom == "pull":
        per = (ZOOM_MAX - 1.0) / frames
        return f"max({ZOOM_MAX:.3f}-{per:.5f}*on,1.0)"
    # drift: in, slowly, across the whole shot
    per = (ZOOM_MAX - 1.0) / frames
    return f"min(1.0+{per:.5f}*on,{ZOOM_MAX:.3f})"


# The camera panel, as a fraction of the frame. Same number as the browser's
# SPLIT_TOP, and imported from plan.py so it cannot drift from it.
TOP_H = int(H * SPLIT_TOP)          # 768
BOT_H = H - TOP_H                   # 1152
# How wide the camera sits in the `corner` layout, and its margin.
CORNER_W = int(W * 0.42)
CORNER_PAD = 28


def _cam_crop(cam: Facecam, pane_w: int, pane_h: int) -> str:
    """A crop that takes the camera window out of the source.

    EXPRESSED IN iw/ih, not in pixels, so the graph never has to know the
    file's resolution — no probe, and a 720p clip and a 1080p clip give the
    same picture.

    This is the browser's arithmetic (aurora_html.py, the `split` branch)
    written as ffmpeg expressions:

        rw = iw / zoom                      the window's width
        rh = rw * (pane_h / pane_w)         matched to the panel's shape
        if rh > ih: rh = ih, rw = ih / A    can't be taller than the source

    The conditional collapses into `min`, because rw is whichever of the two
    is smaller — that is the same branch, without a branch.
    """
    aspect = pane_h / float(pane_w)
    zoom = max(1.0, float(cam.zoom))
    rw = f"min(iw/{zoom:.4f},ih/{aspect:.6f})"
    # x/y clamp the window inside the frame, so an offset that would hang off
    # the edge slides back rather than producing a black margin.
    return (f"crop=w='{rw}':h='{rw}*{aspect:.6f}':"
            f"x='max(0,min(iw-ow,(0.5+{cam.off_x:.4f})*iw-ow/2))':"
            f"y='max(0,min(ih-oh,(0.5+{cam.off_y:.4f})*ih-oh/2))'")


def _stack(i: int, cam: Facecam) -> str:
    """Camera across the top, gameplay under it — the streamer-clip standard.

    Three copies of the source: a blurred backdrop so no panel is ever a hard
    black bar, the camera window filling the top exactly, and the WHOLE
    gameplay frame contained in the bottom. Contained, not cropped: the
    bottom panel is 1080x1152 and a 16:9 frame fits it at full width with
    room to spare, so cropping there would throw away sides for nothing.
    """
    return (
        f"[{i}:v]split=3[bg{i}][cam{i}][game{i}];"
        f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=24:6[bgb{i}];"
        f"[cam{i}]{_cam_crop(cam, W, TOP_H)},scale={W}:{TOP_H}[camf{i}];"
        f"[game{i}]scale={W}:{BOT_H}:force_original_aspect_ratio=decrease[gamef{i}];"
        f"[bgb{i}][camf{i}]overlay=0:0[st{i}];"
        f"[st{i}][gamef{i}]overlay=(W-w)/2:{TOP_H}+({BOT_H}-h)/2[fit{i}]"
    )


def _corner(i: int, cam: Facecam) -> str:
    """Gameplay fills the frame, the camera blown up over its top-left."""
    return (
        f"[{i}:v]split=2[bg{i}][cam{i}];"
        f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H}[bgf{i}];"
        f"[cam{i}]{_cam_crop(cam, CORNER_W, int(CORNER_W * 0.75))},"
        f"scale={CORNER_W}:-2[camf{i}];"
        f"[bgf{i}][camf{i}]overlay={CORNER_PAD}:{CORNER_PAD}[fit{i}]"
    )


def _fit(i: int, framing: str) -> str:
    """Source into a 1080x1920 frame, ending on a label this can zoom.

    `fill` crops to fill — the biggest picture, at the cost of everything
    outside a 9:16 slice of the middle. `blur` keeps the whole frame over a
    blurred, cropped blow-up of itself, which is what to use when the thing
    that matters is at the edge of a 16:9 shot.
    """
    if framing == "blur":
        return (
            f"[{i}:v]split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=24:6[bgb{i}];"
            f"[fg{i}]scale={W}:-2[fgs{i}];"
            f"[bgb{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2[fit{i}]"
        )
    return (f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}[fit{i}]")


def _compose(i: int, seg: Segment) -> str:
    """One segment's source into a full 1080x1920 frame, ending on [fit{i}].

    A camera layout wins over the plain framing, because `stack` already
    decides what happens to the whole frame — there is no "blur, stacked".
    `valid()` refuses a camera layout with no camera position, so by the
    time this runs `seg.facecam` is really there.
    """
    if seg.layout == "stack" and seg.facecam:
        return _stack(i, seg.facecam)
    if seg.layout == "corner" and seg.facecam:
        return _corner(i, seg.facecam)
    return _fit(i, seg.framing)


def _video_chain(i: int, seg: Segment) -> str:
    """One segment's picture: fit it to the frame, then move in it.

    A STATIC SHOT SKIPS zoompan ENTIRELY rather than running it with z='1'.
    Measured on production 2026-09-22: a single-segment 60s render was
    OOM-killed asking for 3.1 GB on an otherwise idle 3.8 GB box — ffmpeg
    alone, nothing else competing. zoompan is the filter in this chain with
    a reputation for exactly that, and running it to do nothing was pure
    cost. `zoom="none"` is now genuinely free.
    """
    # fps: xfade refuses two inputs at different frame rates, and the slides
    # push against a 30fps black frame — captures are often 60. zoompan
    # already sets its own fps below; a static shot has to be told.
    if seg.zoom == "none":
        return (f"{_compose(i, seg)};"
                f"[fit{i}]fps={FPS},setsar=1,format=yuv420p[v{i}]")
    z = _zoom_expr(seg.zoom, seg.length)
    return (
        f"{_compose(i, seg)};"
        f"[fit{i}]"
        f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={W}x{H}:fps={FPS},"
        f"setsar=1,format=yuv420p[v{i}]"
    )


def build_filtergraph(plan: EditPlan, *, font: str = "") -> tuple[str, str, str]:
    """(filter_complex, video_label, audio_label).

    Returned as labels rather than a finished command so the caller owns the
    inputs and the encoder flags, and so a test can read the graph without a
    file on disk anywhere.
    """
    n = len(plan.segments)
    parts = [_video_chain(i, s) for i, s in enumerate(plan.segments)]
    offs = _offsets(plan)
    d = plan.trans_dur

    # ── joins ────────────────────────────────────────────────────────────────
    vlab = "v0"
    for k in range(1, n):
        out = f"x{k}"
        parts.append(f"[{vlab}][v{k}]xfade=transition={plan.transition}:"
                     f"duration={d}:offset={offs[k - 1]}[{out}]")
        vlab = out

    alab = "0:a"
    for k in range(1, n):
        out = f"ax{k}"
        # c1/c2=tri: a linear crossfade. The default (qsin) dips in the middle,
        # which on speech sounds like a dropout rather than a cut.
        parts.append(f"[{alab}][{k}:a]acrossfade=d={d}:c1=tri:c2=tri[{out}]")
        alab = out

    # ── titles and captions, on the joined picture ──────────────────────────
    if font and (plan.title or plan.captions):
        chain = []
        if plan.title:
            chain.append(
                f"drawtext=fontfile={_esc(font)}:expansion=none:text={_esc(plan.title)}:"
                f"fontcolor=white:fontsize=76:box=1:boxcolor=black@0.45:boxborderw=18:"
                f"x=(w-text_w)/2:y=h*0.12-text_h/2")
        # NOT `n`: that name is the segment count, and the sound effects below
        # address their inputs as n + j. Reusing it here as the caption counter
        # sent them to input 34 on a six-input command (prod, 2026-09-23).
        drawn = 0
        for cue in plan.captions[:120]:
            try:
                start, end = float(cue["start"]), float(cue["end"])
                text = str(cue.get("text") or "").strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not text:
                continue
            chain.append(caption_filters(font, text, start, end, drawn))
            drawn += 1
        if chain:
            parts.append(f"[{vlab}]" + ",".join(chain) + "[vtxt]")
            vlab = "vtxt"

    # ── sound effects onto the finished timeline ────────────────────────────
    # Each is its own input after the segments; adelay places it, volume sets
    # it, amix with normalize=0 so adding a cue does not duck the speech.
    if plan.sfx:
        mix = [f"[{alab}]"]
        for j, cue in enumerate(plan.sfx):
            idx = n + j
            ms = max(0, int(cue.at * 1000))
            parts.append(f"[{idx}:a]adelay={ms}|{ms},volume={cue.gain:.2f}[s{j}]")
            mix.append(f"[s{j}]")
        parts.append("".join(mix) +
                     f"amix=inputs={len(plan.sfx) + 1}:normalize=0:dropout_transition=0[aout]")
        alab = "aout"

    # ── the open and the close ──────────────────────────────────────────────
    # A slide is an xfade `slideleft` (a push) against half a second of black:
    # in, the black leaves to the left as the video arrives from the right;
    # out, the video leaves to the left and black follows it. Applied AFTER
    # the captions, so the words ride in and out with the picture. xfade
    # overlaps its inputs, and the black is exactly one transition long, so
    # neither changes the video's length. Where a slide replaces it, the
    # fade at that end goes: fading while sliding just darkens the move.
    total = plan_duration(plan)
    black = f"color=c=black:s={W}x{H}:r={FPS}:d={d},format=yuv420p,setsar=1"
    if plan.slide_in:
        parts.append(f"{black}[slb0]")
        parts.append(f"[slb0][{vlab}]xfade=transition=slideleft:duration={d}:offset=0[slin]")
        vlab = "slin"
    if plan.slide_out:
        parts.append(f"{black}[slb1]")
        parts.append(f"[{vlab}][slb1]xfade=transition=slideleft:duration={d}:"
                     f"offset={max(0.0, total - d):.3f}[slout]")
        vlab = "slout"
    ends = []
    if not plan.slide_in:
        ends.append("fade=t=in:st=0:d=0.3")
    if not plan.slide_out:
        ends.append(f"fade=t=out:st={max(0.0, total - 0.4):.2f}:d=0.4")
    parts.append(f"[{vlab}]{','.join(ends) or 'null'}[vout]")
    return ";".join(parts), "vout", alab


def build_command(plan: EditPlan, dst: Path, sfx_paths: dict,
                  *, font: str = "") -> list[str]:
    """The whole ffmpeg argv for one plan.

    `sfx_paths` maps a kind ("whoosh") to the WAV on disk. A cue whose file is
    missing is dropped rather than failing the render: a post going out
    without one whoosh is better than no post.
    """
    plan = _drop_missing_sfx(plan, sfx_paths)
    args: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for seg in plan.segments:
        # -ss BEFORE -i is the fast seek. -t after it bounds the read.
        args += ["-ss", f"{seg.start:.3f}", "-t", f"{seg.length:.3f}", "-i", seg.src]
    for cue in plan.sfx:
        args += ["-i", str(sfx_paths[cue.kind])]

    graph, vlab, alab = build_filtergraph(plan, font=font)
    args += [
        "-filter_complex", graph,
        "-map", f"[{vlab}]", "-map", f"[{alab}]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        # The graph already ends where the plan says; -t is the belt and
        # braces against a filter running long and producing a 3-hour file.
        "-t", f"{plan_duration(plan):.3f}",
        str(dst),
    ]
    return args


def thumb_time(plan: EditPlan) -> float:
    """When to grab the cover frame.

    A plan that named a moment gets it. One that did not gets a point a
    little way into the opening shot — far enough in to be past the fade
    from black, which is the frame you do NOT want representing the video.
    """
    total = plan_duration(plan)
    if 0.0 <= plan.thumb_at <= total:
        return plan.thumb_at
    return round(min(1.5, total / 4.0), 3) if total > 0 else 0.0


def build_thumbnail_command(plan: EditPlan, video: Path, dst: Path,
                            *, font: str = "") -> list[str]:
    """One frame out of the FINISHED video, as the cover image.

    Taken from the render's own output rather than from a source clip, so
    the thumbnail shows the framing, the zoom and the burnt-in title that
    the viewer will actually see. Grabbing it from the source instead is how
    you end up with a cover that looks nothing like the video.

    `thumb_text` is drawn big when a font exists — a cover frame is read at
    the size of a phone tile, so it is larger than the video's own title and
    sits in the middle rather than the top.
    """
    at = thumb_time(plan)
    chain = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    if font and plan.thumb_text:
        chain.append(
            f"drawtext=fontfile={_esc(font)}:expansion=none:text={_esc(plan.thumb_text)}:"
            f"fontcolor=white:fontsize=96:box=1:boxcolor=black@0.5:boxborderw=24:"
            f"x=(w-text_w)/2:y=(h-text_h)/2")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            # -ss before -i seeks; one frame out, at high quality because a
            # cover image is re-encoded by every platform that receives it.
            "-ss", f"{at:.3f}", "-i", str(video),
            "-vf", ",".join(chain), "-frames:v", "1", "-q:v", "2",
            str(dst)]


def _drop_missing_sfx(plan: EditPlan, sfx_paths: dict) -> EditPlan:
    keep = [c for c in plan.sfx if c.kind in sfx_paths]
    if len(keep) == len(plan.sfx):
        return plan
    import dataclasses
    return dataclasses.replace(plan, sfx=keep)
