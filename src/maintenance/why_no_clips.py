"""
Where do clips die? Per channel, counted from the logs.

    journalctl -u highlightz --since "3 days ago" --no-pager \
      | venv/bin/python -m src.maintenance.why_no_clips

Read-only; parses stdin and prints a table. Nothing is inferred — every column
is a literal count of a log event the engine emits.

WHY THIS EXISTS: a channel can score over its threshold thousands of times and
still produce zero clips, and there are four separate places that can swallow
it. Guessing which one costs a round-trip to production each time (the token
theory and the calibration theory were both wrong before this script existed).
The engine already logs the reason at every gate — this just tallies them.

The gates, in the order src/trigger/engine.py applies them:

  calibrating   profile has fewer than calibration_target velocity samples;
                EVERY trigger is suppressed until it does
  cooldown      inside rules.cooldown_seconds of the last clip, and the score
                was not high enough to invoke the emergency override
  fired         passed every gate — a clip job was queued
  not_ready     the clip WAS created on Twitch but never finished processing
                (broadcast too short, or VODs disabled on the channel)
  create_failed the Helix call itself failed

fired >> clips in clips.json means the failure is AFTER the trigger — look at
the last two columns. fired ≈ 0 with a healthy score means the failure is at a
gate.
"""

import json
import re
import sys
from collections import defaultdict

EVENTS = {
    "trigger_fired":                  "fired",
    "trigger_suppressed_cooldown":    "cooldown",
    "trigger_suppressed_calibrating": "calibrating",
    "trigger_cooldown_override":      "override",
    "twitch_clip_not_ready":          "not_ready",
    "twitch_clip_create_failed":      "create_failed",
    "clip_failed":                    "clip_failed",
}
COLS = ["fired", "override", "cooldown", "calibrating",
        "not_ready", "create_failed", "clip_failed"]

_JSON = re.compile(r"\{.*\}\s*$")


def parse(stream) -> tuple[dict, dict]:
    """Returns (per-channel counts, unattributed counts).

    Some events are logged without a channel; they are reported separately
    rather than dropped, because a large unattributed count would mean this
    table is missing part of the story.
    """
    per: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    loose: dict[str, int] = defaultdict(int)
    for line in stream:
        if "event" not in line:
            continue
        m = _JSON.search(line)
        if not m:
            continue
        try:
            rec = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        col = EVENTS.get(rec.get("event", ""))
        if not col:
            continue
        chan = rec.get("channel")
        if chan:
            per[chan][col] += 1
        else:
            loose[col] += 1
    return per, loose


def main() -> int:
    if sys.stdin.isatty():
        print(__doc__.strip().splitlines()[0])
        print('\nusage: journalctl -u highlightz --since "3 days ago" --no-pager '
              '| venv/bin/python -m src.maintenance.why_no_clips')
        return 2

    per, loose = parse(sys.stdin)
    if not per and not loose:
        print("No trigger/clip events found in that log range.")
        print("Check the --since window, and that the unit name is right.")
        return 0

    w = max([18] + [len(c) for c in per])
    print()
    print(f"{'channel':<{w}}" + "".join(f"{c:>14}" for c in COLS))
    print("-" * (w + 14 * len(COLS)))
    for chan in sorted(per, key=lambda c: -sum(per[c].values())):
        row = per[chan]
        print(f"{chan:<{w}}" + "".join(f"{row.get(c, 0):>14}" for c in COLS))
    if loose:
        print(f"{'(no channel)':<{w}}" + "".join(f"{loose.get(c, 0):>14}" for c in COLS))

    print()
    print("HOW TO READ IT")
    print("  fired ≈ 0, cooldown ≈ 0, calibrating ≈ 0")
    print("      the score never crossed the threshold — a formula/threshold")
    print("      question, and the only case where retuning helps.")
    print("  fired ≈ 0, calibrating large")
    print("      every trigger suppressed waiting on the baseline. Check why")
    print("      the profile never reaches calibration_target.")
    print("  fired ≈ 0, cooldown large")
    print("      the score IS crossing, and cooldown is eating it. Tune")
    print("      cooldown_seconds / emergency_threshold, not the weights.")
    print("  fired large, but few clips in clips.json")
    print("      the trigger works and the failure is downstream — look at")
    print("      not_ready / create_failed, and at the post-roll monitor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
