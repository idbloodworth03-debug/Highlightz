"""
Rolling video buffer backed by FFmpeg HLS segmenter.

Uses streamlink to resolve the real HLS URL from a Twitch/YouTube page URL,
then feeds it into FFmpeg which writes a rolling window of .ts segment files.
When a clip is triggered, the relevant segments are concatenated into an MP4.
"""

import asyncio
import subprocess
import sys
import time
import shutil
import structlog
from pathlib import Path
from dataclasses import dataclass, field

from config.settings import settings

log = structlog.get_logger(__name__)

SEGMENT_DURATION = 2  # seconds per .ts segment


@dataclass
class BufferState:
    channel: str
    buffer_dir: Path
    process: asyncio.subprocess.Process | None = None
    start_time: float = field(default_factory=time.time)
    is_running: bool = False


class VideoBuffer:
    def __init__(self, channel: str, stream_url: str) -> None:
        self.channel = channel
        self.stream_url = stream_url
        self.buffer_dir = Path(settings.local_storage_path) / "buffers" / channel
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self._state = BufferState(channel=channel, buffer_dir=self.buffer_dir)
        self._reader_task: asyncio.Task | None = None

    async def _resolve_hls_url(self) -> str:
        """Use streamlink to get the best-quality HLS URL."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, "-m", "streamlink", "--stream-url", self.stream_url, "best"],
                capture_output=True,
                timeout=30,
            ),
        )
        hls_url = result.stdout.decode(errors="replace").strip()
        if not hls_url or result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"streamlink failed: {err}")
        log.info("hls_url_resolved", channel=self.channel)
        return hls_url

    async def start(self) -> None:
        hls_url = await self._resolve_hls_url()

        playlist = str(self.buffer_dir / "live.m3u8")
        segment_pattern = str(self.buffer_dir / "seg%05d.ts")
        max_segments = (settings.buffer_duration_seconds // SEGMENT_DURATION) + 5

        cmd = [
            settings.ffmpeg_path, "-y",
            "-i", hls_url,
            "-c", "copy",
            "-f", "hls",
            "-hls_time", str(SEGMENT_DURATION),
            "-hls_list_size", str(max_segments),
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", segment_pattern,
            playlist,
        ]

        log.info("starting_video_buffer", channel=self.channel)
        self._state.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._state.is_running = True
        self._reader_task = asyncio.create_task(self._stderr_reader())

    async def _stderr_reader(self) -> None:
        proc = self._state.process
        if not proc or not proc.stderr:
            return
        async for line in proc.stderr:
            decoded = line.decode(errors="replace").strip()
            if decoded and "error" in decoded.lower():
                log.warning("ffmpeg_stderr", channel=self.channel, msg=decoded)
        # FFmpeg exited
        if self._state.is_running:
            log.warning("ffmpeg_exited_unexpectedly", channel=self.channel)

    async def stop(self) -> None:
        self._state.is_running = False
        if self._state.process:
            try:
                self._state.process.terminate()
                await asyncio.wait_for(self._state.process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._state.process.kill()
                except ProcessLookupError:
                    pass
        if self._reader_task:
            self._reader_task.cancel()
        log.info("video_buffer_stopped", channel=self.channel)

    def get_segments_for_clip(self, pre_roll: int, post_roll: int, trigger_time: float | None = None) -> list[Path]:
        segments = sorted(self.buffer_dir.glob("seg*.ts"), key=lambda p: p.stat().st_mtime)
        ref = trigger_time or time.time()
        cutoff_start = ref - pre_roll
        cutoff_end = ref + post_roll + 2
        relevant = [s for s in segments if cutoff_start <= s.stat().st_mtime <= cutoff_end]
        return relevant

    async def extract_clip(
        self,
        output_path: Path,
        pre_roll: int | None = None,
        post_roll: int | None = None,
    ) -> Path:
        pre_roll = pre_roll or settings.clip_pre_roll_seconds
        post_roll = post_roll or settings.clip_post_roll_seconds

        trigger_time = time.time()  # anchor BEFORE sleeping so pre_roll cutoff stays correct

        # Wait for post_roll footage to accumulate in the buffer
        if post_roll > 0:
            await asyncio.sleep(post_roll + 1)

        segments = self.get_segments_for_clip(pre_roll, post_roll, trigger_time)
        if not segments:
            raise RuntimeError(f"No buffer segments for channel {self.channel}")

        concat_list = self.buffer_dir / "_concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{s.resolve()}'" for s in segments),
            encoding="utf-8",
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            settings.ffmpeg_path, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        concat_list.unlink(missing_ok=True)

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg clip failed: {stderr.decode(errors='replace')}")

        log.info("clip_extracted", channel=self.channel, output=str(output_path))
        return output_path

    def get_audio_level_db(self) -> float:
        """Return mean volume dBFS of the latest segment. Returns -100 on failure."""
        try:
            segments = sorted(self.buffer_dir.glob("seg*.ts"), key=lambda p: p.stat().st_mtime)
            if not segments:
                return -100.0
            latest = segments[-1]
            null_dev = "NUL" if sys.platform == "win32" else "/dev/null"
            result = subprocess.run(
                [settings.ffmpeg_path, "-i", str(latest), "-af", "volumedetect", "-f", "null", null_dev],
                capture_output=True, timeout=5,
            )
            for line in result.stderr.decode(errors="replace").splitlines():
                if "mean_volume" in line:
                    return float(line.split("mean_volume:")[-1].strip().split()[0])
        except Exception:
            pass
        return -100.0

    def cleanup(self) -> None:
        shutil.rmtree(self.buffer_dir, ignore_errors=True)
