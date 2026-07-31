"""
Permanently unclippable channels (HTTP 403 "User not authorized to create clips").

Measured on prod 2026-07-31: five channels fired ~890 times over 50h and
produced ~1 clip. The engine was scoring them correctly the whole time — the
broadcaster had clipping restricted, Twitch refused every call, and nothing
told the user. They saw an empty queue and a bot that looked broken.

The distinction that matters: a NORMAL clip failure should be retried on the
next moment; this one must stop the channel. Getting that backwards either
spams a dead channel forever or kills a channel over a transient blip.
"""

import asyncio

import pytest

from src.output import twitch_clips as tc


def test_only_the_permanent_403_is_treated_as_unclippable():
    """403 is also returned for token problems, which ARE fixable by
    re-linking. Matching on the bare status would permanently kill a channel
    over an expired token."""
    assert tc.is_not_authorized(403, '{"message":"User not authorized to create clips."}')
    assert tc.is_not_authorized(403, 'USER NOT AUTHORIZED TO CREATE CLIPS')  # case
    # Not this one — a token problem, recoverable.
    assert not tc.is_not_authorized(403, '{"message":"Invalid OAuth token"}')
    # Not another status carrying similar words.
    assert not tc.is_not_authorized(401, "not authorized to create clips")
    assert not tc.is_not_authorized(404, '{"message":"Channel offline."}')
    assert not tc.is_not_authorized(403, "")
    assert not tc.is_not_authorized(200, "")


def test_the_error_is_distinguishable_from_a_generic_failure():
    """The caller branches on the TYPE. If this were a plain RuntimeError the
    processor loop could not tell 'stop this channel' from 'try again later'."""
    assert issubclass(tc.ClipNotAuthorizedError, RuntimeError)
    assert tc.ClipNotAuthorizedError is not RuntimeError


class _Resp:
    def __init__(self, status, body):
        self.status, self._body, self.headers = status, body, {}
    async def json(self): return {}
    async def text(self): return self._body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Session:
    def __init__(self, status, body):
        self.status, self.body, self.calls = status, body, 0
    def post(self, *a, **k):
        self.calls += 1
        return _Resp(self.status, self.body)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.fixture
def helix(monkeypatch):
    def install(status, body):
        s = _Session(status, body)
        monkeypatch.setattr(tc.aiohttp, "ClientSession", lambda *a, **k: s)
        monkeypatch.setattr(tc.settings, "twitch_client_id", "cid")
        return s
    return install


def test_create_clip_raises_and_does_not_burn_retries(helix):
    """Retrying a permanent refusal is pure waste — it produces the same 403
    every time, delaying the queue for nothing."""
    s = helix(403, '{"error":"Forbidden","message":"User not authorized to create clips."}')
    with pytest.raises(tc.ClipNotAuthorizedError):
        asyncio.run(tc.create_clip("tok", "123", retries=3, retry_delay=0))
    assert s.calls == 1, f"retried a permanent refusal {s.calls} times"


def test_a_recoverable_failure_still_returns_none_and_retries(helix):
    """The opposite case must keep the old behaviour: transient problems get
    the retries and report failure without killing the channel."""
    s = helix(500, "upstream boom")
    assert asyncio.run(tc.create_clip("tok", "123", retries=2, retry_delay=0)) is None
    assert s.calls >= 1


def test_channel_offline_is_not_permanent(helix):
    """404 'Channel offline' is a race against the stream ending, not a
    restriction. Killing the stream over it would remove channels that are
    perfectly clippable next time they go live."""
    helix(404, '{"error":"Not Found","message":"Channel offline."}')
    assert asyncio.run(tc.create_clip("tok", "123", retries=1, retry_delay=0)) is None


def test_stopping_a_stream_internally_matches_the_user_facing_delete():
    """A stream the system stops must look identical to one the user removed:
    gone from state, broadcast to their tabs, and the worker told to stop.
    Skipping the broadcast would leave a dead stream on screen until refresh,
    which the realtime contract forbids."""
    from src.dashboard import api

    events, removed = [], []

    async def fake_broadcast(ev, user_id=None):
        events.append((ev, user_id))

    async def fake_publish(channel, uid=""):
        removed.append((channel, uid))

    api._streams["u1:somechan"] = {"channel": "somechan", "user_id": "u1"}
    orig_b, orig_p = api.broadcast, api._publish_remove_stream
    api.broadcast, api._publish_remove_stream = fake_broadcast, fake_publish
    try:
        assert asyncio.run(api.stop_stream_internal("somechan", "u1")) is True
        assert "u1:somechan" not in api._streams
        assert events and events[0][0]["event"] == "stream_removed"
        assert events[0][1] == "u1", "event not scoped to the owning user"
        assert removed == [("somechan", "u1")], "worker was never told to stop"
        # Already gone: reports False rather than broadcasting a phantom removal.
        events.clear()
        assert asyncio.run(api.stop_stream_internal("somechan", "u1")) is False
        assert not events
    finally:
        api.broadcast, api._publish_remove_stream = orig_b, orig_p
        api._streams.pop("u1:somechan", None)
