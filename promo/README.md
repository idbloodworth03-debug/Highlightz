# Highlightz Campaigns promo

The 30-second video announcing Campaigns, plus a 9:16 cut, a 12-second teaser
and a thumbnail. Everything is code: the edit is [Remotion](https://remotion.dev)
(React), the UI shots are recorded from the real app by a Playwright rig, and
the music and sound effects are synthesized by `audio/make_audio.py`.

The rule this project is built around: **real UI, real data, real clips, or a
labelled placeholder.** Nothing on screen is mocked up or typed in by hand.
Every number the video shows comes from `src/data/promo-data.json`, which is
written only by `capture/fetch-data.mjs` and `capture/capture.mjs` from the live
app. While anything is missing, every render shows a "REAL CAPTURE GOES HERE"
card in place of the shot and a "PREVIEW, NOT FOR RELEASE" mark listing what
is missing. `npm run render:all` refuses to run until nothing is missing.

## Deliverables

| File (in `out/`) | Composition | Size | Length |
|---|---|---|---|
| `highlightz-campaigns-promo-16x9.mp4` | `Promo16x9` | 1920x1080, 60 fps | 30.000 s (1800 frames) |
| `highlightz-campaigns-promo-9x16.mp4` | `Promo9x16` | 1080x1920, 60 fps | 30.000 s |
| `highlightz-campaigns-teaser-12s.mp4` | `Teaser12` | 1920x1080, 60 fps | 12.000 s (720 frames) |
| `highlightz-campaigns-teaser-12s-9x16.mp4` | `Teaser12x9x16` | 1080x1920, 60 fps | 12.000 s |
| `highlightz-campaigns-thumbnail.png` | `Promo16x9`, a still | 1920x1080 | |

H.264, yuv420p, CRF 14, AAC audio at -14 LUFS integrated, true peak at or
below -1 dBTP.

The 9:16 cut is reframed, not cropped: each UI shot is recorded a second time
in the real phone layout (390x844 at 3x, an iPhone user agent), shown in a
phone frame and zoomed to the part of the screen the shot is about; the text
overlays move into glass cards inside the platforms' safe zones (nothing in
the top ~150 px or the bottom ~380 px, where TikTok, Reels and Shorts put
their own UI).

## Timing

Everything is cut to a 128 BPM grid: 1 beat = 0.46875 s, 1 bar = 1.875 s,
16 bars = 30 s. At 60 fps one beat is 28.125 frames; cuts land on the nearest
frame (never more than 8 ms off), sounds on the exact sample. The single
source of truth is `src/timeline.json`: the scenes and the audio both read it,
so a cut and its sound cannot drift apart. Change a beat there and both follow.

| Time | Beats | Scene | What is on screen |
|---|---|---|---|
| 0:00.000 | 0-4 | Hook | Logo glitches in; "Every big moment, already a clip." |
| 0:01.875 | 4-16 | Landing | highlightz.app scrolling: hero, what it catches, ten streams, a whip down to Campaigns. NEW badge; callouts "7 live signals, one score" (3.750 s), "10 streams at once" (4.688 s), "Free to start" (5.625 s) |
| 0:07.500 | 16-36 | The bot | Live Streams: a real channel's score climbing with its signals; the crossing at 10.313 s (one-beat freeze, flash, "CLIP CAPTURED"); the clip lands in Clip Review and is approved (12.188 s); the Clip Editor; two featured clips, each credited (14.063 s, 15.000 s); Post to TikTok (15.938 s) |
| 0:16.875 | 36-44 | Slides | "Set your budget." (with the real range), "Choose prizes + rules.", "Clippers compete.", "Winners get paid.", 0.94 s each |
| 0:20.625 | 44-56 | Campaign | The live campaign's real page; prize pool counts up on the coin (21.094 s), split, hashtags, entries and views, the leaderboard sliding into rank (23.438 s) |
| 0:26.250 | 56-64 | CTA | Logo; "Get your stream clipped."; highlightz.app/promote; "Clipper? Browse the campaigns."; fade over the last beat |

The teaser is the hook, the bot's crossing, the campaign and the CTA: 0-1.875 s,
1.875-5.625 s, 5.625-9.375 s, 9.375-12.000 s.

No text is on screen for less than 0.8 s: the shortest text holds are the
slides, at 0.94 s.

## Setup

