"""The posting queue. It REMINDS; it does not post.

That is the property most of these tests defend. The app holds no platform
credentials by design, so anything that lets the UI imply automation — or that
leaves a reminder pointing at a clip that no longer exists — is the bug.
"""

import json
import time
from pathlib import Path

import pytest

from src.publish import schedule as sched


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear()
    sched._loaded = False
    yield
    sched._items.clear()


def _add(uid="u1", due=None, **kw):
    return sched.add(uid, kw.pop("upload_id", "up1"), kw.pop("filename", "clip.mp4"),
                     kw.pop("caption", "nice"), kw.pop("platforms", ["tiktok"]),
                     due if due is not None else time.time() + 3600)


def test_due_is_derived_from_the_clock_not_stored():
    """A stored is-due flag goes wrong the moment the process restarts or an
    item is edited. Deriving it means the list is always right about itself."""
    item = _add(due=time.time() + 60)
    assert item.public()["due"] is False
    assert item.public(now=time.time() + 120)["due"] is True
    assert "due" not in json.loads(json.dumps(sched.asdict(item)))


def test_a_missed_post_is_distinguished_from_one_that_is_merely_due():
    """'Now' and 'you missed this yesterday' need different words — nobody is
    at their desk the second it fires."""
    item = _add(due=time.time() - sched.GRACE_S - 60)
    pub = item.public()
    assert pub["due"] and pub["missed"]
    assert not _add(due=time.time() - 5).public()["missed"]


def test_one_users_item_is_invisible_to_another():
    item = _add(uid="owner")
    assert sched.get(item.id, "someone_else") is None
    assert sched.set_status(item.id, "someone_else", sched.POSTED) is None
    assert sched.remove(item.id, "someone_else") is False
    assert sched.get(item.id, "owner") is not None


def test_deleting_the_clip_removes_anything_queued_for_it():
    """A reminder to post a clip that no longer exists is a reminder to do
    something impossible."""
    a = _add(upload_id="gone")
    b = _add(upload_id="stays")
    assert sched.drop_upload("gone", "u1") == [a.id]
    assert {i.id for i in sched.for_user("u1")} == {b.id}


def test_deleting_the_account_takes_the_queue_with_it():
    _add(uid="leaving"); _add(uid="leaving"); _add(uid="staying")
    assert sched.delete_all_for_user("leaving") == 2
    assert sched.for_user("leaving") == []
    assert len(sched.for_user("staying")) == 1


def test_each_item_is_announced_exactly_once():
    """newly_due marks as it returns, so a tab does not get the same nudge
    every 30 seconds forever."""
    _add(due=time.time() - 10)
    assert len(sched.newly_due()) == 1
    assert sched.newly_due() == []


def test_an_item_that_is_not_due_yet_is_not_announced():
    _add(due=time.time() + 3600)
    assert sched.newly_due() == []


def test_the_queue_is_capped_per_user():
    monkey = sched.MAX_PER_USER
    try:
        sched.MAX_PER_USER = 3
        for _ in range(3):
            _add()
        with pytest.raises(ValueError) as e:
            _add()
        assert "queued" in str(e.value)
    finally:
        sched.MAX_PER_USER = monkey


def test_an_over_long_caption_is_refused_before_it_is_stored():
    with pytest.raises(ValueError):
        _add(caption="x" * (sched.CAPTION_MAX + 1))


def test_the_queue_survives_a_restart():
    item = _add()
    sched._items.clear(); sched._loaded = False        # simulate a fresh process
    assert [i.id for i in sched.for_user("u1")] == [item.id]


def test_a_corrupt_index_does_not_take_the_whole_queue_down():
    sched._INDEX.write_text("[not json", encoding="utf-8")
    sched._items.clear(); sched._loaded = False
    assert sched.for_user("u1") == []          # empty, not an exception


def test_one_unreadable_row_does_not_discard_the_others():
    """A field added in a later version must not make every earlier item
    vanish from someone's queue."""
    good = {"id": "a", "user_id": "u1", "upload_id": "x", "filename": "f",
            "caption": "", "platforms": [], "due_at": 1.0}
    sched._INDEX.write_text(json.dumps([{"nonsense": True}, good]), encoding="utf-8")
    sched._items.clear(); sched._loaded = False
    assert [i.id for i in sched.for_user("u1")] == ["a"]


def test_writes_are_atomic():
    _add()
    assert not list(sched._INDEX.parent.glob("*.tmp")), "temp index left behind"


# ── every exported clip lands here, most without a time yet ──────────────────

def test_a_clip_can_be_queued_with_no_time_yet():
    """Every export lands in the Scheduler, and most arrive before their owner
    has decided when to post. Demanding a time at export would turn the
    scheduler into a chore instead of an inbox."""
    item = _add(due=0)
    pub = item.public()
    assert pub["scheduled"] is False
    assert pub["due"] is False and pub["missed"] is False, \
        "an undated clip must not read as due — 0 is not 'the epoch'"


def test_undated_clips_never_fire_a_reminder():
    _add(due=0)
    assert sched.newly_due() == []


def test_undated_clips_sort_after_scheduled_ones():
    """due_at 0 sorts first numerically, which would pin every undated clip
    above posts that actually have a time."""
    later = _add(due=time.time() + 9999)
    undated = _add(due=0)
    assert [i.id for i in sched.for_user("u1")] == [later.id, undated.id]


def test_rescheduling_re_arms_the_reminder():
    """Moving a missed post to tomorrow must nudge again. notified is sticky
    otherwise, and the new time would pass in silence."""
    item = _add(due=time.time() - 10)
    assert len(sched.newly_due()) == 1          # fires, marks notified
    sched.update(item.id, "u1", due_at=time.time() - 5)
    assert len(sched.newly_due()) == 1, "rescheduled post never nudges again"


def test_editing_the_caption_does_not_re_arm_the_reminder():
    """Only a time change should re-arm — otherwise fixing a typo re-notifies."""
    item = _add(due=time.time() - 10)
    sched.newly_due()
    sched.update(item.id, "u1", caption="fixed typo")
    assert sched.newly_due() == []


def test_the_render_shape_is_stored_so_the_list_need_not_download_the_video():
    """The Scheduler fit-checks every card against every platform. Measuring
    the file instead would pull every render on every page load."""
    item = sched.add("u1", "up1", "c.mp4", "", ["tiktok"], 0, duration_s=42.5, ratio="9:16")
    assert (item.public()["duration_s"], item.public()["ratio"]) == (42.5, "9:16")


def test_editing_cannot_reach_another_users_item():
    item = _add(uid="owner")
    assert sched.update(item.id, "someone_else", caption="hi") is None
