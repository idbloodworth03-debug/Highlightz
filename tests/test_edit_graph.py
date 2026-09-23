"""The filtergraph, asserted as a string because nothing here can run it.

ffmpeg is not installed in the dev container — only on production. A graph
with a dozen filters fails as one opaque error at the end of a render, so
these tests are the only feedback available before it reaches the box. They
check the things that are wrong SILENTLY rather than loudly:

  * an xfade offset out by one transition — the video plays, the cut is just
    in the wrong place, and it gets worse with every join;
  * audio joined without the matching overlap — sound drifts further out of
    step at each cut;
  * amix normalising — adding one whoosh quietly ducks all the speech;
  * zoompan with d != 1 — renders a slideshow, not a moving frame.
"""

import os
import re
from pathlib import Path

import pytest

from src.autopilot import graph as G
from src.autopilot import plan as P
from tests import ffparse as F


def seg(start=0.0, end=20.0, src="/tmp/a.mp4", zoom="punch"):
    return P.Segment(src=src, start=start, end=end, zoom=zoom)


def three():
    return P.EditPlan(segments=[seg(0, 22, "/tmp/a.mp4"), seg(0, 22, "/tmp/b.mp4"),
                                seg(0, 22, "/tmp/c.mp4")],
                      trans_dur=0.5, transition="slideleft")


SFX = {k: f"/opt/sfx/{k}.wav" for k in P.SFX_KINDS}


# ── the offsets, which are the thing most likely to be quietly wrong ────────

def test_one_join_happens_a_transition_before_the_first_shot_ends():
    p = P.EditPlan(segments=[seg(0, 20), seg(0, 20)], trans_dur=0.5)
    assert G._offsets(p) == [19.5]


def test_each_further_join_accounts_for_every_transition_already_spent():
    """offset_k = sum(L0..Lk) - (k+1) * d. Summing the raw lengths instead
    puts the second cut half a second late and the third a second late."""
    assert G._offsets(three()) == [21.5, 43.0]


def test_the_last_offset_lands_inside_the_finished_video():
    p = three()
    assert G._offsets(p)[-1] < P.plan_duration(p)


def test_a_single_segment_has_no_joins():
    assert G._offsets(P.EditPlan(segments=[seg()])) == []


# ── the video chain ─────────────────────────────────────────────────────────

def test_the_picture_fills_the_vertical_frame_rather_than_boxing_it():
    g, _, _ = G.build_filtergraph(three())
    assert "force_original_aspect_ratio=increase" in g
    assert f"crop={G.W}:{G.H}" in g


def test_zoompan_runs_one_output_frame_per_input_frame():
    """d=1 is what makes it a moving frame. With d=frames it holds on one
    frame for the whole shot — a slideshow with audio over it."""
    g, _, _ = G.build_filtergraph(three())
    assert g.count("zoompan=") == 3
    assert "d=1:" in g
    assert "d=660" not in g


def test_every_shot_moves():
    g, _, _ = G.build_filtergraph(three())
    assert "z='1'" not in g, "a shot is statically cropped"


def test_punch_settles_toward_one_and_drift_climbs_toward_the_ceiling():
    punch = G._zoom_expr("punch", 20.0)
    drift = G._zoom_expr("drift", 20.0)
    assert punch.startswith("max(1.200-") and punch.endswith(",1.0)")
    assert drift.startswith("min(1.0+") and drift.endswith(f",{G.ZOOM_MAX:.3f})")


def test_a_static_frame_is_still_expressible():
    assert G._zoom_expr("none", 20.0) == "1"


# ── audio ───────────────────────────────────────────────────────────────────

def test_audio_is_crossfaded_by_the_same_amount_the_video_overlaps():
    """xfade shortens the video by d at every join. Audio joined with concat
    would stay the full length and drift one transition further out of sync
    at each cut."""
    g, _, _ = G.build_filtergraph(three())
    assert g.count("acrossfade=d=0.5") == 2
    assert "concat=" not in g


