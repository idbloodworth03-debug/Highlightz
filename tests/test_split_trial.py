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
    # The owner's own first, live before offline; then the fewest accounts; never a paused one.
    order = [ln.split()[0] for ln in out.splitlines()
             if ln.startswith("  ") and ln.split()[-1] in ("account", "accounts")]
    assert order == ["ownerslive", "ownerschannel", "quietone", "bigstreamer"]
    assert "asleep" not in out
    for private in ("the-owner-name", "customer-one", "example.com", "SECRET-KEY-VALUE", "LIVE_AUTO_BASE"):
        assert private not in out


def test_looking_says_which_of_the_three_states_env_is_in(box, capsys):
    box.env.write_text("A=1\n")
    box.run()
    assert "the split is OFF" in capsys.readouterr().out
    box.env.write_text("INGEST_WORKERS=4\n")
    box.run()
    assert "EVERY channel is in a worker. This is not a trial." in capsys.readouterr().out
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
