"""
Per-channel rule overrides. Falls back to global settings when not defined.
Rules are loaded from a simple dict so they can later be persisted to DB.
"""

from dataclasses import dataclass, field


@dataclass
class ChannelRules:
    velocity_multiplier: float = 2.0   # how many x above avg to score 1.0
    trigger_threshold: float = 60.0
    pre_roll: int = 45
    post_roll: int = 15
    cooldown_seconds: int = 60         # min seconds between clips
    extra_keywords: frozenset = field(default_factory=frozenset)


# Preset profiles for common content types
PRESETS: dict[str, ChannelRules] = {
    "default": ChannelRules(),
    "chess": ChannelRules(
        velocity_multiplier=4.0,
    ),
    "fps": ChannelRules(
        velocity_multiplier=2.5,
    ),
    "irl": ChannelRules(
        velocity_multiplier=3.5,
        pre_roll=45,
        post_roll=15,
    ),
}

# Channel-specific overrides: channel_name -> ChannelRules
# trigger_threshold is overridden by the adaptive StreamerProfile once loaded.
CHANNEL_OVERRIDES: dict[str, ChannelRules] = {
    # LoL Hecarim OTP — loud/reactive, frequent short spikes.
    # Pre-seeded profile in seed_profiles/dantes.json
    "dantes": ChannelRules(
        velocity_multiplier=2.2,
        trigger_threshold=53.0,
        pre_roll=45,
        post_roll=15,
        cooldown_seconds=90,
        extra_keywords=frozenset({
            "hecarim", "heca", "penta", "pentakill", "challenger",
            "outplay", "diff", "inting", "smite", "goated", "carried",
            "tilted", "villain", "baron", "gank", "based", "grandmaster",
        }),
    ),
    # CS2 streamer — case openings, esports co-streams, skin analysis.
    # Pre-seeded profile in seed_profiles/ohnepixel.json
    "ohnepixel": ChannelRules(
        velocity_multiplier=2.0,
        trigger_threshold=56.0,
        pre_roll=45,
        post_roll=15,
        cooldown_seconds=90,
        extra_keywords=frozenset({
            "knife", "karambit", "butterfly", "bayonet", "fade", "doppler",
            "marble", "lore", "emerald", "vanilla", "rare", "covert",
            "contraband", "bluegem", "gem", "float", "pattern", "stattrak",
            "s1mple", "niko", "ace", "unbox", "case", "key",
        }),
    ),
    # R6S — loud reactive stream, massive viewership, rage/clutch clips dominate.
    # Pre-seeded profile in seed_profiles/jynxzi.json
    "jynxzi": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=51.0,
        pre_roll=45,
        post_roll=20,
        cooldown_seconds=90,
        extra_keywords=frozenset({
            # R6S gameplay
            "ace", "clutch", "wallbang", "headshot", "drone", "plant",
            "defuser", "operator", "ranked", "siege", "r6",
            # Operators
            "jager", "ash", "thermite", "vigil", "valkyrie", "maestro",
            "kapkan", "thorn", "nokk", "doc", "rook", "echo", "lion",
            "finka", "frost", "buck", "twitch",
            # Jynxzi chat slang / hype words
            "mid", "cooked", "unreal", "diff", "widow", "cry",
            "goated", "carried", "based", "no shot",
        }),
    ),
}


def get_rules(channel: str) -> ChannelRules:
    return CHANNEL_OVERRIDES.get(channel.lower(), PRESETS["default"])
