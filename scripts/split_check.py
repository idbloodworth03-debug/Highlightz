"""Is the split healthy? One read-only look at it, safe to paste.

    cd /opt/highlightz && venv/bin/python scripts/split_check.py
    cd /opt/highlightz && venv/bin/python scripts/split_check.py --minutes=120

THE SPLIT (docs/HANDOFF.md, "THE SPLIT"): with INGEST_WORKERS set, channels
are watched in worker processes instead of in the one web process. This is
the check to run after turning it on, first for the trial on one channel
(INGEST_ONLY) and later for everything. It answers, in order:

  1. Is it on, and is every worker process there?
  2. Which channels are in a worker, and what do they say they are doing?
  3. What have the workers logged? Has one died? Has one tried to load the
     dashboard (the one thing a worker must never do)?
  4. Has a channel in a worker done the things the trial is for: been found
     live, received audio, recorded video, had a clip cut?

Then one verdict: clean so far, or the line to act on.

Read-only. It reads .env (two settings), /proc, streams.json and the
service's journal. It prints EVENT NAMES AND COUNTS, never a field of a log
line: no account, no address, no token, no command line. Channel names are
printed only for the channels of a trial, which the owner chose by name.

`--log=<file>` reads that file instead of the journal (a machine without
systemd, or a saved log).
"""
import collections
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNIT = "highlightz"

# What a channel in a worker has to be seen doing before the trial has shown
# anything. Each is the event the stream worker logs when it happens.
MILESTONES = (("stream_found", "found its channel live"),
              ("audio_meter_receiving_audio", "is receiving the channel's audio"),
              ("clip_recorder_pipeline_up", "is recording the rolling video"),
              ("clip_cut_ok", "cut a clip's video"))

# A worker that tried to load the dashboard, or failed an import at all. The
# first is refused loudly by worker_main; either means code that ran fine in
# one process does not run in a worker.
FORBIDDEN = ("must not be imported", "ImportError", "ModuleNotFoundError")

# The web process's own lines about its workers that are worth a row. The
# start-up lines are counted separately; ingest_split_on is a warning by
# design (it says the switch is on) and is not a problem.
QUIET = {"ingest_split_on", "ingest_worker_up", "ingest_worker_given_channels"}


def summarize(lines) -> dict:
    """Count what the lines say. Names and levels only: no other field of a
    line is kept, so nothing private can reach the output."""
    out = {"workers": {}, "web": collections.Counter(), "forbidden": 0, "lost": 0,
           "ups": 0, "lines": 0, "worker_lines": 0}
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict) or not isinstance(d.get("event"), str):
            continue
        out["lines"] += 1
        event, level = d["event"][:60], str(d.get("level") or "")
        index = d.get("ingest")
        if isinstance(index, int) and not isinstance(index, bool):
            out["worker_lines"] += 1
            w = out["workers"].setdefault(index, {"lines": 0, "events": collections.Counter(),
                                                  "errors": collections.Counter(),
                                                  "warnings": collections.Counter()})
            w["lines"] += 1
            w["events"][event] += 1
            if level in ("error", "critical"):
                w["errors"][event] += 1
            elif level == "warning":
                w["warnings"][event] += 1
            if any(mark in raw for mark in FORBIDDEN):
                out["forbidden"] += 1
            continue
        if event == "ingest_worker_lost":
            out["lost"] += 1
        if event == "ingest_worker_up":
            out["ups"] += 1
        if (event.startswith(("ingest_", "link_")) and event not in QUIET
                and level in ("warning", "error", "critical")):
            out["web"][event] += 1
    return out


def milestones(summary: dict) -> list[tuple[str, int]]:
    """(what, how many times any worker logged it), in the order they happen."""
    total = collections.Counter()
    for w in summary["workers"].values():
        total.update(w["events"])
    return [(words, total.get(event, 0)) for event, words in MILESTONES]


