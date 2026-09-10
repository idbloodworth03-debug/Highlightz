"""
Highlights outrank triggered clips on the free tier, and duplicates of one
moment stop arriving in pairs.

TWO CHANGES, ONE FILE, because they are the same complaint from two ends: the
free queue should hold the best moments available, and it should hold each of
them once.

WHAT MAKES THE EVICTION HALF DELICATE. Automatic deletion of a user's pending
clips was removed in 2026-08-03 precisely because a full queue silently
destroying work is hostile, and this puts a narrow version of it back. Every
one of those constraints is a test here: it only displaces a TRIGGERED clip,
only for a Highlight, only on free, only the weakest one, only while there is
something to displace, and it tells the open tab. Any of those slipping is the
old bad behaviour returning under a new name.
"""

import pytest

from src.billing import plans
from src.dashboard import api


NOW = 1_800_000_000.0

PEOPLE = {
    "freebie": {"id": "freebie", "subscription_status": "none", "plan": "free"},
    "starter": {"id": "starter", "subscription_status": "active", "plan": "starter"},
    "pro":     {"id": "pro", "subscription_status": "active", "plan": "pro"},
}

FREE_PENDING = plans.PLAN_LIMITS["free"]["max_pending"]
FREE_SUGGEST = plans.PLAN_LIMITS["free"]["max_suggested"]


@pytest.fixture
def store(monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda *a, **k: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    sent = []

    async def _capture(msg, user_id=None):
        sent.append(msg)
    monkeypatch.setattr(api, "broadcast", _capture)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "notify_clip_missed", _noop)

    from src.stats import stream_stats
    monkeypatch.setattr(stream_stats, "record", lambda *a, **k: None)

    api._clips.clear()
    yield sent
    api._clips.clear()


def _put(uid, cid, *, suggested=False, score=50.0, ts=NOW, channel="lacy"):
    api._clips[cid] = {"id": cid, "user_id": uid, "channel": channel,
                       "status": "pending", "suggested": suggested,
                       "trigger_score": score, "created_at": ts,
                       "platform": "twitch"}
    return api._clips[cid]


def _new(uid, cid, *, suggested=False, score=50.0, ts=NOW, channel="lacy"):
    return {"id": cid, "user_id": uid, "channel": channel, "status": "pending",
            "suggested": suggested, "trigger_score": score, "created_at": ts,
            "platform": "twitch"}


def _fill(uid, n, *, suggested=False, score=50.0, channel="lacy", start=0):
    """n pending clips, spaced far enough apart to never trip the dedup."""
    for i in range(n):
        _put(uid, f"{'s' if suggested else 't'}{start + i}", suggested=suggested,
             score=score, ts=NOW - (i + 1) * 10_000, channel=channel)


# ── the eviction ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_highlight_takes_a_slot_when_its_own_budget_is_full(store):
    """The whole point. Free holds five Highlights; a sixth used to be dropped
    while twenty triggered clips sat next to it."""
    _fill("freebie", FREE_SUGGEST, suggested=True)
    _fill("freebie", 3, suggested=False, score=60.0)
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True, ts=NOW))
    assert "hot" in api._clips
    assert sum(1 for c in api._clips.values() if c.get("suggested")) == FREE_SUGGEST + 1


@pytest.mark.asyncio
async def test_the_weakest_triggered_clip_is_the_one_displaced(store):
    """NOT the oldest. Displacing by age eventually throws away a 95 to make
    room, which loses on the very measure this is meant to serve."""
    _fill("freebie", FREE_SUGGEST, suggested=True)
    _put("freebie", "great", score=95.0, ts=NOW - 50_000)   # oldest, best
    _put("freebie", "weak", score=51.0, ts=NOW - 10_000)    # newest, worst
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    assert "weak" not in api._clips
    assert "great" in api._clips


@pytest.mark.asyncio
async def test_the_older_clip_loses_a_tie_on_score(store):
    _fill("freebie", FREE_SUGGEST, suggested=True)
    _put("freebie", "older", score=60.0, ts=NOW - 90_000)
    _put("freebie", "newer", score=60.0, ts=NOW - 10_000)
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    assert "older" not in api._clips and "newer" in api._clips


@pytest.mark.asyncio
async def test_it_stops_once_the_queue_is_all_highlights(store):
    """The bound. Without it this is unbounded eviction wearing a new hat."""
    _fill("freebie", FREE_SUGGEST, suggested=True)
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    assert "hot" not in api._clips, "nothing was left to displace"


@pytest.mark.asyncio
async def test_a_triggered_clip_never_displaces_anything(store):
    """Priority runs one way. A triggered clip hitting its own cap is dropped
    exactly as before — this feature must not become general eviction."""
    _fill("freebie", FREE_PENDING, suggested=False, score=60.0)
    _put("freebie", "s0", suggested=True, ts=NOW - 500_000)
    before = set(api._clips)
    await api.notify_clip_ready(_new("freebie", "another", score=99.0))
    assert "another" not in api._clips
    assert set(api._clips) == before, "a triggered clip evicted something"


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", ["starter", "pro"])
async def test_paid_tiers_do_not_evict(store, uid):
    """They have room for both and never need the trade. Eviction there would
    be destroying a clip somebody is paying to keep."""
    cap = plans.PLAN_LIMITS[PEOPLE[uid]["plan"]]["max_suggested"]
    _fill(uid, cap, suggested=True)
    _put(uid, "t_keep", score=10.0, ts=NOW - 10_000)
    await api.notify_clip_ready(_new(uid, "hot", suggested=True))
    assert "t_keep" in api._clips
    assert "hot" not in api._clips


