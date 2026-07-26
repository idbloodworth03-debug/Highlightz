# Session handoff — project state & hard-won knowledge

Read this before making changes. `CLAUDE.md` has the binding engineering
rules; this file is the context behind them. Last updated: **2026-07-10**
(landing type redesign, trial-system rework, full prod audit + hardening).

## Production environment (facts, verified 2026-07-10)

- Prod runs at `/opt/highlightz` on a DigitalOcean droplet (1vCPU/2GB,
  IP 137.184.24.121). Operator is **root** — never use sudo in commands.
- Deploy: `cd /opt/highlightz && git fetch origin && git reset --hard origin/claude/magical-feynman-7Sp19 && systemctl restart highlightz`
  - **Deploys never run `pip install`** — adding a dependency to
    requirements.txt requires a manual `venv/bin/pip install -r
    requirements.txt` on the droplet, or prod crash-loops on ImportError.
- Working branch: `claude/magical-feynman-7Sp19` (push here, never elsewhere).
- Prod Python: `/opt/highlightz/venv/bin/python`.
- **Service runs as the unprivileged `highlightz` user** under the hardened
  unit (`deploy/highlightz.service`: ProtectSystem=strict, NoNewPrivileges,
  PrivateTmp, only `clips/` writable). **`.env` must stay
  `640 root:highlightz`** — systemd reads EnvironmentFile as root, but
  pydantic-settings ALSO opens `.env` from the process; a 600 root:root file
  crash-loops the service (bit us live on 2026-07-10; runbook is in the
  unit-file comments).
- **Firewall**: ufw active, default-deny incoming, only 22/80/443 allowed.
  Uvicorn additionally binds `127.0.0.1:8000` (DASHBOARD_HOST setting,
  default 127.0.0.1; the Dockerfile overrides to 0.0.0.0 for port mappings).
- **TLS**: certbot cert for highlightz.app; auto-renewal timer verified
  active (fires twice daily).
- **Backups**: nightly cron `10 4 * * *` → `src.maintenance.backup`
  (installed + first archive verified 2026-07-10). **No off-site copy yet**
  — set BACKUP_S3_BUCKET/BACKUP_S3_ENDPOINT + AWS keys (DO Space) in .env.
- **Token encryption**: TOKEN_ENCRYPTION_KEY is set on prod; all 28 stored
  OAuth token fields were rotated onto it with
  `src.maintenance.rotate_token_key --apply` (2026-07-10). Session key and
  token key are now independent — DASHBOARD_SECRET_KEY may be rotated
  freely. Delete `clips/users.json.pre-rotation` (old-key snapshot) once
  clipping is confirmed healthy for a few users.
- ADMIN_TWITCH_ID intentionally unset — the owner declined setting it.
  Admin is granted via `python -m src.auth.grant_admin <username>`.
- DNS is on **DigitalOcean** (Namecheap is registrar only). Google Search
  Console: domain property verified via DO TXT record; sitemap.xml submitted
  and accepted. Do not remove the TXT record or robots/sitemap endpoints.

## Non-negotiable product constraints

- **No video recording/re-hosting, ever.** Clips are real Twitch clips made
  via the official Helix API with the user's own token; Twitch hosts
  everything. This is the compliance moat — rejected repeatedly for both
  60s clips and Kick.
- Twitch clips are ~30s, hard API limit (no duration param on Create Clip).
  Captured buffer ~90s; creators can extend to 60s only in Twitch's browser
  editor (edit_url currently discarded — storing it + an "Extend to 60s"
  button was designed but not built).
