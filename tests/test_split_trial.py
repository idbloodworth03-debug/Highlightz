"""scripts/split_trial.py: the trial is started without editing a command.

2026-11-18: the trial's start command had the word CHANNEL in it, to be
replaced by hand. It was pasted as written and production was restarted with
INGEST_ONLY=CHANNEL. Held here: looking changes nothing and hands over
commands that are already filled in; a name nobody has added never reaches
.env; .env keeps every other line, its owner and its permissions; and nothing
private is printed.
"""

import importlib.util
import json
import os
import pathlib
import stat

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "split_trial.py"
spec = importlib.util.spec_from_file_location("split_trial", _PATH)
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

ENV = ("DASHBOARD_SECRET_KEY=SECRET-KEY-VALUE\n"
       "# INGEST_WORKERS=9 was tried once\n"
       "LIVE_AUTO_BASE=44\n"
       "INGEST_WORKERS=1\n"
       "MAX_CONCURRENT_STREAMS=120\n"
       "INGEST_ONLY=CHANNEL\n")


@pytest.fixture
def box(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV)
    os.chmod(env, 0o640)
    store = tmp_path / "clips"
    store.mkdir()
    (store / "users.json").write_text(json.dumps([
        {"id": "owner", "username": "the-owner-name", "email": "owner@example.com", "is_admin": True},
        {"id": "c1", "username": "customer-one", "email": "c1@example.com", "is_admin": False},
        {"id": "c2", "username": "customer-two", "is_admin": False}]))
    (store / "streams.json").write_text(json.dumps([
        {"channel": "BigStreamer", "platform": "twitch", "status": "live", "user_id": "c1"},
        {"channel": "bigstreamer", "platform": "twitch", "status": "live", "user_id": "c2"},
        {"channel": "quietone", "platform": "kick", "status": "live", "user_id": "c1"},
        {"channel": "ownerschannel", "platform": "twitch", "status": "offline", "user_id": "owner"},
        {"channel": "ownerslive", "platform": "kick", "status": "live", "user_id": "owner"},
        {"channel": "asleep", "platform": "twitch", "status": "paused", "user_id": "c2"},
        {"channel": "", "status": "live"}, "junk"]))
    restarts = []

    def restart():
        restarts.append(1)
        return True, "the site was restarted"

    def run(*argv):
        return T.main(list(argv), env_path=env, store=store, restart=restart)
    return type("Box", (), {"env": env, "store": store, "restarts": restarts, "run": staticmethod(run)})


# ── .env ─────────────────────────────────────────────────────────────────────

def test_the_two_settings_are_read_as_the_site_reads_them():
    assert T.read_split(ENV) == (1, ["channel"])
    assert T.read_split("INGEST_WORKERS=4\n") == (4, [])
    assert T.read_split("INGEST_WORKERS=1\nINGEST_ONLY= A , b,\nINGEST_WORKERS=2\n") == (2, ["a", "b"])
    assert T.read_split("INGEST_WORKERS=x\nINGEST_ONLY=\"a\"\n") == (0, ["a"])
    assert T.read_split("# INGEST_WORKERS=9\nOTHER=1\n") == (0, [])


def test_taking_them_out_keeps_every_other_line_exactly():
    out = T.without_split(ENV)
    assert out == ("DASHBOARD_SECRET_KEY=SECRET-KEY-VALUE\n# INGEST_WORKERS=9 was tried once\n"
                   "LIVE_AUTO_BASE=44\nMAX_CONCURRENT_STREAMS=120\n")
    assert T.without_split(out) == out


def test_a_last_line_with_no_line_ending_is_not_glued_onto():
    """`printf ... >> .env` onto "A=1" with no newline makes "A=1INGEST_WORKERS=1":
    one setting ruined and the other not set."""
    assert T.with_trial("A=1", ["xqc"]) == "A=1\nINGEST_WORKERS=1\nINGEST_ONLY=xqc\n"
    assert T.with_trial("", ["a", "b"]) == "INGEST_WORKERS=1\nINGEST_ONLY=a,b\n"
    assert T.read_split(T.with_trial(ENV, ["quietone"])) == (1, ["quietone"])
    assert T.with_trial(ENV, ["quietone"]).count("INGEST_") == 3          # the comment, and one of each