Needs Node 22, ffmpeg, Python 3 with numpy and scipy (the audio), Chromium
(Playwright's, for the capture rig and for Remotion), and
[streamlink](https://streamlink.github.io) for the featured clips.

```sh
cd promo
npm ci
pip install numpy scipy
npm run audio                 # writes public/audio/promo-30s.wav and teaser-12s.wav
npm run studio                # the edit in a browser, scrubbable
```

`remotion.config.ts` points Remotion at the Chromium headless shell on the
machine this was built on; anywhere else, delete that line and Remotion
downloads its own.

## Getting the real shots

The machine running these steps must reach `highlightz.app`, `twitch.tv`,
`*.twitch.tv`, `*.ttvnw.net`, `*.jtvnw.net` and `*.twitchcdn.net`.

1. **A demo account.** Create it on /admin (Users → add, a password
   account) and give it Pro. Make it an admin: an admin joining a campaign
   is not counted in the campaign's public joined count, and admin-only
   controls are hidden in every capture (`HIDE_ADMIN_CSS` in
   `capture/capture.mjs`). With it, **join the live campaign** (a campaign's
   page is locked until you join), and for the Post shot connect a TikTok
   account to it. Sign in, and copy the value of the `session` cookie.

   ```sh
   export HZ_SESSION='<the session cookie>'
   ```

2. **Record a real crossing.** With the demo account, start monitoring a
   busy live Twitch channel (Live Streams → Monitor stream), then:

   ```sh
   npm run record-live -- --channel <login>
   ```

   It listens to the dashboard's WebSocket until the bot captures a clip on
   that channel, and writes every `score_update` and the `clip_ready` to
   `capture/recordings/live.json`.

3. **Record the UI.**

   ```sh
   npm run capture
   ```

   Each shot is the real page, stepped one 60 fps frame at a time under
   virtual time (`capture/vt.js`: timers, animation frames, clocks, CSS and
   Web Animations and video all move only when the rig says so), so the
   motion is exact however long a frame takes to screenshot. Frames are taken
   at 2x (3x on the phone) and piped into ffmpeg at CRF 10. The Live Streams
   and Clip Review shots replay the recording from step 2 into the real
   screens in place of the live socket; the cursor clicks Approve for real.
   Nothing is posted. Shots are written to `public/captures/<shot>-<layout>.mp4`,
   and their size, frame count and focus region to the data file.

   `--shots campaign --layouts tall` re-records just one. `--test --base
   <url> --out <dir>` runs the scripts against another server (a local copy)
   and writes only to `<dir>`: never to `public/captures` or the data file.

4. **Fetch the numbers and the clips.**

   ```sh
   npm run fetch-data -- --funded-by Highlightz
   ```

   The budget range from the live /promote calculator, the clip counter
   from /landing/stats, the live campaign as the demo account sees it from
   /campaigns, and the featured clips from /landing/showcase (which already
   drops any channel that opted out), downloaded from Twitch to
   `public/captures/clip-N.mp4`. `--funded-by` sets the "Prize pool from …"
   line, which the API does not carry; leave it off and the line is not shown.
   Entrant names are masked (`j•••••`) while `mask_entrant_names` is true in
   the data file.

5. **Check and render.**

   ```sh
   npm run check-data            # lists anything still missing
   npm run render:all            # all four deliverables, only when nothing is
   ```

6. **Watch it frame by frame.** A contact sheet of every 15th frame:

   ```sh
   ffmpeg -i out/highlightz-campaigns-promo-16x9.mp4 -vf "select='not(mod(n,15))',scale=480:-1,tile=6x20" -frames:v 1 out/sheet.png
   ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt,duration -of compact out/highlightz-campaigns-promo-16x9.mp4
   ffmpeg -i out/highlightz-campaigns-promo-16x9.mp4 -af ebur128=peak=true -f null - 2>&1 | tail -12
   ```

   Check every frame showing a real page for anything private: emails,
   tokens, billing, payout details, admin-only controls. The captures run
   signed in as the demo account, so its own email is the one to look for.

## Guardrails, and where each is enforced

- **Real or placeholder, never invented.** `src/data/promo-data.json` holds
  only what the scripts read from the live app; `src/missing.js` lists what is
  still absent, the preview shows it, and `capture/check-data.mjs` blocks the
  release renders on it.
- **No private data.** The capture account is a demo account; admin-only UI
  is hidden by CSS during capture; the campaign's leaderboard names are
  masked unless the owner switches `mask_entrant_names` off.
- **Only clips from channels that have not opted out, credited.** Featured
  clips come only from /landing/showcase, which filters opted-out channels at
  read time; every clip on screen carries "CLIP OF {channel}" and its
  platform, up within its first 0.1 s and held until the cut.
- **No claims the product does not make.** The copy describes what the app
  does (signals, a score, a threshold, clips, review, an editor, posting,
  campaigns). It never calls the product AI.

## Music

The track is original: every drum, bass note, chord, riser and sound effect is
synthesized in `audio/make_audio.py` (F minor, 128 BPM, 16 bars), so there is
no licence to track. Sound effects sit on the cuts (whoosh, hit, pop, click,
tick, coin, glitch), the music ducks under each, and the mix fades over the
last beat. To use a licensed track instead:

```sh
python3 audio/make_audio.py --music path/to/track.wav [--teaser-music path/to/teaser.wav]
```

The WAV must be 128 BPM with its first downbeat at 0.000 s; it is resampled,
cut to length, and gets the same ducking, fade and loudness.

## Licences

- **Remotion** is free for individuals, non-profits and companies of up to
  three people; a larger company needs a Remotion company licence
  (https://remotion.dev/license) before rendering this for commercial use.
- Fonts (Sora, IBM Plex Mono, Lobster) and the logo in `public/brand/` are
  copies of the app's own files in `src/dashboard/static/`.

## Layout

```
src/timeline.json       the beat grid: scenes, shots, sound cues
src/data/promo-data.json  the real data (written by the scripts only)
src/missing.js          what the release still lacks
src/scenes/             Hook, Landing, Bot, Slides, Campaign, Cta
src/kit/                frames, phone, UI shot camera, effects, look
src/Promo.tsx           the 30 s cut and the teaser; Root.tsx registers them
audio/make_audio.py     music, SFX, ducking, master
capture/rig.mjs         browser, virtual time stepping, cursor, recording
capture/vt.js           virtual time, injected into the page
capture/record-live.mjs real score updates up to a captured clip
capture/capture.mjs     the UI shots
capture/fetch-data.mjs  the numbers and the featured clips
capture/check-data.mjs  the release gate
```
