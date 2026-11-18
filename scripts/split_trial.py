"""Start, widen, stop or look at the split on production, with nothing to edit.

    cd /opt/highlightz && venv/bin/python scripts/split_trial.py
    cd /opt/highlightz && venv/bin/python scripts/split_trial.py start <channel>
    cd /opt/highlightz && venv/bin/python scripts/split_trial.py all
    cd /opt/highlightz && venv/bin/python scripts/split_trial.py stop

WHY THIS EXISTS (2026-11-18). The trial used to be started with a pasted
command that had the word CHANNEL in it, to be replaced by hand. It was
pasted as written, so production was restarted with INGEST_ONLY=CHANNEL. A
command that needs editing gets pasted unedited. So:

  * with no arguments it CHANGES NOTHING: it says where the split stands and
    lists the channels a trial could use, each with the exact command to
    start a trial on it, already filled in;
  * `start <channel>` REFUSES a name nobody has added (CHANNEL included), so
    a slip cannot reach .env. Given a real one it writes the two settings and
    restarts the site;
  * `all` moves EVERY channel into worker processes (owner, 2026-11-18: "I
    need all the cores carrying the load"). It REFUSES unless the trial that
    is running has been seen to work: every worker there, none died, none
    loaded what it must not, and a channel in a worker found live, receiving
    audio, recording video and with a clip cut. It reads that from the same
    places split_check.py does, so "clean" has one definition. Refused, it
    says which of those is missing and NOTHING IS CHANGED;
  * `stop` takes the two settings out and restarts the site. It is the way
    back from a trial and from `all` alike.

THE SPLIT is in docs/HANDOFF.md ("THE SPLIT"). A trial is INGEST_WORKERS=1
with INGEST_ONLY=<channel>: only that channel is watched in a worker process,
every other channel in the one process as always. Everything is
INGEST_WORKERS=<n> with no INGEST_ONLY.

`all --errors-read`: the trial's worker logged errors, somebody has read
split_check.py's list of them and they are ordinary (a channel's feed
dropping is logged as one). Nothing prints this form: it is handed over,
finished, by whoever read the list.

WHAT IT TOUCHES. .env: only the INGEST_WORKERS and INGEST_ONLY lines, written
into the same file so its owner and permissions stay as they are (the service
cannot start if they change). And `systemctl restart highlightz`.

WHAT IT PRINTS. Channel names, their platform and status, and whether one is
on an admin account. Never an account's name, an address or any other
setting from .env.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNIT = "highlightz"
KEYS = ("INGEST_WORKERS", "INGEST_ONLY")
HERE = "cd /opt/highlightz && venv/bin/python scripts/"
# How far back `all` looks for what the trial's channel did in a worker.
GATE_MINUTES = 60
# The live-now note is rewritten every 30 seconds while the site is up.
LIVE_NOTE_FRESH_S = 180.0


# ── .env: two lines, and nothing else is read or kept ────────────────────────

def read_split(text: str) -> tuple[int, list[str]]:
    """(INGEST_WORKERS, the names in INGEST_ONLY) as .env has them. The last
    line for a key wins, as it does for the site."""
    workers, only = 0, []
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or key.strip() not in KEYS:
            continue
        value = value.strip().strip("'\"")
        if key.strip() == "INGEST_WORKERS":
            try:
                workers = max(0, int(value))
            except ValueError:
                workers = 0
        else:
            only = [n.strip().lower() for n in value.split(",") if n.strip()]
    return workers, only


def without_split(text: str) -> str:
    """The same file with the two settings taken out. Every other line is kept
    exactly, in order."""
    keep = [ln for ln in text.splitlines(keepends=True)
            if ln.strip().partition("=")[0].strip() not in KEYS or "=" not in ln]
    return "".join(keep)


def with_trial(text: str, names: list[str]) -> str:
    """The same file set up for a trial on `names`. A file whose last line
    has no line ending gets one first, so nothing is glued onto it."""
    base = without_split(text)
    if base and not base.endswith("\n"):
        base += "\n"
    return base + f"INGEST_WORKERS=1\nINGEST_ONLY={','.join(names)}\n"


def with_everything(text: str, count: int) -> str:
    """The same file with EVERY channel in `count` worker processes: the
    worker count, and no INGEST_ONLY (a name left there would keep it a
    trial)."""
    base = without_split(text)
    if base and not base.endswith("\n"):
        base += "\n"
    return base + f"INGEST_WORKERS={int(count)}\n"


def workers_for(cores: int | None) -> int:
    """How many worker processes carry the whole site: HALF THE CORES, at
    least 2 and at most 8.

    Half, not all: every live channel also runs a streamlink and an ffmpeg
    process, and those are already spread over the cores by the kernel, as is
    the clip processor. What was pinned to ONE core is the app's loop (chat,
    scoring, the audio level, the cut). A worker is one such loop, so the
    other half is left for the processes that were never the problem. On the
    8-core server that is 4: at about 0.7% of a loop a channel, 120 live is
    about a fifth of each (docs/HANDOFF.md, "THE SPLIT", stage 5)."""
    try:
        cores = int(cores or 0)
    except (TypeError, ValueError):
        cores = 0
    return max(2, min(8, cores // 2))


def rewrite(path: pathlib.Path, text: str) -> None:
    """Write INTO the file that is there: same inode, so the same owner,
    group and mode (640 root:highlightz on production; the service reads it
    as its own user and cannot start if that is lost)."""
    with open(path, "r+", encoding="utf-8") as f:
        f.seek(0)
        f.write(text)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())


# ── which channels there are ────────────────────────────────────────────────

def listed(store: pathlib.Path) -> dict | None:
    """{channel: {"platform", "statuses": [...], "accounts": n, "admin": bool}}
    from streams.json and users.json. None if streams.json cannot be read."""
    try:
        records = json.loads((store / "streams.json").read_text())
    except (OSError, ValueError):
        return None
    admins = set()
    try:
        for u in json.loads((store / "users.json").read_text()):
            if isinstance(u, dict) and u.get("is_admin") and u.get("id"):
                admins.add(str(u["id"]))
    except (OSError, ValueError):
        pass
    out: dict = {}
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("channel") or "").strip().lower()
        if not name:
            continue
        row = out.setdefault(name, {"platform": str(rec.get("platform") or "twitch")[:12],
                                    "statuses": [], "accounts": 0, "admin": False})
        row["accounts"] += 1
        row["statuses"].append(str(rec.get("status") or "?")[:20])
        if str(rec.get("user_id") or "") in admins:
            row["admin"] = True
    return out


def live_names(store: pathlib.Path, now: float | None = None) -> set | None:
    """The channels that are live RIGHT NOW, from the note the site writes
    every 30 seconds of who holds a live slot (headroom.json). None when
    there is no fresh note to read: then the saved statuses are all there is.

    streams.json is not this: its statuses are only as fresh as the last time
    a channel was added, removed or paused, and a trial started on a channel
    that only LOOKED live waits for something that is not happening."""
    now = time.time() if now is None else now
    try:
        got = json.loads((store / "headroom.json").read_text())
        keys, at = got.get("keys") or [], float(got.get("at") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if now - at > LIVE_NOTE_FRESH_S:
        return None
    return {str(k).partition(":")[2].strip().lower() if ":" in str(k) else str(k).strip().lower()
            for k in keys} - {""}


def _is_live(name: str, row: dict, live: set | None) -> bool:
    return (name in live) if live is not None else ("live" in row["statuses"])


def candidates(channels: dict, top: int = 8, live: set | None = None) -> list[tuple[str, dict]]:
    """The channels a trial could use, best first. LIVE NOW comes first: a
    trial shows nothing until its channel is live, and a channel that is
    offline may stay so for hours. Among the live, one on an admin account
    (its buttons can be pressed by whoever is running the trial), then listed
    by the fewest accounts (the fewest people affected if the trial goes
    wrong). A channel that is only paused is left out: a paused channel is
    not watched at all. `live`: live_names(), or None to go by the saved
    statuses."""
    rows = [(n, r) for n, r in channels.items() if any(s != "paused" for s in r["statuses"])]
    rows.sort(key=lambda kv: (not _is_live(kv[0], kv[1], live), not kv[1]["admin"],
                              kv[1]["accounts"], kv[0]))
    return rows[:top]


def best_pair(rows: list, live: set | None) -> list[str]:
    """Two live channels to try together, or [] when there are not two: the
    best one, and the best one on the OTHER platform if there is one (a
    Twitch and a Kick channel between them use every path a worker has),
    else the next best. Two channels are twice as likely to have a clip
    caught soon, which is the last thing a trial waits for."""
    up = [(n, r) for n, r in rows if _is_live(n, r, live)]
    if len(up) < 2:
        return []
    first = up[0]
    other = next((x for x in up[1:] if x[1]["platform"] != first[1]["platform"]), up[1])
    return [first[0], other[0]]


def _status(row: dict, name: str = "", live: set | None = None) -> str:
    if live is not None:
        return "live now" if name in live else "not live"
    return "live" if "live" in row["statuses"] else sorted(set(row["statuses"]))[0]


# ── the three things it does ─────────────────────────────────────────────────

def _restart() -> tuple[bool, str]:
    try:
        got = subprocess.run(["systemctl", "restart", UNIT], capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return False, "systemctl is not on this machine, so the site was NOT restarted"
    except subprocess.TimeoutExpired:
        return False, "the restart did not finish in two minutes; look at: systemctl status highlightz"
    if got.returncode != 0:
        return False, "the restart did not work (it needs root)"
    return True, "the site was restarted"


def _service() -> str:
    try:
        got = subprocess.run(["systemctl", "is-active", UNIT], capture_output=True, text=True, timeout=20)
        return (got.stdout.strip() or "unknown")[:20]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown (no systemctl here)"


def _commit() -> str:
    try:
        got = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        return got.stdout.strip()[:12] or "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def _workers_found() -> int:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("stream_cost", ROOT / "scripts" / "stream_cost.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return sum(1 for row in mod.procs().values() if row[0] == "worker")
    except Exception:
        return 0


def show(env_path: pathlib.Path, store: pathlib.Path) -> int:
    try:
        workers, only = read_split(env_path.read_text())
        env_ok = True
    except OSError:
        workers, only, env_ok = 0, [], False
    channels = listed(store)
    found = _workers_found()

    print("=" * 70)
    print("THE SPLIT'S TRIAL: WHERE IT STANDS  (nothing was changed)")
    print("=" * 70)
    print(f"  code in this folder    : {_commit()}")
    print(f"  the site               : {_service()}")
    if not env_ok:
        print("  .env                   : could not be read from here (run this as root)")
    elif workers <= 0:
        print("  .env                   : the split is OFF"
              + ("   <- INGEST_ONLY is set but does nothing without INGEST_WORKERS" if only else ""))
    elif not only:
        print(f"  .env                   : INGEST_WORKERS={workers}, no INGEST_ONLY"
              "   <- EVERY channel is in a worker process")
    else:
        print(f"  .env                   : a trial, INGEST_WORKERS={workers}, on: {', '.join(only)}")
        unknown = [n for n in only if channels is not None and n not in channels]
        for n in unknown:
            print(f"    \"{n}\" IS NOT A CHANNEL ANYBODY HAS ADDED"
                  + (" (it is the placeholder from the example command)." if n == "channel" else "."))
        if unknown and len(unknown) == len(only):
            print("    So no channel is in a worker: every channel is being watched in the one")
            print("    process, as always, and one idle worker process is running beside it.")
            print("    Start a trial on a real channel below, or stop it:")
            print(f"      {HERE}split_trial.py stop")
    print(f"  worker processes found : {found}")

    if env_ok and workers > 0 and not only:
        print()
        print("  THE WHOLE SITE IS SPLIT. How it is doing, and what each loop is carrying:")
        print(f"    {HERE}split_check.py")
        print(f"    {HERE}stream_cost.py")
        print("  To go back to the one process (restarts the site once):")
        print(f"    {HERE}split_trial.py stop")
        return 0
    if env_ok and workers > 0 and only and channels is not None and any(n in channels for n in only):
        print()
        print("  A TRIAL IS RUNNING. Is its channel doing everything in a worker?")
        print(f"    {HERE}split_check.py")
        print("  When that reads \"CLEAN, AND A CHANNEL HAS DONE EVERYTHING\", move every")
        print(f"  channel into worker processes ({workers_for(os.cpu_count())} of them; restarts the site once).")
        print("  It checks the same things itself first and changes nothing if one is missing:")
        print(f"    {HERE}split_trial.py all")

    print()
    print("=" * 70)
    print("CHANNELS A TRIAL COULD USE")
    print("=" * 70)
    if channels is None:
        print("  streams.json could not be read from here (run this as root, in /opt/highlightz).")
        return 1
    live = live_names(store)
    rows = candidates(channels, live=live)
    if not rows:
        print("  Nobody has a channel listed that is not paused, so there is nothing to try it on.")
        return 0
    if not any(r["admin"] for _n, r in rows):
        print("  None of these is on an admin account. A trial is safest on a channel of")
        print("  your own: add one to your account, then run this again.")
        print()
    print("  Best first: LIVE NOW (a trial shows nothing until its channel is live), on an")
    print("  admin account (you can press its buttons), listed by the fewest accounts.")
    print("  Copy ONE of the commands as it is.")
    print("  STARTING A TRIAL RESTARTS THE SITE ONCE (as a deploy does): tabs reconnect by")
    print("  themselves and live channels take a moment to come back.")
    if live is None:
        print("  The status is as last saved: a channel added a moment ago reads \"starting\",")
        print("  and one saved as live may have ended since.")
    else:
        print(f"  \"live now\" is from the site's own note of who is live ({len(live)} channels).")
        if not any(_is_live(n, r, live) for n, r in rows):
            print("  NONE OF THESE IS LIVE RIGHT NOW. A trial on one of them waits for it to go live.")
    print()
    pair = best_pair(rows, live)
    if pair:
        print("  THE QUICKEST TRIAL: the two best live ones together (a clip has to be caught")
        print("  on one of them before the trial has shown everything, and two are quicker):")
        print(f"      {HERE}split_trial.py start {','.join(pair)}")
        print()
        print("  Or one channel:")
    for name, row in rows:
        who = "admin account" if row["admin"] else f"listed by {row['accounts']} account{'' if row['accounts'] == 1 else 's'}"
        print(f"  {name[:28]:<28} {row['platform']:<8} {_status(row, name, live):<12} {who}")
        print(f"      {HERE}split_trial.py start {name}")
    return 0


def start(names: list[str], env_path: pathlib.Path, store: pathlib.Path, restart=_restart) -> int:
    names = [n.strip().lower() for part in names for n in part.split(",") if n.strip()]
    channels = listed(store)
    if channels is None:
        print("streams.json could not be read from here, so the channel cannot be checked.")
        print("NOTHING WAS CHANGED. Run this as root, in /opt/highlightz.")
        return 1
    if not names:
        print("Which channel? NOTHING WAS CHANGED. Run this with no arguments to see the")
        print("channels a trial could use, each with its command:")
        print(f"  {HERE}split_trial.py")
        return 1
    unknown = [n for n in names if n not in channels]
    if unknown:
        for n in unknown:
            print(f"\"{n}\" is not a channel anybody has added"
                  + (": it is the placeholder from the example command." if n == "channel" else "."))
        print("NOTHING WAS CHANGED. Run this with no arguments to see the channels a trial")
        print("could use, each with its command:")
        print(f"  {HERE}split_trial.py")
        return 1
    try:
        rewrite(env_path, with_trial(env_path.read_text(), names))
    except OSError as exc:
        print(f".env could not be written ({exc.strerror}). NOTHING WAS CHANGED. Run this as root.")
        return 1
    print(f"  .env                   : INGEST_WORKERS=1, INGEST_ONLY={','.join(names)}")
    for n in names:
        row = channels[n]
        print(f"  {n[:22]:<22} : {row['platform']}, {_status(row)}"
              + ("" if row["accounts"] == 1 else f", listed by {row['accounts']} accounts")
              + ("   <- PAUSED for everybody who lists it: it will not be watched until one of them is back"
                 if all(s == "paused" for s in row["statuses"]) else ""))
    ok, said = restart()
    print(f"  restart                : {said}")
    if not ok:
        print("  The settings are written. Restart the site for them to take effect:")
        print(f"    systemctl restart {UNIT}")
        return 1
    print()
    print("  THE TRIAL IS ON. Only the channel above is in a worker process; every other")
    print("  channel is watched as always. While it is live, use its card as usual")
    print("  (approve, reject, undo, pause, resume). Then, and at any time:")
    print(f"    {HERE}split_check.py")
    print("  When that reads \"CLEAN, AND A CHANNEL HAS DONE EVERYTHING\", every channel can")
    print("  be moved into worker processes (it checks the same things first):")
    print(f"    {HERE}split_trial.py all")
    print("  To end the trial:")
    print(f"    {HERE}split_trial.py stop")
    return 0


# ── everything: every channel into worker processes ─────────────────────────

def _split_check():
    import importlib.util
    spec = importlib.util.spec_from_file_location("split_check", ROOT / "scripts" / "split_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def evidence(minutes: int = GATE_MINUTES) -> dict:
    """What the trial has been seen doing, read exactly as split_check.py
    reads it: the worker processes in /proc and the service's journal."""
    check = _split_check()
    lines, readable, source = check.read_lines(minutes, None)
    summary = check.summarize(lines)
    return {"found": len(check.workers_running()), "readable": readable, "source": source,
            "summary": summary, "milestones": check.milestones(summary)}


