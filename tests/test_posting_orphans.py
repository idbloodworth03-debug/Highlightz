"""A restart must not strand a post forever.

THE BUG THIS FILE EXISTS TO PREVENT COMING BACK. `posting` is a claim about a
coroutine running in THIS process. A deploy kills every one of them, but the
status is on disk and survives — and afterwards nothing could undo it:

  * `due_for_posting` returns only pending and failed, so the worker never
    looks at the item again;
  * `/publish/schedule/{id}/post` (the Retry button) refused any item whose
    status was `posting` with 409 "Already posting";
  * the card's chip carries `animation: scPulse 1s infinite`, so it blinks at
    1 Hz for ever.

The owner reported it as "the scheduler starts to flash almost as if it is
buffering", on 2026-09-18, after a deploy landed inside TikTok's three-minute
publish poll. Nothing short of editing schedule.json on the server could
recover the item.
"""

import pytest

from src.publish import schedule as sched


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    monkeypatch.setattr(sched, "_items", {})
    monkeypatch.setattr(sched, "_loaded", True)

    def add(status, results):
        it = sched.Item(id="i" + str(len(sched._items)), user_id="u1",
                        upload_id="up1", filename="clip.mp4", caption="c",
                        platforms=["tiktok"], due_at=1.0)
        it.status = status
        it.results = results
        sched._items[it.id] = it
        return it

    return type("S", (), {"add": staticmethod(add)})


def test_an_item_left_posting_by_a_restart_is_freed(store):
    it = store.add(sched.POSTING, {"tiktok": {"status": sched.R_POSTING, "at": 1.0}})
    changed = sched.reclaim_posting_orphans()

    assert [i.id for i in changed] == [it.id]
    assert it.status == sched.FAILED, "the chip would still be pulsing"
    assert it.results["tiktok"]["status"] == sched.R_FAILED


def test_it_is_marked_retryable_because_the_post_may_have_landed(store):
    """We lost track; we did not observe a failure. Saying otherwise would
    stop the automatic retry AND tell the user something we do not know."""
    it = store.add(sched.POSTING, {"tiktok": {"status": sched.R_POSTING, "at": 1.0}})
    sched.reclaim_posting_orphans()
    row = it.results["tiktok"]
    assert row["retryable"] is True
    assert "restarted" in row["error"]
    assert "lost track" in row["error"]


def test_a_platform_that_already_posted_is_left_alone(store):
    """Half-posted items are the dangerous case: reclaiming the posted row
    too would make the retry send the clip to that platform twice."""
    it = store.add(sched.POSTING, {
        "youtube": {"status": sched.R_POSTED, "url": "https://y/1", "at": 1.0},
        "tiktok": {"status": sched.R_POSTING, "at": 2.0}})
    sched.reclaim_posting_orphans()
    assert it.results["youtube"]["status"] == sched.R_POSTED
    assert it.results["youtube"]["url"] == "https://y/1"
    assert it.results["tiktok"]["status"] == sched.R_FAILED
    assert it.status == sched.FAILED


def test_items_that_were_not_posting_are_untouched(store):
    pend = store.add(sched.PENDING, {})
    done = store.add(sched.POSTED, {"tiktok": {"status": sched.R_POSTED, "at": 1.0}})
    fail = store.add(sched.FAILED, {"tiktok": {"status": sched.R_FAILED, "at": 1.0,
                                               "error": "nope", "retryable": False}})
    assert sched.reclaim_posting_orphans() == []
    assert (pend.status, done.status, fail.status) == (sched.PENDING, sched.POSTED,
                                                       sched.FAILED)
    assert fail.results["tiktok"]["error"] == "nope", "an old failure was rewritten"


def test_running_it_twice_changes_nothing_the_second_time(store):
    store.add(sched.POSTING, {"tiktok": {"status": sched.R_POSTING, "at": 1.0}})
    assert len(sched.reclaim_posting_orphans()) == 1
    assert sched.reclaim_posting_orphans() == []


def test_the_reclaimed_item_is_visible_to_the_worker_again(store):
    """The whole point: failed and due means due_for_posting returns it."""
    it = store.add(sched.POSTING, {"tiktok": {"status": sched.R_POSTING, "at": 1.0}})
    sched.reclaim_posting_orphans()
    assert it.id in [i.id for i in sched.due_for_posting(now=1000.0)]


def test_it_survives_the_round_trip_to_disk(store):
    """It is written, not just fixed in memory — otherwise the next restart
    strands it all over again."""
    store.add(sched.POSTING, {"tiktok": {"status": sched.R_POSTING, "at": 1.0}})
    sched.reclaim_posting_orphans()
    sched._items.clear()
    sched._loaded = False
    sched._load()
    assert [i.status for i in sched._items.values()] == [sched.FAILED]


# ── the wiring ───────────────────────────────────────────────────────────────

def test_startup_reclaims_before_the_first_worker_pass():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.schedule_due_task)
    assert "reclaim_posting_orphans()" in src
    assert src.index("reclaim_posting_orphans()") < src.index("while True:"), \
        "reclaiming inside the loop would fight live posts every 30 seconds"


def test_retry_refuses_only_a_post_that_is_really_in_flight():
    """Refusing on the stored status is what made the orphan unrecoverable
    from the UI. `_inflight` is this process's live truth."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.publish_schedule_post_now)
    assert "poster._inflight" in src
    assert "Already posting." in src
