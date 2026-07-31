"""
Viewer-clip analyzer.

The output of this script is going to drive a decision about the trigger
formula, so its arithmetic has to be right on data whose answer is known. A
plausible-looking wrong number here is worse than no analysis at all — it
would send a threshold in the wrong direction on real streams.
"""

import json

import pytest

from src.maintenance import analyze_viewer_clips as az


def rec(ts, channel="chan", peak=None, score=None, thr=50.0, creator="v1", clip="s"):
    r = {"ts": ts, "channel": channel, "clip_id": clip, "creator_id": creator,
         "creator": creator, "title": "", "threshold": thr}
    if peak is not None:
        r["our_peak"] = peak
    if score is not None:
        r["our_score"] = score
    return r


# ── The measure actually used ─────────────────────────────────────────────

def test_peak_is_preferred_over_the_biased_point_sample():
    """Both present: the window peak wins. The point sample is taken after the
    moment and understates the bot; silently averaging them would corrupt
    every downstream number."""
    assert az.effective_score(rec(1, peak=80.0, score=10.0)) == (80.0, "peak")
    assert az.effective_score(rec(1, score=10.0)) == (10.0, "point")
    assert az.effective_score(rec(1)) == (None, "none")


# ── Moment clustering ─────────────────────────────────────────────────────

def test_a_viral_moment_counts_once_not_ten_times():
    """Ten viewers clipping one play is ONE highlight. Counting clips instead
    of moments would let a single viral moment dominate the hit rate."""
    rows = [rec(1000 + i, creator=f"v{i}") for i in range(10)]
    assert len(az.cluster_moments(rows)) == 1

    # Separated by more than the gap = genuinely different moments.
    far = [rec(1000), rec(1000 + az.CONSENSUS_GAP + 5), rec(3000)]
    assert len(az.cluster_moments(far)) == 3


def test_clustering_is_order_independent():
    a = az.cluster_moments([rec(1000), rec(1010), rec(5000)])
    b = az.cluster_moments([rec(5000), rec(1010), rec(1000)])
    assert [len(x) for x in a] == [len(x) for x in b] == [2, 1]


# ── Matching our clips to their moments ───────────────────────────────────

def test_our_clip_matches_when_it_precedes_the_viewer_clip():
    """We fire at the peak; the viewer's created_at lands after it. The window
    must therefore reach further BACK than forward, or real catches get
    scored as misses."""
    ours = [{"channel": "chan", "created_at": 1000.0, "id": "c1"}]
    assert az.our_clip_near(ours, "chan", 1000.0 + 60) is not None   # we led by 60s
    assert az.our_clip_near(ours, "chan", 1000.0 + 200) is None      # far too early
    assert az.our_clip_near(ours, "chan", 1000.0 - 20) is not None   # slight overshoot ok
    assert az.our_clip_near(ours, "chan", 1000.0 - 200) is None


def test_matching_is_per_channel():
    """Two monitored streams spiking at once must not credit each other."""
    ours = [{"channel": "other", "created_at": 1000.0, "id": "c1"}]
    assert az.our_clip_near(ours, "chan", 1000.0) is None
    assert az.our_clip_near(ours, "OTHER", 1000.0) is not None       # case-insensitive


def test_the_nearest_of_several_clips_is_chosen():
    ours = [{"channel": "c", "created_at": 1000.0, "id": "far"},
            {"channel": "c", "created_at": 1050.0, "id": "near"}]
    assert az.our_clip_near(ours, "c", 1060.0)["id"] == "near"


# ── Percentiles ───────────────────────────────────────────────────────────

def test_percentiles_on_a_known_series():
    vals = [float(i) for i in range(1, 11)]      # 1..10
    assert az._percentile(vals, 0.0) == 1.0
    assert az._percentile(vals, 1.0) == 10.0
    # round(0.5*9) = round(4.5) = 4 under banker's rounding, so index 4 -> 5.0.
    # For an even-length series this picks the LOWER of the two middles; that
    # is a deliberate, conservative choice, not interpolation.
    assert az._percentile(vals, 0.5) == 5.0
    assert az._percentile(vals, 0.9) == 9.0
    assert az._percentile([7.0], 0.5) == 7.0     # single sample
    assert az._percentile([], 0.5) == 0.0


# ── Loading ───────────────────────────────────────────────────────────────

def test_a_torn_last_line_does_not_lose_the_whole_file(tmp_path):
    """kill -9 mid-append leaves a partial JSON line. Days of learning data
    must not be discarded because of the last few bytes."""
    p = tmp_path / "v.jsonl"
    p.write_text(json.dumps(rec(1)) + "\n" + json.dumps(rec(2)) + "\n" + '{"ts": 3, "cha')
    assert len(az.load_viewer_clips(p)) == 2


def test_missing_files_report_empty_rather_than_raising(tmp_path):
    assert az.load_viewer_clips(tmp_path / "nope.jsonl") == []
    assert az.load_our_clips(tmp_path / "nope.json") == []


# ── End to end on data with a known answer ────────────────────────────────

def test_report_arithmetic_on_a_constructed_case(capsys):
    """Three moments, one channel, bar 50:
         A  peak 80, we clipped it        -> over bar, caught
         B  peak 70, we did NOT clip it   -> over bar, MISSED (cooldown-shaped)
         C  peak 20, we did NOT clip it   -> under bar, missed
       So: 2/3 over bar (66.7%), 1/3 clipped (33.3%).
    """
    rows = [rec(1000, peak=80.0, clip="a"),
            rec(5000, peak=70.0, clip="b", creator="v2"),
            rec(9000, peak=20.0, clip="c", creator="v3")]
    ours = [{"channel": "chan", "created_at": 1000.0, "id": "ours-a"}]

    az.report(rows, ours)
    out = capsys.readouterr().out

    assert "3 viewer clips" in out
    assert " 66.7%" in out, "over-bar rate wrong"
    assert " 33.3%" in out, "actually-clipped rate wrong"
    # B beat the bar and still produced nothing — that is the cooldown-shaped
    # bug the report is meant to surface, so it must be listed.
    assert "OVER BAR" in out


def test_report_warns_loudly_when_every_record_is_a_point_sample(capsys):
    az.report([rec(1000, score=30.0), rec(5000, score=40.0)], [])
    out = capsys.readouterr().out
    assert "UNDERSTATE" in out.upper(), "no warning that the numbers are biased low"


def test_report_survives_an_empty_dataset(capsys):
    az.report([], [])
    assert "No viewer clips recorded yet" in capsys.readouterr().out


def test_records_with_no_paired_score_are_excluded_not_counted_as_zero(capsys):
    """'We weren't watching' and 'we scored 0' are different facts. Treating
    the first as the second would invent misses that never happened."""
    rows = [rec(1000, peak=80.0), rec(5000)]        # second has no score at all
    az.report(rows, [{"channel": "chan", "created_at": 1000.0}])
    out = capsys.readouterr().out
    assert "paired with one of our scores: 1/2" in out
    assert "100.0%" in out, "the one paired moment should score 100%, not 50%"
