"""The camera, and the clipper/streamer split.

Owner (2026-09-21): "I need it to start with it framing it correctly. Putting
the face cam in the right spot… I need it to be a minute long for clippers.
Streamers it does not really matter for."

THE ONE THAT CAN EMBARRASS SOMEBODY is a camera layout on a channel whose
camera position nobody ever recorded. The crop would land wherever the
default offset points — a patch of gameplay — and the finished video would
present it to viewers as the streamer's face, on every clip from that
channel, with no error anywhere. `valid()` refuses it and `coerce()` drops
the layout rather than the plan; both are asserted here.

The geometry is asserted against the browser editor's, because a user drags
the camera window in `aurora_html.py` and the server has to reproduce what
they saw. Same three numbers, same meanings, same SPLIT_TOP.
"""

import re

import pytest

from src.autopilot import graph as G
from src.autopilot import llm_common as C
from src.autopilot import plan as P


CAM = P.Facecam(off_x=-0.34, off_y=0.30, zoom=2.4)


def seg(clip_id="c0", **kw):
    return P.Segment(src="/tmp/a.mp4", start=0.0, end=22.0, clip_id=clip_id, **kw)


# ── the rule that protects a stranger's face ────────────────────────────────

def test_a_camera_layout_without_a_camera_position_is_refused():
    for layout in ("stack", "corner"):
        ok, why = P.valid(P.EditPlan(segments=[seg(layout=layout)]))
        assert not ok, f"{layout} was allowed with no facecam"
        assert "facecam" in why


def test_the_same_layout_with_a_position_is_fine():
    for layout in ("stack", "corner"):
        p = P.EditPlan(segments=[seg(layout=layout, facecam=CAM)])
        assert P.valid(p)[0]


def test_no_camera_means_no_camera_treatment_in_the_graph():
    g = G.build_filtergraph(P.EditPlan(segments=[seg(layout="none")]))[0]
    assert "vstack" not in g and "overlay" not in g


def test_the_model_asking_for_a_camera_where_there_is_none_loses_the_layout_not_the_plan():
    """The cut is still good — it just gets framed instead of stacked."""
    plan, notes = coerced(layout="stack", facecams={})
    assert plan.segments[0].layout == "none"
    assert plan.segments[0].facecam is None
    assert P.valid(plan)[0]
    assert any("has no camera" in n for n in notes)


def test_the_model_is_told_which_channels_have_a_camera():
    clips = [{"id": "c0", "channel": "novafps"}, {"id": "c1", "channel": "other"}]
    sources = {"c0": ("/t/0.mp4", 40.0), "c1": ("/t/1.mp4", 40.0)}
    b = C.brief(clips, sources, None, {"novafps": CAM})
    flags = {c["clip_id"]: c["has_camera"] for c in b["candidates"]}
    assert flags == {"c0": True, "c1": False}


def test_the_cameras_position_is_never_sent_to_the_model():
    """It is a fact about the channel, not a judgement. The model only needs
    to know whether the option exists."""
    b = C.brief([{"id": "c0", "channel": "novafps"}],
                {"c0": ("/t/0.mp4", 40.0)}, None, {"novafps": CAM})
    import json
    blob = json.dumps(b)
    assert "off_x" not in blob and "-0.34" not in blob


# ── the geometry matches the browser the user dragged it in ─────────────────

def test_the_camera_panel_is_the_same_fraction_as_the_browsers():
    """aurora_html.py's SPLIT_TOP is 0.4. A server that used 0.33 would
    render something the user did not preview."""
    assert P.SPLIT_TOP == 0.4
    assert G.TOP_H == int(1920 * 0.4)
    assert G.TOP_H + G.BOT_H == G.H


def test_the_crop_is_expressed_against_the_source_not_in_pixels():
    """No probe: a 720p clip and a 1080p clip have to give the same picture,
    and graph.py never opens a file."""
    g = G.build_filtergraph(P.EditPlan(segments=[seg(layout="stack",
                                                     facecam=CAM)]))[0]
    crop = re.search(r"crop=w='([^']+)'", g).group(1)
    assert "iw" in crop and "ih" in crop
    assert not re.search(r"crop=w='\d+(\.\d+)?'", g)


