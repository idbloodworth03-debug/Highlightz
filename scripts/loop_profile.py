"""Ask the running site where its ONE loop spends its time, and print it.

    cd /opt/highlightz && venv/bin/python scripts/loop_profile.py
    cd /opt/highlightz && venv/bin/python scripts/loop_profile.py --seconds=30

Run it while channels are live: an idle site has nothing to show.

scripts/stream_cost.py says HOW BUSY the one loop is (44% of its core with 33
channels live on 2026-11-18, the thing that caps the site at about 52 live on
any size of server). This says WHAT it is busy with, which is what decides
whether that cap can be lifted cheaply or needs the work split across
processes. See src/maintenance/loop_profile.py for how it is measured.

It leaves a request file in the data folder; the running site picks it up
within a second, times every step its loop takes for that long, and writes the
report there. Nothing is sent
to the process, so a site still running older code is not disturbed: the
request is simply not answered, and this says so.

Read-only. Prints function and file names only: no values, no command lines.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main(argv: list[str]) -> int:
    from config.settings import settings
    from src.maintenance import loop_profile as lp

    seconds = lp.DEFAULT_S
    for a in argv:
        if a.startswith("--seconds="):
            seconds = lp._seconds_asked(a.split("=", 1)[1])
    store = pathlib.Path(settings.local_storage_path)
    req, rep = store / lp.REQUEST, store / lp.REPORT
    if not store.is_dir():
        print(f"The data folder ({store}) is not here. Run this from /opt/highlightz.")
        return 1
    before = rep.stat().st_mtime if rep.exists() else 0.0
    req.write_text(str(seconds))
    req.chmod(0o644)
    print(f"Asked the running site to time its loop for {seconds:.0f} seconds...", flush=True)

    # Picked up within a second or two when the site is running this code.
    for _ in range(16):
        time.sleep(0.5)
        if not req.exists():
            break
    else:
        try:
            req.unlink()
        except OSError:
            pass
        print("\nThe running site did not pick the request up. It is either stopped or "
              "still running\ncode from before this tool existed. Run the deploy command, "
              "then run this again.")
        return 1

    deadline = time.time() + seconds + 20
    while time.time() < deadline:
        if rep.exists() and rep.stat().st_mtime > before:
            print()
            print(rep.read_text(), end="")
            return 0
        time.sleep(0.5)
    print("\nThe site took the request but wrote no report. Look for loop_profile_failed "
          "in:\n  journalctl -u highlightz -n 200 --no-pager | grep loop_profile")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
