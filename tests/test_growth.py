"""scripts/growth.py: sign-ups by day from the real accounts, and where the pace leads.

Owner, 2026-11-19, of an answer worked out from one screenshot: "i got more
then this use accurate numbers". Held here: a day is the owner's calendar day
and staff are not counted (as on the admin page); a day that is not over is
compared with yesterday by the same time; the daily multiple and the three
paths are plain arithmetic that can be checked by hand; and nothing but
counts is printed.
"""

import importlib.util
import json
import pathlib
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "growth.py"
spec = importlib.util.spec_from_file_location("growth", _PATH)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

NY = ZoneInfo("America/New_York")


def at(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


def acct(i, when, **kw):
    return {"id": f"u{i}", "username": f"secret-name-{i}", "email": f"secret{i}@example.com",
            "created_at": when, **kw}


NOW = at(2026, 11, 18, 18, 0)                # 6 PM in New York: three quarters of the day gone


def test_a_day_is_the_owners_calendar_day_not_utc():
    """9 PM Eastern is already tomorrow on UTC. It is counted today."""
    users = [acct(1, at(2026, 11, 17, 21, 0)), acct(2, at(2026, 11, 18, 0, 5)), acct(3, at(2026, 11, 18, 17, 59))]
    got = dict((d.isoformat(), n) for d, n in G.daily(users, NOW, NY, 3))
    assert got == {"2026-11-16": 0, "2026-11-17": 1, "2026-11-18": 2}
    utc, name = G.zone_for("UTC")
    assert dict((d.isoformat(), n) for d, n in G.daily(users, NOW, utc, 3))["2026-11-18"] == 3


def test_staff_and_broken_records_are_not_counted():
    users = [acct(1, at(2026, 11, 18)), acct(2, at(2026, 11, 18), is_admin=True),
             {"username": "no id", "created_at": at(2026, 11, 18)}, "junk", None,
             acct(3, "soon"), acct(4, 0), acct(5, NOW + 9999)]
    people = G.customers(users)
    assert [u["id"] for u in people] == ["u1", "u3", "u4", "u5"]
    assert G.daily(people, NOW, NY, 1)[0][1] == 1        # no date, a bad date and a future date: in no day


def test_a_day_that_is_not_over_is_compared_by_the_same_time():
    users = ([acct(i, at(2026, 11, 17, 9)) for i in range(4)]            # yesterday, by 6 PM: 4
             + [acct(10 + i, at(2026, 11, 17, 22)) for i in range(6)]    # yesterday, after 6 PM: 6 more
             + [acct(20 + i, at(2026, 11, 18, 10)) for i in range(12)])  # today so far: 12
    today, yday_same, gone = G.by_this_time(users, NOW, NY)
    assert (today, yday_same) == (12, 4) and gone == pytest.approx(0.75)


@pytest.mark.parametrize("counts, want", [
    ([10, 10, 10, 10], 1.0), ([5, 10, 20, 40, 80], 2.0), ([80, 40, 20, 10], 0.5),
])
def test_the_daily_multiple_is_what_each_day_was_of_the_one_before(counts, want):
    assert G.daily_multiple(counts) == pytest.approx(want, rel=0.03)


def test_too_little_to_fit_is_said_not_guessed():
    assert G.daily_multiple([3, 9]) is None and G.daily_multiple([0, 0, 0, 0]) is None
    assert G.daily_multiple([]) is None


def test_the_three_paths_can_be_checked_by_hand():
    # A: holds at 100 a day.
    assert G.path(100, 1.0, 21) == [100.0 * (i + 1) for i in range(21)]
    # C: doubling. 200, then 400, then 800.
    assert G.path(100, 2.0, 3) == [200.0, 600.0, 1400.0]
    # B: the growth runs out evenly over 2 days (x2, then x1.5), then holds at 300.
    assert G.path(100, 2.0, 5, fade_days=2) == [200.0, 500.0, 800.0, 1100.0, 1400.0]
    # One path, read at any day: the 7-day figure is a point on the 21-day line.
    assert G.path(50, 1.3, 21, 7)[:7] == G.path(50, 1.3, 7, 7)
    # Runaway arithmetic stops at a number, never an error.
    assert G.path(1000, 50.0, 60)[-1] == 1e18


def _spike():
    """Quiet for two weeks, then more each day; today (so far) the most."""
    users, n = [acct(0, at(2026, 1, 1), is_admin=True)], 0
    for back, count in enumerate([10] * 14 + [20, 40, 80]):                 # oldest first
        day = 17 - back                                                     # days before today
        for k in range(count):
            n += 1
            users.append(acct(n, NOW - day * 86400 + (k % 5) * 600 - 6 * 3600,
                              found_via="tiktok" if day <= 2 else "friend"))
    for k in range(120):                                                    # today, before 6 PM
        n += 1
        users.append(acct(n, at(2026, 11, 18, 8) + k * 60, found_via="tiktok", found_detail="typed by a person"))
    return users


def test_the_report_says_the_days_the_pace_and_where_it_leads():
    out = "\n".join(G.report(_spike(), NOW, "America/New_York", days=21, live=60, live_limit=110))
    assert "SIGN-UPS BY DAY  (America/New_York time; staff not counted)" in out
    assert "Wed Nov 18     120" in out and "<- today, not finished" in out
    assert "Tue Nov 17      80" in out and "Mon Nov 16      40" in out
    assert "accounts in all: 400" in out                                    # 140 + 140 + 120; staff left out
    assert "today so far (18 h in)" in out and "yesterday, the whole day         : 80" in out
    assert "-> today is running at 1.5x yesterday" in out                   # 120 against 80 by 6 PM
    assert "last 7 full days / the 7 before  : 180 / 70   -> 2.6x" in out     # 80+40+20+4x10 / 7x10
    assert "over the last 7 full days, x2.00 over the last 3" in out
    for label in ("A. it holds: every day like today", "B. grows one more week, by less each day, holds",
                  "a day, every day, to the end"):
        assert label in out
    # A is exact: 400 accounts and 120 a day.
    a = next(ln for ln in out.splitlines() if ln.strip().startswith("A."))
    assert a.split()[-3:] == ["1,240", "2,080", "2,920"]
    assert "  tiktok                  240 / 0" in out                       # today, yesterday, the day before
    assert "  friend                    0 / 40" in out                       # there before, not now
    assert "channels live right now          : 60 of a limit of 110" in out
    assert "limit is reached at about 733 accounts" in out                 # 110 / (60 / 400)
    assert "about 3 day(s) away" in out                                     # 333 more at 120 a day


def test_nothing_but_counts_is_printed():
    out = "\n".join(G.report(_spike(), NOW, "America/New_York", live=60, live_limit=110))
    for private in ("secret-name", "example.com", "typed by a person", "u1"):
        assert private not in out
    src = _PATH.read_text()
    assert "username" not in src and "email" not in src and "found_detail" not in src


def test_where_they_came_from_is_only_the_fixed_answers():
    users = [acct(1, NOW - 3600, found_via="tiktok"), acct(2, NOW - 3600, found_via="My Friend Bob <bob@x.com>"),
             acct(3, NOW - 3600), acct(4, NOW - 4 * 86400, found_via="tiktok")]
    last3, prior3 = G.sources(G.customers(users), NOW, NY)
    assert dict(last3) == {"tiktok": 1, "(not said)": 2} and dict(prior3) == {"tiktok": 1}


def test_a_multiplying_week_is_called_arithmetic_when_it_passes_belief():
    users, n = [], 0
    for back, count in enumerate([3, 9, 27, 81, 243, 729, 2187]):
        for k in range(count):
            n += 1
            users.append(acct(n, NOW - (7 - back) * 86400 - 3600 * 5 + k))
    for k in range(3000):
        n += 1
        users.append(acct(n, at(2026, 11, 18, 9) + k))
    out = "\n".join(G.report(users, NOW, "America/New_York"))
    assert "C IS ARITHMETIC, NOT A FORECAST." in out and "beyond counting" in out


def test_a_flat_or_falling_week_reads_the_holding_line(monkeypatch):
    users = [acct(i, NOW - d * 86400 - 3600 * 7 + i) for d in range(1, 15) for i in range(d * 100, d * 100 + 10)]
    users += [acct(9000 + i, at(2026, 11, 18, 9) + i) for i in range(3)]
    out = "\n".join(G.report(users, NOW, "America/New_York"))
    assert "were not growing day on day" in out and "B. grows" not in out
    assert "TODAY IS RUNNING WELL BELOW YESTERDAY" in out


def test_early_in_the_day_yesterday_is_what_it_starts_from():
    one_am = at(2026, 11, 18, 1, 0)
    users = [acct(i, at(2026, 11, 17, 14) + i) for i in range(50)] + [acct(99, at(2026, 11, 18, 0, 30))]
    out = "\n".join(G.report(users, one_am, "America/New_York"))
    assert "too early in the day for today to say much" in out
    assert "starting from 51 accounts and about 50 new a day" in out


def test_no_note_of_who_is_live_means_no_server_section(tmp_path):
    assert G.live_now(tmp_path, time.time()) is None
    (tmp_path / "headroom.json").write_text(json.dumps({"at": time.time() - 900, "keys": ["a:b"]}))
    assert G.live_now(tmp_path, time.time()) is None                       # stale: the site is not writing it
    (tmp_path / "headroom.json").write_text(json.dumps({"at": time.time(), "keys": ["a:b", "c:d"]}))
    assert G.live_now(tmp_path, time.time()) == 2
    assert "WHAT THE SERVER CARRIES" not in "\n".join(G.report(_spike(), NOW, live=None, live_limit=110))


def test_an_unknown_zone_is_utc_and_says_so():
    out = "\n".join(G.report(_spike(), NOW, "Not/AZone"))
    assert "SIGN-UPS BY DAY  (UTC time; staff not counted)" in out
