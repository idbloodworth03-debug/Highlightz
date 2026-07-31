"""why_no_clips: log tally that decides where clips die."""

import io
import json

from src.maintenance import why_no_clips as w


def line(event, channel=None, **kw):
    """A journalctl line: syslog prefix, then structlog's JSON."""
    rec = {"event": event, **({"channel": channel} if channel else {}), **kw}
    return f"Jul 31 03:00:00 host highlightz[123]: {json.dumps(rec)}\n"


def test_counts_each_gate_against_the_right_channel():
    src = io.StringIO("".join([
        line("trigger_fired", "jynxzi"),
        line("trigger_fired", "jynxzi"),
        line("trigger_suppressed_cooldown", "jynxzi"),
        line("trigger_suppressed_calibrating", "kaicenat"),
        line("twitch_clip_not_ready", "lacy"),
    ]))
    per, loose = w.parse(src)
    assert per["jynxzi"]["fired"] == 2
    assert per["jynxzi"]["cooldown"] == 1
    assert per["kaicenat"]["calibrating"] == 1
    assert per["lacy"]["not_ready"] == 1
    assert not loose


def test_events_without_a_channel_are_reported_not_dropped():
    """A big unattributed count would mean the per-channel table is missing
    part of the story — silently discarding those would hide it."""
    per, loose = w.parse(io.StringIO(line("trigger_fired")))
    assert loose["fired"] == 1 and not per


def test_unrelated_log_noise_is_ignored():
    src = io.StringIO("".join([
        "plain text with no json at all\n",
        line("score_update", "jynxzi"),          # real event, not a gate
        "Jul 31 host x: {broken json\n",
        line("trigger_fired", "jynxzi"),
    ]))
    per, _ = w.parse(src)
    assert dict(per["jynxzi"]) == {"fired": 1}


def test_trailing_json_is_found_after_a_syslog_prefix():
    """structlog's JSON sits at the END of a journalctl line, after the host
    and PID. Anchoring on the first '{' would capture the wrong span when the
    prefix contains braces."""
    per, _ = w.parse(io.StringIO(
        "Jul 31 03:00:00 host highlightz[1]: " + json.dumps(
            {"event": "trigger_fired", "channel": "c", "signals": {"CHAT": 1}}) + "\n"))
    assert per["c"]["fired"] == 1
