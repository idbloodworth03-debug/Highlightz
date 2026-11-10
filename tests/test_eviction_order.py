"""What goes first when the clip store is over its cap (2026-11-10).

Prod, after a Pro user's "nothing but dud videos" on a Kick channel: 938 files
deleted to make room in 8 hours, 3,165 unreviewed clips with 2,781 already
videoless, 1,580 of them Kick. Oldest-first within pending treated a paying
customer's Kick file (gone for good) like a free account's Twitch file (can be
fetched from Twitch again). The order is now: no record first, then pending
before approved, then free before paid, then Twitch before Kick, then oldest.
"""

import os
import time

import pytest

from src.clips import files


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_ROOT", tmp_path)
    monkeypatch.setattr(files.settings, "clip_file_max_total_mb", 1)   # 1 MB cap, 0.9 MB target

    def put(name, age_s=7200, size=600_000):
        p = tmp_path / f"{name}.mp4"
        p.write_bytes(b"x" * size)
        os.utime(p, (time.time() - age_s, time.time() - age_s))
    put.dir = tmp_path
    files._forget_scan()
    return put


def _left(store):
    return sorted(p.stem for p in store.dir.glob("*.mp4"))


def test_a_free_accounts_file_goes_before_a_paying_customers_older_one(store):
    store("paid_old", age_s=36000)
    store("free_new", age_s=7200)
    files.trim_to_cap(keep_ids=set(), exempt_ids=set(),
                      ranks={"paid_old": (1, 0), "free_new": (0, 0)})
    assert _left(store) == ["paid_old"]


def test_a_twitch_file_goes_before_a_kick_file_of_the_same_tier(store):
    store("kick_old", age_s=36000)
    store("twitch_new", age_s=7200)
    files.trim_to_cap(keep_ids=set(), exempt_ids=set(),
                      ranks={"kick_old": (1, 1), "twitch_new": (1, 0)})
    assert _left(store) == ["kick_old"]


def test_approved_still_outlives_every_pending_file(store):
    """Approving is the user's strongest signal; a free account's approved
    clip outlives a paying account's unreviewed one."""
    store("free_approved", age_s=90000)
    store("paid_pending", age_s=7200)
    files.trim_to_cap(keep_ids={"free_approved"}, exempt_ids=set(),
                      ranks={"free_approved": (0, 1), "paid_pending": (1, 1)})
    assert _left(store) == ["free_approved"]


def test_a_file_with_no_clip_record_goes_first(store):
    store("orphan", age_s=7200)
    store("free_pending", age_s=36000)
    files.trim_to_cap(keep_ids=set(), exempt_ids=set(), ranks={"free_pending": (0, 0)})
    assert _left(store) == ["free_pending"]


def test_the_full_order_on_one_store(store):
    names = {"orphan": None, "free_tw": (0, 0), "free_kick": (0, 1),
             "paid_tw": (1, 0), "paid_kick": (1, 1), "approved_free_tw": (0, 0)}
    for i, n in enumerate(names):
        store(n, age_s=7200 + (len(names) - i) * 3600)   # earlier in the dict = OLDER
    # 6 files x 0.6 MB = 3.6 MB against a 1 MB cap; the 0.9 MB target leaves ONE
    files.trim_to_cap(keep_ids={"approved_free_tw"}, exempt_ids=set(),
                      ranks={k: v for k, v in names.items() if v})
    assert _left(store) == ["approved_free_tw"]


def test_ranks_come_from_the_clip_records_and_the_owners_plan(monkeypatch):
    from src.dashboard import api
    from src.auth import users as user_store
    monkeypatch.setattr(api, "_clips", {
        "a": {"id": "a", "user_id": "free1", "platform": "twitch"},
        "b": {"id": "b", "user_id": "pro1", "platform": "kick"},
        "c": {"id": "c", "user_id": "adm", "platform": "kick"},
        "d": {"id": "d", "user_id": "", "platform": "twitch"},
    })
    people = {"free1": {"id": "free1", "subscription_status": "none"},
              "pro1": {"id": "pro1", "subscription_status": "active", "plan": "pro"},
              "adm": {"id": "adm", "is_admin": True}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    assert files.eviction_ranks() == {"a": (0, 0), "b": (1, 1), "c": (1, 1), "d": (0, 0)}


def test_a_broken_lookup_falls_back_to_the_old_order(monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(api, "_clips", None)          # .values() raises
    assert files.eviction_ranks() == {}
