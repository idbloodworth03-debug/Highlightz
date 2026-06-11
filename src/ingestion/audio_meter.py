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
import re
import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_M_RE = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_DB = -100.0


class AudioMeter:
    def __init__(self, channel: str, stream_url: str) -> None:
        self.channel = channel
        self.stream_url = stream_url
        self._level_db: float = _SILENCE_DB
        self._sl: asyncio.subprocess.Process | None = None
        self._ff: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        try:
            # streamlink: audio-only, ads disabled, write raw stream to stdout
            self._sl = await asyncio.create_subprocess_exec(
                settings.streamlink_path,
                "--stdout",
                "--twitch-disable-ads",
                "--loglevel", "none",
                self.stream_url,
                "audio_only,worst",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # ffmpeg: read streamlink output, run ebur128, discard all output,
            # emit momentary loudness on stderr which we parse.
            self._ff = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path,
                "-hide_banner", "-nostats",
                "-i", "pipe:0",
                "-af", "ebur128=metadata=1",
                "-f", "null", "-",
                stdin=self._sl.stdout,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader = asyncio.create_task(self._read_loop(), name=f"audiometer-{self.channel}")
            log.info("audio_meter_started", channel=self.channel)
        except Exception as exc:
            log.warning("audio_meter_start_failed", channel=self.channel, error=str(exc))
            await self.stop()

    async def _read_loop(self) -> None:
        assert self._ff is not None and self._ff.stderr is not None
        try:
            while self._running:
                line = await self._ff.stderr.readline()
                if not line:
                    break
                m = _M_RE.search(line.decode(errors="replace"))
                if m:
                    try:
                        # ebur128 momentary loudness in LUFS; treated as a dB-like
                        # value by the engine (only relative change matters).
                        self._level_db = float(m.group(1))
                    except ValueError:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("audio_meter_read_error", channel=self.channel, error=str(exc))

    async def get_audio_level_db(self) -> float:
        """Latest momentary loudness, or -100 if no audio yet."""
        return self._level_db

    async def stop(self) -> None:
        self._running = False
        if self._reader:
            self._reader.cancel()
            self._reader = None
        for proc in (self._ff, self._sl):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
        self._ff = None
        self._sl = None
        log.info("audio_meter_stopped", channel=self.channel)
