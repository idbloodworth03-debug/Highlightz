"""
Streamer profile — persists per-channel baselines so the trigger engine
can score relative to *that streamer's normal*, not a global constant.

Stored as JSON in clips/profiles/<channel>.json
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from config.settings import settings

_SIGNAL_KEYS = [
    "CHAT_VELOCITY", "AUDIO_SPIKE", "KEYWORD",
    "SENTIMENT", "VIEWER_SPIKE", "SILENCE_BURST",
]

# ── Learning mechanics (redesigned July 2026: gentle + self-recovering) ─────
#
# The old mechanism had two documented failure modes:
#   DEAD clipping — every reject both raised the threshold (+2) AND cut the
#   weights of every fired signal (floor 0.3). A rejection streak compounded:
#   the bar rose while the channel's maximum achievable score fell, and once
#   max-score dropped below the threshold nothing could EVER fire again —
#   weights only recovered through approvals, which require fires. Permanent.
#   STALE clipping — approvals boosted fired signals toward a 2.5x cap, so the
#   profile locked onto the SHAPE of past keepers and went blind to good
#   moments of a different shape.
#
# Fixes, in order of importance:
#   1. Weight bounds tightened to [0.75, 1.5]: at the 0.75 floor the maximum
#      raw score is ~110 x 0.75 ≈ 82 (≈100 with the multi-signal bonus) —
#      above the 80 threshold ceiling, so death-by-weights is mathematically
#      impossible. The 1.5 cap lets a preferred signal lead but never
#      monopolize, bounding archetype lock-in from the other side.
#   2. Weights mean-revert toward 1.0 a little every hour (decay_weights,
#      called from the worker's hourly maintenance next to threshold decay) —
#      opinions fade unless refreshed by new reviews, so recovery no longer
#      depends on approvals that suppression makes impossible.
#   3. Asymmetric, gentler steps: a reject is weak evidence (users bulk-reject
#      junk — 724 rejects vs 17 approvals in the July log), an approval is
#      rare deliberate evidence and keeps its full step.
_LEARN_RATE_APPROVE = 0.06   # per-approval weight nudge (was 0.08)
_LEARN_RATE_REJECT  = 0.02   # per-reject weight nudge (was 0.08 — 4x gentler)
_WEIGHT_MIN = 0.75           # was 0.3 — old floor allowed permanent death
_WEIGHT_MAX = 1.5            # was 2.5 — old cap allowed archetype lock-in
_WEIGHT_REVERSION = 0.05     # hourly pull toward 1.0 — stale preferences fade in days

# Adaptive trigger-threshold bounds + per-clip step sizes.
# CEIL 80 lets a persistently-rejected channel get genuinely picky (below the
# 85 emergency override). REJECT_STEP lowered 2.0 → 0.75 (July 2026): at +2 a
# routine rejection streak silenced a channel in minutes — ten rejects moved
# the bar +20. At +0.75 the same streak moves it +7.5, and reaching the
# ceiling takes ~27 consecutive rejects: a real verdict, not one bad stretch.
# Approvals keep the full −2 (rare, deliberate, should open the tap).
# Floor 50 (July 2026 analysis): no approved clip has ever scored below 60,
# so approvals must not drag the bar into territory that only makes junk.
# Per-hour pull of the trigger threshold back toward the channel's seed. Kept
# at the worker's historical 10% so behaviour is unchanged for channels that
# WERE decaying; the fix is that it now applies to channels that were not.
_THRESHOLD_REVERSION = 0.10
# Cap on a single catch-up (see decay_elapsed). 30 days of pull lands any
# profile effectively at its seed without an unbounded computation.
_MAX_DECAY_HOURS = 720.0
_THRESHOLD_FLOOR = 50.0
_THRESHOLD_CEIL  = 80.0
_APPROVE_STEP    = 2.0
_REJECT_STEP     = 0.75


@dataclass
class StreamerProfile:
    channel: str
    platform: str = "twitch"

    # ── Chat velocity baseline (rolling exponential average + variance) ──
    avg_velocity: float = 0.0       # messages/sec during normal moments
    var_velocity: float = 0.0       # EMA of squared deviation → rolling variance
    velocity_samples: int = 0

    # ── Sentiment baseline ────────────────────────────────────────────────
    avg_sentiment: float = 0.0      # VADER compound abs value, 0–1
    sentiment_samples: int = 0

    # ── Keyword rate baseline ─────────────────────────────────────────────
    avg_keyword_rate: float = 0.0   # keyword hits / total messages
    keyword_samples: int = 0

    # ── Audio level baseline ──────────────────────────────────────────────
    avg_audio_db: float = -30.0     # streamer's typical dBFS during normal content
    audio_db_samples: int = 0

    # ── Pre-flight research (existing Twitch clips, one-time seed) ────────
    researched: bool = False              # research attempted (even if no clips found)
    research_clips_per_day: float = 0.0   # how often this channel already gets clipped

    # ── Calibration gate ─────────────────────────────────────────────────
    # No clips fire until we've collected this many velocity samples.
    # At 3s fast intervals → 60 samples ≈ 3 minutes of silent observation.
    # Lowered 100 → 60 (July 2026 volume pass): the first clip of a session
    # is where "is this even working?" doubt lives — 3 minutes of baseline is
    # enough for the ratio-gated spike detector, and existing calibrated
    # profiles are unaffected (their sample counts already exceed either bar).
    calibration_target: int = 60

    # ── Adaptive trigger threshold ────────────────────────────────────────
    # Starts at global default; nudges down when clips get approved,
    # nudges up when clips get rejected (stays in [_THRESHOLD_FLOOR, _THRESHOLD_CEIL]).
    trigger_threshold: float = 60.0
    # Wall-clock of the last decay tick. Decay is a function of ELAPSED TIME,
    # not of monitoring uptime — see decay_elapsed. 0.0 = never decayed.
    last_decay_ts: float = 0.0
    # Which rules preset this channel is being scored under, recorded ON the
    # profile rather than looked up by every caller.
    #
    # Decay mean-reverts the threshold toward the preset's seed, so anything
    # that decays has to know the preset. ProfileManager.load does — and it used
    # to hardcode "default", pulling every channel toward 63 while the worker's
    # own hourly decay pulled toward the real preset's seed. The two fought, and
    # since a deploy restarts the service and runs load(), the load side won:
    # "small" channels were dragged back to a big-channel bar every deploy.
    #
    # Passing the preset into load() instead would have fixed only the callers
    # that happen to know it; the reject/approve/undo paths do not. Storing it
    # here means every load gets the right seed with no argument to forget.
    # Old profiles deserialise to "default", which is what they were already
    # being decayed toward, so nothing moves on the first load after upgrade.
    preset: str = "default"

    # ── Per-signal learned weight multipliers (1.0 = unchanged) ──────────
    # Nudged by record_clip() based on which signals fired in each clip.
    signal_weights: dict = field(
        default_factory=lambda: {k: 1.0 for k in _SIGNAL_KEYS}
    )

    # ── Clip history ──────────────────────────────────────────────────────
    total_clips: int = 0
    approved_clips: int = 0
    rejected_clips: int = 0

    # ── Session stats ─────────────────────────────────────────────────────
    total_sessions: int = 0
    total_watch_seconds: float = 0.0

    # ── Timestamps ───────────────────────────────────────────────────────
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # ── Smoothing factor for exponential moving average ───────────────────
    _EMA_ALPHA: float = field(default=0.1, repr=False)

    def update_velocity(self, sample: float) -> None:
        if self.velocity_samples == 0 or self.avg_velocity == 0:
            self.avg_velocity = sample
            self.var_velocity = 0.0
            self.velocity_samples += 1
            return

        # Spike-aware baseline ("influence"): once a real baseline exists, a
        # reading far ABOVE the mean is a hype spike. Without protection, every
        # sample — including the ones during a pop-off — feeds the rolling mean
        # and variance, so a sustained hype run drags "normal" up to meet it and
        # the detector goes quiet mid-moment.
        #
        # We gate the spike by RATIO to the mean (>= _SPIKE_RATIO×) rather than a
        # z-score: the z-score's own denominator (the variance) gets inflated by
        # the first spike, which then disengages the guard and lets the baseline
        # chase the spike — a feedback trap. The ratio test depends only on the
        # held-down mean, so it stays engaged for the whole spike. On a spike we
        # nudge the mean only weakly and LEAVE THE VARIANCE UNTOUCHED, so the
        # spread reflects calm variability (an honest at-rest reference). Quiet
        # readings (below the mean) always update fully — a genuine lull IS normal.
        diff = sample - self.avg_velocity
        is_spike = (self.velocity_samples >= 30
                    and self.avg_velocity > 0
                    and sample >= self.avg_velocity * self._SPIKE_RATIO)
        if is_spike:
            self.avg_velocity += self._EMA_ALPHA * self._SPIKE_INFLUENCE * diff
        else:
            # Incremental EMA mean + variance (Finch 2009): update variance from
            # the pre-update deviation so the two stay consistent.
            self.avg_velocity += self._EMA_ALPHA * diff
            self.var_velocity = (1 - self._EMA_ALPHA) * (self.var_velocity + self._EMA_ALPHA * diff * diff)
        self.velocity_samples += 1

    @property
    def velocity_std(self) -> float:
        return self.var_velocity ** 0.5

    def update_sentiment(self, sample: float) -> None:
        if self.sentiment_samples == 0:
            self.avg_sentiment = sample
        else:
            self.avg_sentiment = (1 - self._EMA_ALPHA) * self.avg_sentiment + self._EMA_ALPHA * sample
        self.sentiment_samples += 1

    def update_keyword_rate(self, sample: float) -> None:
        if self.keyword_samples == 0:
            self.avg_keyword_rate = sample
        else:
            self.avg_keyword_rate = (1 - self._EMA_ALPHA) * self.avg_keyword_rate + self._EMA_ALPHA * sample
        self.keyword_samples += 1

    def update_audio_db(self, sample_db: float) -> None:
        if sample_db <= -100.0:
            return
        if self.audio_db_samples == 0:
            self.avg_audio_db = sample_db
        else:
            self.avg_audio_db = (1 - self._EMA_ALPHA) * self.avg_audio_db + self._EMA_ALPHA * sample_db
        self.audio_db_samples += 1

    def record_clip(self, approved: bool, signals: list[dict] | None = None) -> None:
        self.total_clips += 1
        if approved:
            self.approved_clips += 1
            self.trigger_threshold = max(_THRESHOLD_FLOOR, self.trigger_threshold - _APPROVE_STEP)
        else:
            self.rejected_clips += 1
            self.trigger_threshold = min(_THRESHOLD_CEIL, self.trigger_threshold + _REJECT_STEP)

        # Nudge per-signal weights based on which signals were active.
        # Approved: signals that fired strongly get a weight boost (they predict good clips).
        # Rejected: signals that fired strongly get a small weight cut — rejects are
        # bulk actions and weak evidence, so they nudge at 1/3 the approval rate.
        # Bounds are tight ([0.75, 1.5]) and weights mean-revert hourly, so no
        # streak can silence a channel or lock it onto one clip archetype.
        if signals:
            for sig in signals:
                key = sig.get("type", "")
                value = float(sig.get("value", 0.0))
                if key not in self.signal_weights or value <= 0.25:
                    continue
                if approved:
                    delta = _LEARN_RATE_APPROVE * value
                    self.signal_weights[key] = min(_WEIGHT_MAX, self.signal_weights[key] + delta)
                else:
                    delta = _LEARN_RATE_REJECT * value
                    self.signal_weights[key] = max(_WEIGHT_MIN, self.signal_weights[key] - delta)

    def decay_elapsed(self, seed_threshold: float, now: float | None = None,
                      max_hours: float = _MAX_DECAY_HOURS,
                      assume_last: float | None = None) -> float:
        """Apply however many hours of decay have actually elapsed, and return
        the number of hours applied.

        WHY THIS EXISTS (2026-07-29): decay used to live only inside
        `_profile_update_loop`, which runs `while self._running` — so it ticked
        only while a channel was ACTIVELY MONITORED, and only after an hour of
        continuous uptime. A channel that got pushed to the ceiling and then
        stopped being watched (stream ended, idle reaper, user removed it)
        froze there permanently: nothing fires at 80, so nothing gets approved,
        so nothing ever brings it back down. Eight prod channels were stuck
        that way, one with 225 stored clips and zero that would fire.

        Making decay depend on wall-clock time and applying it at LOAD fixes
        that class of bug outright: a profile recovers whether or not anyone
        was watching, and a stream monitored in short bursts still recovers.

        `max_hours` caps a single catch-up so a profile untouched for months
        does one bounded correction rather than an enormous jump — it lands at
        the seed either way, just predictably.
        """
        now = time.time() if now is None else now
        if self.last_decay_ts <= 0.0:
            # No clock yet (profile predates this field). Rather than starting
            # from `now` — which would strand an already-stuck channel until it
            # happens to be loaded twice, an hour apart — start from when the
            # profile was last WRITTEN, if the caller can tell us. That is a
            # real measure of when the channel was last active: a file untouched
            # for five days means five days of neglect, and the decay it earned
            # is applied immediately.
            self.last_decay_ts = assume_last if assume_last and assume_last > 0 else now
            if self.last_decay_ts >= now:
                return 0.0
        hours = (now - self.last_decay_ts) / 3600.0
        if hours < 1.0:
            return 0.0
        hours = min(hours, max_hours)
        # Compound the same per-hour pull the worker applied, so behaviour is
        # identical whether one tick or fifty are being caught up on.
        retained = (1.0 - _THRESHOLD_REVERSION) ** hours
        self.trigger_threshold = round(
            seed_threshold + (self.trigger_threshold - seed_threshold) * retained, 2)
        w_retained = (1.0 - _WEIGHT_REVERSION) ** hours
        for key, w in self.signal_weights.items():
            self.signal_weights[key] = round(1.0 + (w - 1.0) * w_retained, 4)
        self.last_decay_ts = now
        return hours

    def decay_weights(self, rate: float = _WEIGHT_REVERSION) -> None:
        """Mean-revert learned signal weights toward neutral (1.0).

        Called hourly by the worker next to the threshold decay. This is the
        anti-staleness / anti-death valve: preferences learned from past
        reviews fade over days unless refreshed by new reviews, so a channel
        can never be permanently locked into (or out of) one clip shape by
        history alone."""
        for key, w in self.signal_weights.items():
            self.signal_weights[key] = round(w + rate * (1.0 - w), 4)

    @property
    def is_calibrated(self) -> bool:
        """True once enough baseline samples exist to trust the trigger."""
        return self.velocity_samples >= self.calibration_target

    @property
    def calibration_pct(self) -> float:
        return min(100.0, self.velocity_samples / self.calibration_target * 100)

    @property
    def approval_rate(self) -> float:
        if self.total_clips == 0:
            return 0.0
        return self.approved_clips / self.total_clips

    # Spike = this many rolling standard deviations above the rolling mean.
    _SPIKE_SIGMA = 2.5
    # Baseline protection: a reading at/above this multiple of the current mean is
    # treated as a hype spike and barely moves the baseline (see update_velocity).
    _SPIKE_RATIO = 2.0
    # How much a flagged spike is allowed to move the baseline mean (0 = not at
    # all, 1 = same as a normal reading). Low so sustained hype can't inflate it.
    _SPIKE_INFLUENCE = 0.15

    @property
    def velocity_spike_multiplier(self) -> float:
        """
        How many x above the rolling mean counts as a full spike. Once enough
        samples exist this is derived from the channel's own variance — a spike
        is _SPIKE_SIGMA standard deviations above the rolling mean, expressed as
        a ratio (1 + Nσ/mean). A calm channel (low σ) trips on a smaller relative
        jump; a chaotic channel (high σ) needs a bigger one. Clamped to [1.5, 4.0]
        so near-zero variance can't make it hair-trigger and huge variance can't
        make it un-clippable. Falls back to the old fixed ramp during cold-start.
        """
        if self.velocity_samples < 30:
            return 2.0
        if self.avg_velocity <= 0:
            return max(1.5, 2.5 - (self.velocity_samples / 300))
        sigma_ratio = 1.0 + self._SPIKE_SIGMA * (self.velocity_std / self.avg_velocity)
        return max(1.5, min(sigma_ratio, 4.0))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_EMA_ALPHA", None)
        d["approval_rate"]           = round(self.approval_rate, 3)
        d["velocity_spike_multiplier"] = round(self.velocity_spike_multiplier, 2)
        d["velocity_std"]              = round(self.velocity_std, 3)
        d["is_calibrated"]            = self.is_calibrated
        d["calibration_pct"]          = round(self.calibration_pct, 1)
        # Round signal weights for cleaner display
        d["signal_weights"] = {k: round(v, 3) for k, v in self.signal_weights.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StreamerProfile":
        d.pop("approval_rate", None)
        d.pop("velocity_spike_multiplier", None)
        data = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        # Backfill signal_weights for profiles saved before this feature existed
        if "signal_weights" not in data:
            data["signal_weights"] = {k: 1.0 for k in _SIGNAL_KEYS}
        else:
            # Ensure all keys present (in case new signals were added)
            for k in _SIGNAL_KEYS:
                data["signal_weights"].setdefault(k, 1.0)
            # One-time migration for profiles saved under the old wide bounds
            # ([0.3, 2.5]): clamp into the new safe range so a historically
            # crushed profile is revived on its next load instead of staying
            # dead until enough hourly reversion ticks accumulate.
            data["signal_weights"] = {
                k: min(_WEIGHT_MAX, max(_WEIGHT_MIN, v))
                for k, v in data["signal_weights"].items()
            }
        return cls(**data)
