"""
Shared chat-signal scoring primitives.

Both the live trigger engine (src/trigger/engine.py) and the VOD analyzer
(src/vod/analyzer.py) score chat through these functions so the two paths can
never drift. Live additionally layers audio / viewer / silence signals on top;
VOD has only chat, but every chat-derived signal is computed identically here:
emote-weighted velocity, unique-sender diversity, emote homogeneity (crowdspeak),
keyword density, and sentiment.

All functions are pure and return a 0.0-1.0 signal value.
"""

# Base per-signal weights for the chat signals, shared by live and VOD so the
# relative contribution of each chat signal is identical on both paths. Live
# also adds AUDIO_SPIKE (38), VIEWER_SPIKE (15) and SILENCE_BURST (12) which VOD
# cannot measure; the VOD threshold is scaled down to compensate for the missing
# headroom rather than re-weighting the chat signals.
CHAT_WEIGHTS = {
    "CHAT_VELOCITY":     22,
    # Cut 12 → 4 (July 2026 training-log analysis, n=806): keyword-led clips
    # went 0/91 approved and keyword AUC vs outcome was 0.51 (coin flip) — the
    # keyword list predicts nothing for this content mix. Kept small (not 0)
    # so it can still support a moment, but it can no longer lead one.
    # Blast radius: this also lowers VOD's chat-only ceiling — the VOD
    # threshold scale in src/vod/analyzer.py was retuned 0.50 → 0.42 to match.
    "KEYWORD":            4,
    "SENTIMENT":          5,
    "EMOTE_HOMOGENEITY":  9,
}

# Multi-signal bonus: when this many chat signals are genuinely strong (> 0.5),
# multiply the raw score. Matches the live engine.
MULTI_SIGNAL_MIN_ACTIVE = 3
MULTI_SIGNAL_BONUS       = 1.2

# Clip-it community trip-wire: this many unique users requesting a clip within a
# short window is treated as community consensus and forces the score to at least
# CLIP_IT_FLOOR (which clears the emergency_threshold so it always clips).
CLIP_IT_MIN_SENDERS = 3
CLIP_IT_FLOOR       = 90.0

# Dry-spell recalibration: streamers change activity within a single stream
# (new game, a calmer just-chatting stretch, a different lobby), so a threshold
# that was right an hour ago can go stale and stop clipping entirely. When a
# *calibrated* channel goes this long with no trigger, nudge its trigger_threshold
# down one step so the bar re-adapts to the new normal, then reset the clock and
# repeat until a clip fires. This composes with — does not replace — accept/reject
# learning (which still moves the threshold per clip) and the hourly decay back
# toward the seed threshold (which pulls it up again once the lull ends). The
# floor keeps a genuinely dead stream from loosening into clipping random noise.
# Tuned after observing the tug-of-war on real channels: at -3/10min the
# dry-spell overpowered reject feedback (+2/reject) and dragged busy channels to
# the old floor of 40, where the bar is low enough that weak, marginal moments
# fire constantly — the "clipping poorly" failure mode. A floor of 52 kept a
# recalibrated channel selective, and -2/15min lets the user's approve/reject
# signal win the argument while still un-sticking a genuinely stale threshold.
# Floor raised 52 → 60 (July 2026 training-log analysis, n=806): no approved
# clip has ever scored below 60 (keepers average 90.8), and a floor of 60
# would have suppressed 82 junk clips with zero keeper loss — below 60 the
# formula demonstrably produces only rejects.
DRY_SPELL_SECS            = 900.0   # 15 min with no trigger → recalibrate
DRY_SPELL_THRESHOLD_STEP  = 2.0     # lower trigger_threshold by this each dry cycle
DRY_SPELL_THRESHOLD_FLOOR = 60.0    # dry-spell alone never lowers below this


# Message-length collapse: mean message length (chars) at/below this during an
# active window reads as frantic short-burst hype ("OMG", "LETSGO", emotes).
LENGTH_COLLAPSE_CHARS = 12.0


