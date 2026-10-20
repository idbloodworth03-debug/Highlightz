"""A stopped worker closes its platform's HTTP session, whatever its channel
was doing.

The first alert email (2026-10-20) carried asyncio's "Unclosed client session"
15 times at once. A worker's platform opens an aiohttp session on its first
API call — get_stream_info, the "is it live?" check — and the only close was
in _cleanup, which runs after a LIVE session. A worker stopped while its
channel was offline or queued for capacity (removed, paused for idling, plan
lapsed, shutdown) dropped the session for the garbage collector to find, and
it reports each one as an error. Reproduced with the real worker before the
fix: every stop left the session open.
"""

import asyncio
import gc
import logging

import pytest

from src.ingestion.platform.base import ChannelOffline
from src.ingestion.platform.kick import KickPlatform


class _OfflineKick(KickPlatform):
    """Opens its session exactly as the real get_stream_info does, then answers
    "offline". No network."""

    async def get_stream_info(self, channel):
        await self._ensure_session()
        raise ChannelOffline(f"{channel} is not live on Kick")


def _worker(platform, monkeypatch):
    from src.dashboard import api
    from src.ingestion import stream_worker
    from src.profiles.profile import StreamerProfile

    async def _noop(*a, **k): return None

    async def _profile(*a, **k): return StreamerProfile(channel="nova")
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(stream_worker, "get_profile_manager",
                        lambda uid: type("PM", (), {"load": staticmethod(_profile),
                                                    "save": staticmethod(_noop)})())
    return stream_worker.StreamWorker(
        config=stream_worker.WorkerConfig(channel="nova", platform_name="kick", user_id="u1"),
        platform=platform, queue=None, shared_buffers={})


async def _stop_while_offline(monkeypatch):
    """main.stop_worker's sequence: stop(), then cancel the task, which is
    sitting in the 30-second wait between "is it live?" checks."""
    platform = _OfflineKick()
    w = _worker(platform, monkeypatch)
    task = asyncio.create_task(w.start())
    for _ in range(200):
        if platform._session is not None:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert platform._session is not None and not platform._session.closed, \
        "the offline check never opened a session; this test proves nothing"
    w.stop()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return platform


@pytest.mark.asyncio
async def test_stopping_a_worker_whose_channel_is_offline_closes_its_session(monkeypatch):
    platform = await _stop_while_offline(monkeypatch)
    assert platform._session.closed, "the stopped worker left its HTTP session open"


@pytest.mark.asyncio
async def test_nothing_reports_an_unclosed_session_afterwards(monkeypatch, caplog):
    """The alert itself: once the worker is gone, collecting it must not log
    asyncio's error."""
    caplog.set_level(logging.ERROR, logger="asyncio")
    await _stop_while_offline(monkeypatch)
    gc.collect()
    await asyncio.sleep(0)
    assert not [r for r in caplog.records if "Unclosed client session" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_worker_that_ends_on_its_own_closes_its_session(monkeypatch):
    """Not cancelled: told to stop between checks, the loop ends normally."""
    platform = _OfflineKick()
    w = _worker(platform, monkeypatch)
    _real_sleep = asyncio.sleep

    async def _quick(*_a, **_k):
        w._running = False
        await _real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", _quick)
    await w.start()
    assert platform._session is not None and platform._session.closed
