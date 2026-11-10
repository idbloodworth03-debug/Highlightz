"""Stop capture that no running worker holds (2026-11-10).

The structural fix is StreamWorker._stop_capture / stop(): capture is stopped
first and cannot be cut off. This is the backstop for any path that still
slips through, because the cost of a leak is open-ended: on prod, 2,499 of
2,789 audio-meter deaths in a day were on channels nobody listed any more,
each one a streamlink and an ffmpeg spawned every five minutes, forever, plus
an alert email.

A meter is an orphan when it is running (audio_meter.running()) and no worker
holds it as its _buffer. A recorder is an orphan when no worker holds it as
its _recorder. Both are compared by identity, not by channel name: two users
watching one channel hold two meters and share one recorder.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


async def reap_orphan_capture(workers) -> int:
    """`workers` are the live StreamWorker instances. Returns how many
    pipelines were stopped."""
    from src.ingestion import audio_meter, clip_recorder
    workers = list(workers)
    meters = {id(w._buffer): w._buffer for w in workers if getattr(w, "_buffer", None) is not None}
    recs = {w._recorder for w in workers if getattr(w, "_recorder", None) is not None}
    n = 0
    for m in audio_meter.running():
        if id(m) in meters:
            continue
        log.warning("capture_orphan_reaped", kind="audio_meter", channel=m.channel)
        m.halt()
        try:
            await m.stop()
        except Exception as exc:
            log.warning("capture_orphan_stop_failed", kind="audio_meter",
                        channel=m.channel, error=str(exc))
        n += 1
    for channel in await clip_recorder.reap(keep=recs):
        log.warning("capture_orphan_reaped", kind="clip_recorder", channel=channel)
        n += 1
    return n
