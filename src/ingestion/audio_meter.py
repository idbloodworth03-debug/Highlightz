"""
AudioMeter — transient audio-loudness probe for the trigger engine.

Pulls an audio-only feed via streamlink → FFmpeg (raw PCM) → Python RMS.
FFmpeg outputs 8kHz mono s16le PCM to stdout; we compute dBFS directly
from the raw samples. No file writes, no regex parsing of filter output.
"""

import asyncio
import math
import os
import struct
import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_SILENCE_DB    = -100.0
_CHUNK_BYTES   = 3200   # 0.2s at 8kHz mono s16le (8000 × 2 bytes × 0.2s)
_RESTART_DELAY = 15


def _rms_db(data: bytes) -> float:
    """Convert raw s16le PCM bytes to dBFS. Returns -100 for silence."""
    n = len(data) // 2
    if n == 0:
        return _SILENCE_DB
    samples = struct.unpack_from(f"<{n}h", data[:n * 2])
    mean_sq = sum(s * s for s in samples) / n
    if mean_sq < 1.0:
        return _SILENCE_DB
    return max(_SILENCE_DB, 20.0 * math.log10(math.sqrt(mean_sq) / 32768.0))


class AudioMeter:
    def __init__(self, channel: str, stream_url: str) -> None:
        self.channel    = channel
        self.stream_url = stream_url
        self._level_db: float = _SILENCE_DB
        self._sl: asyncio.subprocess.Process | None = None
        self._ff: asyncio.subprocess.Process | None = None
        self._reader:   asyncio.Task | None = None
        self._sl_log:   asyncio.Task | None = None
        self._monitor:  asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._launch()
        self._monitor = asyncio.create_task(
            self._monitor_loop(), name=f"audiometer-monitor-{self.channel}"
        )
        log.info("audio_meter_started", channel=self.channel)

    async def _launch(self) -> None:
        r_fd, w_fd = os.pipe()
        try:
            # streamlink → write end of OS pipe
            self._sl = await asyncio.create_subprocess_exec(
                settings.streamlink_path,
                "--stdout",
                "--loglevel", "warning",
                self.stream_url,
                "audio_only,worst",
                stdout=w_fd,
                stderr=asyncio.subprocess.PIPE,
            )
            os.close(w_fd); w_fd = -1

            # FFmpeg reads from read end → outputs raw 8kHz mono PCM to stdout
            self._ff = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path,
                "-hide_banner", "-nostats",
                "-i", "pipe:0",
                "-vn",
                "-ac", "1",
                "-ar", "8000",
                "-f", "s16le",
                "pipe:1",
                stdin=r_fd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            os.close(r_fd); r_fd = -1

            self._reader = asyncio.create_task(
                self._read_loop(), name=f"audiometer-reader-{self.channel}"
            )
            self._sl_log = asyncio.create_task(
                self._log_sl_stderr(), name=f"audiometer-sl-log-{self.channel}"
            )
            log.info("audio_meter_pipeline_up", channel=self.channel)
        except Exception as exc:
            for fd in (r_fd, w_fd):
                if fd >= 0:
                    try: os.close(fd)
                    except OSError: pass
            log.error("audio_meter_launch_failed", channel=self.channel, error=str(exc))
            await self._kill_procs()

    async def _read_loop(self) -> None:
        if not self._ff or not self._ff.stdout:
            log.warning("audio_meter_no_stdout", channel=self.channel)
            return
        samples_read = 0
        try:
            while self._running:
                data = await self._ff.stdout.read(_CHUNK_BYTES)
                if not data:
                    log.warning("audio_meter_pcm_stream_ended", channel=self.channel,
                                chunks_read=samples_read)
                    break
                self._level_db = _rms_db(data)
                samples_read += 1
                if samples_read == 1:
                    log.info("audio_meter_receiving_audio", channel=self.channel,
                             first_db=round(self._level_db, 1))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("audio_meter_read_error", channel=self.channel, error=str(exc))

    async def _log_sl_stderr(self) -> None:
        if not self._sl or not self._sl.stderr:
            return
        try:
            async for raw in self._sl.stderr:
                line = raw.decode(errors="replace").strip()
                if line:
                    log.warning("streamlink_stderr", channel=self.channel, line=line)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _monitor_loop(self) -> None:
        await asyncio.sleep(8)
        while self._running:
            sl_dead = self._sl is None or self._sl.returncode is not None
            ff_dead = self._ff is None or self._ff.returncode is not None
            if sl_dead or ff_dead:
                log.warning("audio_meter_process_died", channel=self.channel,
                            streamlink_rc=self._sl.returncode if self._sl else "n/a",
                            ffmpeg_rc=self._ff.returncode if self._ff else "n/a")
                await self._kill_procs()
                self._level_db = _SILENCE_DB
                log.info("audio_meter_restarting", channel=self.channel, delay=_RESTART_DELAY)
                await asyncio.sleep(_RESTART_DELAY)
                if self._running:
                    await self._launch()
            await asyncio.sleep(5)

    async def _kill_procs(self) -> None:
        for task in (self._reader, self._sl_log):
            if task:
                task.cancel()
        self._reader = self._sl_log = None
        for proc in (self._ff, self._sl):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                except ProcessLookupError:
                    pass
        self._ff = self._sl = None

    async def get_audio_level_db(self) -> float:
        return self._level_db

    async def stop(self) -> None:
        self._running = False
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None
        await self._kill_procs()
        log.info("audio_meter_stopped", channel=self.channel)
