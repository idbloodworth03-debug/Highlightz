"""
Tracks real-time chat metrics: messages-per-second velocity,
keyword hits, and a rolling average for spike detection.
"""

import time
import re
from collections import deque
from dataclasses import dataclass

HIGH_ENERGY_KEYWORDS = {
    "clip", "clipit", "pogchamp", "pog", "poggers", "omegalul", "lul",
    "holy", "insane", "wtf", "omg", "nooo", "noway", "no way", "lets go",
    "letsgo", "gg", "rip", "ez", "clutch", "monkas", "pepega", "sadge",
    "widepeeposad", "catjam", "hyperclap", "clap", "goat",
}

CLIP_TRIGGER_PHRASES = re.compile(
    r"\b(clip\s*it|someone\s*clip|clip\s*that|clip\s*this)\b", re.IGNORECASE
)


@dataclass
class ChatSnapshot:
    timestamp: float
    messages: list[str]
    velocity: float          # messages per second at snapshot time
    keyword_hits: int
    trigger_phrases: int


class ChatMetrics:
    """
    Sliding-window metrics tracker.
    Call `ingest(message)` for every incoming chat message.
    Call `snapshot()` to get current metrics for the trigger engine.
    """

    def __init__(self, window_seconds: int = 15, extra_keywords: frozenset | None = None) -> None:
        self._keywords = HIGH_ENERGY_KEYWORDS | (extra_keywords or frozenset())
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._messages: deque[str] = deque()
        self._keyword_counts: deque[int] = deque()
        self._trigger_counts: deque[int] = deque()

        # Rolling 5-min average for spike detection
        self._long_window: deque[float] = deque()
        self._long_window_seconds = 300

        # Silence-burst tracking
        self._last_quiet_time: float = 0.0
        self._quiet_duration: float = 0.0
        self._in_quiet: bool = False
        self._quiet_start: float = 0.0

    def ingest(self, message: str) -> None:
        now = time.time()
        tokens = set(re.findall(r"\w+", message.lower()))
        keyword_hit = int(bool(tokens & self._keywords))
        trigger_hit = int(bool(CLIP_TRIGGER_PHRASES.search(message)))

        self._timestamps.append(now)
        self._messages.append(message)
        self._keyword_counts.append(keyword_hit)
        self._trigger_counts.append(trigger_hit)
        self._long_window.append(now)

        self._prune(now)
        self._update_silence_tracking(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
            self._messages.popleft()
            self._keyword_counts.popleft()
            self._trigger_counts.popleft()

        long_cutoff = now - self._long_window_seconds
        while self._long_window and self._long_window[0] < long_cutoff:
            self._long_window.popleft()

    def current_velocity(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        return len(self._timestamps) / max(elapsed, 1.0)

    def long_term_velocity(self) -> float:
        if len(self._long_window) < 2:
            return 0.0
        elapsed = self._long_window[-1] - self._long_window[0]
        return len(self._long_window) / max(elapsed, 1.0)

    def velocity_spike_ratio(self) -> float:
        lt = self.long_term_velocity()
        if lt == 0:
            return 1.0
        return self.current_velocity() / lt

    def _update_silence_tracking(self, now: float) -> None:
        """Track periods where velocity drops below 0.25x long-term average."""
        lt = self.long_term_velocity()
        if lt == 0:
            return
        threshold = 0.25 * lt
        current = self.current_velocity()

        if current < threshold:
            if not self._in_quiet:
                self._in_quiet = True
                self._quiet_start = now
        else:
            if self._in_quiet:
                self._in_quiet = False
                self._quiet_duration = now - self._quiet_start
                self._last_quiet_time = now

    def silence_burst_score(self) -> float:
        """
        Returns 0.0-1.0 indicating a silence-then-burst pattern.
        1.0 if >= 4s of silence in last 30s AND current velocity >= 2x long-term avg.
        0.5 if >= 2s of quiet followed by current spike.
        0.0 otherwise.
        """
        now = time.time()
        lt = self.long_term_velocity()
        if lt == 0:
            return 0.0

        time_since_quiet_ended = now - self._last_quiet_time
        if time_since_quiet_ended > 30 or self._last_quiet_time == 0.0:
            return 0.0

        current = self.current_velocity()
        if current < 2.0 * lt:
            return 0.0

        if self._quiet_duration >= 4.0:
            return 1.0
        if self._quiet_duration >= 2.0:
            return 0.5
        return 0.0

    def snapshot(self) -> ChatSnapshot:
        now = time.time()
        self._prune(now)
        return ChatSnapshot(
            timestamp=now,
            messages=list(self._messages),
            velocity=self.current_velocity(),
            keyword_hits=sum(self._keyword_counts),
            trigger_phrases=sum(self._trigger_counts),
        )
