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


# What a human should be shown for each of the above.
#
# WHY THIS EXISTS. Signals are written into stored clips as the STRINGIFIED
# ENUM — "SignalType.CHAT_VELOCITY" — and one screen was rendering that
# straight into the page: Settings -> Usage stats -> Top signal read
# "SignalType.CHAT_VELOCITY" to real users. The clip modal had always looked
# right only because it carried its own private `.replace('SignalType.','')`,
# so the leak was invisible from anywhere that already worked.
#
# One table, so a new signal type gets a name once instead of being formatted
# ad hoc by whichever screen happens to show it next.
SIGNAL_LABELS: dict[str, str] = {
    "CHAT_VELOCITY":     "Chat velocity",
    "KEYWORD":           "Keyword hits",
    "SENTIMENT":         "Sentiment",
    "AUDIO_SPIKE":       "Audio spike",
    "MANUAL":            "Manual",
    "VIEWER_SPIKE":      "Viewer spike",
    "SILENCE_BURST":     "Silence burst",
    "EMOTE_HOMOGENEITY": "Emote wall",
}


def signal_label(raw: str) -> str:
    """A human name for a stored signal type, however it was written down.

    Accepts every shape that has been persisted over time — "SignalType.KEYWORD",
    "KEYWORD", "keyword" — because clips on disk predate any one of them being
    settled on, and a five-year-old clip must not print a Python repr.

    An unknown type is title-cased rather than dropped: a signal we have not
    named yet is still worth naming badly, and returning "" would silently blank
    the field.
    """
    if not raw:
        return ""
    key = str(raw).split(".")[-1].upper()
    return SIGNAL_LABELS.get(key, key.replace("_", " ").capitalize())


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
    # TWO DIFFERENT THINGS, AND THEY USED TO SHARE ONE FIELD.
    #
    # post_roll is how long to WAIT before asking Twitch for a clip. It must
    # stay small (TAIL_SECS, 4s): Twitch's Create Clip captures the window
    # ENDING at the moment of the call, so waiting longer pushes the moment
    # off the front of what Twitch keeps.
    #
    # clip_post_roll is how much of the AFTERMATH the cut keeps in our own
    # file — the reaction, the replay, chat losing it. The presets set it
    # between 22s and 32s by content type.
    #
    # The cut used post_roll for both, so every captured clip ended 4 seconds
    # after the moment: measured on production 2026-09-19, Kick clips ran a
    # median of 24s against presets asking for 35-66s (variety 10+4=14s was
    # the shortest, exactly the observed minimum). 0 means "fall back to
    # post_roll", which is what a manually forced clip still does.
    post_roll: int = 10
    clip_post_roll: int = 0
    virality_score: float = 0.0
    clip_title: str = ""
