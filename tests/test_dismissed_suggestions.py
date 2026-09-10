"""A dismissed crowd suggestion must not come back on its own.

THE REPORTED BUG, and the mechanism behind it. Clearing the review queue made
suggested clips from the same stream reappear. Suggestions are not clips we
create: they are viewer clips that already exist on Twitch, and the poll keeps
returning them for `viewer_clips._WINDOW_SECS` after they are made. Two things
stopped a repeat, and dismissing the clip defeated both:

  * `SuggestionBuffer._emitted` — in memory, so a deploy empties it;
  * `_our_clip_identity()`, which excludes every `twitch_clip_id` in clips.json
    — and reject/delete/clear/cull all DELETE the row.

So the durable guard was the clip record itself, and removing the clip deleted
the only evidence it had been suggested. `test_the_old_guard_was_the_clip_row`
below reproduces exactly that, and is the reason the fix is a separate
tombstone rather than a bigger in-memory set.
"""

import importlib
import pathlib
import tempfile
from datetime import datetime, timezone

import pytest

from tests.test_suggested_clips import api_client  # noqa: F401


@pytest.fixture
def ds(tmp_path, monkeypatch):
    """A dismissal store on its own file, so tests never touch real state."""
    from src.trigger import dismissed_suggestions as mod
    monkeypatch.setattr(mod, "_FILE", tmp_path / "dismissed_suggestions.json")
    return mod


def sug(slug="abc", channel="Chan", moment=1_000_000.0, **kw):
    return dict({"suggested": True, "twitch_clip_id": slug,
                 "channel": channel, "created_at": moment}, **kw)


# ── the mechanism the fix replaces ───────────────────────────────────────────

