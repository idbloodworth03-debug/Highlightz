"""
Show which channels are stuck at a raised trigger threshold, and what the
elapsed-time decay fix will do to each on its next load.

Background: threshold decay used to run only inside `_profile_update_loop`,
which ticks `while self._running` — so it applied only while a channel was
ACTIVELY monitored, and only after an hour of continuous uptime. A channel
pushed to the 80 ceiling by rejections and then left unwatched froze there:
nothing fires at 80, so nothing gets approved, so nothing brings it back down.

Decay is now a function of elapsed wall-clock time and is applied when the
profile loads, so those channels heal on their own. This script previews that
— it never writes anything.

  venv/bin/python -m src.maintenance.show_stuck_profiles
"""

import json
import sys
import time
from pathlib import Path

from config.settings import settings
from src.profiles.profile import _THRESHOLD_REVERSION
from src.trigger.rules import get_rules


def main() -> int:
    base = Path(settings.local_storage_path) / "profiles"
    if not base.exists():
        print(f"no profiles directory at {base}")
        return 1

    now = time.time()
    rows = []
    for pf in sorted(base.glob("*/*.json")):
        try:
            d = json.loads(pf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ch = d.get("channel") or pf.stem
        thr = float(d.get("trigger_threshold") or 60.0)
        seed = get_rules(ch, "default").trigger_threshold
        last = float(d.get("last_decay_ts") or 0.0)
        # Profiles saved before this fix have no clock; their first load only
        # starts it, so the honest preview is "recovery begins then".
        hours = (now - last) / 3600.0 if last > 0 else 0.0
        retained = (1.0 - _THRESHOLD_REVERSION) ** min(hours, 720.0)
        after = seed + (thr - seed) * retained if last > 0 else thr
        rows.append((ch, thr, seed, after, last, pf))

    if not rows:
        print("no profiles found")
        return 0

    stuck = [r for r in rows if r[1] > r[2] + 5]
    print(f"=== {len(rows)} profiles, {len(stuck)} sitting well above their seed ===\n")
    print(f"  {'channel':22s} {'now':>6s} {'seed':>6s} {'after load':>11s}   note")
    for ch, thr, seed, after, last, _ in sorted(rows, key=lambda r: -r[1])[:30]:
        if last <= 0:
            note = "clock starts on next load; recovers from there"
        elif abs(thr - after) < 0.2:
            note = "already at/near seed"
        elif after < thr:
            note = f"drops {thr - after:.1f} points immediately"
        else:
            note = f"rises {after - thr:.1f} toward seed"
        flag = "  <-- STUCK" if thr > seed + 5 else ""
        print(f"  {ch[:22]:22s} {thr:6.1f} {seed:6.1f} {after:11.1f}   {note}{flag}")

    print("\nHow recovery behaves from the ceiling (80) toward a 60 seed:")
    for h in (1, 6, 24, 72, 168):
        v = 60 + 20 * (1.0 - _THRESHOLD_REVERSION) ** h
        print(f"  after {h:3d}h unwatched: {v:5.1f}")
    print("\nActive rejections still hold the bar up — one rejection is +0.75,")
    print("and decay only pulls ~10% of the gap per hour, so a channel you are")
    print("genuinely rejecting on stays strict. This only rescues neglect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