def not_ready(*, workers: int, only: list, channels: dict | None, seen: dict,
              errors_read: bool = False) -> list[str]:
    """Why every channel may NOT be moved into workers yet, in words; empty
    when it may. The worst thing first, and only one of them.

    THE RULE (docs/HANDOFF.md, "THE SPLIT", stage 5): everything only after a
    trial on a real channel has been seen to work. Nothing but a real stream
    in a worker process shows that a real stream works in a worker process,
    and moved all at once a fault stops every account's clips together."""
    check = f"  {HERE}split_check.py"
    if workers <= 0 or not only:
        return ["NO TRIAL IS RUNNING, so nothing has shown that a real channel works in a",
                "worker process. Start one on a channel that is live now; the channels a",
                "trial could use, each with its command:",
                f"  {HERE}split_trial.py"]
    if channels is not None and not any(n in channels for n in only):
        return ["THE TRIAL HAS NOT STARTED: it names "
                + (f"\"{only[0]}\", which nobody has added." if len(only) == 1
                   else f"{len(only)} channels nobody has added."),
                "No channel is in a worker. The channels a trial could use, each with its",
                "command:",
                f"  {HERE}split_trial.py"]
    if seen["found"] < workers:
        return [f"A WORKER IS MISSING: .env asks for {workers} and {seen['found']} "
                f"{'is' if seen['found'] == 1 else 'are'} running.", "See:", check]
    summary = seen["summary"]
    if not seen["readable"] or not summary["lines"]:
        return ["WHAT THE TRIAL'S WORKER HAS DONE COULD NOT BE READ (the journal is closed to",
                "this user, or had no lines). Run this as root on the server."]
    if summary["forbidden"]:
        return [f"A WORKER TRIED TO LOAD SOMETHING IT MUST NOT ({summary['forbidden']} line(s)): code that",
                "works in one process is failing in a worker. Send the output of:", check]
    if summary["lost"]:
        return [f"A WORKER DIED {summary['lost']} TIME(S) in the last {GATE_MINUTES} minutes. That needs a look",
                "before every channel depends on one. Send the output of:", check]
    missing = [words for words, n in seen["milestones"] if not n]
    if missing:
        return ["THE TRIAL'S CHANNEL HAS NOT YET DONE EVERYTHING IN A WORKER. Still to be seen:"] + [
                f"  - {words}" for words in missing] + [
                "It has to be live for these, and a clip has to be caught on it. Leave it",
                "running through a live stream, then look again:", check]
    errors = sum(sum(w["errors"].values()) for w in summary["workers"].values())
    if errors and not errors_read:
        return [f"THE TRIAL'S WORKER LOGGED {errors} ERROR(S). Some are ordinary (a channel's feed",
                "dropping is logged as one), and some are not. They are named by:", check,
                "Send that output before going further."]
    return []


