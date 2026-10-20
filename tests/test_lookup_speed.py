"""Two reads that grew with the business (audit, 2026-10-20).

1. Every user lookup re-read and re-parsed the WHOLE users file. get_by_id runs
   in the auth middleware on every request and several times inside most
   handlers, on the event loop that also scores live channels: ~10 ms a
   lookup at 1,000 users, ~37 ms at 3,000. Now the parsed users are kept with
   the bytes they came from and re-parsed only when those bytes change; what
   callers get is always their own copy.
2. /me's "clips lost in the last 24 hours" read the whole stream-stats log,
   which is append-only and never trimmed, on every page load and reconnect.
   Now it reads backwards from the end and stops at the first row older than
   the window.
"""

import json
import time

import pytest

from src.auth import users as user_store
from src.stats import stream_stats


# ── 1. the users file ────────────────────────────────────────────────────────

@pytest.fixture
def users_file(tmp_path, monkeypatch):
    f = tmp_path / "users.json"
    monkeypatch.setattr(user_store, "_USERS_FILE", f)
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(user_store, "_snap", None)

    def write(rows):
        f.write_text(json.dumps(rows))
    return write


def _people(n):
    return [{"id": f"u{i}", "username": f"user{i}", "plan": "free", "prefs": {"a": i}} for i in range(n)]


def test_many_lookups_parse_the_file_once(users_file, monkeypatch):
    users_file(_people(50))
    parses = []
    real = user_store.json.loads
    monkeypatch.setattr(user_store.json, "loads", lambda s, *a, **k: (parses.append(1), real(s, *a, **k))[1])
    for i in range(200):
        assert user_store.get_by_id(f"u{i % 50}")["username"] == f"user{i % 50}"
    user_store._load()
    assert len(parses) == 1, f"parsed {len(parses)} times for 200 lookups"


def test_a_lookup_copies_one_record_not_the_whole_list(users_file, monkeypatch):
    users_file(_people(50))
    user_store.get_by_id("u1")
    unpickled = []
    real = user_store.pickle.loads
    monkeypatch.setattr(user_store.pickle, "loads", lambda b, *a, **k: (unpickled.append(1), real(b, *a, **k))[1])
    for i in range(50):
        user_store.get_by_id(f"u{i}")
    assert unpickled == [], "each lookup rebuilt the whole list"


def test_valid_json_that_is_not_a_list_still_falls_back_to_the_backup(users_file, tmp_path):
    (tmp_path / "users.json").write_text('{"oops": 1}')
    (tmp_path / "users.json.bak").write_text(json.dumps(_people(2)))
    assert [u["id"] for u in user_store._load()] == ["u0", "u1"]


def test_a_rewrite_is_seen_even_at_the_same_size_and_instant(users_file):
    """Bytes are compared, not mtimes: a same-size rewrite inside one
    timestamp tick must not read as unchanged."""
    users_file([{"id": "u1", "username": "aaaa", "plan": "free"}])
    assert user_store.get_by_id("u1")["username"] == "aaaa"
    users_file([{"id": "u1", "username": "bbbb", "plan": "free"}])
    assert user_store.get_by_id("u1")["username"] == "bbbb"


def test_a_save_is_seen_by_the_next_lookup(users_file):
    users_file(_people(3))
    rows = user_store._load()
    rows[1]["plan"] = "pro"
    user_store._save(rows)
    assert user_store.get_by_id("u1")["plan"] == "pro"


def test_what_a_caller_gets_is_its_own(users_file):
    users_file(_people(3))
    u = user_store.get_by_id("u2")
    u["plan"] = "pro"
    u["prefs"]["a"] = 99
    rows = user_store._load()
    rows[0]["plan"] = "pro"
    assert user_store.get_by_id("u2") == {"id": "u2", "username": "user2", "plan": "free", "prefs": {"a": 2}}
    assert user_store._load()[0]["plan"] == "free"


def test_a_corrupt_file_still_falls_back_to_the_backup(users_file, tmp_path):
    (tmp_path / "users.json").write_text("{not json")
    (tmp_path / "users.json.bak").write_text(json.dumps(_people(2)))
    assert [u["id"] for u in user_store._load()] == ["u0", "u1"]
    assert user_store.get_by_id("u1")["username"] == "user1"


def test_no_file_is_no_users(users_file):
    assert user_store._load() == [] and user_store.get_by_id("u1") is None


