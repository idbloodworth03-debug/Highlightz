# Highlightz — Development Roadmap

---

## Phase 1: Cloud Deployment (Current Priority)

### 1.1 Provision the Server
- Spin up a DigitalOcean Droplet (Ubuntu 22.04, $12/mo, 2GB RAM recommended)
- Point a domain at the droplet IP (A record)
- Run `bash /opt/highlightz/deploy/setup.sh` — everything is already written, just needs a live server

### 1.2 Harden the Setup
- **Fix the systemd service** — currently runs as `root`, should run as a dedicated `highlightz` user (least-privilege)
- **HTTPS via certbot** — `certbot --nginx -d yourdomain.com` after Nginx is up
- **Firewall** — only expose ports 80/443; Redis and uvicorn stay internal
- **Secret rotation** — generate a real `DASHBOARD_SECRET_KEY` (32+ random bytes) and strong `DASHBOARD_PASSWORD` in `/opt/highlightz/.env`

### 1.3 Persistence & Reliability
- **Concurrent write safety** — the JSON files (`clips.json`, `streams.json`, profiles) have no locking right now; under multiple simultaneous streams this is a race condition waiting to happen. Replace with SQLite + aiosqlite (zero external dependencies, still file-based)
- **Stream auto-recovery** — if FFmpeg or streamlink crashes mid-stream, StreamWorker should retry with backoff instead of silently dying
- **Redis persistence** — enable `appendonly yes` in Redis config so the job queue survives a server reboot
- **Log rotation** — configure journald or logrotate so logs don't fill the disk over time

### 1.4 Remote Access Quality-of-Life
- **Dashboard auth hardening** — add brute-force rate limiting on `/login` (currently there is none — someone can hammer it infinitely)
- **HTTPS WebSocket** — Nginx config already has upgrade headers; verify `wss://` works end-to-end after certbot
- **Mobile-friendly dashboard** — the current UI is desktop-only; add a responsive viewport + touch-friendly approve/reject buttons so you can review clips from your phone

---

## Phase 2: Auto-Edit & Clip Enhancement

### 2.1 Auto-Generated Clip Titles
- After a clip is created, run a **title generation prompt** against the Claude API using the clip's metadata (channel name, game, top keywords that fired, trigger score, chat snapshot)
- Example output: `"xQc goes insane on Valorant — 94pt highlight"` or `"PogChamp moment: sub train + audio spike"`
- Store as `clip.generated_title` in metadata; show it on the clip card with an edit-in-place field so you can tweak before posting

### 2.2 Auto-Generated Clip Descriptions & Tags
- Same Claude API call, but also generate:
  - A short description (2–3 sentences) for YouTube/TikTok
  - 5–10 hashtags based on game + keywords
  - A category suggestion (funny, hype, fail, clutch, etc.)
- These feed directly into the auto-post step below

### 2.3 Thumbnail Generation
- Extract a **key frame** from the clip at the highest-audio-spike moment (already detectable via `get_audio_level_db()`)
- Optionally overlay the clip title as text using FFmpeg's `drawtext` filter — no external tools needed
- Store as `clip.thumbnail_path`; show thumbnail preview on the clip card

### 2.4 Clip Trimming UI
- Add a **trim handles** video player in the dashboard (using the HTML5 `<video>` element + a range slider overlay)
- Let you drag the start/end points and hit "Re-cut" — triggers a new `extract_clip()` call with custom pre/post roll
- Useful when the auto-clip grabbed slightly too much silence at the start

### 2.5 Highlight Reel Compilation
- At the end of a stream session (or on demand), concatenate the approved clips into a single **highlight reel** MP4 using FFmpeg concat
- Add a simple title card between clips (channel name, timestamp) via FFmpeg `drawtext`
- Configurable: longest clip first, chronological, or score-sorted

---

## Phase 3: Auto-Post

### 3.1 TikTok Auto-Post
- Use the **TikTok Content Posting API** (requires applying for API access)
- On approve (or auto-approve above threshold X), upload the MP4 with generated title + hashtags
- Rate-limit to N posts/day configurable per channel

### 3.2 YouTube Shorts Auto-Post
- Use the **YouTube Data API v3** (`videos.insert`) — credentials are already partially wired in (`youtube_api_key` in settings)
- Auto-format: clips ≤60s go as Shorts, longer clips go as regular uploads
- Metadata: generated title, description, tags, thumbnail, category ID

### 3.3 Twitter/X Auto-Post
- Use the **Twitter API v2** (`media/upload` + `tweets`)
- Post clips ≤2m20s directly as video tweets
- Template: `"{title} on {channel} 🎮 #{game} #twitch #highlight"`

### 3.4 Discord Webhook Post
- Simplest integration — no OAuth needed, just a webhook URL
- On clip approval, POST an embed with: clip thumbnail, title, score breakdown, approve/reject buttons (Discord components)
- Good as a notification layer even before the social APIs are wired up

### 3.5 Post Queue & Scheduling
- Add a **post queue** in the dashboard: clips sit in "ready to post" state; you can batch-approve and schedule them (e.g., "post 3 TikToks today at 12pm, 6pm, 9pm")
- Prevents spamming all platforms simultaneously and lets you review auto-generated titles before they go live

---

