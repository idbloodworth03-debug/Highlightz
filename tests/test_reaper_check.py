"""scripts/reaper_check.py: are the rules that pause Free accounts being applied?

Owner, 2026-11-19: "can you double check that all of the reaper bot settings
are working and kicking off free tier users for all the cases I mentioned
earlier I do not see it working". The answer has to come from the server, so
this is the reading of it. Held here: every account lands in the place the
rules put it, or is named as late for the rule it is late for; a running
site older than the code on disk is the first thing said; an account with
room in its queue is never called a failure; and nothing private is printed.
"""

import collections
import importlib.util
import json
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "reaper_check.py"
spec = importlib.util.spec_from_file_location("reaper_check", _PATH)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

NOW = 2_000_000_000.0
UP = NOW - 6 * 3600                      # the site has been up six hours


class Scene:
    def __init__(self):
        self.users, self.streams, self.clips, self.activity, self.decided = [], [], [], {}, {}

    def acct(self, name, pending=0, newest_ago=3600, status="live", hold=None, seen_ago=600,
             decided_ago=None, suggested=0, **user):
        uid = "id-" + name
        self.users.append({"id": uid, "username": name, "email": name + "@private.example",
                           "subscription_status": "none", **user})
        rec = {"channel": name + "ch", "platform": "twitch", "status": status, "user_id": uid,
               "added_at": NOW - 900_000}
        if hold:
            rec["hold"] = hold
        self.streams.append(rec)
        for i in range(pending):
            self.clips.append({"id": f"{name}-{i}", "user_id": uid, "status": "pending",
                               "created_at": NOW - newest_ago - 60 * i})
        for i in range(suggested):
            self.clips.append({"id": f"{name}-s{i}", "user_id": uid, "status": "pending", "suggested": True,
                               "created_at": NOW - 5})
        if seen_ago is not None:
            self.activity[uid] = NOW - seen_ago
        if decided_ago is not None:
            self.decided[uid] = NOW - decided_ago
        return uid

    def rows(self, started=UP, live=()):
        return {r["name"]: r for r in R.classify(self.users, self.streams, self.clips, self.activity,
                                                 self.decided, NOW, started, live)}


@pytest.fixture
def sc():
    return Scene()


# ── where the rules put an account ──────────────────────────────────────────

def test_each_account_lands_where_the_rules_put_it(sc):
    sc.acct("room", pending=7)                                            # watched: the plan
    sc.acct("fresh", pending=20, newest_ago=300)                          # full five minutes
    sc.acct("deciding", pending=20, newest_ago=5000, decided_ago=200)     # full, but at their queue
    sc.acct("held", pending=20, status="paused", hold="queue_full")
    sc.acct("heldroom", pending=3, status="paused", hold="queue_full")    # room again: waits for a visit
    sc.acct("idle", pending=2, status="paused", seen_ago=40_000)
    sc.acct("card", status="paused", hold="billing")
    got = sc.rows()
    assert {n: r["kind"] for n, r in got.items()} == {
        "room": "room", "fresh": "inside", "deciding": "inside", "held": "held", "heldroom": "held",
        "idle": "idle", "card": "billing"}
    assert got["held"]["room"] is False and got["heldroom"]["room"] is True


def test_a_full_queue_still_watched_after_fifteen_minutes_is_late(sc):
    sc.acct("just", pending=20, newest_ago=R.FULL_FOR + R.QUEUE_SLACK - 5)       # the rule's own minute
    sc.acct("late", pending=20, newest_ago=R.FULL_FOR + R.QUEUE_SLACK + 600, seen_ago=5)
    got = sc.rows(live=["id-late:latech"])
    assert got["just"]["kind"] == "inside"
    assert got["late"]["kind"] == "LATE_QUEUE" and got["late"]["late_s"] == pytest.approx(R.QUEUE_SLACK + 600)
    assert got["late"]["live"] == 1
    # It does not matter whether a tab is open: with one it is 15 minutes, without one it is 5.


def test_the_clock_is_never_from_before_the_site_started(sc):
    """A restart forgets who decided a clip a minute ago, so the rule gives
    everybody 15 minutes from it. The check must not call that late."""
    sc.acct("old", pending=20, newest_ago=50_000)
    assert sc.rows(started=NOW - 600)["old"]["kind"] == "inside"
    assert sc.rows(started=NOW - 3000)["old"]["kind"] == "LATE_QUEUE"
    assert sc.rows(started=None)["old"]["kind"] == "LATE_QUEUE"


