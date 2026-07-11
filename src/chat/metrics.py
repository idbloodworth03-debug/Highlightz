"""
Tracks real-time chat metrics: messages-per-second velocity,
keyword hits, and a rolling average for spike detection.
"""

import time
import re
from collections import deque, Counter
from dataclasses import dataclass, field

HIGH_ENERGY_KEYWORDS = {
    "clip", "clipit", "pogchamp", "pog", "poggers", "omegalul", "lul",
    "holy", "insane", "wtf", "omg", "nooo", "noway", "no way", "lets go",
    "letsgo", "gg", "rip", "ez", "clutch", "monkas", "pepega", "sadge",
    "widepeeposad", "catjam", "hyperclap", "clap", "goat",
}

# Emotes that signal genuine hype/excitement — count 2.5x in weighted velocity.
# Single-token messages matching these get amplified in the velocity calculation.
HYPE_EMOTES = frozenset({
    "pogchamp", "pog", "poggers", "poggies", "kekw", "omegalul",
    "lul", "lmao", "lol", "gg", "trihard", "monkas", "catjam", "hyperclap",
    "clap", "sadge", "pepehands", "biblethump", "weirdchamp", "pepega",
    "widepeeposad", "painchamp",
})
HYPE_EMOTE_WEIGHT = 2.5

CLIP_TRIGGER_PHRASES = re.compile(
    r"\b(clip\s*it|someone\s*clip|clip\s*that|clip\s*this)\b", re.IGNORECASE
)

# A message consisting of a single word (typical Twitch emote or short reaction)
_SINGLE_TOKEN = re.compile(r"^\w+$")

# Window for the clip-it trip-wire unique-sender check
_CLIP_IT_WINDOW = 10.0


def emote_weight(message: str) -> float:
    """Weight a message for weighted velocity: single-token hype emotes count 2.5x."""
    stripped = message.strip().lower()
    if _SINGLE_TOKEN.match(stripped) and stripped in HYPE_EMOTES:
        return HYPE_EMOTE_WEIGHT
    return 1.0


def emote_homogeneity_of(messages: list[str]) -> float:
    """
    Fraction of messages that are the single most-common single-token (emote).
    Returns 0 when fewer than 4 single-token messages are present (avoids noise
    on thin chat). Shared by live metrics and VOD replay so both measure
    crowdspeak identically.
    """
    if not messages:
        return 0.0
    emote_tokens = [
        m.strip().lower() for m in messages
        if _SINGLE_TOKEN.match(m.strip())
    ]
    if len(emote_tokens) < 4:
        return 0.0
    top_count = Counter(emote_tokens).most_common(1)[0][1]
    return top_count / len(messages)


@dataclass
class ChatSnapshot:
    timestamp: float
    messages: list[str]
    velocity: float             # raw messages per second
    weighted_velocity: float    # hype-emote-weighted messages per second
    keyword_hits: int
    trigger_phrases: int
    unique_senders: int         # distinct users in current window
    clip_it_unique_senders: int # distinct users who sent "clip it" in last 10s
    emote_homogeneity: float    # fraction of messages that are the single most-common emote [0,1]
    velocity_acceleration: float = 1.0  # recent-half vs older-half chat rate (>1 = accelerating)
    avg_message_length: float = 0.0     # mean message char length (collapses during frantic hype)
    # Wall-clock time of the last ingested message EVER (not window-pruned), so
    # the dashboard heartbeat can distinguish "chat is quiet" from "chat was
    # never connected". 0.0 = no message has ever arrived.
    last_message_ts: float = 0.0


