"""The five sound effects, as files the render can mix in.

The browser synthesizes these in WebAudio; the server had nothing, so every
automatic edit went out with only the clip's own audio on it. These pin the
synthesis maths and the "a missing effect costs a whoosh, not the post" rule.
"""

import re

import pytest

from src.autopilot import sfx as S
from src.autopilot import plan as P


def test_the_names_match_the_plan_and_the_browser():
    assert set(S.KINDS) == set(P.SFX_KINDS)


def test_the_chirp_phase_is_the_integral_of_the_frequency():
    """sin(2*PI*f(t)*t) is the classic mistake — it sweeps at twice the rate
    and lands an octave out. The phase has to be the integral: f0*t plus
    (f1-f0)*t^2/2T."""
    e = S._chirp(140, 38, 0.28)
    k = (38 - 140) / (2 * 0.28)
    assert "140.0000*t" in e
    assert f"{k:.4f}*t*t" in e
    assert e.startswith("sin(2*PI*(")


def test_a_falling_sweep_has_a_negative_second_term():
    assert "+-" in S._chirp(140, 38, 0.28) or "-" in S._chirp(140, 38, 0.28)
    rising = S._chirp(300, 3200, 0.3)
    assert re.search(r"\+\d", rising), "a rising sweep should climb"


@pytest.mark.parametrize("kind", S.KINDS)
def test_every_effect_has_a_recipe_and_a_duration(kind):
    chain, dur = S._recipe(kind)
    assert chain and 0.05 < dur < 2.0
    assert "volume=" in chain, f"{kind} has no level set"


@pytest.mark.parametrize("kind", S.KINDS)
def test_every_command_writes_one_mono_wav_at_the_renders_rate(kind):
    """amix resamples anything that does not match, which is work done per
    clip for no reason."""
    cmd = S.command(kind)
    assert cmd[0] == "ffmpeg" and "-y" in cmd
    assert cmd[cmd.index("-ar") + 1] == str(S.RATE) == "48000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[-1].endswith(f"{kind}.wav")


def test_the_tonal_effects_carry_the_browsers_own_frequencies():
    """ding is two partials at 1320 and 2640; hit falls 140 to 38; pop falls
    760 to 320. If these drift, a clip previewed in the editor sounds
    different once the server makes it."""
    assert "1320" in S._recipe("ding")[0] and "2640" in S._recipe("ding")[0]
    assert "140.0000*t" in S._recipe("hit")[0]
    assert "760.0000*t" in S._recipe("pop")[0]


def test_the_riser_stops_dead_so_the_cut_lands_on_silence():
    chain, dur = S._recipe("riser")
    assert "afade=t=out:st=0.8:d=0.05" in chain


def test_the_noisy_effects_are_band_limited():
    for kind in ("whoosh", "riser"):
        chain, _ = S._recipe(kind)
        assert "highpass=" in chain and "lowpass=" in chain


def test_the_files_live_outside_the_repo():
    """Generated, not shipped: deterministic, a few KB, and a binary in the
    repo is a binary somebody has to trust."""
    assert "sfx" in str(S.root())
    assert "src/autopilot" not in str(S.root())


def test_a_cue_with_no_file_is_dropped_rather_than_failing_the_render():
    from src.autopilot import graph as G
    p = P.EditPlan(segments=[P.Segment(src="/tmp/a.mp4", start=0, end=20)],
                   sfx=[P.Sfx(at=0.0, kind="riser"), P.Sfx(at=5.0, kind="ding")])
    args = G.build_command(p, "/tmp/o.mp4", {"ding": "/x/ding.wav"})
    g = args[args.index("-filter_complex") + 1]
    assert g.count("adelay") == 1 and "amix=inputs=2" in g