def verdict(*, asked: int, found: int, summary: dict, only: list, journal: bool,
            unlisted: list | None = None) -> list[str]:
    """What to do, in words. The worst thing first, and only one of them.

    `unlisted`: the names in INGEST_ONLY that nobody has added (None when
    streams.json could not be read)."""
    end = ["To turn the split off (every channel is then watched in the one process,",
           "as before):",
           "  cd /opt/highlightz && venv/bin/python scripts/split_trial.py stop"]
    if asked <= 0:
        if found:
            return [f"THE SWITCH IS OFF IN .env BUT {found} WORKER PROCESS(ES) ARE RUNNING. The",
                    "site was started before .env was changed. Restart it."]
        return ["THE SPLIT IS OFF. Nothing here to check: every channel is watched in the",
                "one process. To see the channels a trial could use, each with its command:",
                "  cd /opt/highlightz && venv/bin/python scripts/split_trial.py"]
    if found < asked:
        return [f"A WORKER IS MISSING. .env asks for {asked} and {found} "
                f"{'is' if found == 1 else 'are'} running. The site",
                "restarts a worker by itself within seconds, so look again; if it is",
                "still missing, the journal says why (ingest_worker_start_failed or",
                "ingest_worker_silent)."] + end
    if summary["forbidden"]:
        return [f"STOP: A WORKER TRIED TO LOAD SOMETHING IT MUST NOT ({summary['forbidden']} line(s)).",
                "Code that works in one process is failing in a worker."] + end + [
                "Then send this output; the lines themselves are in:",
                f"  journalctl -u {UNIT} --no-pager | grep -E 'must not be imported|ImportError' | tail -5"]
    if summary["lost"]:
        return [f"A WORKER DIED {summary['lost']} TIME(S) in this window and was started again.",
                "Its channels were interrupted for a few seconds each time. Once after a",
                "deploy is nothing; more than that needs a look before going further."] + end
    if only and unlisted is not None and len(unlisted) == len(only):
        # 2026-11-18: production sat like this (INGEST_ONLY=CHANNEL, the
        # placeholder) and this said "CLEAN SO FAR ... leave it running
        # through a live stream", so the owner waited for something that
        # could not happen. No channel in a worker is not a clean trial; it
        # is no trial.
        return ["THE TRIAL HAS NOT STARTED. INGEST_ONLY names "
                + (f"\"{only[0]}\", which nobody has added," if len(only) == 1
                   else f"{len(only)} channels nobody has added,"),
                "so no channel is in a worker and there is nothing to wait for: every",
                "channel is being watched in the one process, as always. For the channels",
                "a trial could use, each with the command that starts it:",
                "  cd /opt/highlightz && venv/bin/python scripts/split_trial.py"]
    errors = sum(sum(w["errors"].values()) for w in summary["workers"].values())
    if errors:
        return [f"THE WORKERS LOGGED {errors} ERROR(S), named above. Some are ordinary (a",
                "channel's feed dropping is logged as one); compare the names with what the",
                "site logs without the split before deciding. Nothing is broken by them",
                "alone."]
    if not journal or not summary["lines"]:
        # No lines at all is not "the workers were quiet": the web process
        # alone logs more than that. The journal was not readable from here.
        return ["THE PROCESSES ARE THERE, but nothing the site logged could be read (the",
                "journal is closed to this user, or had no lines in this window), so what",
                "the workers have been doing is unknown. Run this as root on the server."]
    if not summary["worker_lines"]:
        return ["THE WORKERS ARE RUNNING AND HAVE LOGGED NOTHING in this window. That is",
                "normal for a channel that is offline. Run this again while a channel",
                "in a worker is live."]
    done = [n for _w, n in milestones(summary)]
    if all(done):
        return ["CLEAN, AND A CHANNEL HAS DONE EVERYTHING THE TRIAL IS FOR IN A WORKER:",
                "found live, audio, video, a clip cut. No worker died, none logged an",
                "error, none loaded anything it must not."
                + ("" if only else " This is the whole site, not a trial.")]
    return ["CLEAN SO FAR: no worker died, none logged an error, none loaded anything it",
            "must not. Not everything the trial is for has happened yet (see the list",
            "above); leave it running through a live stream and run this again."]


