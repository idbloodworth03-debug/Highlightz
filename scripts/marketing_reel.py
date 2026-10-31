"""Make one marketing reel by hand, from a clip already captured. Run on PRODUCTION.

    venv/bin/python scripts/marketing_reel.py --list
    venv/bin/python scripts/marketing_reel.py --clip 3f2a

Then watch it at https://highlightz.app/admin/edit-preview.mp4 (signed in as
an admin; on a phone, long-press to save it).

The site makes these BY ITSELF for every new clip of a channel in
MARKETING_REEL_CHANNELS captured on an admin's account (src/marketing/auto.py),
straight into that admin's Library. This script is for the clips that came
in before that existed, and for redoing one.

READ-ONLY. It reads clips.json and the clip's file and writes exactly one
file, the preview (the same path scripts/edit_preview.py uses). It never
touches the Library or clips.json: both are held in the running server's
memory, and a second process writing them would be overwritten — or would
overwrite — on the server's next save.

Only clips on ADMIN accounts, like the automatic path: a clip's file belongs
to the account that captured it.

It runs its own ffmpeg, outside the server's one-render slot, so avoid
running it while Autopilot is busy (it is one ~30s encode).
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                          # noqa: E402
from src.clips import files as clip_files                     # noqa: E402
from src.marketing import auto, reel                          # noqa: E402

ROOT = pathlib.Path(settings.local_storage_path)
OUT = ROOT / "edit-preview.mp4"


def _admins() -> set[str]:
    from src.auth import users as user_store
    return {str(u.get("id")) for u in user_store.get_all() if u.get("is_admin")}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list the clips this can use and stop")
    ap.add_argument("--clip", default="", help="clip id (a prefix is enough); default: the best one")
    ap.add_argument("--channel", default="", help="a channel other than MARKETING_REEL_CHANNELS")
    args = ap.parse_args()

    chans = {args.channel.lower()} if args.channel else auto.channels()
    if not chans:
        print("No channels: set MARKETING_REEL_CHANNELS in .env or pass --channel.")
        return 1
    if not reel.assets_ready():
        print(f"The ad section is missing: {reel.assets_dir() / reel.AD}")
        return 1
    try:
        clips = json.loads((ROOT / "clips.json").read_text())
    except Exception as exc:
        print(f"could not read clips.json: {exc}")
        return 1
    admins = _admins()
    have = clip_files.existing_ids()
    pool = [c for c in clips
            if str(c.get("channel") or "").lower() in chans and c.get("id") in have
            and str(c.get("user_id")) in admins and c.get("status") != "rejected"]
    if not pool:
        others = sum(1 for c in clips if str(c.get("channel") or "").lower() in chans
                     and c.get("id") in have)
        print(f"No {', '.join(sorted(chans))} clips with a video on an admin account.")
        if others:
            print(f"({others} are on other accounts; their files are theirs, so they are not used."
                  " Monitor the channel on your own admin account.)")
        return 1
    from src.autopilot import plan as P
    pool = P.rank(pool)
    if args.list:
        for c in pool[:30]:
            mr = (c.get("marketing_reel") or {}).get("status", "-")
            print(f"  --clip {c['id'][:10]:<12} {c.get('channel', '?'):<12} {c.get('status', '?'):<9}"
                  f" reel:{mr:<9} {(c.get('clip_title') or '')[:40]}")
        return 0
    if args.clip:
        pool = [c for c in pool if str(c.get("id", "")).startswith(args.clip)]
        if len(pool) != 1:
            print(f"--clip {args.clip!r} matches {len(pool)} clips; run --list")
            return 1
    clip = pool[0]
    src = clip_files.path_for(clip["id"])
    print(f"clip {clip['id'][:10]}  {clip.get('channel')}  {clip.get('clip_title') or ''}")
    t = time.time()
    try:
        info = await reel.make(clip, src, OUT, preset=auto._preset_for(str(clip.get("user_id")), clip))
    except reel.ReelError as exc:
        print(f"FAILED: {exc}")
        return 1
    h, tl = info["hook"], info["timeline"]
    print(f"hook    {h['start']:.1f}-{h['end']:.1f}s of the clip ({h['source']}; "
          f"score peak {h['peak_at']}, moment {h['moment_at']})")
    print(f"ff      {tl['ff']} at {tl['ff_speed']}x    1x {tl['react']}")
    print(f"reel    {info['seconds']}s, rendered in {time.time() - t:.0f}s -> {OUT}")
    print("watch   https://highlightz.app/admin/edit-preview.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
