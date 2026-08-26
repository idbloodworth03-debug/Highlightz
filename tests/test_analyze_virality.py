"""The virality report has to be right about data it has never seen.

This is a measuring instrument, and its output will be used to decide whether
to change a formula that controls what gets clipped. An instrument that reads
plausibly on real data and is quietly wrong is worse than no instrument: the
numbers look like evidence.

So it is checked against fixtures whose answer is known in advance — a score
that is PERFECT, one that is PURE NOISE, and one that is BACKWARDS — and it has
to say so in each case. Anything that cannot tell those three apart cannot be
trusted on production data either.
"""

import json
import math

import pytest

from src.maintenance.analyze_virality import auc, mean, median, stdev


# ── the statistics themselves ────────────────────────────────────────────────

def test_auc_is_one_when_the_score_is_perfect():
    """Every kept clip scores above every discarded one."""
    assert auc([80, 90, 100], [10, 20, 30]) == 1.0


def test_auc_is_zero_when_the_score_is_exactly_backwards():
    assert auc([10, 20, 30], [80, 90, 100]) == 0.0


def test_auc_is_a_half_when_the_score_says_nothing():
    """THE NUMBER THE WHOLE REPORT TURNS ON. If this drifted off 0.5 the report
    would call a useless score useful."""
    # Genuinely interleaved. [10,30,50,70] vs [20,40,60,80] LOOKS interleaved
    # and is not — every positive sits just below a negative, which is a real
    # 0.375. Picking that fixture by eye is exactly the mistake this test is
    # here to catch in the code.
    assert auc([10, 40, 60, 90], [20, 30, 70, 80]) == pytest.approx(0.5, abs=0.02)


def test_auc_counts_ties_as_half_rather_than_as_a_win():
    """A score that gives every clip the same value has no skill. Ranking ties
    arbitrarily would report 1.0 or 0.0 for a constant."""
    assert auc([50, 50, 50], [50, 50, 50]) == pytest.approx(0.5)


def test_auc_refuses_rather_than_inventing_a_number_for_one_class():
    assert auc([], [1, 2, 3]) is None
    assert auc([1, 2, 3], []) is None


def test_auc_is_unaffected_by_the_scale_of_the_score():
    """It is a ranking measure. If it moved when the numbers were rescaled, a
    formula change that only shifted the range would look like an improvement."""
    pos, neg = [60, 70, 80], [10, 50, 65]
    assert auc(pos, neg) == auc([p * 3 + 1000 for p in pos], [n * 3 + 1000 for n in neg])


def test_the_averages_do_not_crash_on_nothing():
    assert mean([]) is None and median([]) is None and stdev([]) is None
    assert stdev([5]) is None, "one sample has no spread to report"


def test_median_handles_an_even_count():
    assert median([1, 2, 3, 4]) == 2.5


# ── the report end to end, on data whose answer is known ─────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.maintenance import analyze_virality as av
    monkeypatch.setattr(av, "STORE", tmp_path)
    for name in ("HUMAN", "TRAIN", "VIEWER", "CLIPS", "USERS"):
        monkeypatch.setattr(av, name, tmp_path / {
            "HUMAN": "human_scores.jsonl", "TRAIN": "training_log.jsonl",
            "VIEWER": "viewer_clips.jsonl", "CLIPS": "clips.json",
            "USERS": "users.json"}[name])
    return av, tmp_path


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _human(i, human_v, bot_v, labeler="lab1", sig=None):
    return {"ts": 1000 + i, "clip_id": f"c{i}", "channel": "nova",
            "labeler_id": labeler, "labeler": labeler,
            "human": {"virality": human_v, "sentiment": 5, "audio": 5},
            "bot_signals": sig or {"AUDIO_SPIKE": bot_v / 100},
            "bot_trigger_score": 50, "bot_virality_score": bot_v}


# trigger=13 by default so virality never accidentally equals round(13*0.7,1)
# = 9.1 for an integer score — otherwise a stray fixture row gets classified as
# a VOD moment and quietly changes the counts a test is asserting on.
def _outcome(i, label, virality, user="u_other", trigger=13):
    return {"ts": 1000 + i, "clip_id": f"o{i}", "channel": "nova",
            "user_id": user, "label": label, "trigger_score": trigger,
            "virality_score": virality, "signals": {"AUDIO_SPIKE": virality / 100}}


def _run(av, capsys, argv):
    import sys
    old = sys.argv
    sys.argv = ["analyze_virality"] + argv
    try:
        rc = av.main()
    finally:
        sys.argv = old
    return rc, capsys.readouterr().out


