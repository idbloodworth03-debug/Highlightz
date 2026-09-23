"""Framing, burnt-in captions and the cover frame.

Owner (2026-09-21), on what the model is for: "pick out the right templates
for each video, provide auto captions, transitions, sound effects, it also
needs a thumbnail and a caption for the video."

Transitions, sound effects and the post caption were already built. These
are the three that were not, and the one that can go quietly wrong is the
caption TIMING. The renderer shows a caption with `enable=between(t,...)`,
where `t` is the finished video's clock — but the transcript the model reads
is in the source clip's clock, and the two differ by one transition per join
because xfade overlaps. A cue left unconverted is right on the first shot
and progressively wronger after it: words over the wrong moment, and no
error anywhere. Most of this file is about that.
"""

import pytest

from src.autopilot import graph as G
from src.autopilot import llm_common as C
from src.autopilot import plan as P


def seg(start=0.0, end=22.0, src="/tmp/a.mp4", clip_id="c0", **kw):
    return P.Segment(src=src, start=start, end=end, clip_id=clip_id, **kw)


def three():
    return P.EditPlan(segments=[seg(0, 22, "/tmp/a.mp4", "c0"),
                                seg(0, 22, "/tmp/b.mp4", "c1"),
                                seg(0, 22, "/tmp/c.mp4", "c2")],
                      trans_dur=0.5, transition="slideleft")


# ── the timeline arithmetic, once, for everything that uses it ──────────────

def test_each_segment_starts_one_transition_earlier_than_its_raw_sum():
    """[0, 21.5, 43.0] for three 22s shots at 0.5s, not [0, 22, 44]."""
    assert P.segment_starts(three()) == [0.0, 21.5, 43.0]


def test_the_joins_are_the_segment_starts_without_the_first():
    """graph._offsets and plan.segment_starts must not be able to disagree —
    they are the same numbers, and captions are placed with them too."""
    p = three()
    assert G._offsets(p) == P.segment_starts(p)[1:]


def test_the_last_segment_ends_exactly_at_the_plans_duration():
    p = three()
    last = P.segment_starts(p)[-1] + p.segments[-1].length
    assert last == pytest.approx(P.plan_duration(p))


def test_a_moment_in_the_first_shot_is_itself():
    p = three()
    assert P.timeline_time(p, 0, 5.0) == 5.0


def test_a_moment_in_a_later_shot_is_shifted_by_every_transition_before_it():
    """THE WHOLE POINT. 5s into the third shot is 48s into the video, not
    49s — two joins have each eaten half a second."""
    p = three()
    assert P.timeline_time(p, 2, 5.0) == 48.0


def test_a_window_that_does_not_start_at_zero_is_measured_from_its_in_point():
    """The source clock is the ORIGINAL file's, so a segment cut from 10s to
    30s shows its own 12s two seconds in."""
    p = P.EditPlan(segments=[seg(10, 30)], trans_dur=0.5)
    assert P.timeline_time(p, 0, 12.0) == 2.0


def test_a_moment_that_was_trimmed_out_has_nowhere_to_go():
    p = P.EditPlan(segments=[seg(10, 30)], trans_dur=0.5)
    assert P.timeline_time(p, 0, 4.0) == -1.0
    assert P.timeline_time(p, 0, 44.0) == -1.0


def test_an_impossible_segment_index_does_not_raise():
    assert P.timeline_time(three(), 9, 1.0) == -1.0


# ── framing: the "template" decision ────────────────────────────────────────

def test_fill_crops_and_blur_keeps_the_whole_frame():
    fill = G.build_filtergraph(P.EditPlan(segments=[seg(framing="fill")]))[0]
    blur = G.build_filtergraph(P.EditPlan(segments=[seg(framing="blur")]))[0]
    assert "boxblur" not in fill
    assert "boxblur=24:6" in blur and "overlay=(W-w)/2:(H-h)/2" in blur


def test_a_blurred_shot_still_moves_and_still_joins():
    """The blur chain ends on the same label the zoom and the xfade expect,
    so mixing framings in one plan must not break the graph."""
    p = three()
    p.segments[1].framing = "blur"
    g, vlab, _ = G.build_filtergraph(p)
    assert g.count("zoompan=") == 3
    assert g.count("xfade") == 2
    assert vlab == "vout"