def test_the_old_guard_was_the_clip_row():
    """PROOF OF THE ORIGINAL BUG, not a test of the fix.

    Held constant: the same viewer clip is offered to a fresh buffer, which is
    what a restarted service sees. The ONLY difference between the two halves
    is whether the slug is still in our stored clips. That is what makes this a
    diagnosis: dismissing the clip is what unblocked the re-suggestion.
    """
    from src.trigger import suggested_clips as sc
    row = {"id": "abc", "creator_id": "999", "title": "t", "duration": 30.0,
           "view_count": 5, "url": "u", "embed_url": "e", "thumbnail_url": "",
           "created_at": datetime.fromtimestamp(1_000_000, timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ")}
    sc.reset()
    kept = sc.buffer_for("chan")
    kept.offer([row], set(), {"abc"}, now=2.0)          # clip still in clips.json
    assert kept.ready(now=2.0 + sc.SETTLE_SECS + 1) == []

    sc.reset()                                          # a restart
    cleared = sc.buffer_for("chan")
    cleared.offer([row], set(), set(), now=2.0)         # clip was removed
    assert [s.slug for s in cleared.ready(now=2.0 + sc.SETTLE_SECS + 1)] == ["abc"], \
        "the buffer no longer re-suggests a cleared clip — this test is stale"
    sc.reset()


# ── the tombstone ────────────────────────────────────────────────────────────

def test_a_dismissed_slug_stays_dismissed(ds):
    assert ds.is_dismissed("u1", "chan", "abc", 1_000_000.0) is False
    assert ds.dismiss("u1", [sug()]) == 1
    assert ds.is_dismissed("u1", "chan", "abc", 1_000_000.0) is True


def test_it_survives_a_restart(ds):
    """The whole point. The in-memory guards already covered one process."""
    ds.dismiss("u1", [sug()])
    path = ds._FILE                    # capture BEFORE the reload resets it
    from src.trigger import dismissed_suggestions as mod
    reloaded = importlib.reload(mod)   # re-runs the module body, as a restart does
    reloaded._FILE = path              # a new process, same file still on disk
    try:
        assert reloaded.is_dismissed("u1", "chan", "abc", 1_000_000.0) is True
    finally:
        importlib.reload(mod)


def test_the_same_moment_under_another_viewers_slug_is_also_suppressed(ds):
    """Several viewers clip one moment and each gets a different slug. Matching
    only the exact slug would let the same 30 seconds return as a neighbour's
    clip, which to the user is indistinguishable from the reported bug."""
    ds.dismiss("u1", [sug(slug="abc", moment=1_000_000.0)])
    assert ds.is_dismissed("u1", "chan", "xyz", 1_000_010.0) is True


def test_the_exact_slug_is_matched_even_when_the_moment_does_not_line_up(ds):
    """The slug check is not redundant with the moment check. A timestamp can
    fail to parse (_parse_ts returns 0.0 on a malformed created_at) or arrive
    different from the one we stored, and the moment branch then matches
    nothing — but it is still, unambiguously, the same clip.
    """
    ds.dismiss("u1", [sug(slug="abc", moment=1_000_000.0)])
    assert ds.is_dismissed("u1", "chan", "abc", 0.0) is True
    assert ds.is_dismissed("u1", "chan", "abc", 5_000_000.0) is True


def test_a_genuinely_different_moment_still_gets_through(ds):
    """The suppression must not swallow the feature. A later moment on a
    channel the user dismissed one clip from is still a new suggestion."""
    ds.dismiss("u1", [sug(moment=1_000_000.0)])
    assert ds.is_dismissed("u1", "chan", "qqq", 1_000_500.0) is False


def test_it_does_not_reach_across_channels(ds):
    """Two streams can have a moment at the same instant. Suppressing across
    channels would drop a suggestion the user never saw — a worse failure than
    showing one twice."""
    ds.dismiss("u1", [sug(channel="chan", moment=1_000_000.0)])
    assert ds.is_dismissed("u1", "other", "xyz", 1_000_010.0) is False


def test_it_does_not_reach_across_users(ds):
    """The suggestion buffer is shared by every worker on a channel, so this is
    the specific way a per-channel guard would have been wrong: one user
    clearing their queue must not silence a suggestion for everyone else."""
    ds.dismiss("u1", [sug()])
    assert ds.is_dismissed("u2", "chan", "abc", 1_000_000.0) is False


def test_only_suggestions_are_remembered(ds):
    """A clip we created is excluded from intake by creator id and can never be
    offered back, so a tombstone for one would be dead weight."""
    assert ds.dismiss("u1", [{"twitch_clip_id": "t1", "channel": "c",
                              "created_at": 1.0}]) == 0
    assert ds.dismiss("u1", [sug(twitch_clip_id="")]) == 0


def test_dismissing_twice_records_one_tombstone(ds):
    ds.dismiss("u1", [sug()])
    assert ds.dismiss("u1", [sug()]) == 0


def test_tombstones_expire(ds):
    """A clip is only re-offerable while inside the poll's lookback window, so
    keeping these forever would grow the file for no benefit."""
    ds.dismiss("u1", [sug()])
    later = __import__("time").time() + ds.RETENTION_SECS + 1
    assert ds.is_dismissed("u1", "chan", "abc", 1_000_000.0, now=later) is False


def test_a_corrupt_file_does_not_take_the_pipeline_down(ds):
    ds._FILE.write_text("{not json", encoding="utf-8")
    assert ds.is_dismissed("u1", "chan", "abc", 1.0) is False
    assert ds.dismiss("u1", [sug()]) == 1


# ── undo is the only way back ────────────────────────────────────────────────

def test_undo_lifts_the_tombstone(ds):
    """Requirement, in the user's words: gone unless it comes back from the
    undo button. Restoring the clip while permanently blocking the moment would
    leave them with the clip and no idea why nothing like it arrived again."""
    ds.dismiss("u1", [sug()])
    assert ds.forget("u1", [sug()]) == 1
    assert ds.is_dismissed("u1", "chan", "abc", 1_000_000.0) is False


def test_forgetting_leaves_other_dismissals_alone(ds):
    ds.dismiss("u1", [sug(slug="a", moment=1.0), sug(slug="b", moment=9999.0)])
    ds.forget("u1", [sug(slug="a", moment=1.0)])
    assert ds.is_dismissed("u1", "chan", "b", 9999.0) is True


# ── the endpoints ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,method", [
    ("/clips/{id}/reject", "post"),
    ("/clips/{id}", "delete"),
])
def test_every_single_clip_removal_records_a_dismissal(path, method):
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api)
    assert src.count("_dismissed.dismiss(") == 4, \
        "a removal path stopped recording dismissals, or a new one was added"