def test_a_perfect_score_is_reported_as_perfect(store, capsys):
    """Human rating and bot score move together exactly."""
    av, tmp = store
    _write(av.HUMAN, [_human(i, h, h * 10) for i in range(40)
                      for h in [(i % 10) + 1]])
    _write(av.TRAIN, [_outcome(i, "approved", 90) for i in range(40)]
                     + [_outcome(100 + i, "rejected", 10) for i in range(40)])
    rc, out = _run(av, capsys, [])
    assert rc == 0
    assert "AUC, virality_score -> approved :   1.000" in out
    assert "carrying useful information" in out


def test_a_backwards_score_is_not_reported_as_working(store, capsys):
    """The clips people KEEP score lowest. An instrument that reported this as
    fine would recommend keeping a formula that is inverted."""
    av, tmp = store
    _write(av.TRAIN, [_outcome(i, "approved", 10) for i in range(40)]
                     + [_outcome(100 + i, "rejected", 90) for i in range(40)])
    rc, out = _run(av, capsys, [])
    assert "AUC, virality_score -> approved :   0.000" in out
    assert "coin flip" in out
    assert "carrying useful information" not in out


def test_pure_noise_is_called_a_coin_flip(store, capsys):
    """THE CASE THAT MATTERS MOST — it is the likely real answer, and the one a
    sloppy report would dress up as a weak positive."""
    av, tmp = store
    rows = []
    for i in range(60):
        rows.append(_outcome(i, "approved" if i % 2 == 0 else "rejected",
                             (i * 37) % 100))
    _write(av.TRAIN, rows)
    rc, out = _run(av, capsys, [])
    line = [l for l in out.splitlines() if "AUC, virality_score" in l][0]
    value = float(line.split(":")[1].strip())
    assert 0.4 <= value <= 0.6, f"noise reported as {value}"
    assert "coin flip" in out


def test_the_excluded_account_really_is_excluded(store, capsys):
    """The whole reason for --exclude. The owner's 200 indiscriminate approvals
    must not be able to drag the number."""
    av, tmp = store
    (tmp / "users.json").write_text(json.dumps([
        {"id": "u_owner", "username": "Bloodworthhh"},
        {"id": "u_other", "username": "someone"}]))
    noise = [_outcome(i, "approved", (i * 37) % 100, user="u_owner")
             for i in range(200)]
    real = ([_outcome(500 + i, "approved", 90, user="u_other") for i in range(30)]
            + [_outcome(600 + i, "rejected", 10, user="u_other") for i in range(30)])
    _write(av.TRAIN, noise + real)

    _, without = _run(av, capsys, [])
    _, with_ex = _run(av, capsys, ["--exclude", "Bloodworthhh"])
    assert "260 outcomes -> 60 after exclusions" in with_ex, \
        "the exclusion did not actually remove the owner's rows"
    assert "AUC, virality_score -> approved :   1.000" in with_ex
    assert "1.000" not in without.split("per account")[0].split("AUC")[1][:20], \
        "the owner's noise was supposed to be dragging the unfiltered number"


def test_vod_moments_are_kept_out_of_the_correlations(store, capsys):
    """A VOD moment's virality IS trigger*0.7 — a rescaled copy of the trigger,
    not an independent estimate. Left in, it drags every correlation toward
    whatever the trigger predicts and makes this formula look like it tracks
    reality when what it is tracking is itself."""
    av, tmp = store
    live = [_outcome(i, "approved" if i % 2 else "rejected", (i * 31) % 100)
            for i in range(60)]
    vod = [dict(_outcome(500 + i, "approved", 0), trigger_score=t,
                virality_score=round(t * 0.7, 1))
           for i, t in enumerate(range(20, 90))]
    _write(av.TRAIN, live + vod)

    _, out = _run(av, capsys, [])
    assert "VOD-shaped" in out, "the VOD moments were not detected"
    assert "60 outcomes ->" in out, \
        "VOD moments reached the correlation section"

    _, kept = _run(av, capsys, ["--include-vod"])
    assert "130 outcomes ->" in kept, "--include-vod did not put them back"


