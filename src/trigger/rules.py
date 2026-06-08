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
    # ── General / unknown ─────────────────────────────────────────────────────
    "default": ChannelRules(),

    # ── Small / growing streamers (< ~1 000 average viewers) ─────────────────
    # Low absolute chat volume means spikes are smaller; lower multiplier and
    # threshold ensure genuine moments still get captured.
    "small": ChannelRules(
        velocity_multiplier=1.5,
        trigger_threshold=48.0,
        pre_roll=50,
        post_roll=20,
        cooldown_seconds=45,
        extra_keywords=frozenset({
            "pog", "pogchamp", "clip it", "clip that", "lets go", "let's go",
            "hype", "insane", "crazy", "no way", "omg", "bro", "clutch",
            "w", "l", "based", "goated",
        }),
    ),

    # ── FPS games (Valorant, CS2, Warzone, Apex, Overwatch) ──────────────────
    # Fast action windows — shorter pre-roll so the clip starts tight on the play.
    "fps": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=58.0,
        pre_roll=35,
        post_roll=15,
        cooldown_seconds=50,
        extra_keywords=frozenset({
            "ace", "clutch", "headshot", "spray", "one-tap", "noscope",
            "no scope", "knife", "flick", "quickscope", "wallbang", "global",
            "ranked", "banger", "outplay", "diff", "goated", "cracked",
            "insane", "highlight", "multikill",
        }),
    ),

    # ── Strategy / chess / slow-paced games ──────────────────────────────────
    # Very sparse chat — only genuine crowd eruptions should fire.
    "chess": ChannelRules(
        velocity_multiplier=4.0,
        trigger_threshold=65.0,
        cooldown_seconds=90,
        extra_keywords=frozenset({
            "brilliancy", "blunder", "checkmate", "resign", "trap", "gambit",
            "sac", "sacrifice", "fork", "pin", "skewer", "en passant",
            "promotion", "immortal", "queen", "rook", "knight", "bishop",
            "endgame", "opening", "theory",
        }),
    ),

    # ── IRL / outdoor / in-person streams ────────────────────────────────────
    # Audio spikes (crowd, environment) matter more than chat velocity.
    "irl": ChannelRules(
        velocity_multiplier=3.0,
        trigger_threshold=55.0,
        pre_roll=50,
        post_roll=20,
        cooldown_seconds=70,
        extra_keywords=frozenset({
            "omg", "no way", "crazy", "wild", "wtf", "based", "npc",
            "police", "fight", "interaction", "public", "street", "crowd",
            "arrested", "security", "run", "clip this",
        }),
    ),

    # ── Variety / Just Chatting / reaction streams ────────────────────────────
    "variety": ChannelRules(
        velocity_multiplier=2.0,
        trigger_threshold=55.0,
        pre_roll=45,
        post_roll=20,
        cooldown_seconds=60,
        extra_keywords=frozenset({
            "lmao", "lmfao", "omg", "wtf", "no way", "pog", "hype",
            "clip it", "clip that", "based", "goated", "crazy", "wild",
            "ratio", "copium", "cope", "w", "l", "bro moment",
        }),
    ),

    # ── MOBA games (League of Legends, Dota 2, SMITE, HotS) ─────────────────
    "moba": ChannelRules(
        velocity_multiplier=2.8,
        trigger_threshold=58.0,
        pre_roll=35,
        post_roll=20,
        cooldown_seconds=50,
        extra_keywords=frozenset({
            "pentakill", "penta", "quadra", "triple kill", "ace", "baron",
            "dragon", "teamfight", "outplay", "diff", "inting", "gank",
            "smite", "engage", "wombo combo", "rampage", "mega kill",
            "ultra kill", "divine rapier", "roshan",
        }),
    ),

    # ── Casino / gambling / case opening streams ──────────────────────────────
    "casino": ChannelRules(
        velocity_multiplier=1.8,
        trigger_threshold=50.0,
        pre_roll=35,
        post_roll=25,
        cooldown_seconds=40,
        extra_keywords=frozenset({
            "jackpot", "big win", "massive win", "bonus", "retrigger",
            "max win", "bust", "profit", "rare", "covert", "contraband",
            "stattrak", "gem", "unbox", "case", "insane drop", "crazy drop",
            "knife", "gloves", "fade", "doppler",
        }),
    ),

    # ── Sports watching / co-streams ─────────────────────────────────────────
    "sports": ChannelRules(
        velocity_multiplier=1.8,
        trigger_threshold=50.0,
        pre_roll=40,
        post_roll=25,
        cooldown_seconds=50,
        extra_keywords=frozenset({
            "goal", "score", "touchdown", "home run", "three pointer",
            "slam dunk", "penalty", "foul", "offside", "red card",
            "yellow card", "hat trick", "mvp", "clutch", "overtime",
            "buzzer beater", "game winner", "playoff", "championship",
        }),
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


def get_rules(channel: str, preset: str = "default") -> ChannelRules:
    """Return rules for a channel, checking overrides first, then the chosen preset."""
    if channel.lower() in CHANNEL_OVERRIDES:
        return CHANNEL_OVERRIDES[channel.lower()]
    return PRESETS.get(preset, PRESETS["default"])
