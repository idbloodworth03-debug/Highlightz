"""
Tests for the v2 trigger improvements:
  - velocity_score acceleration + length-collapse boosts (monotonic, never lower)
  - StreamerProfile rolling std-dev → N-sigma spike multiplier
  - ChatMetrics derivative + message-length signals
  - _monitor_and_fire window timing (MIN_WAIT) + double-peak adoption
"""

import asyncio
import pytest
from unittest.mock import patch

from src.trigger import scoring
from src.trigger.engine import TriggerEngine
from src.trigger.rules import ChannelRules, CHANNEL_OVERRIDES
from src.profiles.profile import StreamerProfile
from src.chat.metrics import ChatMetrics


@pytest.fixture(autouse=True)
def clear_overrides():
    CHANNEL_OVERRIDES.clear()
    yield
    CHANNEL_OVERRIDES.clear()


# ── scoring: boosts can only raise, never lower ────────────────────────────────

def _base(**kw):
    # spike_ratio/multiplier = 0.5 base → leaves headroom for the boosts to show.
    args = dict(spike_ratio=1.0, spike_multiplier=2.0, velocity=5.0,
                weighted_velocity=5.0, unique_senders=0)
    args.update(kw)
    return scoring.velocity_score(**args)


def test_acceleration_boost_raises_score():
    base = _base(acceleration=1.0)
    boosted = _base(acceleration=2.0)
    assert boosted > base
    assert boosted <= 1.0


def test_length_collapse_boost_raises_score():
    base = _base(avg_message_length=40.0)         # long messages → no boost
    boosted = _base(avg_message_length=4.0)       # frantic short bursts → boost
    assert boosted > base
    assert boosted <= 1.0


def test_neutral_inputs_match_legacy_behavior():
    # accel=1.0 and no length info → identical to the pre-v2 two-boost score.
    assert _base(acceleration=1.0, avg_message_length=None) == _base()


def test_boosts_never_exceed_one():
    assert _base(spike_ratio=10.0, acceleration=5.0, avg_message_length=1.0,
                 unique_senders=50) <= 1.0


# ── profile: rolling std-dev multiplier ────────────────────────────────────────

def test_low_variance_gives_tighter_multiplier_than_high_variance():
    calm = StreamerProfile(channel="calm")
    for _ in range(60):
        calm.update_velocity(5.0)        # perfectly steady → ~0 variance
    chaotic = StreamerProfile(channel="chaotic")
    for i in range(60):
        chaotic.update_velocity(2.0 if i % 2 == 0 else 18.0)  # swings wildly

    assert calm.velocity_std < chaotic.velocity_std
    # A calm channel trips on a smaller relative jump than a chaotic one.
    assert calm.velocity_spike_multiplier < chaotic.velocity_spike_multiplier


def test_multiplier_is_clamped():
    chaotic = StreamerProfile(channel="x")
    for i in range(80):
        chaotic.update_velocity(0.1 if i % 2 == 0 else 100.0)
    assert 1.5 <= chaotic.velocity_spike_multiplier <= 4.0


def test_cold_start_uses_fixed_multiplier():
    p = StreamerProfile(channel="new")
    for _ in range(10):
        p.update_velocity(5.0)
    assert p.velocity_spike_multiplier == 2.0   # <30 samples → legacy ramp


def test_variance_survives_dict_roundtrip():
    p = StreamerProfile(channel="rt")
    for v in (3.0, 9.0, 4.0, 11.0, 5.0):
        p.update_velocity(v)
    restored = StreamerProfile.from_dict(p.to_dict())
    assert restored.var_velocity == p.var_velocity


def test_legacy_profile_without_variance_defaults_zero():
    restored = StreamerProfile.from_dict({"channel": "old", "avg_velocity": 5.0,
                                          "velocity_samples": 100})
    assert restored.var_velocity == 0.0
    # No variance recorded → falls back to the legacy ramp, no crash.
    assert restored.velocity_spike_multiplier >= 1.5


