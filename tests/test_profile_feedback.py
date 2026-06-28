"""
Regression test for the silently-dropped feedback bug.

The approve/reject endpoints used cache-only ProfileManager.get(), which returns
None whenever the channel's profile isn't currently loaded in memory (after a
deploy, or for a channel that isn't actively being monitored). That silently
dropped the approve/reject signal — skewing per-channel learning toward whatever
the user happened to do while the stream was live. load() always returns a
profile, so the feedback is always recorded.
"""

import asyncio

import pytest

from src.profiles.manager import ProfileManager


def test_get_is_cache_only_but_load_always_returns(tmp_path, monkeypatch):
    # Point the manager at an isolated dir so we don't touch real profiles.
    pm = ProfileManager.__new__(ProfileManager)
    pm._profiles_dir = tmp_path
    pm._cache = {}
    pm._lock = asyncio.Lock()
    pm._user_id = "u1"

    async def go():
        # Uncached channel: the old path (get) returns None → feedback dropped.
        assert await pm.get("jynxzi") is None
        # The fixed path (load) always returns a profile → feedback recordable.
        prof = await pm.load("jynxzi")
        assert prof is not None
        # Recording an approval now actually sticks.
        prof.record_clip(approved=True, signals=[{"type": "CHAT_VELOCITY", "value": 0.8}])
        assert prof.approved_clips == 1
        # And it's cached afterwards, so a later get() sees it too.
        assert await pm.get("jynxzi") is prof

    asyncio.run(go())


def test_approval_lowers_threshold_rejection_raises_it():
    # The threshold drift that the dropped-approval bug unbalanced: approvals must
    # pull the bar down (clip more), rejections push it up (clip less).
    from src.profiles.profile import StreamerProfile
    p = StreamerProfile(channel="x", trigger_threshold=60.0)
    p.record_clip(approved=True)
    assert p.trigger_threshold < 60.0
    p2 = StreamerProfile(channel="y", trigger_threshold=60.0)
    p2.record_clip(approved=False)
    assert p2.trigger_threshold > 60.0
