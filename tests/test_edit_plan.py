"""The edit plan: the arithmetic, the ranking, and what a bad plan cannot do.

Owner, 2026-09-19: the auto-edit has to produce something a clipper can
post as their own — around sixty seconds, sound effects, transitions
throughout, framing that moves. Before this, `render.py` put a static crop
and a fade on one clip and called it an edit.

The plan is the contract between whatever decides the edit (a formula today,
a model later) and the renderer. These pin the two things that decide whether
the output is usable at all:

  * the DURATION ARITHMETIC, because xfade overlaps its inputs, so joining
    segments subtracts. Getting this wrong means "60 seconds" is 66 and
    TikTok refuses the upload;
  * VALIDATION, because the second builder is a language model and an
    out-point past the end of the file is an ffmpeg failure three minutes
    into a render.
"""

import pytest

from src.autopilot import plan as P


def seg(start=0.0, end=20.0, **kw):
    return P.Segment(src="/tmp/a.mp4", start=start, end=end, **kw)


def clip(cid, *, suggested=False, virality=0.0, score=50.0, at=1000.0):
    return {"id": cid, "suggested": suggested, "virality_score": virality,
            "score": score, "approved_at": at, "channel": "novafps"}


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_a_single_segment_is_its_own_length():
    p = P.EditPlan(segments=[seg(0, 41.5)])
    assert P.plan_duration(p) == pytest.approx(41.5)


def test_joining_segments_subtracts_the_transition():
    """xfade OVERLAPS. Three 22s segments with 0.5s joins is 65s, not 66 —
    and a plan that thinks otherwise overshoots the platform ceiling."""
    p = P.EditPlan(segments=[seg(0, 22), seg(0, 22), seg(0, 22)], trans_dur=0.5)
    assert P.plan_duration(p) == pytest.approx(65.0)


def test_an_empty_plan_is_zero_not_a_crash():
    assert P.plan_duration(P.EditPlan()) == 0.0


def test_the_builder_lands_just_over_a_minute():
    """The requirement is a number, so this is the test that matters most.
    Four 30s clips: two used whole are 59.5s — 2.5s short of the target, too
    little for a shot, and not paid by TikTok. It used to stop there; now a
    third, minimum-length shot takes it over the line."""
    clips = [clip(f"c{i}", virality=90 - i) for i in range(4)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 30.0) for i in range(4)}
    p = P.build(clips, sources)
    assert P.SAFELY_OVER_S <= P.plan_duration(p) <= 66.5
    ok, why = P.valid(p)
    assert ok, why


def test_one_long_clip_is_left_whole():
    """A clip that already runs a minute is a perfectly good edit. Cutting it
    into three pieces to have joins would be decoration for its own sake."""
    p = P.build([clip("c1")], {"c1": ("/tmp/c1.mp4", 62.0)})
    assert len(p.segments) == 1
    assert P.plan_duration(p) == pytest.approx(P.TARGET_S, abs=0.1)


def test_short_clips_are_assembled_until_the_target_is_reached():
    clips = [clip(f"c{i}") for i in range(4)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 18.0) for i in range(4)}
    p = P.build(clips, sources)
    assert len(p.segments) >= 3, "one 18s clip was posted as a 60s edit"
    assert P.plan_duration(p) <= P.TARGET_S + 0.5


def test_it_never_exceeds_the_segment_ceiling():
    clips = [clip(f"c{i}") for i in range(12)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 7.0) for i in range(12)}
    p = P.build(clips, sources)
    assert len(p.segments) <= P.MAX_SEGMENTS


def test_a_clip_too_short_to_survive_a_transition_is_skipped():
    p = P.build([clip("tiny"), clip("ok")],
                {"tiny": ("/tmp/t.mp4", 2.0), "ok": ("/tmp/o.mp4", 30.0)})
    assert [s.clip_id for s in p.segments] == ["ok"]


def test_a_clip_with_no_file_is_not_planned():
    p = P.build([clip("has"), clip("none")], {"has": ("/tmp/h.mp4", 30.0)})
    assert [s.clip_id for s in p.segments] == ["has"]


# ── which clips, in which order ──────────────────────────────────────────────

