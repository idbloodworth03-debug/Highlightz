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
from collections import deque
import structlog
from typing import Callable, Awaitable

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from config.settings import settings
from .signals import Signal, SignalType, TriggerEvent
from .rules import get_rules
from . import scoring
from src.chat.metrics import ChatMetrics, ChatSnapshot

log = structlog.get_logger(__name__)

# ~15 minutes of evaluations, comfortably longer than the measured p90 clip
# visibility lag (369s) so late-surfacing viewer clips still find a score.
_SCORE_HISTORY_MAX = 1200

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
        self._last_signals: list = []  # latest built signals; read by _monitor_and_fire on a re-peak
        # Rolling score history: (epoch, score). Exists so a viewer clip that
        # surfaces LATE can still be paired with what we thought at the moment
        # it was actually made. Measured Twitch clip-visibility lag is ~167s
        # median (p90 369s), so 15 minutes of history covers essentially every
        # clip. Small: one float pair per evaluation.
        self._score_history: deque = deque(maxlen=_SCORE_HISTORY_MAX)
        # Dry-spell clock: reference time used to detect "no clip in a long while".
        # Anchored on the first calibrated evaluation and reset on every trigger fire
        # and every dry-spell recalibration. See _maybe_recalibrate_dry_spell.
        self._dry_anchor: float = 0.0
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

    def score_at(self, ts: float, tolerance: float = 20.0) -> float | None:
        """What we scored at wall-clock `ts` — the score closest in time within
        `tolerance` seconds, or None if we have no reading that near.

        This is what makes viewer clips usable despite Twitch surfacing them
        minutes late: we look BACKWARD at what the engine thought when the
        viewer actually clipped, rather than needing to react in the moment.
        """
        best = None
        best_gap = tolerance
        for t, sc in self._score_history:
            gap = abs(t - ts)
            if gap <= best_gap:
                best_gap = gap
                best = sc
        return best

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
        self._last_signals = signals   # latest snapshot, read by _monitor_and_fire on a re-peak
        self._score_history.append((now, round(score, 1)))
        log.debug("trigger_score", channel=self.channel, score=round(score, 1))

        if self.on_score:
            breakdown = {str(s.type).split(".")[-1]: round(s.value, 3) for s in signals}
            # Attach raw measurements so the dashboard can show live dB / viewer counts
            sig_meta = {str(s.type).split(".")[-1]: s.metadata for s in signals}
            audio_meta  = sig_meta.get("AUDIO_SPIKE", {})
            viewer_meta = sig_meta.get("VIEWER_SPIKE", {})
            chat_meta   = sig_meta.get("CHAT_VELOCITY", {})
            breakdown["_audio_db"]      = round(audio_db, 1)
            breakdown["_audio_peak_db"] = round(self._audio_peak_db, 1)
            breakdown["_audio_base_db"] = audio_meta.get("baseline_db", round(self._audio_baseline_db, 1))
            breakdown["_viewers"]       = int(viewer_meta.get("viewer_current", self._viewer_current))
            breakdown["_viewer_base"]   = int(viewer_meta.get("viewer_baseline", self._viewer_baseline))
            # Heartbeat: raw chat rate vs baseline, freshness of the last chat
            # message ever received (-1 = none yet — distinguishes "quiet chat"
            # from "chat never connected"), and the live firing bar so the
            # dashboard can show distance-to-clip.
            breakdown["_chat_vps"]      = chat_meta.get("velocity", round(snapshot.velocity, 2))
            breakdown["_chat_base_vps"] = chat_meta.get("baseline", 0)
            breakdown["_last_chat_s"]   = int(now - snapshot.last_message_ts) if snapshot.last_message_ts > 0 else -1
            breakdown["_threshold"]     = round(threshold, 1)
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

        # Dry-spell recalibration: a calibrated channel that hasn't clipped in a
        # long time has probably changed activity — loosen the bar a notch so the
        # engine re-adapts instead of staying stuck on a stale threshold.
        self._maybe_recalibrate_dry_spell(now)

        if in_cooldown:
            secs_left = int(rules.cooldown_seconds - (now - self._last_trigger))
            since_last = now - self._last_trigger
            # The override may break the normal cooldown, but it must still honor
            # its own minimum spacing — otherwise a channel parked above
            # emergency_threshold fires every tick and floods the clip queue with
            # ~1 duplicate job/second of the same sustained moment.
            if score >= rules.emergency_threshold and since_last >= rules.emergency_cooldown_seconds:
                # Score is exceptional and enough time has passed since the last
                # clip — break the normal cooldown so a huge moment isn't missed.
                log.info("trigger_cooldown_override", channel=self.channel,
                         score=round(score, 1), threshold=round(threshold, 1),
                         emergency_threshold=rules.emergency_threshold,
                         emergency_cooldown_s=rules.emergency_cooldown_seconds,
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
            self._dry_anchor   = now   # a clip fired — restart the dry-spell clock

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

    def _maybe_recalibrate_dry_spell(self, now: float) -> None:
        """
        Adapt to mid-stream activity changes. If a calibrated channel goes
        DRY_SPELL_SECS with no trigger, the threshold has likely gone stale for
        whatever the streamer is doing now (switched game, calmer segment, etc.),
        so lower trigger_threshold one step and reset the clock. Repeats until a
        clip fires. The mutation lands on the shared StreamerProfile, so the
        worker's profile loop persists and broadcasts it on its next save.

        Composes with the other threshold movers rather than overriding them:
          - accept/reject (record_clip) still nudges per clip,
          - the hourly decay still pulls back toward the seed threshold,
          - this only ever lowers, and never below DRY_SPELL_THRESHOLD_FLOOR, so a
            dead stream can't loosen into clipping random noise.
        """
        if self.profile is None:
            return
        # Anchor on the first calibrated tick (and for already-calibrated
        # returning streamers) so a fresh session doesn't read as an instant dry
        # spell off the cold _last_trigger == 0 reference.
        if self._dry_anchor == 0.0:
            self._dry_anchor = now
            return
        if now - self._dry_anchor < scoring.DRY_SPELL_SECS:
            return

        before = self.profile.trigger_threshold
        target = max(scoring.DRY_SPELL_THRESHOLD_FLOOR,
                     before - scoring.DRY_SPELL_THRESHOLD_STEP)
        self._dry_anchor = now   # reset the clock either way so we re-check in 10 min

        if target >= before:
            # accept/reject learning already has it at/below the dry-spell floor —
            # don't fight that by raising it; just keep watching.
            return

        self.profile.trigger_threshold = round(target, 2)
        log.info("dry_spell_recalibrated", channel=self.channel,
                 minutes_dry=round(scoring.DRY_SPELL_SECS / 60, 1),
                 threshold_from=round(before, 1),
                 threshold_to=round(target, 1),
                 reason="no clip in a long while — streamer activity likely changed")

    async def _monitor_and_fire(
        self,
        peak_score: float,
        signals: list,
        rules,
        lag_offset: int,
    ) -> None:
        """
        Fire the clip at the TOP of the trigger, not on the downfall.

        Twitch captures the last ~30 s of broadcast at API-call time, so call
        timing is what positions the moment in the clip. The trigger fires when
        chat pops off, i.e. the on-screen moment is ~now — so calling right then
        ends the 30 s window on the moment, keeping the build-up + payoff in
        frame. The previous behavior waited for the score to decay (up to ~45 s),
        which slid the window forward into the aftermath and produced flat clips.

        We now hold only a short SETTLE window — long enough for the reaction to
        crest into Twitch's buffer and to capture the true local peak (the score
        often climbs a beat past the threshold crossing) — then fire immediately.
        We never wait for decay. If a higher crest lands inside the settle we
        adopt it (and its fresher signals), but we do not chase a second peak for
        tens of seconds; capping the wait is the whole point.
        """
        SETTLE_SECS = 3.0    # brief hold to catch the crest; NOT a decay wait
        TAIL_SECS   = 4      # small post-decision tail before the API call
        INTERVAL    = 1.0

        waited = 0.0
        peak   = peak_score
        while self._running and waited < SETTLE_SECS:
            await asyncio.sleep(INTERVAL)
            waited += INTERVAL
            cur = self._last_score
            # Track the crest only — adopt a higher peak (and its fresher signals)
            # if the score is still climbing, so we clip the true top.
            if cur > peak:
                peak = cur
                if self._last_signals:
                    signals = self._last_signals

        # Two-point guard: check after the sleep loop and again right before
        # firing so a stop() that lands in that narrow window is caught.
        if not self._running:
            return

        log.info("trigger_clip_timing", channel=self.channel,
                 waited_secs=round(waited, 1), peak_score=round(peak, 1),
                 score_at_fire=round(self._last_score, 1))

        if not self._running:
            return

        event = TriggerEvent(
            channel=self.channel,
            score=peak,
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
            acceleration=snapshot.velocity_acceleration,
            avg_message_length=snapshot.avg_message_length,
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
                "acceleration": round(snapshot.velocity_acceleration, 2),
                "avg_msg_len": round(snapshot.avg_message_length, 1),
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

    # Titles say what was actually MEASURED, phrased as a viewer would see it.
    # (The old set over-promised: "Massive Pop-Off" for a viewer-count blip,
    # "Insane Moment" for a silence pattern — titles routinely didn't match
    # the clip. A label is only used when its signal clearly led the moment.)
    _SIGNAL_TITLES = {
        SignalType.CHAT_VELOCITY:     "Chat Erupts",
        SignalType.AUDIO_SPIKE:       "Loud Reaction",
        SignalType.KEYWORD:           "Chat Calls For The Clip",
        SignalType.VIEWER_SPIKE:      "Viewers Flood In",
        SignalType.SENTIMENT:         "Emotions Run High",
        SignalType.SILENCE_BURST:     "Silence, Then Chaos",
        SignalType.EMOTE_HOMOGENEITY: "Chat Speaks As One",
    }

    def _generate_clip_title(self, signals: list[Signal]) -> str:
        """Title from what the signals actually measured.

        Dominance rule: name the peak signal only when it clearly led
        (strong AND well ahead of the runner-up). When several signals are
        strong at once, say that instead — calling a multi-signal moment
        "Loud Reaction" just because audio edged out chat by 0.02 is how the
        old titles ended up inaccurate. Weak, broad activity gets a neutral
        title rather than a manufactured superlative."""
        val = {s.type: s.value for s in signals}
        ranked = sorted(val.items(), key=lambda x: x[1], reverse=True)
        if not ranked or ranked[0][1] < 0.2:
            return f"Clip from {self.channel}"
        peak, peak_val = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        strong = sum(1 for v in val.values() if v > 0.5)

        if self._sub_raid_active and (time.time() - self._sub_raid_time) < 30:
            label = "Sub/Raid Hype"
        elif peak_val >= 0.5 and (runner_up < 0.3 or peak_val >= 1.5 * runner_up):
            label = self._SIGNAL_TITLES.get(peak, "Highlight")   # one clear story
        elif strong >= 2:
            label = "Everything Pops Off At Once"                # genuinely multi-signal
        else:
            label = "Hype Moment"                                # active, but no headline act
        return f"{self.channel} — {label}"

    def _compute_score(self, signals: list[Signal]) -> float:
        # Chat-signal weights come from the shared scoring module so live and VOD
        # never drift; audio/viewer/silence are live-only and added on top.
        base_weights = {
            SignalType.CHAT_VELOCITY:    scoring.CHAT_WEIGHTS["CHAT_VELOCITY"],
            # Cut 38 → 24 (July 2026 volume rebalance): audio was the largest
            # weight but a weak outcome separator (AUC 0.58, saturated at
            # ~0.86 even on rejected clips) — and requiring loudness meant
            # quiet moments (chat erupting over a silent clutch) structurally
            # couldn't reach the bar. Loudness now supports a clip; it no
            # longer gates one. A loud-but-chat-dead window (music,
            # soundboards) scores ~25 and stays far from any threshold.
            # 24 -> 22 (July 2026 human-calibration, n=1001): audio correlates
            # -0.03 with human-judged virality, and the human "audio spike"
            # slider vs this signal is -0.035 — i.e. what we measure as an
            # audio spike is NOT what a person hears as one. Both datasets now
            # agree it's weak (outcome AUC was 0.58). Trimmed, not gutted: at
            # 22 a loud moment still comfortably supports a clip.
            SignalType.AUDIO_SPIKE:      22,
            SignalType.KEYWORD:          scoring.CHAT_WEIGHTS["KEYWORD"],
            SignalType.SENTIMENT:        scoring.CHAT_WEIGHTS["SENTIMENT"],
            # Raised 7 → 15 (July 2026 training-log analysis, n=806): the best
            # outcome separator of all signals (AUC 0.73; approved-clip mean
            # 0.63 vs 0.26 in junk) and viewer-led clips were approved at 10%
            # — 4-5x the base rate.
            # 15 -> 20 (July 2026 human-calibration): the ONLY signal supported
            # by both datasets — outcome AUC 0.73 (best separator) and the sole
            # positive, non-contested correlation with human virality (+0.07).
            # Two independent measurements agreeing is the strongest evidence
            # this project has produced, so this gets the increase.
            SignalType.VIEWER_SPIKE:     19,
            # 14 -> 10 (July 2026 human-calibration): the only signal that is
            # significantly INVERTED against human judgment (-0.102, n=1001) —
            # it fires on clips humans score low. It was raised 12 -> 14 on a
            # theory (held breath -> payoff) that the data does not support.
            # Kept at 10 rather than removed: quiet-highlight coverage still
            # matters and one dataset is not enough to delete a signal.
            SignalType.SILENCE_BURST:    11,
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
