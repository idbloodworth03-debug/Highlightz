"""
Clip processor: picks up a TriggerEvent, extracts the clip from the
video buffer, attaches metadata, uploads to storage, and saves to DB.
"""

import asyncio
import time
import structlog
from pathlib import Path

from config.settings import settings
from src.trigger.signals import TriggerEvent
from src.ingestion.video_buffer import VideoBuffer
from src.output.storage import StorageBackend
from src.queue.job_queue import ClipJob
from .metadata import ClipMetadata

log = structlog.get_logger(__name__)


class ClipProcessor:
    def __init__(self, storage: StorageBackend, buffers: dict[str, VideoBuffer]) -> None:
        self._storage = storage
        self._buffers = buffers    # channel -> VideoBuffer, managed by StreamWorker

    async def process(self, job: ClipJob) -> ClipMetadata:
        channel = job.channel
        buffer = self._buffers.get(channel)
        if not buffer:
            raise RuntimeError(f"No active buffer for channel '{channel}'")

        meta = ClipMetadata(
            id=job.clip_id,
            channel=channel,
            platform=job.platform,
            trigger_score=job.trigger_score,
            trigger_signals=job.trigger_signals,
            chat_snapshot=job.chat_snapshot,
            stream_title=job.stream_title,
            game=job.game,
            virality_score=job.virality_score,
            clip_title=job.clip_title,
        )

        tmp_path = Path(settings.local_storage_path) / "tmp" / f"{meta.id}.mp4"
        log.info("processing_clip", clip_id=meta.id, channel=channel)

        await buffer.extract_clip(
            output_path=tmp_path,
            pre_roll=job.pre_roll,
            post_roll=job.post_roll,
        )

        meta.duration_seconds = await self._probe_duration(tmp_path)
        meta.storage_url = await self._storage.upload(tmp_path, meta.id)
        meta.status = "pending"

        tmp_path.unlink(missing_ok=True)
        log.info("clip_ready", clip_id=meta.id, url=meta.storage_url)
        return meta

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        try:
            import shutil
            ffprobe = shutil.which("ffprobe") or settings.ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
            proc = await asyncio.create_subprocess_exec(
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return float(stdout.decode().strip())
        except Exception:
            return 0.0
