"""One change to a channel's profile, applied in whichever process holds it.

THE SPLIT, stage 3 (docs/HANDOFF.md, "THE SPLIT"). A channel's profile is ONE
object in memory: the stream worker scores against it every second and saves
it every 30, and the dashboard changes it when a clip is approved or
rejected, an accidental reject is undone, or the sensitivity or preset is
changed. In one process they share the object. Split, the worker that is
watching the channel holds it, and a change made in the dashboard has to be
made THERE: made in the web process's own copy it would never reach the
detector, and the worker's next save would overwrite it.

So each change is a small dict (`op`) that can be sent, and `apply` is the
one function that makes it, on the ProfileManager of the process it runs in:

    {"op": "record_clip", "approved": bool, "signals": [...]}
    {"op": "restore", "snap": {...}}                 an undo (see `snapshot`)
    {"op": "set", "sensitivity": int | None, "preset": str | None}

It returns what the dashboard needs afterwards: the profile as a dict, the
snapshot from before (what makes a reject fully reversible), and whether
anything changed.

`snapshot` and `restore` moved here from src/dashboard/undo.py, which still
exports them under their old names.
"""

from __future__ import annotations

from src.profiles.manager import get_profile_manager


def snapshot(profile) -> dict:
    """The mutable scoring state record_clip() touches, and nothing else."""
    return {
        "trigger_threshold": profile.trigger_threshold,
        "signal_weights":    dict(profile.signal_weights),
        "total_clips":       profile.total_clips,
        "approved_clips":    profile.approved_clips,
        "rejected_clips":    profile.rejected_clips,
    }


def restore(profile, snap: dict) -> None:
    profile.trigger_threshold = snap["trigger_threshold"]
    profile.signal_weights    = dict(snap["signal_weights"])
    profile.total_clips       = snap["total_clips"]
    profile.approved_clips    = snap["approved_clips"]
    profile.rejected_clips    = snap["rejected_clips"]


async def apply(uid: str | None, channel: str, op: dict, platform: str = "twitch") -> dict | None:
    """Make one change here, save it, and say what happened. None if there is
    no profile to change. A "set" that changes nothing saves nothing."""
    pm = get_profile_manager(uid)
    profile = await pm.load(channel, platform)
    if not profile:
        return None
    before = snapshot(profile)
    kind = op.get("op")
    touched = True
    if kind == "record_clip":
        profile.record_clip(approved=bool(op.get("approved")), signals=op.get("signals") or [])
    elif kind == "restore":
        restore(profile, op["snap"])
    elif kind == "set":
        touched = False
        sens = op.get("sensitivity")
        if sens is not None and int(sens) != int(getattr(profile, "sensitivity", 0)):
            profile.sensitivity = int(sens)
            touched = True
        if op.get("preset") is not None:
            profile.preset = op["preset"]
            touched = True
    else:
        raise ValueError(f"unknown profile op {kind!r}")
    if touched:
        await pm.save(profile)
    return {"profile": profile.to_dict(), "before": before, "touched": touched}
