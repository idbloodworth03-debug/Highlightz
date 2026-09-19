"""Every clip file that leaves the server is recorded, and only those.

Owner, 2026-09-19: "make it so that we can see every clip that is
downloaded."

THE TWO THINGS THIS HAS TO GET RIGHT, because they pull against each other:

  * every real download is logged, or the panel is worse than nothing — an
    operator who trusts an incomplete log draws conclusions from it;
  * playing a clip on its card is NOT a download. `GET /clips/{id}/file`
    serves the file inline for the player and as an attachment for the
    button, from the same handler. Logging both would put a row in the panel
    every time anybody pressed play, which buries the thing being asked for.

And the privacy line that does not move: an admin can see THAT a download
happened. `_require_admin` guards the view, the file endpoint still checks
ownership, and nothing here hands an admin another account's video.
"""

import json

import pytest

from src.stats import downloads as dl


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_FILE", tmp_path / "downloads.jsonl")
    return dl


CLIP = {"id": "c1", "channel": "jynxzi", "platform": "twitch",
        "clip_title": "insane 1v4", "is_vod_moment": False}


# ── the log itself ───────────────────────────────────────────────────────────

def test_a_download_is_recorded_with_what_the_panel_shows(store):
    store.record(CLIP, "u1", 4_000_000)
    rows = store.recent()
    assert len(rows) == 1
    r = rows[0]
    assert (r["user_id"], r["clip_id"], r["channel"]) == ("u1", "c1", "jynxzi")
    assert r["title"] == "insane 1v4"
    assert r["size"] == 4_000_000
    assert r["ts"] > 0


def test_nothing_about_the_viewer_or_the_file_is_kept(store):
    """It records that an owner took their own clip. It is not a trail on
    what anybody watched, and it must not become one."""
    store.record(CLIP, "u1", 10)
    r = store.recent()[0]
    assert not {"ip", "user_agent", "path", "token", "referer"} & set(r)


def test_the_newest_download_is_first(store):
    for i in range(5):
        store.record({**CLIP, "id": f"c{i}"}, "u1", 1)
    ids = [r["clip_id"] for r in store.recent()]
    assert ids == sorted(ids, reverse=True) or ids[0] == "c4"


def test_a_torn_line_does_not_lose_the_rest(store):
    """An append cut off by a crash must not make the whole panel empty."""
    store.record(CLIP, "u1", 1)
    with store._FILE.open("a") as fh:
        fh.write('{"ts": 1, "user_id": "u2"\n')     # no closing brace
    store.record({**CLIP, "id": "c2"}, "u3", 1)
    users = {r["user_id"] for r in store.recent()}
    assert users == {"u1", "u3"}


def test_a_missing_file_is_an_empty_log_not_a_crash(store):
    assert store.recent() == []
    assert store.totals()["events"] == 0


def test_titles_are_bounded_so_one_row_cannot_be_enormous(store):
    store.record({**CLIP, "clip_title": "x" * 500}, "u1", 1)
    assert len(store.recent()[0]["title"]) == 120


# ── the totals ───────────────────────────────────────────────────────────────

def test_distinct_clips_are_counted_apart_from_events(store):
    """The same clip pulled three times is ONE clip somebody wanted. Showing
    only the event count would read a single user re-downloading as demand."""
    for _ in range(3):
        store.record(CLIP, "u1", 100)
    store.record({**CLIP, "id": "c2"}, "u2", 100)
    t = store.totals()
    assert t["events"] == 4
    assert t["clips"] == 2
    assert t["users"] == 2
    assert t["bytes"] == 400


def test_the_last_day_is_counted_from_the_clock(store):
    import time
    store.record(CLIP, "u1", 1)
    with store._FILE.open("a") as fh:                    # a week ago
        fh.write(json.dumps({"ts": time.time() - 7 * 86400, "user_id": "u1",
                             "clip_id": "old", "size": 1}) + "\n")
    t = store.totals()
    assert t["events"] == 2 and t["last_24h"] == 1


# ── bounded, and erasable ────────────────────────────────────────────────────

def test_the_log_is_pruned_rather_than_growing_forever(store, monkeypatch):
    monkeypatch.setattr(store, "_MAX_LINES", 10)
    for i in range(25):
        store.record({**CLIP, "id": f"c{i}"}, "u1", 1)
    rows = store.recent(limit=100)
    assert len(rows) == 10, "a log nobody trims is a disk-full incident"
    assert {r["clip_id"] for r in rows} == {f"c{i}" for i in range(15, 25)}, \
        "pruning kept the oldest instead of the newest"


def test_deleting_an_account_takes_its_downloads_with_it(store):
    store.record(CLIP, "u1", 1)
    store.record(CLIP, "u2", 1)
    assert store.delete_all_for_user("u1") == 1
    assert {r["user_id"] for r in store.recent()} == {"u2"}


def test_account_deletion_actually_calls_it():
    """An erasure request that left a per-account trail of what somebody took
    and when would not be an erasure."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api)
    block = src[src.index("async def delete_account"):]
    block = block[:block.index("user_store.delete(uid)")]
    assert "downloads as _dl_purge" in block and "_dl_purge.delete_all_for_user(uid)" in block


# ── the wiring ───────────────────────────────────────────────────────────────

def test_only_the_download_button_is_logged_not_the_player():
    """Same endpoint, two jobs: `?download=1` is the button, no flag is the
    inline player behind every clip card."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.get_clip_file)
    assert "if download:" in src, "the inline player is being logged as a download"
    i = src.index("if download:")
    assert "dl_log.record(clip, uid" in src[i:]
    # The ownership check still comes first, or the log would be the least of it.
    assert src.index('clip.get("user_id") != uid') < i


def test_logging_can_never_fail_the_download():
    """Telemetry must not cost somebody the file they asked for."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.get_clip_file)
    i = src.index("if download:")
    assert "try:" in src[i:] and "except Exception:" in src[i:]


def test_the_admin_view_is_admin_only_and_names_the_account():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.admin_downloads)
    assert "_require_admin(request)" in src
    assert 'row["user"]' in src, "a log of raw uuids is a log nobody reads"
    assert "(deleted account)" in src


def test_the_panel_exists_and_refreshes_rather_than_going_stale():
    from src.dashboard.api import ADMIN_HTML as h
    assert 'data-tab="downloads"' in h and 'id="panel-downloads"' in h
    assert "async function loadDownloads()" in h
    # Re-read on EVERY open, not just the first, and on focus.
    assert "if(b.dataset.tab === 'downloads') loadDownloads();" in h
    assert "window.addEventListener('focus'" in h


def test_no_event_is_emitted_that_nothing_handles():
    """The admin console has no socket. An event with no handler is dropped,
    and worse, it reads as realtime wiring to the next person."""
    from src.dashboard.api import ADMIN_HTML as h
    from src.dashboard.aurora_html import DASHBOARD_HTML as d
    import inspect
    from src.dashboard import api
    assert "download_recorded" not in inspect.getsource(api.get_clip_file)
    assert "download_recorded" not in h and "download_recorded" not in d
