# The GTA 6 breaking-news ad

A 24-second 9:16 product ad built on the owner's hook clip (owner, 2026-11-14:
"make another ad using this video as the hook"). The clip is an AI news
anchor: "Breaking. GTA 6 is coming. Who's clipping the biggest moments first?"
The ad answers him, and the newsroom never leaves: each product beat is a
headline under an "HZ NEWS" bar.

Output: `out/highlightz-gta6-ad.mp4`, 1080x1920, 60 fps, H.264 CRF 12,
AAC 320k, -14 LUFS / -1 dBTP, 24.4 s.

## Build

```sh
cd promo && npm install
# the owner's clip, gitignored:
cp <clip>.mov ad/gta-hook.mov
python3 -m pip install numpy scipy pillow realesrgan-ncnn-py     # + apt libomp5
npm run gta:footage    # Real-ESRGAN 2x of the first 145 frames -> public/ad-gta/frames (~9 min on 4 cores)
npm run gta:audio      # his voice for the hook, music, sound effects -> public/ad-gta/gta-ad.wav
npm run render:gta     # the picture (~5 min), then the sound muxed on
AD=GtaAd node ad/stills.mjs 0.6 6.4 10.4 13.6   # stills at given seconds, for checking layout
```

## How it is made

- **One timing file.** `src/ad/gta.json` holds every time: the scenes, each
  word of the lower third (from a speech-to-text pass with word timings), the
  chat lines of the spike and every sound cue. `src/ad/GtaAd.tsx` and
  `ad/make_gta_audio.py` both read it, at 128 BPM (a bar is 1.875 s).
- **The hook is his clip, untouched**, for exactly 3 bars (5.625 s, 135 of its
  24 fps frames; his last word ends at 5.58 s). The clip's last 2.75 s is its
  own end card at 720p; the ad rebuilds that card at 1080p with the same
  words ("Catch it live. Post it first.", highlightz.app).
- **Footage.** Upscaled 2x with Real-ESRGAN (`reel/upscale_reel.py`), Lanczos
  down to 1080x1920, 30% of the plain resize kept under it so skin keeps its
  texture, JPEG q96 4:4:4, picked per output frame. The photographic model
  (realesrgan-x4plus) is cleaner on a face but takes ~7 minutes a frame here.
- **His voice** is the only speech. A speech-to-text pass over the final mix
  reads exactly his three sentences and nothing else.
- **Music.** Original, synthesized (nothing sampled, nothing to license): the
  playful trap from the Duxxion ad, moved to F# minor because his clip has a
  faint steady tone at 1480 Hz (an F#) under the voice. A newsroom pulse sits
  13 LU under him for the hook; the drop is on "YOU ARE."; the spike bar is a
  build; "everyone else" goes muffled; it ends on the end card.
- **What the ad claims** is the site's own wording, read off the landing page
  and `PLAN_LIMITS` on 2026-11-14: 30 channels at once on Studio, 10 on Pro, 1
  on Free; "Every big moment, already a clip"; "Trigger score · live" against
  the channel's own threshold; the signal "Chat Calls For The Clip"; the Clip
  Editor's "Cam + Game" layout and 1080x1920 60 fps export and one-press
  posting to TikTok (both Pro); Free is one channel, Twitch or Kick, no card,
  no time limit. No speed is promised and no date is given for the game. If a
  plan or a feature changes, change `GtaAd.tsx` and re-render.
- **What it does not use.** "GTA 6" appears as plain words only: no logo, no
  artwork, no footage of the game. The live tiles, the clip thumbnail and the
  phone are drawn shapes, not real channels, streamers or screenshots. TikTok
  is named in text; its logo is not shown.

## Before it runs as a paid ad

The hook carries a "KlingAI 4.0" watermark in the bottom right corner, which
is what that tool puts on clips made without a paid plan. The ad leaves it
visible; it was not cropped or covered. Check that the plan it was made on
allows commercial use, or re-export the clip from a plan that does (which
also removes the mark), then drop the new file in as `ad/gta-hook.mov` and
run the three build steps again.

## Timeline

| Time | What |
|---|---|
| 0:00 | Hook: the anchor, with a lower third showing each word as he says it and a joke ticker |
| 0:05.6 | Freeze on his face: "YOU ARE." then "…if Highlightz is watching" |
| 0:07.5 | "GTA 6 drops. Everyone goes live.": 30 live tiles fill the screen |
| 0:09.4 | "You can't watch them all. Highlightz can.": a scan, "watching 30/30 at once" |
| 0:11.3 | "Chat explodes…": the trigger score climbs past the channel's threshold |
| 0:13.1 | "…it's already a clip.": a clip card, Approve pressed |
| 0:15.0 | "Make it vertical. Post in one press.": a phone, Post to TikTok, Posted (Pro) |
| 0:16.9 | "Everyone else: still scrubbing the VOD.": a grey player and a snail |
| 0:18.8 | "It's free to start.": 1 channel, no card, no time limit |
| 0:20.6 | Highlightz, "Catch it live. Post it first.", highlightz.app, "back to you, chat" |
