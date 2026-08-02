"""The failure mode here is not a crash — it is a confident, wrong list.

Every test below is about the output MEANING what it says: an average that is
really an average, a band filter that does not quietly include people outside
it, and the two "this data cannot support that conclusion" warnings, which are
the only thing standing between a snapshot and someone emailing 40 strangers
based on one lucky night.
"""

import json
import time
from datetime import datetime, timezone, timedelta

import pytest

from src.maintenance import find_streamers as fs


def _row(login, viewers, ts, game="Just Chatting", lang="en"):
    return {"login": login, "name": login.title(), "uid": "1", "viewers": viewers,
            "game": game, "lang": lang, "title": "t", "ts": ts}


def test_average_is_over_live_samples_not_a_single_snapshot():
    now = time.time()
    rows = [_row("a", 100, now), _row("a", 500, now + 3600), _row("a", 300, now + 7200)]
    e = fs.aggregate(rows)["a"]
    assert e["avg"] == 300.0
    assert e["samples"] == 3
    assert (e["min"], e["max"]) == (100, 500)


def test_a_streamer_with_one_lucky_night_is_not_reported_as_that_number():
    """The whole reason this samples over days: 900 once and 120 twice is a
    120-viewer streamer, and a snapshot would have called them 900."""
    now = time.time()
    rows = [_row("b", 900, now), _row("b", 120, now + 3600), _row("b", 120, now + 7200)]
    e = fs.aggregate(rows)["b"]
    assert e["avg"] == 380.0
    assert e["median"] == 120.0, "median is what makes the outlier visible"


def test_the_band_filter_excludes_channels_outside_it(tmp_path, monkeypatch, capsys):
    now = time.time()
    rows = ([_row("small", 20, now + i) for i in range(3)]
            + [_row("target", 250, now + i) for i in range(3)]
            + [_row("huge", 9000, now + i) for i in range(3)])
    _write(tmp_path, monkeypatch, rows)

    assert fs.report(100, 500, 3, "en", 40) == 0
    out = capsys.readouterr().out
    assert "target" in out
    assert "small" not in out and "huge" not in out


def test_channels_below_the_sample_threshold_are_dropped(tmp_path, monkeypatch, capsys):
    now = time.time()
    _write(tmp_path, monkeypatch,
           [_row("seen_once", 250, now), *[_row("seen_thrice", 250, now + i) for i in range(3)]])
    fs.report(100, 500, 3, "en", 40)
    out = capsys.readouterr().out
    assert "seen_thrice" in out
    assert "seen_once" not in out


def test_it_warns_that_one_pass_is_not_an_average(tmp_path, monkeypatch, capsys):
    now = time.time()
    _write(tmp_path, monkeypatch, [_row(f"c{i}", 250, now) for i in range(5)])
    fs.report(100, 500, 1, "en", 40)
    out = capsys.readouterr().out
    assert "snapshot with extra steps" in out, "no warning that n=1 is not an average"


def test_it_refuses_to_pretend_the_us_signal_is_real_when_sampling_is_one_sided():
    """Sample only during US prime and everyone scores 100% US — the number is
    an artifact of when you looked, not a fact about them. Reporting it anyway
    would be the most damaging kind of wrong: plausible and unfalsifiable."""
    # 01:00 UTC is inside US prime; three passes, all in it.
    base = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc).timestamp()
    rows = [_row("x", 250, base + i * 60) for i in range(3)]
    assert fs._hour_coverage(rows).issubset(fs.US_PRIME_UTC)


def test_the_us_prime_percentage_reflects_when_they_are_actually_live():
    base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    # live at 01:00 and 02:00 UTC (US prime) and at 12:00 (not)
    rows = [_row("y", 250, (base + timedelta(hours=h)).timestamp())
            for h in (1, 2, 12)]
    e = fs.aggregate(rows)["y"]
    assert e["us_prime_pct"] == 67


def test_no_samples_is_an_error_not_an_empty_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fs, "_store", lambda: tmp_path)
    monkeypatch.setattr(fs, "_samples_file", lambda: tmp_path / "samples.jsonl")
    assert fs.report(100, 500, 3, "en", 40) == 1
    assert "No samples yet" in capsys.readouterr().out


