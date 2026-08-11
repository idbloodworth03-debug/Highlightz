"""The VOD scanner's audio pass.

WHY IT EXISTS. A scan scored chat and nothing else, so AUDIO_SPIKE — the
heaviest non-chat signal the live engine has, 22 of its 110 points — never
contributed. A moment where the streamer screamed over calm chat scored well
live and was invisible to a scan of the same stream. Run side by side, the live
bot beat a scan of its own VOD, and this was most of the quality half of that.

TWO THINGS THESE TESTS PROTECT ABOVE ALL:

  * A FAILED DECODE MUST NOT COST THE USER MOMENTS. Missing streamlink, a
    subscriber-only VOD, a network stall — every one of them has to fall back to
    exactly the chat-only behaviour that shipped before, bar included. If the
    threshold claimed the audio pool while no audio arrived, a missing binary
    would silently halve someone's results.
  * VOD AND LIVE MUST AGREE ON WHAT LOUD MEANS. The baseline, peak decay and
    spike range are replayed from the live engine rather than reinvented, so a
    moment scores the same whether it was caught live or found afterwards.
"""

import asyncio

import pytest

from src.vod import analyzer, vod_audio


# ── the threshold moves with the pool, in both directions ────────────────────

def test_the_bar_rises_when_audio_is_actually_scored():
    """Audio lifts every loud moment's score. If the bar stayed where a
    chat-only scan set it, the scan would trigger on everything and the extra
    signal would buy noise instead of accuracy."""
    assert analyzer._threshold_scale(True) > analyzer._threshold_scale(False)


def test_the_chat_only_bar_is_untouched():
    """0.62 is the calibrated, shipped value. Re-deriving it from the weight
    table lands on 0.629, which would quietly raise the bar on every chat-only
    scan — a change nobody asked for, in the wrong direction."""
    assert analyzer._threshold_scale(False) == 0.62


def test_both_scales_match_the_documented_anchor():
    """scale = 0.50 x (pool x multi-signal bonus) / 57.6. Checked to 2dp so the
    constants cannot drift away from the derivation that justifies them."""
    from src.trigger import scoring
    chat_pool = sum(scoring.CHAT_WEIGHTS.values())
    for with_audio, scale in ((False, 0.62), (True, 0.87)):
        pool = chat_pool + (analyzer.VOD_AUDIO_WEIGHT if with_audio else 0)
        anchor = 0.50 * (pool * scoring.MULTI_SIGNAL_BONUS) / 57.6
        assert abs(anchor - scale) < 0.01, (with_audio, anchor, scale)


def test_the_audio_weight_matches_the_live_engine():
    """Mirrored, not imported — the live table is built inside evaluate() with
    per-profile multipliers applied. If the live weight is retuned this fails."""
    import inspect
    from src.trigger import engine
    src = inspect.getsource(engine.TriggerEngine)
    assert f"SignalType.AUDIO_SPIKE:      {analyzer.VOD_AUDIO_WEIGHT}," in src, (
        "live AUDIO_SPIKE weight changed; update VOD_AUDIO_WEIGHT to match")


# ── scoring with audio present ───────────────────────────────────────────────

def _quiet_window(n=40):
    return [{"text": "just talking here", "author": f"u{i % 12}"} for i in range(n)]


def test_a_loud_moment_scores_higher_than_the_same_chat_without_audio():
    """The entire point: a scream over moderate chat has to beat the same chat
    with no scream."""
    from src.trigger.rules import get_rules
    rules = get_rules("c", "balanced")
    w = _quiet_window()
    silent, _ = analyzer._score_window(w, 400, 300.0, 15.0, 0, rules, audio_score=0.0)
    loud,   _ = analyzer._score_window(w, 400, 300.0, 15.0, 0, rules, audio_score=1.0)
    assert loud > silent, (loud, silent)


def test_audio_appears_in_the_breakdown_only_when_it_was_scored():
    """The breakdown is shown to the user. Reporting AUDIO_SPIKE: 0.0 on a scan
    that never decoded audio would claim a signal that was not consulted."""
    from src.trigger.rules import get_rules
    rules = get_rules("c", "balanced")
    _, with_bd = analyzer._score_window(_quiet_window(), 400, 300.0, 15.0, 0,
                                        rules, audio_score=0.4)
    _, without = analyzer._score_window(_quiet_window(), 400, 300.0, 15.0, 0, rules)
    assert "AUDIO_SPIKE" in with_bd
    assert "AUDIO_SPIKE" not in without


def test_chat_only_scoring_is_byte_identical_to_before_the_audio_pass():
    """Passing no audio must leave the old path exactly as it was — same score,
    same breakdown keys."""
    from src.trigger.rules import get_rules
    rules = get_rules("c", "balanced")
    a, bd_a = analyzer._score_window(_quiet_window(), 400, 300.0, 15.0, 0, rules)
    b, bd_b = analyzer._score_window(_quiet_window(), 400, 300.0, 15.0, 0, rules,
                                     audio_score=None)
    assert a == b
    assert set(bd_a) == set(bd_b) == {
        "CHAT_VELOCITY", "KEYWORD", "SENTIMENT", "EMOTE_HOMOGENEITY"}


# ── the dB -> score replay ───────────────────────────────────────────────────

