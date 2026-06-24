"""
Clip processor tests — cover the Twitch Helix path that runs in production.

The processor no longer captures/uploads video; it creates a clip on the
platform via the user's OAuth token (see src/processor/clip_processor.py). These
tests pin the exact failure modes seen in prod logs:
  - create_clip → None  (Twitch rejected the POST, e.g. "Channel offline.")
  - get_clip    → None  (202 accepted but the capture never materialized)
  - no valid token
and the happy path. post_roll=0 so process() doesn't sleep.
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.processor.clip_processor import ClipProcessor
from src.queue.job_queue import ClipJob


def _job(**kw) -> ClipJob:
    base = dict(
        channel="stableronaldo",
        platform="twitch",
        trigger_score=85.0,
        trigger_signals=[],
        chat_snapshot=[],
        post_roll=0,
        user_id="user123",
    )
    base.update(kw)
    return ClipJob(**base)


@pytest.mark.asyncio
async def test_twitch_happy_path_sets_url_and_pending():
    job = _job()
    with patch("src.processor.clip_processor.user_store.get_valid_twitch_token",
               new=AsyncMock(return_value="tok")), \
         patch("src.processor.clip_processor.twitch_clips.resolve_broadcaster_id",
               new=AsyncMock(return_value="267160288")), \
         patch("src.processor.clip_processor.twitch_clips.create_clip",
               new=AsyncMock(return_value="SlugABC")), \
         patch("src.processor.clip_processor.twitch_clips.get_clip",
               new=AsyncMock(return_value={"id": "SlugABC",
                                           "url": "https://clips.twitch.tv/SlugABC",
                                           "embed_url": "https://clips.twitch.tv/embed?clip=SlugABC",
                                           "thumbnail_url": "https://x/thumb.jpg",
                                           "duration": 30.0})):
        processor = ClipProcessor()
        meta = await processor.process(job)

    assert meta.status == "pending"
    assert meta.twitch_url == "https://clips.twitch.tv/SlugABC"
    assert meta.twitch_clip_id == "SlugABC"
    assert meta.duration_seconds == 30.0


@pytest.mark.asyncio
async def test_create_clip_failure_raises():
    """The exact overnight failure: Twitch rejects POST /clips (Channel offline)."""
    job = _job()
    with patch("src.processor.clip_processor.user_store.get_valid_twitch_token",
               new=AsyncMock(return_value="tok")), \
         patch("src.processor.clip_processor.twitch_clips.resolve_broadcaster_id",
               new=AsyncMock(return_value="267160288")), \
         patch("src.processor.clip_processor.twitch_clips.create_clip",
               new=AsyncMock(return_value=None)):
        processor = ClipProcessor()
        with pytest.raises(RuntimeError, match="Twitch clip creation failed"):
            await processor.process(job)


@pytest.mark.asyncio
async def test_clip_never_materializes_raises():
    """202 accepted but get_clip never returns a queryable clip → must fail, not
    fabricate a dead clips.twitch.tv link."""
    job = _job()
    with patch("src.processor.clip_processor.user_store.get_valid_twitch_token",
               new=AsyncMock(return_value="tok")), \
         patch("src.processor.clip_processor.twitch_clips.resolve_broadcaster_id",
               new=AsyncMock(return_value="267160288")), \
         patch("src.processor.clip_processor.twitch_clips.create_clip",
               new=AsyncMock(return_value="SlugABC")), \
         patch("src.processor.clip_processor.twitch_clips.get_clip",
               new=AsyncMock(return_value=None)):
        processor = ClipProcessor()
        with pytest.raises(RuntimeError, match="never finished processing"):
            await processor.process(job)


@pytest.mark.asyncio
async def test_no_token_raises_before_any_api_call():
    job = _job()
    resolve = AsyncMock(return_value="267160288")
    with patch("src.processor.clip_processor.user_store.get_valid_twitch_token",
               new=AsyncMock(return_value=None)), \
         patch("src.processor.clip_processor.twitch_clips.resolve_broadcaster_id",
               new=resolve):
        processor = ClipProcessor()
        with pytest.raises(RuntimeError, match="No valid Twitch token"):
            await processor.process(job)
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_platform_raises():
    job = _job(platform="youtube")
    processor = ClipProcessor()
    with pytest.raises(RuntimeError, match="only supports Twitch and Kick"):
        await processor.process(job)
