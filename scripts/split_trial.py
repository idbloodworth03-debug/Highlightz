"""Start, stop or look at the split's trial on production, with nothing to edit.

    cd /opt/highlightz && venv/bin/python scripts/split_trial.py
    cd /opt/highlightz && venv/bin/python scripts/split_trial.py start <channel>
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
  * `stop` takes the two settings out and restarts the site.

THE SPLIT is in docs/HANDOFF.md ("THE SPLIT"). A trial is INGEST_WORKERS=1
with INGEST_ONLY=<channel>: only that channel is watched in a worker process,
every other channel in the one process as always.

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

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNIT = "highlightz"
KEYS = ("INGEST_WORKERS", "INGEST_ONLY")
HERE = "cd /opt/highlightz && venv/bin/python scripts/"


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


def candidates(channels: dict, top: int = 8) -> list[tuple[str, dict]]:
    """The channels a trial could use, best first: on an admin account (its
    buttons can be pressed by whoever is running the trial), live now, and
    listed by the fewest accounts (the fewest people affected if the trial
    goes wrong). A channel that is only paused is left out: a paused channel
    is not watched at all."""
    rows = [(n, r) for n, r in channels.items() if any(s != "paused" for s in r["statuses"])]
    rows.sort(key=lambda kv: (not kv[1]["admin"], "live" not in kv[1]["statuses"],
                              kv[1]["accounts"], kv[0]))
    return rows[:top]


def _status(row: dict) -> str:
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
              "   <- EVERY channel is in a worker. This is not a trial.")
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

    print()
    print("=" * 70)
    print("CHANNELS A TRIAL COULD USE")
    print("=" * 70)
    if channels is None:
        print("  streams.json could not be read from here (run this as root, in /opt/highlightz).")
        return 1
    rows = candidates(channels)
    if not rows:
        print("  Nobody has a channel listed that is not paused, so there is nothing to try it on.")
        return 0
    if not any(r["admin"] for _n, r in rows):
        print("  None of these is on an admin account. A trial is safest on a channel of")
        print("  your own: add one to your account, then run this again.")
        print()
    print("  Best first: on an admin account (you can press its buttons), live now,")
    print("  listed by the fewest accounts. Copy ONE of the commands as it is.")
    print()
    for name, row in rows:
        who = "admin account" if row["admin"] else f"listed by {row['accounts']} account{'' if row['accounts'] == 1 else 's'}"
        print(f"  {name[:28]:<28} {row['platform']:<8} {_status(row):<12} {who}")
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
    print("  To end the trial:")
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
    return 0


def main(argv: list[str], env_path: pathlib.Path | None = None,
         store: pathlib.Path | None = None, restart=_restart) -> int:
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
    print("Run it with no arguments to look, `start <channel>` to start a trial, or `stop`.")
    print("NOTHING WAS CHANGED.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
