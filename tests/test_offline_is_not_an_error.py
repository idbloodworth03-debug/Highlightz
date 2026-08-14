"""An offline channel is the expected case, not a fault.

Most monitored channels are offline most of the time, and a clipper queueing up
tomorrow's roster adds every one of them offline. That path used to be treated
as a failure three separate ways: tenacity retried the "not live" answer three
times, the worker logged a full traceback at ERROR, and the user was shown "a
stream hit an error — reconnecting" every ~30s per channel.

These tests pin the separation: offline is quiet, real faults stay loud.
"""

import asyncio

import pytest

from src.ingestion.platform.base import ChannelOffline


# ── the type ─────────────────────────────────────────────────────────────────

def test_channel_offline_is_still_a_value_error():
    """Every platform's is_live() does `except ValueError`, and so do callers
    outside this package. Narrowing the type must not change what they catch."""
    assert issubclass(ChannelOffline, ValueError)


@pytest.mark.parametrize("mod", ["twitch", "kick", "youtube"])
def test_every_platform_raises_the_specific_type_for_not_live(mod):
    """A bare ValueError here is indistinguishable from a parse failure or a
    bad argument, which is exactly how this ended up on the error path."""
    import importlib, inspect
    m = importlib.import_module(f"src.ingestion.platform.{mod}")
    src = inspect.getsource(m)
    # The RAISE site, not merely the name — the import line alone contains the
    # name, so a presence check passes against code that reverted the raise.
    assert "raise ChannelOffline(" in src, \
        f"{mod} still raises a bare ValueError for not-live"
    assert 'raise ValueError(f"Channel' not in src and \
           'raise ValueError("Channel' not in src, \
        f"{mod} has a not-live path still raising a bare ValueError"


def test_is_live_still_returns_false_rather_than_raising():
    """is_live() is the one place the offline answer is already expected. If the
    narrowed type escaped it, every liveness check would become a crash."""
    import inspect
    from src.ingestion.platform.twitch import TwitchPlatform
    src = inspect.getsource(TwitchPlatform.is_live)
    assert "except ValueError" in src and "return False" in src


# ── the retry ────────────────────────────────────────────────────────────────

def test_not_live_is_not_retried():
    """Retrying it asks Helix the same question three times with backoff and
    burns quota that every user's clipping shares."""
    import inspect
    from src.ingestion.platform import twitch
    src = inspect.getsource(twitch)
    assert "retry_if_not_exception_type(ChannelOffline)" in src


@pytest.mark.asyncio
async def test_the_offline_answer_reaches_the_caller_unwrapped():
    """Tenacity wraps a retried failure in RetryError. If ChannelOffline were
    still retried, the worker's `except ChannelOffline` would never match and
    every offline channel would fall through to the error branch — the bug,
    rebuilt with an extra layer."""
    from src.ingestion.platform.twitch import TwitchPlatform

    p = TwitchPlatform()

    async def _boom(*a, **k):
        raise ChannelOffline("nova is not live on Twitch")

    # Drive the decorated method with its body replaced: the decorator is what
    # is under test, not the HTTP call.
    p._get_token = _boom
    with pytest.raises(ChannelOffline):
        await p.get_stream_info("nova")


# ── the worker ───────────────────────────────────────────────────────────────

def _worker():
    from src.ingestion.stream_worker import StreamWorker, WorkerConfig
    return StreamWorker(config=WorkerConfig(channel="nova", platform_name="twitch",
                                            user_id="u1"),
                        platform=None, queue=None, shared_buffers={})


@pytest.mark.asyncio
async def test_an_offline_channel_never_broadcasts_an_error(monkeypatch):
    """The user-visible half. A waiting channel must not flash a toast saying
    something went wrong, because nothing did."""
    from src.dashboard import api
    from src.ingestion import stream_worker

    sent = []

    async def _bc(msg, **kw): sent.append(msg)
    monkeypatch.setattr(api, "broadcast", _bc)

    w = _worker()
    calls = {"n": 0}

    async def _session():
        calls["n"] += 1
        if calls["n"] >= 2:
            w._running = False          # stop after the first retry
        raise ChannelOffline("nova is not live")
    w._run_session = _session
    _real_sleep = asyncio.sleep      # capture first: the lambda below
    monkeypatch.setattr(asyncio, "sleep",   # would otherwise call itself
                        lambda *_a, **_k: _real_sleep(0))

    async def _noop(*a, **k): return None
    w._research_if_new = _noop
    monkeypatch.setattr(stream_worker, "get_profile_manager",
                        lambda uid: type("PM", (), {
                            "load": staticmethod(lambda *a, **k: _mk_profile()),
                            "save": staticmethod(_noop)})())
    await w.start()

    events = [m.get("event") for m in sent]
    assert "stream_error" not in events, "an offline channel told the user it errored"
    assert "stream_status" in events
    assert {m.get("status") for m in sent if m.get("event") == "stream_status"} == {"offline"}


@pytest.mark.asyncio
async def test_a_real_fault_still_shouts(monkeypatch):
    """The other half — quieting offline must not quiet genuine breakage."""
    from src.dashboard import api
    from src.ingestion import stream_worker

    sent = []

    async def _bc(msg, **kw): sent.append(msg)
    monkeypatch.setattr(api, "broadcast", _bc)

    w = _worker()
    calls = {"n": 0}

    async def _session():
        calls["n"] += 1
        if calls["n"] >= 2:
            w._running = False
        raise RuntimeError("chat socket exploded")
    w._run_session = _session
    _real_sleep = asyncio.sleep      # capture first: the lambda below
    monkeypatch.setattr(asyncio, "sleep",   # would otherwise call itself
                        lambda *_a, **_k: _real_sleep(0))

    async def _noop(*a, **k): return None
    w._research_if_new = _noop
    monkeypatch.setattr(stream_worker, "get_profile_manager",
                        lambda uid: type("PM", (), {
                            "load": staticmethod(lambda *a, **k: _mk_profile()),
                            "save": staticmethod(_noop)})())
    await w.start()

    events = [m.get("event") for m in sent]
    assert "stream_error" in events, "a real fault went silent"
    assert {m.get("status") for m in sent if m.get("event") == "stream_status"} == {"reconnecting"}


async def _mk_profile():
    from src.profiles.profile import StreamerProfile
    return StreamerProfile(channel="nova")


def test_a_broadcast_ending_is_not_a_crash():
    """A streamer going to bed ended the session with RuntimeError, which landed
    in the generic handler — traceback plus error toast, every night."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._liveness_check)
    assert "ChannelOffline" in src and 'RuntimeError("stream_offline")' not in src


def test_the_error_toast_reads_the_field_the_backend_actually_sends():
    """The handler read msg.message; the backend has only ever sent `error`. So
    the specific reason was never displayed to anyone — every fault showed the
    same generic sentence."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    i = html.index("msg.event==='stream_error'")
    # Comments in this block quote the old field by name to explain the bug, so
    # strip them — otherwise the test fails on its own documentation.
    code = "\n".join(l for l in html[i:i + 900].splitlines()
                     if not l.strip().startswith("//"))
    assert "flash(msg.error||" in code
    assert "msg.message" not in code