def test_a_clip_the_store_marks_as_vod_is_detected_even_without_the_identity(store, capsys):
    av, tmp = store
    (tmp / "clips.json").write_text(json.dumps(
        [{"id": "v1", "is_vod_moment": True, "trigger_score": 80,
          "virality_score": 12}]))
    from src.maintenance.analyze_virality import is_vod_shaped
    assert is_vod_shaped({"is_vod_moment": True, "trigger_score": 80,
                          "virality_score": 12})
    assert is_vod_shaped({"vod_id": "v9", "virality_score": 12})
    assert not is_vod_shaped({"trigger_score": 80, "virality_score": 12})


def test_the_advice_names_the_right_risk(store, capsys):
    """Nothing gates capture on virality_score, so a weight change here cannot
    cost clip volume — and simulate_weights.py, which models volume, has
    nothing to say about it. Telling the owner to run it would send them to
    the wrong tool for the wrong risk."""
    av, tmp = store
    rows = []
    for i in range(60):
        v = (i % 10) + 1
        rows.append(_human(i, v, v * 10, sig={
            "AUDIO_SPIKE": v / 10, "SILENCE_BURST": (v / 10) * 0.6,
            "EMOTE_HOMOGENEITY": (v / 10) * 0.3, "KEYWORD": 0.5}))
    _write(av.HUMAN, rows)
    _, out = _run(av, capsys, [])
    assert "cannot cost clip volume" in out
    assert "nothing to say about this formula" in out


def test_nothing_in_the_pipeline_gates_a_clip_on_virality():
    """The claim above, checked against the code rather than remembered. If a
    threshold ever starts reading virality_score, the advice becomes wrong and
    a weight change becomes able to change what gets captured."""
    import inspect
    import re as _re
    from src.trigger import engine
    # A comparison with virality on one side of it — `-> float:` in a signature
    # is not one, which is why this looks for the operator NEXT TO the name.
    cmp_re = _re.compile(r"virality\w*\s*(?:>=|<=|>|<)|(?:>=|<=|>|<)\s*[\w.]*virality")
    for line in inspect.getsource(engine).splitlines():
        stripped = line.strip()
        if "virality" not in stripped or stripped.startswith(("#", '"', "'")):
            continue
        assert not cmp_re.search(stripped), (
            "something now compares virality_score against a threshold, so a "
            "weight change here CAN now affect what gets clipped:\n  " + stripped)


def test_a_misspelled_exclusion_is_reported_rather_than_silently_ignored(store, capsys):
    """An --exclude that matches nothing excludes nobody, and the report would
    otherwise look identical to one where it worked."""
    av, tmp = store
    (tmp / "users.json").write_text(json.dumps([{"id": "u1", "username": "real"}]))
    _write(av.TRAIN, [_outcome(i, "approved", 50) for i in range(40)])
    _, out = _run(av, capsys, ["--exclude", "Bloodworth"])   # missing an h
    assert "matched no account" in out and "Bloodworth" in out


def test_an_account_can_be_excluded_by_twitch_login_or_id_too(store, capsys):
    av, tmp = store
    (tmp / "users.json").write_text(json.dumps([
        {"id": "u_owner", "username": "Display Name", "twitch_login": "bloodworthhh"}]))
    _write(av.TRAIN, [_outcome(i, "approved", 50, user="u_owner") for i in range(40)])
    _, out = _run(av, capsys, ["--exclude", "bloodworthhh"])
    assert "40 outcomes -> 0 after exclusions" in out


def test_a_flat_score_is_flagged_before_any_correlation_is_believed(store, capsys):
    """Section 1 exists so nobody reads a correlation on a constant."""
    av, tmp = store
    (tmp / "clips.json").write_text(json.dumps(
        [{"id": f"c{i}", "virality_score": 50} for i in range(50)]))
    _, out = _run(av, capsys, [])
    assert "sd 0.0" in out


def test_clips_scoring_exactly_zero_are_called_out(store, capsys):
    """A pile of 0.0 usually means missing signals, not unremarkable moments —
    and averaged in silently it makes every other number look worse."""
    av, tmp = store
    (tmp / "clips.json").write_text(json.dumps(
        [{"id": f"c{i}", "virality_score": 0 if i < 20 else 60} for i in range(50)]))
    _, out = _run(av, capsys, [])
    assert "score exactly 0" in out


def test_it_says_so_instead_of_guessing_when_there_is_no_data(store, capsys):
    av, tmp = store
    rc, out = _run(av, capsys, [])
    assert rc == 0, "an empty store must not crash the report"
    assert "NONE at" in out
    assert "not enough" in out.lower()