def test_highlights_come_first_whatever_their_score():
    """Owner: "highlight clips always get pushed out first and after that
    clips with the highest virality." A highlight is a moment a crowd of
    viewers clipped themselves — other people voting beats any number the
    formula produced."""
    out = P.rank([clip("plain", virality=99, score=99),
                  clip("crowd", suggested=True, virality=1, score=1)])
    assert [c["id"] for c in out] == ["crowd", "plain"]


def test_within_a_group_the_most_viral_wins():
    out = P.rank([clip("mid", virality=40), clip("top", virality=90),
                  clip("low", virality=5)])
    assert [c["id"] for c in out] == ["top", "mid", "low"]


def test_virality_ties_fall_back_to_the_trigger_score_then_to_newest():
    out = P.rank([clip("older", virality=50, score=70, at=100),
                  clip("newer", virality=50, score=70, at=900),
                  clip("better", virality=50, score=95, at=100)])
    assert [c["id"] for c in out] == ["better", "newer", "older"]


def test_the_builder_takes_the_ranked_order():
    clips = [clip("plain", virality=99), clip("crowd", suggested=True)]
    sources = {"plain": ("/tmp/p.mp4", 40.0), "crowd": ("/tmp/c.mp4", 40.0)}
    p = P.build(clips, sources)
    assert p.segments[0].clip_id == "crowd"


# ── what makes it look edited ────────────────────────────────────────────────

def test_a_clipper_video_is_longer_than_a_minute_but_not_by_much():
    """TikTok's Creator Rewards pays only for videos LONGER than one minute;
    60.00s is not. Owner, 2026-09-23: fix that, "but do not make clips too
    long either"."""
    assert P.SAFELY_OVER_S <= P.TARGET_S <= 65.0
    for n, dur in ((1, 90.0), (2, 36.0), (4, 18.0), (4, 30.0), (3, 30.0), (2, 59.9)):
        clips = [clip(f"c{i}") for i in range(n)]
        p = P.build(clips, {f"c{i}": (f"/tmp/c{i}.mp4", dur) for i in range(n)})
        assert P.SAFELY_OVER_S <= P.plan_duration(p) <= 66.5, f"{n} clips of {dur}s"
        assert P.valid(p)[0]


def test_when_the_footage_cannot_reach_a_minute_the_builder_does_not_pretend():
    """Two 30s clips are 59.5s at most. Nothing to add; it stays as it is
    rather than stretching or looping anything."""
    clips = [clip("a"), clip("b")]
    p = P.build(clips, {"a": ("/tmp/a.mp4", 30.0), "b": ("/tmp/b.mp4", 30.0)})
    assert P.plan_duration(p) == pytest.approx(59.5) and len(p.segments) == 2


def test_the_formula_slides_in_and_out_with_a_whoosh_and_slides_every_cut():
    """Owner, 2026-09-23: "one at the beginning like it sliding into frame
    with a whoosh sound and then one at the end with it sliding out and the
    same sound. Also if other clips are combined make them do the same
    thing." One move, one sound, nothing else."""
    clips = [clip(f"c{i}") for i in range(3)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 22.0) for i in range(3)}
    p = P.build(clips, sources)
    assert p.slide_in and p.slide_out and p.transition == "slideleft"
    total, d = P.plan_duration(p), p.trans_dur
    # Where each cut's transition BEGINS — the same list the graph's xfade
    # offsets are (graph._offsets is segment_starts()[1:]).
    cut_starts = P.segment_starts(p)[1:]
    assert {c.kind for c in p.sfx} == {"whoosh"}, "only the whoosh"
    assert [c.at for c in p.sfx] == pytest.approx([0.0, *cut_starts, total - d])
    assert len({c.gain for c in p.sfx}) == 1, "the same sound, not a louder one somewhere"


def test_a_plan_that_does_not_slide_only_whooshes_its_cuts():
    """A model plan that opts out keeps the old fades and gets no open/close
    whoosh — the flags are what decide, not the builder's name."""
    p = P.EditPlan(segments=[seg(0, 20), seg(0, 20)], trans_dur=0.5)
    assert [(c.kind, c.at) for c in P._sfx_for(p)] == [("whoosh", 19.5)]


