"""
Per-channel rule overrides. Falls back to global settings when not defined.
Rules are loaded from a simple dict so they can later be persisted to DB.
"""

from dataclasses import dataclass


@dataclass
class ChannelRules:
    velocity_multiplier: float = 2.0   # how many x above avg to score 1.0
    trigger_threshold: float = 60.0
    pre_roll: int = 45
    post_roll: int = 15
    cooldown_seconds: int = 60         # min seconds between clips


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
CHANNEL_OVERRIDES: dict[str, ChannelRules] = {}


def get_rules(channel: str) -> ChannelRules:
    return CHANNEL_OVERRIDES.get(channel, PRESETS["default"])
