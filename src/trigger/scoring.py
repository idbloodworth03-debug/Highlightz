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
# also adds AUDIO_SPIKE (38), VIEWER_SPIKE (7) and SILENCE_BURST (12) which VOD
# cannot measure; the VOD threshold is scaled down to compensate for the missing
# headroom rather than re-weighting the chat signals.
CHAT_WEIGHTS = {
    "CHAT_VELOCITY":     22,
    "KEYWORD":           12,
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


def velocity_score(
    spike_ratio: float,
    spike_multiplier: float,
    velocity: float,
    weighted_velocity: float,
    unique_senders: int,
) -> float:
    """
    Chat-velocity signal (0-1) with two boosts:
      1. Emote weight boost (max +30%): when hype emotes dominate the window,
         weighted_velocity > velocity and the ratio amplifies the score.
      2. Unique sender diversity boost (max +20%): more distinct users = more
         organic; ramps from 5 senders (no boost) to 20+ (full +20%).
    """
    score = min(spike_ratio / spike_multiplier, 1.0)

    if velocity > 0 and weighted_velocity > velocity:
        emote_ratio = weighted_velocity / velocity
        emote_boost = min((emote_ratio - 1.0) / 1.5, 0.30)
        score = min(score * (1.0 + emote_boost), 1.0)

    if unique_senders >= 5:
        diversity_boost = min((unique_senders - 5) / 75, 0.20)
        score = min(score * (1.0 + diversity_boost), 1.0)

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
