"""
Trigger engine: aggregates signals from chat, audio, and NLP,
computes a composite score (0-100), and fires a TriggerEvent when the
score crosses the channel's threshold.

All signals are scored *relative to the streamer's profile baseline*,
so a normally-quiet chess channel triggers at the same sensitivity
as a naturally-loud FPS channel.
"""

import asyncio
import time
import structlog
from typing import Callable, Awaitable

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from config.settings import settings
from .signals import Signal, SignalType, TriggerEvent
from .rules import get_rules
from src.chat.metrics import ChatMetrics, ChatSnapshot

log = structlog.get_logger(__name__)

OnTrigger = Callable[[TriggerEvent], Awaitable[None]]
OnScore = Callable[[str, float, dict], Awaitable[None]]


class TriggerEngine:
    def __init__(
        self,
        channel: str,
        on_trigger: OnTrigger,
        on_score: OnScore | None = None,
        profile=None,
        buffer=None,    # VideoBuffer | None
    ) -> None:
        self.channel = channel
        self.on_trigger = on_trigger
        self.on_score = on_score
        self.profile = profile
        self.buffer = buffer
        _init_rules = get_rules(channel)
        self._metrics = ChatMetrics(extra_keywords=_init_rules.extra_keywords)
        self._vader = SentimentIntensityAnalyzer()
        self._last_trigger: float = 0.0
        self._running = False
        self._audio_baseline_db: float = -30.0
        self._audio_samples: int = 0
        self._sub_raid_active: bool = False
        self._sub_raid_time: float = 0.0
        self._viewer_current: float = 0.0
        self._viewer_baseline: float = 0.0
        self._viewer_samples: int = 0

    # ── Public API ────────────────────────────────────────────────────────

    def ingest_chat(self, author: str, message: str) -> None:
        self._metrics.ingest(message)

    def notify_sub_raid(self) -> None:
        """Call when a sub, gifted sub, or raid event fires."""
        self._sub_raid_active = True
        self._sub_raid_time = time.time()

    def update_viewer_count(self, count: int) -> None:
        """Update viewer count. Tracks raw current value and slow EMA baseline separately."""
        self._viewer_current = float(count)
        if self._viewer_samples == 0:
            self._viewer_baseline = float(count)
        else:
            self._viewer_baseline = 0.95 * self._viewer_baseline + 0.05 * count
        self._viewer_samples += 1

    async def evaluate(self) -> None:
        rules = get_rules(self.channel)
        now = time.time()

        # Use profile's adaptive threshold if available
        threshold = self.profile.trigger_threshold if self.profile else rules.trigger_threshold

        if now - self._last_trigger < rules.cooldown_seconds:
            return

        snapshot = self._metrics.snapshot()
        signals = self._build_signals(snapshot, rules)
        score = self._compute_score(signals)

        log.debug("trigger_score", channel=self.channel, score=round(score, 1))

        if self.on_score:
            breakdown = {str(s.type).split(".")[-1]: round(s.value, 3) for s in signals}
            await self.on_score(self.channel, round(score, 1), breakdown)

        if score >= threshold:
            self._last_trigger = now
            event = TriggerEvent(
                channel=self.channel,
                score=score,
                signals=signals,
                pre_roll=rules.pre_roll,
                post_roll=rules.post_roll,
            )
            log.info("trigger_fired", channel=self.channel, score=round(score, 1),
                     threshold=round(threshold, 1))
            await self.on_trigger(event)

    async def run_evaluation_loop(self, interval: float = 1.0) -> None:
        self._running = True
        while self._running:
            await self.evaluate()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    # ── Internal ─────────────────────────────────────────────────────────

    def _build_signals(self, snapshot: ChatSnapshot, rules) -> list[Signal]:
        signals: list[Signal] = []

        # ── Chat velocity (0-1) ───────────────────────────────────────────
        current_velocity = snapshot.velocity
        if self.profile and self.profile.avg_velocity > 0 and self.profile.velocity_samples >= 10:
            spike_ratio = current_velocity / self.profile.avg_velocity
            spike_multiplier = self.profile.velocity_spike_multiplier
        else:
            spike_ratio = self._metrics.velocity_spike_ratio()
            spike_multiplier = rules.velocity_multiplier

        velocity_score = min(spike_ratio / spike_multiplier, 1.0)
        signals.append(Signal(
            type=SignalType.CHAT_VELOCITY,
            value=velocity_score,
            channel=self.channel,
            metadata={
                "velocity": round(current_velocity, 2),
                "spike_ratio": round(spike_ratio, 2),
                "baseline": round(self.profile.avg_velocity, 2) if self.profile else 0,
            },
        ))

        # ── Audio loudness (0-1) ──────────────────────────────────────────
        current_db = -100.0
        if self.buffer is not None:
            current_db = self.buffer.get_audio_level_db()
            if current_db > -100:
                if self._audio_samples == 0:
                    self._audio_baseline_db = current_db
                else:
                    self._audio_baseline_db = 0.9 * self._audio_baseline_db + 0.1 * current_db
                self._audio_samples += 1
                db_diff = current_db - self._audio_baseline_db
                audio_score = min(max(db_diff / 10.0, 0.0), 1.0)
            else:
                audio_score = 0.0
        else:
            audio_score = 0.0
        signals.append(Signal(
            type=SignalType.AUDIO_SPIKE,
            value=audio_score,
            channel=self.channel,
            metadata={"audio_db": round(current_db, 1),
                      "baseline_db": round(self._audio_baseline_db, 1)},
        ))

        # ── Keyword (0-1) ─────────────────────────────────────────────────
        if snapshot.messages:
            keyword_rate = snapshot.keyword_hits / len(snapshot.messages)
            trigger_bonus = min(snapshot.trigger_phrases * 0.15, 0.3)
            if self.profile and self.profile.avg_keyword_rate > 0 and self.profile.keyword_samples >= 10:
                keyword_score = min((keyword_rate / max(self.profile.avg_keyword_rate, 0.01)) * 0.5 + trigger_bonus, 1.0)
            else:
                keyword_score = min(keyword_rate * 3 + trigger_bonus, 1.0)
        else:
            keyword_score = 0.0
        signals.append(Signal(
            type=SignalType.KEYWORD,
            value=keyword_score,
            channel=self.channel,
            metadata={"keyword_hits": snapshot.keyword_hits},
        ))

        # ── Sentiment (0-1) ───────────────────────────────────────────────
        if snapshot.messages:
            recent = snapshot.messages[-20:]
            compounds = [abs(self._vader.polarity_scores(m)["compound"]) for m in recent]
            avg_compound = sum(compounds) / len(compounds)
            if self.profile and self.profile.avg_sentiment > 0 and self.profile.sentiment_samples >= 10:
                sentiment_score = min(avg_compound / max(self.profile.avg_sentiment, 0.01) * 0.5, 1.0)
            else:
                sentiment_score = min(avg_compound * 2.0, 1.0)
        else:
            sentiment_score = 0.0
        signals.append(Signal(
            type=SignalType.SENTIMENT,
            value=sentiment_score,
            channel=self.channel,
            metadata={},
        ))

        # ── Viewer spike (0-1) ────────────────────────────────────────────
        # Score 1.0 when current viewers are 1.5x the slow EMA baseline.
        if self._viewer_samples >= 5 and self._viewer_baseline > 0:
            spike_ratio = self._viewer_current / self._viewer_baseline
            viewer_score = max(min((spike_ratio - 1.0) / 0.5, 1.0), 0.0)
        else:
            viewer_score = 0.0
        signals.append(Signal(
            type=SignalType.VIEWER_SPIKE,
            value=viewer_score,
            channel=self.channel,
            metadata={"viewer_current": round(self._viewer_current, 0),
                      "viewer_baseline": round(self._viewer_baseline, 0)},
        ))

        # ── Silence burst (0-1) ───────────────────────────────────────────
        silence_score = self._metrics.silence_burst_score()
        signals.append(Signal(
            type=SignalType.SILENCE_BURST,
            value=silence_score,
            channel=self.channel,
            metadata={},
        ))

        return signals

    def _compute_score(self, signals: list[Signal]) -> float:
        base_weights = {
            SignalType.CHAT_VELOCITY: 40,
            SignalType.AUDIO_SPIKE: 30,
            SignalType.KEYWORD: 20,
            SignalType.SENTIMENT: 10,
            SignalType.VIEWER_SPIKE: 10,
            SignalType.SILENCE_BURST: 10,
        }
        # Apply per-signal learned multipliers from the streamer profile
        weights = {}
        for sig_type, base in base_weights.items():
            key = str(sig_type).split(".")[-1]   # e.g. "CHAT_VELOCITY"
            multiplier = (
                self.profile.signal_weights.get(key, 1.0)
                if self.profile else 1.0
            )
            weights[sig_type] = base * multiplier

        raw = sum(s.value * weights.get(s.type, 0) for s in signals)
        raw = min(raw, 100)

        # Multiplier: x1.25 if 3 or more signals are active (value > 0.25)
        active = sum(1 for s in signals if s.value > 0.25)
        if active >= 3:
            raw *= 1.25

        # Sub/raid flat bonus (+15 if fired within last 30s)
        if self._sub_raid_active and (time.time() - self._sub_raid_time) < 30:
            raw += 15

        return min(raw, 100)