def test_the_file_is_written_into_not_replaced(box):
    before = os.stat(box.env)
    T.rewrite(box.env, "A=1\n")
    after = os.stat(box.env)
    assert box.env.read_text() == "A=1\n"                                 # shorter: the tail is gone
    assert (after.st_ino, after.st_uid, after.st_gid) == (before.st_ino, before.st_uid, before.st_gid)
    assert stat.S_IMODE(after.st_mode) == 0o640


# ── looking ──────────────────────────────────────────────────────────────────

def test_looking_changes_nothing_and_hands_over_finished_commands(box, capsys):
    assert box.run() == 0
    out = capsys.readouterr().out
    assert box.env.read_text() == ENV and box.restarts == []
    assert "nothing was changed" in out
    assert '"channel" IS NOT A CHANNEL ANYBODY HAS ADDED (it is the placeholder from the example command).' in out
    assert "every channel is being watched in the one" in out
    # Each channel comes with its command, already filled in. No placeholder anywhere.
    assert "cd /opt/highlightz && venv/bin/python scripts/split_trial.py start ownerslive" in out
    assert "<channel>" not in out and "start CHANNEL" not in out
    # LIVE first (a trial shows nothing until its channel is live), the owner's
    # own first among those, then the fewest accounts; never a paused one.
    order = [ln.split()[0] for ln in out.splitlines()
             if ln.startswith("  ") and ln.split()[-1] in ("account", "accounts")]
    assert order == ["ownerslive", "quietone", "bigstreamer", "ownerschannel"]
    assert "asleep" not in out
    for private in ("the-owner-name", "customer-one", "example.com", "SECRET-KEY-VALUE", "LIVE_AUTO_BASE"):
        assert private not in out


def test_looking_says_which_of_the_three_states_env_is_in(box, capsys):
    box.env.write_text("A=1\n")
    box.run()
    assert "the split is OFF" in capsys.readouterr().out
    box.env.write_text("INGEST_WORKERS=4\n")
    box.run()
    out = capsys.readouterr().out
    assert "EVERY channel is in a worker process" in out and "THE WHOLE SITE IS SPLIT" in out
    assert "split_trial.py stop" in out and "CHANNELS A TRIAL COULD USE" not in out
    box.env.write_text("INGEST_WORKERS=1\nINGEST_ONLY=quietone\n")
    box.run()
    out = capsys.readouterr().out
    assert "a trial, INGEST_WORKERS=1, on: quietone" in out and "IS NOT A CHANNEL" not in out


def test_with_no_channel_of_the_owners_it_says_so(box, capsys):
    recs = [r for r in json.loads((box.store / "streams.json").read_text())
            if isinstance(r, dict) and r.get("user_id") != "owner"]
    (box.store / "streams.json").write_text(json.dumps(recs))
    box.run()
    assert "None of these is on an admin account." in capsys.readouterr().out


# ── starting and stopping ────────────────────────────────────────────────────

@pytest.mark.parametrize("argv, says", [
    (("start", "CHANNEL"), '"channel" is not a channel anybody has added: it is the placeholder'),
    (("start", "nosuchone"), '"nosuchone" is not a channel anybody has added.'),
    (("start", "quietone,nosuchone"), '"nosuchone" is not a channel anybody has added.'),
    (("start",), "Which channel? NOTHING WAS CHANGED."),
    (("begin", "quietone"), "NOTHING WAS CHANGED."),
    (("stop", "quietone"), "NOTHING WAS CHANGED."),
])
def test_a_slip_never_reaches_env_or_restarts_the_site(box, capsys, argv, says):
    assert box.run(*argv) == 1
    out = capsys.readouterr().out
    assert says in out and "NOTHING WAS CHANGED" in out
    assert box.env.read_text() == ENV and box.restarts == []


def test_a_real_channel_starts_the_trial_and_keeps_the_rest_of_env(box, capsys):
    assert box.run("start", "OwnersLive") == 0
    out = capsys.readouterr().out
    text = box.env.read_text()
    assert T.read_split(text) == (1, ["ownerslive"])
    assert text.startswith("DASHBOARD_SECRET_KEY=SECRET-KEY-VALUE\n# INGEST_WORKERS=9 was tried once\n"
                           "LIVE_AUTO_BASE=44\nMAX_CONCURRENT_STREAMS=120\n")
    assert "INGEST_ONLY=CHANNEL" not in text and stat.S_IMODE(os.stat(box.env).st_mode) == 0o640
    assert box.restarts == [1]
    assert "THE TRIAL IS ON." in out and "scripts/split_check.py" in out and "split_trial.py stop" in out
    assert "SECRET-KEY-VALUE" not in out


