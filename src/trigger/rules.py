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
    #
    # 85 -> 75 (July 2026, viewer-clip evidence). 6905 viewer clips over 50h
    # gave 1316 crowd-validated moments; 675 multi-viewer moments produced no
    # clip of ours, and the biggest of those scored 85-100 against bars of
    # 51-58. They cleared the trigger threshold easily and were lost to
    # cooldown, because the override only rescues a moment once it beats 85.
    # Moments a dozen people clip are exactly what this override exists for.
    #
    # Volume risk is bounded, not open-ended: emergency_cooldown_seconds still
    # forces 60s between override fires, so the worst case is one extra clip
    # per channel per minute during sustained hype.
    emergency_threshold: float = 75.0
    # Floor on how often the emergency override itself may fire. Without this, a
    # channel that parks ABOVE emergency_threshold (a long sustained hype segment)
    # breaks cooldown on every 1s evaluation tick and enqueues ~1 clip/second of
    # the same moment, flooding the queue. 60s ≈ the Twitch capture window, so
    # back-to-back emergency clips don't overlap into near-duplicates.
    emergency_cooldown_seconds: int = 60
    extra_keywords: frozenset = field(default_factory=frozenset)


# Preset profiles for common content types
#
# July 2026 volume pass (owner-directed): cooldowns halved and seed
# thresholds trimmed ~4-6 points across presets. Cooldowns were the real
# volume choke — e.g. "variety" allowed one clip per 10 minutes no matter
# how good the stream was, which read as "the bot isn't working". Halving
# them buys volume from moments that already cleared the quality bar. The
# hourly decay pulls existing channels toward the new (lower) seeds, so the
# loosening rolls out gently instead of as a hard reset.
PRESETS: dict[str, ChannelRules] = {
    # ── General / unknown ─────────────────────────────────────────────────────
    # Balanced defaults for unknown content. 40s pre-roll captures most action
    # windows; 3-minute cooldown prevents clip flooding.
    "default": ChannelRules(
        velocity_multiplier=2.5,
        trigger_threshold=63.0,
        pre_roll=40,
        post_roll=25,
        cooldown_seconds=120,
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
        post_roll=22,
        cooldown_seconds=180,
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
        trigger_threshold=61.0,
        pre_roll=22,
        post_roll=22,
        cooldown_seconds=120,
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
        trigger_threshold=56.0,
        pre_roll=15,
        post_roll=25,
        cooldown_seconds=150,
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
        post_roll=30,
        cooldown_seconds=480,
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
        trigger_threshold=60.0,
        pre_roll=10,
        post_roll=25,
        cooldown_seconds=300,
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
        trigger_threshold=62.0,
        pre_roll=38,
        post_roll=28,
        cooldown_seconds=180,
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
        trigger_threshold=54.0,
        pre_roll=18,
        post_roll=30,
        cooldown_seconds=120,
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
        trigger_threshold=58.0,
        pre_roll=6,
        post_roll=32,
        cooldown_seconds=300,
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
        post_roll=25,
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
        post_roll=25,
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


# ── automatic preset selection ────────────────────────────────────────────────
# Presets were only ever chosen by hand, from a dropdown, at the moment a
# channel was added. Most people leave it on Default, which means the tuning
# work in the table above almost never reached the streams it was written for —
# and the group it failed hardest were small channels, whose thin chat is
# exactly what "small" exists to compensate for.
#
# Twitch already tells us both facts we need (game category and concurrent
# viewers) in the same StreamInfo we fetch to start monitoring, so the pick can
# be made for the user instead.

# Concurrent viewers below which chat is too thin to score like a big channel.
# 50 is a judgement, not a measurement: it is roughly where a 15s window starts
# holding enough messages for a velocity ratio to mean something rather than be
# one person typing. Named so it can be moved when there is data to move it by.
SMALL_CHANNEL_VIEWERS = 50

# Twitch category -> preset. Matched case-insensitively on a normalised name, so
# "Counter-Strike 2" and "counter-strike 2" both land. Substring matching is
# deliberate: Twitch renames categories (Counter-Strike: GO -> Counter-Strike 2,
# FIFA -> EA Sports FC) and an exact table would silently rot at every rename.
GAME_PRESETS: tuple[tuple[str, str], ...] = (
    # FPS — fast, frequent, short reaction windows
    ("counter-strike", "fps"), ("valorant", "fps"), ("call of duty", "fps"),
    ("apex legends", "fps"), ("overwatch", "fps"), ("rainbow six", "fps"),
    ("fortnite", "fps"), ("pubg", "fps"), ("battlefield", "fps"),
    ("halo", "fps"), ("escape from tarkov", "fps"), ("destiny", "fps"),
    ("the finals", "fps"), ("delta force", "fps"), ("marvel rivals", "fps"),
    # MOBA — long setups, payoff at the end of a fight, so a long pre-roll
    ("league of legends", "moba"), ("dota", "moba"), ("smite", "moba"),
    ("teamfight tactics", "moba"), ("heroes of the storm", "moba"),
    # Strategy / slow — chat barely moves, so the multiplier is high
    ("chess", "chess"), ("starcraft", "chess"), ("age of empires", "chess"),
    ("civilization", "chess"), ("total war", "chess"), ("hearthstone", "chess"),
    ("magic: the gathering", "chess"), ("go", "chess"),
    # Gambling — bursty, all-or-nothing reactions
    ("slots", "casino"), ("virtual casino", "casino"), ("poker", "casino"),
    # IRL — long, meandering, big post-roll
    ("irl", "irl"), ("travel", "irl"), ("outdoors", "irl"),
    ("food & drink", "irl"), ("special events", "irl"), ("asmr", "irl"),
    # Watching sport — the reaction lands almost before the play does
    ("sports", "sports"), ("football", "sports"), ("basketball", "sports"),
    ("boxing", "sports"), ("mma", "sports"), ("wrestling", "sports"),
    # Talk / variety
    ("just chatting", "variety"), ("art", "variety"), ("music", "variety"),
    ("games + demos", "variety"), ("talk shows", "variety"),
    ("makers & crafting", "variety"),
)


def preset_for_game(game: str) -> str | None:
    """Preset for a Twitch category name, or None if we have no opinion."""
    g = (game or "").strip().lower()
    if not g:
        return None
    for needle, preset in GAME_PRESETS:
        if needle in g:
            return preset
    return None


def auto_preset(game: str = "", viewer_count: int = 0) -> str:
    """Best preset for a channel from what Twitch tells us about it.

    SIZE BEATS GENRE, deliberately. A 20-viewer Valorant channel has more in
    common with another 20-viewer channel than with a 20,000-viewer Valorant
    channel: the binding problem is that a 15s window holds three messages, not
    that the game is fast. "small" drops the bar 63 -> 50 and the spike
    multiplier 2.5 -> 1.6, which is what a thin chat needs; the genre presets
    mostly tune roll lengths, which matter far less when nothing is firing at
    all.

    Returns "default" rather than guessing when the category is unknown — an
    unfamiliar game is not evidence for any particular preset.
    """
    if 0 < viewer_count < SMALL_CHANNEL_VIEWERS:
        return "small"
    return preset_for_game(game) or "default"
