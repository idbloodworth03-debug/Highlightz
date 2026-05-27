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

        trimmed_path = await self._trim_leading_silence(tmp_path)

        meta.duration_seconds = await self._probe_duration(trimmed_path)
        meta.storage_url = await self._storage.upload(trimmed_path, meta.id)
        meta.status = "pending"

        trimmed_path.unlink(missing_ok=True)
        log.info("clip_ready", clip_id=meta.id, url=meta.storage_url)
        return meta

    @staticmethod
    async def _trim_leading_silence(path: Path) -> Path:
        """
        Detect leading silence and re-cut the clip to start when audio kicks in.
        Returns the original path (possibly replaced in-place) or a trimmed copy.
        Skips trimming if leading silence is under 2s or detection fails.
        """
        import shutil
        ffmpeg = settings.ffmpeg_path
        MIN_TRIM = 2.0   # don't bother trimming less than 2s
        MAX_TRIM = 35.0  # never cut more than 35s (keeps safety margin)

        try:
            # silencedetect: noise floor -45dB, min silence duration 0.3s
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-i", str(path),
                "-af", "silencedetect=n=-45dB:d=0.3",
                "-f", "null", "-",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stderr.decode(errors="replace")
        except Exception as exc:
            log.warning("silencedetect_failed", path=str(path), error=str(exc))
            return path

        # Parse first silence_end — that's when audio first appears
        trim_start = 0.0
        for line in output.splitlines():
            if "silence_end" in line:
                try:
                    trim_start = float(line.split("silence_end:")[-1].strip().split()[0])
                except ValueError:
                    pass
                break

        if trim_start < MIN_TRIM or trim_start > MAX_TRIM:
            return path

        out_path = path.with_name(path.stem + "_trim.mp4")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y",
                "-ss", str(trim_start),
                "-i", str(path),
                "-c", "copy",
                str(out_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0 and out_path.exists():
                path.unlink(missing_ok=True)
                log.info("silence_trimmed", trimmed_seconds=round(trim_start, 2), output=str(out_path))
                return out_path
        except Exception as exc:
            log.warning("silence_trim_failed", path=str(path), error=str(exc))
            out_path.unlink(missing_ok=True)

        return path

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