def test_every_bulk_removal_records_a_dismissal():
    """reject, delete, clear-pending and bulk-cull all delete the row, so all
    four must leave a tombstone. Clearing is the one that was reported."""
    import inspect
    from src.dashboard import api
    for fn in (api.reject_clip, api.delete_clip_endpoint,
               api.clear_pending_clips, api.bulk_cull_clips):
        assert "_dismissed.dismiss(" in inspect.getsource(fn), fn.__name__


def test_undo_forgets():
    import inspect
    from src.dashboard import api
    assert "_dismissed.forget(" in inspect.getsource(api.undo_last)


def test_an_expiring_undo_entry_does_not_forget():
    """Only an actual undo lifts a dismissal. An entry that merely times out
    means the user never took it back — the "no" stands."""
    import inspect
    from src.dashboard import api
    assert "_dismissed.forget(" not in inspect.getsource(api._drop_undo_entry)


# ── the worker consults it ───────────────────────────────────────────────────

def test_the_worker_checks_before_landing_a_suggestion():
    """Reads _land_for_user, not _land_suggestions. The per-user half was split
    out when suggestions started fanning out to every watcher of a channel —
    _land_suggestions now decides WHO gets offered a moment, and this decides
    whether a given user actually receives it, which is where the dismissal
    lives."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker._land_for_user)
    assert "dismissed_suggestions.is_dismissed(" in src, \
        "suggestions are landed without checking whether they were dismissed"
    # The branch must SKIP, not merely log. Window is the guard's own block —
    # wide enough to clear the log line, narrow enough that it cannot pass on
    # some unrelated `continue` further down the loop.
    after = src.split("is_dismissed(")[1]
    assert "continue" in after[:after.index("if used >= room")], \
        "the check does not actually skip the suggestion"


def test_the_check_is_not_inside_the_shared_buffer():
    """The buffer is per channel and shared across users; the dismissal is per
    user. Putting the check there would make one user's clear suppress
    everyone's suggestion."""
    import inspect
    from src.trigger import suggested_clips
    assert "dismissed" not in inspect.getsource(suggested_clips).lower()


def test_it_cannot_touch_triggered_clips():
    """Blast radius. The store is consulted only where suggestions are landed —
    nothing on the trigger or clip-creation path can be suppressed by it."""
    import inspect
    from src.ingestion import stream_worker
    from src.processor import clip_processor
    for mod in (stream_worker, clip_processor):
        src = inspect.getsource(mod)
        for i, line in enumerate(src.splitlines()):
            if "is_dismissed" in line:
                fn = "\n".join(src.splitlines()[max(0, i - 60):i])
                assert "_land_suggestions" in fn, \
                    f"{mod.__name__}: is_dismissed consulted outside suggestion landing"


# ── end to end, through the real endpoints ───────────────────────────────────

def test_clearing_the_queue_then_undoing_leaves_no_tombstone(api_client, ds):
    """THE REPORTED FLOW, both halves. Clear the queue and the suggestion must
    be suppressed; press undo and the suppression must be gone, or the user
    gets their clip back and then silently never sees that moment again."""
    api = api_client.api
    api._clips["sug1"] = {
        "id": "sug1", "user_id": "punter", "channel": "aceu", "status": "pending",
        "created_at": 1_000_000.0, "suggested": True, "twitch_clip_id": "slug1",
        "platform": "twitch", "trigger_signals": [], "trigger_score": 0.0}

    assert api_client.post("/clips/clear-pending").json()["removed"] == 1
    assert ds.is_dismissed("punter", "aceu", "slug1", 1_000_000.0) is True

    assert api_client.post("/clips/undo").json()["restored"] == 1
    assert ds.is_dismissed("punter", "aceu", "slug1", 1_000_000.0) is False


def test_rejecting_a_suggestion_dismisses_it(api_client, ds):
    api = api_client.api
    api._clips["sug1"] = {
        "id": "sug1", "user_id": "punter", "channel": "aceu", "status": "pending",
        "created_at": 1_000_000.0, "suggested": True, "twitch_clip_id": "slug1",
        "platform": "twitch", "trigger_signals": [], "trigger_score": 0.0}
    api_client.post("/clips/sug1/reject")
    assert ds.is_dismissed("punter", "aceu", "slug1", 1_000_000.0) is True


