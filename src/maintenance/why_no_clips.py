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

import io
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

    raw = sys.stdin.read()
    per, loose = parse(io.StringIO(raw))
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

    print_failures(failures(io.StringIO(raw)))
    return 0



# ── Failure breakdown ─────────────────────────────────────────────────────────

def failures(stream) -> dict:
    """Tally create_clip failures by (status, message, broadcaster_id).

    Kept separate from the gate table because these carry a broadcaster_id
    rather than a channel name, so they cannot be attributed without a lookup.
    """
    out: dict = defaultdict(int)
    for line in stream:
        if "twitch_clip_create_failed" not in line:
            continue
        m = _JSON.search(line)
        if not m:
            continue
        try:
            rec = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        body = rec.get("body") or ""
        try:
            msg = json.loads(body).get("message", body)[:70]
        except (json.JSONDecodeError, AttributeError):
            msg = body[:70]
        out[(rec.get("status"), msg, str(rec.get("broadcaster_id") or ""))] += 1
    return out


async def _resolve_names(ids: set) -> dict:
    """broadcaster_id -> login, via Helix Get Users. Best effort: an unresolved
    id is still printed, just without a name."""
    import aiohttp
    from config.settings import settings
    from src.output.twitch_clips import HELIX_BASE, _get_app_token
    names: dict = {}
    ids = [i for i in ids if i]
    if not ids:
        return names
    async with aiohttp.ClientSession() as s:
        token = await _get_app_token(s)
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {token}"}
        for i in range(0, len(ids), 100):
            params = [("id", x) for x in ids[i:i + 100]]
            async with s.get(f"{HELIX_BASE}/users", headers=headers,
                             params=params) as r:
                if r.status != 200:
                    continue
                for u in (await r.json()).get("data", []):
                    names[str(u["id"])] = u.get("login", "")
    return names


def print_failures(tally: dict) -> None:
    import asyncio
    if not tally:
        print("\nNo clip-creation failures logged.")
        return
    names = {}
    try:
        names = asyncio.run(_resolve_names({b for (_, _, b) in tally}))
    except Exception as exc:
        print(f"(could not resolve broadcaster names: {exc})")

    print("\nCLIP-CREATION FAILURES")
    print(f"{'count':>7}  {'status':>6}  {'channel':<20} {'message'}")
    print("-" * 96)
    for (status, msg, bid), n in sorted(tally.items(), key=lambda kv: -kv[1]):
        who = names.get(bid) or f"id:{bid}" if bid else "?"
        print(f"{n:>7}  {status:>6}  {who:<20} {msg}")
    print()
    print("'User not authorized to create clips' is a CHANNEL setting, not our")
    print("bug — the streamer has restricted clipping (often to subs/followers).")
    print("No amount of formula or threshold work changes it; the channel simply")
    print("cannot be clipped by us. Surface it to the user instead of silently")
    print("monitoring a channel that can never produce a clip.")

if __name__ == "__main__":
    raise SystemExit(main())
