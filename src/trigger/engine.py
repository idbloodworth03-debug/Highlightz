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
from . import scoring
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
        preset: str = "default",
    ) -> None:
        self.channel = channel
        self.preset  = preset
        self.on_trigger = on_trigger
        self.on_score = on_score
        self.profile = profile
        self.buffer = buffer
        _init_rules = get_rules(channel, preset)
        self._metrics = ChatMetrics(extra_keywords=_init_rules.extra_keywords)
        self._vader = SentimentIntensityAnalyzer()
        self._last_trigger: float = 0.0
        self._running = False
        # If the profile already has calibrated audio data, start from there
        # so the very first evaluation isn't falsely loud.
        if profile and profile.audio_db_samples >= 30:
            self._audio_baseline_db: float = profile.avg_audio_db
            self._audio_samples: int = profile.audio_db_samples
        else:
            self._audio_baseline_db: float = -30.0
            self._audio_samples: int = 0
        self._audio_peak_db: float = self._audio_baseline_db
        self._sub_raid_active: bool = False
        self._sub_raid_time: float = 0.0
        self._viewer_current: float = 0.0
        self._viewer_baseline: float = 0.0
        self._viewer_samples: int = 0
        self._last_score: float = 0.0  # updated each evaluation; read by monitoring tasks
        # Detached post-trigger monitoring tasks (see _monitor_and_fire). Tracked so
        # stop() can cancel them — otherwise a removed stream keeps firing clips.
        self._monitor_tasks: set[asyncio.Task] = set()

    # ── Public API ────────────────────────────────────────────────────────

    def ingest_chat(self, author: str, message: str) -> None:
        self._metrics.ingest(message, author=author)

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
        rules = get_rules(self.channel, self.preset)
        now = time.time()

        # Use profile's adaptive threshold if available
        threshold = self.profile.trigger_threshold if self.profile else rules.trigger_threshold

        in_cooldown = now - self._last_trigger < rules.cooldown_seconds

        snapshot = self._metrics.snapshot()
        audio_db = await self.buffer.get_audio_level_db() if self.buffer else -100.0
        signals = self._build_signals(snapshot, rules, audio_db)
        score = self._compute_score(signals)

        # Clip-it community trip-wire: 3+ unique users explicitly requesting a clip
        # in the last 10s is treated as community consensus — raise score to at least 90
        # so it overrides cooldown (emergency_threshold default = 85).
        if snapshot.clip_it_unique_senders >= scoring.CLIP_IT_MIN_SENDERS and (not self.profile or self.profile.is_calibrated):
            if score < scoring.CLIP_IT_FLOOR:
                log.info("clip_it_tripwire", channel=self.channel,
                         unique_senders=snapshot.clip_it_unique_senders,
                         score_before=round(score, 1))
                score = scoring.CLIP_IT_FLOOR

        self._last_score = score
        log.debug("trigger_score", channel=self.channel, score=round(score, 1))

        if self.on_score:
            breakdown = {str(s.type).split(".")[-1]: round(s.value, 3) for s in signals}
            # Attach raw measurements so the dashboard can show live dB / viewer counts
            sig_meta = {str(s.type).split(".")[-1]: s.metadata for s in signals}
            audio_meta  = sig_meta.get("AUDIO_SPIKE", {})
            viewer_meta = sig_meta.get("VIEWER_SPIKE", {})
            breakdown["_audio_db"]      = round(audio_db, 1)
            breakdown["_audio_peak_db"] = round(self._audio_peak_db, 1)
            breakdown["_audio_base_db"] = audio_meta.get("baseline_db", round(self._audio_baseline_db, 1))
            breakdown["_viewers"]       = int(viewer_meta.get("viewer_current", self._viewer_current))
            breakdown["_viewer_base"]   = int(viewer_meta.get("viewer_baseline", self._viewer_baseline))
            try:
                await self.on_score(self.channel, round(score, 1), breakdown)
            except Exception as exc:
                log.warning("on_score_callback_error", channel=self.channel, error=str(exc))

        # Calibration gate: suppress all triggers until the profile baseline is
        # established from enough live observation samples. This prevents the engine
        # from firing on the first velocity blip before it knows what "normal" looks like.
        if self.profile and not self.profile.is_calibrated:
            pct = round(self.profile.calibration_pct, 0)
            if score >= threshold:
                log.info("trigger_suppressed_calibrating", channel=self.channel,
                         score=round(score, 1), calibration_pct=pct,
                         samples=self.profile.velocity_samples,
                         target=self.profile.calibration_target)
            return

        if in_cooldown:
            secs_left = int(rules.cooldown_seconds - (now - self._last_trigger))
            if score >= rules.emergency_threshold:
                # Score is exceptional — break the cooldown so a huge moment isn't
                # missed just because a smaller clip fired recently.
                log.info("trigger_cooldown_override", channel=self.channel,
                         score=round(score, 1), threshold=round(threshold, 1),
                         emergency_threshold=rules.emergency_threshold,
                         cooldown_remaining_s=secs_left)
                # Fall through to the fire block below
            elif score >= threshold:
                log.info("trigger_suppressed_cooldown", channel=self.channel,
                         score=round(score, 1), threshold=round(threshold, 1),
                         cooldown_remaining_s=secs_left)
                return
            else:
                return

        if score >= threshold:
            self._last_trigger = now

            # Chat lag correction: chat reactions trail the on-screen event by ~2s.
            # When chat velocity is stronger than audio, extend pre_roll by 2s so
            # the clip captures the moment that caused the spike rather than starting
            # at the peak of chat activity.
            chat_val = next((s.value for s in signals if s.type == SignalType.CHAT_VELOCITY), 0.0)
            audio_val = next((s.value for s in signals if s.type == SignalType.AUDIO_SPIKE), 0.0)
            lag_offset = 2 if chat_val > audio_val else 0

            sig_vals = {str(s.type).split(".")[-1]: round(s.value, 3) for s in signals}
            log.info("trigger_fired", channel=self.channel, score=round(score, 1),
                     threshold=round(threshold, 1), signals=sig_vals,
                     audio_db=round(audio_db, 1), audio_peak=round(self._audio_peak_db, 1),
                     audio_base=round(self._audio_baseline_db, 1))

            # Don't push the clip job immediately. Instead, monitor the score for
            # up to 52 s and fire the job only when excitement dulls (score < 40 %
            # of peak). This makes the clip end at the natural end of the moment
            # rather than cutting off mid-reaction. The processor still waits a
            # small tail (TAIL_SECS) after the monitoring delay so the platform API
            # call happens at roughly trigger_time + watched + TAIL_SECS, and the
            # platform captures ~60 s ending at that point.
            mon = asyncio.create_task(
                self._monitor_and_fire(score, signals, rules, lag_offset),
                name=f"monitor-{self.channel}-{int(now)}",
            )
            self._monitor_tasks.add(mon)
            mon.add_done_callback(self._monitor_tasks.discard)

    async def run_evaluation_loop(self, interval: float = 1.0) -> None:
        self._running = True
        while self._running:
            try:
                await self.evaluate()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("evaluate_error", channel=self.channel, error=str(exc))
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False
        # Cancel any in-flight post-trigger monitors so a removed stream can't
        # push a clip after the worker is gone.
        for t in list(self._monitor_tasks):
            t.cancel()
        self._monitor_tasks.clear()

    # ── Internal ─────────────────────────────────────────────────────────

    async def _monitor_and_fire(
        self,
        peak_score: float,
        signals: list,
        rules,
        lag_offset: int,
    ) -> None:
        """
        After a trigger fires, poll _last_score (updated by the normal evaluation
        loop every 1 s) until the excitement dulls: score drops below 40 % of the
        peak, or 52 s elapse (Twitch captures the last ~60 s, so 52 s + TAIL_SECS
        still leaves a small pre-trigger window).  Then push the clip job with a
        short post_roll tail so the platform API is called at the natural end of
        the moment.
        """
        DECAY_FACTOR  = 0.40   # "excitement dulled" threshold relative to peak
        MAX_WAIT_SECS = 52.0   # hard cap — Twitch buffer is ~60 s
        TAIL_SECS     = 5      # seconds to include after decay before calling API
        INTERVAL      = 2.0

        waited = 0.0
        while self._running and waited < MAX_WAIT_SECS:
            await asyncio.sleep(INTERVAL)
            waited += INTERVAL
            if self._last_score < peak_score * DECAY_FACTOR:
                break

        if not self._running:
            return

        log.info("trigger_clip_timing", channel=self.channel,
                 waited_secs=round(waited, 1), peak_score=round(peak_score, 1),
                 score_at_fire=round(self._last_score, 1))

        event = TriggerEvent(
            channel=self.channel,
            score=peak_score,
            signals=signals,
            pre_roll=rules.pre_roll + lag_offset,
            post_roll=TAIL_SECS,
            virality_score=self._compute_virality_score(signals),
            clip_title=self._generate_clip_title(signals),
        )
        try:
            await self.on_trigger(event)
        except Exception as exc:
            log.error("on_trigger_callback_error", channel=self.channel, error=str(exc))

    def _build_signals(self, snapshot: ChatSnapshot, rules, audio_db: float = -100.0) -> list[Signal]:
        signals: list[Signal] = []

        # ── Chat velocity (0-1) ───────────────────────────────────────────
        # Uses raw velocity for the spike_ratio so it stays consistent with
        # the calibrated avg_velocity baseline, then applies two boosts:
        #   1. Emote weight boost — when hype emotes (PogChamp/KEKW/etc.) dominate,
        #      weighted_velocity > velocity; the ratio amplifies the score by up to +30%.
        #   2. Unique sender diversity boost — more distinct users = more organic; up to +20%.
        current_velocity = snapshot.velocity
        if self.profile and self.profile.avg_velocity > 0 and self.profile.velocity_samples >= 10:
            spike_ratio = current_velocity / self.profile.avg_velocity
            spike_multiplier = self.profile.velocity_spike_multiplier
        else:
            spike_ratio = self._metrics.velocity_spike_ratio()
            spike_multiplier = rules.velocity_multiplier

        velocity_score = scoring.velocity_score(
            spike_ratio, spike_multiplier,
            snapshot.velocity, snapshot.weighted_velocity, snapshot.unique_senders,
        )

        signals.append(Signal(
            type=SignalType.CHAT_VELOCITY,
            value=velocity_score,
            channel=self.channel,
            metadata={
                "velocity": round(current_velocity, 2),
                "weighted_velocity": round(snapshot.weighted_velocity, 2),
                "spike_ratio": round(spike_ratio, 2),
                "unique_senders": snapshot.unique_senders,
                "baseline": round(self.profile.avg_velocity, 2) if self.profile else 0,
            },
        ))

        # ── Emote homogeneity / crowdspeak (0-1) ─────────────────────────
        # Fires when >50% of chat messages in the window are the same single emote.
        # 50% → score 0.0 (ramp starts), 70% → score 0.5, 100% → score 1.0.
        homogeneity = snapshot.emote_homogeneity
        homo_score = scoring.emote_homogeneity_score(homogeneity)
        signals.append(Signal(
            type=SignalType.EMOTE_HOMOGENEITY,
            value=homo_score,
            channel=self.channel,
            metadata={"homogeneity": round(homogeneity, 3)},
        ))

        # ── Audio loudness (0-1) ──────────────────────────────────────────
        # Two phases:
        #   Warmup (first 30 samples): fast EMA builds the baseline from the
        #   streamer's actual audio, no scoring yet.  Skipped entirely when
        #   the profile already has ≥30 samples from a previous session.
        #   Scoring: peak vs slow-EMA baseline; 15 dB above baseline = 100%.
        #   15 dB means something went from barely audible to a shout/explosion
        #   — far enough that normal loudness variation never pins the bar.
        _AUDIO_WARMUP = 30
        _SPIKE_RANGE_DB = 15.0   # dB above baseline that maps to 1.0
        current_db = audio_db
        if current_db > -100:
            if self._audio_samples < _AUDIO_WARMUP:
                # Fast EMA: settle on this streamer's normal level in ~10s
                if self._audio_samples == 0:
                    self._audio_baseline_db = current_db
                else:
                    self._audio_baseline_db = 0.7 * self._audio_baseline_db + 0.3 * current_db
                self._audio_peak_db = self._audio_baseline_db
                self._audio_samples += 1
                audio_score = 0.0
            else:
                # Slow EMA baseline (~60s time constant), rolling peak with decay
                self._audio_baseline_db = 0.983 * self._audio_baseline_db + 0.017 * current_db
                if current_db >= self._audio_peak_db:
                    self._audio_peak_db = current_db
                else:
                    self._audio_peak_db = max(self._audio_baseline_db,
                                              self._audio_peak_db - 1.5)
                self._audio_samples += 1
                db_diff = self._audio_peak_db - self._audio_baseline_db
                audio_score = min(max(db_diff / _SPIKE_RANGE_DB, 0.0), 1.0)
        else:
            audio_score = 0.0
        signals.append(Signal(
            type=SignalType.AUDIO_SPIKE,
            value=audio_score,
            channel=self.channel,
            metadata={"audio_db":   round(current_db, 1),
                      "peak_db":    round(self._audio_peak_db, 1),
                      "baseline_db": round(self._audio_baseline_db, 1)},
        ))

        # ── Keyword (0-1) ─────────────────────────────────────────────────
        kw_baseline = (
            self.profile.avg_keyword_rate
            if self.profile and self.profile.keyword_samples >= 10 else 0.0
        )
        keyword_score = scoring.keyword_score(
            snapshot.keyword_hits, len(snapshot.messages),
            snapshot.trigger_phrases, baseline_rate=kw_baseline,
        )
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
            sent_baseline = (
                self.profile.avg_sentiment
                if self.profile and self.profile.sentiment_samples >= 10 else 0.0
            )
            sentiment_score = scoring.sentiment_score(avg_compound, baseline=sent_baseline)
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

    def _compute_virality_score(self, signals: list[Signal]) -> float:
        """
        Estimate virality potential (0-100) independent of the trigger threshold.
        Crowdspeak (emote homogeneity) is weighted highly — synchronized emote spam
        is one of the strongest predictors of a shareable moment.
        """
        val = {s.type: s.value for s in signals}
        silence   = val.get(SignalType.SILENCE_BURST, 0.0)
        audio     = val.get(SignalType.AUDIO_SPIKE, 0.0)
        keyword   = val.get(SignalType.KEYWORD, 0.0)
        sentiment = val.get(SignalType.SENTIMENT, 0.0)
        viewer    = val.get(SignalType.VIEWER_SPIKE, 0.0)
        crowdspeak = val.get(SignalType.EMOTE_HOMOGENEITY, 0.0)

        score = (
            audio      * 28 +   # loud = viral; crowd roar / stream reactions dominate
            silence    * 20 +
            crowdspeak * 18 +
            ((keyword + sentiment) / 2) * 16 +
            viewer     * 12 +
            val.get(SignalType.CHAT_VELOCITY, 0.0) * 6
        )

        # Sub/raid bonus
        if self._sub_raid_active and (time.time() - self._sub_raid_time) < 30:
            score += 5

        return round(min(score, 100), 1)

    def _generate_clip_title(self, signals: list[Signal]) -> str:
        """Generate a punchy social-ready title based on the peak signal."""
        val = {s.type: s.value for s in signals}
        ranked = sorted(val.items(), key=lambda x: x[1], reverse=True)
        peak, peak_val = ranked[0] if ranked else (None, 0.0)

        if peak_val < 0.2:
            return f"Clip from {self.channel}"

        titles = {
            SignalType.SILENCE_BURST:     "Insane Moment",
            SignalType.AUDIO_SPIKE:       "Big Reaction",
            SignalType.KEYWORD:           "Chat Goes Wild",
            SignalType.VIEWER_SPIKE:      "Massive Pop-Off",
            SignalType.SENTIMENT:         "Pure Emotion",
            SignalType.CHAT_VELOCITY:     "Chat Explodes",
            SignalType.EMOTE_HOMOGENEITY: "Chat Spams",
        }
        label = titles.get(peak, "Highlight")

        if self._sub_raid_active and (time.time() - self._sub_raid_time) < 30:
            label = "Sub/Raid Hype"

        return f"{self.channel} — {label}"

    def _compute_score(self, signals: list[Signal]) -> float:
        # Chat-signal weights come from the shared scoring module so live and VOD
        # never drift; audio/viewer/silence are live-only and added on top.
        base_weights = {
            SignalType.CHAT_VELOCITY:    scoring.CHAT_WEIGHTS["CHAT_VELOCITY"],
            SignalType.AUDIO_SPIKE:      38,   # loud audio is the strongest single predictor
            SignalType.KEYWORD:          scoring.CHAT_WEIGHTS["KEYWORD"],
            SignalType.SENTIMENT:        scoring.CHAT_WEIGHTS["SENTIMENT"],
            SignalType.VIEWER_SPIKE:      7,
            SignalType.SILENCE_BURST:    12,
            SignalType.EMOTE_HOMOGENEITY: scoring.CHAT_WEIGHTS["EMOTE_HOMOGENEITY"],   # crowdspeak — CHI 2017 validated
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

        # Multiplier: x1.2 only when 3+ signals are genuinely strong (>0.5).
        # Raising from >0.25 prevents keyword noise + any velocity blip from
        # triggering the bonus on every moderately-active chat window.
        active = sum(1 for s in signals if s.value > 0.5)
        if active >= scoring.MULTI_SIGNAL_MIN_ACTIVE:
            raw *= scoring.MULTI_SIGNAL_BONUS

        # Sub/raid flat bonus (+15 if fired within last 30s)
        if self._sub_raid_active and (time.time() - self._sub_raid_time) < 30:
            raw += 15

        return min(raw, 100)
