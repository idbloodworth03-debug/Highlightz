"""Where does the green line come from? Measure it. Run on PRODUCTION.

    venv/bin/python scripts/edge_check.py --user <account prefix> --clip <clip prefix> --hook 18-26

Owner, 2026-09-23, after the zoom was taken out of the auto-edit: "still
got the green line on the right at the beginning". A green edge on 4:2:0
video is pixels whose colour channels are near zero, and there are several
places one can come from — the source file itself, the seek into the clip
where the hook starts, or the slide against black. Guessing at which has
already been wrong once, so this reads the actual pixels instead:

  1. ffmpeg's version and the source clip's stream (codec, size, coded
     size, pixel format) — a coded size bigger than the picture is the
     classic origin of green columns;
  2. the right-hand 4 pixels of the SOURCE, 4 times a second, from 0s and
     from the hook's in-point;
  3. the right-hand 4 pixels of the RENDERED preview (edit-preview.mp4, the
     middle band where the picture sits), 4 times a second for 10 seconds.

Each sample prints its average Y/U/V. Neutral colours sit near U=V=128;
green is BOTH well below it. It reads files and prints numbers — it writes
nothing and touches nothing a user can see.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                          # noqa: E402
from src.clips import files as clip_files                      # noqa: E402

ROOT = pathlib.Path(settings.local_storage_path)
GREEN_BELOW = 100          # both U and V under this reads as green


def run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return (out.stdout or "") + (out.stderr or "")
    except Exception as exc:
        return f"(failed: {exc})"


def strip_stats(path, *, start: float, seconds: float, band: bool) -> list[tuple]:
    """(t, Y, U, V) of the right-hand 4px strip, 4 samples a second.

    `band` crops to the middle 600px of a 1920-tall render, where the
    picture sits over the blurred background; the source is measured full
    height.
    """
    crop = "crop=4:600:iw-4:(ih-600)/2" if band else "crop=4:ih:iw-4:0"
    vf = f"fps=4,{crop},signalstats,metadata=print:file=-"
    txt = run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
               "-i", str(path), "-vf", vf, "-f", "null", "-"])
    rows, cur = [], {}
    for line in txt.splitlines():
        m = re.match(r"frame:\d+\s+pts:\S+\s+pts_time:(\S+)", line)
        if m:
            if cur:
                rows.append(cur)
            cur = {"t": float(m.group(1))}
            continue
        m = re.match(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG)=(\S+)", line)
        if m and cur:
            cur[m.group(1)] = float(m.group(2))
    if cur:
        rows.append(cur)
    return [(start + r["t"], r.get("YAVG"), r.get("UAVG"), r.get("VAVG")) for r in rows]


def green_scan(path, *, start: float, seconds: float, cols: int = 270,
               w: int = 1080, h: int = 1920, fps: int = 30) -> list[tuple]:
    """Every frame, every pixel of the right `cols` columns: where is green?

    The strip averages above can miss a 1-2px line (averaged away over 4
    columns) or a line in the blurred bands above and below the picture, and
    4 samples a second can step over a few frames. This reads raw RGB and
    counts pixels whose green clearly beats both red and blue. Returns
    (t, green_pixel_count, x_min, x_max, y_min, y_max) per frame with any.
    Pure Python on purpose: the box's venv has no numpy or Pillow.
    """
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
           "-i", str(path), "-vf", f"crop={cols}:{h}:{w - cols}:0",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=600).stdout
    except Exception as exc:
        print(f"   (scan failed: {exc})")
        return []
    size = cols * h * 3
    out = []
    for i in range(len(raw) // size):
        frame = raw[i * size:(i + 1) * size]
        r, g, b = frame[0::3], frame[1::3], frame[2::3]
        n = 0
        x0 = y0 = 10 ** 9
        x1 = y1 = -1
        for k, (rr, gg, bb) in enumerate(zip(r, g, b)):
            if gg > 60 and gg > rr + 30 and gg > bb + 30:
                n += 1
                y, x = divmod(k, cols)
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
        if n >= 20:
            out.append((start + i / fps, n, w - cols + x0, w - cols + x1, y0, y1))
    return out


def show_scan(title: str, rows: list[tuple], frames: int) -> None:
    print(f"\n{title}")
    if not rows:
        print(f"   no green in any of {frames} frames")
        return
    print("     t    green px   columns       rows")
    for t, n, x0, x1, y0, y1 in rows:
        print(f"  {t:6.3f}  {n:8d}   x {x0:4d}-{x1:4d}   y {y0:4d}-{y1:4d}")


def show(title: str, rows: list[tuple]) -> None:
    print(f"\n{title}")
    if not rows:
        print("   (no samples — the file could not be read)")
        return
    print("     t      Y      U      V")
    for t, y, u, v in rows:
        green = u is not None and v is not None and u < GREEN_BELOW and v < GREEN_BELOW
        print(f"  {t:5.2f}  {y or 0:5.1f}  {u or 0:5.1f}  {v or 0:5.1f}"
              + ("   <- GREEN" if green else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="account id (prefix ok)")
    ap.add_argument("--clip", required=True, help="clip id (prefix ok)")
    ap.add_argument("--hook", default="", help="the hook's START-END, e.g. 18-26")
    args = ap.parse_args()

    clips = json.loads((ROOT / "clips.json").read_text())
    mine = [c for c in clips
            if str(c.get("user_id", "")).startswith(args.user)
            and str(c.get("id", "")).startswith(args.clip)]
    if len(mine) != 1:
        print(f"{len(mine)} clips match --user {args.user!r} --clip {args.clip!r}; "
              "it has to be exactly one of your own")
        return 1
    src = clip_files.path_for(mine[0]["id"])
    if not src or not src.exists():
        print("that clip has no file")
        return 1

    print("=" * 60)
    print("EDGE CHECK")
    print("=" * 60)
    print(run(["ffmpeg", "-version"]).splitlines()[0])
    print(f"source: {src.name}")
    print(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=codec_name,profile,width,height,coded_width,coded_height,"
               "pix_fmt,r_frame_rate:format=format_name",
               "-of", "default=nw=1", str(src)]).strip())

    show("SOURCE, right edge, from 0s", strip_stats(src, start=0.0, seconds=2.0, band=False))
    if args.hook:
        try:
            hs = float(args.hook.replace(",", "-").split("-")[0])
        except ValueError:
            print(f"--hook wants START-END (got {args.hook!r})")
            return 1
        show(f"SOURCE, right edge, from the hook ({hs:g}s)",
             strip_stats(src, start=hs, seconds=3.0, band=False))

    out = ROOT / "edit-preview.mp4"
    if out.exists():
        show("RENDER (edit-preview.mp4), right edge of the picture, first 10s",
             strip_stats(out, start=0.0, seconds=10.0, band=True))
        print("\nscanning every frame, pixel by pixel (about a minute)…")
        show_scan("RENDER, right quarter, EVERY frame of the first 1.5s",
                  green_scan(out, start=0.0, seconds=1.5), 45)
        if args.hook:
            try:
                a, b = (float(x) for x in args.hook.replace(",", "-").split("-"))
                join = (b - a) - 0.5          # where the hook slides into the clip
                show_scan(f"RENDER, right quarter, every frame around the hook's "
                          f"slide ({join:.1f}s)",
                          green_scan(out, start=max(0.0, join - 0.25), seconds=1.0), 30)
            except ValueError:
                pass
    else:
        print("\nno edit-preview.mp4 — render one first")
    print("\nPaste everything above back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
