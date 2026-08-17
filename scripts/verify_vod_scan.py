"""Run a REAL VOD scan on the real box, against a real Twitch VOD.

Everything else that verifies the scanner runs in-process with Twitch stubbed,
which cannot prove the two things that actually broke it in production: whether
the external binaries exist, and whether the live Twitch responses match what
the analyzer expects. Both are facts about this machine, so they can only be
checked here.

    /opt/highlightz/venv/bin/python scripts/verify_vod_scan.py <vod_id_or_url>
    /opt/highlightz/venv/bin/python scripts/verify_vod_scan.py <vod> --no-audio

Pick a SHORT vod for the first run. The audio pass decodes the whole thing, so
a six-hour stream takes many minutes; --no-audio skips it and checks the rest.
Read-only: it creates no clips and writes nothing to the database.
"""
import asyncio
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings          # noqa: E402
from src.vod import analyzer                  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
NO_AUDIO = "--no-audio" in sys.argv
if not args:
    sys.exit("usage: verify_vod_scan.py <vod_id_or_url> [--no-audio]")

vod_id = analyzer.parse_vod_id(args[0]) or args[0].strip()
if not vod_id.isdigit():
    sys.exit(f"could not read a VOD id out of {args[0]!r}")

ok = True
def check(label, passed, detail=""):
    global ok
    ok = ok and passed
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


print("=" * 70)
print("ENVIRONMENT")
print("=" * 70)

sl = shutil.which(settings.streamlink_path)
ff = shutil.which(settings.ffmpeg_path)
check("streamlink is installed", bool(sl), sl or f"'{settings.streamlink_path}' not on PATH")
check("ffmpeg is installed", bool(ff), ff or f"'{settings.ffmpeg_path}' not on PATH")

for name, path in (("streamlink", sl), ("ffmpeg", ff)):
    if not path:
        continue
    try:
        out = subprocess.run([path, "--version"], capture_output=True, timeout=20)
        check(f"{name} runs", out.returncode == 0,
              (out.stdout or out.stderr).decode(errors="replace").splitlines()[0][:60])
    except Exception as exc:
        check(f"{name} runs", False, str(exc))

audio_on = settings.vod_audio_enabled and not NO_AUDIO
print(f"  ..    VOD_AUDIO_ENABLED = {settings.vod_audio_enabled}"
      + ("  (skipped by --no-audio)" if NO_AUDIO else ""))
check("Twitch credentials are configured",
      bool(settings.twitch_client_id and settings.twitch_client_secret))

if not ok:
    print("\nEnvironment problems above — the scan will fail regardless of code.")
    sys.exit(1)

if NO_AUDIO:
    settings.vod_audio_enabled = False

print()
print("=" * 70)
print(f"LIVE SCAN  vod {vod_id}   audio={'on' if audio_on else 'off'}")
print("=" * 70)

state = {"moments": [], "errors": [], "done": False, "audio_secs": 0,
         "info": None, "last_pct": -1.0}

async def on_progress(pct, data=None):
    data = data or {}
    if data.get("vod_title") and state["info"] is None:
        state["info"] = data
        print(f"  VOD: {data.get('vod_title','?')[:50]}  "
              f"channel={data.get('channel','?')}  "
              f"duration={data.get('duration',0):.0f}s  game={data.get('game','?')}")
    if data.get("audio_seconds"):
        state["audio_secs"] = data["audio_seconds"]
    if pct - state["last_pct"] >= 10:
        state["last_pct"] = pct
        extra = f" audio={state['audio_secs']}s" if state["audio_secs"] else ""
        print(f"  {pct:5.1f}%  {data.get('phase','') or '':<8}{extra}")

async def on_moment(m):
    state["moments"].append(m)

async def on_done(*a, **k):
    state["done"] = True

async def on_error(msg):
    state["errors"].append(msg)

start = time.time()
try:
    asyncio.run(analyzer.run_vod_analysis(
        vod_id, "", "default", "verify-script",
        on_progress, on_moment, on_done, on_error))
except Exception as exc:
    import traceback
    traceback.print_exc()
    state["errors"].append(f"{type(exc).__name__}: {exc}")

elapsed = time.time() - start

print()
print("=" * 70)
print("RESULT")
print("=" * 70)
check("the scan raised no error", not state["errors"],
      "; ".join(state["errors"])[:200] if state["errors"] else "")
check("the scan ran to completion", state["done"])
check("VOD metadata was fetched", state["info"] is not None)
if audio_on:
    check("the audio pass decoded something", state["audio_secs"] > 0,
          f"{state['audio_secs']}s decoded")
check("moments were found", bool(state["moments"]),
      f"{len(state['moments'])} moment(s)")

for m in state["moments"][:5]:
    print(f"        {m.get('timecode','?'):>9}  score={m.get('score',0):5.1f}  "
          f"{str(m.get('reason',''))[:44]}")

print()
print(f"took {elapsed:.1f}s")
print("VOD SCANNER IS WORKING" if ok else "VOD SCANNER IS NOT WORKING — see the FAIL lines")
sys.exit(0 if ok else 1)
