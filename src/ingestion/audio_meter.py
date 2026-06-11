"""
AudioMeter — transient audio-loudness probe for the trigger engine.

Pulls an AUDIO-ONLY feed via streamlink and pipes it straight into FFmpeg's
ebur128 loudness meter. Only the momentary loudness number is kept in memory;
no audio or video is ever written to disk or served. This exists purely to feed
the audio-spike detection signal — Highlightz stores no media.

Exposes the same `get_audio_level_db()` coroutine the engine expects from the
old VideoBuffer, so it is a drop-in replacement for detection purposes.
"""

import asyncio
import os
import re
import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_M_RE = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_DB = -100.0
_RESTART_DELAY = 15  # seconds before attempting restart on process death


class AudioMeter:
    def __init__(self, channel: str, stream_url: str) -> None:
        self.channel = channel
        self.stream_url = stream_url
        self._level_db: float = _SILENCE_DB
        self._sl: asyncio.subprocess.Process | None = None
        self._ff: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._sl_logger: asyncio.Task | None = None
        self._monitor: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._launch()
        self._monitor = asyncio.create_task(
            self._monitor_loop(), name=f"audiometer-monitor-{self.channel}"
        )
        log.info("audio_meter_started", channel=self.channel, url=self.stream_url)

    async def _launch(self) -> None:
        """Spawn streamlink → ffmpeg pipeline and start reading ebur128 output."""
        r_fd, w_fd = os.pipe()
        try:
            # streamlink writes raw stream to the write end of an OS pipe
            self._sl = await asyncio.create_subprocess_exec(
                settings.streamlink_path,
                "--stdout",
                "--twitch-disable-ads",
                "--loglevel", "warning",
                self.stream_url,
                "audio_only,worst",
                stdout=w_fd,
                stderr=asyncio.subprocess.PIPE,
            )
            os.close(w_fd)
            w_fd = -1
            # ffmpeg reads from the read end of the same pipe
            self._ff = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path,
                "-hide_banner", "-nostats",
                "-i", "pipe:0",
                "-af", "ebur128=metadata=1",
                "-f", "null", "-",
                stdin=r_fd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            os.close(r_fd)
            r_fd = -1
            self._reader = asyncio.create_task(
                self._read_loop(), name=f"audiometer-reader-{self.channel}"
            )
            self._sl_logger = asyncio.create_task(
                self._log_streamlink_stderr(), name=f"audiometer-sl-log-{self.channel}"
            )
        except Exception as exc:
            for fd in (r_fd, w_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            log.error("audio_meter_launch_failed", channel=self.channel, error=str(exc))
            await self._kill_procs()

    async def _log_streamlink_stderr(self) -> None:
        """Drain and log streamlink's stderr so failures are visible."""
        if not self._sl or not self._sl.stderr:
            return
        try:
            async for raw in self._sl.stderr:
                line = raw.decode(errors="replace").strip()
                if line:
                    log.warning("streamlink_stderr", channel=self.channel, line=line)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("sl_stderr_drain_error", channel=self.channel, error=str(exc))

    async def _read_loop(self) -> None:
        if not self._ff or not self._ff.stderr:
            return
        try:
            while self._running:
                line = await self._ff.stderr.readline()
                if not line:
                    # FFmpeg exited — level stays at last known value until restart
                    log.warning("audio_meter_ffmpeg_exited", channel=self.channel)
                    break
                m = _M_RE.search(line.decode(errors="replace"))
                if m:
                    try:
                        self._level_db = float(m.group(1))
                    except ValueError:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("audio_meter_read_error", channel=self.channel, error=str(exc))

    async def _monitor_loop(self) -> None:
        """Watch for process death and restart the pipeline automatically."""
        await asyncio.sleep(5)  # give processes time to stabilise
        while self._running:
            sl_dead = self._sl is None or self._sl.returncode is not None
            ff_dead = self._ff is None or self._ff.returncode is not None
            if sl_dead or ff_dead:
                rc_sl = self._sl.returncode if self._sl else "n/a"
                rc_ff = self._ff.returncode if self._ff else "n/a"
                log.warning(
                    "audio_meter_process_died",
                    channel=self.channel,
                    streamlink_rc=rc_sl,
                    ffmpeg_rc=rc_ff,
                )
                await self._kill_procs()
                self._level_db = _SILENCE_DB
                log.info("audio_meter_restarting", channel=self.channel, delay=_RESTART_DELAY)
                await asyncio.sleep(_RESTART_DELAY)
                if self._running:
                    await self._launch()
            await asyncio.sleep(5)

    async def _kill_procs(self) -> None:
        for task in (self._reader, self._sl_logger):
            if task:
                task.cancel()
        self._reader = None
        self._sl_logger = None
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
        self._ff = None
        self._sl = None

    async def get_audio_level_db(self) -> float:
        """Latest momentary loudness, or -100 if no audio yet."""
        return self._level_db

    async def stop(self) -> None:
        self._running = False
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None
        await self._kill_procs()
        log.info("audio_meter_stopped", channel=self.channel)