def test_the_sampling_floor_sits_below_the_band_on_purpose():
    """Cutting collection at --min would only ever record a target's good
    nights, biasing every average upward — the exact error this tool exists to
    avoid."""
    import inspect
    src = inspect.getsource(fs.main)
    assert '"--floor", type=int, default=60' in src
    assert '"--min", type=int, default=100' in src


# --------------------------------------------------------------- sampling

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status = payload, status

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._p
    def raise_for_status(self):
        if self.status >= 400:
            raise AssertionError(f"HTTP {self.status}")


class _Session:
    """Serves fixed pages of /streams in descending viewer order."""
    def __init__(self, pages):
        self.pages, self.gets = pages, 0

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    def post(self, *a, **k):
        return _Resp({"access_token": "t"})

    def get(self, url, headers=None, params=None):
        i = self.gets
        self.gets += 1
        if i >= len(self.pages):
            return _Resp({"data": [], "pagination": {}})
        return _Resp({"data": self.pages[i],
                      "pagination": {"cursor": f"c{i}"}})


def _stream(login, viewers):
    return {"user_login": login, "user_name": login, "user_id": "1",
            "viewer_count": viewers, "game_name": "G", "language": "en",
            "title": "t"}


def _run_sample(monkeypatch, tmp_path, pages, floor=60, max_pages=60):
    sess = _Session(pages)
    monkeypatch.setattr(fs.aiohttp, "ClientSession", lambda *a, **k: sess)
    monkeypatch.setattr(fs.settings, "twitch_client_id", "id")
    monkeypatch.setattr(fs.settings, "twitch_client_secret", "secret")
    f = tmp_path / "samples.jsonl"
    monkeypatch.setattr(fs, "_store", lambda: tmp_path)
    monkeypatch.setattr(fs, "_samples_file", lambda: f)
    import asyncio
    rc = asyncio.run(fs.sample("en", floor, max_pages, 0.0))
    got = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
    return rc, got, sess


def test_sampling_stops_once_viewer_counts_drop_below_the_floor(tmp_path, monkeypatch):
    """/streams is sorted descending, which is the only reason walking it
    terminates. If this stops working the pass walks all of Twitch and burns
    the Helix budget that live clip creation shares."""
    pages = [[_stream(f"a{i}", 900 - i) for i in range(100)],
             [_stream(f"b{i}", 200 - i * 3) for i in range(100)],  # tail < 60
             [_stream(f"c{i}", 10) for i in range(100)]]
    rc, got, sess = _run_sample(monkeypatch, tmp_path, pages)
    assert rc == 0
    assert sess.gets == 2, f"kept paging past the floor ({sess.gets} requests)"
    assert all(r["viewers"] >= 60 for r in got), "sub-floor channels recorded"


def test_max_pages_is_a_hard_stop(tmp_path, monkeypatch):
    """Backstop for the case above failing: never unbounded, whatever Helix
    returns."""
    pages = [[_stream(f"x{p}_{i}", 900) for i in range(100)] for p in range(50)]
    rc, got, sess = _run_sample(monkeypatch, tmp_path, pages, max_pages=3)
    assert sess.gets == 3


def test_a_rate_limit_stops_the_pass_rather_than_hammering(tmp_path, monkeypatch):
    """429 means we are eating into the budget clip creation needs. Backing off
    and keeping a partial sample beats finishing the list."""
    sess = _Session([])
    monkeypatch.setattr(sess, "get", lambda *a, **k: _Resp({}, status=429))
    monkeypatch.setattr(fs.aiohttp, "ClientSession", lambda *a, **k: sess)
    monkeypatch.setattr(fs.settings, "twitch_client_id", "id")
    monkeypatch.setattr(fs.settings, "twitch_client_secret", "secret")
    monkeypatch.setattr(fs, "_store", lambda: tmp_path)
    monkeypatch.setattr(fs, "_samples_file", lambda: tmp_path / "s.jsonl")
    import asyncio
    assert asyncio.run(fs.sample("en", 60, 60, 0.0)) == 0


def test_missing_credentials_fail_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fs.settings, "twitch_client_id", "")
    monkeypatch.setattr(fs.settings, "twitch_client_secret", "")
    import asyncio
    assert asyncio.run(fs.sample("en", 60, 60, 0.0)) == 1
    assert "not set" in capsys.readouterr().out


def _write(tmp_path, monkeypatch, rows):
    f = tmp_path / "samples.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(fs, "_store", lambda: tmp_path)
    monkeypatch.setattr(fs, "_samples_file", lambda: f)