def test_the_window_can_never_be_taller_than_the_source():
    """The browser clamps rh to vh. Expressed as a min() here — same branch,
    without a branch. A window taller than the frame crops black."""
    g = G.build_filtergraph(P.EditPlan(segments=[
        seg(layout="stack", facecam=P.Facecam(zoom=1.0))]))[0]
    assert "min(iw/1.0000,ih/" in g


def test_the_window_slides_back_inside_the_frame_rather_than_hanging_off():
    g = G.build_filtergraph(P.EditPlan(segments=[
        seg(layout="stack", facecam=P.Facecam(off_x=-0.49, off_y=0.49))]))[0]
    x = re.search(r"x='([^']+)'", g).group(1)
    assert x.startswith("max(0,min(iw-ow,")


def test_a_centred_camera_is_the_middle_of_the_frame():
    g = G.build_filtergraph(P.EditPlan(segments=[
        seg(layout="stack", facecam=P.Facecam(off_x=0.0, off_y=0.0))]))[0]
    assert "(0.5+0.0000)*iw" in g


# ── the stack actually composes ─────────────────────────────────────────────

def test_stack_puts_the_camera_over_the_top_and_the_game_under_it():
    g = G.build_filtergraph(P.EditPlan(segments=[seg(layout="stack",
                                                     facecam=CAM)]))[0]
    assert f"scale={G.W}:{G.TOP_H}" in g                 # camera fills the top
    assert f"overlay=0:0" in g
    assert f"{G.TOP_H}+({G.BOT_H}-h)/2" in g             # game centred below


def test_the_gameplay_panel_is_contained_not_cropped():
    """The bottom panel is 1080x1152 and a 16:9 frame fits it at full width
    with room to spare — cropping there throws away sides for nothing."""
    g = G.build_filtergraph(P.EditPlan(segments=[seg(layout="stack",
                                                     facecam=CAM)]))[0]
    assert f"scale={G.W}:{G.BOT_H}:force_original_aspect_ratio=decrease" in g


def test_no_panel_is_ever_a_hard_black_bar():
    g = G.build_filtergraph(P.EditPlan(segments=[seg(layout="stack",
                                                     facecam=CAM)]))[0]
    assert "boxblur" in g, "the backdrop is missing"


def test_a_stacked_shot_still_moves_and_still_joins():
    p = P.EditPlan(segments=[seg("c0", layout="stack", facecam=CAM),
                             seg("c1", layout="none")], trans_dur=0.5)
    g, vlab, _ = G.build_filtergraph(p)
    assert g.count("zoompan=") == 2
    assert g.count("xfade") == 1
    assert vlab == "vout"


def test_two_stacked_shots_do_not_share_label_names():
    """A duplicated label is a graph that fails to parse, and it only shows
    up on the second segment."""
    p = P.EditPlan(segments=[seg("c0", layout="stack", facecam=CAM),
                             seg("c1", layout="stack", facecam=CAM)],
                   trans_dur=0.5)
    g = G.build_filtergraph(p)[0]
    for i in range(2):
        assert f"[camf{i}]" in g and f"[gamef{i}]" in g and f"[st{i}]" in g


def test_corner_keeps_the_gameplay_full_frame():
    g = G.build_filtergraph(P.EditPlan(segments=[seg(layout="corner",
                                                     facecam=CAM)]))[0]
    assert f"crop={G.W}:{G.H}" in g
    assert f"overlay={G.CORNER_PAD}:{G.CORNER_PAD}" in g
    assert f"scale={G.CORNER_W}:-2" in g


def test_a_camera_layout_wins_over_the_framing_choice():
    """There is no "blur, stacked" — stack already decides the whole frame."""
    g = G.build_filtergraph(P.EditPlan(segments=[
        seg(layout="stack", framing="blur", facecam=CAM)]))[0]
    assert g.count("boxblur") == 1, "the blur framing ran as well as the stack"


# ── clipper vs streamer ─────────────────────────────────────────────────────