def test_two_channels_can_be_named_either_way(box, capsys):
    assert box.run("start", "quietone", "ownerslive") == 0
    assert T.read_split(box.env.read_text()) == (1, ["quietone", "ownerslive"])
    assert box.run("start", "ownerslive,quietone") == 0
    assert T.read_split(box.env.read_text()) == (1, ["ownerslive", "quietone"])
    capsys.readouterr()


def test_a_channel_paused_for_everybody_is_started_with_a_warning(box, capsys):
    assert box.run("start", "asleep") == 0
    assert "PAUSED for everybody who lists it" in capsys.readouterr().out


def test_stop_takes_both_out_and_restarts_once(box, capsys):
    assert box.run("stop") == 0
    assert box.env.read_text() == T.without_split(ENV) and box.restarts == [1]
    assert "THE SPLIT IS OFF." in capsys.readouterr().out
    # Again: already off, so nothing is written and the site is not restarted.
    assert box.run("stop") == 0
    assert box.restarts == [1] and "already off" in capsys.readouterr().out


def test_a_restart_that_fails_is_said_and_the_settings_stand(box, capsys):
    code = T.main(["start", "quietone"], env_path=box.env, store=box.store,
                  restart=lambda: (False, "the restart did not work (it needs root)"))
    out = capsys.readouterr().out
    assert code == 1 and "the restart did not work" in out and "systemctl restart highlightz" in out
    assert "THE TRIAL IS ON" not in out
    assert T.read_split(box.env.read_text()) == (1, ["quietone"])


def test_an_unreadable_store_changes_nothing(box, capsys):
    (box.store / "streams.json").unlink()
    assert box.run("start", "quietone") == 1 and box.run() == 1
    assert box.env.read_text() == ENV and box.restarts == []
    capsys.readouterr()


def test_it_prints_no_setting_but_the_two_and_no_account():
    src = _PATH.read_text()
    assert src.count("username") == 0 and src.count("email") == 0
    body = src[src.index("def read_split("):src.index("def without_split(")]
    assert "not in KEYS" in body


# ── who is live now ────────────────────────────────────────────────────────

def _note(box, keys, age=5.0):
    import time
    (box.store / "headroom.json").write_text(json.dumps(
        {"live": len(keys), "at": time.time() - age, "keys": keys}))


def test_live_now_is_the_sites_own_note_not_the_saved_status(box, capsys):
    """streams.json said three channels were live. Its statuses are only as
    fresh as the last add, remove or pause; the note is 30 seconds old."""
    _note(box, ["c1:BigStreamer", "c2:bigstreamer", "owner:ownerschannel", "solo"])
    assert T.live_names(box.store) == {"bigstreamer", "ownerschannel", "solo"}
    box.run()
    out = capsys.readouterr().out
    order = [ln.split()[0] for ln in out.splitlines()
             if ln.startswith("  ") and ln.split()[-1] in ("account", "accounts")]
    assert order == ["ownerschannel", "bigstreamer", "ownerslive", "quietone"]
    assert "live now" in out and "not live" in out and "is as last saved" not in out


def test_a_stale_or_missing_note_falls_back_and_says_so(box, capsys):
    assert T.live_names(box.store) is None                                # no note at all
    _note(box, ["c1:quietone"], age=600)
    assert T.live_names(box.store) is None                                # the site is not writing it
    (box.store / "headroom.json").write_text("not json")
    assert T.live_names(box.store) is None
    box.run()
    assert "is as last saved" in capsys.readouterr().out


def test_when_nothing_listed_is_live_it_says_a_trial_would_wait(box, capsys):
    _note(box, [])
    box.run()
    assert "NONE OF THESE IS LIVE RIGHT NOW" in capsys.readouterr().out


# ── everything ───────────────────────────────────────────────────────────

def _seen(found=1, lost=0, forbidden=0, lines=500, readable=True, errors=0, done=(3, 2, 1, 1)):
    import collections
    names = ("found its channel live", "is receiving the channel's audio",
             "is recording the rolling video", "cut a clip's video")
    return {"found": found, "readable": readable, "source": "test",
            "summary": {"lost": lost, "forbidden": forbidden, "lines": lines, "worker_lines": 40,
                        "workers": {0: {"errors": collections.Counter({"some_error": errors} if errors else {}),
                                        "warnings": collections.Counter()}}},
            "milestones": list(zip(names, done))}


