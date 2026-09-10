"""
Every user watching a channel gets its Highlights — not just one of them.

THE BUG THIS FIXES, because it is not obvious from either side alone. The
viewer-clip poll is gated per CHANNEL so five users watching one streamer cost
one Helix call rather than five. That gate means exactly one worker per channel
reaches the suggestion code each cycle. And `SuggestionBuffer.ready()` is
destructive — it pops the ripened candidates. So the winning worker drained the
buffer and handed every moment to its own user alone.

The result: on any channel with more than one watcher, exactly ONE account
received Highlights and which one was a race. A new user adding a channel
somebody already monitored received none, permanently. From inside that account
the feature simply did not exist.
"""

import pytest

from src.dashboard import api
from src.ingestion import stream_worker as sw


class _Sug:
    def __init__(self, slug, ts=1000.0, channel="lacy"):
        self.slug, self.created_at, self.channel = slug, ts, channel
        self.url = self.embed_url = self.thumbnail_url = "u"
        self.title, self.duration = "moment", 30.0
        self.view_count, self.clipper_count = 5, 2


class _Buf:
    """Destructive, exactly like the real one — ready() empties itself."""

    def __init__(self, items):
        self._items = list(items)

    def ready(self):
        out, self._items = self._items, []
        return out


class _Worker:
    def __init__(self, uid, channel="lacy"):
        self._config = type("C", (), {"user_id": uid, "platform_name": "twitch",
                                      "channel": channel})()
        self._stream_info = None


@pytest.fixture
def landed(monkeypatch):
    got = []

    async def _capture(row):
        got.append(row)
    monkeypatch.setattr(api, "notify_clip_ready", _capture)
    monkeypatch.setattr(api, "suggestion_room", lambda uid: (0, 5))
    from src.trigger import dismissed_suggestions
    monkeypatch.setattr(dismissed_suggestions, "is_dismissed",
                        lambda *a, **k: False)
    api._streams.clear()
    yield got
    api._streams.clear()


def _watch(uid, channel="lacy"):
    api._streams[f"{uid}:{channel}"] = {"channel": channel, "user_id": uid,
                                        "status": "live"}


@pytest.mark.asyncio
async def test_every_watcher_of_the_channel_receives_the_moment(landed):
    """THE FIX. One worker ripens it; all three accounts get it."""
    _watch("alice"); _watch("bob"); _watch("carol")
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([_Sug("m1")]))
    assert {r["user_id"] for r in landed} == {"alice", "bob", "carol"}


@pytest.mark.asyncio
async def test_a_new_user_on_an_already_watched_channel_is_not_left_out(landed):
    """The reported symptom, stated directly: a brand-new account added a
    channel somebody else was already monitoring and saw zero Highlights."""
    _watch("veteran"); _watch("newcomer")
    # The veteran's worker is the one that wins the shared poll.
    await sw.StreamWorker._land_suggestions(_Worker("veteran"), _Buf([_Sug("m1")]))
    assert "newcomer" in {r["user_id"] for r in landed}


@pytest.mark.asyncio
async def test_each_user_gets_their_own_clip_id(landed):
    """A shared id would collide in the clip store and only one account would
    ever see the moment — the same bug wearing a different hat."""
    _watch("alice"); _watch("bob")
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([_Sug("m1")]))
    ids = [r["id"] for r in landed]
    assert len(ids) == len(set(ids)) == 2


@pytest.mark.asyncio
async def test_watchers_of_another_channel_get_nothing(landed):
    _watch("alice", "lacy"); _watch("stranger", "marlon")
    await sw.StreamWorker._land_suggestions(_Worker("alice", "lacy"),
                                            _Buf([_Sug("m1", channel="lacy")]))
    assert {r["user_id"] for r in landed} == {"alice"}


@pytest.mark.asyncio
async def test_the_running_worker_is_served_even_if_the_registry_lags(landed):
    """The stream record is written by the dashboard and the worker starts
    around the same moment. Losing this worker's own suggestions to that gap
    would be a worse bug than the one being fixed."""
    # Registry empty — nobody is listed as watching anything.
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([_Sug("m1")]))
    assert {r["user_id"] for r in landed} == {"alice"}


@pytest.mark.asyncio
async def test_the_worker_is_not_served_twice_when_it_is_registered(landed):
    """It appears in its own watcher list. Appending it again would land every
    moment twice for whichever account happened to win the poll."""
    _watch("alice")
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([_Sug("m1")]))
    assert len(landed) == 1


@pytest.mark.asyncio
async def test_each_user_is_metered_by_their_own_budget(landed, monkeypatch):
    """Fan-out must not spend one user's allowance on another's behalf."""
    rooms = {"alice": (0, 5), "bob": (5, 5)}     # bob is full
    monkeypatch.setattr(api, "suggestion_room", lambda uid: rooms[uid])
    _watch("alice"); _watch("bob")
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([_Sug("m1")]))
    assert {r["user_id"] for r in landed} == {"alice"}


@pytest.mark.asyncio
async def test_one_users_dismissal_does_not_suppress_anyone_else(landed, monkeypatch):
    from src.trigger import dismissed_suggestions
    monkeypatch.setattr(dismissed_suggestions, "is_dismissed",
                        lambda uid, *a, **k: uid == "bob")
    _watch("alice"); _watch("bob")
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([_Sug("m1")]))
    assert {r["user_id"] for r in landed} == {"alice"}


@pytest.mark.asyncio
async def test_an_empty_buffer_lands_nothing_for_anybody(landed):
    _watch("alice"); _watch("bob")
    await sw.StreamWorker._land_suggestions(_Worker("alice"), _Buf([]))
    assert landed == []