def test_eight_hours_without_a_visit_is_late_with_room_for_the_saved_clock(sc):
    sc.acct("recent", pending=1, seen_ago=R.IDLE_FOR - 60)
    sc.acct("edge", pending=1, seen_ago=R.IDLE_FOR + R.IDLE_SLACK - 60)   # the clock is saved every 5 minutes
    sc.acct("gone", pending=1, seen_ago=R.IDLE_FOR + R.IDLE_SLACK + 60)
    sc.acct("never", pending=0, seen_ago=None)                            # no record: since the channel was added
    got = sc.rows()
    assert [got[n]["kind"] for n in ("recent", "edge", "gone", "never")] == ["room", "room", "LATE_IDLE", "LATE_IDLE"]


def test_only_plans_that_pause_are_looked_at(sc):
    sc.acct("pro", pending=200, newest_ago=90_000, subscription_status="active", plan="pro",
            stripe_customer_id="cus_1")
    sc.acct("staff", pending=500, newest_ago=90_000, is_admin=True)
    sc.acct("trial", pending=200, newest_ago=90_000, subscription_status="trialing", trial_ends_at=NOW + 9e9)
    sc.streams.append({"channel": "ghost", "status": "live", "user_id": "id-deleted"})
    sc.streams += ["junk", {"status": "live"}]
    assert sc.rows() == {}


def test_highlight_clips_do_not_fill_the_queue(sc):
    sc.acct("a", pending=19, suggested=9, newest_ago=90_000)
    assert sc.rows()["a"]["kind"] == "room" and sc.rows()["a"]["used"] == 19


def test_the_queue_and_the_last_decision_are_read_as_the_site_reads_them():
    clips = [{"user_id": "u", "status": "pending", "created_at": 50.0},
             {"user_id": "u", "status": "pending", "created_at": "bad"},
             {"user_id": "u", "status": "approved", "created_at": 99.0},
             {"user_id": "u", "status": "pending", "suggested": True, "created_at": 99.0},
             {"status": "pending"}, "junk"]
    assert R.queues(clips) == {"u": (2, 50.0)} and R.queues(None) == {} and R.queues({"a": 1}) == {}
    lines = [json.dumps({"ts": 10, "event": "approved", "user_id": "u"}),
             json.dumps({"ts": 30, "event": "rejected", "user_id": "u"}),
             json.dumps({"ts": 20, "event": "undone", "user_id": "v"}),
             json.dumps({"ts": 99, "event": "caught", "user_id": "u"}),          # not a decision
             json.dumps({"ts": 99, "event": "missed", "user_id": "w", "why": "approved"}),
             "not json \"approved\"", ""]
    assert R.last_decisions(lines) == {"u": 30.0, "v": 20.0}


def test_only_the_end_of_the_ledger_is_read(tmp_path):
    f = tmp_path / "ledger.jsonl"
    f.write_text("".join(json.dumps({"ts": i, "event": "approved", "user_id": "u"}) + "\n" for i in range(2000)))
    whole, tail = R.tail_lines(f), R.tail_lines(f, max_bytes=500)
    assert len(whole) == 2000 and 0 < len(tail) < 20
    assert json.loads(tail[0])["ts"] == 2000 - len(tail), "a line cut in half was kept"
    assert R.tail_lines(tmp_path / "missing") == []


# ── the running site ────────────────────────────────────────────────────────

def test_when_the_site_started_is_read_from_proc(tmp_path, monkeypatch):
    (tmp_path / "stat").write_text("cpu 1 2 3\nbtime 1000\n")
    for pid, argv, start in ((10, ["python3", "-m", "src.main"], 500), (11, ["python3", "-m", "src.ingestion.worker_main"], 700),
                             (12, ["bash", "-c", "x"], 1)):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
        (d / "stat").write_text(f"{pid} (python3 -m x) S " + " ".join(["0"] * 18) + f" {start} 0 0\n")
    (tmp_path / "self").mkdir()
    monkeypatch.setattr(R.os, "sysconf", lambda name: 100)
    assert R.site_started(tmp_path) == 1005.0                              # boot + 500 ticks at 100 a second
    assert R.site_started(tmp_path / "nothing-here") is None


def _report(sc, started=UP, changed=UP - 100, on_disk=True, events=None, journal=True, **kw):
    rows = list(R.classify(sc.users, sc.streams, sc.clips, sc.activity, sc.decided, NOW, started, kw.pop("live", ())))
    return "\n".join(R.report(rows, now=NOW, started=started, changed=changed, on_disk=on_disk,
                               events=collections.Counter(events or {}), journal=journal, span=3600.0, **kw))


def test_a_running_site_older_than_the_files_is_the_first_thing_said(sc):
    """What `git reset` without a restart leaves: the rule in the files and
    not in the process. It pauses nobody, and that is not a broken rule."""
    sc.acct("late", pending=20, newest_ago=9000)
    out = _report(sc, changed=UP + 3600)
    assert "<- AFTER the site started: the running site is OLDER than the files" in out
    assert "the 15-minute rule       : in the code on disk, NOT in the running site" in out
    assert "THE RUNNING SITE IS OLDER THAN THE CODE ON DISK." in out
    assert "why 1 full account(s) above are still watched." in out
    assert R.DEPLOY in out
    assert "That is a fault" not in out