def test_it_warns_when_the_humans_themselves_did_not_discriminate(store, capsys):
    """Correlating a bot score against a rater who put 5 on everything measures
    the rater, not the bot."""
    av, tmp = store
    _write(av.HUMAN, [_human(i, 5, (i * 7) % 100) for i in range(40)])
    _, out = _run(av, capsys, [])
    assert "humans barely discriminate" in out.lower()


def test_it_compares_virality_against_the_trigger_score_it_has_to_beat(store, capsys):
    """A separate virality formula only earns its six weights if it outperforms
    the number that already existed."""
    av, tmp = store
    _write(av.TRAIN, [_outcome(i, "approved", 60, trigger=90) for i in range(30)]
                     + [_outcome(100 + i, "rejected", 40, trigger=10) for i in range(30)])
    _, out = _run(av, capsys, [])
    assert "AUC, trigger_score  -> approved" in out
    assert "no better than the trigger score" in out


def test_the_weight_proposal_is_labelled_as_a_hypothesis(store, capsys):
    """It is fitted on the same data it would be judged by, and a weight change
    moves what gets CLIPPED. Presenting it as a patch is how a report becomes a
    bad deploy."""
    av, tmp = store
    # Three signals that MOVE with the rating and one that is constant: a
    # constant has no rank order, so it correlates with nothing and must not be
    # counted toward the three needed to propose weights.
    rows = []
    for i in range(60):
        v = (i % 10) + 1
        rows.append(_human(i, v, v * 10, sig={
            "AUDIO_SPIKE":       v / 10,
            "SILENCE_BURST":     (v / 10) * 0.6,
            "EMOTE_HOMOGENEITY": (v / 10) * 0.3,
            "KEYWORD":           0.5,
        }))
    _write(av.HUMAN, rows)
    _, out = _run(av, capsys, [])
    assert "HYPOTHESIS, NOT A PATCH" in out
    assert "simulate_weights" in out


def test_the_csv_carries_both_sources_and_every_signal(store, capsys, tmp_path):
    av, tmp = store
    _write(av.HUMAN, [_human(i, 5, 50) for i in range(30)])
    _write(av.TRAIN, [_outcome(i, "approved", 50) for i in range(40)])
    out_csv = tmp_path / "dump.csv"
    _run(av, capsys, ["--csv", str(out_csv)])
    text = out_csv.read_text()
    header = text.splitlines()[0]
    for sig in av.SIGNALS:
        assert sig in header
    assert "human," in text and "outcome," in text


def test_the_report_never_writes_to_the_store(store, capsys):
    """It reads live production data. A report that can mutate the clip store
    is not a report."""
    av, tmp = store
    (tmp / "clips.json").write_text(json.dumps([{"id": "c1", "virality_score": 50}]))
    _write(av.TRAIN, [_outcome(i, "approved", 50) for i in range(40)])
    before = {p.name: p.read_bytes() for p in tmp.iterdir()}
    _run(av, capsys, [])
    after = {p.name: p.read_bytes() for p in tmp.iterdir()}
    assert before == after, "the report modified the store"


def test_the_current_weights_match_the_formula_that_is_running():
    """The report compares proposals against CURRENT_VIRALITY_WEIGHTS. If that
    table drifts from engine.py the whole comparison is against a formula
    nobody is running."""
    import inspect
    from src.maintenance.analyze_virality import CURRENT_VIRALITY_WEIGHTS
    from src.trigger.engine import TriggerEngine
    src = inspect.getsource(TriggerEngine._compute_virality_score)
    for signal, weight in CURRENT_VIRALITY_WEIGHTS.items():
        if signal in ("KEYWORD", "SENTIMENT"):
            continue          # the formula averages the pair then multiplies
        assert f"* {weight}" in src or f"* {weight} " in src, (
            f"{signal} is {weight} in the report but that number is not in "
            f"_compute_virality_score any more")
    assert "(keyword + sentiment) / 2) * 16" in src, \
        "the keyword/sentiment pairing changed — the report splits it as 8+8"


# ── the capture gap that made section 4 unanswerable ─────────────────────────

def _engine():
    from src.trigger.engine import TriggerEngine
    import inspect
    return TriggerEngine, inspect


def test_the_score_history_now_carries_virality_too():
    """79k viewer clips — the only unprompted human judgement we have at
    volume — were labelled with the TRIGGER score, because that was all the
    history held. No amount of waiting would have made them able to speak to
    the virality formula."""
    TriggerEngine, inspect = _engine()
    src = inspect.getsource(TriggerEngine)
    assert "_compute_virality_score(signals)))" in src, \
        "the history no longer records virality alongside the trigger"


