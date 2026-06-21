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
    # Score must reach this to break cooldown early — avoids missing huge moments
    # right after a smaller clip fires. Set to 101 to disable override entirely.
    emergency_threshold: float = 85.0
    extra_keywords: frozenset = field(default_factory=frozenset)


# Preset profiles for common content types
PRESETS: dict[str, ChannelRules] = {
    # ── General / unknown ─────────────────────────────────────────────────────
    # Balanced defaults for unknown content. 40s pre-roll captures most action
    # windows; 3-minute cooldown prevents clip flooding.
    "default": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=68.0,
        pre_roll=40,
        post_roll=15,
        cooldown_seconds=240,
        extra_keywords=frozenset({
            "pog", "pogchamp", "clip it", "clip that", "lets go",
            "hype", "insane", "no way", "clutch", "goated",
        }),
    ),

    # ── Small / growing streamers (< ~500 average viewers) ───────────────────
    # Chat is sparse; genuine spikes are smaller in absolute terms.
    # Low multiplier (1.3x) fires on modest relative spikes.
    # 5-minute cooldown prevents over-clipping thin chat.
    # Short 20s pre-roll avoids capturing dead air before the moment.
    "small": ChannelRules(
        velocity_multiplier=1.6,
        trigger_threshold=50.0,
        pre_roll=20,
        post_roll=12,
        cooldown_seconds=360,
        extra_keywords=frozenset({
            "pog", "pogchamp", "clip it", "clip that", "lets go",
            "insane", "no way", "clutch", "goated", "holy",
        }),
    ),

    # ── FPS games (Valorant, CS2, Warzone, Apex, Overwatch) ──────────────────
    # Chat peaks ~8s after the kill; spike is sharp and brief (15-30s window).
    # 22s pre-roll captures the full play leading up to the clutch.
    # 3-minute cooldown — major plays are at least a round apart.
    "fps": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=65.0,
        pre_roll=22,
        post_roll=12,
        cooldown_seconds=210,
        extra_keywords=frozenset({
            "ace", "clutch", "headshot", "one tap", "noscope",
            "no scope", "flick", "quickscope", "wallbang",
            "outplay", "cracked", "multikill", "spraydown", "awp",
            "top frag", "retake", "defuse",
        }),
    ),

    # ── Strategy / chess / slow-paced games ──────────────────────────────────
    # Chat reacts within 1-3s of a blunder/brilliancy — quickest reaction of
    # all genres. Very sparse baseline: need 4.5x spike to confirm a real event.
    # 15s pre-roll is enough; 4-minute cooldown prevents multiple clips per game.
    "chess": ChannelRules(
        velocity_multiplier=4.5,
        trigger_threshold=58.0,
        pre_roll=15,
        post_roll=15,
        cooldown_seconds=240,
        extra_keywords=frozenset({
            "brilliancy", "blunder", "checkmate", "resign", "trap", "gambit",
            "sac", "sacrifice", "fork", "pin", "skewer", "en passant",
            "promotion", "immortal", "queen", "rook", "knight", "bishop",
            "endgame", "opening", "theory", "brilliant", "inaccuracy",
            "mistake", "0-1", "1-0", "draw", "stalemate", "zugzwang",
            "desperado", "tactics", "puzzle",
        }),
    ),

    # ── IRL / outdoor / in-person streams ────────────────────────────────────
    # Audio is the primary signal — crowd noise, altercations, surprises.
    # Chat reacts 2-5s after the incident; spikes are sharp but brief.
    # 20s pre-roll captures context; long 15-minute cooldown for organic pacing.
    "irl": ChannelRules(
        velocity_multiplier=3.5,
        trigger_threshold=52.0,
        pre_roll=20,
        post_roll=18,
        cooldown_seconds=900,
        extra_keywords=frozenset({
            "omg", "no way", "crazy", "wild", "wtf", "based", "npc",
            "police", "fight", "interaction", "public", "street", "crowd",
            "arrested", "security", "run", "clip this", "bro what",
            "call the cops", "they said", "unscripted", "real life",
            "someone call", "what is happening", "oh my god",
        }),
    ),

    # ── Variety / Just Chatting / reaction streams ────────────────────────────
    # Silence-burst is the top predictor here: the pause before a punchline.
    # 10s pre-roll captures the setup; 10-minute cooldown for natural pacing.
    "variety": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=64.0,
        pre_roll=10,
        post_roll=15,
        cooldown_seconds=600,
        extra_keywords=frozenset({
            "omg", "no way", "pog", "clip it", "clip that",
            "goated", "wild", "unhinged", "sheesh", "no shot",
        }),
    ),

    # ── MOBA games (League of Legends, Dota 2, SMITE, HotS) ─────────────────
    # Teamfights build over 30-90s; chat peaks twice (engage + result).
    # 38s pre-roll starts before the engage; 4-minute cooldown per objective.
    "moba": ChannelRules(
        velocity_multiplier=2.8,
        trigger_threshold=66.0,
        pre_roll=38,
        post_roll=18,
        cooldown_seconds=300,
        extra_keywords=frozenset({
            "pentakill", "penta", "quadra", "triple kill", "ace", "baron",
            "dragon", "teamfight", "outplay", "diff", "inting", "gank",
            "smite", "engage", "wombo combo", "rampage", "mega kill",
            "ultra kill", "divine rapier", "roshan", "nexus", "ancient",
            "inhibitor", "ward", "vision", "courier", "buyback", "aegis",
            "first blood", "shutdown", "stolen", "backdoor",
        }),
    ),

    # ── Casino / gambling / case opening streams ──────────────────────────────
    # Wins are visible on screen; chat reacts within 3-5s.
    # 18s pre-roll captures the spin/open; 3-minute cooldown prevents spam.
    "casino": ChannelRules(
        velocity_multiplier=1.8,
        trigger_threshold=56.0,
        pre_roll=18,
        post_roll=20,
        cooldown_seconds=180,
        extra_keywords=frozenset({
            "jackpot", "big win", "massive win", "bonus", "retrigger",
            "max win", "bust", "profit", "rare", "covert", "contraband",
            "stattrak", "gem", "unbox", "case", "insane drop", "crazy drop",
            "knife", "gloves", "fade", "doppler", "x multiplier",
            "feature", "free spins", "scatter", "wild", "rng",
            "house edge", "all in", "up big", "down bad", "busted",
        }),
    ),

    # ── Sports watching / co-streams ─────────────────────────────────────────
    # Goal/score chat spike is the sharpest of all genres (0-2s after event).
    # 6s pre-roll only — anything longer captures dead play time.
    # Post-roll 22s for celebrations; 10-minute cooldown by game pace.
    "sports": ChannelRules(
        velocity_multiplier=3.5,
        trigger_threshold=62.0,
        pre_roll=6,
        post_roll=22,
        cooldown_seconds=600,
        extra_keywords=frozenset({
            "goal", "score", "touchdown", "home run", "three pointer",
            "slam dunk", "penalty", "foul", "offside", "red card",
            "yellow card", "hat trick", "mvp", "clutch", "overtime",
            "buzzer beater", "game winner", "playoff", "championship",
            "golazo", "screamer", "howler", "og", "own goal",
            "var", "checked", "disallowed", "injury time", "last minute",
            "walk off", "pinch hit", "grand slam", "sack", "interception",
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