def test_each_blurred_segment_uses_its_own_labels():
    """Two blurred shots sharing a label name is a graph that fails to parse
    — and it only happens on the second one."""
    p = three()
    for s in p.segments:
        s.framing = "blur"
    g = G.build_filtergraph(p)[0]
    for i in range(3):
        assert f"[bgb{i}]" in g and f"[fgs{i}]" in g


def test_an_unknown_framing_is_rejected_by_the_plan():
    p = P.EditPlan(segments=[seg(framing="vertical")])
    ok, why = P.valid(p)
    assert not ok and "framing" in why


def test_the_formula_letterboxes_rather_than_cropping():
    """Owner, 2026-09-22, after watching the first real render: "I only see
    half of the clip… I would rather just have it the entire clip with the
    blurr on the top and the bottom."

    Cropping a 16:9 stream to fill a 9:16 frame keeps a tall slice of the
    middle and loses the camera and most of the HUD with it. Blur keeps the
    whole frame at the cost of a smaller picture, and that is the trade the
    owner picked having seen both.
    """
    clips = [{"id": f"c{i}", "suggested": i == 0, "channel": "novafps"}
             for i in range(3)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 24.0) for i in range(3)}
    p = P.build(clips, sources)
    assert P.valid(p)[0]
    assert all(s.framing == "blur" for s in p.segments)
    g = G.build_filtergraph(p)[0]
    assert "boxblur" in g, "the blurred backdrop is not in the graph"


def test_fill_is_still_reachable_for_a_plan_that_wants_it():
    """The model can still choose it per shot, and the preview has --fill."""
    p = P.EditPlan(segments=[seg(framing="fill")])
    assert "boxblur" not in G.build_filtergraph(p)[0]


# ── captions, converted from source time ────────────────────────────────────

def coerced(captions, segments=None):
    clips = [{"id": f"c{i}", "channel": "novafps"} for i in range(3)]
    sources = {f"c{i}": (f"/tmp/{i}.mp4", 40.0) for i in range(3)}
    data = {
        "segments": segments or [
            {"clip_id": "c0", "start": 0.0, "end": 22.0, "zoom": "punch",
             "framing": "fill", "why": ""},
            {"clip_id": "c1", "start": 0.0, "end": 22.0, "zoom": "drift",
             "framing": "fill", "why": ""}],
        "transition": "slideleft", "sfx": [], "captions": captions,
        "thumbnail": {"clip_id": "c0", "at": 3.0, "text": "NO WAY"},
        "title": "t", "caption": "c", "hashtags": [],
    }
    return C.coerce(data, sources, clips, target_s=60.0)


def test_a_caption_on_the_first_shot_keeps_its_time():
    plan, _ = coerced([{"clip_id": "c0", "start": 4.0, "end": 6.0, "text": "oh"}])
    assert plan.captions == [{"start": 4.0, "end": 6.0, "text": "oh"}]


def test_a_caption_on_a_later_shot_is_moved_onto_the_finished_clock():
    """4s into the second clip is 25.5s into the video, because the join
    overlapped half a second. Left at 4.0 it would appear during the FIRST
    shot instead — the failure with no error message."""
    plan, _ = coerced([{"clip_id": "c1", "start": 4.0, "end": 6.0, "text": "no"}])
    assert plan.captions == [{"start": 25.5, "end": 27.5, "text": "no"}]


def test_every_caption_lands_inside_the_finished_video():
    plan, _ = coerced([
        {"clip_id": "c0", "start": 1.0, "end": 3.0, "text": "a"},
        {"clip_id": "c1", "start": 20.0, "end": 21.5, "text": "b"}])
    total = P.plan_duration(plan)
    assert plan.captions and all(0 <= c["start"] < c["end"] <= total
                                 for c in plan.captions)


