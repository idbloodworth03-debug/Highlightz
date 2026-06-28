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

_LEARN_RATE = 0.08   # per-clip nudge size
_WEIGHT_MIN = 0.3
_WEIGHT_MAX = 2.5


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
    # At 3s fast intervals → 100 samples = 5 minutes of silent observation.
    calibration_target: int = 100

    # ── Adaptive trigger threshold ────────────────────────────────────────
    # Starts at global default; nudges down when clips get approved,
    # nudges up when clips get rejected (stays in [30, 90])
    trigger_threshold: float = 60.0

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
            self.trigger_threshold = max(30, self.trigger_threshold - 2)
        else:
            self.rejected_clips += 1
            self.trigger_threshold = min(65, self.trigger_threshold + 1)

        # Nudge per-signal weights based on which signals were active.
        # Approved: signals that fired strongly get a weight boost (they predict good clips).
        # Rejected: signals that fired strongly get a weight cut (they caused false positives).
        if signals:
            for sig in signals:
                key = sig.get("type", "")
                value = float(sig.get("value", 0.0))
                if key not in self.signal_weights or value <= 0.25:
                    continue
                delta = _LEARN_RATE * value
                if approved:
                    self.signal_weights[key] = min(_WEIGHT_MAX, self.signal_weights[key] + delta)
                else:
                    self.signal_weights[key] = max(_WEIGHT_MIN, self.signal_weights[key] - delta)

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
        return cls(**data)