def test_a_sustained_level_never_reads_as_a_spike():
    """A streamer who is simply loud all stream is not spiking. If constant
    volume scored, the scanner would fire continuously on shouty channels."""
    steady = {s: -20.0 for s in range(300)}
    scores = vod_audio.score_timeline(steady)
    assert max(scores.values()) < 0.05, max(scores.values())


def test_a_burst_above_the_baseline_scores():
    quiet = {s: -35.0 for s in range(200)}
    quiet.update({s: -12.0 for s in range(200, 210)})     # ~23 dB over baseline
    scores = vod_audio.score_timeline(quiet)
    assert max(scores[s] for s in range(200, 210)) > 0.8
    assert scores[150] < 0.05, "the quiet stretch before it should not score"


def test_the_warmup_suppresses_the_opening_seconds():
    """Mirrors the live engine: before the baseline settles, nothing spikes.
    Without it every scan would fire in its first few seconds."""
    loud_from_zero = {s: -5.0 for s in range(120)}
    scores = vod_audio.score_timeline(loud_from_zero)
    assert all(scores[s] == 0.0 for s in range(vod_audio._WARMUP))


def test_silence_scores_zero_rather_than_spiking():
    """_rms_db returns exactly -100 for true silence. Treating that as a huge
    delta from the baseline would make every silent gap a highlight."""
    tl = {s: -35.0 for s in range(120)}
    tl.update({s: vod_audio._SILENCE_DB for s in range(120, 140)})
    scores = vod_audio.score_timeline(tl)
    assert all(scores[s] == 0.0 for s in range(120, 140))


def test_scoring_is_causal_so_a_late_moment_cannot_rescore_an_early_one():
    """Deliberately not a global normalisation: the live engine cannot see the
    future, so neither may a scan, or the same moment would score differently
    depending on what happened hours later."""
    base = {s: -30.0 for s in range(400)}
    early = vod_audio.score_timeline(dict(base))
    with_late_bang = dict(base)
    with_late_bang.update({s: 0.0 for s in range(380, 390)})
    later = vod_audio.score_timeline(with_late_bang)
    assert [early[s] for s in range(300)] == [later[s] for s in range(300)]


def test_an_empty_timeline_is_handled():
    assert vod_audio.score_timeline({}) == {}


# ── failing soft ─────────────────────────────────────────────────────────────

def test_a_missing_binary_yields_no_audio_rather_than_an_exception(monkeypatch):
    """A box without streamlink must still complete chat-only scans."""
    async def _boom(*a, **k):
        raise FileNotFoundError("streamlink")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        vod_audio.extract_db_timeline("https://twitch.tv/videos/1"))
    assert out == {}


def test_no_audio_means_the_chat_only_bar_is_used(monkeypatch):
    """THE DANGEROUS CASE. If the scan claimed the audio pool while the decode
    returned nothing, the bar would sit 40% higher over chat-only scores and a
    missing binary would silently gut everyone's results."""
    from src.trigger.rules import get_rules
    rules = get_rules("c", "balanced")

    class _P:
        trigger_threshold = 60.0
        velocity_spike_multiplier = 2.0
        velocity_samples = 100

    no_audio, _ = analyzer._vod_threshold(_P(), rules, with_audio=False)
    audio, _    = analyzer._vod_threshold(_P(), rules, with_audio=True)
    assert no_audio == 60.0 * 0.62
    assert audio > no_audio


def test_the_scan_decides_with_audio_from_what_arrived_not_from_the_flag():
    """`with_audio` must be derived from the decoded timeline, not from
    settings.vod_audio_enabled — the flag says we tried, not that it worked."""
    import inspect
    src = inspect.getsource(analyzer.run_vod_analysis)
    assert "with_audio = bool(audio_scores)" in src


def test_the_pass_is_off_by_default():
    """It turns a seconds-long scan into a minutes-long one and pulls the whole
    audio track, so a box opts in rather than inheriting it."""
    from config.settings import Settings
    assert Settings().vod_audio_enabled is False


# ── the "is it hung?" surface ────────────────────────────────────────────────

def test_the_scan_reports_a_phase_for_every_stage():
    """The screen labels the stage it is in. A stage that reports no phase falls
    back to "Scoring moments…", which during a multi-minute audio decode tells
    the user the wrong thing is slow."""
    import inspect
    src = inspect.getsource(analyzer.run_vod_analysis)
    for phase in ('"phase": "fetch"', '"phase": "audio"', '"phase": "score"'):
        assert phase in src, f"no progress report carries {phase}"


def test_the_progress_bar_animates_independently_of_the_percentage():
    """The audio decode reports once per 30s of decoded audio, so the percentage
    genuinely sits still for long stretches. A bar that only moves with progress
    is indistinguishable from a hung job."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "rd-track working" in html, "the sweep class is never applied"
    assert "@keyframes rdScan" in html
    assert "prefers-reduced-motion" in html


def test_elapsed_time_is_driven_by_the_browser_not_the_server():
    """The decisive signal. Sweep and percentage both come from the server, so
    if the job or socket died they would freeze together and look exactly like a
    slow scan. A locally-ticked counter keeps moving only while the page is
    alive."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    i = html.index("function ScanActivity")
    block = html[i:i + 1200]
    assert "setInterval" in block and "Date.now()" in block


def test_elapsed_time_survives_reopening_the_tab_mid_scan():
    """created_at (the server's start) must win over the client stamp, or
    reconnecting halfway through a 6-minute scan restarts the clock at 0s and
    hides how long it has really been going."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "job.created_at ? job.created_at * 1000" in html