def test_too_short_to_slide_in_and_out_is_refused():
    p = P.EditPlan(segments=[seg(0, 0.9)], slide_in=True, slide_out=True)
    ok, why = P.valid(p)
    assert not ok


def test_sound_is_timed_against_the_finished_video_not_the_segment():
    """A cue placed by summing segment lengths lands late by one transition
    per join, which drifts further with every cut."""
    p = P.EditPlan(segments=[seg(0, 20), seg(0, 20), seg(0, 20)], trans_dur=0.5)
    p.sfx = P._sfx_for(p)
    cuts = [c.at for c in p.sfx if c.kind == "whoosh"]
    assert cuts == pytest.approx([19.5, 39.0])
    assert max(c.at for c in p.sfx) <= P.plan_duration(p)


def test_the_framing_moves_and_does_not_repeat_the_same_move():
    clips = [clip(f"c{i}") for i in range(3)]
    sources = {f"c{i}": (f"/tmp/c{i}.mp4", 22.0) for i in range(3)}
    p = P.build(clips, sources)
    zooms = [s.zoom for s in p.segments]
    assert zooms[0] == "punch", "the opener does not grab"
    assert "none" not in zooms, "a static crop is what this exists to stop being"
    assert len(set(zooms)) > 1, "the same move on every shot"


def test_the_window_keeps_the_payoff_not_the_lead_in():
    """The clip was cut as [trigger - pre_roll, trigger + post_roll] and the
    presets put pre above post, so the moment is late in the file and the
    front is run-up."""
    start, end = P._window(40.0, 20.0)
    assert end == 40.0
    assert start == pytest.approx(20.0)


# ── validation, because the next builder is a language model ─────────────────

def test_a_good_plan_validates():
    p = P.build([clip("c1")], {"c1": ("/tmp/c1.mp4", 45.0)})
    assert P.valid(p) == (True, "")


@pytest.mark.parametrize("bad,why", [
    (P.EditPlan(), "no segments"),
    (P.EditPlan(segments=[seg(0, 2)]), "shorter"),
    (P.EditPlan(segments=[seg(20, 5)]), "inside out"),
    (P.EditPlan(segments=[seg(-4, 20)]), "inside out"),
    (P.EditPlan(segments=[seg(0, 20, zoom="spin")]), "zoom"),
    (P.EditPlan(segments=[seg(0, 20)], transition="explode"), "transition"),
    (P.EditPlan(segments=[seg(0, 20)], trans_dur=0), "duration"),
    (P.EditPlan(segments=[seg(0, 20)], trans_dur=9), "duration"),
])
def test_a_broken_plan_is_refused_with_a_reason(bad, why):
    ok, msg = P.valid(bad)
    assert not ok and why in msg


def test_a_sound_cue_past_the_end_is_refused():
    p = P.EditPlan(segments=[seg(0, 20)], sfx=[P.Sfx(at=99.0, kind="hit")])
    ok, msg = P.valid(p)
    assert not ok and "outside" in msg


def test_an_unknown_sound_is_refused():
    p = P.EditPlan(segments=[seg(0, 20)], sfx=[P.Sfx(at=1.0, kind="airhorn")])
    assert not P.valid(p)[0]


def test_too_many_segments_is_refused():
    p = P.EditPlan(segments=[seg(0, 20)] * (P.MAX_SEGMENTS + 1))
    ok, msg = P.valid(p)
    assert not ok and "max" in msg


def test_the_sfx_names_match_the_browser_editors():
    """The same plan has to mean the same thing in both renderers, or a clip
    previewed in the editor sounds different once the server makes it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    for kind in P.SFX_KINDS:
        assert f"{kind}(ctx, dest, at, vol)" in html, f"{kind} has no browser twin"


def test_a_plan_round_trips_through_json():
    """It is stored on the clip and will come back from a model as JSON."""
    import json
    p = P.build([clip("c1")], {"c1": ("/tmp/c1.mp4", 45.0)})
    d = json.loads(json.dumps(p.to_dict()))
    assert d["segments"][0]["clip_id"] == "c1"
    assert d["source"] == "formula"
