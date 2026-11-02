# The Duxxion clip ad

A 26-second 9:16 ad for Duxxion's campaign, built from the owner's clip of
Duxxion scoring in Rocket League (owner, 2026-11-02: "professional and high
quality but also goofy and entertaining, 20-30 seconds, no voice over, trendy
music, talk about the campaign and joining it").

Output: `out/highlightz-duxxion-ad.mp4`, 1080x1920, 60 fps, H.264 CRF 12,
AAC 320k, -14 LUFS / -1 dBTP, 26.25 s.

## Build

```sh
cd promo
# the owner's clip, gitignored:
cp <clip>.mp4 ad/duxxion-goal.mp4
pip install numpy scipy pillow realesrgan-ncnn-py     # + apt libomp5
npm run ad:footage     # Real-ESRGAN 2x of the first 197 frames -> public/ad/frames (~15 min on 4 cores)
npm run ad:audio       # music (--style trap|jersey|phonk), sound effects, the laugh, the bleep -> public/ad/duxxion-ad-<style>.wav
npm run render:ad      # the picture, then the sound muxed on
node ad/stills.mjs 0.3 9.5 14.3   # stills at given seconds, for checking layout
```

## How it is made

- **One timing file.** `src/ad/ad.json` holds every time: which source
  frame shows when (slow-mo, freeze, the rewind), where his laugh comes from,
  the bleep, the scenes and every sound cue. `src/ad/DuxAd.tsx` and
  `ad/make_ad_audio.py` both read it, at 128 BPM (a bar is 1.875 s).
- **The clip.** Only its first 6.53 s (197 frames): after that it fades to
  his Instagram card. Upscaled 2x with Real-ESRGAN (`reel/upscale_reel.py`),
  Lanczos down to 1080x1920, JPEG q96 4:4:4, picked per output frame.
- **His voice is muted.** The clip's speech swears throughout (checked with
  a speech-to-text pass). Only his laugh after the goal is heard; his last
  yell sits under a censor bleep, with "*BLEEP*" on screen. A speech-to-text
  pass over the final mix finds no words.
- **The clip's own caption** ("musty catching strays", burned in at y ~962)
  is always covered by ours while the clip is on screen.
- **Music.** Original, synthesized in `make_ad_audio.py` (nothing sampled,
  nothing to license), three styles on the same edit: `trap` (playful trap:
  marimba hook, pizzicato strings, half-time drums, sliding 808; the
  default), `jersey` (jersey club: triplet kicks, bed-squeaks, chord stabs)
  and `phonk` (the first version, which the owner asked to replace). Each
  drops on frame one, tape-stops on the record scratch, plays backwards
  through the rewind, sinks through the slow-mo, and drops again on the
  goal. To put a track on the rendered picture without re-rendering:
  `ffmpeg -i out/duxxion-ad-video-only.mp4 -i public/ad/duxxion-ad-<style>.wav
  -map 0:v -map 1:a -c:v copy -c:a aac -b:a 320k -shortest out/...mp4`.
- **Campaign facts on screen** ($500 pool; $250 / $150 / $100; most views on
  one clip; free to join; post on TikTok or Reels with #duxhighlightz and add
  the link; last day to post Thu, Nov 5, since posting closes 12:00 AM ET
  Fri, Nov 6) are from the live campaign as captured in
  src/data/promo-data.json (hashtag, end_at). An older HANDOFF note says
  #duxxion: that is not the campaign's hashtag. If the campaign changes, change `DuxAd.tsx`.

## Timeline

| Time | What |
|---|---|
| 0:00 | Hook: his goal celebration, then again zoomed in. "me when my Duxxion clip hits 1M views" |
| 0:01.9 | Record scratch, freeze. "ok wait. let me explain", VHS rewind |
| 0:03.8 | Replay: "meet Duxxion (DUCKS-ee-on)", "watch him cook", slow-mo "wait for it…" |
| 0:09.4 | GOAL on the drop; "…then hits the report button", *BLEEP* |
| 0:11.3 | Duxxion's Campaign, LIVE NOW, "clip him. win cash." |
| 0:13.1 | $500 prize pool, split between the top 3 |
| 0:15.0 | Podium: $150 / $250 / $100, "(that's a lot of burritos)" |
| 0:16.9 | Most views on ONE clip wins; a view counter on a phone |
| 0:18.8 | How to join (free): join on Highlightz, clip his stream, post with #duxhighlightz + add the link; last day to post Thu, Nov 5 |
| 0:24.4 | Highlightz, "Join Duxxion's campaign", highlightz.app, "see you on the leaderboard" |