def _stream_cost():
    spec = importlib.util.spec_from_file_location("stream_cost", ROOT / "scripts" / "stream_cost.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def workers_running() -> list[dict]:
    """The ingest worker processes on this machine: index, memory, and how
    many live channels each is capturing RIGHT NOW. From /proc, the same
    reading scripts/stream_cost.py takes: a live channel is its audio
    meter's streamlink (the recorder's where the meter is off), which is a
    child of the process watching it."""
    cost = _stream_cost()
    snap = cost.procs()
    out = {pid: {"index": row[5], "rss": row[1], "live": 0, "_rec": 0}
           for pid, row in snap.items() if row[0] == "worker"}
    for pid, row in snap.items():
        if row[0] in ("meter_streamlink", "recorder_streamlink"):
            owner = cost._watcher(pid, snap)            # noqa: SLF001
            if owner in out:
                out[owner]["live" if row[0] == "meter_streamlink" else "_rec"] += 1
    for v in out.values():
        v["live"] = v["live"] or v["_rec"]
        del v["_rec"]
    return sorted(out.values(), key=lambda v: v["index"])


def channels(store: pathlib.Path, count: int, only: list) -> dict:
    """What streams.json says of the channels that run in a worker.

    A trial: one row per named channel (name, how many accounts list it, the
    statuses). Everything: statuses counted per worker, no names."""
    from src.ingestion.bridge import owner_of
    try:
        records = json.loads((store / "streams.json").read_text())
    except (OSError, ValueError):
        return {"readable": False}
    named = {n.lower() for n in only}
    rows, per_worker = {}, {}
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("channel") or "").strip().lower()
        if not name or (named and name not in named):
            continue
        status = str(rec.get("status") or "?")[:20]
        index = owner_of(name, count)
        per_worker.setdefault(index, collections.Counter())[status] += 1
        if named:
            row = rows.setdefault(name, {"worker": index, "listed": 0, "statuses": collections.Counter()})
            row["listed"] += 1
            row["statuses"][status] += 1
    return {"readable": True, "rows": rows, "per_worker": per_worker,
            "missing": sorted(named - set(rows))}


def read_lines(minutes: int, log_file: str | None):
    """(lines, could they be read, where from)."""
    if log_file:
        try:
            return pathlib.Path(log_file).read_text(errors="replace").splitlines(), True, "the file given"
        except OSError as exc:
            return [], False, f"the file could not be read ({exc.strerror})"
    try:
        got = subprocess.run(["journalctl", "-u", UNIT, "--since", f"-{int(minutes)}min",
                              "-o", "cat", "--no-pager"],
                             capture_output=True, text=True, errors="replace", timeout=120)
    except FileNotFoundError:
        return [], False, "journalctl is not on this machine"
    except subprocess.TimeoutExpired:
        return [], False, "the journal took too long to read; try a smaller --minutes"
    if got.returncode != 0:
        return [], False, "the journal could not be read (run this as root)"
    return got.stdout.splitlines(), True, f"the last {int(minutes)} minutes of the journal"


def _counts(counter, top=12) -> list[str]:
    rows = [f"    {n:>7,}  {name}" for name, n in counter.most_common(top)]
    if len(counter) > top:
        rows.append(f"    {sum(n for _k, n in counter.most_common()[top:]):>7,}  "
                    f"({len(counter) - top} other names)")
    return rows


