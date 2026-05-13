# Highlightz — Claude Memory

## What This Project Is
Highlightz is an automatic stream highlight detection and clipping bot for Twitch/YouTube. It monitors live streams 24/7, scores each moment 0–100 every second using 6 signals, auto-clips when the score exceeds a per-streamer threshold, and shows everything on a local web dashboard at http://localhost:8000.

## Repository
- GitHub: https://github.com/idbloodworth03-debug/Highlightz
- Restricted to repo: `idbloodworth03-debug/Highlightz`
- Development branch: `claude/review-codebase-Lt9y8` — all changes go here, never push directly to `main`

## How to Run Locally (Windows)
- Double-click `start.bat`
- Requires Python at `C:\Python314\python.exe`
- Requires Redis running
- FFmpeg at `C:\Users\Ian\AppData\Local\CapCut\Apps\6.1.2.2338\ffmpeg.exe`
- Dashboard opens at http://localhost:8000

## Key Files
| File | Purpose |
|------|---------|
| `src/dashboard/api.py` | FastAPI dashboard, WebSocket, all REST endpoints, full HTML/JS UI, auth |
| `src/trigger/engine.py` | Scoring engine — 6 signals → composite score 0–100 |
| `src/profiles/profile.py` | StreamerProfile dataclass — per-channel baselines, signal weights, learning logic |
| `src/ingestion/stream_worker.py` | Orchestrates one stream end-to-end |
| `src/ingestion/video_buffer.py` | FFmpeg HLS buffering via streamlink |
| `src/processor/clip_processor.py` | Extracts MP4 clips from buffer segments |
| `config/settings.py` | All settings loaded from .env |
| `start.bat` | Windows launcher |
| `ROADMAP.md` | Full development roadmap (phases 1–6) |

## Scoring Formula
- Chat Velocity: 40pts, Audio Spike: 30pts, Keywords: 20pts, Sentiment: 10pts, Viewer Spike: 10pts, Silence-then-Burst: 10pts
- ×1.25 multiplier if 3+ signals active simultaneously
- +15 flat bonus if sub/raid in last 30s
- Capped at 100
- Clip = 45s pre-roll + 15s post-roll, 60s cooldown between clips
- Default trigger threshold: 60 (range 30–90, adapts per streamer)

## Per-Streamer Learning
- Each streamer has a profile JSON at `clips/profiles/<channel>.json`
- `signal_weights` dict (keys: CHAT_VELOCITY, AUDIO_SPIKE, KEYWORD, SENTIMENT, VIEWER_SPIKE, SILENCE_BURST), default 1.0, range 0.3–2.5
- Approve a clip → boosts weights of signals that fired strongly (+nudge, learn rate 0.08)
- Reject a clip → reduces those weights
- Threshold: approved −2pts (min 30), rejected +3pts (max 90)
- Completely isolated per streamer

## Persistence
- Clips: `clips/clips.json`
- Streams: `clips/streams.json`
- Profiles: `clips/profiles/<channel>.json`
- WARNING: No concurrent write locking — race condition risk with multiple simultaneous streams (SQLite migration is on the roadmap)

## Auth
- Login page at `/login`
- Password set via `DASHBOARD_PASSWORD` in `.env` (default: "highlightz")
- Uses Starlette SessionMiddleware + itsdangerous cookies
- `DASHBOARD_SECRET_KEY` must also be set in `.env`
- No brute-force rate limiting yet (on roadmap)

## Cloud Deployment (Not Done Yet)
- Plan: DigitalOcean Droplet ($12/mo Ubuntu 22.04) + domain pointed to droplet IP
- Deploy files are in `deploy/` folder: `setup.sh`, `highlightz.service`, `nginx.conf`, `env.production`
- Server setup: `git clone https://github.com/idbloodworth03-debug/Highlightz.git /opt/highlightz && bash /opt/highlightz/deploy/setup.sh`
- Then edit `/opt/highlightz/.env`, run `systemctl start highlightz`, run `certbot --nginx -d yourdomain.com`
- This is the current top priority — server has not been created yet

## Known Issues / Gotchas
- `score display` in JS uses `Math.round(c.trigger_score)` — trigger_score is already 0–100 scale
- Streamlink must be installed to `C:\Python314\Lib\site-packages` (not user packages) — use admin CMD: `C:\Python314\python.exe -m pip install streamlink --target C:\Python314\Lib\site-packages`
- CapCut FFmpeg has no `ffprobe.exe` — `_probe_duration()` catches the error and returns 0.0
- `dashboard_password` and `dashboard_secret_key` must be set in `.env` for auth to work
- JSON persistence has no write locking — concurrent stream workers can corrupt state

## Roadmap Summary
See `ROADMAP.md` for full detail. High-level phases:
1. **Cloud Deployment** — provision server, harden setup, fix persistence, remote access
2. **Auto-Edit** — AI-generated titles/descriptions/tags, thumbnail extraction, trim UI, highlight reels
3. **Auto-Post** — TikTok, YouTube Shorts, Twitter/X, Discord webhook, post scheduling
4. **Signal Improvements** — better audio analysis, computer vision (YOLO), game-aware profiles, multilingual sentiment, emote weighting
5. **Dashboard & UX** — analytics page, multi-user auth, PWA/mobile, clip search & export
6. **Scale & Infrastructure** — SQLite→PostgreSQL, horizontal scaling, S3/Spaces storage, Prometheus metrics

## Next Steps (as of last session)
- Provision the DigitalOcean droplet and run `deploy/setup.sh`
- Investigate original git history — local repo at `C:\Users\Ian\Desktop\SuperClipBot` may have full commit history that was lost when GitHub repo was initialized fresh