TRIAL = "A=1\nINGEST_WORKERS=1\nB=2\nINGEST_ONLY=quietone\n"


def _all(box, seen, *extra):
    return T.main(["all", *extra], env_path=box.env, store=box.store,
                  restart=lambda: (box.restarts.append(1), (True, "the site was restarted"))[1], seen=seen)


def test_half_the_cores_carry_the_loops():
    assert [T.workers_for(n) for n in (8, 16, 4, 2, 1, 0, None, 64, "x")] == [4, 8, 2, 2, 2, 2, 2, 8, 2]


def test_the_whole_site_is_the_worker_count_and_no_list():
    out = T.with_everything(TRIAL, 4)
    assert out == "A=1\nB=2\nINGEST_WORKERS=4\n" and T.read_split(out) == (4, [])
    assert T.with_everything("A=1", 4) == "A=1\nINGEST_WORKERS=4\n"       # nothing glued on


def test_a_trial_seen_working_moves_every_channel_and_restarts_once(box, capsys, monkeypatch):
    box.env.write_text(TRIAL)
    monkeypatch.setattr(T.os, "cpu_count", lambda: 8)
    assert _all(box, _seen()) == 0
    out = capsys.readouterr().out
    assert box.env.read_text() == "A=1\nB=2\nINGEST_WORKERS=4\n" and box.restarts == [1]
    assert stat.S_IMODE(os.stat(box.env).st_mode) == 0o640
    assert "EVERY CHANNEL IS NOW WATCHED IN ONE OF 4 WORKER PROCESSES" in out
    assert "split_check.py" in out and "split_trial.py stop" in out and "NOTHING WAS CHANGED" not in out


@pytest.mark.parametrize("env, seen, says", [
    ("A=1\n", _seen(), "NO TRIAL IS RUNNING"),                              # the split is off
    (ENV, _seen(), "THE TRIAL HAS NOT STARTED"),                            # INGEST_ONLY=CHANNEL
    (TRIAL, _seen(found=0), "A WORKER IS MISSING"),
    (TRIAL, _seen(readable=False), "COULD NOT BE READ"),
    (TRIAL, _seen(lines=0), "COULD NOT BE READ"),
    (TRIAL, _seen(forbidden=2), "TRIED TO LOAD SOMETHING IT MUST NOT"),
    (TRIAL, _seen(lost=1), "A WORKER DIED 1 TIME(S)"),
    (TRIAL, _seen(done=(3, 2, 1, 0)), "cut a clip's video"),                # live, but no clip yet
    (TRIAL, _seen(done=(0, 0, 0, 0)), "found its channel live"),            # never went live
    (TRIAL, _seen(errors=3), "LOGGED 3 ERROR(S)"),
])
def test_anything_short_of_a_working_trial_changes_nothing(box, capsys, env, seen, says):
    box.env.write_text(env)
    assert _all(box, seen) == 1
    out = capsys.readouterr().out
    assert says in out and "NOTHING WAS CHANGED." in out, out
    assert box.env.read_text() == env and box.restarts == []


def test_the_worst_thing_is_the_one_said(box, capsys):
    box.env.write_text(TRIAL)
    _all(box, _seen(found=0, forbidden=1, lost=2, done=(0, 0, 0, 0), errors=5))
    out = capsys.readouterr().out
    assert "A WORKER IS MISSING" in out and "DIED" not in out and "ERROR(S)" not in out


def test_errors_somebody_has_read_do_not_block_but_nothing_else_is_waived(box, capsys, monkeypatch):
    monkeypatch.setattr(T.os, "cpu_count", lambda: 8)
    box.env.write_text(TRIAL)
    assert _all(box, _seen(errors=3), "--errors-read") == 0 and box.restarts == [1]
    box.env.write_text(TRIAL)
    box.restarts.clear()
    assert _all(box, _seen(errors=3, done=(1, 1, 1, 0)), "--errors-read") == 1 and box.restarts == []
    assert _all(box, _seen(lost=1), "--errors-read") == 1 and box.restarts == []
    assert box.env.read_text() == TRIAL
    capsys.readouterr()


