"""Are the rules that pause Free accounts being applied? One read-only look.

    cd /opt/highlightz && venv/bin/python scripts/reaper_check.py

Owner, 2026-11-19: "can you double check that all of the reaper bot settings
are working and kicking off free tier users for all the cases I mentioned
earlier I do not see it working". The tests say the rules work. That says
nothing about the server. This reads the server.

THE RULES (docs/HANDOFF.md, the two "Free" sections). A Free account's
channels are paused when:
  1. its review queue is full and nobody is there (no tab, no request, for 5
     minutes);
  2. its review queue has been full for 15 minutes with nothing accepted or
     rejected, whatever tabs are open;
  3. it has not used the dashboard for 2 hours (8 until 2026-11-19).
A channel paused for a full queue stays paused until the queue has room.
AND WHAT IS NOT A RULE: a Free account whose queue has room, and which has
been at the dashboard in the last 2 hours, is watched. That is the plan.

WHAT THIS DOES. For every Free account with a channel it reads the same
files the site runs on (the channels and whether each is paused, the clips
waiting in the queue and when the newest landed, when the account last made
a request, the last clip it accepted or rejected) and puts the account in
one of: paused for a full queue; paused for another reason; being watched
because the rules say so (room in the queue, or full for less than 15
minutes); or SHOULD BE PAUSED AND IS NOT, with which rule and how late.

It also says whether the site that is RUNNING is the code that is on disk:
a rule that is in the files and not in the running process pauses nobody,
and that is the first thing to rule out.

WHAT IT CANNOT SEE, and how that is handled. Which accounts have a tab open
is in the running site's memory, not in a file. It does not need it: a full
queue is paused within 15 minutes with or without a tab, so anything full
for longer and still watched is late by either rule. When an account last
made a request is saved every five minutes, so the 2-hour check allows for
that.

Read-only. Prints counts, and the NAME of an account only when it should
have been paused and was not. Never an address, an id or a setting.
"""
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNIT = "highlightz"
FULL_FOR = 900              # api._QUEUE_FULL_UNREVIEWED
IDLE_FOR = 7200             # api._IDLE_STREAM_TIMEOUT
IDLE_WORDS = f"{IDLE_FOR // 3600} hours"
QUEUE_SLACK = 180           # the rule is asked every minute; three is late
IDLE_SLACK = 900            # asked every five minutes, the clock saved every five
LIVE_NOTE_FRESH_S = 180.0
DEPLOY = ("cd /opt/highlightz && git fetch origin && git reset --hard "
          "origin/claude/handoff-context-n4badl && systemctl restart highlightz")
# What the pause rules log, by name. Counted, never read further.
EVENTS = ("queue_full_pause", "streams_paused_queue_full", "streams_paused_idle",
          "streams_paused_hours", "watch_hours_pass", "watch_hours_error",
          "idle_stream_reaper_pausing", "streams_resumed_on_return", "idle_pause_lifted_paid_plan",
          "reaper_subscription_lapsed", "reaper_trial_expired", "pause_stream_failed",
          "resume_stream_failed", "queue_pause_notice_failed",
          "idle_stream_reaper_error", "queue_full_watch_error")
BROKEN = ("idle_stream_reaper_error", "queue_full_watch_error", "pause_stream_failed",
          "watch_hours_error")


# ── the running site ────────────────────────────────────────────────────────

def site_started(proc: pathlib.Path = pathlib.Path("/proc")) -> float | None:
    """When the web process (`python -m src.main`) started, as a Unix time.
    None when it is not running or cannot be seen."""
    try:
        boot = next(float(line.split()[1]) for line in (proc / "stat").read_text().splitlines()
                    if line.startswith("btime "))
        ticks = float(os.sysconf("SC_CLK_TCK"))
    except (OSError, StopIteration, ValueError, IndexError):
        return None
    best = None
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
            if "src.main" not in argv or "-m" not in argv:
                continue
            # Field 22, counted after the name in brackets (which may hold spaces).
            after = (p / "stat").read_text().rsplit(")", 1)[1].split()
            started = boot + float(after[19]) / ticks
        except (OSError, ValueError, IndexError):
            continue
        best = started if best is None else min(best, started)
    return best


def code_changed_at(root: pathlib.Path = ROOT) -> float:
    """When the files the rules live in were last written on disk."""
    out = 0.0
    for rel in ("src/dashboard/api.py", "src/main.py"):
        try:
            out = max(out, (root / rel).stat().st_mtime)
        except OSError:
            pass
    return out


