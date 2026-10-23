"""The not-ready report (src/maintenance/clip_not_ready_report.py), on a
made-up journal. It is run on production to tell four explanations of a burst
of `twitch_clip_not_ready` apart (all of Twitch, one channel, several accounts
clipping one channel at once, or Twitch finishing after we stopped waiting),
so each number that separates them is pinned here."""

import json
from datetime import datetime, timezone

from src.maintenance import clip_not_ready_report as nr

T0 = datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc).timestamp()


def _line(ev: str, at: float, **kw) -> str:
    ts = datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({"event": ev, "timestamp": ts, "level": "info", **kw})


def _request(lines, at, channel, bid, user, slug, outcome, seconds=20.0):
    lines.append(_line("creating_twitch_clip", at - 5, channel=channel, broadcaster_id=bid, user_id=user))
    lines.append(_line("twitch_clip_requested", at, slug=slug, broadcaster_id=bid, attempt=1))
    if outcome == "ready":
        lines.append(_line("twitch_clip_ready_after", at + seconds, slug=slug, seconds=seconds))
    elif outcome == "not ready":
        lines.append(_line("twitch_clip_not_ready", at + 50, slug=slug, channel=channel, waited_seconds=50.0))


def _journal():
    lines = ["Started highlightz.service.", "not json {", '{"event": torn']
    # jynxzi, 11:00-11:59: three accounts clip each moment within seconds of
    # each other; most of those fail. One lone request succeeds.
    for k in range(4):
        base = T0 + k * 600
        for j, user in enumerate(("u1", "u2", "u3")):
            ok = "ready" if j == 0 else "not ready"
            _request(lines, base + j * 3, "jynxzi", "111", user, f"jy{k}{j}", ok, seconds=25.0)
    _request(lines, T0 + 3000, "jynxzi", "111", "u1", "jyalone", "ready", seconds=42.5)
    # xqc, the same hour, one account: all ready.
    for k in range(3):
        _request(lines, T0 + 200 + k * 900, "xqc", "222", "u4", f"xq{k}", "ready", seconds=18.0)
    # 12:00: one request with no outcome yet, and a refusal on xqc.
    _request(lines, T0 + 3700, "xqc", "222", "u4", "xqpending", None)
    lines.append(_line("twitch_clip_rate_limited", T0 + 3710, broadcaster_id="222", attempt=1))
    return lines


def _run(lines, twitch=None):
    out = []
    nr.report(lines, out=out.append, twitch=twitch)
    return "\n".join(out)


def test_requests_are_joined_to_their_outcome_and_channel():
    data = nr.collect(_journal())
    s = data["slugs"]
    assert s["jy00"]["outcome"] == "ready" and s["jy00"]["channel"] == "jynxzi"   # channel via broadcaster id
    assert s["jy01"]["outcome"] == "not ready" and s["jy01"]["crowded"]
    assert s["jyalone"]["crowded"] is False and s["xq0"]["crowded"] is False
    assert s["xqpending"]["outcome"] == "no outcome yet"
    assert data["users"]["jynxzi"] == {"u1", "u2", "u3"}
    assert data["refusals"] == {("xqc", "twitch_clip_rate_limited"): 1}


def test_the_hours_and_channels_say_where_it_fails():
    text = _run(_journal())
    hours = text.split("== per channel")[0]
    # 11:00: 12 crowded jynxzi + 1 lone jynxzi + 3 xqc = 16 requests, 8 lost.
    assert "10-23 11:00          16      8          8   50%   42.5 s" in hours
    assert "10-23 12:00           1      0          0    0%   -" in hours
    chans = text.split("== per channel ==")[1]
    jy = next(l for l in chans.splitlines() if "jynxzi" in l)
    # Crowded requests fail 8 of 12; the lone one does not.
    assert "13      5          8  62%         3" in jy, jy
    assert "67% of  12 |   0% of   1" in jy, jy
    xq = next(l for l in chans.splitlines() if l.strip().startswith("xqc"))
    assert "   0%         1" in xq and "0% of   4" in xq, xq
    assert "also: xqc twitch_clip_rate_limited x1" in chans
    assert "ready times: median 25.0 s, 90th 25.0 s, slowest 42.5 s" in text


def test_twitch_is_asked_what_became_of_the_lost_ones():
    asked = []

    async def twitch(slugs):
        asked.extend(slugs)
        return {s: ("finished late" if s.endswith("1") else "never made") for s in slugs}

    text = _run(_journal(), twitch)
    assert sorted(asked) == sorted(f"jy{k}{j}" for k in range(4) for j in (1, 2))
    lost = text.split("asked of Twitch now ==")[1]
    assert "finished late 4, never made 4" in lost
    assert asked[0] == "jy32"                      # newest first


def test_a_twitch_that_cannot_be_asked_is_said_so():
    async def down(slugs):
        return None
    assert "Twitch could not be asked" in _run(_journal(), down)


def test_an_empty_window_says_so():
    assert _run(["nothing here"]) == "No Twitch clip requests in this window."
