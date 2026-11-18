"""scripts/split_check.py: is the split healthy, in one paste (2026-11-18).

The check the owner runs after turning INGEST_WORKERS on, first for the trial
on one channel. Held here: it counts names and levels and nothing else; a
worker that died, or that tried to load the dashboard, is the verdict; clean
is only called clean; and it cannot print a field of a log line.
"""

import importlib.util
import json
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "split_check.py"
spec = importlib.util.spec_from_file_location("split_check", _PATH)
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)


def L(event, level="info", ingest=None, **fields):
    d = {"event": event, "level": level, "timestamp": "2026-11-18T17:00:00Z", **fields}
    if ingest is not None:
        d["ingest"] = ingest
    return json.dumps(d)


HEALTHY = [
    L("ingest_split_on", "warning", workers=1),
    L("ingest_worker_up", index=0, pid=4242),
    L("ingest_worker_given_channels", index=0, channels=1),
    L("worker_starting", ingest=0, channel="somebody"),
    L("stream_found", ingest=0, channel="somebody"),
    L("audio_meter_receiving_audio", ingest=0),
    L("clip_recorder_pipeline_up", ingest=0),
    L("clip_cut_ok", ingest=0), L("clip_cut_ok", ingest=0),
    L("clip_file_skipped", "warning", ingest=0),
    L("dashboard_request", path="/me"),                        # the web process's own business
    "INFO:     127.0.0.1 - \"GET / HTTP/1.1\" 200",             # not ours to parse
    "{broken json",
    json.dumps(["a", "list"]), json.dumps({"no": "event"}),
]


def test_it_counts_names_and_levels_per_worker_and_nothing_else():
    s = K.summarize(HEALTHY)
    assert s["lines"] == 11 and s["worker_lines"] == 7
    assert s["ups"] == 1 and s["lost"] == 0 and s["forbidden"] == 0
    w = s["workers"][0]
    assert w["lines"] == 7 and w["events"]["clip_cut_ok"] == 2
    assert dict(w["warnings"]) == {"clip_file_skipped": 1} and not w["errors"]
    assert not s["web"], "the line that says the switch is on is not a problem"
    assert K.milestones(s) == [("found its channel live", 1), ("is receiving the channel's audio", 1),
                               ("is recording the rolling video", 1), ("cut a clip's video", 2)]


def test_a_worker_that_loads_what_it_must_not_is_counted_wherever_the_words_are():
    lines = HEALTHY + [
        L("worker_crashed", "error", ingest=0,
          error="ImportError('src.dashboard.api must not be imported in an ingest worker')"),
        L("clip_cut_error", "warning", ingest=1, traceback="... ModuleNotFoundError: No module named 'x'"),
        # The same words from the WEB process are not a worker's doing.
        L("something", "error", error="ImportError: nope"),
    ]
    s = K.summarize(lines)
    assert s["forbidden"] == 2
    assert dict(s["workers"][0]["errors"]) == {"worker_crashed": 1}
    v = K.verdict(asked=2, found=2, summary=s, only=[], journal=True)
    assert v[0].startswith("STOP: A WORKER TRIED TO LOAD SOMETHING IT MUST NOT (2 line(s))")
    assert any("remove INGEST_WORKERS and INGEST_ONLY" in x for x in v)


def test_the_web_processes_lines_about_its_workers_are_shown_when_they_are_trouble():
    s = K.summarize([L("ingest_worker_lost", "error", index=1), L("ingest_worker_lost", "error", index=1),
                     L("ingest_status_not_its_channel", "warning", index=0),
                     L("link_cast_failed", "warning"), L("ingest_worker_up", index=1),
                     L("app_loop_overloaded", "error")])
    assert s["lost"] == 2 and s["ups"] == 1
    assert dict(s["web"]) == {"ingest_worker_lost": 2, "ingest_status_not_its_channel": 1,
                              "link_cast_failed": 1}


def _v(**kw):
    base = dict(asked=1, found=1, summary=K.summarize(HEALTHY), only=["somebody"], journal=True)
    base.update(kw)
    return K.verdict(**base)


