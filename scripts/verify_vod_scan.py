"""Run a REAL VOD scan on the real box, against a real Twitch VOD.

Everything else that verifies the scanner runs in-process with Twitch stubbed,
which cannot prove the two things that actually broke it in production: whether
the external binaries exist, and whether the live Twitch responses match what
the analyzer expects. Both are facts about this machine, so they can only be
checked here.

    venv/bin/python scripts/verify_vod_scan.py 123456789
    venv/bin/python scripts/verify_vod_scan.py https://www.twitch.tv/videos/123456789
    venv/bin/python scripts/verify_vod_scan.py lacy            # latest VOD for a channel
    venv/bin/python scripts/verify_vod_scan.py lacy --no-audio
    venv/bin/python scripts/verify_vod_scan.py lacy --minutes=20
    venv/bin/python scripts/verify_vod_scan.py lacy --full        # the whole VOD

Only the first 10 minutes of the VOD are scanned unless --full is given: a
six-hour VOD is ~11 minutes of chat paging before the audio pass even begins,
and the question here is "does this work", not "how many moments are in it".
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

# Scan only the first N minutes. A full six-hour VOD is ~11 minutes of chat
# paging before the audio pass even starts, which is a fine thing for the
# product to do and a terrible thing to sit and watch when the question is
# merely "does this work at all". Ten minutes of a real VOD answers that.
MINUTES = 10.0
for a in sys.argv[1:]:
    if a.startswith("--minutes="):
        MINUTES = float(a.split("=", 1)[1])
FULL = "--full" in sys.argv
if not args:
    sys.exit("usage: verify_vod_scan.py VOD_ID_OR_URL_OR_CHANNEL [--no-audio]")

async def _latest_vod_for_channel(login: str) -> str | None:
    """Most recent archived VOD for a channel, so the caller can pass a channel
    name instead of hunting for a VOD id on twitch.tv."""
    import aiohttp
    from src.output import twitch_clips
    # Any failure here is a diagnosis, not a crash: an app-token 403 means the
    # Twitch credentials are wrong, which is exactly the kind of thing this
    # script exists to report clearly rather than as a traceback.
    try:
        token = await analyzer._get_app_token()
        bid = await twitch_clips.resolve_broadcaster_id(login)
    except Exception as exc:
        print(f"  could not reach Twitch: {exc}")
        return None
    if not token:
        print("  could not get a Twitch app token — check TWITCH_CLIENT_ID "
              "and TWITCH_CLIENT_SECRET in .env")
        return None
    if not bid:
        print(f"  Twitch has no channel called {login!r}")
        return None
    hdrs = {"Client-ID": settings.twitch_client_id,
            "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"{twitch_clips.HELIX_BASE}/videos", headers=hdrs,
                                params={"user_id": bid, "type": "archive",
                                        "first": "1"}) as resp:
                if resp.status != 200:
                    print(f"  Twitch returned {resp.status} listing that channel's VODs")
                    return None
                data = (await resp.json()).get("data") or []
    except Exception as exc:
        print(f"  could not list VODs: {exc}")
        return None
    return data[0]["id"] if data else None


target = args[0].strip()
vod_id = analyzer.parse_vod_id(target) or target
if not vod_id.isdigit():
    # Not a VOD id or URL — treat it as a channel name and find their latest.
    print(f"looking up the most recent VOD for channel {vod_id!r} ...")
    found = asyncio.run(_latest_vod_for_channel(vod_id.lstrip("@")))
    if not found:
        sys.exit(f"no archived VOD found for {vod_id!r} — pass a VOD url or id "
                 f"instead, e.g. https://www.twitch.tv/videos/123456789")
    print(f"  using VOD {found}")
    vod_id = found

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

# ffmpeg wants -version (single dash) and EXITS NON-ZERO on --version after
# printing its banner, so keying off the exit code alone reported a perfectly
# working ffmpeg as broken. What actually proves the binary runs is that it
# printed its own version string.
for name, path, flag in (("streamlink", sl, "--version"),
                         ("ffmpeg", ff, "-version")):
    if not path:
        continue
    try:
        out = subprocess.run([path, flag], capture_output=True, timeout=20)
        text = (out.stdout or b"").decode(errors="replace") \
             + (out.stderr or b"").decode(errors="replace")
        first = next((l for l in text.splitlines() if l.strip()), "")
        # Either signal is enough, and neither alone is: streamlink exits 0 but
        # prints "streamlink 8.4.0" with no the word "version" in it, while
        # ffmpeg given a flag it dislikes exits NON-zero after printing its
        # version banner. Requiring both is what produced the false FAIL.
        check(f"{name} runs",
              out.returncode == 0 or "version" in text.lower(), first[:60])
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

# Clamp the duration the analyzer is told about. fetch_vod_chat pages until it
# reaches `duration`, so a smaller number simply stops it early — the same real
# code path on the same real VOD, just less of it.
if not FULL:
    _real_info = analyzer.fetch_vod_info
    async def _clamped_info(vid, token):
        info = await _real_info(vid, token)
        if info and info.get("duration", 0) > MINUTES * 60:
            print(f"  (VOD is {info['duration'] / 3600:.1f}h — scanning the first "
                  f"{MINUTES:.0f} min; pass --full for all of it)")
            info = dict(info, duration=MINUTES * 60)
        return info
    analyzer.fetch_vod_info = _clamped_info

print()
print("=" * 70)
print(f"LIVE SCAN  vod {vod_id}   audio={'on' if audio_on else 'off'}"
      + ("   FULL" if FULL else f"   first {MINUTES:.0f} min"))
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