def test_deleting_a_suggestion_dismisses_it(api_client, ds):
    api = api_client.api
    api._clips["sug1"] = {
        "id": "sug1", "user_id": "punter", "channel": "aceu", "status": "approved",
        "created_at": 1_000_000.0, "suggested": True, "twitch_clip_id": "slug1",
        "platform": "twitch", "trigger_signals": [], "trigger_score": 0.0}
    api_client.delete("/clips/sug1")
    assert ds.is_dismissed("punter", "aceu", "slug1", 1_000_000.0) is True


def test_removing_a_TRIGGERED_clip_records_nothing(api_client, ds):
    """Blast radius on the normal path. Our own clips are excluded from intake
    by creator id and can never be offered back, so nothing about ordinary
    housekeeping should write a tombstone."""
    api = api_client.api
    api._clips["trig1"] = {
        "id": "trig1", "user_id": "punter", "channel": "aceu", "status": "pending",
        "created_at": 1_000_000.0, "twitch_clip_id": "mine1",
        "platform": "twitch", "trigger_signals": [], "trigger_score": 71.0}
    api_client.post("/clips/clear-pending")
    assert ds.count_for("punter") == 0


# ── the batch must survive one bad row ───────────────────────────────────────

def test_one_unreadable_row_does_not_lose_the_whole_batch(ds):
    """THE FAILURE MODE THAT MATCHES THE REPORT EXACTLY.

    clear-pending deletes every pending row and saves, THEN calls dismiss()
    once with all of them. _key() used to do a bare float(created_at), so a
    single clip carrying anything non-numeric raised straight out of dismiss()
    — after the clips were already gone. Every tombstone in that batch was
    lost and every one of those moments was free to come back, which is
    "I cleared the queue and they came back" precisely.

    The bad row now costs only its own moment-match; it still gets its
    exact-slug tombstone, and its siblings are untouched.
    """
    uid = "u"
    ds.dismiss(uid, [sug(slug="A", moment=1_700_000_000.0),
                     sug(slug="B", moment="2026-08-29T12:00:00Z"),
                     sug(slug="C", moment=1_700_000_100.0)])
    for slug, moment in (("A", 1_700_000_000.0), ("C", 1_700_000_100.0)):
        assert ds.is_dismissed(uid, "Chan", slug, moment), \
            f"{slug} lost its tombstone because a sibling was malformed"
    assert ds.is_dismissed(uid, "Chan", "B", 0.0), \
        "the malformed row lost even its exact-slug tombstone"


@pytest.mark.parametrize("moment", [None, "", "not-a-date", float("nan"),
                                    float("inf"), [], {}, True])
def test_no_clip_shape_can_break_a_removal(ds, moment):
    """Whatever ends up on a clip row, removing it must not raise: the caller
    has already deleted it and cannot put it back."""
    uid = "u"
    assert ds.dismiss(uid, [sug(slug="X", moment=moment)]) >= 0
    assert ds.is_dismissed(uid, "Chan", "X", 0.0), \
        "the slug tombstone was not recorded"


def test_a_hand_edited_tombstone_file_does_not_break_the_check(ds):
    """The file survives restarts, so it can hold anything by the time it is
    read back. is_dismissed runs inside the worker's landing loop — raising
    there would stop suggestions for that user entirely."""
    import json
    ds._FILE.write_text(json.dumps({"u": [
        {"slug": "A", "channel": "chan", "moment": "oops", "at": "yesterday"},
        "not-a-row",
        {"slug": "B", "channel": "chan", "moment": 1_700_000_000.0, "at": 0},
    ]}), encoding="utf-8")
    assert ds.is_dismissed("u", "chan", "A", 1_700_000_000.0), \
        "an unreadable row dropped a tombstone that was validly recorded"
    assert not ds.is_dismissed("u", "chan", "ZZZ", 1.0)


def test_forget_survives_the_same_junk(ds):
    """Undo calls this on the same rows; it must not raise either."""
    uid = "u"
    ds.dismiss(uid, [sug(slug="A", moment=1_700_000_000.0)])
    ds.forget(uid, [sug(slug="A", moment="not-a-date"), "not-a-clip", None])
    assert not ds.is_dismissed(uid, "Chan", "A", 1_700_000_000.0), \
        "undo did not lift the tombstone"


# ── the last hop, driven rather than read ────────────────────────────────────
#
# test_the_worker_checks_before_landing_a_suggestion above reads the worker's
# SOURCE. That is worth having, but it cannot see the failure that would
# actually let a moment come back: the worker passing a different user id,
# channel or slug than the one the dismissal was filed under. A source scan
# stays green through every one of those. This runs the real
# _land_suggestions against a real store and watches what it does.