def test_the_crossfade_is_linear_so_speech_does_not_dip():
    g, _, _ = G.build_filtergraph(three())
    assert "c1=tri:c2=tri" in g


def test_sound_effects_are_delayed_onto_the_timeline_and_mixed_without_ducking():
    """amix normalises by default, which divides every input by the number of
    inputs — so adding one whoosh would quietly halve the speech."""
    p = three()
    p.sfx = [P.Sfx(at=0.0, kind="riser"), P.Sfx(at=21.5, kind="whoosh")]
    g, _, alab = G.build_filtergraph(p)
    assert "adelay=0|0" in g
    assert "adelay=21500|21500" in g
    assert "normalize=0" in g
    assert "amix=inputs=3" in g, "the speech track is not in the mix"
    assert alab == "aout"


def test_with_no_sound_effects_the_audio_is_the_clips_own():
    p = three()
    p.sfx = []
    _, _, alab = G.build_filtergraph(p)
    assert alab == "ax2"


# ── the command ─────────────────────────────────────────────────────────────

def test_each_segment_is_trimmed_by_seeking_not_by_decoding():
    """-ss before -i seeks. After -i it decodes and throws away, which on a
    1 vCPU box turns a render into a timeout."""
    p = P.EditPlan(segments=[seg(12.0, 32.0, "/tmp/a.mp4")], trans_dur=0.5)
    args = G.build_command(p, "/tmp/out.mp4", SFX)
    i = args.index("-i")
    assert args[i - 4:i] == ["-ss", "12.000", "-t", "20.000"]


def test_the_sound_files_come_after_every_segment():
    """The graph addresses them by input index — `[3:a]` for the first cue of
    a three-segment plan — so their order in argv is load-bearing."""
    p = three()
    p.sfx = [P.Sfx(at=0.0, kind="riser")]
    args = G.build_command(p, "/tmp/out.mp4", SFX)
    inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    assert inputs[:3] == ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4"]
    assert inputs[3] == SFX["riser"]
    g = args[args.index("-filter_complex") + 1]
    assert "[3:a]adelay" in g


# ── slide in, slide out (owner, 2026-09-23) ─────────────────────────────────

def sliding():
    p = three()
    p.slide_in = p.slide_out = True
    return p


def test_the_video_slides_in_from_black_at_the_start():
    g = G.build_filtergraph(sliding())[0]
    assert f"color=c=black:s={G.W}x{G.H}:r={G.FPS}:d=0.5" in g
    assert "[slb0][vtxt]" not in g                     # no captions here
    assert "xfade=transition=slideleft:duration=0.5:offset=0[slin]" in g


def test_the_video_slides_out_to_black_at_the_end_without_changing_its_length():
    p = sliding()
    g = G.build_filtergraph(p)[0]
    total = P.plan_duration(p)
    assert f"[slin][slb1]xfade=transition=slideleft:duration=0.5:offset={total - 0.5:.3f}[slout]" in g
    args = G.build_command(p, "/tmp/o.mp4", SFX)
    assert args[args.index("-t", args.index("-filter_complex")) + 1] == f"{total:.3f}"


def test_a_slide_replaces_the_fade_at_its_end_and_only_there():
    assert "fade=t=in" not in G.build_filtergraph(sliding())[0]
    assert "fade=t=out" not in G.build_filtergraph(sliding())[0]
    p = three()
    p.slide_in = True
    g = G.build_filtergraph(p)[0]
    assert "fade=t=in" not in g and "fade=t=out" in g


def test_the_captions_ride_in_and_out_with_the_picture():
    """Drawn BEFORE the slides, so words are not left hanging over black."""
    p = sliding()
    p.captions = [{"start": 0.1, "end": 1.0, "text": "here we go"}]
    g = G.build_filtergraph(p, font="/f/x.ttf")[0]
    assert g.index("drawtext") < g.index("[slb0][vtxt]xfade")