def everything(env_path: pathlib.Path, store: pathlib.Path, restart=_restart, seen=None,
               errors_read: bool = False, count: int | None = None) -> int:
    try:
        text = env_path.read_text()
    except OSError as exc:
        print(f".env could not be read ({exc.strerror}). NOTHING WAS CHANGED. Run this as root.")
        return 1
    workers, only = read_split(text)
    if workers > 0 and not only:
        print(f"  EVERY CHANNEL IS ALREADY IN A WORKER PROCESS (INGEST_WORKERS={workers}).")
        print("  NOTHING WAS CHANGED, and the site was not restarted. How it is doing:")
        print(f"    {HERE}split_check.py")
        return 0
    channels = listed(store)
    if seen is None:
        try:
            seen = evidence()
        except Exception as exc:                       # the check could not even run
            print(f"The trial could not be checked ({type(exc).__name__}). NOTHING WAS CHANGED.")
            return 1
    why = not_ready(workers=workers, only=only, channels=channels, seen=seen, errors_read=errors_read)
    if why:
        for line in why:
            print(line)
        print("NOTHING WAS CHANGED.")
        return 1
    count = workers_for(os.cpu_count()) if count is None else int(count)
    try:
        rewrite(env_path, with_everything(text, count))
    except OSError as exc:
        print(f".env could not be written ({exc.strerror}). NOTHING WAS CHANGED. Run this as root.")
        return 1
    print("  the trial              : every worker there, none died, none loaded what it must not;")
    print("                           a channel in a worker was found live, received audio,")
    print("                           recorded video and had a clip cut")
    print(f"  .env                   : INGEST_WORKERS={count}, INGEST_ONLY removed")
    ok, said = restart()
    print(f"  restart                : {said}")
    if not ok:
        print("  The setting is written. Restart the site for it to take effect:")
        print(f"    systemctl restart {UNIT}")
        return 1
    print()
    print(f"  EVERY CHANNEL IS NOW WATCHED IN ONE OF {count} WORKER PROCESSES. Channels take a")
    print("  minute or two to come back, as after a deploy. In about ten minutes:")
    print(f"    {HERE}split_check.py")
    print("  and for what each of the loops is carrying:")
    print(f"    {HERE}stream_cost.py")
    print("  To go back to the one process (restarts the site once):")
    print(f"    {HERE}split_trial.py stop")
    return 0