def velocity_score(
    spike_ratio: float,
    spike_multiplier: float,
    velocity: float,
    weighted_velocity: float,
    unique_senders: int,
    acceleration: float = 1.0,
    avg_message_length: float | None = None,
) -> float:
    """
    Chat-velocity signal (0-1). Every adjustment is a *boost* — it can only
    raise the score, never lower it — so adding them cannot reduce clips on a
    healthy stream:
      1. Emote weight boost (max +30%): hype emotes dominate the window.
      2. Unique sender diversity boost (max +20%): more distinct users = organic.
      3. Acceleration boost (max +15%): chat still speeding up (the ramp into a
         peak) — catches the moment a beat earlier than absolute velocity.
      4. Length-collapse boost (max +10%): messages go short and frantic during
         real hype; only applies when chat is actually moving (score > 0).
    """
    score = min(spike_ratio / spike_multiplier, 1.0)

    if velocity > 0 and weighted_velocity > velocity:
        emote_ratio = weighted_velocity / velocity
        emote_boost = min((emote_ratio - 1.0) / 1.5, 0.30)
        score = min(score * (1.0 + emote_boost), 1.0)

    if unique_senders >= 5:
        diversity_boost = min((unique_senders - 5) / 75, 0.20)
        score = min(score * (1.0 + diversity_boost), 1.0)

    # Derivative of chat speed: ramp from 1.2x (no boost) to 2.0x (full +15%).
    if acceleration > 1.2:
        accel_boost = min((acceleration - 1.2) / 0.8, 1.0) * 0.15
        score = min(score * (1.0 + accel_boost), 1.0)

    # Length collapse: only meaningful while chat is actually moving.
    if score > 0 and avg_message_length is not None and 0 < avg_message_length <= LENGTH_COLLAPSE_CHARS:
        collapse_boost = min((LENGTH_COLLAPSE_CHARS - avg_message_length) / LENGTH_COLLAPSE_CHARS, 1.0) * 0.10
        score = min(score * (1.0 + collapse_boost), 1.0)

    return score


def emote_homogeneity_score(homogeneity: float) -> float:
    """
    Crowdspeak signal (0-1): fires when most of the window is the same emote.
    50% → 0.0 (ramp starts), 70% → 0.5, 100% → 1.0.
    """
    if homogeneity >= 0.7:
        score = 0.5 + (homogeneity - 0.7) / 0.6   # 0.7→0.5, 1.0→1.0
    elif homogeneity >= 0.5:
        score = (homogeneity - 0.5) / 0.4          # 0.5→0.0, 0.7→0.5
    else:
        score = 0.0
    return min(score, 1.0)


def keyword_score(
    keyword_hits: int,
    message_count: int,
    trigger_phrases: int,
    baseline_rate: float = 0.0,
) -> float:
    """
    Keyword-density signal (0-1). When a calibrated baseline rate is supplied
    (live engine with a profile), score relative to it; otherwise use the
    absolute fallback used by both cold-start live and VOD replay.
    """
    if message_count <= 0:
        return 0.0
    keyword_rate = keyword_hits / message_count
    trigger_bonus = min(trigger_phrases * 0.15, 0.3)
    if baseline_rate > 0:
        return min((keyword_rate / max(baseline_rate, 0.01)) * 0.5 + trigger_bonus, 1.0)
    return min(keyword_rate * 3 + trigger_bonus, 1.0)


def sentiment_score(avg_compound: float, baseline: float = 0.0) -> float:
    """
    Sentiment-intensity signal (0-1) from the mean absolute VADER compound.
    Relative to a calibrated baseline when supplied, else the absolute fallback.
    """
    if baseline > 0:
        return min(avg_compound / max(baseline, 0.01) * 0.5, 1.0)
    return min(avg_compound * 2.0, 1.0)