# ── profile: spike-aware baseline (z-score influence) ──────────────────────────

def test_sustained_spike_does_not_drag_baseline_to_hype_level():
    # Calm baseline ~5, then a long pop-off at 50 (10x). The spike-aware update
    # must keep the baseline an honest at-rest reference — nowhere near 50 — so
    # the moment still reads as a clear spike for its whole duration.
    p = StreamerProfile(channel="x")
    for _ in range(60):
        p.update_velocity(5.0)
    for _ in range(20):
        p.update_velocity(50.0)        # sustained hype
    assert p.avg_velocity < 30.0       # did NOT climb to the hype level
    # 50 still sits well above the (held-down) baseline → still reads as a spike.
    assert 50.0 >= p.avg_velocity * p._SPIKE_RATIO


def test_spike_aware_baseline_stays_below_naive_ema():
    # Same input, but a plain EMA (full influence) would chase the spike up.
    # The spike-aware baseline must end up substantially lower.
    p = StreamerProfile(channel="x")
    for _ in range(60):
        p.update_velocity(5.0)
    for _ in range(20):
        p.update_velocity(50.0)
    naive = 5.0
    for _ in range(20):
        naive += p._EMA_ALPHA * (50.0 - naive)   # plain EMA over the same burst
    assert p.avg_velocity < naive * 0.6


def test_quiet_readings_below_mean_still_update_fully():
    # The clamp is upside-only: a genuine lull (below the mean) IS normal and
    # must still pull the baseline down at the full rate.
    p = StreamerProfile(channel="x")
    for _ in range(60):
        p.update_velocity(10.0)
    before = p.avg_velocity
    for _ in range(20):
        p.update_velocity(2.0)
    assert p.avg_velocity < before - 1.0   # baseline dropped meaningfully


# ── metrics: derivative + length ───────────────────────────────────────────────

def test_acceleration_neutral_on_thin_data():
    m = ChatMetrics()
    assert m.velocity_acceleration() == 1.0


def test_avg_message_length_computed():
    m = ChatMetrics()
    for msg in ("hello there", "hi", "yo"):
        m.ingest(msg, author="u")
    snap = m.snapshot()
    assert snap.avg_message_length > 0
    assert snap.velocity_acceleration > 0


# ── monitor: fire at the top of the trigger ────────────────────────────────────

async def _run_monitor(initial_peak, scripted_scores):
    """Drive _monitor_and_fire with asyncio.sleep stubbed; each 'sleep' advances
    _last_score to the next scripted value. Returns the fired TriggerEvent."""
    fired = []

    async def cap(ev):
        fired.append(ev)

    engine = TriggerEngine("dp", on_trigger=cap)
    engine._running = True
    scores = iter(scripted_scores)

    async def fake_sleep(_):
        try:
            engine._last_score = next(scores)
        except StopIteration:
            engine._running = False   # end the loop if the script runs out

    with patch("src.trigger.engine.asyncio.sleep", side_effect=fake_sleep):
        await engine._monitor_and_fire(initial_peak, [], ChannelRules(), 0)
    return fired


@pytest.mark.asyncio
async def test_crest_adopts_higher_peak_during_settle():
    # During the short settle the score is still climbing to 95; the fired clip
    # should carry the adopted crest, not the threshold-crossing value.
    fired = await _run_monitor(80.0, [95.0, 95.0, 95.0])
    assert fired
    assert fired[0].score == 95.0


@pytest.mark.asyncio
async def test_fires_at_top_not_on_decay():
    # The whole point: clip at the top of the trigger. Even though the score
    # collapses right after the peak, we fire at the original peak after only the
    # brief settle — we never wait for / fire on the downfall.
    fired = await _run_monitor(80.0, [5.0, 5.0, 5.0])
    assert fired
    assert fired[0].score == 80.0