def test_a_lookup_at_a_thousand_users_is_cheap(users_file):
    users_file(_people(1000))
    user_store.get_by_id("u1")                       # the one parse
    t = time.perf_counter()
    for i in range(300):
        user_store.get_by_id(f"u{i}")
    per = (time.perf_counter() - t) / 300 * 1000
    assert per < 2.0, f"{per:.2f} ms a lookup — it was ~8 ms when every lookup re-parsed"


# ── 2. the stream-stats log ──────────────────────────────────────────────────

@pytest.fixture
def stats_log(tmp_path, monkeypatch):
    f = tmp_path / "stream_stats.jsonl"
    monkeypatch.setattr(stream_stats, "_LOG_FILE", f)
    return f


def _row(ts, uid, event):
    # The shape record() writes: ts first.
    return json.dumps({"ts": ts, "event": event, "user_id": uid, "channel": "xqc",
                       "clip_id": "c", "clip_at": ts})


def _full_count(f, uid, event, since):
    n = 0
    for line in f.read_text().splitlines():
        r = json.loads(line)
        n += r["user_id"] == uid and r["event"] == event and r["ts"] >= since
    return n


def _history(f, now, old=3000):
    rows = [_row(now - 5 * 86400 + i, "u1" if i % 3 else "u2",
                 stream_stats.MISSED if i % 2 else stream_stats.EXPIRED) for i in range(old)]
    rows += [_row(now - 3600 * h, "u1", stream_stats.MISSED) for h in (20, 10, 1)]
    rows += [_row(now - 3600 * 5, "u1", stream_stats.EXPIRED), _row(now - 60, "u2", stream_stats.MISSED)]
    f.write_text("\n".join(rows) + "\n")


def test_the_counts_match_a_full_read(stats_log):
    now = time.time()
    _history(stats_log, now)
    since = now - 86400
    assert stream_stats.missed_since("u1", since) == _full_count(stats_log, "u1", stream_stats.MISSED, since) == 3
    assert stream_stats.evictions_since("u1", since) == 1
    assert stream_stats.missed_since("u2", since) == 1


def test_only_the_recent_end_is_read(stats_log, monkeypatch):
    now = time.time()
    _history(stats_log, now, old=20000)
    seen = []
    real = stream_stats._row_ts
    monkeypatch.setattr(stream_stats, "_row_ts", lambda raw: (seen.append(1), real(raw))[1])
    stream_stats.missed_since("u1", now - 86400)
    assert len(seen) < 50, f"looked at {len(seen)} rows to count the last day"


def test_lines_split_across_blocks_are_not_lost(stats_log, monkeypatch):
    now = time.time()
    _history(stats_log, now, old=200)
    monkeypatch.setattr(stream_stats, "_TAIL_BLOCK", 37)    # every line straddles a block
    assert stream_stats.missed_since("u1", now - 86400) == 3
    assert stream_stats.missed_since("u1", now - 6 * 86400) == \
        _full_count(stats_log, "u1", stream_stats.MISSED, now - 6 * 86400)


def test_a_bad_line_or_an_older_shape_does_not_lose_the_rest(stats_log):
    now = time.time()
    lines = [_row(now - 7200, "u1", stream_stats.MISSED), "{garbage",
             json.dumps({"event": stream_stats.MISSED, "user_id": "u1", "ts": now - 60}),
             _row(now - 30, "u1", stream_stats.MISSED)]
    stats_log.write_text("\n".join(lines) + "\n")
    assert stream_stats.missed_since("u1", now - 86400) == 3


def test_a_row_a_little_out_of_order_is_still_counted(stats_log):
    """A clock that stepped back can leave a recent row just before an older
    one; the read runs an hour past the window for exactly that."""
    now = time.time()
    lines = [_row(now - 3 * 86400, "u1", stream_stats.MISSED),
             _row(now - 600, "u1", stream_stats.MISSED),
             _row(now - 86400 - 1200, "u1", stream_stats.EXPIRED),   # 20 min past the window
             _row(now - 60, "u1", stream_stats.MISSED)]
    stats_log.write_text("\n".join(lines) + "\n")
    assert stream_stats.missed_since("u1", now - 86400) == 2


def test_another_users_row_naming_this_id_elsewhere_is_not_counted(stats_log):
    now = time.time()
    lines = [json.dumps({"ts": now - 60, "event": stream_stats.MISSED, "user_id": "u2",
                         "channel": "u1", "clip_id": "c", "clip_at": now}),
             _row(now - 30, "u1", stream_stats.MISSED)]
    stats_log.write_text("\n".join(lines) + "\n")
    assert stream_stats.missed_since("u1", now - 86400) == 1


def test_no_log_is_nothing_missed(stats_log):
    assert stream_stats.missed_since("u1", 0) == 0
