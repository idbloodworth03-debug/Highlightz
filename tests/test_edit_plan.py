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


def test_one_long_clip_is_left_whole():
    """A clip that already runs a minute is a perfectly good edit. Cutting it
    into three pieces to have joins would be decoration for its own sake."""
    p = P.build([clip("c1")], {"c1": ("/tmp/c1.mp4", 62.0)})
    assert len(p.segments) == 1
    assert P.plan_duration(p) == pytest.approx(P.TARGET_S, abs=0.1)


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


# ── one clip, and the hook (owner, 2026-09-23) ──────────────────────────────
#
# "we basically make the user pick out a 5-10 second part of the clip that is
# either a hype moment or a controversial part … duplicate that 5-10 second
# part, put it at the beginning, roll the original clip from the start again.
# Make the same swoosh slide in transition for this part as well and instead
# of combining clips just keep it only to one clip."

def hooked(start=20.0, end=28.0, dur=40.0):
    c = clip("c1")
    c["hook"] = {"start": start, "end": end}
    return P.build([c], {"c1": ("/tmp/c1.mp4", dur)})


def test_the_builder_never_combines_clips():
    clips = [clip(f"c{i}", virality=90 - i) for i in range(4)]
    for dur in (18.0, 30.0, 59.9):
        p = P.build(clips, {f"c{i}": (f"/tmp/c{i}.mp4", dur) for i in range(4)})
        assert len({s.clip_id for s in p.segments}) == 1, f"{dur}s clips were stitched"
        assert len(p.segments) == 1 and not p.hook


def test_the_clip_plays_whole_from_its_start():
    p = P.build([clip("c1")], {"c1": ("/tmp/c1.mp4", 37.5)})
    assert (p.segments[0].start, p.segments[0].end) == (0.0, 37.5)
    assert P.plan_duration(p) == pytest.approx(37.5)


def test_a_long_upload_stops_short_of_three_minutes():
    """YouTube Shorts takes up to three minutes; nothing else caps it."""
    p = P.build([clip("c1")], {"c1": ("/tmp/c1.mp4", 600.0)})
    assert P.plan_duration(p) == pytest.approx(P.MAX_MAIN_S) and P.MAX_MAIN_S < 180


def test_the_hook_opens_the_video_then_the_clip_plays_from_the_start():
    p = hooked(20.0, 28.0, 40.0)
    assert p.hook and P.valid(p)[0]
    hook, main = p.segments
    assert (hook.start, hook.end) == (20.0, 28.0)
    assert (main.start, main.end) == (0.0, 40.0)
    assert hook.src == main.src and hook.clip_id == main.clip_id == "c1"
    # One join, which overlaps: 8 + 40 - 0.5.
    assert P.plan_duration(p) == pytest.approx(47.5)


def test_the_hook_slides_across_with_the_same_whoosh():
    """Slide in on the hook, slide + whoosh into the clip, slide out."""
    p = hooked(20.0, 28.0, 40.0)
    assert p.slide_in and p.slide_out and p.transition == "slideleft"
    assert [(c.kind, c.at) for c in p.sfx] == [
        ("whoosh", 0.0), ("whoosh", 7.5), ("whoosh", pytest.approx(47.0))]


@pytest.mark.parametrize("start,end", [
    (20.0, 24.0),     # 4s: too short to be a moment
    (20.0, 31.0),     # 11s: longer than bait
    (35.0, 43.0),     # runs off the end of a 40s clip
    (-1.0, 6.0),      # before the start
    (28.0, 20.0),     # inside out
])
def test_an_unusable_hook_is_ignored_and_the_clip_still_gets_its_edit(start, end):
    p = hooked(start, end, 40.0)
    assert not p.hook and len(p.segments) == 1 and P.valid(p)[0]


def test_a_garbled_hook_is_ignored_not_crashed():
    for raw in ("12-20", {"start": "x", "end": 20}, {"start": 3}, None, [5, 10]):
        c = clip("c1")
        c["hook"] = raw
        p = P.build([c], {"c1": ("/tmp/c1.mp4", 40.0)})
        assert not p.hook and len(p.segments) == 1


@pytest.mark.parametrize("length", [5.0, 10.0])
def test_the_hook_bounds_are_inclusive(length):
    p = hooked(10.0, 10.0 + length, 40.0)
    assert p.hook and P.valid(p)[0]


def test_only_the_hook_may_be_shorter_than_a_shot():
    """A five-second hook is fine; a five-second ordinary shot is still one a
    transition would eat."""
    assert P.valid(hooked(10.0, 15.0))[0]
    assert not P.valid(P.EditPlan(segments=[seg(0, 5.0), seg(0, 30.0)]))[0]


def test_a_plan_cannot_claim_a_hook_it_does_not_have():
    good = hooked()
    for broken, why in (
        (dict(segments=good.segments[:1]), "hook and one clip"),
        (dict(segments=[good.segments[0], P.Segment(src="/tmp/other.mp4",
                                                     start=0, end=40)]), "not from the clip"),
        (dict(segments=[P.Segment(src="/tmp/c1.mp4", start=0, end=12),
                        good.segments[1]]), "5-10s"),
        (dict(segments=[P.Segment(src="/tmp/c1.mp4", start=30, end=38),
                        P.Segment(src="/tmp/c1.mp4", start=0, end=25)]), "outside"),
    ):
        p = P.EditPlan(hook=True, trans_dur=0.5, **broken)
        ok, msg = P.valid(p)
        assert not ok and why in msg, (why, msg)


def test_the_hook_is_read_against_the_part_of_the_clip_that_plays():
    """A hook past MAX_MAIN_S would be a preview of footage the video never
    reaches."""
    p = hooked(175.0, 182.0, 600.0)
    assert not p.hook
