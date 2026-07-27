"""
Regression tests for the two documented learning failure modes:

DEAD clipping — a rejection streak used to raise the threshold (+2 each) AND
crush signal weights (floor 0.3) until the channel's maximum achievable score
fell below the threshold, permanently: weights only recovered via approvals,
which require fires. STALE clipping — approvals grew weights toward 2.5x,
locking the profile onto the shape of past keepers.

The redesigned mechanics must guarantee: no streak can make firing impossible,
no streak can monopolize the score, and history fades without new reviews.
"""

from src.profiles.profile import (
    StreamerProfile, _WEIGHT_MIN, _WEIGHT_MAX, _THRESHOLD_CEIL,
)

# Live base weights (engine._compute_score): velocity 36, audio 24, keyword 4,
# sentiment 5, viewer 15, silence 14, homogeneity 12 → pool ≈ 110. Homogeneity
# has no learned weight entry, so the learnable pool is 98 of it.
_LEARNABLE_POOL = 36 + 24 + 4 + 5 + 15 + 14
_FIXED_POOL     = 12

_ALL_FIRED = [{"type": k, "value": 0.9} for k in
              ("CHAT_VELOCITY", "AUDIO_SPIKE", "KEYWORD",
               "SENTIMENT", "VIEWER_SPIKE", "SILENCE_BURST")]


def test_rejection_streak_can_never_make_firing_impossible():
    # 500 straight rejections of full-signal clips — the worst case that used
    # to permanently kill a channel.
    p = StreamerProfile(channel="x", trigger_threshold=60.0)
    for _ in range(500):
        p.record_clip(approved=False, signals=_ALL_FIRED)

    assert p.trigger_threshold == _THRESHOLD_CEIL          # picky, not broken
    assert all(w >= _WEIGHT_MIN for w in p.signal_weights.values())
    # A maximal moment (every signal at 1.0) must still clear the ceiling —
    # even BEFORE the 1.25 multi-signal bonus.
    max_raw = sum(p.signal_weights[k] * base for k, base in {
        "CHAT_VELOCITY": 36, "AUDIO_SPIKE": 22, "KEYWORD": 6,
        "SENTIMENT": 5, "VIEWER_SPIKE": 20, "SILENCE_BURST": 10,
    }.items()) + _FIXED_POOL
    assert max_raw > _THRESHOLD_CEIL


def test_rejects_nudge_gently_and_asymmetrically():
    p = StreamerProfile(channel="x", trigger_threshold=60.0)
    for _ in range(10):
        p.record_clip(approved=False)
    # Ten rejects used to move the bar +20 (dead in one bad stretch); now +7.5.
    assert p.trigger_threshold == 60.0 + 10 * 0.75
    # One approval undoes more than two rejects (rare evidence speaks louder).
    p.record_clip(approved=True)
    assert p.trigger_threshold == 60.0 + 10 * 0.75 - 2.0


def test_approval_streak_cannot_monopolize_the_score():
    p = StreamerProfile(channel="x")
    for _ in range(500):
        p.record_clip(approved=True, signals=[{"type": "AUDIO_SPIKE", "value": 1.0}])
    assert p.signal_weights["AUDIO_SPIKE"] == _WEIGHT_MAX   # capped at 1.5, not 2.5
    # Other signals were untouched — a differently-shaped good moment keeps
    # its full base contribution.
    assert p.signal_weights["CHAT_VELOCITY"] == 1.0


def test_weights_mean_revert_toward_neutral():
    p = StreamerProfile(channel="x")
    p.signal_weights["AUDIO_SPIKE"] = _WEIGHT_MAX
    p.signal_weights["KEYWORD"] = _WEIGHT_MIN
    for _ in range(48):                      # ~2 days of hourly ticks
        p.decay_weights()
    assert abs(p.signal_weights["AUDIO_SPIKE"] - 1.0) < 0.05
    assert abs(p.signal_weights["KEYWORD"] - 1.0) < 0.05


def test_legacy_crushed_profile_is_revived_on_load():
    # Profiles saved under the old bounds may carry weights at 0.3 (dead) or
    # 2.5 (locked-in); loading clamps them into the safe range immediately.
    p = StreamerProfile.from_dict({
        "channel": "x",
        "signal_weights": {"CHAT_VELOCITY": 0.3, "AUDIO_SPIKE": 2.5, "KEYWORD": 1.0},
    })
    assert p.signal_weights["CHAT_VELOCITY"] == _WEIGHT_MIN
    assert p.signal_weights["AUDIO_SPIKE"] == _WEIGHT_MAX
    assert p.signal_weights["KEYWORD"] == 1.0


def test_weight_pool_is_preserved_at_110():
    """Volume guard for the July-2026 human-calibration retune.

    Clip volume tracks the SIZE of the weight pool, not its distribution: a
    smaller pool means lower scores against unchanged thresholds, i.e. fewer
    clips. The retune leaned the formula toward human ratings by moving weight
    BETWEEN signals (VIEWER_SPIKE +5, KEYWORD +2 / SILENCE_BURST -4,
    AUDIO_SPIKE -2, EMOTE_HOMOGENEITY -1) while holding the total at 110, so
    the average clip scores the same and only the ranking shifts. Any future
    edit that shrinks this pool will quietly reduce clip volume — which is why
    it is pinned here.
    """
    from src.trigger import scoring
    live_only = {"AUDIO_SPIKE": 22, "VIEWER_SPIKE": 20, "SILENCE_BURST": 10}
    pool = sum(scoring.CHAT_WEIGHTS.values()) + sum(live_only.values())
    assert pool == 110, f"weight pool moved to {pool} — clip volume will change"
    # And the chat-only pool that drives the VOD scanner stays ~stable too.
    assert 55 <= sum(scoring.CHAT_WEIGHTS.values()) <= 60