def test_the_deploy_command_is_the_one_the_project_uses():
    claude = (_PATH.parent.parent / "CLAUDE.md").read_text()
    assert R.DEPLOY in claude


def test_late_on_current_code_is_called_a_fault_and_names_who(sc):
    sc.acct("stuck-one", pending=20, newest_ago=9000)
    sc.acct("stuck-two", pending=1, seen_ago=50_000)
    sc.acct("fine", pending=4)
    out = _report(sc, live=["id-stuck-one:stuck-onech"])
    assert "SHOULD BE PAUSED AND IS NOT       :     2" in out
    assert "stuck-one" in out and "queue full (20/20), nothing decided" in out and "live now" in out
    assert "stuck-two" in out and "no visit in over 2 hours" in out
    assert "2 ACCOUNT(S) SHOULD HAVE BEEN PAUSED AND WERE NOT, on a site running the" in out
    assert "fine" not in out.replace("fine.", ""), "an account that is where it should be was named"


def test_an_error_from_a_rule_outranks_everything_but_a_stale_site(sc):
    sc.acct("late", pending=20, newest_ago=9000)
    out = _report(sc, events={"queue_full_watch_error": 3, "queue_full_pause": 2})
    assert "A PAUSE RULE LOGGED 3 ERROR(S)" in out and "<- AN ERROR" in out
    assert "     2  queue_full_pause" in out


def test_all_in_place_says_so_and_says_who_is_still_watched_and_why(sc):
    for i in range(5):
        sc.acct(f"r{i}", pending=i, seen_ago=600 + i * 1500)              # all inside the 2 hours
    sc.acct("held", pending=20, status="paused", hold="queue_full")
    out = _report(sc, events={"queue_full_pause": 1}, live=["id-r0:r0ch", "id-other:x"], live_total=2)
    assert "EVERY FREE ACCOUNT IS WHERE THE RULES PUT IT" in out
    assert "The 5 still watched have ROOM in their queue. No rule stops those" in out
    assert "that is a new rule to decide, not a" in out
    assert "Of the 5 watched with room: 3 have not made a request in over an hour" in out   # 3,600 s and up
    assert "Their queues hold 10 clips of the 100 they could; 1 of them hold none." in out
    assert "Live right now: 1 of the site's 2 live channels are Free accounts'." in out
    assert "SHOULD BE PAUSED AND IS NOT       :     0" in out


def test_just_restarted_is_not_called_working_or_broken(sc):
    sc.acct("old", pending=20, newest_ago=9000)
    out = _report(sc, started=NOW - 400, changed=NOW - 5000)
    assert "THE SITE STARTED 7 min AGO, and 1 full account(s) are still watched." in out
    assert "Run this again after that; nothing is late yet." in out
    assert "EVERY FREE ACCOUNT IS WHERE" not in out
    # With nobody full and still watched there is nothing to wait for.
    sc.streams[0].update(status="paused", hold="queue_full")
    out = _report(sc, started=NOW - 400, changed=NOW - 5000)
    assert "EVERY FREE ACCOUNT IS WHERE THE RULES PUT IT" in out and "THE SITE STARTED" not in out


def test_the_other_states_of_the_site_are_said(sc):
    sc.acct("a", pending=1)
    assert "THE SITE'S PROCESS WAS NOT FOUND" in _report(sc, started=None)
    out = _report(sc, on_disk=False)
    assert "THE 15-MINUTE RULE IS NOT IN THE CODE IN THIS FOLDER." in out and R.DEPLOY in out
    assert "the journal could not be read from here" in _report(sc, journal=False)
    assert "nothing: no pause, no restart, no error" in _report(sc, events={})


def test_nothing_private_is_printed(sc):
    sc.acct("stuck", pending=20, newest_ago=9000)
    sc.acct("roomy-person", pending=3)
    out = _report(sc)
    assert "private.example" not in out and "id-" not in out
    assert "roomy-person" not in out                     # only an account that is late is named
    src = _PATH.read_text()
    assert '"email"' not in src and "found_detail" not in src


def test_its_numbers_are_the_sites_own():
    from src.dashboard import api
    assert R.FULL_FOR == api._QUEUE_FULL_UNREVIEWED and R.IDLE_FOR == api._IDLE_STREAM_TIMEOUT
    src = pathlib.Path(api.__file__).read_text()
    for name in R.EVENTS:
        assert f'"{name}"' in src, f"{name} is not something the site logs"
    assert R.rule_on_disk() is True