def test_a_caption_for_a_line_that_was_trimmed_out_is_dropped():
    plan, notes = coerced([{"clip_id": "c0", "start": 35.0, "end": 37.0,
                            "text": "cut out"}])
    assert plan.captions == []
    assert any("outside every kept window" in n for n in notes)


def test_a_caption_running_past_the_cut_is_clamped_not_dropped():
    """The line IS on screen; only its tail was trimmed. Dropping it whole
    loses a caption the viewer needed."""
    plan, _ = coerced([{"clip_id": "c0", "start": 21.0, "end": 26.0, "text": "hi"}])
    assert len(plan.captions) == 1
    assert plan.captions[0]["end"] == 22.0


def test_a_caption_for_a_clip_that_is_not_in_the_cut_is_dropped():
    plan, _ = coerced([{"clip_id": "c2", "start": 1.0, "end": 2.0, "text": "no"}])
    assert plan.captions == []


def test_captions_follow_the_right_copy_of_a_repeated_clip():
    """A plan that opens and closes on the same clip is normal. Matching on
    the id alone would put both cues on the first appearance."""
    plan, _ = coerced(
        [{"clip_id": "c0", "start": 26.0, "end": 28.0, "text": "later"}],
        segments=[{"clip_id": "c0", "start": 0.0, "end": 22.0, "zoom": "punch",
                   "framing": "fill", "why": ""},
                  {"clip_id": "c0", "start": 25.0, "end": 40.0, "zoom": "drift",
                   "framing": "fill", "why": ""}])
    # Second segment starts at 21.5; 26.0 is 1s into its 25.0 in-point.
    assert plan.captions[0]["start"] == 22.5


def test_a_caption_with_no_timing_is_skipped_not_crashed():
    plan, notes = coerced([{"clip_id": "c0", "text": "no times"},
                           {"clip_id": "c0", "start": 1, "end": 2, "text": "ok"}])
    assert [c["text"] for c in plan.captions] == ["ok"]
    assert any("no usable timing" in n for n in notes)


def test_an_empty_caption_is_not_drawn():
    plan, _ = coerced([{"clip_id": "c0", "start": 1, "end": 2, "text": "   "}])
    assert plan.captions == []


def test_no_transcript_means_no_captions_rather_than_invented_ones():
    plan, _ = coerced([])
    assert plan.captions == []
    assert P.valid(plan)[0]


def test_the_converted_captions_are_what_the_renderer_draws():
    """End to end: the shape coerce produces is the shape the graph reads."""
    plan, _ = coerced([{"clip_id": "c1", "start": 4.0, "end": 6.0, "text": "no way"}])
    g = G.build_filtergraph(plan, font="/f/x.ttf")[0]
    assert "NO WAY" in g
    assert "between(t,25.50,27.50)" in g


# ── captions the FORMULA places, from real Whisper transcripts ──────────────
#
# Owner, 2026-09-22, having watched a render with no captions: "can we add
# captions to this as well". A model call is not required for a burnt-in
# caption — Whisper already produced its own timed cues in `transcribe.py`.
# `captions_for_plan` is the deterministic counterpart to `coerce`'s caption
# handling above: same `_place_captions`, same clock, no model in the loop.

def test_formula_captions_use_the_same_timeline_conversion_as_the_llms():
    plan = three()
    transcripts = {"c1": [{"start": 4.0, "end": 6.0, "text": "no"}]}
    out = C.captions_for_plan(plan, transcripts)
    assert out == [{"start": 25.5, "end": 27.5, "text": "no"}]


def test_formula_captions_from_several_clips_land_in_timeline_order():
    plan = three()
    transcripts = {
        "c2": [{"start": 2.0, "end": 3.0, "text": "third"}],
        "c0": [{"start": 1.0, "end": 2.0, "text": "first"}],
    }
    out = C.captions_for_plan(plan, transcripts)
    assert [c["text"] for c in out] == ["first", "third"]
    assert out[0]["start"] < out[1]["start"]


def test_formula_captions_drop_a_line_outside_every_kept_window():
    plan = three()
    transcripts = {"c0": [{"start": 35.0, "end": 37.0, "text": "cut out"}]}
    assert C.captions_for_plan(plan, transcripts) == []


