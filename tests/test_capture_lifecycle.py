"""Capture must stop with its session, and a Kick restart must use a fresh link
(prod, 2026-11-10).

Read on prod over 24 h: streamlink failed 5,124 times with "403 Forbidden" on
Kick playback URLs (the signed token in the link expires, and every restart
reused it); every r2m_world recorder restart lived six seconds; and 2,499 of
2,789 audio-meter deaths were on channels nobody listed any more (capture that
outlived its session, as with adinross on 2026-11-03).
"""

from __future__ import annotations

import asyncio

import pytest

from src.ingestion import audio_meter, clip_recorder, orphans, restart
from src.ingestion.platform.base import ChannelOffline, StreamInfo


class Dead:
    returncode = 1


class Alive:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True


@pytest.fixture
def fast(monkeypatch):
    """asyncio.sleep without the waiting; records every delay asked for."""
    real = asyncio.sleep
    asked = []

    async def quick(d, *a, **k):
        asked.append(d)
        await real(0)
    monkeypatch.setattr(asyncio, "sleep", quick)
    return asked


def _drive(pipe, launches_wanted, refresh_calls=None):
    """Run a pipeline's monitor loop with fake processes until it has been
    launched `launches_wanted` times (or its refresher has been called
    `refresh_calls` times). Returns the URLs it launched with."""
    launched = []

    async def fake_launch():
        launched.append(pipe.stream_url)
        pipe._backoff.started()
        pipe._sl, pipe._ff = Dead(), Dead()          # dies straight away
        if len(launched) >= launches_wanted:
            pipe._running = False

    async def no_kill():
        pipe._sl = pipe._ff = None

    pipe._launch = fake_launch
    pipe._kill_procs = no_kill
    pipe._running = True
    pipe._sl, pipe._ff = Dead(), Dead()
    asyncio.run(asyncio.wait_for(pipe._monitor_loop(), 5))
    return launched


def _refresher(answers):
    calls = []

    async def fresh():
        a = answers[min(len(calls), len(answers) - 1)]
        calls.append(a)
        if isinstance(a, BaseException):
            raise a
        return a
    fresh.calls = calls
    return fresh


@pytest.mark.parametrize("make", [
    lambda url, r: audio_meter.AudioMeter("r2m_world", url, refresh_url=r),
    lambda url, r: clip_recorder.ClipRecorder("r2m_world", url, refresh_url=r),
])
def test_a_kick_restart_asks_for_a_fresh_link(fast, make):
    r = _refresher(["hls://B?token=new", "hls://C?token=newer"])
    pipe = make("hls://A?token=expired", r)
    assert _drive(pipe, 2) == ["hls://B?token=new", "hls://C?token=newer"]


@pytest.mark.parametrize("make", [
    lambda url, r: audio_meter.AudioMeter("r2m_world", url, refresh_url=r),
    lambda url, r: clip_recorder.ClipRecorder("r2m_world", url, refresh_url=r),
])
def test_an_offline_channel_waits_instead_of_relaunching(fast, make):
    off = ChannelOffline("not live")
    r = _refresher([off, off, off, "hls://back?token=t"])
    pipe = make("hls://A?token=expired", r)
    launched = _drive(pipe, 1)
    assert launched == ["hls://back?token=t"], "a dead link was relaunched while offline"
    assert len(r.calls) == 4
    restarts = [d for d in fast if d not in (5, 8)]
    assert restarts == sorted(restarts) and restarts[-1] > restarts[0], \
        "the wait does not grow while the channel is offline"


def test_twitch_has_no_refresher_and_relaunches_its_page_url(fast):
    pipe = audio_meter.AudioMeter("xqc", "https://www.twitch.tv/xqc")
    assert _drive(pipe, 2) == ["https://www.twitch.tv/xqc"] * 2


def test_a_failed_lookup_falls_back_to_the_link_it_had(fast):
    r = _refresher([RuntimeError("kick api down")])
    pipe = audio_meter.AudioMeter("r2m_world", "hls://A?token=t", refresh_url=r)
    assert _drive(pipe, 1) == ["hls://A?token=t"]


def test_the_recorder_gets_a_refresher_only_on_kick(monkeypatch, tmp_path):
    monkeypatch.setattr(clip_recorder.settings, "clip_capture_enabled", True)
    monkeypatch.setattr(clip_recorder, "_recorders", {})
    monkeypatch.setattr(clip_recorder, "_refs", {})

    async def no_start(self):
        pass
    monkeypatch.setattr(clip_recorder.ClipRecorder, "start", no_start)

    async def go():
        k = await clip_recorder.ensure("r2m_world", "hls://x", platform="kick")
        t = await clip_recorder.ensure("xqc", "https://www.twitch.tv/xqc", platform="twitch")
        return k, t
    k, t = asyncio.run(go())
    assert k._refresh is not None and t._refresh is None


