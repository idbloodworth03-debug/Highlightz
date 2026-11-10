"""How the capture pipelines (audio meter, clip recorder) restart, and what
they may write to the log.

Owner, 2026-09-30 audit, from the production journal: a Kick channel whose
stream refused to open (`403 Forbidden` on the playback URL) was restarted
every ~20 seconds, forever, by BOTH pipelines, each start spawning a fresh
streamlink + ffmpeg on a one-CPU box, and each failure logging the playback
URL with its access token in it.

RESTARTS BACK OFF, BUT ONLY WHEN THEY KEEP FAILING. A pipeline that ran for a
while and then dropped (a live stream's network blip, the case restarts exist
for) comes back after the same 15 seconds as before, every time. Only a
pipeline that dies again within HEALTHY_S of starting counts as a failure in a
row, and each one doubles the wait, up to MAX_S. So a healthy stream's
recovery is unchanged and a stream that cannot open costs one attempt every
five minutes instead of three a minute.
"""

from __future__ import annotations

import re
import time

import structlog

log = structlog.get_logger(__name__)

BASE_S = 15.0
MAX_S = 300.0
HEALTHY_S = 60.0


class Backoff:
    def __init__(self, base: float = BASE_S, cap: float = MAX_S, healthy: float = HEALTHY_S,
                 clock=time.monotonic) -> None:
        self.base, self.cap, self.healthy, self._clock = base, cap, healthy, clock
        self.failures = 0
        self._started = clock()

    def started(self) -> None:
        """Call when a pipeline is launched."""
        self._started = self._clock()

    def next_delay(self) -> float:
        """Call when it has died: how long to wait before the next launch."""
        if self._clock() - self._started >= self.healthy:
            self.failures = 0               # it had been running: a blip, not a refusal
        self.failures += 1
        return min(self.cap, self.base * (2 ** (self.failures - 1)))


# Signed playback URLs carry their credential in the query string (Kick's IVS
# `token=`, Twitch's `sig=`/`token=`). Keep the host and path, which are what
# make a line useful, and drop the value.
_SECRET_Q = re.compile(r"([?&](?:token|sig|signature|auth|access_token|key|policy|p)=)[^&\s)\"']+",
                       re.IGNORECASE)


def redact(line: str) -> str:
    return _SECRET_Q.sub(r"\1<redacted>", line)


# ── A KICK LINK GOES STALE; A RESTART MUST NOT REUSE IT ─────────────────────
#
# Prod, 2026-11-10 (24 h): streamlink failed 5,124 times with "403 Client
# Error: Forbidden" on Kick playback URLs, and every r2m_world recorder
# restart lived six seconds. A Kick stream URL is the IVS playlist with a
# signed `token=` that expires; the pipelines kept the URL they were started
# with and retried it forever, so a channel whose token expired mid-stream
# lost its capture (and every clip's video) until the stream ended, and a
# channel that had ended retried a dead link every five minutes. Twitch is not
# affected: its URL is the channel page, which streamlink resolves afresh on
# every launch.

def kick_url_refresher(channel: str):
    """An async callable returning a fresh stream URL for a Kick channel, or
    raising ChannelOffline. Uses its own short-lived client, closed every time,
    so it never depends on (or leaks) the worker's HTTP session."""
    async def fresh() -> str:
        from src.ingestion.platform.kick import KickPlatform
        p = KickPlatform()
        try:
            return (await p.get_stream_info(channel)).stream_url
        finally:
            try:
                await p.close()
            except Exception:
                pass
    return fresh


async def launch_url(refresh, current: str, *, channel: str, kind: str) -> str | None:
    """The URL to relaunch with. A fresh one when there is a refresher; None
    when the platform says the channel is offline (do not launch, wait); the
    current one when there is no refresher or the lookup itself failed."""
    if refresh is None:
        return current
    from src.ingestion.platform.base import ChannelOffline
    try:
        url = await refresh()
    except ChannelOffline:
        return None
    except Exception as exc:
        log.warning("capture_url_refresh_failed", kind=kind, channel=channel,
                    error=redact(str(exc))[:200])
        return current
    return url or current
