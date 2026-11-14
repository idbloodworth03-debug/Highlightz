#!/usr/bin/env python3
"""The GTA 6 breaking-news ad's footage: the owner's hook clip, upscaled, one
JPEG a frame.

The clip (ad/gta-hook.mov, 720x1280 HEVC, 24 fps, 8.79 s) is an AI news anchor
for its first 145 frames (6.04 s) and its own end card after that; the ad
plays the first 135 and freezes on one of the still ones that follow, so 145
are prepared. They are upscaled 2x with Real-ESRGAN (reel/upscale_reel.py, the
same model and colour handling as the earlier ads), brought down to the ad's
1080x1920 with Lanczos, and written as JPEG quality 96, 4:4:4, to
public/ad-gta/frames/NNNN.jpg, which src/ad/GtaAd.tsx picks from by index.

WHY NOT THE BIGGER MODEL. realesrgan-x4plus is the photographic model and it
is cleaner on his face, but on this CPU it takes ~7 minutes a frame (about 4
hours for the clip on 4 cores) against ~15 s for realesr-animevideov3. Side by
side at the ad's size the fast model is already crisper than a plain resize;
BLEND keeps a share of the plain resize under it so skin keeps its texture
instead of going smooth.

    python3 ad/prepare_gta_footage.py      # resumable: the upscale skips done frames
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
CLIP = os.path.join(HERE, "gta-hook.mov")
WORK = os.path.join(HERE, "esrgan-gta")
OUT = os.path.join(PROMO, "public", "ad-gta", "frames")
FRAMES = 145
BLEND = 0.30          # how much of the plain Lanczos resize sits under the upscale


def main():
    subprocess.run([sys.executable, os.path.join(PROMO, "reel", "upscale_reel.py"), CLIP,
                    "--work", WORK, "--frames", str(FRAMES)], check=True)
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)
    for k in range(FRAMES):
        name = f"{k:04d}"
        dst = os.path.join(OUT, name + ".jpg")
        if os.path.exists(dst):
            continue
        up = Image.open(os.path.join(WORK, "x2", name + ".png")).convert("RGB").resize((1080, 1920), Image.LANCZOS)
        plain = Image.open(os.path.join(WORK, "src", name + ".png")).convert("RGB").resize((1080, 1920), Image.LANCZOS)
        Image.blend(up, plain, BLEND).save(dst, quality=96, subsampling=0, optimize=True)
    print(f"{FRAMES} frames in {OUT}")


if __name__ == "__main__":
    main()
