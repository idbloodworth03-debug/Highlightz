"""
ClipRecorder — a bounded rolling video buffer per monitored channel.

WHAT CHANGED AND WHY. Until now the product held no stream video at all: the
only pull was `audio_only,worst` into an RMS meter that wrote nothing to disk,
and clips were created through Twitch's Clips API so Twitch hosted every byte.
That kept the architecture simple and the compliance story short, but it also
meant the product could never hand the user a FILE — and a file is what the
editor, the scheduler and a download button all need. It also left Kick
unservable, because Kick has no clip-creation API at all.

This module is the deliberate crossing of that line, on the owner's decision.
It records a short rolling window of each monitored channel so that when the
trigger fires we can cut the moment out of video we already have, instead of
asking a platform for it afterwards.

THE FOUR THINGS THAT KEEP THIS FROM EATING THE BOX
──────────────────────────────────────────────────
1. **Nothing is transcoded, ever.** streamlink hands us the platform's own
   segments and ffmpeg remuxes them with `-c copy`. The CPU cost is close to
   zero, which matters because clip detection runs an audio meter per channel
   on a single core and detection must always win (see CLAUDE.md). A transcode
   here would starve the thing the product actually sells.

2. **The buffer is a ring, not an archive.** Segments older than
   `clip_capture_buffer_s` are deleted by the prune loop. The steady state is
   a few hundred MB across every monitored channel, not a growing library.

3. **A global cap bounds the whole feature.** The droplet has one disk shared
   with clips, uploads, billing writes and the database. A full disk is not a
   "capture is broken" event, it is an everything-is-broken event, so the cap
   is enforced in the prune loop and capture stops rather than filling it.

4. **It is a separate process tree from the meter.** The recorder deliberately
   runs its own streamlink rather than teeing the meter's, so that a recorder
   crash, a restart or a disk-full stop cannot take scoring down with it. The
   duplicated pull is cheap: the meter's audio-only feed is ~128 kbps against
   the video feed's several Mbps, so running both costs a couple of percent
   over running one, and buys complete isolation.

PRECISION. Segments are cut on keyframes, so a cut lands within one segment
length of the requested window — `clip_capture_segment_s` seconds, 2 by
default. The cut is biased to start EARLY rather than late: an extra second of
lead-in reads as a clip that captured the whole moment, while a second missing
off the front reads as a clip that started too late.
"""

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

# Where the ring buffers live. Under the existing storage root so backups,
# permissions and the disk-usage story stay in one place.
_ROOT = Path(settings.local_storage_path) / "capture"

_SEG_RE = re.compile(r"^seg-(\d+)\.ts$")

# How long a channel's pipeline is given before the monitor decides it died.
# Matches the audio meter's shape so the two behave alike under a flaky feed.
_RESTART_DELAY = 15.0
_PRUNE_INTERVAL = 5.0

MB = 1024 * 1024


def buffer_dir(channel: str) -> Path:
    """The ring directory for a channel.

    The channel name reaches us from a user-supplied "add this stream" request,
    so it never becomes a path component unsanitised — same rule the upload
    library follows. Anything outside the safe set is replaced, which can
    collide two odd names into one directory; that is acceptable because the
    directory holds only transient segments, and it is far better than a name
    that walks out of the capture root.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", channel or "")[:64] or "_"
    return _ROOT / safe


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.glob("**/*.ts"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def total_bytes() -> int:
    """Bytes held by every channel's ring buffer right now."""
    return _dir_bytes(_ROOT)


def select_segments(segs: list[tuple[Path, float]], start_ts: float,
                    end_ts: float, seg_s: float) -> tuple[list[Path], float]:
    """Choose the segments covering [start_ts, end_ts] and the offset into them.

    Pure, and separated from `cut` so the window arithmetic can be tested
    without a video pipeline — the part that silently goes wrong is the maths,
    not the ffmpeg invocation.

    A segment spans (mtime - seg_s, mtime]. Every overlapping segment is taken,
    plus one extra in FRONT: a copy can only begin at a keyframe, so starting
    from the previous segment guarantees the requested moment is inside the
    copied range rather than just before its first keyframe. The returned
    offset is measured from the start of the first chosen segment.
    """
    picked: list[tuple[Path, float]] = []
    for i, (path, ended) in enumerate(segs):
        seg_start = ended - seg_s
        if seg_start < end_ts and ended > start_ts:
            if not picked and i > 0:
                picked.append(segs[i - 1])
            picked.append((path, ended))
    if not picked:
        return [], 0.0
    first_start = picked[0][1] - seg_s
    return [p for p, _ in picked], max(0.0, start_ts - first_start)


