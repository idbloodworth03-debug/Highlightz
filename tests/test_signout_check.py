"""scripts/signout_check.py sorts signed-out-looking moments correctly and
never prints an address (audit, 2026-11-21)."""

import gzip
import importlib.util
import pathlib

import pytest

SPEC = importlib.util.spec_from_file_location(
    "signout_check", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "signout_check.py")
sc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sc)

IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
DASH, LANDING = 289_700, 61_500


def line(ip, ts, req, st, size, ua):
    return f'{ip} - - [{ts} +0000] "{req} HTTP/2.0" {st} {size} "https://highlightz.app/" "{ua}"\n'


@pytest.fixture
def logdir(tmp_path):
    rows = [
        # landing pages from strangers, so the landing size is known
        *[line(f"9.9.9.{i}", f"18/Nov/2026:09:0{i}:00", "GET /", 200, LANDING + i, PC) for i in range(5)],
        # A: dashboard, landing 2 min later, dashboard again with no sign-in
        line("11.0.0.1", "18/Nov/2026:10:00:00", "GET /", 200, DASH, IOS),
        line("11.0.0.1", "18/Nov/2026:10:02:00", "GET /", 200, LANDING, IOS),
        line("11.0.0.1", "18/Nov/2026:10:05:00", "GET /", 200, DASH, IOS),
        # B: dashboard, sign-in page, signs in again
        line("22.0.0.2", "18/Nov/2026:10:00:00", "GET /", 200, DASH, PC),
        line("22.0.0.2", "18/Nov/2026:10:01:00", "GET /login", 200, 9000, PC),
        line("22.0.0.2", "18/Nov/2026:10:01:05", "GET /auth/twitch", 302, 0, PC),
        line("22.0.0.2", "18/Nov/2026:10:01:20", "GET /auth/twitch/callback?code=c&state=s", 302, 0, PC),
        line("22.0.0.2", "18/Nov/2026:10:01:21", "GET /", 200, DASH, PC),
        # C: a dashboard download cut short, then nothing
        line("33.0.0.3", "18/Nov/2026:10:00:00", "GET /", 200, DASH, IOS),
        line("33.0.0.3", "18/Nov/2026:10:00:30", "GET /", 200, 23_000, IOS),
        # D: signed out on purpose: not counted
        line("44.0.0.4", "18/Nov/2026:10:00:00", "GET /", 200, DASH, PC),
        line("44.0.0.4", "18/Nov/2026:10:00:10", "POST /logout", 302, 0, PC),
        line("44.0.0.4", "18/Nov/2026:10:00:11", "GET /login", 200, 9000, PC),
        # E: a week away
        line("55.0.0.5", "10/Nov/2026:10:00:00", "GET /", 200, DASH, PC),
        line("55.0.0.5", "17/Nov/2026:11:00:00", "GET /", 200, LANDING, PC),
        line("66.0.0.6", "18/Nov/2026:10:00:00", "GET /login?error=invalid_state", 200, 9000, PC),
        line("77.0.0.7", "18/Nov/2026:10:00:00", "GET /", 200, LANDING, "curl/8.0"),
    ]
    (tmp_path / "access.log").write_text("".join(rows[5:]))
    with gzip.open(tmp_path / "access.log.2.gz", "wt") as f:
        f.writelines(rows[:5])
    return tmp_path


def test_each_moment_is_sorted_by_what_it_was_and_what_came_next(logdir, capsys):
    assert sc.main(str(logdir)) == 0
    out = capsys.readouterr().out
    rows = {}
    for l in out.splitlines():
        for kind in ("landing page", "sign-in page", "cut-off download"):
            if l.strip().startswith(kind + " "):
                rows[kind] = l.split()[-3:]
    #                    no sign-in, signed in again, did not come back
    assert rows["landing page"] == ["1", "0", "0"]
    assert rows["sign-in page"] == ["0", "1", "0"]
    assert rows["cut-off download"] == ["0", "0", "1"]
    assert "invalid_state" in out
    assert "POST /logout 1" in out


def test_a_week_away_lands_in_the_6_to_8_day_column(logdir, capsys):
    sc.main(str(logdir))
    computer = next(l for l in capsys.readouterr().out.splitlines() if l.strip().startswith("computer "))
    #           <10m 10m-1h 1h-1d 1-6d 6-8d >8d
    assert computer.split()[1:] == ["1", "0", "0", "0", "1", "0"]


def test_no_address_is_ever_printed(logdir, capsys):
    sc.main(str(logdir))
    out = capsys.readouterr().out
    for ip in ("11.0.0.1", "22.0.0.2", "33.0.0.3", "9.9.9.1"):
        assert ip not in out
    assert "code=c" not in out and "state=s" not in out


def test_an_empty_directory_says_so(tmp_path, capsys):
    assert sc.main(str(tmp_path)) == 1
    assert "no log lines" in capsys.readouterr().out