def main(argv: list[str]) -> int:
    from config.settings import settings

    minutes, log_file = 30, None
    for a in argv:
        if a.startswith("--minutes="):
            try:
                minutes = max(1, min(24 * 60, int(a.split("=", 1)[1])))
            except ValueError:
                pass
        elif a.startswith("--log="):
            log_file = a.split("=", 1)[1]

    asked = max(0, int(getattr(settings, "ingest_workers", 0) or 0))
    only = [n.strip().lower() for n in str(getattr(settings, "ingest_only", "") or "").split(",")
            if n.strip()]
    running = workers_running()
    lines, readable, source = read_lines(minutes, log_file)
    summary = summarize(lines)
    mb = 1048576

    print("=" * 70)
    print("THE SPLIT, AS IT IS RUNNING")
    print("=" * 70)
    if asked <= 0:
        print("  .env                   : INGEST_WORKERS is not set   <- the split is OFF")
    else:
        print(f"  .env                   : INGEST_WORKERS={asked}"
              + (f", INGEST_ONLY names {len(only)} channel{'' if len(only) == 1 else 's'}"
                 "   <- a TRIAL: every other channel is in the web process" if only
                 else "   <- EVERY channel is watched in a worker"))
    print(f"  worker processes found : {len(running)}"
          + ("" if not running else "   ("
             + ", ".join(f"worker {w['index']}: {w['rss'] / mb:.0f}MB" for w in running) + ")"))
    for w in running:
        n = w.get("live", 0)
        print(f"  worker {w['index']}, right now    : "
              + (f"capturing {n} live channel{'' if n == 1 else 's'}" if n
                 else "capturing nothing (no channel of its own is live)"))
    unlisted = None
    if asked > 0:
        ch = channels(pathlib.Path(settings.local_storage_path), asked, only)
        if ch.get("readable") and only:
            unlisted = ch["missing"]
        if not ch.get("readable"):
            print("  channels               : streams.json could not be read from here")
        elif only:
            for name, row in sorted(ch["rows"].items()):
                # The file is written when a channel is added, removed or
                # paused, not when it goes live: a channel added a minute ago
                # reads "starting" here for as long as nobody saves again.
                # What is true NOW is the line above (from /proc) and the
                # list further down (from the journal).
                said = ", ".join(f"{s} x{n}" if n > 1 else s for s, n in row["statuses"].most_common())
                print(f"  {name[:22]:<22} : worker {row['worker']}   (last saved as: {said}"
                      + (f"; listed by {row['listed']} accounts" if row["listed"] > 1 else "") + ")")
            for name in ch["missing"]:
                print(f"  {name[:22]:<22} : NOT LISTED BY ANYBODY   <- nobody has added this "
                      "channel, so nothing is in a worker.")
                print("      For the channels a trial could use, each with its command:")
                print("      cd /opt/highlightz && venv/bin/python scripts/split_trial.py")
        else:
            for index in sorted(ch["per_worker"]):
                said = ", ".join(f"{n} {s}" for s, n in ch["per_worker"][index].most_common())
                print(f"  worker {index}'s channels    : {said}")

    print()
    print("=" * 70)
    print(f"WHAT WAS LOGGED  ({source}; names and counts only)")
    print("=" * 70)
    if not readable:
        print(f"  could not be read: {source}")
    else:
        print(f"  lines read             : {summary['lines']:,}   ({summary['worker_lines']:,} from workers)")
        print(f"  workers started        : {summary['ups']}   (once each after a restart of the site is normal)")
        print(f"  workers that died      : {summary['lost']}")
        print(f"  loaded what they must not: {summary['forbidden']}")
        for index in sorted(summary["workers"]):
            w = summary["workers"][index]
            print(f"  worker {index}               : {w['lines']:,} lines, "
                  f"{sum(w['errors'].values())} errors, {sum(w['warnings'].values())} warnings")
            for title, counter in (("errors", w["errors"]), ("warnings", w["warnings"])):
                if counter:
                    print(f"   {title}:")
                    for row in _counts(counter, 8):
                        print(row)
        if summary["web"]:
            print("  the web process, about its workers:")
            for row in _counts(summary["web"], 8):
                print(row)

        print()
        print("=" * 70)
        print("WHAT A CHANNEL HAS DONE IN A WORKER  (what the trial is for)")
        print("=" * 70)
        for words, n in milestones(summary):
            print(f"  {words:<34} : " + (f"yes ({n:,})" if n else "not yet"))
        print("  Approve, reject, undo, pause and resume are yours to try on the channel's")
        print("  card: each should behave exactly as it does for any other channel.")

    print()
    for line in verdict(asked=asked, found=len(running), summary=summary, only=only,
                        journal=readable, unlisted=unlisted):
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
