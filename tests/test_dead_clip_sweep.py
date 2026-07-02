"""
Tests for the dead-clip sweep: clips deleted on Twitch's side after capture
(mods on some channels mass-delete clips) must be removed from the store and
broadcast as clip_removed — and an API failure must never read as 'all gone'.
"""

import asyncio
import time

import pytest

from src.dashboard import api


def _clip(cid, slug, *, age=3600, platform="twitch", vod=False, user="u1"):
    return {
        "id": cid, "twitch_clip_id": slug, "platform": platform,
        "is_vod_moment": vod, "created_at": time.time() - age,
        "user_id": user, "channel": "caseoh_", "status": "pending",
    }


@pytest.fixture()
def sweep_env(monkeypatch):
    clips = {
        "alive":  _clip("alive", "SlugAlive"),
        "gone":   _clip("gone", "SlugGone"),
        "young":  _clip("young", "SlugYoung", age=60),          # too fresh — skip
        "vod":    {**_clip("vod", "", vod=True), "twitch_clip_id": ""},   # skip
        "kick":   _clip("kick", "SlugK", platform="kick"),      # skip
    }
    events = []

    async def fake_broadcast(msg, user_id=None):
        events.append((msg, user_id))

    monkeypatch.setattr(api, "_clips", clips)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    return clips, events


def test_removes_only_clips_twitch_no_longer_has(sweep_env):
    clips, events = sweep_env
    seen_slugs = {}

    async def fetcher(slugs):
        seen_slugs["q"] = set(slugs)
        return {"SlugAlive"}          # Twitch still has 'alive'; 'gone' vanished

    removed = asyncio.run(api.sweep_dead_twitch_clips(fetcher))
    assert removed == 1
    assert "gone" not in clips and "alive" in clips
    # Young / VOD / non-Twitch clips were never even queried
    assert seen_slugs["q"] == {"SlugAlive", "SlugGone"}
    # Realtime contract: the open tab learns about the removal
    assert events == [({"event": "clip_removed", "clip_id": "gone"}, "u1")]


def test_lookup_failure_removes_nothing(sweep_env):
    clips, events = sweep_env

    async def failing(slugs):
        return None                    # API blip — 'unknown', not 'all gone'

    removed = asyncio.run(api.sweep_dead_twitch_clips(failing))
    assert removed == 0
    assert set(clips) == {"alive", "gone", "young", "vod", "kick"}
    assert events == []


def test_no_candidates_is_a_noop(monkeypatch):
    monkeypatch.setattr(api, "_clips", {})

    async def boom(slugs):             # must never be called with no candidates
        raise AssertionError("fetcher called")

    assert asyncio.run(api.sweep_dead_twitch_clips(boom)) == 0