def test_every_mode_posts_one_clip():
    """Owner, 2026-09-23: "keep it only to one clip" — clipper and streamer
    alike."""
    assert P.limits_for("clipper") == P.limits_for("streamer") == (P.TARGET_S, 1)
    clips = [{"id": f"c{i}", "channel": "novafps"} for i in range(4)]
    sources = {f"c{i}": (f"/t/{i}.mp4", 25.0) for i in range(4)}
    for mode in P.MODES:
        assert len(P.build(clips, sources, mode=mode).segments) == 1, mode


def test_an_unknown_mode_is_treated_as_a_clipper():
    assert P.limits_for("") == P.limits_for("clipper")


def test_a_streamer_gets_one_clip_left_alone():
    clips = [{"id": f"c{i}", "channel": "novafps"} for i in range(4)]
    sources = {f"c{i}": (f"/t/{i}.mp4", 25.0) for i in range(4)}
    p = P.build(clips, sources, mode="streamer")
    assert len(p.segments) == 1
    assert P.plan_duration(p) == pytest.approx(25.0)
    assert P.valid(p)[0]


def test_the_mode_survives_a_fallback():
    """A streamer whose model call failed must not get a clipper's video."""
    plan, meta = C.formula([{"id": f"c{i}", "channel": "n"} for i in range(4)],
                           {f"c{i}": (f"/t/{i}.mp4", 25.0) for i in range(4)},
                           "model timed out", mode="streamer")
    assert meta["source"] == "formula"
    assert len(plan.segments) == 1


def test_the_camera_survives_a_fallback():
    plan, _ = C.formula([{"id": "c0", "channel": "novafps"}],
                        {"c0": ("/t/0.mp4", 25.0)}, "down",
                        facecams={"novafps": CAM})
    assert plan.segments[0].layout == "stack"
    assert plan.segments[0].facecam == CAM


def test_the_formula_stacks_whenever_it_knows_where_the_camera_is():
    p = P.build([{"id": "c0", "channel": "novafps"}],
                {"c0": ("/t/0.mp4", 25.0)}, facecams={"novafps": CAM})
    assert p.segments[0].layout == "stack"
    assert P.valid(p)[0]


def test_the_formula_leaves_an_unknown_channel_alone():
    p = P.build([{"id": "c0", "channel": "someone_else"}],
                {"c0": ("/t/0.mp4", 25.0)}, facecams={"novafps": CAM})
    assert p.segments[0].layout == "none"
    assert P.valid(p)[0]


# ── helper ──────────────────────────────────────────────────────────────────

def coerced(*, layout="stack", facecams=None, mode="clipper"):
    clips = [{"id": "c0", "channel": "novafps"}]
    sources = {"c0": ("/t/0.mp4", 40.0)}
    data = {"segments": [{"clip_id": "c0", "start": 0.0, "end": 30.0,
                          "zoom": "punch", "framing": "fill", "layout": layout,
                          "why": ""}],
            "transition": "slideleft", "sfx": [], "captions": [],
            "thumbnail": {"clip_id": "c0", "at": 2.0, "text": ""},
            "title": "t", "caption": "c", "hashtags": []}
    return C.coerce(data, sources, clips, target_s=60.0, mode=mode,
                    facecams=facecams)


def test_a_model_chosen_stack_is_honoured_where_a_camera_exists():
    plan, _ = coerced(layout="stack", facecams={"novafps": CAM})
    assert plan.segments[0].layout == "stack"
    assert plan.segments[0].facecam == CAM
    assert P.valid(plan)[0]


def test_a_streamer_mode_answer_is_cut_to_one_segment():
    clips = [{"id": f"c{i}", "channel": "n"} for i in range(3)]
    sources = {f"c{i}": (f"/t/{i}.mp4", 40.0) for i in range(3)}
    data = {"segments": [{"clip_id": f"c{i}", "start": 0.0, "end": 20.0,
                          "zoom": "punch", "framing": "fill", "layout": "none",
                          "why": ""} for i in range(3)],
            "transition": "slideleft", "sfx": [], "captions": [],
            "thumbnail": {"clip_id": "c0", "at": 1.0, "text": ""},
            "title": "t", "caption": "c", "hashtags": []}
    plan, _ = C.coerce(data, sources, clips, target_s=60.0, mode="streamer")
    assert len(plan.segments) == 1
