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
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._land_suggestions)
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