def test_already_everything_is_left_alone(box, capsys):
    box.env.write_text("A=1\nINGEST_WORKERS=4\n")
    assert _all(box, _seen(found=0)) == 0
    out = capsys.readouterr().out
    assert "ALREADY IN A WORKER PROCESS" in out and "NOTHING WAS CHANGED" in out
    assert box.env.read_text() == "A=1\nINGEST_WORKERS=4\n" and box.restarts == []


def test_a_slip_after_all_changes_nothing(box, capsys):
    box.env.write_text(TRIAL)
    for argv in (["all", "now"], ["all", "--force"], ["everything"]):
        assert T.main(argv, env_path=box.env, store=box.store, restart=lambda: (True, "x"), seen=_seen()) == 1
    assert box.env.read_text() == TRIAL and "NOTHING WAS CHANGED" in capsys.readouterr().out


def test_a_check_that_cannot_run_changes_nothing(box, capsys, monkeypatch):
    box.env.write_text(TRIAL)

    def broken(minutes=60):
        raise RuntimeError("no journal here")
    monkeypatch.setattr(T, "evidence", broken)
    assert T.main(["all"], env_path=box.env, store=box.store, restart=lambda: (True, "x")) == 1
    assert box.env.read_text() == TRIAL and "NOTHING WAS CHANGED" in capsys.readouterr().out


def test_the_gate_reads_what_the_check_reads(monkeypatch):
    """One definition of "the trial worked": the journal and /proc, through
    split_check's own functions, and its own names for the four things."""
    check = T._split_check()
    line = lambda event, **kw: json.dumps({"event": event, "level": "info", **kw})
    lines = [line("stream_found", ingest=0), line("audio_meter_receiving_audio", ingest=0),
             line("clip_recorder_pipeline_up", ingest=0), line("clip_cut_ok", ingest=0),
             line("something_else"), line("clip_cut_ok")]            # the last: the web process, not a worker
    monkeypatch.setattr(T, "_split_check", lambda: check)
    monkeypatch.setattr(check, "read_lines", lambda minutes, log: (lines, True, "test"))
    monkeypatch.setattr(check, "workers_running", lambda: [{"index": 0, "rss": 1, "live": 1}])
    seen = T.evidence()
    assert seen["found"] == 1 and [n for _w, n in seen["milestones"]] == [1, 1, 1, 1]
    assert T.not_ready(workers=1, only=["quietone"], channels={"quietone": {}}, seen=seen) == []
    lines.pop(3)                                                      # no clip cut in a WORKER
    seen = T.evidence()
    assert "cut a clip's video" in "\n".join(T.not_ready(workers=1, only=["quietone"],
                                                          channels={"quietone": {}}, seen=seen))


def test_a_running_trial_is_shown_its_next_two_commands(box, capsys):
    box.env.write_text(TRIAL)
    box.run()
    out = capsys.readouterr().out
    assert "A TRIAL IS RUNNING" in out
    assert "cd /opt/highlightz && venv/bin/python scripts/split_check.py" in out
    assert "cd /opt/highlightz && venv/bin/python scripts/split_trial.py all" in out
    box.env.write_text(ENV)                                           # a trial that names nobody
    box.run()
    assert "split_trial.py all" not in capsys.readouterr().out


def test_the_quickest_trial_is_two_live_channels_one_of_each_platform_if_there_is(box, capsys):
    # As saved: ownerslive (kick, the owner's), quietone (kick), bigstreamer (twitch) are live.
    box.run()
    out = capsys.readouterr().out
    assert "cd /opt/highlightz && venv/bin/python scripts/split_trial.py start ownerslive,bigstreamer" in out
    # By the site's own note only one is live: there is no pair to offer.
    _note(box, ["c1:quietone"])
    box.run()
    assert "THE QUICKEST TRIAL" not in capsys.readouterr().out
    # Two live on the same platform: the two best, in order.
    _note(box, ["c1:quietone", "owner:ownerslive"])
    box.run()
    assert "split_trial.py start ownerslive,quietone\n" in capsys.readouterr().out
    # And the command it prints is one `start` accepts as it is.
    assert box.run("start", "ownerslive,quietone") == 0
    assert T.read_split(box.env.read_text()) == (1, ["ownerslive", "quietone"])
    capsys.readouterr()