class _Ripe:
    """One ripe suggestion, shaped as SuggestionBuffer.ready() returns them."""

    def __init__(self, slug, channel="aceu", moment=1_000_000.0):
        self.slug, self.channel, self.created_at = slug, channel, moment
        self.url = f"https://clips.twitch.tv/{slug}"
        self.embed_url = self.url + "/embed"
        self.thumbnail_url = self.url + "/thumb"
        self.title, self.view_count = "a moment", 10
        self.clipper_count, self.duration = 3, 30.0


class _Buf:
    def __init__(self, *ripe):
        self._ripe = list(ripe)

    def ready(self):
        return self._ripe


async def _land(worker, buf, landed):
    """Run the real _land_suggestions, capturing what it would create."""
    from src.dashboard import api as dashboard_api
    import src.ingestion.stream_worker as sw

    async def _capture(row):
        landed.append(row)

    orig_notify = dashboard_api.notify_clip_ready
    orig_room = dashboard_api.suggestion_room
    dashboard_api.notify_clip_ready = _capture
    dashboard_api.suggestion_room = lambda uid: (0, 5)   # plenty of budget
    try:
        await sw.StreamWorker._land_suggestions(worker, buf)
    finally:
        dashboard_api.notify_clip_ready = orig_notify
        dashboard_api.suggestion_room = orig_room


class _Worker:
    """The bare attributes _land_suggestions touches on self.

    `channel` is needed since suggestions began fanning out to every watcher:
    the worker asks the dashboard who else is on this channel. In these tests
    the stream registry is empty, so the fan-out falls back to this worker's
    own user — which is exactly the single-user case these were written for.
    """

    def __init__(self, uid, channel="lacy"):
        self._config = type("C", (), {"user_id": uid, "platform_name": "twitch",
                                      "channel": channel})()
        self._stream_info = None


def test_the_worker_really_drops_a_moment_the_user_cleared(ds):
    """THE WHOLE POINT, end to end and behavioural: file the dismissal exactly
    as clear-pending does, then let the real worker try to land it again."""
    import asyncio
    ds.dismiss("punter", [sug(slug="slug1", channel="aceu", moment=1_000_000.0)])

    landed = []
    asyncio.run(_land(_Worker("punter"),
                      _Buf(_Ripe("slug1", "aceu", 1_000_000.0)), landed))
    assert landed == [], \
        "a moment the user cleared was landed in their queue again"


def test_the_same_worker_still_lands_something_they_never_saw(ds):
    """The other half. Without this the test above passes just as well if
    _land_suggestions has quietly stopped landing anything at all."""
    import asyncio
    ds.dismiss("punter", [sug(slug="slug1", channel="aceu", moment=1_000_000.0)])

    landed = []
    asyncio.run(_land(_Worker("punter"),
                      _Buf(_Ripe("fresh", "aceu", 2_000_000.0)), landed))
    assert len(landed) == 1, "a brand new moment was not landed"
    assert landed[0]["twitch_clip_id"] == "fresh"


def test_a_neighbours_clip_of_the_cleared_moment_is_dropped_too(ds):
    """Several viewers clip the same seconds and each gets its own slug.
    Suppressing only the exact slug would let the same moment back in under a
    different one, which to the user is the bug they reported."""
    import asyncio
    ds.dismiss("punter", [sug(slug="slug1", channel="aceu", moment=1_000_000.0)])

    landed = []
    asyncio.run(_land(_Worker("punter"),
                      _Buf(_Ripe("neighbour", "aceu", 1_000_002.0)), landed))
    assert landed == [], "the same moment came back under another viewer's slug"


def test_another_users_clear_does_not_suppress_this_users_suggestion(ds):
    """The buffer is shared per channel; the dismissal is per user. One person
    clearing their queue must not silently cost everyone else the moment."""
    import asyncio
    ds.dismiss("punter", [sug(slug="slug1", channel="aceu", moment=1_000_000.0)])

    landed = []
    asyncio.run(_land(_Worker("someone_else"),
                      _Buf(_Ripe("slug1", "aceu", 1_000_000.0)), landed))
    assert len(landed) == 1, \
        "one user's clear suppressed a suggestion another user never saw"
