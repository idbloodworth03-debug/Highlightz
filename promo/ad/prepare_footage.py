#!/usr/bin/env python3
"""The Duxxion clip ad's footage: the owner's clip, upscaled, one JPEG a frame.

The clip (ad/duxxion-goal.mp4, 720x1280 HEVC, 30 fps, 10.67 s) fades to the
streamer's Instagram card after 6.53 s, so only its first 197 frames are used.
Those are upscaled 2x with Real-ESRGAN (reel/upscale_reel.py, the same model
and colour handling as the reel's upload copy), brought down to the ad's
1080x1920 with Lanczos, and written as JPEG quality 96, 4:4:4, to
public/ad/frames/NNNN.jpg, which src/ad/DuxAd.tsx picks from frame by frame
(so slow-mo, freezes and the rewind show exact source frames).

    python3 ad/prepare_footage.py          # resumable: the upscale skips done frames
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
CLIP = os.path.join(HERE, "duxxion-goal.mp4")
WORK = os.path.join(HERE, "esrgan")
OUT = os.path.join(PROMO, "public", "ad", "frames")
FRAMES = 197


def main():
    subprocess.run([sys.executable, os.path.join(PROMO, "reel", "upscale_reel.py"), CLIP,
                    "--work", WORK, "--frames", str(FRAMES)], check=True)
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)
    x2 = os.path.join(WORK, "x2")
    for k in range(FRAMES):
        name = f"{k:04d}"
        dst = os.path.join(OUT, name + ".jpg")
        if os.path.exists(dst):
            continue
        im = Image.open(os.path.join(x2, name + ".png")).convert("RGB")
        im = im.resize((1080, 1920), Image.LANCZOS)
        im.save(dst, quality=96, subsampling=0, optimize=True)
    print(f"{FRAMES} frames in {OUT}")


if __name__ == "__main__":
    main()
