from dataclasses import dataclass, field
from enum import Enum
import time


class SignalType(str, Enum):
    CHAT_VELOCITY = "chat_velocity"
    KEYWORD = "keyword"
    SENTIMENT = "sentiment"
    AUDIO_SPIKE = "audio_spike"
    MANUAL = "manual"
    VIEWER_SPIKE = "viewer_spike"
    SILENCE_BURST = "silence_burst"
    EMOTE_HOMOGENEITY = "emote_homogeneity"  # crowdspeak: >N% of chat is the same emote


@dataclass
class Signal:
    type: SignalType
    value: float          # normalised 0.0–1.0
    channel: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class TriggerEvent:
    channel: str
    score: float
    signals: list[Signal]
    timestamp: float = field(default_factory=time.time)
    pre_roll: int = 30
    post_roll: int = 10
    virality_score: float = 0.0
    clip_title: str = ""