def test_the_readers_survive_entries_written_before_virality_existed():
    """THE DEPLOY TRAP. _score_history is an in-memory deque that outlives a
    code reload, so for one history window after every deploy it holds a mix of
    2-tuples and 3-tuples. Unpacking blindly raises inside the clip pipeline."""
    from collections import deque
    from src.trigger.engine import TriggerEngine

    e = TriggerEngine.__new__(TriggerEngine)
    e._score_history = deque([
        (100.0, 40.0),            # written before the change
        (110.0, 55.0, 61.0),      # written after
        (120.0, 30.0),            # and again
    ])
    assert e.score_at(110.0) == 55.0
    assert e.score_at(100.0) == 40.0
    assert e.score_window(90.0, 130.0) == (55.0, 3), \
        "old entries must still count toward the trigger peak"
    assert e.virality_window(90.0, 130.0) == 61.0, \
        "virality must come only from entries that have it"


def test_virality_window_returns_nothing_rather_than_guessing():
    from collections import deque
    from src.trigger.engine import TriggerEngine
    e = TriggerEngine.__new__(TriggerEngine)
    e._score_history = deque([(100.0, 40.0), (110.0, 55.0)])
    assert e.virality_window(90.0, 130.0) is None, \
        "a window with no virality readings must not invent one"
    e._score_history = deque([(100.0, 40.0, 70.0)])
    assert e.virality_window(200.0, 300.0) is None, "wrong window returned a value"


def test_the_viewer_clip_record_pairs_the_virality_number():
    import inspect
    from src.trigger import viewer_clips
    src = inspect.getsource(viewer_clips)
    assert '"our_virality_peak"' in src
    assert "virality_window(" in src, "the record is not filled from the window"


def test_the_report_reads_the_new_field_when_it_appears(store, capsys):
    av, tmp = store
    _write(av.VIEWER, [{"ts": 1000 + i, "channel": "nova", "clip_id": f"slug{i}",
                        "our_peak": 60, "our_virality_peak": 70 + (i % 5)}
                       for i in range(40)])
    (tmp / "clips.json").write_text(json.dumps(
        [{"id": f"c{i}", "virality_score": 40} for i in range(50)]))
    _, out = _run(av, capsys, [])
    assert "moments a stranger clipped" in out
    assert "our virality averaged 72.0" in out
    assert "+32.0" in out, "the comparison against all captured clips is missing"


def test_until_then_it_says_schema_gap_not_shortage_of_data(store, capsys):
    """"not enough overlap" implied we needed more records. We had 79,175 —
    none of which could ever have worked, because clip_id there is Twitch's
    slug for the VIEWER'S clip and never joins against our store."""
    av, tmp = store
    _write(av.VIEWER, [{"ts": 1000 + i, "channel": "nova", "clip_id": f"slug{i}",
                        "our_peak": 60} for i in range(40)])
    _, out = _run(av, capsys, [])
    assert "schema gap" in out
    assert "not enough overlap" not in out


def test_it_checks_whether_the_humans_agree_with_each_other(store, capsys):
    """The ceiling on every other number. If two people rank the same clip
    differently, no formula can score well against their average, and a flat
    correlation is evidence about the TARGET rather than about the bot."""
    av, tmp = store
    rows = []
    for i in range(60):                      # same clip, two raters, no agreement
        rows.append(_human(i, (i * 7) % 10 + 1, 50, labeler="lab1"))
        rows.append(_human(i, (i * 3) % 10 + 1, 50, labeler="lab2"))
    _write(av.HUMAN, rows)
    _, out = _run(av, capsys, [])
    assert "rater-to-rater agreement" in out
    assert "DO NOT AGREE WITH EACH OTHER" in out


def test_it_does_not_cry_disagreement_when_they_agree(store, capsys):
    av, tmp = store
    rows = []
    for i in range(60):
        v = (i % 10) + 1
        rows.append(_human(i, v, 50, labeler="lab1"))
        rows.append(_human(i, min(10, v + (i % 2)), 50, labeler="lab2"))
    _write(av.HUMAN, rows)
    _, out = _run(av, capsys, [])
    assert "rater-to-rater agreement" in out
    assert "DO NOT AGREE" not in out


def test_it_says_when_there_are_no_doubly_rated_clips(store, capsys):
    av, tmp = store
    _write(av.HUMAN, [_human(i, 5, 50, labeler=f"lab{i}") for i in range(40)])
    _, out = _run(av, capsys, [])
    assert "too few doubly-rated clips" in out