def test_formula_captions_skip_an_unknown_clip_id():
    plan = three()
    transcripts = {"not-in-the-cut": [{"start": 1.0, "end": 2.0, "text": "no"}]}
    assert C.captions_for_plan(plan, transcripts) == []


def test_no_transcripts_means_no_formula_captions_either():
    assert C.captions_for_plan(three(), {}) == []


def test_formula_captions_feed_the_renderer_the_same_way_the_llms_do():
    plan = three()
    plan.captions = C.captions_for_plan(plan, {"c0": [{"start": 1.0, "end": 2.0,
                                                        "text": "hi there"}]})
    g = G.build_filtergraph(plan, font="/f/x.ttf")[0]
    assert "HI THERE" in g


# ── the cover frame ─────────────────────────────────────────────────────────

def test_the_thumbnail_moment_is_converted_like_a_caption():
    plan, _ = coerced([], segments=[
        {"clip_id": "c1", "start": 0.0, "end": 22.0, "zoom": "punch",
         "framing": "fill", "why": ""}])
    # Only one segment now, so c0's thumbnail cannot be placed.
    assert plan.thumb_at == -1.0


def test_a_thumbnail_inside_the_cut_is_placed():
    plan, _ = coerced([])
    assert plan.thumb_at == 3.0
    assert plan.thumb_text == "NO WAY"


def test_a_thumbnail_nobody_chose_still_gets_a_frame_past_the_fade():
    """Frame zero is black — the fade-in starts there. A cover that is a
    black rectangle is worse than no cover."""
    p = three()
    assert p.thumb_at == -1.0
    assert G.thumb_time(p) > 0.0


def test_a_chosen_thumbnail_time_is_used_as_given():
    p = three()
    p.thumb_at = 30.0
    assert G.thumb_time(p) == 30.0


def test_a_thumbnail_past_the_end_is_refused_by_the_plan():
    p = three()
    p.thumb_at = 900.0
    ok, why = P.valid(p)
    assert not ok and "thumbnail" in why


def test_the_thumbnail_is_taken_from_the_finished_video():
    """From the render's own output, so the cover shows the framing, the
    zoom and the burnt-in title the viewer will actually see."""
    p = three()
    p.thumb_at = 12.0
    args = G.build_thumbnail_command(p, "/tmp/out.mp4", "/tmp/cover.jpg")
    i = args.index("-i")
    assert args[i - 2:i] == ["-ss", "12.000"]
    assert args[i + 1] == "/tmp/out.mp4"
    assert args[args.index("-frames:v") + 1] == "1"
    assert args[-1] == "/tmp/cover.jpg"


def test_the_cover_is_the_same_shape_as_the_video():
    args = G.build_thumbnail_command(three(), "/tmp/out.mp4", "/tmp/c.jpg")
    vf = args[args.index("-vf") + 1]
    assert f"crop={G.W}:{G.H}" in vf


def cover_vf(plan, font=""):
    """The -vf value of a thumbnail command, which is where its text lives."""
    args = G.build_thumbnail_command(plan, "/tmp/o.mp4", "/tmp/c.jpg", font=font)
    return args[args.index("-vf") + 1]


def test_cover_text_is_only_drawn_when_a_font_exists():
    """drawtext without a real TTF is an ffmpeg error, and a missing font is
    not a reason to lose the cover."""
    p = three()
    p.thumb_text = "INSANE"
    assert "drawtext" not in cover_vf(p)
    assert "INSANE" in cover_vf(p, font="/f/x.ttf")


def test_cover_text_is_escaped_like_every_other_drawtext():
    p = three()
    p.thumb_text = "it's 100%"
    assert "it'\\''s 100%" in cover_vf(p, font="/f/x.ttf")


def test_cover_text_is_bigger_than_the_videos_own_title():
    """It is read at the size of a phone tile, not full screen."""
    p = three()
    p.thumb_text, p.title = "BIG", "the title"
    assert "fontsize=96" in cover_vf(p, font="/f/x.ttf")
    assert "fontsize=76" in G.build_filtergraph(p, font="/f/x.ttf")[0]
