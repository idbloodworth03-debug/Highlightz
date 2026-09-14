"""Why clips are not downloadable, answered on the box it applies to.

WHY THIS IS A MODULE AND NOT A PASTED SCRIPT. "Downloads don't work" has at
least six causes and they live in different places: the flag may be off in the
SERVICE (as opposed to in somebody's shell), the recorder may not be running,
the buffer may not be covering the requested window, the disk may be out of
headroom, retention may have swept the files, or the running code may simply
predate the feature. A dev container can answer none of them — see CLAUDE.md.
This reads the real state on the real machine and prints the one that applies.

    cd /opt/highlightz && venv/bin/python -m src.maintenance.why_no_download
    venv/bin/python -m src.maintenance.why_no_download --user <user_id>

It writes nothing and changes nothing.
"""

import subprocess
import sys
import time
from pathlib import Path

from config.settings import settings


def _n(x) -> str:
    return f"{x:,}"


def _service_env() -> dict:
    """What the RUNNING service has, which is the only version that counts.

    A variable exported in an interactive shell is invisible to a systemd
    unit, and that mismatch is the single most likely explanation for "I set
    CLIP_CAPTURE_ENABLED=true and nothing changed".
    """
    try:
        out = subprocess.run(["systemctl", "show", "highlightz", "-p", "MainPID"],
                             capture_output=True, text=True, timeout=10).stdout
        pid = out.strip().split("=")[-1]
        if not pid or pid == "0":
            return {}
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
        return dict(kv.split("=", 1) for kv in raw.split("\0") if "=" in kv)
    except Exception:
        return {}


def main(argv) -> int:
    want_user = ""
    for a in argv:
        if a.startswith("--user="):
            want_user = a.split("=", 1)[1]
        elif a == "--user" and argv.index(a) + 1 < len(argv):
            want_user = argv[argv.index(a) + 1]

    print("=" * 72)
    print("1. IS CAPTURE ON, IN THE SERVICE?")
    print("=" * 72)
    print(f"  settings.clip_capture_enabled      : {settings.clip_capture_enabled}")
    env = _service_env()
    if env:
        val = env.get("CLIP_CAPTURE_ENABLED", "(not set in the service env)")
        print(f"  CLIP_CAPTURE_ENABLED in the service: {val}")
    else:
        print("  (could not read the running service's environment — run as root,")
        print("   or the unit is not called 'highlightz')")
    envfile = Path("/opt/highlightz/.env")
    if envfile.exists():
        line = [l for l in envfile.read_text().splitlines()
                if l.strip().startswith("CLIP_CAPTURE_ENABLED")]
        print(f"  in .env                            : {line[0] if line else '(absent)'}")
    if not settings.clip_capture_enabled:
        print("\n  >> THIS IS THE ANSWER. No file is cut for any clip while this is")
        print("     off, so no clip can be downloaded. Set CLIP_CAPTURE_ENABLED=true")
        print("     in /opt/highlightz/.env and restart the service. Exporting it in")
        print("     a shell does NOT reach a systemd unit.")

    print()
    print("=" * 72)
    print("2. ARE THERE FILES ON DISK?")
    print("=" * 72)
    from src.clips import files as clip_files
    root = clip_files.root()
    mp4s = sorted(root.glob("*.mp4")) if root.exists() else []
    total = sum(p.stat().st_size for p in mp4s)
    print(f"  {root}")
    print(f"  {_n(len(mp4s))} file(s), {total / (1024*1024):.0f} MB")
    if mp4s:
        newest = max(mp4s, key=lambda p: p.stat().st_mtime)
        age_h = (time.time() - newest.stat().st_mtime) / 3600
        print(f"  newest: {newest.name}  ({age_h:.1f}h ago)")
        if age_h > 24:
            print("  >> Files exist but nothing new in over a day — capture has")
            print("     stopped producing. Check section 4.")
    else:
        print("  >> No files at all. Either capture never ran, or every cut failed.")
        print("     Section 4 says which.")

    print()
    print("=" * 72)
    print("3. DO THE CLIP RECORDS LINE UP WITH THE FILES?")
    print("=" * 72)
    print("  This is what the browser asks: a Download button appears when the")
    print("  file for a clip's id is on disk.")
    try:
        from src.dashboard import api
        clips = [c for c in api._clips.values()
                 if not want_user or c.get("user_id") == want_user]
        clips.sort(key=lambda c: c.get("created_at", 0), reverse=True)
        if not clips:
            print(f"  no clip records{' for ' + want_user if want_user else ''}")
        else:
            ready = sum(1 for c in clips if clip_files.exists(c.get("id", "")))
            print(f"  {_n(len(clips))} clip record(s), {_n(ready)} with a file "
                  f"({100 * ready // max(1, len(clips))}%)")
            print("\n  most recent 10:")
            for c in clips[:10]:
                has = clip_files.exists(c.get("id", ""))
                when = time.strftime("%m-%d %H:%M",
                                     time.localtime(c.get("created_at", 0)))
                print(f"    {'FILE ' if has else '  -- '} {when}  "
                      f"{(c.get('channel') or '?')[:18]:<18} {c.get('id', '')[:8]}")
            if ready and ready == len(clips):
                print("\n  >> Every clip has a file. The backend is serving downloads.")
                print("     If the button is not on screen, the running code is older")
                print("     than the feature — check section 5.")
    except Exception as exc:
        print(f"  could not read clip records: {type(exc).__name__}: {exc}")

    print()
    print("=" * 72)
    print("4. WHAT DID THE CUTS SAY?")
    print("=" * 72)
    print("  Every give-up in the cut path logs clip_file_skipped with a reason.")
    print("  Run this and read the `why`:")
    print("    journalctl -u highlightz --since '24 hours ago' | grep clip_file_skipped")
    print()
    print("  what each one means:")
    print("    buffer_miss    the rolling buffer did not cover the moment —")
    print("                   normal right after monitoring starts, a problem if")
    print("                   it is every clip (the recorder is not capturing)")
    print("    no_recorder    capture is off, or the recorder failed to start")
    print("    no_headroom    the disk cap is reached; clip_file_max_total_mb")
    print("                   is " + _n(settings.clip_file_max_total_mb) + " MB")
    print("    cut_raised     ffmpeg failed; the error is on the same line")
    print()
    print("  and the successes, which should outnumber them:")
    print("    journalctl -u highlightz --since '24 hours ago' | grep -c clip_file_ready")

    print()
    print("=" * 72)
    print("5. IS THE RUNNING CODE CURRENT?")
    print("=" * 72)
    try:
        head = subprocess.run(["git", "log", "--oneline", "-1"],
                              cwd="/opt/highlightz", capture_output=True,
                              text=True, timeout=10).stdout.strip()
        print(f"  deployed HEAD: {head or '(not a git checkout)'}")
    except Exception:
        print("  (could not read git HEAD)")
    print("  The download button needs `file_state` in the dashboard bundle:")
    try:
        n = Path("/opt/highlightz/src/dashboard/aurora_html.py").read_text().count("file_state")
        print(f"    file_state appears {n} time(s) — {'OK' if n else 'THE CODE IS OLD'}")
    except Exception:
        print("    (could not read aurora_html.py)")
    print()
    print("  A restart is what picks up new code AND a changed .env:")
    print("    systemctl restart highlightz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