- Twitch's embedded player gates mature channels for logged-out viewers
  (shows "clip is no longer available" though the clip is fine). Dashboard
  keeps the inline embed + "Watch on Twitch" fallback bar; landing showcase
  uses thumbnails + lightbox with a Twitch escape hatch; mobile links out
  (embeds break in many mobile browsers — Error #4000).
- Broadcasters/mods delete clips. The 6-hour dead-clip sweep
  (sweep_dead_clips_task) removes Twitch-deleted clips and prunes the
  showcase. Fail-safe: a failed lookup deletes NOTHING; `first=100` is
  explicit so pagination can't read as deletion.
- Kick: no public clip-creation API (verified June 2026). Kick sign-in
  disabled (Twitch is the only sign-in; Kick linking still works). Kick UI
  gated behind under-construction screen; Kick scrubbed from marketing.

## Billing (Stripe) — current design (TWO TIERS since 2026-07-11)

- **Two plans**: Starter $10/mo (3 monitored streams, 50 pending clips, no
  VOD scanner) and Pro $25/mo (10 streams, 200 pending, VOD scanner).
  Plumbing: `src/billing/plans.py` (PLAN_LIMITS + get_plan), price ids in
  settings (STRIPE_PRICE_ID_STARTER / STRIPE_PRICE_ID_PRO; legacy
  STRIPE_PRICE_ID = the $15 era, mapped to 'pro' — **existing subscribers
  are grandfathered as Pro**). The webhook reads the subscription's price id
  (extract_price_id → plan_for_price) and stores `plan` on the user, so
  portal upgrades/downgrades take effect automatically. Enforcement is
  backend-side: add_stream limit, pending-clip eviction cap, 403 on
  /vod/analyze; /me exposes plan + limits and the dashboard mirrors them
  (VOD screen shows an upgrade card for Starter — gate placed AFTER hooks,
  React hook-order). Admins and admin-granted trials get Pro features.
  Checkout: /billing/checkout?plan=starter|pro; plan SWITCHING for active
  subscribers goes through the Stripe portal. Paywall + landing show both
  tiers. The portal is **self-configuring**: if the Stripe account has no
  saved Customer Portal configuration (sessions.create fails account-wide
  until one exists — this is why "Manage billing" used to 500),
  create_portal_url builds one via the API (cancel at period end, card
  update, invoice history, Starter↔Pro switching when both price ids are
  set) and retries once, caching the config id. If Stripe still fails,
  /billing/portal renders a branded "temporarily unavailable" page with a
  back link — never a raw 500. No dashboard portal setup is required
  anymore; a manually saved config, if one exists, is used untouched.
- **Billed immediately. There is NO self-serve free trial.**
  trial_period_days, the trial-claims ledger (`trial_claims.json` helpers),
  and every "7 days free" promise were removed from checkout, landing,
  login, paywall, TOS, FAQ, JSON-LD, and meta tags. The prod
  trial_claims.json file is inert.
- **Free access exists only as an admin-granted timed trial**: /admin panel
  → "Trial…" dropdown per user (3d/1w/2w/1m/3m) → POST
  `/admin/users/{id}/grant-trial {days:1..365}`. Sets app-managed
  `subscription_status="trialing"` + `trial_ends_at`; **no Stripe
  involvement**. Expiry rides the existing enforcement (auth middleware +
  idle reaper): flips to 'expired', stops streams, broadcasts
  subscription_expired live. Re-granting extends/replaces the window from
  now. The grant broadcasts subscription_active so an open paywall tab
  unlocks without refresh. Endpoint rejects admins and active subscribers.
- Users on an admin trial CAN reach checkout to subscribe early — the
  checkout guard hard-blocks only status "active"; the live-Stripe check
  still prevents double subscriptions.
- Paywall copy variants (`_paywall_copy`): new ("Get Highlightz Pro") /
  returning ("Restart your subscription") / trial_ended ("Your free trial
  has ended"). None promise free days — locked by tests.
- Webhook: signature-verified, idempotent (TTL + lock), and
  apply_subscription_event resolves the ACTUAL affected user on customer
  mismatch. Checkout always reuses the stored Stripe customer.
- Promo codes: allow_promotion_codes on; 50%-off-first-month coupon exists.
  Planned streamer partnership: per-streamer code + $5/paid signup (manual
  payout, Stripe redemption count is source of truth).

## Trigger formula — state and history (RETUNED 2026-07-10, evidence-based)

**First data-driven retune**, from `src.maintenance.analyze_training_log`
run against prod's 806 labeled outcomes (17 approved / 724 rejected /
65 expired; July was firing ~60 clips/day at 0.6% approval):

- KEYWORD weight 12 → 4 (keyword-led clips went 0/91 approved, AUC 0.51).
  VOD threshold scale retuned 0.50 → 0.42 to compensate (shared
  CHAT_WEIGHTS shrink VOD's chat-only ceiling ~57.6 → ~48).
- VIEWER_SPIKE weight 7 → 15 (best separator: AUC 0.73; viewer-led clips
  approved at 10%, 4-5× base rate). Total weight pool stays 105 → score
  scale and learned thresholds preserved.
- Dry-spell floor 52 → 60 and adaptive-threshold floor 30 → 50 (no approved
  clip has EVER scored below 60; keepers average 90.8; floor 60 would have
  cut 82 junk clips with zero keeper loss).
- Known-but-deferred (round 2, wants a month of post-retune data): audio &
  velocity signals are SATURATED (junk-class means 0.86/0.84 — pinned near
  max for everything that fires, AUC only 0.58/0.60). Real fix is widening
  measurement ranges (e.g. audio 15 → ~20 dB), but that rescales every
  learned threshold — bigger blast radius. EMOTE_HOMOGENEITY never fires in
  practice (mean 0.001 — ramp may start too high); SILENCE_BURST near-inert.
  Weight-learning proper still gated on positives (17 << 60 per class).
- Rerun the analyzer anytime: `venv/bin/python -m
  src.maintenance.analyze_training_log [--days N] [--channel X]`. Caveats:
  dataset only contains FIRED moments (can't see misses), and labels are
  the owner curating test channels (approve bar = showcase-worthy).

**Learning redesign (2026-07-11, owner-directed — "it adjusts too hard")**:
the old approve/reject mechanics had two proven failure modes. DEAD: each
reject raised the threshold +2 AND cut every fired signal's weight (floor
0.3); once max-achievable-score fell below the threshold nothing could ever
fire again — weights only recovered via approvals, which need fires. STALE:
approvals grew weights toward 2.5x, locking the profile onto the shape of
past keepers. New mechanics: weight bounds [0.75, 1.5] (at the floor,
max raw ≈ 82 > threshold ceiling 80 → death-by-weights impossible; the cap
bounds archetype lock-in), hourly mean-reversion of weights toward 1.0
(profile.decay_weights, called next to the threshold decay — history fades
in ~days without new reviews), asymmetric steps (reject +0.75 was +2 —
~27 consecutive rejects to reach the ceiling; approve stays −2; weight
nudges 0.06 approve / 0.02 reject, was 0.08 both). from_dict clamps legacy
out-of-range weights on load, so historically crushed profiles revive on
next use. Locked by tests/test_learning_stability.py.

**Volume pass 2 (2026-07-11, owner-directed — "clip a tad more, even if not
amazing")**: the real choke was preset COOLDOWNS, not thresholds — variety
allowed one clip per 10 min, default one per 4 min, regardless of stream
quality. Cooldowns halved across presets (default 240→120s, variety
600→300, small 360→180, irl 900→480, fps 210→120, moba 300→180, chess
240→150, casino 180→120, sports 600→300; channel overrides already at 90s
untouched). Preset seed thresholds trimmed ~4-6 pts (default 68→63, fps
65→61, moba 66→62, variety 64→60, sports 62→58, chess 58→56, casino
56→54) — the hourly decay pulls existing channels toward the new seeds
gradually. Calibration gate 100→60 samples (~3 min) so a session's FIRST
clip lands sooner (stored profiles keep their persisted target; only new
profiles get 60). Floors (60 dry-spell / 50 learn) unchanged.

**Volume rebalance (2026-07-11, owner-directed)**: the retune over-quieted
things and audio dominance meant quiet moments (chat erupting over silent
gameplay) structurally couldn't fire. Owner wants more clip volume WITHOUT
lowering thresholds. Changes: CHAT_VELOCITY 22→36 (baseline-relative, works
for quiet and loud channels alike), AUDIO_SPIKE 38→24 (was saturated at
~0.86 on junk, AUC 0.58 — loudness now supports, never gates),
SILENCE_BURST 12→14, EMOTE_HOMOGENEITY 9→12, multi-signal bonus 1.2→1.25.
Total pool 105→110. VOD scale re-anchored 0.42→0.62 (rule: 0.50 × new chat
ceiling / 57.6). Net effect: loud chat-hype fires slightly more (78 vs 75),
quiet chat-hype goes from never-fires (43) to fires-with-support (57-66),
loud-but-chat-dead drops further (25). Clip titles reworked at the same
time: dominance rule (a signal is named only when clearly leading; near-ties
say "Everything Pops Off At Once"; weak activity says "Hype Moment") with
accurate labels ("Chat Erupts", "Silence, Then Chaos", "Viewers Flood In").

- Clip at the TOP of the trigger (3s settle to catch the crest). The
  decay-wait/double-peak dwell (8–45s) produced flat aftermath clips and was
  reverted. Do not reintroduce waiting-for-decay.
- Spike-aware baseline (profile.update_velocity): readings ≥2× mean barely
  move the baseline (ratio gate, variance frozen during spikes). A z-score
  gate was tried and rejected (feedback trap).
- Threshold bounds: approve −2 / reject +2, clamped [50, 80]; 80 stays below
  the 85 emergency override. Dry-spell recalibration: −2 per 15 min, floor
  60 (history: −3/10min floor-40 caused the "clipping poorly" incident;
  floors raised 30→50 / 52→60 in the July 2026 data-driven retune).
- Feedback loop uses pm.load() (a cache-only get() silently dropped most
  approvals for months — thresholds ratcheted up-only). reset_feedback has
  --raise-floor mode.
- Training log (clips/training_log.jsonl): every clip outcome with its
  signal vector. Weight-learning ("#4") is deliberately NOT built — needs
  ~150 labeled examples with 60+ of each class. Don't fit weights on
  esports-paper priors.
- VOD scanner: profile-aware (learned threshold ×0.5, learned spike
  multiplier) + top-K (~3 moments/hour, max 12), one-moment-per-run dedup,
  90s cooldown.

## Training Studio (blind human scoring — added 2026-07-17)

Team-only side of the dashboard for calibrating the formula against human
judgment. A `is_labeler` role (granted from the admin panel, "Make Trainer";
NOT admin; labelers bypass the billing gate — they're the owner's team) shows
a Training nav item. The screen serves the labeler's own clips BLIND —
`/training/queue` strips trigger_score, signals, virality, review status and
even the generated clip_title (titles name the bot's dominant signal). The
human rates 1-10 sliders on the dimensions a viewer can actually judge:
sentiment, audio, and virality. (Chat-velocity and keyword sliders were
REMOVED 2026-07-23 at the owner's request — humans can't honestly rate
message-rate spikes from a 30s clip, so those scores were dataset noise.
Historical records keep the old keys; the analyzer reads whichever keys a
record has.) `/training/score` joins the bot's hidden signal
vector + scores SERVER-SIDE at save time into `clips/human_scores.jsonl`
(append-only, one score per clip per labeler, auto-included in backups).
Analysis: `venv/bin/python -m src.maintenance.analyze_human_scores` —
per-dimension Spearman correlation human-vs-bot, biggest disagreements,
per-labeler counts. The eventual goal: fit signal weights on this paired
data once there's volume (the same ~60+/class bar as the training log).
Caveat: blindness relies on labelers not cross-checking the same clip in the
normal Clip Review screen, which still shows scores.

## Frontend / pages

- Dashboard = one Babel-standalone React string in `aurora_html.py` —
  no bundler. A JSX error white-screens everything → ALWAYS extract the
  babel block and compile with @babel/preset-react before pushing.
- Landing = LANDING_HTML string in `api.py` (plain string, no f-string
  braces; the string in api.py is canonical).
- **Typography (NEW 2026-07-10)**: Anton (display — 3D block-letter
  extrusion via layered text-shadow; hollow neon accent words via
  -webkit-text-stroke with an @supports fallback) + Sora (body, variable
  weight). Self-hosted woff2 in `src/dashboard/static/fonts/` (preloaded,
  font-display swap) — no Google Fonts requests. The og-card.png was
  already made with Anton, so the site matches its own share card. Mobile
  heading sizes retuned in the 680px media query. The tracked
  Anton-Regular.ttf is the og-card source font — keep it.
- CSS traps: `.wrap` (class) beats `section` (type) on the padding
  shorthand — sections use longhand padding. Grid `1fr` means
  minmax(auto,1fr): mobile relies on minmax(0,1fr) + min-width:0 chains.
- **Showcase curation (admin)**: the dashboard has an admin-only **Landing
  Page** tab (NAV `adminOnly`, `LandingScreen` in aurora_html.py) listing
  what's live on the marketing page with Remove / ↑ / ↓ plus an "approved
  clips you can add" list filtered by streamer. Backed by
  POST /admin/showcase/{id} (toggle) and /admin/showcase/{id}/move?dir=.
  Cap is `_SHOWCASE_MAX` (8) and adding past it **409s** rather than
  silently evicting the oldest. Both endpoints broadcast
  `showcase_updated`; the ws handler re-pulls /landing/showcase so every
  admin tab (and the clip modal's Feature button) stays in sync. The
  per-clip Feature button in ClipModal still works — same endpoint.
  Showcase grid on the landing page is centered flex (partial rows centre).
- Landing features: animated capture demo, live clips counter
  (/landing/stats, monotonic clip_counter.json), admin-curated showcase
  (/landing/showcase + lightbox), FAQ (10 items — "How does billing work?"
  replaced the trial question), $15 pricing with promo hint, og/twitter
  cards, JSON-LD (SoftwareApplication + FAQPage), robots.txt + sitemap.xml;
  login/paywall are noindex.

## Verification workflow (what "done" means here)

1. `python -m pytest tests/` — ~90 tests, all green.
2. JSX: extract babel block → babel.transformSync with preset-react.
3. Render in headless Chromium (Playwright, executablePath:
   '/opt/pw-browsers/chromium'). unpkg/jsdelivr are BLOCKED by the sandbox
   proxy — vendor React/ReactDOM/Babel locally. Stub API routes with fixture
   JSON; register catch-all Playwright routes BEFORE specific ones (matching
   is newest-first). Dismiss the first-run welcome modal before interacting.
4. Real screenshots at 1440px and 360–390px, eyeballed, before any deploy
   command is given. Known cosmetic: +6px scrollWidth at 360px from the hero
   demo glow — pre-existing, clipped by body overflow-x:hidden.

## Maintenance commands (server)

- Backup now: `venv/bin/python -m src.maintenance.backup`
  (nightly cron installed: `10 4 * * *`, logs to backups/backup.log).
- Token key rotation (already applied; keep for future rotations):
  `venv/bin/python -m src.maintenance.rotate_token_key [--apply]` —
  dry-run default, snapshots users.json, idempotent.
- Threshold maintenance: `venv/bin/python -m src.profiles.reset_feedback`
  (dry-run default) / `--raise-floor` (surgical) / `--apply`.
- Data lives in `clips/` (users.json, clips.json, profiles/,
  training_log.jsonl, clip_counter.json, showcase.json) — untouched by
  git reset deploys; owned by the `highlightz` user.

## July 2026 audit — remaining open items

- **Off-site backup** (HIGH): local tar only today; set BACKUP_S3_* to a DO
  Space — zero code changes needed.
- Redis pub/sub listener has no reconnect: a Redis blip kills the listener
  task → whole process exits → systemd restarts (~10s blip, sockets drop,
  streams restore on boot). Clip processor retries connection errors with
  no backoff (tight error-log loop during outages).
- requirements.txt is fully unpinned; no CI runs the test suite.
- Delete `clips/users.json.pre-rotation` once rotation is confirmed good.
- Paywall feature list says "Per-channel AI learning baseline" — contradicts
  the "not AI" branding (one-line fix).
- Smaller leftovers: /auth/kick 503s before redirect when unconfigured;
  orphaned blank Kick account in prod users.json; backup tar not written
  atomically; counter seed double-counts reviewed clips; VodScreen reconnect
  duplicate moment (cosmetic); nginx client_max_body_size 500M unnecessary;
  PROCESSING_KEY dead constant in job_queue.py; end-to-end test-mode Stripe
  checkout never confirmed on prod.

## Queued nice-to-haves

Discord webhook notifications on clip_ready (top retention idea), edit_url
"Extend to 60s" button, per-promo-code signup tracking in admin, first-run
onboarding flow, "trial ending soon" notice for admin-granted trials,
streamer partnership (clips-first DM, free Pro + custom code + $5/paid
signup; target a 300–1,000 viewer streamer).