@pytest.mark.asyncio
async def test_only_this_users_clips_are_ever_touched(store):
    """Eviction reads the shared clip store. Scoping it by user is the only
    thing stopping one account's Highlight from deleting another's clip."""
    _fill("freebie", FREE_SUGGEST, suggested=True)
    _put("pro", "someone_elses", score=1.0, ts=NOW - 99_000)
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    assert "someone_elses" in api._clips


@pytest.mark.asyncio
async def test_an_approved_clip_is_never_displaced(store):
    """Only the pending queue is under pressure. An approved clip is something
    the user has already decided to keep."""
    _fill("freebie", FREE_SUGGEST, suggested=True)
    kept = _put("freebie", "approved", score=1.0, ts=NOW - 99_000)
    kept["status"] = "approved"
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    assert "approved" in api._clips
    assert "hot" not in api._clips, "an approved clip was treated as displaceable"


@pytest.mark.asyncio
async def test_the_open_tab_is_told_about_the_eviction(store):
    """Realtime contract. Without this the queue shows a clip that is no longer
    there until the user reloads — and 'refresh to see it' is a bug."""
    _fill("freebie", FREE_SUGGEST, suggested=True)
    _put("freebie", "weak", score=20.0, ts=NOW - 10_000)
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    events = [m["event"] for m in store]
    assert "clip_removed" in events
    assert events.index("clip_removed") < events.index("clip_ready"), \
        "the arrival was announced before the departure"
    removed = next(m for m in store if m["event"] == "clip_removed")
    assert removed["clip_id"] == "weak"


@pytest.mark.asyncio
async def test_the_evicted_clips_file_is_cleaned_up(store, monkeypatch):
    """It is disk nothing can free through the UI once the record is gone."""
    deleted = []
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: deleted.append(c["id"]))
    _fill("freebie", FREE_SUGGEST, suggested=True)
    _put("freebie", "weak", score=20.0, ts=NOW - 10_000)
    await api.notify_clip_ready(_new("freebie", "hot", suggested=True))
    assert deleted == ["weak"]


# ── the duplicates ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_highlights_of_one_moment_land_once(store):
    """THE REPORTED BUG. A Highlight is timestamped when a VIEWER clipped, so
    one person reacting on reflex and another after the replay are routinely a
    minute apart while capturing the same play."""
    _put("freebie", "first", suggested=True, ts=NOW)
    await api.notify_clip_ready(
        _new("freebie", "second", suggested=True, ts=NOW + 90))
    assert "second" not in api._clips


@pytest.mark.asyncio
async def test_highlights_far_enough_apart_are_still_two_moments(store):
    _put("freebie", "first", suggested=True, ts=NOW)
    await api.notify_clip_ready(
        _new("freebie", "later", suggested=True, ts=NOW + 400))
    assert "later" in api._clips


@pytest.mark.asyncio
async def test_the_wider_window_does_not_apply_to_triggered_clips(store):
    """Their timestamps come from the detector firing, not a human reacting,
    so widening theirs would suppress genuinely separate moments on a busy
    channel — trading a visible duplicate for an invisible miss."""
    _put("freebie", "t_first", suggested=False, ts=NOW)
    await api.notify_clip_ready(_new("freebie", "t_second", ts=NOW + 90))
    assert "t_second" in api._clips


@pytest.mark.asyncio
async def test_a_highlight_is_still_deduped_against_our_own_clip(store):
    """If the detector already caught the moment, the crowd's version of it is
    a duplicate — at the narrow window, since ours is timestamped when it
    fired."""
    _put("freebie", "ours", suggested=False, ts=NOW)
    await api.notify_clip_ready(_new("freebie", "theirs", suggested=True, ts=NOW + 20))
    assert "theirs" not in api._clips


@pytest.mark.asyncio
async def test_a_different_channel_is_never_a_duplicate(store):
    _put("freebie", "on_lacy", suggested=True, ts=NOW, channel="lacy")
    await api.notify_clip_ready(
        _new("freebie", "on_marlon", suggested=True, ts=NOW + 5, channel="marlon"))
    assert "on_marlon" in api._clips


@pytest.mark.asyncio
async def test_another_users_clip_is_never_a_duplicate(store):
    """Two people watching one channel each get their own copy — the dedup is
    about one queue holding a moment twice, not about the moment existing."""
    _put("pro", "theirs", suggested=True, ts=NOW)
    await api.notify_clip_ready(_new("freebie", "mine", suggested=True, ts=NOW + 5))
    assert "mine" in api._clips


# ── the source-side guard ────────────────────────────────────────────────────

def test_the_resuggest_guard_is_wider_than_the_clustering_window():
    """They were one constant and did different jobs. CLUSTER_SECS groups clips
    INTO a moment — widening it merges separate plays and loses one. The
    re-suggest guard asks 'have I sent this already', and its failure is a
    straggler forming a second cluster minutes later."""
    from src.trigger import suggested_clips as sc
    assert sc.RESUGGEST_SECS > sc.CLUSTER_SECS


def test_a_late_straggler_does_not_become_a_second_suggestion():
    from src.trigger import suggested_clips as sc
    buf = sc.SuggestionBuffer("lacy")
    buf._emitted_moments.append(NOW)
    assert buf._already_suggested_moment(NOW + 90) is True
    assert buf._already_suggested_moment(NOW + 400) is False


def test_free_is_the_only_tier_with_highlight_priority():
    assert plans.PLAN_LIMITS["free"]["highlight_priority"] is True
    for tier in ("starter", "pro", "locked"):
        assert plans.PLAN_LIMITS[tier]["highlight_priority"] is False, tier
