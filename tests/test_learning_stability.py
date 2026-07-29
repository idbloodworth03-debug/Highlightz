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
        "CHAT_VELOCITY": 38, "AUDIO_SPIKE": 22, "KEYWORD": 5,
        "SENTIMENT": 5, "VIEWER_SPIKE": 19, "SILENCE_BURST": 11,
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
    BETWEEN signals (CHAT_VELOCITY +2, VIEWER_SPIKE +4, KEYWORD +1 /
    SILENCE_BURST -3, AUDIO_SPIKE -2, EMOTE_HOMOGENEITY -2) while holding the total at 110, so
    the average clip scores the same and only the ranking shifts. Any future
    edit that shrinks this pool will quietly reduce clip volume — which is why
    it is pinned here.
    """
    from src.trigger import scoring
    live_only = {"AUDIO_SPIKE": 22, "VIEWER_SPIKE": 19, "SILENCE_BURST": 11}
    pool = sum(scoring.CHAT_WEIGHTS.values()) + sum(live_only.values())
    assert pool == 110, f"weight pool moved to {pool} — clip volume will change"
    # And the chat-only pool that drives the VOD scanner stays ~stable too.
    assert 55 <= sum(scoring.CHAT_WEIGHTS.values()) <= 60


def test_threshold_recovers_by_elapsed_time_not_monitoring_uptime():
    """The dead-channel bug (found 2026-07-29).

    Decay used to live only inside `_profile_update_loop`, which runs
    `while self._running` — so it ticked only while a channel was ACTIVELY
    monitored, and only after an hour of continuous uptime. A channel pushed to
    the 80 ceiling and then left unwatched froze there permanently: nothing
    fires at 80, so nothing gets approved, so nothing pulls it back down. Eight
    production channels were stuck like that, one with 225 stored clips and
    zero that would fire today.

    Decay is now a function of elapsed WALL-CLOCK time and is applied on load,
    so a profile heals whether or not anyone was watching it.
    """
    import time as _t
    p = StreamerProfile(channel="lacy", trigger_threshold=80.0)
    now = _t.time()

    # First sight only starts the clock — it must never jump the threshold.
    assert p.decay_elapsed(60.0, now=now) == 0.0
    assert p.trigger_threshold == 80.0
    assert p.last_decay_ts == now

    # Under an hour: nothing happens (no drift from repeated loads).
    assert p.decay_elapsed(60.0, now=now + 600) == 0.0
    assert p.trigger_threshold == 80.0

    # A day unwatched: recovers most of the way toward the seed on its own.
    p.decay_elapsed(60.0, now=now + 24 * 3600)
    assert 60.0 < p.trigger_threshold < 64.0, p.trigger_threshold

    # A week unwatched: effectively back at the seed — the channel is alive.
    p2 = StreamerProfile(channel="marlon", trigger_threshold=80.0,
                         last_decay_ts=now)
    p2.decay_elapsed(60.0, now=now + 7 * 24 * 3600)
    assert abs(p2.trigger_threshold - 60.0) < 0.5

    # Catch-up is bounded, and it converges to the seed rather than past it.
    p3 = StreamerProfile(channel="old", trigger_threshold=80.0, last_decay_ts=1.0)
    p3.decay_elapsed(60.0, now=now)
    assert 59.5 <= p3.trigger_threshold <= 60.5

    # It pulls UP as well as down — a channel below its seed also converges.
    p4 = StreamerProfile(channel="low", trigger_threshold=50.0, last_decay_ts=now)
    p4.decay_elapsed(60.0, now=now + 24 * 3600)
    assert 50.0 < p4.trigger_threshold <= 60.0

    # Signal weights ride the same clock and revert toward neutral.
    p5 = StreamerProfile(channel="w", last_decay_ts=now)
    p5.signal_weights["AUDIO_SPIKE"] = 1.5
    p5.decay_elapsed(60.0, now=now + 24 * 3600)
    # Weights revert at 5%/hour vs the threshold's 10%, so a day takes 1.5 to
    # ~1.15 — deliberately slower, since a learned preference is worth more
    # than a threshold that has drifted.
    assert 1.0 <= p5.signal_weights["AUDIO_SPIKE"] < 1.2
    p5.decay_elapsed(60.0, now=now + 7 * 24 * 3600)
    assert abs(p5.signal_weights["AUDIO_SPIKE"] - 1.0) < 0.05


def test_decay_survives_a_save_load_round_trip():
    """last_decay_ts must persist, or every load restarts the clock and decay
    never accumulates — the same silent-no-op class as the original bug."""
    import time as _t
    now = _t.time()
    p = StreamerProfile(channel="x", trigger_threshold=80.0, last_decay_ts=now)
    restored = StreamerProfile.from_dict(p.to_dict())
    assert restored.last_decay_ts == now
    restored.decay_elapsed(60.0, now=now + 24 * 3600)
    assert restored.trigger_threshold < 80.0


def test_clockless_profile_recovers_on_first_load_via_mtime():
    """Migration path for the 24 profiles that were already stuck at the
    ceiling when this fix shipped.

    They have no last_decay_ts. Starting their clock at `now` would strand
    them: recovery would need the profile loaded TWICE, an hour apart, which
    for an unmonitored channel may never happen. Seeding from the file's
    mtime — when the channel was last actually active — applies the decay it
    already earned on the very first load.
    """
    import time as _t
    now = _t.time()
    five_days_ago = now - 5 * 24 * 3600

    stuck = StreamerProfile(channel="lacy", trigger_threshold=80.0)
    hrs = stuck.decay_elapsed(63.0, now=now, assume_last=five_days_ago)
    assert hrs > 100
    assert abs(stuck.trigger_threshold - 63.0) < 0.5, stuck.trigger_threshold

    # A recently-written profile has an mtime near now, so it earns almost
    # nothing — an active channel keeps the strictness it just learned.
    fresh = StreamerProfile(channel="active", trigger_threshold=80.0)
    fresh.decay_elapsed(63.0, now=now, assume_last=now - 300)
    assert fresh.trigger_threshold == 80.0

    # A future/garbage mtime must never inflate the threshold or run backwards.
    weird = StreamerProfile(channel="skew", trigger_threshold=80.0)
    assert weird.decay_elapsed(63.0, now=now, assume_last=now + 9999) == 0.0
    assert weird.trigger_threshold == 80.0

    # With no hint at all, behaviour is the safe original: start the clock.
    noinfo = StreamerProfile(channel="n", trigger_threshold=80.0)
    assert noinfo.decay_elapsed(63.0, now=now) == 0.0
    assert noinfo.trigger_threshold == 80.0
