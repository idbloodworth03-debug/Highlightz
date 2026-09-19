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

from pathlib import Path

from src.autopilot.plan import EditPlan, Segment, plan_duration

FPS = 30
W, H = 1080, 1920

# How far each move travels. Small on purpose: a 1.2x punch reads as a camera
# move, a 1.6x punch reads as a mistake and softens the picture.
ZOOM_MAX = 1.20
PUNCH_SETTLE_S = 0.8


def _esc(text: str) -> str:
    """Escape for a drawtext `text=` value. Same order as render.py: the
    backslash first, then the characters that end a value."""
    out = text.replace("\\", "\\\\")
    for ch in ("'", ":", ",", ";", "[", "]", "%"):
        out = out.replace(ch, "\\" + ch)
    return out


def _offsets(plan: EditPlan) -> list[float]:
    """Where each join sits on the finished timeline.

    Join k happens once every shot before it has played, minus the transitions
    already spent: offset_k = sum(L0..Lk) - (k+1) * d. For one join that is
    simply L0 - d, which is the case worth holding in your head.
    """
    out: list[float] = []
    running = 0.0
    for k, seg in enumerate(plan.segments[:-1]):
        running += seg.length
        out.append(round(running - (k + 1) * plan.trans_dur, 3))
    return out


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


def _video_chain(i: int, seg: Segment) -> str:
    """One segment's picture: fill the vertical frame, then move in it."""
    z = _zoom_expr(seg.zoom, seg.length)
    return (
        f"[{i}:v]"
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
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
                f"drawtext=fontfile={font}:text='{_esc(plan.title)}':"
                f"fontcolor=white:fontsize=76:box=1:boxcolor=black@0.45:boxborderw=18:"
                f"x=(w-text_w)/2:y=h*0.12-text_h/2")
        for cue in plan.captions[:120]:
            try:
                start, end = float(cue["start"]), float(cue["end"])
                text = _esc(str(cue.get("text") or ""))
            except (KeyError, TypeError, ValueError):
                continue
            if not text:
                continue
            chain.append(
                f"drawtext=fontfile={font}:text='{text}':"
                f"fontcolor=white:fontsize=54:box=1:boxcolor=black@0.5:boxborderw=14:"
                f"x=(w-text_w)/2:y=h*0.78:"
                f"enable='between(t,{start:.2f},{end:.2f})'")
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

    # A short fade at each end so the post does not start or stop on a jolt.
    total = plan_duration(plan)
    parts.append(f"[{vlab}]fade=t=in:st=0:d=0.3,"
                 f"fade=t=out:st={max(0.0, total - 0.4):.2f}:d=0.4[vout]")
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


def _drop_missing_sfx(plan: EditPlan, sfx_paths: dict) -> EditPlan:
    keep = [c for c in plan.sfx if c.kind in sfx_paths]
    if len(keep) == len(plan.sfx):
        return plan
    import dataclasses
    return dataclasses.replace(plan, sfx=keep)
