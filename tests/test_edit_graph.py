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

import pytest

from src.autopilot import graph as G
from src.autopilot import plan as P


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


def test_text_that_would_break_the_graph_is_escaped():
    p = three()
    p.title = "it's 100%: [wild], really"
    g = G.build_filtergraph(p, font="/f/x.ttf")[0]
    assert "it\\'s 100\\%\\: \\[wild\\]\\, really" in g


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