class ChatMetrics:
    """
    Sliding-window metrics tracker.
    Call `ingest(author, message)` for every incoming chat message.
    Call `snapshot()` to get current metrics for the trigger engine.
    """

    def __init__(self, window_seconds: int = 15, extra_keywords: frozenset | None = None) -> None:
        self._keywords = HIGH_ENERGY_KEYWORDS | (extra_keywords or frozenset())
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._messages: deque[str] = deque()
        self._authors: deque[str] = deque()
        self._keyword_counts: deque[int] = deque()
        self._trigger_counts: deque[int] = deque()
        self._emote_weights: deque[float] = deque()  # weight per message for weighted velocity

        # Rolling 5-min average for spike detection
        self._long_window: deque[float] = deque()
        self._long_window_seconds = 300

        # Clip-it trip-wire: (timestamp, author) pairs within the last 10s
        self._clip_it_events: deque[tuple[float, str]] = deque()

        # Last message ever ingested (never pruned) — dashboard heartbeat.
        self._last_message_ts: float = 0.0

        # Silence-burst tracking
        self._last_quiet_time: float = 0.0
        self._quiet_duration: float = 0.0
        self._in_quiet: bool = False
        self._quiet_start: float = 0.0

    def ingest(self, message: str, author: str = "") -> None:
        now = time.time()
        tokens = set(re.findall(r"\w+", message.lower()))
        keyword_hit = int(bool(tokens & self._keywords))
        trigger_hit = int(bool(CLIP_TRIGGER_PHRASES.search(message)))

        # Emote weight: single-token hype emotes count 2.5x in weighted velocity
        weight = emote_weight(message)

        self._last_message_ts = now
        self._timestamps.append(now)
        self._messages.append(message)
        self._authors.append(author)
        self._keyword_counts.append(keyword_hit)
        self._trigger_counts.append(trigger_hit)
        self._emote_weights.append(weight)
        self._long_window.append(now)

        if trigger_hit and author:
            self._clip_it_events.append((now, author))

        self._prune(now)
        self._update_silence_tracking(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
            self._messages.popleft()
            self._authors.popleft()
            self._keyword_counts.popleft()
            self._trigger_counts.popleft()
            self._emote_weights.popleft()

        long_cutoff = now - self._long_window_seconds
        while self._long_window and self._long_window[0] < long_cutoff:
            self._long_window.popleft()

        clip_cutoff = now - _CLIP_IT_WINDOW
        while self._clip_it_events and self._clip_it_events[0][0] < clip_cutoff:
            self._clip_it_events.popleft()

    def current_velocity(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        return len(self._timestamps) / max(elapsed, 1.0)

    def weighted_velocity(self) -> float:
        """Velocity weighted by emote type — hype emotes count 2.5x."""
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        return sum(self._emote_weights) / max(elapsed, 1.0)

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

    def velocity_acceleration(self) -> float:
        """
        Derivative of chat speed: rate in the recent half of the window vs the
        older half. >1.0 means chat is still accelerating (the ramp *into* a
        peak); ~1.0 steady; <1.0 decelerating (past the peak). Catches moments a
        beat earlier than absolute velocity, which only crosses threshold once
        the spike is already large. Returns 1.0 (neutral) on thin data.
        """
        if len(self._timestamps) < 6:
            return 1.0
        now = self._timestamps[-1]
        half = self._window / 2.0
        mid = now - half
        recent = sum(1 for t in self._timestamps if t >= mid)
        older  = len(self._timestamps) - recent
        if older <= 0:
            return 1.0
        # Normalize both halves to per-second rates over the same span.
        return (recent / half) / max(older / half, 1e-6)

    def avg_message_length(self) -> float:
        """Mean character length of messages in the window. Frantic real hype
        collapses message length (short bursts: 'OMG', 'LETSGO', emotes), so a
        low value alongside elevated velocity is a hype tell. 0.0 when empty."""
        if not self._messages:
            return 0.0
        return sum(len(m) for m in self._messages) / len(self._messages)

    def emote_homogeneity(self) -> float:
        """Fraction of the window that is the single most-common emote (crowdspeak)."""
        return emote_homogeneity_of(list(self._messages))

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
        unique_senders = len(set(self._authors)) if self._authors else 0
        clip_it_unique = len({a for _, a in self._clip_it_events if a})
        return ChatSnapshot(
            timestamp=now,
            messages=list(self._messages),
            velocity=self.current_velocity(),
            weighted_velocity=self.weighted_velocity(),
            keyword_hits=sum(self._keyword_counts),
            trigger_phrases=sum(self._trigger_counts),
            unique_senders=unique_senders,
            clip_it_unique_senders=clip_it_unique,
            emote_homogeneity=self.emote_homogeneity(),
            velocity_acceleration=self.velocity_acceleration(),
            avg_message_length=self.avg_message_length(),
            last_message_ts=self._last_message_ts,
        )