def test_kick_url_refresher_closes_its_own_client(monkeypatch):
    from src.ingestion.platform import kick
    closed = []

    async def info(self, ch):
        if ch == "gone":
            raise ChannelOffline("off")
        return StreamInfo(channel=ch, platform="kick", stream_url="hls://fresh",
                          chat_channel_id="", title="", game="", viewer_count=1,
                          is_mature=True, broadcaster_id="1")

    async def close(self):
        closed.append(1)
    monkeypatch.setattr(kick.KickPlatform, "get_stream_info", info)
    monkeypatch.setattr(kick.KickPlatform, "close", close)
    assert asyncio.run(restart.kick_url_refresher("r2m_world")()) == "hls://fresh"
    with pytest.raises(ChannelOffline):
        asyncio.run(restart.kick_url_refresher("gone")())
    assert closed == [1, 1]


# ── the session's stop path ─────────────────────────────────────────────────

class FakeMeter:
    def __init__(self, channel="r2m_world", stop_takes=0.0):
        self.channel, self.stop_takes = channel, stop_takes
        self.halted = self.stopped = False

    def halt(self):
        self.halted = True

    async def stop(self):
        await asyncio.sleep(self.stop_takes)
        self.stopped = True


class _Platform:
    async def close(self):
        pass


def _worker(monkeypatch):
    from src.ingestion import stream_worker as sw
    released = []

    async def release(ch):
        released.append(ch)
    monkeypatch.setattr(clip_recorder, "release", release)
    w = sw.StreamWorker(config=sw.WorkerConfig(channel="r2m_world", platform_name="kick",
                                               user_id="u1", preset="default"),
                        platform=_Platform(), queue=None, shared_buffers={})
    w.released = released
    return w


def test_stop_halts_the_meter_and_releases_the_recorder_at_once(monkeypatch):
    w = _worker(monkeypatch)
    meter = FakeMeter()

    async def go():
        w._buffer, w._recorder = meter, object()
        w.stop()
        assert meter.halted, "the meter could restart after stop()"
        assert w._buffer is None and w._recorder is None
        for _ in range(5):
            await asyncio.sleep(0)
    asyncio.run(go())
    assert meter.stopped and w.released == ["r2m_world"]


def test_a_cleanup_cancelled_half_way_still_stops_capture(monkeypatch):
    """The leak: stop_worker cancels the worker while it is cleaning up. The
    capture stop is shielded, so it completes anyway."""
    w = _worker(monkeypatch)
    meter = FakeMeter(stop_takes=0.05)

    async def go():
        w._buffer, w._recorder = meter, object()
        t = asyncio.ensure_future(w._cleanup())
        await asyncio.sleep(0.01)                  # inside meter.stop()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        await asyncio.sleep(0.1)
    asyncio.run(go())
    assert meter.halted and meter.stopped
    assert w.released == ["r2m_world"], "the recorder hold was never released"


def test_capture_is_stopped_before_the_cleanup_can_hang(monkeypatch):
    """A session task that will not finish used to hold the capture hostage."""
    w = _worker(monkeypatch)
    meter = FakeMeter()

    async def go():
        done = asyncio.Event()

        async def stubborn():
            # Swallows cancellation until the test lets it go, like a session
            # task that will not finish.
            while not done.is_set():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    continue

        w._buffer, w._recorder = meter, object()
        w._tasks = [asyncio.ensure_future(stubborn())]
        t = asyncio.ensure_future(w._cleanup())
        await asyncio.sleep(0.05)
        assert meter.halted and meter.stopped and w.released == ["r2m_world"]
        done.set()
        w._tasks[0].cancel()
        await asyncio.wait_for(t, 1)
    asyncio.run(asyncio.wait_for(go(), 5))


# ── the orphan reaper ────────────────────────────────────────────────────────

def test_the_reaper_stops_only_what_no_worker_holds(monkeypatch):
    owned, orphan = FakeMeter("live1"), FakeMeter("gone1")
    monkeypatch.setattr(audio_meter, "_running", {owned, orphan})

    class Rec:
        def __init__(self, ch):
            self.channel, self.halted, self.stopped = ch, False, False

        def halt(self):
            self.halted = True

        async def stop(self):
            self.stopped = True
    kept_rec, lost_rec = Rec("live1"), Rec("gone1")
    monkeypatch.setattr(clip_recorder, "_recorders", {"live1": kept_rec, "gone1": lost_rec})
    monkeypatch.setattr(clip_recorder, "_refs", {"live1": 1, "gone1": 3})

    class W:
        def __init__(self, b, r):
            self._buffer, self._recorder = b, r
    n = asyncio.run(orphans.reap_orphan_capture([W(owned, kept_rec)]))
    assert n == 2
    assert orphan.halted and orphan.stopped and not owned.halted
    assert lost_rec.halted and lost_rec.stopped and not kept_rec.stopped
    assert list(clip_recorder._recorders) == ["live1"] and "gone1" not in clip_recorder._refs


def test_a_started_meter_is_tracked_and_a_stopped_one_is_not(monkeypatch):
    monkeypatch.setattr(audio_meter, "_running", set())
    m = audio_meter.AudioMeter("x", "u")

    async def no_launch():
        pass
    m._launch = no_launch

    async def go():
        await m.start()
        assert m in audio_meter.running()
        m.halt()
        assert m not in audio_meter.running()
    asyncio.run(go())


def test_the_reaper_runs_from_main():
    import pathlib
    src = pathlib.Path("src/main.py").read_text()
    assert "reap_orphan_capture_task()" in src