def stop(env_path: pathlib.Path, restart=_restart) -> int:
    try:
        text = env_path.read_text()
        workers, only = read_split(text)
        if workers <= 0 and not only and without_split(text) == text:
            print("  The split was already off: .env has neither setting. NOTHING WAS CHANGED,")
            print("  and the site was not restarted.")
            return 0
        rewrite(env_path, without_split(text))
    except OSError as exc:
        print(f".env could not be written ({exc.strerror}). NOTHING WAS CHANGED. Run this as root.")
        return 1
    print("  .env                   : INGEST_WORKERS and INGEST_ONLY removed")
    ok, said = restart()
    print(f"  restart                : {said}")
    if not ok:
        print(f"  Restart the site for it to take effect:  systemctl restart {UNIT}")
        return 1
    print()
    print("  THE SPLIT IS OFF. Every channel is watched in the one process, as before.")
    print("  Channels take a minute or two to come back, as after a deploy.")
    return 0


def main(argv: list[str], env_path: pathlib.Path | None = None,
         store: pathlib.Path | None = None, restart=_restart, seen=None) -> int:
    env_path = env_path or ROOT / ".env"
    if store is None:
        from config.settings import settings
        store = pathlib.Path(settings.local_storage_path)
        if not store.is_absolute():
            store = ROOT / store
    if not argv:
        return show(env_path, store)
    if argv[0] == "start":
        return start(argv[1:], env_path, store, restart)
    if argv[0] == "stop" and len(argv) == 1:
        return stop(env_path, restart)
    if argv[0] == "all" and all(a == "--errors-read" for a in argv[1:]):
        return everything(env_path, store, restart, seen=seen, errors_read="--errors-read" in argv)
    print("Run it with no arguments to look, `start <channel>` to start a trial, `all` to")
    print("move every channel once the trial is clean, or `stop`.")
    print("NOTHING WAS CHANGED.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
