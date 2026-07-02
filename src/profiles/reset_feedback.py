"""
One-time maintenance: reset the FEEDBACK-learned state on every streamer profile.

Why: approve/reject feedback was silently dropped on cache misses (fixed in the
load()-not-get() change), so per-channel trigger_threshold and signal_weights
drifted — busy channels ratcheted their threshold up toward the 65 cap from
rejections with no balancing approvals. This restores a clean starting point so
the now-correct feedback loop re-learns from scratch.

What it resets (the bug-corrupted, feedback-derived fields ONLY):
  - trigger_threshold  -> 60.0 (neutral default)
  - signal_weights     -> 1.0 for every signal
  - approved_clips / rejected_clips / total_clips -> 0

What it KEEPS (observation-derived, NOT affected by the feedback bug):
  - avg_velocity / var_velocity / velocity_samples (the channel's "normal")
  - audio / sentiment / keyword baselines, calibration, research flags, etc.
  Keeping these means no recalibration blackout — channels keep clipping.

Usage (on the server, from /opt/highlightz):
  venv/bin/python -m src.profiles.reset_feedback           # DRY RUN (preview)
  venv/bin/python -m src.profiles.reset_feedback --apply   # write (with .bak backups)

--raise-floor mode: a surgical alternative to the full reset. Only lifts
trigger_threshold up to the dry-spell floor (profiles the old -3/10min
dry-spell dragged down to 40 stay there otherwise, since the dry-spell only
ever lowers). Keeps ALL learning — weights, counts, baselines — untouched.
  venv/bin/python -m src.profiles.reset_feedback --raise-floor           # preview
  venv/bin/python -m src.profiles.reset_feedback --raise-floor --apply   # write
"""

import json
import shutil
import sys
from pathlib import Path

from config.settings import settings
from src.profiles.profile import StreamerProfile, _SIGNAL_KEYS
from src.trigger.scoring import DRY_SPELL_THRESHOLD_FLOOR

_NEUTRAL_THRESHOLD = 60.0
_PROFILES_ROOT = Path(settings.local_storage_path) / "profiles"


def _iter_profile_files():
    # Per-user dirs (profiles/<user_id>/<channel>.json) and any global ones.
    yield from sorted(_PROFILES_ROOT.glob("**/*.json"))


def main() -> None:
    apply       = "--apply" in sys.argv[1:]
    raise_floor = "--raise-floor" in sys.argv[1:]
    files = list(_iter_profile_files())
    if not files:
        print(f"No profile files under {_PROFILES_ROOT}")
        return

    mode = "APPLY" if apply else "DRY RUN (no files written — pass --apply to write)"
    print(f"Profiles root: {_PROFILES_ROOT}")
    print(f"Mode: {mode}" + ("  [raise-floor only]" if raise_floor else "  [full reset]"))
    print(f"Found {len(files)} profile file(s)\n")
    print(f"{'channel':22} {'thr':>6} {'appr':>5} {'rej':>5}   ->  change")
    print("-" * 60)

    changed = 0
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            p = StreamerProfile.from_dict(raw)
        except Exception as exc:
            print(f"SKIP {path.name}: unreadable ({exc})")
            continue

        before = f"{p.channel:22} {p.trigger_threshold:6.1f} {p.approved_clips:5d} {p.rejected_clips:5d}"

        if raise_floor:
            # Surgical: only lift thresholds the old dry-spell dragged below the
            # (new) floor. Everything learned stays as-is.
            if p.trigger_threshold >= DRY_SPELL_THRESHOLD_FLOOR:
                print(f"{before}   ->  unchanged (already >= {DRY_SPELL_THRESHOLD_FLOOR:.0f})")
                continue
            p.trigger_threshold = DRY_SPELL_THRESHOLD_FLOOR
            print(f"{before}   ->  thr={DRY_SPELL_THRESHOLD_FLOOR:.0f} (learning kept)")
        else:
            # Reset only the feedback-derived fields.
            p.trigger_threshold = _NEUTRAL_THRESHOLD
            p.signal_weights = {k: 1.0 for k in _SIGNAL_KEYS}
            p.approved_clips = 0
            p.rejected_clips = 0
            p.total_clips = 0
            print(f"{before}   ->  thr={_NEUTRAL_THRESHOLD:.0f} weights=1.0 counts=0")

        if apply:
            shutil.copy2(path, path.with_suffix(".json.bak"))   # reversible
            path.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
        changed += 1

    print("-" * 60)
    if apply:
        print(f"Updated {changed} profile(s). Backups written alongside as *.json.bak.")
        print("Restart the service so the in-memory cache reloads:")
        print("  systemctl restart highlightz")
    else:
        print(f"Would update {changed} profile(s). Re-run with --apply to write.")


if __name__ == "__main__":
    main()