def rule_on_disk(root: pathlib.Path = ROOT) -> bool:
    """Is the 15-minute rule in the code in this folder?"""
    try:
        return "queue_full_watch" in (root / "src" / "main.py").read_text()
    except OSError:
        return False


# ── the accounts ────────────────────────────────────────────────────────────

def queues(clips) -> dict:
    """{account: (clips waiting in its review queue, when the newest landed)}.
    Highlight clips are not counted, as in the site's own count."""
    out: dict = {}
    for c in clips if isinstance(clips, list) else []:
        if not isinstance(c, dict) or c.get("status") != "pending" or c.get("suggested"):
            continue
        uid = c.get("user_id")
        if not uid:
            continue
        try:
            at = float(c.get("created_at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        n, newest = out.get(uid, (0, 0.0))
        out[uid] = (n + 1, max(newest, at))
    return out


def last_decisions(lines) -> dict:
    """{account: when it last accepted, rejected or took back a clip}, from
    the ledger's rows."""
    out: dict = {}
    for raw in lines:
        if '"approved"' not in raw and '"rejected"' not in raw and '"undone"' not in raw:
            continue
        try:
            d = json.loads(raw)
            uid, ts = d.get("user_id"), float(d.get("ts") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if uid and d.get("event") in ("approved", "rejected", "undone") and ts > out.get(uid, 0.0):
            out[uid] = ts
    return out


def tail_lines(path: pathlib.Path, max_bytes: int = 8_000_000) -> list[str]:
    """The end of a file that is only ever added to: the recent rows."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines


def classify(users, streams, clips, activity, decisions, now: float, started: float | None,
             live_keys=()) -> list[dict]:
    """One row for every account that has a channel and whose plan pauses
    (Free). `kind` is where the rules put it, or the rule it is late for."""
    from src.billing import plans
    by_uid: dict = collections.defaultdict(list)
    for rec in streams if isinstance(streams, list) else []:
        if isinstance(rec, dict) and rec.get("user_id") and rec.get("channel"):
            by_uid[str(rec["user_id"])].append(rec)
    room = queues(clips)
    live = collections.Counter(str(k).split(":", 1)[0] for k in live_keys if ":" in str(k))
    known = {str(u["id"]): u for u in users if isinstance(u, dict) and u.get("id")}
    rows = []
    for uid, recs in by_uid.items():
        user = known.get(uid)
        if user is None or not plans.pauses_when_idle(user):
            continue
        cap = plans.limits_for(user)["max_pending"]
        used, newest = room.get(uid, (0, 0.0))
        watching = [r for r in recs if r.get("status") != "paused"]
        holds = {str(r.get("hold") or "") for r in recs if r.get("status") == "paused"}
        full = cap > 0 and used >= cap
        added = max((float(r.get("added_at") or 0) for r in recs), default=0.0)
        try:
            seen = float(activity.get(uid, added) or added)
        except (TypeError, ValueError, AttributeError):
            seen = added
        row = {"uid": uid, "name": str(user.get("username") or "?")[:40], "channels": len(recs),
               "watching": len(watching), "used": used, "cap": cap, "live": live.get(uid, 0),
               "full": full, "late_s": 0.0}
        if not watching:
            # "hours" first: it is the one a visit does NOT lift, so an
            # account held for both is out of hours until it has some.
            row["kind"] = ("hours" if "hours" in holds else
                           "held" if "queue_full" in holds else
                           "billing" if holds == {"billing"} else "idle")
            row["room"] = not full
        elif full:
            since = max(newest, decisions.get(uid, 0.0), started or 0.0)
            over = now - since - FULL_FOR
            row["kind"] = "LATE_QUEUE" if over > QUEUE_SLACK else "inside"
            row["late_s"] = max(0.0, over)
        else:
            over = now - seen - IDLE_FOR
            row["kind"] = "LATE_IDLE" if over > IDLE_SLACK else "room"
            row["late_s"] = max(0.0, over)
            row["away_s"] = max(0.0, now - seen)
        rows.append(row)
    return rows


# ── what the rules logged ───────────────────────────────────────────────────

def logged(since_s: float, unit: str = UNIT, cap_s: float = 3 * 3600) -> tuple[collections.Counter, bool, float]:
    """(how many times each pause-rule event was logged, could the journal be
    read, how many seconds back it looked). Lines are streamed and only the
    ones naming one of EVENTS are parsed, and only for their name."""
    span = max(60.0, min(float(since_s), cap_s))
    got: collections.Counter = collections.Counter()
    try:
        proc = subprocess.Popen(["journalctl", "-u", unit, "--since", f"-{int(span)}s", "-o", "cat", "--no-pager"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
    except FileNotFoundError:
        return got, False, span
    seen_any = False
    try:
        for raw in proc.stdout:
            seen_any = True
            if '"event"' not in raw:
                continue
            for name in EVENTS:
                if f'"{name}"' in raw:
                    got[name] += 1
                    break
    finally:
        proc.stdout.close()
        code = proc.wait()
    return got, bool(code == 0 and seen_any), span


def _ago(s: float) -> str:
    s = max(0.0, s)
    if s < 5400:
        return f"{s / 60:.0f} min"
    if s < 2 * 86400:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.0f} days"


def report(rows: list[dict], *, now: float, started: float | None, changed: float, on_disk: bool,
           events: collections.Counter | None = None, journal: bool = False, span: float = 0.0,
           live_total: int | None = None) -> list[str]:
    kinds = collections.Counter(r["kind"] for r in rows)
    stale = started is not None and changed > started + 5
    out = ["=" * 70, "THE SITE THAT IS RUNNING", "=" * 70]
    if started is None:
        out.append("  the site's process       : NOT FOUND   <- is the site up? (systemctl status highlightz)")
    else:
        out.append(f"  started                  : {_ago(now - started)} ago")
        out.append(f"  code on disk last changed: {_ago(now - changed)} ago"
                   + ("   <- AFTER the site started: the running site is OLDER than the files" if stale else ""))
    out.append("  the 15-minute rule       : " + ("in the code on disk" if on_disk else "NOT in the code on disk")
               + ("" if not on_disk or started is None else
                  ", NOT in the running site" if stale else ", and the site was started with it"))

    late = [r for r in rows if r["kind"].startswith("LATE")]
    out += ["", "=" * 70, f"FREE ACCOUNTS WITH A CHANNEL: {len(rows):,}", "=" * 70]
    out.append(f"  paused, review queue full         : {kinds['held']:>5,}"
               + (f"   ({sum(1 for r in rows if r['kind'] == 'held' and r.get('room'))} have room again and restart on their next visit)"
                  if kinds["held"] else ""))
    out.append(f"  paused, {IDLE_WORDS} without a visit   : {kinds['idle']:>5,}")
    if kinds["billing"]:
        out.append(f"  paused, card failing              : {kinds['billing']:>5,}")
    if kinds["hours"]:
        out.append(f"  paused, out of watching hours     : {kinds['hours']:>5,}   (start again Monday 00:00 UTC, or when they buy hours)")
    out.append(f"  watched, queue has room           : {kinds['room']:>5,}   <- the plan: Free is watched until its queue is full")
    out.append(f"  watched, full, inside its 15 min  : {kinds['inside']:>5,}")
    out.append(f"  SHOULD BE PAUSED AND IS NOT       : {len(late):>5,}")
    roomy = [r for r in rows if r["kind"] == "room"]
    if roomy:
        gone = sorted(r.get("away_s", 0.0) for r in roomy)
        hour = sum(1 for s in gone if s >= 3600)
        out.append(f"  Of the {len(roomy):,} watched with room: {hour:,} have not made a request in over an hour")
        out.append(f"  (the longest {_ago(gone[-1])}); they are watched until the queue fills or {IDLE_WORDS} pass.")
        out.append(f"  Their queues hold {sum(r['used'] for r in roomy):,} clips of the "
                   f"{sum(r['cap'] for r in roomy):,} they could; "
                   f"{sum(1 for r in roomy if r['used'] == 0):,} of them hold none.")
    if live_total is not None:
        mine = sum(r["live"] for r in rows)
        out.append(f"  Live right now: {mine:,} of the site's {live_total:,} live channels are Free accounts'.")

    if late:
        out += ["", "=" * 70, "SHOULD BE PAUSED AND IS NOT  (worst first)", "=" * 70]
        for r in sorted(late, key=lambda r: -r["late_s"])[:15]:
            why = (f"queue full ({r['used']}/{r['cap']}), nothing decided" if r["kind"] == "LATE_QUEUE"
                   else f"no visit in over {IDLE_WORDS}")
            out.append(f"  {r['name']:<28} {why:<40} {_ago(r['late_s'])} late"
                       + ("   live now" if r["live"] else ""))
        if len(late) > 15:
            out.append(f"  ... and {len(late) - 15:,} more")

    if events is not None:
        out += ["", "=" * 70, f"WHAT THE PAUSE RULES LOGGED  (the last {_ago(span)}; names and counts only)", "=" * 70]
        if not journal:
            out.append("  the journal could not be read from here (run this as root on the server)")
        elif not events:
            out.append("  nothing: no pause, no restart, no error from either rule in that time")
        else:
            for name in EVENTS:
                if events.get(name):
                    out.append(f"  {events[name]:>6,}  {name}" + ("   <- AN ERROR" if name in BROKEN else ""))

    out.append("")
    broken = sum((events or {}).get(n, 0) for n in BROKEN)
    late_queue = [r for r in late if r["kind"] == "LATE_QUEUE"]
    if started is None:
        out += ["  THE SITE'S PROCESS WAS NOT FOUND, so nothing is being paused or watched.",
                "  systemctl status highlightz"]
    elif stale and on_disk:
        out += ["  THE RUNNING SITE IS OLDER THAN THE CODE ON DISK. The files were updated",
                "  (a `git reset`) and the site was not restarted, so the 15-minute rule and",
                "  \"stays paused until there is room\" are in the files and NOT running. That is",
                f"  why {len(late_queue):,} full account(s) above are still watched." if late_queue else
                "  Nothing above is late yet, but nothing new is being enforced either.",
                "  Restart it with the deploy command:",
                "  " + DEPLOY]
    elif not on_disk:
        out += ["  THE 15-MINUTE RULE IS NOT IN THE CODE IN THIS FOLDER. Deploy:", "  " + DEPLOY]
    elif broken:
        out += [f"  A PAUSE RULE LOGGED {broken:,} ERROR(S), named above. Send this output."]
    elif late:
        out += [f"  {len(late):,} ACCOUNT(S) SHOULD HAVE BEEN PAUSED AND WERE NOT, on a site running the",
                "  current code. That is a fault. Send this output."]
    elif now - started < FULL_FOR + QUEUE_SLACK and kinds["inside"]:
        # Only worth saying when somebody is full and still watched: they are
        # inside the 15 minutes everybody gets after a restart.
        out += [f"  THE SITE STARTED {_ago(now - started)} AGO, and {kinds['inside']:,} full account(s) are still watched.",
                "  Nobody with a tab open is paused for a full queue in the first 15 minutes",
                "  after a restart (so a deploy cannot pause somebody in the middle of their",
                "  queue). Run this again after that; nothing is late yet."]
    else:
        out += ["  EVERY FREE ACCOUNT IS WHERE THE RULES PUT IT: nothing that should be paused",
                "  is being watched."]
        if kinds["room"]:
            out += [f"  The {kinds['room']:,} still watched have ROOM in their queue. No rule stops those: Free",
                    f"  is watched until its queue is full, or {IDLE_WORDS} pass without a visit. If those",
                    "  are the ones you expected to be gone, that is a new rule to decide, not a",
                    "  broken one."]
    return out


def main(argv: list[str]) -> int:
    from config.settings import settings
    store = pathlib.Path(settings.local_storage_path)
    if not store.is_absolute():
        store = ROOT / store

    def load(name, default):
        try:
            return json.loads((store / name).read_text())
        except (OSError, ValueError):
            return default
    users, streams = load("users.json", None), load("streams.json", None)
    if users is None or streams is None:
        print("The accounts or the channels could not be read. Run this as root, in /opt/highlightz.")
        return 1
    now = time.time()
    started = site_started()
    note = load("headroom.json", {})
    fresh = isinstance(note, dict) and now - float(note.get("at") or 0) <= LIVE_NOTE_FRESH_S
    keys = (note.get("keys") or []) if fresh else []
    rows = classify(users, streams, load("clips.json", []), load("user_activity.json", {}),
                    last_decisions(tail_lines(store / "stream_stats.jsonl")), now, started, keys)
    events, journal, span = logged(now - started if started else 3600.0)
    for line in report(rows, now=now, started=started, changed=code_changed_at(), on_disk=rule_on_disk(),
                       events=events, journal=journal, span=span, live_total=len(keys) if fresh else None):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