def test_every_shot_runs_at_the_output_frame_rate():
    """xfade refuses inputs at different frame rates, and the slides push
    against a 30fps black frame while captures are often 60fps."""
    p = sliding()
    p.segments[1].zoom = "none"
    g = G.build_filtergraph(p)[0]
    for i, s in enumerate(p.segments):
        chain = g[g.index(f"[{i}:v]"):g.index(f"[v{i}]")]
        assert f"fps={G.FPS}" in chain, f"shot {i} ({s.zoom}) keeps its source rate"


def test_a_formula_plan_renders_to_a_whole_command():
    """End to end: slides, a cut, sound and captions together — every input
    opened is read, every label mapped is produced."""
    clips = [{"id": "a", "channel": "x"}, {"id": "b", "channel": "x"}]
    p = P.build(clips, {"a": ("/x/a.mp4", 36.22), "b": ("/x/b.mp4", 67.02)})
    p.captions = [{"start": i + 0.1, "end": i + 0.9, "text": f"cue {i}"} for i in range(20)]
    args = G.build_command(p, "/tmp/o.mp4", SFX, font="/f/x.ttf")
    g = args[args.index("-filter_complex") + 1]
    read = {int(k) for k in re.findall(r"\[(\d+):[av]\]", g)}
    assert read == set(range(args.count("-i")))
    assert g.count("xfade=") == 3, "a slide in, one cut, a slide out"
    assert [c.kind for c in p.sfx] == ["whoosh"] * 3


@pytest.mark.parametrize("n_captions", [0, 1, 34])
def test_every_input_the_graph_reads_is_one_the_command_opens(n_captions):
    """Regression, prod 2026-09-23: "Invalid file index 34 in filtergraph
    description". The caption loop reused the name `n` as its counter, so
    after 34 captions the sound effects were addressed as inputs 34+ in a
    command that opened six files. No test combined captions WITH sound
    effects — this one does, and checks every `[k:a]` / `[k:v]` against the
    real number of -i inputs rather than any one expected index."""
    p = three()
    p.sfx = [P.Sfx(at=0.0, kind="riser"), P.Sfx(at=21.5, kind="whoosh")]
    p.captions = [{"start": i + 0.1, "end": i + 0.9, "text": f"cue {i}"}
                  for i in range(n_captions)]
    args = G.build_command(p, "/tmp/out.mp4", SFX, font="/f/x.ttf")
    opened = args.count("-i")
    graph = args[args.index("-filter_complex") + 1]
    read = {int(k) for k in re.findall(r"\[(\d+):[av]\]", graph)}
    assert read and max(read) < opened, f"reads inputs {sorted(read)}, opens {opened}"
    assert read == set(range(opened)), "an input is opened but never used, or the reverse"


def test_a_missing_sound_file_drops_the_cue_rather_than_the_post():
    p = three()
    p.sfx = [P.Sfx(at=0.0, kind="riser"), P.Sfx(at=21.5, kind="whoosh")]
    args = G.build_command(p, "/tmp/out.mp4", {"whoosh": "/opt/sfx/whoosh.wav"})
    g = args[args.index("-filter_complex") + 1]
    assert g.count("adelay") == 1
    assert "amix=inputs=2" in g


def test_the_output_is_bounded_by_the_plans_own_duration():
    """Belt and braces: a filter running long must not produce a three-hour
    file on a 50 GB disk."""
    p = three()
    args = G.build_command(p, "/tmp/out.mp4", SFX)
    assert args[args.index("-t", args.index("-filter_complex")) + 1] == "65.000"


def test_it_encodes_something_the_platforms_accept():
    args = G.build_command(three(), "/tmp/out.mp4", SFX)
    for flag, val in (("-c:v", "libx264"), ("-pix_fmt", "yuv420p"),
                      ("-c:a", "aac"), ("-movflags", "+faststart")):
        assert args[args.index(flag) + 1] == val


