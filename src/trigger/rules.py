"""
Per-channel rule overrides and streamer-specific profiles.

To add a new streamer:
  1. Add their keywords to STREAMER_KEYWORDS below
  2. Add a ChannelRules entry to CHANNEL_OVERRIDES below
  3. That's it — the engine picks it up automatically

Signal weight keys: CHAT_VELOCITY, AUDIO_SPIKE, KEYWORD, SENTIMENT, VIEWER_SPIKE, SILENCE_BURST
Weights are multipliers on top of base weights (1.0 = default, 2.0 = double importance)
"""

from dataclasses import dataclass, field


@dataclass
class ChannelRules:
    velocity_multiplier: float = 2.0    # how many x above avg to score 1.0
    trigger_threshold: float = 60.0
    pre_roll: int = 45
    post_roll: int = 15
    cooldown_seconds: int = 60          # min seconds between clips
    extra_keywords: set = field(default_factory=set)  # added on top of global keywords
    signal_weights: dict = field(default_factory=dict) # per-signal weight overrides


# ── Global high-energy keywords (apply to every channel) ─────────────────────

GLOBAL_KEYWORDS = {
    "clip", "clipit", "pogchamp", "pog", "poggers", "omegalul", "lul",
    "holy", "insane", "wtf", "omg", "nooo", "noway", "no way", "lets go",
    "letsgo", "gg", "rip", "ez", "clutch", "monkas", "pepega", "sadge",
    "widepeeposad", "catjam", "hyperclap", "clap", "goat", "w", "l",
    "kekw", "gigachad", "copium", "hopium", "caught", "diff",
}


# ── Streamer-specific keyword packs ──────────────────────────────────────────
# Add extra words/emotes unique to each streamer's community here

STREAMER_KEYWORDS: dict[str, set] = {

    # ── Jynxzi (Rainbow Six Siege) ────────────────────────────────────────
    # High-energy R6 FPS streamer — big reactions, clutch plays, rage moments
    "jynxzi": {
        # His catchphrases / reactions
        "yooo", "yoooo", "yooooo", "bro", "nahhh", "nah", "no cap",
        "nocap", "sheesh", "bussin", "fr", "frfr", "twin", "cuh",
        "deadass", "lowkey", "highkey", "valid", "send",

        # R6 Siege specific
        "ace", "aceit", "clutch", "1v1", "1v2", "1v3", "1v4", "1v5",
        "wallbang", "headshot", "operator", "drone", "plant", "defuse",
        "ranked", "plat", "diamond", "champ", "champion",
        "thermite", "ash", "jager", "valkyrie", "mozzie",

        # His community emotes / reactions
        "jynxzigang", "jynxpog", "gigajynx", "clueless", "aware",
        "turtle", "turtletime", "ez clap", "ezclap", "diff",

        # Hype moments his chat floods
        "no way bro", "bro what", "how", "actual", "actual goat",
        "top frag", "topfrag", "dropped", "carried", "ratio",
        "chat diff", "stream diff",
    },

}


# ── Preset profiles for common content types ──────────────────────────────────

PRESETS: dict[str, ChannelRules] = {
    "default": ChannelRules(),
    "chess": ChannelRules(
        velocity_multiplier=4.0,
        trigger_threshold=65.0,
    ),
    "fps": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=58.0,
        signal_weights={
            "AUDIO_SPIKE": 1.4,     # gunshots/explosions matter more
            "CHAT_VELOCITY": 1.2,
        },
    ),
    "irl": ChannelRules(
        velocity_multiplier=3.5,
        pre_roll=45,
        post_roll=15,
    ),
}


# ── Channel-specific overrides ────────────────────────────────────────────────
# Add a new entry here to tune any streamer. channel name must be lowercase.

CHANNEL_OVERRIDES: dict[str, ChannelRules] = {

    "jynxzi": ChannelRules(
        velocity_multiplier=2.2,        # his chat moves fast, don't need huge spike
        trigger_threshold=55.0,         # slightly lower — he has frequent hype moments
        pre_roll=40,                    # R6 rounds are short, catch the buildup
        post_roll=12,
        cooldown_seconds=45,            # allow more frequent clips than default
        extra_keywords=STREAMER_KEYWORDS["jynxzi"],
        signal_weights={
            "CHAT_VELOCITY": 1.3,       # chat goes crazy on big plays
            "AUDIO_SPIKE": 1.5,         # his mic reactions are a strong signal
            "KEYWORD": 1.4,             # community has strong keyword patterns
            "SENTIMENT": 0.8,           # less reliable for hype gaming content
        },
    ),

}


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_rules(channel: str) -> ChannelRules:
    return CHANNEL_OVERRIDES.get(channel.lower(), PRESETS["default"])


def get_keywords(channel: str) -> set[str]:
    """Returns global keywords merged with any streamer-specific ones."""
    rules = get_rules(channel)
    return GLOBAL_KEYWORDS | rules.extra_keywords