@pytest.mark.parametrize("kw, starts", [
    (dict(asked=0, found=0), "THE SPLIT IS OFF."),
    (dict(asked=0, found=2), "THE SWITCH IS OFF IN .env BUT 2 WORKER PROCESS(ES) ARE RUNNING."),
    (dict(asked=4, found=3), "A WORKER IS MISSING. .env asks for 4 and 3 are running."),
    (dict(asked=1, found=0), "A WORKER IS MISSING. .env asks for 1 and 0 are running."),
    (dict(), "CLEAN, AND A CHANNEL HAS DONE EVERYTHING THE TRIAL IS FOR IN A WORKER:"),
    (dict(summary=K.summarize(HEALTHY[:5])), "CLEAN SO FAR:"),
    (dict(summary=K.summarize(HEALTHY[:3])), "THE WORKERS ARE RUNNING AND HAVE LOGGED NOTHING"),
    (dict(summary=K.summarize([]), journal=False), "THE PROCESSES ARE THERE, but nothing the site logged could be read"),
    # A journal that answers with no lines at all was not really read: the
    # web process alone logs more than nothing. Never "the workers were quiet".
    (dict(summary=K.summarize([]), journal=True), "THE PROCESSES ARE THERE, but nothing the site logged could be read"),
    (dict(summary=K.summarize(HEALTHY + [L("ingest_worker_lost", "error", index=0)])),
     "A WORKER DIED 1 TIME(S)"),
    (dict(summary=K.summarize(HEALTHY + [L("worker_session_error", "error", ingest=0)])),
     "THE WORKERS LOGGED 1 ERROR(S)"),
])
def test_the_verdict_is_the_worst_thing_and_only_that(kw, starts):
    assert _v(**kw)[0].startswith(starts)


def test_clean_is_never_said_over_a_death_or_a_forbidden_import():
    bad = HEALTHY + [L("ingest_worker_lost", "error", index=0),
                     L("x", "error", ingest=0, error="must not be imported")]
    v = " ".join(_v(summary=K.summarize(bad)))
    assert "CLEAN" not in v and v.startswith("STOP")


def test_the_channels_of_a_trial_are_named_and_the_rest_are_only_counted(tmp_path):
    recs = [{"channel": "Somebody", "status": "live", "user_id": "u1"},
            {"channel": "somebody", "status": "paused", "user_id": "u2"},
            {"channel": "another", "status": "offline", "user_id": "u1"},
            {"channel": "", "status": "live"}, "junk"]
    (tmp_path / "streams.json").write_text(json.dumps(recs))
    trial = K.channels(tmp_path, 1, ["somebody", "nobodyadded"])
    assert list(trial["rows"]) == ["somebody"] and trial["missing"] == ["nobodyadded"]
    assert trial["rows"]["somebody"]["listed"] == 2
    assert dict(trial["rows"]["somebody"]["statuses"]) == {"live": 1, "paused": 1}
    every = K.channels(tmp_path, 2, [])
    assert every["rows"] == {} and sum(sum(c.values()) for c in every["per_worker"].values()) == 3
    assert K.channels(tmp_path / "missing", 1, []) == {"readable": False}


def test_a_log_file_can_stand_in_for_the_journal(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("\n".join(HEALTHY))
    lines, ok, where = K.read_lines(30, str(f))
    assert ok and len(lines) == len(HEALTHY) and where == "the file given"
    assert K.read_lines(30, str(tmp_path / "nope.log"))[1] is False


def test_the_whole_thing_runs_and_prints_no_field_of_any_line(tmp_path, monkeypatch, capsys):
    from config.settings import settings
    secret = [L("stream_found", ingest=0, channel="somebody", user_id="acct-SECRET-1",
                url="https://x.example/?token=SECRET-TOKEN", email="person@example.com"),
              L("worker_session_error", "error", ingest=0, error="boom SECRET-ERR at 10.0.0.9")]
    f = tmp_path / "app.log"
    f.write_text("\n".join(HEALTHY + secret))
    (tmp_path / "streams.json").write_text(json.dumps(
        [{"channel": "somebody", "status": "live", "user_id": "acct-SECRET-1"}]))
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(settings, "ingest_workers", 1)
    monkeypatch.setattr(settings, "ingest_only", "somebody")
    monkeypatch.setattr(K, "workers_running", lambda: [{"index": 0, "rss": 74 * 1048576}])
    assert K.main([f"--log={f}", "--minutes=5"]) == 0
    out = capsys.readouterr().out
    assert "INGEST_WORKERS=1, INGEST_ONLY names 1 channel" in out and "worker 0: 74MB" in out
    assert "somebody               : worker 0, live" in out
    assert "worker_session_error" in out and "THE WORKERS LOGGED 1 ERROR(S)" in out
    assert "cut a clip's video                 : yes (2)" in out
    for private in ("SECRET", "person@example.com", "10.0.0.9", "token="):
        assert private not in out


def test_it_reads_this_machine_for_real():
    """/proc as it is. Whatever is running, a row is an index and a size,
    and this test process (a python that is not a worker) is not one."""
    import os
    cost = K._stream_cost()
    assert cost.procs()[os.getpid()][0] == "other_python"
    assert all(set(w) == {"index", "rss"} and w["rss"] > 0 for w in K.workers_running())


def test_it_never_prints_a_command_line_or_a_field():
    src = _PATH.read_text()
    printing = src[src.index("def main("):src.index('if __name__ == "__main__":')]
    assert "argv[" not in printing and "cmdline" not in printing
    # summarize keeps the event's name and the level; nothing else leaves it.
    body = src[src.index("def summarize("):src.index("def milestones(")]
    assert body.count("d.get(") == 3 and 'd["event"]' in body