def test_titles_and_captions_only_appear_when_a_font_exists():
    """drawtext without a real TTF is an ffmpeg error, and a missing font is
    not a reason to lose the post — render.py has always skipped text rather
    than failing."""
    p = three()
    p.title = "INSANE 1v5"
    p.captions = [{"start": 1.0, "end": 2.0, "text": "no way"}]
    assert "drawtext" not in G.build_filtergraph(p, font="")[0]
    g = G.build_filtergraph(p, font="/f/DejaVuSans-Bold.ttf")[0]
    assert g.count("drawtext") == 2
    assert "INSANE 1v5" in g


# ── escaping, checked against what ffmpeg will actually parse ───────────────
# Two live failures in two days, both on an apostrophe, both from escaping for
# one of ffmpeg's two parse passes. Both of those fixes had a test that pinned
# the exact string the fix produced — and both passed, because a test that
# pins an output only proves the code does what the author believed. These
# instead run the graph through tests/ffparse.py, a port of both parsers.

NASTY = [
    "I'm not sure", "it's 100%: [wild], really", "one, two; three",
    "'quoted'", "ends with '", "back\\slash", "a:b:c", "  padded  ",
    "What the fuck?", "50% off, don't [miss] it; ok: go",
]


def test_the_escaping_is_ffmpegs_own_worked_example():
    """ffmpeg-filters(1), "Notes on filtergraph escaping", character for
    character."""
    assert G._esc("this is a 'string': may contain one, or more, special characters") \
        == r"this is a \\\'string\\\'\\: may contain one\, or more\, special characters"


def test_the_parser_port_reads_the_docs_example_the_way_the_docs_say():
    """The port is only evidence if it agrees with ffmpeg on ffmpeg's own
    example."""
    g = r"drawtext=text=this is a \\\'string\\\'\\: may contain one\, or more\, special characters"
    assert F.drawtexts(g) == [{"text": "this is a 'string': may contain one, or more, special characters"}]


def test_the_parser_port_catches_the_2026_09_23_failure():
    """The exact drawtext prod choked on. The port reproduces what went
    wrong: the apostrophe opened a quote in the second pass and every
    option after it became caption text."""
    prod = ("drawtext=fontfile=/f.ttf:text='I'\\''M GONNA':fontcolor=0xF7A745:"
            "fontsize=100:enable='between(t,53.69,54.23)'")
    [d] = F.drawtexts(prod)
    assert sorted(d) == ["fontfile", "text"]
    assert "fontcolor=" in d["text"]


@pytest.mark.parametrize("text", NASTY)
def test_every_caption_reaches_ffmpeg_as_itself_with_all_its_options(text):
    p = three()
    p.captions = [{"start": 1.0, "end": 2.0, "text": text},
                  {"start": 3.0, "end": 4.0, "text": "next line"}]
    parsed = F.drawtexts(G.build_filtergraph(p, font="/f/x.ttf")[0])
    shown = text.strip().upper()
    lines, _ = G.caption_layout(shown)
    assert [d["text"] for d in parsed] == lines + ["NEXT LINE"], \
        "a caption was garbled, or swallowed the one after it"
    for d in parsed:
        assert d["expansion"] == "none", "a % in speech would be read as a directive"
        assert {"fontcolor", "fontsize", "borderw", "x", "y", "enable"} <= set(d)
    assert parsed[0]["enable"] == "between(t,1.00,2.00)"


@pytest.mark.parametrize("text", NASTY)
def test_titles_and_covers_reach_ffmpeg_as_themselves(text):
    p = three()
    p.title = text
    p.thumb_text = text
    [title] = F.drawtexts(G.build_filtergraph(p, font="/f/x.ttf")[0])
    assert title["text"] == text.strip()
    cmd = G.build_thumbnail_command(p, Path("/tmp/v.mp4"), Path("/tmp/c.jpg"), font="/f/x.ttf")
    [cover] = F.drawtexts(cmd[cmd.index("-vf") + 1])
    assert cover["text"] == text.strip() and cover["expansion"] == "none"