## Phase 4: Signal & Detection Improvements

### 4.1 Better Audio Analysis
- Currently uses `volumedetect` (average dB) — upgrade to **spectral analysis**: detect crowd noise vs. music vs. speech vs. silence more accurately
- Use `ffmpeg -af astats` for peak vs. RMS distinction — catches sudden explosive moments that average-dB misses

### 4.2 On-Screen Event Detection (Computer Vision)
- Run a lightweight **YOLO or OCR pass** on the key frame to detect:
  - Kill feed / death screen
  - Score changes
  - "Victory" / "Defeat" overlays
  - Twitch alerts (sub/donation popups visible on stream)
- Adds a 7th signal: `VISUAL_EVENT` — highest-confidence detection

### 4.3 Game-Aware Signal Profiles
- Different games have wildly different "hype" patterns (Minecraft vs. Valorant vs. Just Chatting)
- Detect the current game via Twitch API (`game_name` field already available) and load a **game-specific signal preset** on top of the learned weights
- Example: Valorant preset boosts `AUDIO_SPIKE` and `SILENCE_BURST`; Just Chatting boosts `CHAT_VELOCITY` and `SENTIMENT`

### 4.4 Multi-Language Keyword & Sentiment Support
- VADER only works in English — add `langdetect` + a multilingual sentiment model (e.g., `transformers` with `cardiffnlp/twitter-xlm-roberta-base-sentiment`) for Spanish, Portuguese, Korean streamers
- Keyword lists should also be localizable per streamer

### 4.5 Emote Weighting
- Twitch emotes carry huge sentiment signal that VADER ignores (KEKW, PogChamp, Sadge, etc.)
- Build a **Twitch emote sentiment map** — score each emote and factor it into the SENTIMENT signal

---

## Phase 5: Dashboard & UX

### 5.1 Analytics Page
- Per-streamer stats: approval rate over time, average trigger score of approved vs. rejected clips, which signals fire most for each streamer
- Line chart of trigger score over a session (already being broadcast over WebSocket — just needs a chart renderer like Chart.js)
- "Best clips of the week" summary

### 5.2 Multi-User Auth
- Currently single-password for everyone — add role-based access:
  - **Admin**: full control, can change settings and weights
  - **Reviewer**: can only approve/reject clips
  - **Viewer**: read-only dashboard
- Simple user table in SQLite (ties into Phase 1.3 DB migration)

### 5.3 Mobile App / PWA
- Convert the dashboard to a **Progressive Web App** (add `manifest.json` + service worker)
- Push notifications: "New highlight detected on xQc — 91pts" sent to your phone the moment a clip is created
- Review clips from your phone while away from desk

### 5.4 Clip Search & Filter
- Full-text search across clip titles, keywords, and channel names
- Filter by: score range, date, platform, approval status, game, signal type that triggered it
- Export filtered results as a CSV or ZIP of MP4s

---

## Phase 6: Scale & Infrastructure

### 6.1 Replace JSON Persistence with SQLite → PostgreSQL
- SQLite first (zero-setup, same server) → PostgreSQL when you need concurrent writes from multiple workers
- Schema: `streams`, `clips`, `profiles`, `post_history`, `users`

### 6.2 Horizontal Scaling
- Currently maxes at 20 concurrent streams per instance (`max_concurrent_streams` in settings)
- Worker pool: deploy multiple instances of the stream worker behind a Redis job queue, all sharing the same DB and storage
- DigitalOcean managed Redis + Spaces (S3-compatible) makes this straightforward

### 6.3 S3/Spaces Storage
- Local storage on a $12 droplet will fill up fast with 60s MP4s at 1080p
- Wire up `STORAGE_BACKEND=s3` with DigitalOcean Spaces — already coded in `src/output/storage.py`, just needs credentials in `.env`
- Clips expire from Spaces after 30 days (lifecycle rule) unless permanently saved

### 6.4 Monitoring & Alerting
- Add a `/metrics` endpoint (Prometheus-compatible) exposing: active streams, clips per hour, queue depth, FFmpeg process health
- Plug into Grafana Cloud (free tier) for dashboards and PagerDuty/Discord alerts if the service crashes

---

## Priority Order (Recommended)

| # | Task | Effort | Impact |
|---|------|--------|--------|
| 1 | Provision server + deploy | Low | Unblocks everything |
| 2 | Fix JSON race conditions (SQLite) | Medium | Stability |
| 3 | Stream auto-recovery on crash | Low | Reliability |
| 4 | HTTPS + rate-limit login | Low | Security |
| 5 | Discord webhook on new clip | Low | Instant visibility |
| 6 | Auto-generate clip titles (Claude API) | Medium | High quality-of-life |
| 7 | Thumbnail from key frame | Medium | Needed for social posts |
| 8 | YouTube Shorts auto-post | Medium | Core feature |
| 9 | TikTok auto-post | Medium | Core feature |
| 10 | Trim UI in dashboard | Medium | Polish |
| 11 | Emote sentiment weighting | Low | Signal quality |
| 12 | Game-aware signal presets | Medium | Detection accuracy |
| 13 | Highlight reel compilation | Medium | Content creation |
| 14 | Analytics page | Medium | Insights |
| 15 | Computer vision signal | High | Advanced detection |
