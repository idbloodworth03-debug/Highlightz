"""
Tests for the VOD-scanner quality fixes: profile-aware calibration (the scan
uses the channel's LEARNED threshold/spike multiplier, not the cold preset) and
top-K ranking (only the best ~3 moments/hour reach the queue, not every
above-threshold run).
"""

import asyncio
from unittest.mock import patch

import pytest

from src.profiles.profile import StreamerProfile
from src.trigger.rules import ChannelRules
from src.vod import analyzer


# ── _vod_threshold: learned calibration wins over preset ──────────────────────

def _calibrated_profile(threshold=80.0, samples=100):
    p = StreamerProfile(channel="x", trigger_threshold=threshold)
    for _ in range(samples):
        p.update_velocity(5.0)
    return p


def test_threshold_uses_learned_profile_over_preset():
    rules = ChannelRules()          # preset threshold 60 → bar 25.2
    p = _calibrated_profile(threshold=80.0)
    thr, mult = analyzer._vod_threshold(p, rules)
    assert thr == 80.0 * 0.62       # heavily-rejected channel scans strict
    assert mult == p.velocity_spike_multiplier   # adaptive, not the preset


def test_threshold_falls_back_to_preset_without_profile():
    rules = ChannelRules()
    thr, mult = analyzer._vod_threshold(None, rules)
    assert thr == rules.trigger_threshold * 0.62
    assert mult == rules.velocity_multiplier


def test_uncalibrated_profile_uses_its_threshold_but_preset_multiplier():
    rules = ChannelRules()
    p = StreamerProfile(channel="x", trigger_threshold=52.0)   # <30 samples
    thr, mult = analyzer._vod_threshold(p, rules)
    assert thr == pytest.approx(52.0 * 0.62)
    assert mult == rules.velocity_multiplier


# ── _top_moments: rank and cap ─────────────────────────────────────────────────

def _m(off, score):
    return {"offset_seconds": off, "score": score}


def test_top_moments_keeps_best_in_chronological_order():
    moments = [_m(100, 31), _m(400, 55), _m(900, 33), _m(1400, 48),
               _m(1900, 30.5), _m(2400, 61), _m(2900, 34)]
    kept = analyzer._top_moments(moments, duration_secs=3600)   # cap = 3
    assert [m["score"] for m in kept] == [55, 48, 61]           # top-3 by score…
    assert [m["offset_seconds"] for m in kept] == [400, 1400, 2400]  # …in time order


def test_top_moments_scales_with_duration_and_caps_at_12():
    many = [_m(i * 100, 30 + i * 0.1) for i in range(60)]
    assert len(analyzer._top_moments(many, duration_secs=2 * 3600)) == 6   # 3/hr
    assert len(analyzer._top_moments(many, duration_secs=10 * 3600)) == 12  # hard cap


def test_top_moments_short_vod_min_three():
    moments = [_m(10, 31), _m(200, 40)]
    assert analyzer._top_moments(moments, duration_secs=600) == moments  # under cap


# ── integration: only survivors are emitted ────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_emits_only_top_moments(monkeypatch):
    async def _none(user_id, channel):
        return None
    monkeypatch.setattr(analyzer, "_load_profile", _none)

    # 20-min VOD (cap=3) with five separated same-shape hype bursts.
    msgs = [{"offset": float(t), "text": "just chatting normally here",
             "author": f"b{t % 15}"} for t in range(0, 1200)]
    for start in (300, 450, 600, 750, 900):
        for t in range(start, start + 15):
            for k in range(12):
                msgs.append({"offset": float(t), "text": "LETSGO",
                             "author": f"h{(t * 12 + k) % 150}"})
    msgs.sort(key=lambda m: m["offset"])

    info = {"id": "1", "title": "T", "channel": "chan", "game": "G",
            "duration": 1200.0, "thumbnail_url": "http://t", "url": "u"}
    emitted, done = [], []

    async def on_moment(m): emitted.append(m)
    async def on_done(ms): done.append(ms)
    async def noop(*a, **k): pass

    with patch.object(analyzer, "_get_app_token", return_value="tok"), \
         patch.object(analyzer, "fetch_vod_info", return_value=info), \
         patch.object(analyzer, "fetch_vod_chat", return_value=msgs):
        await analyzer.run_vod_analysis("1", "chan", "balanced", "u",
                                        noop, on_moment, on_done, noop)

    assert len(emitted) == 3                       # 5 found → top-3 kept
    assert emitted == done[0]                      # on_done gets the same survivors
    offs = [m["offset_seconds"] for m in emitted]
    assert offs == sorted(offs)                    # chronological emission
    # Survivors are fully annotated before emission (end-offset pass ran first).
    assert all("end_offset_seconds" in m for m in emitted)