# ── the caption look ────────────────────────────────────────────────────────
# Owner, 2026-09-23: the boxed 54px captions were "bland black and white".

# Lines from the real prod transcript of 2026-09-22 (a jynxzi clip), plus the
# long sentence-level cue Whisper falls back to when it has no word timings.
REAL_LINES = [
    "Yeah, cuz bro, this", "is so wrong.", "Yeah, maybe this is",
    "It's definitely counting the", "last game for sure.", "You know, I think",
    "Highlight said this is", "fair fair fair fair.", "just keep waiting till",
    "we have leaderboard?", "What do you mean", "she was hard bro?",
    "What the fuck?", "What stream are you", "It's right just being", "sure",
    "WOW WOW WOW WOW", "MMMM WWWW",
    "Okay so what we are going to do now is wait for the leaderboard to update before we queue",
]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def test_captions_are_outlined_and_big_not_a_small_black_plate():
    p = three()
    p.captions = [{"start": 1.0, "end": 2.0, "text": "no way"}]
    [d] = F.drawtexts(G.build_filtergraph(p, font="/f/x.ttf")[0])
    assert d["text"] == "NO WAY"
    assert "box" not in d, "the black plate is back"
    assert "borderw" in d and "shadowcolor" in d
    assert int(d["fontsize"]) >= 72


def test_every_other_caption_is_the_accent_colour():
    p = three()
    p.captions = [{"start": i, "end": i + 0.9, "text": f"cue {i}"} for i in range(1, 5)]
    g = G.build_filtergraph(p, font="/f/x.ttf")[0]
    assert [d["fontcolor"] for d in F.drawtexts(g)] == \
        ["white", "0xF7A745", "white", "0xF7A745"]


def test_the_accent_is_the_browser_editors_own():
    """One product, one amber: the server's must be CAP_ACCENT from the
    editor, or a clip captioned in one place and posted from the other
    looks like two different apps made it."""
    src = Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
    hexcode = re.search(r"const CAP_ACCENT = '#([0-9A-Fa-f]{6})'", src.read_text()).group(1)
    assert G.CAPTION_COLOURS[1] == "0x" + hexcode.upper()


def test_a_long_cue_wraps_onto_two_centred_lines_instead_of_shrinking():
    """On one line this is 52px. Wrapped it is two lines at 100."""
    lines, size = G.caption_layout("IT'S DEFINITELY COUNTING THE")
    assert lines == ["IT'S DEFINITELY", "COUNTING THE"]
    assert size >= G.CAPTION_SPLIT_BELOW
    f = G.caption_filters("/f/x.ttf", "It's definitely counting the", 1.0, 2.0, 0)
    assert f.count("drawtext=") == 2
    assert f.count("enable='between(t,1.00,2.00)'") == 2, "the lines are on different clocks"
    assert f.count("x=(w-text_w)/2") == 2, "each line must centre on its own width"
    offs = [int(x) for x in re.findall(r"y=h\*0\.78\+\((-?\d+)\)", f)]
    assert offs[0] == -offs[1] != 0, "the two lines are not centred on the caption line"


def test_when_nothing_reaches_the_threshold_the_biggest_layout_wins():
    """Regression: the first version tried a third line on a two-word cue,
    got the one-line layout back (there is no third word to wrap), and
    returned THAT — throwing away the two-line layout that was bigger."""
    lines, size = G.caption_layout("SUPERCALIFRAGILISTIC EXPIALIDOCIOUSNESS")
    assert lines == ["SUPERCALIFRAGILISTIC", "EXPIALIDOCIOUSNESS"]
    assert size > G._caption_px("SUPERCALIFRAGILISTIC EXPIALIDOCIOUSNESS")