class ClipRecorder:
    """One channel's rolling buffer.

    Owns a streamlink → ffmpeg pair writing numbered MPEG-TS segments, a prune
    loop that keeps the ring bounded, and a monitor that restarts the pipeline
    if either process dies (streams end, tokens expire, CDNs rotate).
    """

    def __init__(self, channel: str, stream_url: str) -> None:
        self.channel = channel
        self.stream_url = stream_url
        self.dir = buffer_dir(channel)
        self._sl: asyncio.subprocess.Process | None = None
        self._ff: asyncio.subprocess.Process | None = None
        self._monitor: asyncio.Task | None = None
        self._pruner: asyncio.Task | None = None
        self._sl_log: asyncio.Task | None = None
        self._running = False
        self._seq = 0
        # Set when the pipeline has produced at least one segment, so callers
        # can tell "recording" from "launched but the stream never arrived".
        self.armed = False

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self.dir.mkdir(parents=True, exist_ok=True)
        await self._launch()
        self._monitor = asyncio.create_task(
            self._monitor_loop(), name=f"recorder-monitor-{self.channel}")
        self._pruner = asyncio.create_task(
            self._prune_loop(), name=f"recorder-prune-{self.channel}")
        log.info("clip_recorder_started", channel=self.channel,
                 quality=settings.clip_capture_quality,
                 buffer_s=settings.clip_capture_buffer_s)

    async def _launch(self) -> None:
        # Numbering continues across restarts so a new pipeline cannot
        # overwrite segments the old one just wrote and a cut spanning the
        # restart still finds both halves in order.
        self._seq = self._next_seq()
        pattern = str(self.dir / "seg-%05d.ts")
        r_fd, w_fd = os.pipe()
        try:
            self._sl = await asyncio.create_subprocess_exec(
                settings.streamlink_path,
                "--stdout",
                "--loglevel", "warning",
                self.stream_url,
                settings.clip_capture_quality,
                stdout=w_fd,
                stderr=asyncio.subprocess.PIPE,
            )
            os.close(w_fd); w_fd = -1

            self._ff = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path,
                "-hide_banner", "-nostats", "-loglevel", "error",
                "-i", "pipe:0",
                # THE WHOLE COST STORY IS THIS LINE. Copy, never encode.
                "-c", "copy",
                "-f", "segment",
                "-segment_time", str(settings.clip_capture_segment_s),
                "-segment_start_number", str(self._seq),
                # Each segment stands alone, so the concat demuxer can splice
                # any run of them without timestamps jumping at the seams.
                "-reset_timestamps", "1",
                "-segment_format", "mpegts",
                pattern,
                stdin=r_fd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            os.close(r_fd); r_fd = -1

            self._sl_log = asyncio.create_task(
                self._log_sl_stderr(), name=f"recorder-sl-log-{self.channel}")
            log.info("clip_recorder_pipeline_up", channel=self.channel)
        except Exception as exc:
            for fd in (r_fd, w_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            log.error("clip_recorder_launch_failed", channel=self.channel,
                      error=str(exc))
            await self._kill_procs()

    def _next_seq(self) -> int:
        highest = -1
        try:
            for p in self.dir.iterdir():
                m = _SEG_RE.match(p.name)
                if m:
                    highest = max(highest, int(m.group(1)))
        except OSError:
            pass
        return highest + 1

    async def _log_sl_stderr(self) -> None:
        if not self._sl or not self._sl.stderr:
            return
        try:
            async for raw in self._sl.stderr:
                line = raw.decode(errors="replace").strip()
                if line:
                    log.warning("clip_recorder_streamlink_stderr",
                                channel=self.channel, line=line)
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
                log.warning("clip_recorder_process_died", channel=self.channel,
                            streamlink_rc=self._sl.returncode if self._sl else "n/a",
                            ffmpeg_rc=self._ff.returncode if self._ff else "n/a")
                await self._kill_procs()
                await asyncio.sleep(_RESTART_DELAY)
                if self._running:
                    await self._launch()
            await asyncio.sleep(5)

    async def _prune_loop(self) -> None:
        """Keep the ring bounded, and stop capturing if the disk cap is hit.

        Runs in the recorder rather than as one global sweep so that a channel
        whose feed is unusually fat is trimmed on its own schedule.
        """
        while self._running:
            try:
                self._prune_once()
                # The global cap is checked here rather than at write time
                # because ffmpeg owns the writes; the lever we actually have is
                # to stop feeding it. Capture stopping is a degraded feature;
                # a full disk is a dead product.
                cap = settings.clip_capture_max_total_mb * MB
                if cap and total_bytes() > cap:
                    log.error("clip_capture_over_global_cap",
                              channel=self.channel,
                              used_mb=round(total_bytes() / MB),
                              cap_mb=settings.clip_capture_max_total_mb)
                    await self.stop()
                    return
            except Exception as exc:
                log.warning("clip_recorder_prune_failed",
                            channel=self.channel, error=str(exc))
            await asyncio.sleep(_PRUNE_INTERVAL)

    def _prune_once(self) -> None:
        segs = self.segments()
        if not segs:
            return
        self.armed = True
        # Keep a little more than the advertised window so a cut asking for the
        # very oldest moment still finds its head segment.
        keep = int(settings.clip_capture_buffer_s / max(1, settings.clip_capture_segment_s)) + 4
        # The newest segment is still being written by ffmpeg; it is kept by
        # virtue of being newest, and never handed to a cut (see segments()).
        for path, _ in segs[:-keep] if len(segs) > keep else []:
            try:
                path.unlink()
            except OSError:
                pass

    async def _kill_procs(self) -> None:
        if self._sl_log:
            self._sl_log.cancel()
            self._sl_log = None
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

    async def stop(self, wipe: bool = True) -> None:
        self._running = False
        self.armed = False
        for task in (self._monitor, self._pruner):
            if task:
                task.cancel()
        self._monitor = self._pruner = None
        await self._kill_procs()
        if wipe:
            # The buffer exists to serve clips from a live stream. Once the
            # stream is not being monitored the segments are just video of
            # somebody we have no reason to hold, so they go immediately.
            shutil.rmtree(self.dir, ignore_errors=True)
        log.info("clip_recorder_stopped", channel=self.channel, wiped=wipe)

    # ── reading the ring ─────────────────────────────────────────────────

    def segments(self) -> list[tuple[Path, float]]:
        """Complete segments, oldest first, as (path, finished_at_epoch).

        mtime is when ffmpeg closed the file, which is the segment's END. That
        is used rather than a manifest because it survives pipeline restarts
        with no bookkeeping: whatever is on disk describes itself.

        The newest file is excluded — ffmpeg is still appending to it, so its
        mtime is not its end time and its tail is not yet readable.
        """
        found: list[tuple[Path, float]] = []
        try:
            for p in self.dir.iterdir():
                if not _SEG_RE.match(p.name):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > 0:
                    found.append((p, st.st_mtime))
        except OSError:
            return []
        found.sort(key=lambda t: t[1])
        return found[:-1] if len(found) > 1 else []

    def covers(self, start_ts: float, end_ts: float) -> bool:
        """Whether the ring currently spans the requested window."""
        segs = self.segments()
        if not segs:
            return False
        seg_s = max(1, settings.clip_capture_segment_s)
        oldest_start = segs[0][1] - seg_s
        newest_end = segs[-1][1]
        return oldest_start <= start_ts and newest_end >= min(end_ts, newest_end)

    async def cut(self, start_ts: float, end_ts: float, out_path: Path) -> Path | None:
        """Write [start_ts, end_ts] out of the ring to `out_path`.

        Returns the path on success, None if the window is not in the buffer or
        ffmpeg fails. Every caller treats None as "no local file for this clip"
        and carries on — capture is an enhancement to the clip, never a
        precondition for it.
        """
        if end_ts <= start_ts:
            return None
        seg_s = max(1, settings.clip_capture_segment_s)
        segs = self.segments()
        if not segs:
            log.info("clip_cut_no_segments", channel=self.channel)
            return None

        picked, offset = select_segments(segs, start_ts, end_ts, seg_s)
        if not picked:
            log.info("clip_cut_window_not_buffered", channel=self.channel,
                     want_start=round(start_ts, 1), want_end=round(end_ts, 1),
                     have_from=round(segs[0][1] - seg_s, 1),
                     have_to=round(segs[-1][1], 1))
            return None
        duration = end_ts - start_ts
        if duration <= 0:
            return None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        list_path = out_path.with_suffix(".concat.txt")
        try:
            # The concat demuxer takes a file of paths. They are absolute and
            # generated by us, never from user input, so `-safe 0` is bounded
            # by that rather than by trust in the names.
            list_path.write_text(
                "".join(f"file '{p.as_posix()}'\n" for p in picked),
                encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                settings.ffmpeg_path,
                "-hide_banner", "-nostats", "-loglevel", "error",
                "-y",
                "-f", "concat", "-safe", "0",
                # Input seeking: fast, and keyframe-snapped, which is the
                # precision this design accepts in exchange for never encoding.
                "-ss", f"{offset:.3f}",
                "-i", str(list_path),
                "-t", f"{duration:.3f}",
                "-c", "copy",
                # A clip is going to a phone and to social platforms, so the
                # index belongs at the front: without this the file will not
                # start playing until it has fully downloaded.
                "-movflags", "+faststart",
                str(out_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=90.0)
            if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
                log.warning("clip_cut_failed", channel=self.channel,
                            rc=proc.returncode,
                            error=(err or b"").decode(errors="replace")[-300:])
                out_path.unlink(missing_ok=True)
                return None
            log.info("clip_cut_ok", channel=self.channel,
                     seconds=round(duration, 1), segments=len(picked),
                     size_mb=round(out_path.stat().st_size / MB, 1))
            return out_path
        except asyncio.TimeoutError:
            log.warning("clip_cut_timeout", channel=self.channel)
            out_path.unlink(missing_ok=True)
            return None
        except Exception as exc:
            log.warning("clip_cut_error", channel=self.channel, error=str(exc))
            out_path.unlink(missing_ok=True)
            return None
        finally:
            list_path.unlink(missing_ok=True)


# ── registry ─────────────────────────────────────────────────────────────
#
# One recorder per channel, shared by every user monitoring it: two people
# watching the same streamer must not mean two pulls of the same video. The
# refcount is what lets the last one leaving turn the lights off.

_recorders: dict[str, ClipRecorder] = {}
_refs: dict[str, int] = {}
_lock = asyncio.Lock()


def get(channel: str) -> ClipRecorder | None:
    return _recorders.get((channel or "").lower())


async def ensure(channel: str, stream_url: str) -> ClipRecorder | None:
    """Start recording a channel, or join the recording already running.

    Returns None when capture is switched off or the channel has opted out —
    both of which callers treat as "no local file", never as an error.
    """
    if not settings.clip_capture_enabled:
        return None
    key = (channel or "").lower()
    if not key:
        return None
    # THE OPT-OUT IS CHECKED BEFORE THE FIRST BYTE, not at serve time. A
    # broadcaster who has opted out should never have been recorded in the
    # first place, and a check that only hides the result still means their
    # video sat on our disk.
    try:
        from src.auth import optout
        if optout.is_opted_out(key):
            log.info("clip_capture_refused_opted_out", channel=key)
            return None
    except Exception:
        pass
    async with _lock:
        rec = _recorders.get(key)
        if rec is None:
            rec = ClipRecorder(key, stream_url)
            _recorders[key] = rec
            _refs[key] = 0
            await rec.start()
        _refs[key] = _refs.get(key, 0) + 1
        return rec


async def release(channel: str) -> None:
    """Drop one hold on a channel's recorder, stopping it when the last goes."""
    key = (channel or "").lower()
    async with _lock:
        if key not in _recorders:
            return
        _refs[key] = max(0, _refs.get(key, 1) - 1)
        if _refs[key] == 0:
            rec = _recorders.pop(key)
            _refs.pop(key, None)
            await rec.stop()


async def stop_all() -> None:
    async with _lock:
        recs = list(_recorders.values())
        _recorders.clear()
        _refs.clear()
    for rec in recs:
        await rec.stop()
