"""Render one auto-edit and tell you what came out. Run on PRODUCTION.

    /opt/highlightz/venv/bin/python scripts/edit_preview.py
    /opt/highlightz/venv/bin/python scripts/edit_preview.py --dry-run

WHY THIS EXISTS. ffmpeg is not installed in the dev container, only here, so
the filtergraph is written blind and asserted as a string. This is the step
that turns that into evidence: it builds a plan from your own approved
clips, generates the sound effects, runs the real command, and reports the
duration, the size and the streams that came out.

It does NOT touch the Autopilot, the queue, or anything a user can see. It
writes one file into /tmp and prints where. Nothing is posted.

--dry-run prints the plan and the ffmpeg command without running either,
which is what to paste back if the render fails.
"""
import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                          # noqa: E402
from src.autopilot import graph as G                          # noqa: E402
from src.autopilot import plan as P                           # noqa: E402
from src.autopilot import sfx as S                             # noqa: E402
from src.autopilot import render as ap_render                  # noqa: E402
from src.clips import files as clip_files                      # noqa: E402

ROOT = pathlib.Path(settings.local_storage_path)


def probe(path) -> dict:
    """Duration and streams, from ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,codec_name,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout or "{}")
    except Exception as exc:
        return {"error": str(exc)}


def source_duration(path) -> float:
    d = probe(path).get("format", {}).get("duration")
    try:
        return float(d)
    except (TypeError, ValueError):
        return 0.0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the command, run neither")
    ap.add_argument("--user", default="", help="limit to one account id (prefix ok)")
    ap.add_argument("--no-motion", action="store_true",
                    help="render every shot statically — skips the zoompan "
                         "filter. On prod 2026-09-22 a 60s render was "
                         "OOM-killed asking for 3.1 GB on an idle 3.8 GB box, "
                         "and zoompan is the suspect. Use this to get a "
                         "watchable file and to confirm the diagnosis.")
    ap.add_argument("--stack", action="store_true",
                    help="facecam across the top, gameplay under it. Needs "
                         "--cam to say where the camera is in the source.")
    ap.add_argument("--cam", default="-0.33,0.32,2.4",
                    help="where the camera sits, as OFFX,OFFY,ZOOM. Offsets "
                         "are fractions of the frame away from its CENTRE, so "
                         "-0.33,0.32 is down and to the left (the usual Twitch "
                         "corner) and 0,0 is dead centre. ZOOM is how tight "
                         "the window is: 2.4 keeps about a 42%% wide slice. "
                         "Same three numbers the browser editor uses, so a "
                         "position found here transfers to it.")
    ap.add_argument("--any-status", action="store_true",
                    help="render from ANY clip that has a file, not just "
                         "approved ones. Autopilot only ever edits approved "
                         "clips, so this is not what production does — it is "
                         "here because proving the renderer works should not "
                         "require going and clicking Approve first.")
    # NOT /tmp. The service unit is hardened and systemd gives such units a
    # PRIVATE /tmp, so a file this script writes to the real /tmp is invisible
    # to the running server — which is how /admin/edit-preview.mp4 404'd on a
    # render that had just succeeded. The app's own storage directory is the
    # one place both the script and the server agree about.
    ap.add_argument("--out", default=str(ROOT / "edit-preview.mp4"))
    ap.add_argument("--llm", action="store_true",
                    help="let the configured model build the plan instead of "
                         "the formula (LLM_PROVIDER=ollama|anthropic; falls "
                         "back to the formula and says why if it cannot)")
    args = ap.parse_args()

    print("=" * 66)
    print("AUTO-EDIT PREVIEW")
    print("=" * 66)

    try:
        clips = json.loads((ROOT / "clips.json").read_text())
    except Exception as exc:
        print(f"could not read clips.json: {exc}")
        return 1

    have = clip_files.existing_ids()
    want = ("approved", "pending") if args.any_status else ("approved",)
    # startswith, not ==. A user id printed for a human is usually truncated,
    # and an exact match against a truncated id silently returns nothing —
    # which reads as "you have no clips" rather than "your filter is wrong".
    def _mine(c):
        return not args.user or str(c.get("user_id", "")).startswith(args.user)

    pool = [c for c in clips
            if c.get("status") in want and c.get("id") in have and _mine(c)]
    if not pool:
        print(f"No {' or '.join(want)} clips with a file.")
        if not args.any_status:
            n = sum(1 for c in clips if c.get("status") == "pending"
                    and c.get("id") in have and _mine(c))
            if n:
                print(f"\n{n} PENDING clips do have a file. To render one now "
                      f"without approving anything:")
                print("    venv/bin/python scripts/edit_preview.py --any-status")
        return 1

    # ONE account, and NEVER a silently chosen one.
    #
    # This used to be `pool[0].get("user_id")` — whichever clip happened to
    # come first in clips.json. Run on production it picked a stranger's
    # account and started rendering their video, which is the one thing this
    # script must not do: the Privacy Policy says a clip's file is available
    # only to the account it belongs to, and "the operator was only
    # previewing" is not an exception anybody agreed to.
    #
    # So: one account, fine. More than one, and it asks. A tool that touches
    # user video does not get to guess whose.
    accounts = {}
    for c in pool:
        accounts.setdefault(str(c.get("user_id", "")), []).append(c)
    if len(accounts) > 1:
        print(f"\n{len(accounts)} accounts have clips with a file. Name one "
              f"with --user (a prefix is enough):\n")
        for u, cs in sorted(accounts.items(), key=lambda kv: -len(kv[1])):
            chans = sorted({c.get("channel", "?") for c in cs})[:4]
            print(f"   --user {u[:12]:<14} {len(cs):>4} clips   {', '.join(chans)}")
        print("\nRendering somebody else's clips is not something this script "
              "will do by accident.")
        return 1

    uid, pool = next(iter(accounts.items()))
    pool = pool[:12]
    print(f"\n{len(pool)} clips with a file for account {uid[:10]}…"
          f"  (statuses: {', '.join(want)})")

    sources = {}
    for c in pool:
        p = clip_files.path_for(c["id"])
        if p and p.exists():
            d = source_duration(p)
            if d > 0:
                sources[c["id"]] = (p, d)
    print(f"{len(sources)} of them probed cleanly")
    if not sources:
        return 1

    meta: dict = {}
    if args.llm:
        # --llm goes through the same call Autopilot would make, including the
        # fallback: if the provider is unreachable or unconfigured this prints
        # the reason and renders the formula's plan rather than stopping.
        from src.autopilot import builder
        st = builder.status()
        print(f"\nLLM: provider={st['provider']}  configured={st['configured']}"
              f"  target={st['detail']}")
        if st["warning"]:
            print(f"     WARNING: {st['warning']}")
        # The transcript is the only input that says what HAPPENS, and it is
        # what captions are written from. Cached ones only — this script must
        # not kick off a Whisper pass on the box that is watching streams.
        transcripts = {}
        for cid in sources:
            try:
                from src.captions import transcribe as cap
                payload = cap.load(clip_files.path_for(cid))
                if payload:
                    transcripts[cid] = payload.get("segments") or []
            except Exception:
                pass
        print(f"     transcripts available for {len(transcripts)} of {len(sources)}"
              + ("" if transcripts else "  (no captions will be written)"))
        plan, meta = await builder.build(pool, sources, transcripts=transcripts)
        if meta.get("reason"):
            print(f"     fell back to the formula — {meta['reason']}")
        if meta.get("took"):
            print(f"     model took {meta['took']}s")
        for note in meta.get("notes") or []:
            print(f"     note: {note}")
    else:
        plan = P.build(pool, sources, title="")
    if args.no_motion:
        for seg in plan.segments:
            seg.zoom = "none"
        print("\n--no-motion: every shot is static, zoompan is not in the graph")
    if args.stack:
        try:
            ox, oy, cz = (float(x) for x in args.cam.split(","))
        except ValueError:
            print(f"--cam wants OFFX,OFFY,ZOOM (got {args.cam!r})")
            return 1
        cam = P.Facecam(off_x=ox, off_y=oy, zoom=cz)
        for seg in plan.segments:
            seg.layout = "stack"
            seg.facecam = cam
        print(f"--stack: camera at off_x={ox} off_y={oy} zoom={cz}"
              f"   (top {int(P.SPLIT_TOP * 100)}% of the frame)")
        print("   too much gameplay in the top panel? raise zoom."
              "   wrong corner? flip the signs:")
        print("   bottom-left -0.33,0.32   bottom-right 0.33,0.32"
              "   top-left -0.33,-0.32   top-right 0.33,-0.32")
    ok, why = P.valid(plan)
    print(f"\nPLAN  ({plan.source}) — valid: {ok}{'' if ok else '  — ' + why}")
    for i, seg in enumerate(plan.segments):
        print(f"  {i+1}. {seg.channel or '?':<16} {seg.start:6.2f}–{seg.end:6.2f}s"
              f"  ({seg.length:5.2f}s)  zoom={seg.zoom}  framing={seg.framing}")
    print(f"  transition: {plan.transition} @ {plan.trans_dur}s"
          f"   joins: {max(0, len(plan.segments)-1)}")
    print(f"  sound: " + ", ".join(f"{c.kind}@{c.at:.1f}s" for c in plan.sfx))
    print(f"  PREDICTED DURATION: {P.plan_duration(plan):.2f}s")
    print(f"  captions: {len(plan.captions)}")
    for cue in plan.captions[:6]:
        print(f"      {cue['start']:6.2f}–{cue['end']:6.2f}s  {cue['text']!r}")
    if len(plan.captions) > 6:
        print(f"      … and {len(plan.captions) - 6} more")
    print(f"  cover   : {G.thumb_time(plan):.2f}s"
          + (f"  text={plan.thumb_text!r}" if plan.thumb_text else "  (no text)")
          + ("" if plan.thumb_at >= 0 else "   <- auto-picked, the plan chose none"))
    copy = meta.get("copy") or {}
    if copy:
        print(f"  title   : {copy.get('title') or '(none)'}")
        print(f"  caption : {copy.get('caption') or '(none)'}")
        print(f"  hashtags: {' '.join('#' + t for t in copy.get('hashtags') or [])}")
    if not ok:
        return 1

    print("\nSOUND EFFECTS")
    if args.dry_run:
        for k in S.KINDS:
            print(f"  {k:<8} {' '.join(S.command(k))}")
        paths = {k: S.path_for(k) for k in S.KINDS}
    else:
        paths = await S.ensure()
        for k in S.KINDS:
            p = S.path_for(k)
            print(f"  {k:<8} {'ok  ' + str(p.stat().st_size) + ' bytes' if k in paths else 'FAILED'}")

    font = ap_render.font_path()
    cmd = G.build_command(plan, pathlib.Path(args.out), paths, font=font)
    print(f"\nFONT: {font or '(none — title and captions skipped)'}")

    thumb_dst = pathlib.Path(args.out).with_suffix(".jpg")
    thumb_cmd = G.build_thumbnail_command(plan, pathlib.Path(args.out),
                                          thumb_dst, font=font)

    if args.dry_run:
        print("\nCOMMAND\n")
        print(" ".join(repr(a) if " " in a or ";" in a else a for a in cmd))
        print("\nTHUMBNAIL COMMAND\n")
        print(" ".join(repr(a) if " " in a or ";" in a else a for a in thumb_cmd))
        return 0

    print(f"\nRENDERING to {args.out} …")
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _o, err = await proc.communicate()
    took = time.time() - t0

    if proc.returncode != 0:
        print(f"\nFFMPEG FAILED after {took:.1f}s (exit {proc.returncode})\n")
        print((err or b"").decode()[-3000:])
        print("\nRe-run with --dry-run and paste the command back.")
        return 1

    info = probe(args.out)
    fmt = info.get("format", {})
    print(f"\nDONE in {took:.1f}s")
    print(f"  duration : {float(fmt.get('duration', 0)):.2f}s"
          f"   (planned {P.plan_duration(plan):.2f}s)")
    print(f"  size     : {int(fmt.get('size', 0))/1048576:.1f} MB")
    for st in info.get("streams", []):
        if st.get("codec_type") == "video":
            print(f"  video    : {st.get('codec_name')} {st.get('width')}x{st.get('height')}")
        elif st.get("codec_type") == "audio":
            print(f"  audio    : {st.get('codec_name')}")
    # The cover comes out of the finished file, so it runs after the render
    # and a failure here costs the thumbnail, not the video.
    tproc = await asyncio.create_subprocess_exec(
        *thumb_cmd, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    _o, terr = await tproc.communicate()
    if tproc.returncode == 0 and thumb_dst.exists():
        print(f"  cover    : {thumb_dst}  ({thumb_dst.stat().st_size // 1024} KB"
              f" at {G.thumb_time(plan):.2f}s)")
    else:
        print(f"  cover    : FAILED — {(terr or b'').decode()[-400:]}")

    print(f"\nWatch it:  scp root@$(hostname):{args.out} .")
    print(f"See cover: scp root@$(hostname):{thumb_dst} .")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