def test_a_short_punchy_cue_stays_on_one_line():
    assert G.caption_layout("WHAT THE FUCK?")[0] == ["WHAT THE FUCK?"]
    assert G.caption_layout("SURE") == (["SURE"], G.CAPTION_MAX_PX)


def test_every_real_line_stays_inside_the_frame_at_the_size_it_gets():
    """drawtext does not wrap and does not complain — a line too wide just
    runs off both edges. CAPTION_ADVANCE is an estimate (Pillow is test-only,
    prod may not have it), so this measures the real thing: every real line,
    at the size and wrap `caption_layout` picks, in the font prod uses,
    outline included."""
    ImageFont = pytest.importorskip("PIL.ImageFont")
    if not os.path.exists(FONT):
        pytest.skip("DejaVu Sans Bold is not installed here")
    for raw in REAL_LINES:
        shown = raw.upper() if len(raw) < 40 else raw
        lines, size = G.caption_layout(shown)
        face = ImageFont.truetype(FONT, size)
        for ln in lines:
            width = face.getlength(ln) + 2 * max(3, round(size * 0.1))
            assert width <= G.W, f"{ln!r} at {size}px is {width:.0f}px on a {G.W}px frame"


def test_the_live_renderer_burns_in_the_same_captions():
    """render.py is what real Autopilot posts still go through. It draws its
    captions with the same helper, so there is one caption look, not two."""
    from src.autopilot import render as R
    vf = R.video_filter("full", captions=[(0, 1.5, "no way")], font="/f.ttf")
    assert G.caption_filters("/f.ttf", "no way", 0, 1.5, 0) in vf
    assert "box=1" not in vf


def test_a_caption_missing_its_timing_is_skipped_not_crashed():
    p = three()
    p.captions = [{"text": "no times"}, {"start": 1, "end": 2, "text": "fine"}]
    g = G.build_filtergraph(p, font="/f/x.ttf")[0]
    assert g.count("drawtext") == 1


def test_the_video_fades_in_and_out_within_its_own_length():
    p = three()
    g, vlab, _ = G.build_filtergraph(p)
    assert vlab == "vout"
    assert "fade=t=in:st=0:d=0.3" in g
    assert "fade=t=out:st=64.60" in g


# ── the whole thing hangs together ──────────────────────────────────────────

def test_every_label_the_graph_maps_is_one_it_defined():
    """The failure this catches is a typo'd label, which ffmpeg reports as
    "Output with label 'x' does not exist" after parsing everything else."""
    import re
    p = three()
    p.sfx = [P.Sfx(at=0.0, kind="riser"), P.Sfx(at=21.5, kind="hit")]
    p.title = "Hello"
    g, vlab, alab = G.build_filtergraph(p, font="/f/x.ttf")
    produced = set(re.findall(r"\[([a-z0-9]+)\](?=;|$)", g))
    for chunk in g.split(";"):
        produced.update(re.findall(r"\[([a-z][a-z0-9]*)\]\s*$", chunk))
    assert vlab in produced, f"{vlab} is mapped but never produced"
    assert alab in produced, f"{alab} is mapped but never produced"


def test_a_plan_the_builder_produced_renders_to_a_command():
    """End to end through the two modules, which is how it will be called."""
    clips = [{"id": f"c{i}", "suggested": i == 0, "virality_score": 50 - i,
              "channel": "novafps"} for i in range(3)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 24.0) for i in range(3)}
    p = P.build(clips, sources, title="Hook")
    ok, why = P.valid(p)
    assert ok, why
    args = G.build_command(p, "/tmp/out.mp4", SFX, font="/f/x.ttf")
    assert args[0] == "ffmpeg" and args[-1] == "/tmp/out.mp4"
    assert P.plan_duration(p) == pytest.approx(60.0, abs=1.0)
