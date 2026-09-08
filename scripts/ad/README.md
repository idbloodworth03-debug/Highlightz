# "2:47 AM" — the 30-second Highlightz spot

A story ad, not a feature list: a streamer's best moment at 2:47 AM goes
unclipped, and Highlightz has it waiting the next morning. 1080x1920, 30 s,
30 fps, built entirely from the repo's own assets (fonts, logo, real
dashboard screenshots from `static/tutorial/`).

    ad.html    the film: one deterministic `seek(t)` renderer, seven scenes
    render.js  captures 900 JPEG masters through Chromium (Playwright)
    music.py   synthesises the music bed + sound design with numpy -> WAV

Rebuild (from the repo root, Playwright + Chromium present, numpy + imageio-ffmpeg installed):

    node scripts/ad/render.js 30 0 30           # -> scripts/ad/frames/f0000.jpg …
    python3 scripts/ad/music.py scripts/ad/music.wav
    ffmpeg -framerate 30 -i scripts/ad/frames/f%04d.jpg -i scripts/ad/music.wav \
      -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p -c:a aac -b:a 192k \
      -shortest -movflags +faststart highlightz-ad-247am.mp4

Scenes and their seconds live in `S` at the top of the script in ad.html;
music.py follows the same timeline. Every element is positioned by a
function of time, so a frame at any `t` is reproducible and a copy change
is a one-line edit.
