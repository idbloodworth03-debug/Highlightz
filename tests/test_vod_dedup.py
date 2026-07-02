"""
Regression tests for VOD moment de-duplication.

The VOD scanner used to fire one moment every COOLDOWN seconds for as long as
chat stayed above threshold, so a single long sustained-hype segment produced a
stack of near-identical clips (same VOD thumbnail, timestamps 60s apart). The
scan now treats a contiguous above-threshold stretch as ONE moment anchored at
its peak, while genuinely separated spikes still produce separate moments.
"""

import asyncio
from unittest.mock import patch

import pytest

from src.vod import analyzer


@pytest.fixture(autouse=True)
def no_profile(monkeypatch):
    """Pin these tests to preset calibration: no profile file I/O, and the
    threshold matches the pre-profile-aware behavior they were written for."""
    async def _none(user_id, channel):
        return None
    monkeypatch.setattr(analyzer, "_load_profile", _none)


_INFO = {
    "id": "1", "title": "T", "channel": "chan", "game": "G",
    "duration": 0.0, "thumbnail_url": "http://thumb", "url": "u",
}


async def _scan(messages: list[dict], duration: float) -> list[dict]:
    info = {**_INFO, "duration": duration}
    moments: list[dict] = []

    async def on_moment(m):
        moments.append(m)

    async def noop(*a, **k):
        pass

    with patch.object(analyzer, "_get_app_token", return_value="tok"), \
         patch.object(analyzer, "fetch_vod_info", return_value=info), \
         patch.object(analyzer, "fetch_vod_chat", return_value=messages):
        await analyzer.run_vod_analysis("1", "chan", "balanced", "u",
                                        noop, on_moment, noop, noop)
    return moments


def _calm(start: int, end: int) -> list[dict]:
    return [{"offset": float(t), "text": "just chatting normally here",
             "author": f"b{t % 15}"} for t in range(start, end)]


def _hype(start: int, end: int) -> list[dict]:
    out = []
    for t in range(start, end):
        for k in range(12):  # ~12 msgs/sec, short frantic bursts, many users
            out.append({"offset": float(t), "text": "LETSGO",
                        "author": f"h{(t * 12 + k) % 200}"})
    return out


@pytest.mark.asyncio
async def test_sustained_run_collapses_to_one_moment():
    # 5 min calm, then a single 5-min sustained hype run. Pre-fix this fired one
    # clip per 60s COOLDOWN (~5 dupes); now it must be exactly one, anchored in
    # the hype run rather than the calm baseline.
    msgs = sorted(_calm(0, 300) + _hype(300, 600), key=lambda m: m["offset"])
    moments = await _scan(msgs, duration=620.0)
    assert len(moments) == 1
    assert 300 <= moments[0]["offset_seconds"] <= 600


@pytest.mark.asyncio
async def test_separated_spikes_stay_distinct():
    # Two short spikes 300s apart with calm chat between (score drops below
    # threshold) must still produce two separate moments.
    msgs = sorted(_calm(0, 700) + _hype(200, 220) + _hype(500, 520),
                  key=lambda m: m["offset"])
    moments = await _scan(msgs, duration=720.0)
    assert len(moments) == 2
    offs = sorted(m["offset_seconds"] for m in moments)
    assert offs[0] < 250 and offs[1] > 450


@pytest.mark.asyncio
async def test_hype_running_to_end_of_vod_is_flushed():
    # A run still open at the final second must be emitted, not dropped.
    msgs = sorted(_calm(0, 300) + _hype(300, 400), key=lambda m: m["offset"])
    moments = await _scan(msgs, duration=400.0)
    assert len(moments) == 1
    assert moments[0]["offset_seconds"] >= 300
