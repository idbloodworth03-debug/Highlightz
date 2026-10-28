#!/usr/bin/env python3
"""Upscale every frame of the owner's reel 2x with Real-ESRGAN, for the
sharpest upload copy the 720x1280 source allows.

WHY. The reel arrived as 720x1280 HEVC at ~2 Mbps. A plain Lanczos upscale is
faithful but soft, and keeps the compression mush around every letter.
realesr-animevideov3 (Real-ESRGAN's compact video model, BSD-3) redraws hard
edges: on this reel's motion-graphics text, HUD and labels it is visibly
crisper, and it cleans the blocking. On the gameplay it smooths a little.

HOW. ncnn on the CPU (realesrgan-ncnn-py, models bundled in the wheel; no GPU
here). Its CPU path uses one core, about 15 s a frame, so one worker per core
splits the frames between them: ~40 min for the reel's 611 frames on 4 cores.
Resumable: frames already done are skipped, so just run it again if stopped.
Colour: decoded and re-encoded as BT.709 TV range explicitly, both ways, so
the round trip through RGB doesn't shift the source's colours.

    pip install realesrgan-ncnn-py      # needs libomp5 (apt) for its OpenMP
    python3 reel/upscale_reel.py reel/reel-v1.mov
    # then: python3 reel/make_reel_audio.py reel/reel-v1.mov --upscaled reel/esrgan/x2
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "esrgan")


def work(job):
    """One worker: every n-th frame of the to-do list, in its own process."""
    k, n, src, dst, todo = job
    from PIL import Image
    from realesrgan_ncnn_py import Realesrgan
    r = Realesrgan(gpuid=-1, model=0)            # CPU; model 0 = realesr-animevideov3 x2
    mine = todo[k::n]
    t0 = time.time()
    for i, f in enumerate(mine, 1):
        out = r.process_pil(Image.open(os.path.join(src, f)).convert("RGB"))
        tmp = os.path.join(dst, f".{f}")
        out.save(tmp, format="PNG", compress_level=1)
        os.replace(tmp, os.path.join(dst, f))     # a frame exists only once it's whole
        if k == 0 and (i % 10 == 0 or i == len(mine)):
            per = (time.time() - t0) / i
            print(f"worker 0: {i}/{len(mine)}  {per:.1f}s/frame  ~{per * (len(mine) - i) / 60:.0f} min left",
                  flush=True)
    return len(mine)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reel")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    args = ap.parse_args()
    src, dst = os.path.join(WORK, "src"), os.path.join(WORK, "x2")
    os.makedirs(src, exist_ok=True)
    os.makedirs(dst, exist_ok=True)
    if not os.listdir(src):
        subprocess.run(["ffmpeg", "-v", "error", "-i", args.reel, "-fps_mode", "passthrough",
                        "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
                        "-start_number", "0", os.path.join(src, "%04d.png")], check=True)
    frames = sorted(os.listdir(src))
    todo = [f for f in frames if not os.path.exists(os.path.join(dst, f))]
    n = max(1, min(args.workers, len(todo)))
    print(f"{len(frames)} frames, {len(todo)} to do, {n} workers", flush=True)
    if todo:
        with mp.get_context("spawn").Pool(n) as pool:
            pool.map(work, [(k, n, src, dst, todo) for k in range(n)])
    done = len([f for f in os.listdir(dst) if not f.startswith(".")])
    if done != len(frames):
        sys.exit(f"{done} of {len(frames)} frames upscaled")
    print("all frames upscaled", flush=True)


if __name__ == "__main__":
    main()
