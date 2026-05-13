import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from src.processor.clip_processor import ClipProcessor
from src.queue.job_queue import ClipJob


@pytest.mark.asyncio
async def test_process_raises_when_no_buffer():
    storage = AsyncMock()
    processor = ClipProcessor(storage=storage, buffers={})

    job = ClipJob(
        channel="missing_channel",
        platform="twitch",
        trigger_score=0.8,
        trigger_signals=[],
        chat_snapshot=[],
    )

    with pytest.raises(RuntimeError, match="No active buffer"):
        await processor.process(job)


@pytest.mark.asyncio
async def test_process_calls_storage_upload():
    mock_buffer = AsyncMock()
    mock_buffer.extract_clip = AsyncMock(return_value=Path("/tmp/fake.mp4"))

    storage = AsyncMock()
    storage.upload = AsyncMock(return_value="https://example.com/clip.mp4")

    with patch("src.processor.clip_processor.ClipProcessor._probe_duration", return_value=35.0):
        processor = ClipProcessor(storage=storage, buffers={"mychannel": mock_buffer})

        job = ClipJob(
            channel="mychannel",
            platform="twitch",
            trigger_score=0.9,
            trigger_signals=[],
            chat_snapshot=["clip it", "poggers"],
            post_roll=0,
        )

        meta = await processor.process(job)

    assert meta.storage_url == "https://example.com/clip.mp4"
    assert meta.duration_seconds == 35.0
    assert meta.status == "pending"
    storage.upload.assert_called_once()
