"""The not-ready report (src/maintenance/clip_not_ready_report.py), on a
made-up journal written the way production writes it: one clip job at a
time, so each Create Clip follows its own job's lines.

It is run on production to say what a burst of `twitch_clip_not_ready` is:
Twitch or one channel, one account or all of them, slow or never made, and
what the ~50 s wait on each lost clip cost every other job in the one queue.
Each number that separates those is pinned here."""

import json
from datetime import datetime, timezone

from src.maintenance import clip_not_ready_report as nr

T0 = datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc).timestamp()
T1 = T0 + 3600
NAMES = {"u1": "luclips", "u2": "nova", "u3": "pha"}


def _line(ev: str, at: float, **kw) -> str:
    ts = datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({"event": ev, "timestamp": ts, "level": "info", **kw})


def _twitch(lines, at, user, slug, outcome, seconds=5.0, waited=1.0, channel="jynxzi", bid="111"):
    """One Twitch job, in the order the processor logs it."""
    lines.append(_line("processing_clip_job", at - 6, clip_id=slug + "-id", channel=channel,
                       user_id=user, platform="twitch", age_s=waited))
    lines.append(_line("creating_twitch_clip", at - 5, clip_id=slug + "-id", channel=channel,
                       broadcaster_id=bid, user_id=user))
    lines.append(_line("twitch_clip_requested", at, slug=slug, broadcaster_id=bid, attempt=1))
    if outcome == "ready":
        lines.append(_line("twitch_clip_ready_after", at + seconds, slug=slug, seconds=seconds))
    elif outcome == "not ready":
        lines.append(_line("twitch_clip_not_ready", at + 50, slug=slug, channel=channel, waited_seconds=50.0))


def _journal():
    lines = ["Started highlightz.service.", "not json {", '{"event": torn']
    # 11:00 — four quick successes, then BOTH accounts fail in a run, the
    # queue spacing them ~56 s apart; the Kick jobs behind them wait, and two
    # are dropped as stale.
    for k, user in enumerate(("u1", "u2", "u1", "u2")):
        _twitch(lines, T0 + 60 + 12 * k, user, f"ok{k}", "ready", waited=2 + k)
    for k, (user, waited) in enumerate((("u1", 10), ("u2", 60), ("u1", 70), ("u2", 85))):
        _twitch(lines, T0 + 600 + 56 * k, user, f"run{k}", "not ready", waited=waited)
    lines.append(_line("processing_clip_job", T0 + 800, clip_id="k1", channel="westcol", user_id="u1",
                       platform="kick", age_s=80))
    lines.append(_line("processing_clip_job", T0 + 850, clip_id="k2", channel="westcol", user_id="u1",
                       platform="kick", age_s=88))
    lines.append(_line("clip_job_stale_dropped", T0 + 900, clip_id="k3", channel="westcol", age_s=95))
    lines.append(_line("clip_job_stale_dropped", T0 + 950, clip_id="k4", channel="westcol", age_s=120))
    # 12:00 — only u1 fails; u2 succeeds between its failures.
    for k, (user, outcome, waited) in enumerate((("u1", "not ready", 5), ("u2", "ready", 50),
                                                 ("u1", "not ready", 20), ("u2", "ready", 30),
                                                 ("u1", "not ready", 15), ("u2", "ready", 40))):
        _twitch(lines, T1 + 100 + 60 * k, user, f"solo{k}", outcome, seconds=6.0, waited=waited)
    _twitch(lines, T1 + 600, "u3", "pha0", "ready", seconds=27.5, waited=1,
            channel="phantasm__", bid="222")
    lines.append(_line("clip_skipped_queue_full", T1 + 700, channel="westcol", user_id="u1"))
    lines.append(_line("twitch_clip_rate_limited", T1 + 710, broadcaster_id="222", attempt=1))
    return lines


def _run(lines, twitch=None):
    out = []
    nr.report(lines, out=out.append, twitch=twitch, names=lambda: NAMES)
    return "\n".join(out)


def _rows(text: str, section: str, nxt: str) -> dict:
    """The section's rows, keyed by their first cell(s)."""
    body = text.split(section)[1].split(nxt)[0] if nxt else text.split(section)[1]
    rows = {}
    for line in body.splitlines()[2:]:
        cells = line.split()
        if cells:
            rows[" ".join(cells[:2]) if cells[0].count("-") == 1 and len(cells) > 1 else cells[0]] = cells
    return rows


def test_each_request_belongs_to_the_job_before_it():
    s = nr.collect(_journal())["slugs"]
    assert [s[f"run{k}"]["user"] for k in range(4)] == ["u1", "u2", "u1", "u2"]
    assert s["solo1"]["user"] == "u2" and s["pha0"]["user"] == "u3"
    assert s["ok0"]["channel"] == "jynxzi"            # ready_after has no channel: via broadcaster id


def test_per_hour_the_lost_clips_and_the_minutes_the_queue_waited_on_them():
    # Held from each lost job's start (post-roll included) to the give-up:
    # 56 s each, so 4 x 56 s = 3.7 min and 3 x 56 s = 2.8 min.
    rows = _rows(_run(_journal()), "== per hour", "== the queue")
    assert rows["10-23 11:00"][2:] == ["8", "4", "4", "50%", "5.0", "s", "3.7", "min"]
    assert rows["10-23 12:00"][2:] == ["7", "4", "3", "43%", "27.5", "s", "2.8", "min"]


def test_the_queue_shows_what_the_waits_cost_everyone_else():
    text = _run(_journal())
    rows = _rows(text, "== the queue", "== per channel")
    # 11:00: ten jobs waited 2..88 s; two Kick jobs were dropped as stale.
    assert rows["10-23 11:00"][2:] == ["10", "35", "s", "85", "s", "88", "s", "2", "0"]
    assert rows["10-23 12:00"][2:] == ["7", "20", "s", "40", "s", "50", "s", "0", "1"]
    assert "dropped stale, by channel: westcol 2" in text


def test_per_account_says_whether_one_account_or_all_of_them_fail():
    text = _run(_journal())
    rows = _rows(text, "== per account", "ready times")
    assert rows["luclips"][1:] == ["4/2", "3/3"]       # failed in both hours
    assert rows["nova"][1:] == ["4/2", "3/0"]          # failed with luclips, then not alone
    assert rows["pha"][1:] == ["-", "1/0"]
    assert "also: phantasm__ twitch_clip_rate_limited x1" in text
    assert "ready times: median 5.5 s, 90th 6.0 s, slowest 27.5 s" in text


def test_twitch_is_asked_what_became_of_the_lost_ones_newest_first():
    asked = []

    async def twitch(slugs):
        asked.extend(slugs)
        return {s: ("finished late" if s == "run0" else "never made") for s in slugs}

    text = _run(_journal(), twitch)
    assert asked == ["solo4", "solo2", "solo0", "run3", "run2", "run1", "run0"]
    lost = text.split("asked of Twitch now ==")[1]
    assert "finished late 1, never made 6" in lost
    first = lost.splitlines()[2]
    assert "jynxzi" in first and "luclips" in first and "solo4" in first


def test_a_twitch_that_cannot_be_asked_is_said_so():
    async def down(slugs):
        return None
    assert "Twitch could not be asked" in _run(_journal(), down)


def test_a_kick_only_window_still_shows_the_queue():
    lines = [_line("processing_clip_job", T0 + 10, clip_id="k", channel="westcol", user_id="u1",
                   platform="kick", age_s=3)]
    text = _run(lines)
    assert "== the queue" in text and "== per channel" not in text


def test_an_empty_window_says_so():
    assert _run(["nothing here"]) == "No clip jobs in this window."
