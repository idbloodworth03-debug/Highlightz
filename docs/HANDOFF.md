# Session handoff — project state & hard-won knowledge

Read this before making changes. `CLAUDE.md` has the binding engineering
rules; this file is the context behind them. Last updated: **2026-08-02**
(Clip Editor + auto-captions; prod audit).

**Prod release flags as of 2026-08-02 (read from `.env`, not assumed):**
`UPLOADS_ENABLED=true`, `CLIP_IMPORT_ENABLED=true`, `CAPTIONS_ENABLED` unset.
So the Clip Editor is LIVE for Pro users and clip import is live for
everyone; captions are admin-only. Do not restate these from memory —
`grep -E "ENABLED" /opt/highlightz/.env` is the only authority, and
getting it wrong once already meant telling the owner a live feature was
switched off.

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

- **No video recording/re-hosting, ever — on the CLIPPING path.** Clips are
  real Twitch clips made via the official Helix API with the user's own token;
  Twitch hosts everything. This is the compliance moat — rejected repeatedly
  for both 60s clips and Kick. Scope note (2026-07-31): Clip Upload holds
  video bytes, but the source is the user's own upload of their own file, and
  we never fetch from Twitch. The ToS wording (`api.py`, "never records,
  stores, or re-hosts any stream video itself") is specifically about the
  Service creating clips on the user's behalf, which is untouched. **If
  server-side clip fetching is ever added, five user-facing places must be
  rewritten first** — landing hero, features, FAQ, compliance section, and the
  ToS — search for "re-host".
- **We never download from Twitch ourselves.** Being OAuth'd as the user does
  NOT unlock clip video: there is no Twitch scope for it, and the CDN
  (`clips-media-assets2.twitch.tv`) does not check tokens at all. Getting
  bytes means the undocumented thumbnail→MP4 URL pattern or the private GQL
  endpoint. Both are grey-area (StreamLadder-style); neither is in the
  product. Asked and answered 2026-07-31.
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
  **On Kick the blocked nav tabs are genuinely `disabled`, not just dimmed**
  (2026-07-31) — a greyed-but-clickable tab dead-ends and reads as a broken
  app. `KICK_BLOCKED` in `aurora_html.py` is the single source of truth for
  both the nav and the route dispatch, so a tab can never be
  clickable-but-dead or greyed-out-but-working. Account, Feedback, the platform
  switch and Sign out stay live — **Kick must never be a trap**
  (`test_kick_never_traps_the_user`).

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
- **THERE IS A FREE TIER (added 2026-08-03).** `free` = 1 monitored stream,
  15 pending clips, no VOD scanner, no Clip Editor. It exists because the
  growth plan needs 1,000 signups in a month and nobody hands over $10 to find
  out whether the detector works on their channel.
  - **AuthMiddleware no longer paywalls anyone.** It used to redirect every
    non-subscriber to `/billing/paywall`, so a signup without a card saw no
    product at all. Access control now lives with the individual limits
    (add-stream cap, pending cap, VOD gate, Clip Editor gate), each asking
    `limits_for()`. The paywall page still exists and is still linked from
    upgrade prompts; it is just not a wall.
  - **The websocket does NOT check subscription either.** Realtime is how the
    dashboard works at all — closing the socket on a free user leaves a screen
    that silently stops updating, which reads as broken rather than as a limit.
  - **`get_plan` ordering is load-bearing.** No active subscription → free,
    even if a `plan` field is stored (a lapsed subscriber must not keep Pro
    forever). An active subscription with NO stored plan is a legacy $15-era
    customer, grandfathered to Pro — the only case where a missing plan means
    paid, which is why it is checked last. Anything unrecognised falls to free:
    failing open on billing is the expensive direction.
  - **Lapsing trims, it does not shut down.** `_enforce_stream_limit` stops
    only the streams beyond the new allowance, newest first, because a free
    user is still entitled to one. `_stop_user_streams_now` remains for admin
    revoke, where stopping everything IS the intent.
  - **Free is 1 stream because a stream is the scarce resource** — each one
    runs a streamlink+ffmpeg audio meter on the single shared vCPU. Raising it
    is a capacity decision, not a config tweak, and `max_concurrent_streams`
    (20, process-wide) is still the global ceiling.
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
- **Extract from the PARSED Python string, never the raw file.** The React app
  lives inside a triple-quoted string, so Python consumes escapes before the
  browser sees them: `split('\n')` typed with ONE backslash becomes a real
  newline, terminates the JS string and white-screens the app — while a checker
  that reads the file still sees an intact escape and passes. Bit us on
  2026-07-31 (ClipEditor caption). `scratchpad/extract_jsx.py` now imports
  DASHBOARD_HTML, and `test_no_python_escape_is_left_for_python_to_eat_in_the_js`
  fails on any single backslash the interpreter would eat. **Any backslash
  meant for JavaScript must be doubled.**
- **Compiling is NOT enough — a new tab must be added to THREE tables.**
  `NAV` (the sidebar), `HEAD` (route → [title, subtitle]) and the
  `route==='x'` dispatch chain. Miss `HEAD` and `HEAD[route][0]` throws inside
  RdApp's render, React unmounts the whole tree, and the user gets a **white
  screen, not a broken tab** — this happened adding Clip Upload (2026-07-31).
  Babel compiles it fine; only running it catches it.
  `tests/test_dashboard_contract.py` now enforces all three.
- Driving the dashboard in headless Chromium needs an **HTTP origin, not
  `file://`** — the app is fetch-driven and on a file:// page every relative
  fetch resolves to `file:///clips` etc., which Chromium blocks by CORS before
  Playwright's router sees it. `scratchpad/build_dash.py` rewrites the three
  unpkg CDN tags to vendored copies; serve the dir over a throwaway
  `http.createServer` and stub the API with `p.route`. Dismiss the first-run
  welcome modal (button "Start clipping") before asserting on any screen.
- Landing = LANDING_HTML string in `api.py` (plain string, no f-string
  braces; the string in api.py is canonical).
- **Typography (settled 2026-07-30)**: **Lobster (400) for TITLES ONLY**
  (`.hero-copy h1`, `h2.sec-title`, `.formula h2`, `.final h2`); everything
  else — wordmark, stat numbers, price, demo score, all body copy — is **Sora**.
  Self-hosted woff2, preloaded, no Google Fonts request.
  - **Lobster is a SCRIPT.** Two hard rules, both locked by
    `test_lobster_is_titles_only_and_never_uppercased`:
    1. **Never `text-transform:uppercase`** — its letters are drawn to connect
       in lowercase; uppercasing turns it into disconnected slanted capitals.
       All four title rules deliberately have no text-transform.
    2. **Weight 400 only** — it ships one weight; asking for bold makes the
       browser synthesise it by smearing glyphs.
    Also no letter-spacing on titles: the forms are drawn to sit tight.
  - The heavy 3D extrusion was dropped for titles — a multi-layer slab on thin
    script strokes reads as mud. Titles use a soft shadow + purple glow only.
  - **Three display faces were tried and rejected before this**, in order:
    Anton (owner: "pixelated" — it is condensed/industrial AND the extrusion
    stacked hard shadows at 2px GAPS, which banded), Nunito Black (rounded,
    clean — rejected on look), Michroma (angular/esports per a reference image;
    rejected as too thin/technical). Their font files were removed. If a future
    display face is picked, check first whether it ships real weights and
    whether it survives uppercasing.
  - Extrusion lesson worth keeping: hard-shadow 3D stacks need **contiguous
    1px steps**. Gaps between copies are what read as "pixelated".
  - Fixed along the way: `.demo-wrap::before` bled -30px sideways and pushed
    body.scrollWidth past the viewport near 1024px; now `inset:-36px 0`. Page
    overflow is 0 at 1440/1024/390/360. 320px keeps a pre-existing 13px,
    unrelated and masked by overflow-x:hidden.
  - **OPEN: og-card.png is still rendered in Anton**, so the share card matches
    no current face. Regenerate it in Lobster. `Anton-Regular.ttf` is that
    card's source font — **keep it** until the card is redone.
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

## Formula — July 2026 human-calibration retune (n=1001)

Training Studio data (1001 blind human scores, 26 channels, 4 trainers) said
something blunter than "reweight": **the signals barely track human judgment.**
trigger_score vs human virality was +0.081; the bot's top-10% clips scored
3.91/10 from humans vs 3.11 for its bottom 10%. Audio and sentiment did not
even correlate with the matching human slider (-0.035 / +0.020), i.e. what we
measure as an "audio spike" is not what a person hears as one.

Applied a DELIBERATELY SMALL lean (owner's call: volume must not fall, clip
count is the felt value of the product):

    CHAT_VELOCITY 36 -> 38   (RAISED to protect volume — see below)
    AUDIO_SPIKE   24 -> 22   (noise in both datasets)
    VIEWER_SPIKE  15 -> 19   (ONLY signal both datasets back: AUC 0.73 +0.070)
    SILENCE_BURST 14 -> 11   (only significantly INVERTED signal, -0.102)
    EMOTE_HOMOG.  12 -> 10   (noise, token trim)
    KEYWORD        4 ->  5   (best new correlate +0.171 BUT 0/91 approved in
                              the outcome study and its human slider runs
                              inverted -0.276 — hedge, not a bet)
    SENTIMENT      5 ->  5   (noise but too small to matter)

**An equal pool does NOT by itself guarantee equal volume.** The first attempt
(VIEWER 20 / SILENCE 10 / CHAT 36) held the pool at 110 and still lost 6.6% of
clip volume on real data — and the ENTIRE loss landed on one channel
(yaboyyywill -16; every other channel flat, jynxzi +1). Reason: that channel's
clips lean on the signals being cut, and VIEWER_SPIKE is too rare there to give
the points back. Fix was raising CHAT_VELOCITY 36 -> 38: it is present in
nearly every clip, so it restores points broadly. Final measured impact -1.8%
(4 clips of 227 across all history), concentrated entirely on the same channel.
ALWAYS run simulate_weights before deploying a weight change; never reason
about volume from the pool total alone.

**The pool stays at exactly 110** — that is the volume guarantee, pinned by
`test_weight_pool_is_preserved_at_110`. Volume tracks pool SIZE against
unchanged thresholds; redistribution changes which clips rank high, not how
many clear the bar. Never shrink the pool to "raise quality".

REJECTED: the analyzer's own proposal wanted KEYWORD 4 -> 44 (the single
largest weight). That is the least-bad number in a field of noise taking the
whole budget, and it contradicts the 806-label outcome study. Do not apply
proposals unexamined.

Before deploying any weight change run the what-if on prod:
`venv/bin/python -m src.maintenance.simulate_weights` — replays every stored
clip's real signal vector through old vs new weights and prints the volume
delta, per-channel breakdown, and which moments would newly fail to fire.

Open threads from this dataset: zero clips were scored by 2+ trainers, so
inter-rater agreement is unmeasured (add an overlap mode before fitting
weights properly); 43% of records are jynxzi; and 10 clips scored 99-100/100
by the bot were rated 1/10 by humans — score saturation (`raw` caps at 100
BEFORE the multi-signal bonus) is the next thing worth investigating.

## VOD scanner — zero-results bug (fixed 2026-07-28)

A 56-minute VOD with **28,886 chat messages returned zero highlights**. Log:
`threshold: 49.6, peak_score: 37.8, moments: 0`. Not a fetch failure — the
scan worked, nothing could reach the bar.

Cause: the VOD bar is `profile.trigger_threshold x 0.62`, and the profile
threshold is the LIVE learned value, which climbs to the 80 ceiling on
heavily-rejected channels (lacy, marlon, stableronaldo, caseoh_, drsunscreen,
ishowspeed, joe_bartolozzi, flats all sit at 80). 80 x 0.62 = 49.6, while real
VOD scores peak near 38. The 0.62 scale was anchored to a *theoretical*
chat-only ceiling (~72) that never actually occurs.

Two fixes:
1. `_VOD_MAX_BASE = 65` caps the inherited threshold — a rejection-inflated
   live gate ("should I clip right now?") must not gate a VOD *search*
   ("show me this stream's best moments"). Bar drops 49.6 -> 40.3.
2. **Ranked fallback**: when the threshold pass finds NOTHING, surface the
   top-scoring seconds from `score_timeline` (already recorded during the
   scan — no rescan), spaced by COOLDOWN, flagged `below_threshold: True`.
   Only fires on a completely empty scan; padding a scan that found a real
   highlight with mediocre runners-up would make good scans worse.

**The cap alone is NOT sufficient** and the regression test says so — 40.3 is
still above the 37.8 peak that VOD reached. The guarantee comes from the
fallback, which is robust to any score distribution.

### Quality pass (2026-07-28, second round)

Root cause of mediocre results: **VOD scanning is chat-only** — it scores from
4 signals worth 58 points, while live uses 7 worth 110. It judges highlights
with 53% of the formula, which is why real peaks top out near 38.

Two additions:

1. **Viewer clips as ground truth** (`twitch_clips.get_clips_for_vod`). Clips
   real viewers made from the SAME VOD are merged as first-class moments,
   ranked by view_count, deduped against detected ones by COOLDOWN, badged
   "N clipped it" in the UI. A human already decided those moments mattered —
   no inference needed. **`vod_offset` is the clip's END position**, so start =
   `vod_offset - duration`; it is null for clips made during a live broadcast
   and those are skipped. Helix has no video_id filter, so it pages the
   broadcaster's clips (5 pages max) and matches client-side. Fails soft.
2. **Percentile selection** (`_VOD_PCTL = 0.97`). Selection now also runs
   against THIS VOD's own score distribution; effective bar is
   `min(absolute, p97)`, so it can only ADD candidates and `_top_moments`
   still ranks/caps. Self-normalising: identical behaviour on a 200-viewer
   channel and on xQc, and it cannot be mis-anchored the way the absolute bar
   was. Skipped below `_VOD_PCTL_MIN_N` (120) scored seconds — too thin for a
   percentile to mean anything.

Still open: real VOD peaks (~38) sit right on the DEFAULT bar (60 x 0.62 =
37.2), so a healthy channel yields roughly one moment per hour-long VOD from
detection alone. The percentile pass papers over this; the 0.62 scale itself is
probably anchored too high, but recalibrating needs peak scores from several
scans, not one. **Idea 3 (peak prominence) and idea 4 (VOD audio) remain
unbuilt** — note that audio should NOT be ported until the audio signal itself
is fixed (it correlates -0.03 with human virality).

## Dead-channel bug — decay only ran while watching (fixed 2026-07-29)

Eight prod channels sat pinned at the 80 threshold ceiling (lacy, marlon,
stableronaldo, caseoh_, drsunscreen, ishowspeed, joe_bartolozzi, flats). lacy
had 225 stored clips and ZERO that would fire.

Nobody set 80 — the bot learned it. Reject = +0.75, approve = -2.0, ceiling 80.
With 724 rejections against 17 approvals, several channels walked to the cap.

Threshold decay DID exist (10%/hour toward the preset seed) but lived inside
`_profile_update_loop`, which runs `while self._running`. So it ticked **only
while a channel was actively monitored, and only after an hour of continuous
uptime**. A channel that hit the ceiling and then went unwatched — stream
ended, idle reaper, user removed it — froze there permanently. Self-
reinforcing: nothing fires at 80, so nothing is approved, so nothing pulls it
down. Streams monitored in bursts under an hour never decayed at all.

Fix: `StreamerProfile.decay_elapsed(seed, now)` applies decay as a function of
**elapsed wall-clock time**, and `ProfileManager.load` calls it, so a profile
heals whether or not anyone was watching. `last_decay_ts` persists on the
profile (round-trip covered by a test — without it every load restarts the
clock and decay silently never accumulates, the same class of no-op as the
original bug). The worker's hourly tick now calls the same function, so there
is one implementation rather than two that can drift. Catch-up is capped at
720 hours. Signal weights ride the same clock (5%/hour vs the threshold's 10%
— a learned preference is worth more than a drifted threshold).

Recovery from the ceiling toward a 60 seed: 6h -> 70.6, 24h -> 61.6, 72h -> 60.
Active rejection still holds the bar (one reject is +0.75 against ~10% of the
gap per hour), so this rescues neglect, not genuine strictness.

Preview before deploying: `venv/bin/python -m src.maintenance.show_stuck_profiles`
(read-only; lists every profile, its seed, and what happens on next load).

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
  duplicate moment (cosmetic); 
  PROCESSING_KEY dead constant in job_queue.py; end-to-end test-mode Stripe
  checkout never confirmed on prod.

## Clip Upload — the social-publishing foundation (started 2026-07-31)

**TWO INDEPENDENT HALVES, two flags.** They are held back separately because
the reason for holding one back does not apply to the other:

| Flag | What it gates | Why |
|---|---|---|
| `CLIP_IMPORT_ENABLED` | "Your Twitch clips" — browse every clip on your channel | **Complete on its own.** Ready to launch whenever you want. |
| `UPLOADS_ENABLED` | drag-and-drop upload + library | Half a feature until the editor exists. |

The tab renders if EITHER is on (`clipTabOn`), so import can ship alone.

### Clip import (`GET /twitch/clips`)

Lists a user's own clips through documented Helix — metadata only, and it can
never be a route to the video file (that question is CLOSED, see below). Fetched
live and cached 120s rather than stored: a local mirror of Twitch's data goes
stale the moment a clip is retitled or deleted, and there is no reason to own
that problem.

Three things not to regress (`tests/test_clip_import.py`):
1. **The broadcaster comes from the SESSION, never the request.** Honour a
   client-supplied channel and this stops being "your clips" and becomes a
   general-purpose endpoint for enumerating anyone's clips, on our app token
   and our Helix budget.
2. **One page per request, plus a per-user rate limit** (12/min). Helix is 800
   points/min shared across every user AND with live clipping; a helper that
   paged to exhaustion would let one import starve the clip workers.
3. **A Twitch outage is a 502, not an empty list.** An empty list renders as
   "you have no clips" — a lie the user would act on.

Helix sorts clips by **view count, not recency** (documented quirk that has
bitten this codebase before). The payload says `sorted_by: view_count` and the
UI offers its own Most-viewed / Newest toggle rather than claiming an order it
does not have.

### Why the file itself is out of reach (settled 2026-07-31)

`src/maintenance/probe_clip_media.py` run against prod: clips now return
`static-cdn.jtvnw.net/twitch-video-assets/…/landscape/thumb/thumb-…jpg`. The
widely-documented trick (`clips-media-assets2…-preview-480x272.jpg` → strip
suffix, append `.mp4`) assumed the thumbnail sat beside the video; in this
newer asset layout it is under a separate `/thumb/` directory, so **no suffix
swap yields the file**. Do not go fishing for a replacement pattern — that is
guessing at undocumented URLs, and the probe exists so this can be re-checked
with evidence instead. Two earlier attempts produced convincing-looking 403s
that meant nothing (the media path is NOT the clip slug; and every record in
our own `clips.json` carries a VOD thumbnail, so it cannot answer this either).

### Uploads specifically

**HELD BACK — `UPLOADS_ENABLED=false` (the default).** Built and tested, but
not released: shipping "upload a file, then nothing" is worse than not
shipping. Users get an under-construction screen; **the API also returns 503**,
because a UI-only gate still lets a direct POST write to the shared 50 GB disk.
Admins bypass the flag so the owner can exercise it on prod, and their
dashboard shows an orange "Admin preview — your users see an under-construction
screen" banner, so a hidden feature can't be mistaken for a launched one.
**To launch:** set `UPLOADS_ENABLED=true` in prod `.env` and restart. The Pro
plan gate is independent and still applies (`test_the_plan_gate_still_applies_
once_the_feature_is_switched_on`).

**Why it exists.** TikTok's Content Posting API and Instagram's publishing API
both take either raw bytes or a URL on a domain you have *verified you own*.
Neither accepts a twitch.tv link. So posting a clip anywhere — and any editing
— requires possessing the file. That is the whole reason this feature holds
video when nothing else in the product does.

**v1 source is the user's own upload**, deliberately: broadcasters can already
download their own clips from the Twitch Creator Dashboard, so this asks for a
file they are entitled to and keeps us clear of Twitch entirely. The important
architectural point: **the video source is one function, and everything
valuable is downstream of it.** The editor, vertical reframe, captions and
publishing do not care where the MP4 came from — so build those against the
safe source, and swapping in server-side fetching later (if that call is ever
made, with revenue data rather than speculatively) is a small delta.

**Where things are:**
- `src/uploads/library.py` — storage, quota, container sniffing. All caps in
  `config/settings.py`: `upload_max_file_mb` 300, `upload_max_user_mb` 2048,
  `upload_max_total_mb` 25600.
- API: `GET/POST /uploads`, `GET /uploads/{id}/file`, `DELETE /uploads/{id}`.
  Pro-only (`PLAN_LIMITS[...]["uploads"]`), same gate shape as the VOD scanner.
- Frontend: `UploadScreen` in `aurora_html.py`, nav id `uploads`.
- Files land in `clips/uploads/<user_id>/<uuid>.<ext>` — under the gitignored
  `clips/`, so prod data never reaches the repo.

**The three things that must not regress** (all in `tests/test_uploads.py`):
1. **The global cap is the real safety net.** The droplet has ONE 50 GB disk;
   a full disk stops clipping, billing writes and the dashboard, not just
   uploads. Size is checked *during* the copy, not after — checking afterwards
   means the bytes already landed, which is not a cap.
2. **Client input never becomes a path.** Stored paths are a server UUID plus a
   whitelisted extension. The filename is display text only. If that ever
   regresses to joining the client name onto a directory, `../../etc/cron.d/x`
   is a remote write.
3. **Content-Type proves nothing.** A browser labels anything `video/mp4`.
   Containers are sniffed from magic bytes (ISO `ftyp` / EBML) and nothing else.

Also: uploads are deleted with the account (`delete_all_for_user`), one user's
id is a 404 to another, and playback serves HTTP Range (206) so seeking works
without re-downloading a 300 MB file.

**Deploy note:** `deploy/nginx.conf` already carries `client_max_body_size
500M` (a pre-pivot leftover HANDOFF used to list as unnecessary — it is now
load-bearing). No nginx change needed. **Do not lower it below
`upload_max_file_mb`** or uploads fail at the proxy with an opaque 413.

**Next, in order:** ~~vertical reframe + captions~~ (both done — see below),
then TikTok/IG OAuth + publishing.

### Clip Editor + auto-captions (2026-08-01)

The tab is named **Clip Editor** (`uploads`). Adding streamers moved to the
**Live Streams** tab; **Clip Review** is now only clip reviews.

**Editing and export run in the BROWSER, not on the droplet.** Trim, shape
(9:16 / 1:1 / 16:9), zoom, position, title text and burned-in captions all
draw through one function — `paintFrame(ctx, video, o)` in `aurora_html.py` —
and export re-renders that same path into a `MediaRecorder`. This is the single
most important cost decision in the feature: server-side ffmpeg encoding on a
1 vCPU box would contend directly with the audio meters, and the earlier plan
in this file ("expect to need a dedicated encode droplet") is what browser
encoding avoids entirely. **Do not move export server-side** without a very
concrete reason.

Traps already paid for, do not re-learn them:
- **The paint loop changes no state, so React does not re-render while a clip
  plays.** Anything positioned from `videoRef.current.currentTime` *during
  render* is frozen wherever the last state change left it — that is exactly
  how the trim playhead shipped stuck at 0%. The playhead and the clock are
  written imperatively from inside the rAF loop (`headRef` / `clockRef`).
  Calling `setState` every frame instead would re-render the whole editor 60x
  a second; don't.
- **Whisper segments are sentences, not captions.** Straight from the model, a
  clip of continuous speech is often ONE segment spanning the whole thing — a
  correct transcript that renders as a single caption that never changes. Cues
  are rebuilt from word timings; see `_cues_from_words`.
- **MediaRecorder WebM carries no duration header** — `video.duration` is
  `Infinity` until you seek to a huge time (`1e101`). Exported clips would not
  play/scrub without that.
- **Export races its own recorder**: needs a settle delay before start, a
  wall-clock floor, a `requestData()` before stop, and a guard against the
  canvas being resized mid-export.
- **Python eats backslashes before the browser sees them.** `split('\n')` in
  the JSX string became a real newline and white-screened the app. Use
  `String.fromCharCode(10)`. The JSX checker must extract from the **parsed**
  `DASHBOARD_HTML`, not the raw source file, or it validates text the browser
  never receives.

**Auto-captions — Whisper on THIS droplet (owner's call, 2026-08-01).** The
recommendation was a paid transcription API (~$0.006/min, no CPU cost); the
owner chose the free path, so it is built to be safe rather than fast:

- `src/captions/transcribe.py`. `faster-whisper`, `tiny.en`, `compute_type=
  "int8"`, `beam_size=1`, `word_timestamps=True`, VAD off.
- **VAD is OFF (`captions_vad`, default false) and the numbers say so.** The
  voice-activity filter discards audio before transcription, so anything it
  misjudges is gone with no downstream stage able to recover it. A/B on prod
  via `src/maintenance/caption_vad_test.py` — same clip, same build, only the
  flag different:

  | vad_filter | cues | words | coverage | took |
  |---|---|---|---|---|
  | True  | 6 | 17 | 8.37s of 30.01s = **28%** | 4.0s |
  | False | 8 | 19 | 23.66s of 30.01s = **79%** | 2.8s |

  It was also **faster** with VAD off — on a clip this short the VAD pass costs
  more than the audio it skips, which kills the CPU argument that was the only
  reason to have it. Short-form clips are its weak case anyway: game audio and
  music sitting under the voice. `CAPTIONS_VAD=true` puts it back, but re-run
  the A/B before believing it.
- **`word_timestamps=True` is load-bearing, not a nicety.** Without it there
  are no word timings, `_cues_from_words` takes its keep-the-whole-segment
  fallback for everything, and captions collapse back to one static blob with
  all the cue-shaping code still present and silently doing nothing. Guarded by
  `test_word_timestamps_are_requested_from_whisper`. Cue bounds live in the
  `_MAX_CUE_*` constants — raise them and captions read like subtitles instead
  of short-form captions.
- **Four CPU guards, all mutation-tested in `tests/test_captions.py`** (each
  guard was removed and the test confirmed to fail): `_slot =
  asyncio.Semaphore(1)` is **process-wide, not per user** — one transcription
  at a time, ever; `cpu_threads=1` (CTranslate2 otherwise grabs every core it
  sees, which here is the only one); a hard `captions_timeout_s` so a
  pathological file cannot pin the core; and the temp WAV is removed in a
  `finally`.
- The model is **loaded lazily and once**, so a user who never asks for
  captions never pays the load, and a droplet without `faster-whisper`
  installed still boots and clips normally.
- Rationale for all of it: clip detection is the product, captioning is a
  convenience. If they ever compete, detection wins.
- Flags: `CAPTIONS_ENABLED` (default false), `CAPTIONS_MODEL` (`tiny.en` —
  `base.en` is ~2x the cost for a modest gain; only move up if the box is
  visibly idle), `CAPTIONS_TIMEOUT_S`.
- API: `GET/POST /uploads/{id}/captions`, one running job per user (429 on a
  second). Broadcasts `captions_progress` / `captions_ready` /
  `captions_failed`, all handled in `ws.onmessage`. Captions are stored beside
  the video (`<video>.captions.json`) so deleting the upload takes them too.

**UNVERIFIED FROM DEV, AND WHY:** the dev container's egress proxy blocks the
weights host (`httpx.ProxyError: 403`), so `_run_whisper` has **never actually
executed here**. Everything around it — queueing, the semaphore, storage, the
API, the UI, failure rendering — is verified in tests and in headless Chromium.
That one function is deliberately isolated so the unverified surface is as
small as possible. Confirm it on prod:

```bash
venv/bin/pip install faster-whisper                       # deploys never pip install
venv/bin/python -m src.captions.transcribe --selftest      # downloads + runs the model
```

Browser-side note: **enabling captions must not tempt anyone into COOP/COEP.**
`SharedArrayBuffer` is off, which rules out in-browser Whisper (wasm), and
turning it on would break the cross-origin Twitch embeds the dashboard depends
on. That is why transcription is server-side at all.

Encoding is a genuinely different resource profile from the current box — if
export ever *does* move server-side, expect to need a dedicated encode droplet.

## Publishing — deliberately NOT an API integration (2026-08-02)

**We do not post on the user's behalf, and that is the design.** Posting
through TikTok's Content Posting API, Instagram's Content Publishing API or
YouTube's Data API each require the app to pass platform review first — weeks
of calendar time — and YouTube's default 10,000 units/day against 1600 per
`videos.insert` would cap the ENTIRE app at six uploads a day. Instagram will
not accept bytes at all; it fetches from a public URL on a domain you have
verified, i.e. hosting user video publicly. Handing the user a correctly-shaped
file plus a one-tap share needs none of it and shipped in a day.

If server-side posting is ever revisited, that paragraph is the cost, and it
should be paid only with revenue data rather than speculatively.

**Tabs:** Clip Editor is for CUTTING; **Scheduler** is for POSTING. Every
export uploads the render back to the server (`POST /uploads?source=render`)
and drops it in the Scheduler. That round-trip is not bookkeeping — a blob in
one tab's memory is unreachable from the phone that has the TikTok app on it,
and the phone is where the share sheet lives. `source="render"` keeps exports
out of the editor's source-clip picker; they are output, not input.

**What exists:**
- `src/publish/platforms.py` — the single source of the per-platform limits
  (ideal vs hard duration, caption cap, preferred ratio, upload URL). Carries a
  checked-on date; these move, and a stale limit is worse than none because it
  is confidently wrong.
- Editor "Post it" panel: keeps the exported blob, offers `navigator.share`
  with the file (the OS share sheet — TikTok/IG/YouTube, one tap, on a phone),
  a caption box, and a per-platform FIT CHECK.
- `src/publish/schedule.py` + `/publish/schedule` — an in-app posting queue.

**Editor draw options** (all through `paintFrame`, so preview and export can
never disagree): `fill` crop|blur, `capSize`, `capPos` top|middle|bottom|low,
`capHighlight`. Blur fill CONTAINS the video and puts an over-scaled blurred
copy behind — if the foreground ever goes back to cover, blur becomes
decoration over the same crop and the feature is pointless. It degrades to a
plain crop when `ctx.filter` is unsupported, because drawing the background
unblurred puts a giant duplicate of the video behind itself.

**Three things not to regress:**
1. **Container format is a fit rule, not a footnote.** MediaRecorder falls
   back to WebM wherever there is no H.264 encoder (Firefox, always), and
   TikTok/Instagram refuse WebM outright — YouTube accepts it. Before this
   existed the Scheduler said "Fits" and the user found out at the upload page.
   `fmt` is stored on the queue item at export.
2. **The fit check distinguishes "will be rejected" from "loses Shorts
   format".** Over YouTube's Shorts cutoff the video is still accepted, just no
   longer a Short. Calling that a rejection sends people trimming clips that
   were fine.
3. **The share button is a CAPABILITY check (`navigator.canShare({files})`),
   never a user-agent sniff** — it is genuinely absent on most desktop
   browsers, and the desktop path (download + copy caption + open the upload
   page) is a real path, not an apology. `AbortError` is the user cancelling
   the sheet and must not render as a failure.
4. **The queue reminds; it cannot post.** Every user-facing string says so
   (`test_the_queue_never_claims_it_will_post_for_you`). A queue that looks
   automatic and silently isn't costs someone a posting slot — worse than not
   shipping it.

Queue design: `due_at = 0` means "exported, no time picked yet" — most clips
arrive that way, and demanding a time at export would make the Scheduler a
chore instead of an inbox. Undated items sort AFTER scheduled ones (0 sorts
first numerically, which would pin them all to the top) and never fire a
reminder. Changing the time clears `notified` so a rescheduled post nudges
again; changing the caption does not, so fixing a typo doesn't re-notify.
`duration_s`/`ratio` are stored at export so the Scheduler can fit-check every
card without downloading every render. Times are epoch seconds UTC, converted in the browser (the only
place that knows the zone); `due`/`missed` are DERIVED from the clock on every
read and never stored, so a restart or clock step cannot make the list lie; the
`schedule_due` broadcast is only a nudge, so a missed event cannot lose a
reminder. Deleting a clip drops anything queued for it, and account deletion
takes the whole queue.

**Not built, on purpose:** email and browser-push reminders. In-app only for
now — the owner's call. Email needs a sender that does not exist yet.

## Queue-full policy: REFUSE THE NEW CLIP (changed 2026-08-03)

**A full pending queue now drops the new moment. It no longer evicts an old
clip.** Until this change `notify_clip_ready` deleted the OLDEST UNREVIEWED clip
to make room, so a busy stream silently destroyed work the user already had.

**The check runs in `run_clip_processor` BEFORE `processor.process(job)`,** and
that ordering is load-bearing: the Twitch clip is created inside `process()`, so
checking afterwards would leave an orphan clip on the user's Twitch account that
never appears in Highlightz — which the user could find, contradicting the "we
didn't clip this" notice — and would spend a Helix call from a budget shared
with every other user. `pending_room(uid)` is the helper.

The in-`notify_clip_ready` check stays as the race guard (the queue can fill
between the pre-check and arrival) and is what keeps the queue from going over
cap. It sets a flag inside `_data_lock` and broadcasts AFTER releasing it —
awaiting a socket write under that lock stalls every clip in the pipeline.

**`MISSED` is its own ledger event**, never folded into caught or rejected: a
clip that was never made cannot be one the user kept or threw away, and either
substitution corrupts the keep rate shown to streamers. `stream_stats` reports
it as a separate `missed` count per channel and session.

## Queue-full notice / upgrade prompt (2026-08-03)

The pending cap is the conversion lever. Since the policy change above the cap
genuinely does refuse the moment, so "N highlights were not clipped" is
accurate — and because nothing is created on Twitch either, there is no orphan
clip for the user to find and contradict it with.
`test_the_notice_says_the_highlight_was_not_clipped` holds the wording, and
also fails if it drifts back to claiming something was deleted.

- `clip_evicted` broadcast on eviction (live nudge) + `clips_lost_24h` and
  `next_plan` on `/me` (state, so the notice survives a reload and a reconnect).
  **Both paths need the next-tier fields** — the reload path is how most people
  will actually see this, and without them it fell back to "review some to free
  up space" and never mentioned upgrading at all. Found in the browser drive,
  not by reading the code.
- Counted from the `stream_stats` ledger (cap evictions already log as
  `EXPIRED`), so no new storage and it survives a restart — the cap fires while
  the user is away, and an in-memory counter would read zero by the time they
  open the tab.
- **Pro gets the notice but no upgrade button.** There is no tier above it; the
  copy switches to "review or approve some to free up space". An upgrade button
  that leads nowhere is worse than no button.
- `clips_lost_24h == 0` clears the banner, so a true warning cannot become a
  permanent nag once they free up space.

### Dismissing the notice

It felt permanent for three separate reasons, all fixed together:
1. **No dismiss control existed at all.**
2. `setLostClips(p=>p||...)` kept the FIRST value, so no later `/me` could
   lower the count or clear it — once shown it stayed for the life of the tab.
   It replaces now, and there is an `else setLostClips(null)`.
3. The count used a flat 24h window, so even a working dismissal would have
   been undone by the next page load. `_clips_lost_24h` counts from
   `max(now-24h, miss_notice_dismissed_at)`.

Dismissal is persisted on the USER (`POST /me/dismiss-miss-notice`), not in the
tab, and broadcasts `miss_notice_dismissed` so the user's other tabs close it
too. A new miss after dismissing brings it back — dismissing is not permanent
silence.

**`stream_stats` timestamps are no longer rounded.** At 1dp a row written at
t=1000.06 stored 1000.1 — 0.04s in its own future — so a "since this moment"
comparison could count an event that happened before it. That is exactly the
comparison the dismissal window makes, and it showed up as a flaky test rather
than as a bug report.

## Clip trading between admins (2026-08-03)

**Landing Page tab → Grab.** Copies a featured clip into your own Clip Library
so the team can trade — one person's bot catches something and everyone can
post it. Admin-only; `POST /admin/showcase/{clip_id}/grab`.

**Nothing is re-hosted.** The copy is a record pointing at the same Twitch URL,
exactly like every other clip in the product, so this does not touch the
no-re-hosting line at all.

**`_is_grabbed(clip)` is the important part, and every telemetry path checks
it.** A grabbed clip was NOT produced by our formula for the person holding it,
so counting it would:
- inflate the per-channel clip record with a "kept" that has no matching
  "caught" — and that record is what gets shown to streamers,
- teach that channel's profile from a decision the formula never made, drifting
  its threshold on borrowed evidence,
- put a mislabelled row in the training set.

Grabbed clips therefore carry NO `trigger_score` and NO `trigger_signals` —
copying the original's score would fabricate a detection that never happened.
They arrive `approved` (grabbing is the approval) and dedupe on the Twitch URL
rather than the clip id, since the id is per-record.

All five guards mutation-tested (`tests/test_clip_grab.py`): removing the stats
guard, the profile guard, the dedupe, the admin gate, or copying the score
across each fails a test. One test deliberately checks a NORMAL clip still
records and teaches — a guard that swallowed ordinary clips would silently stop
all learning, which is the expensive way to be wrong.

## Referrals + free-plan landing (2026-08-03, growth plan items 2 & 5)

**`src/auth/referrals.py`** — signup attribution INDEPENDENT of Stripe. The
existing promo attribution fires from the Stripe webhook at checkout, so it
records nothing for a free signup, and the growth plan is free signups.

Three rules, all mutation-tested (`tests/test_referrals.py`):
1. **First touch wins, permanently** (`users.set_ref_once` refuses to
   overwrite). Someone who arrives via Tommy's link, returns via Ian's and then
   subscribes still counts as Tommy's — otherwise whoever posted most recently
   harvests everyone else's work and the weekly table stops meaning anything.
2. **A link and a typed code are the same thing.** `?ref=tommy` and a typed
   `TOMMY` resolve identically; splitting them undercounts every lane that uses
   both, which is all of them.
3. **Unknown codes are dropped, never stored** — a stored typo becomes a row in
   the report attributed to nobody, reading as a real lane that produced users.

**The ordering trap:** the ref rides the SESSION COOKIE out to twitch.tv and
back, and `twitch_callback` calls `request.session.clear()` for session
fixation three lines from the attribution. It must be read BEFORE that; reading
after silently attributes nobody and every signup shows as Direct.
`test_the_callback_reads_the_ref_before_clearing_the_session` guards it.
Captured on `/`, `/login` AND `/auth/twitch` — a bio link may point at any.

**Short links:** `highlightz.app/ian` and `highlightz.app/r/ian` both work
alongside `?ref=ian`. A bio field displays whatever URL you type, so the
attribution cannot be hidden outright — a bare path just reads as a page
instead of as tracking.

`GET /{slug}` is registered LAST in api.py and refuses anything not in
REFERRERS, so it cannot shadow a future `/pricing` or `/settings`; an unknown
slug behaves exactly as if the route did not exist. Two tests hold that: one
asserts the route is last in `app.routes` and every real path precedes it, the
other checks real pages still resolve. Referral paths are also in the open-path
check — without that the auth middleware bounces a signed-out visitor to
`/login` and the ref is gone before any handler runs. 302 not 301: a cached
permanent redirect would keep sending that person to the landing page after
they signed in.

Add a person by adding a key to `REFERRERS`; their link, short link and typed
code all start working.

**Admin → Referrals** shows the weekly table from the plan: signups /
connected a channel / still active wk2 / paid. Users younger than 7 days are
excluded from the retention column entirely rather than counted as churned.

**Landing page now leads with free** — a Free card (first of three), hero note,
meta descriptions, the FAQ billing answer, and the JSON-LD `AggregateOffer`
(`lowPrice` 0.00, `offerCount` 3). The price grid was widened from 840px to
1120px; at `minmax(300px,1fr)` it only ever fitted two cards and wrapped the
third. A price in schema.org that disagrees with the page is what
structured-data penalties are for, so those two move together.

## Streamer outreach shortlist (`src/maintenance/find_streamers.py`)

For the streamer-partnership idea below. **Two things Helix does not have, and
the tool is built around both:**

1. **No country field. Anywhere.** Not on streams, users, or channels.
   "US-based" is not queryable. The proxies are `language` (necessary, far from
   sufficient — `en` is also UK/CA/AU/IE) and WHEN someone is live. The report
   prints a `US?` column = the share of that channel's live samples falling in
   22:00-07:00 UTC. It is only meaningful if you sample around the clock, so
   the report checks the hours you actually covered and prints `n/a` instead of
   a fake number when every pass was inside that window.
2. **No average-viewers endpoint.** `GET /streams` is viewers *right now*. A
   single snapshot of a 100-500 band is mostly people having an unusual night.
   Hence `--sample` (collect, on a cron) and `--report` (aggregate), with a
   warning when fewer than 3 passes exist.

`--floor` (60) sits deliberately BELOW `--min` (100): collecting only at the
band's floor would record targets' good nights and miss their quiet ones,
biasing every average upward.

Helix budget is shared with live clip creation (800 pts/min). One pass is
~20-40 requests, paced by `--delay`, hard-capped by `--max-pages`, and a 429
ends the pass rather than retrying. Do not cron it more than a few times a day.

## Per-channel clip record (`src/stats/stream_stats.py`, 2026-08-03)

"We caught 40 moments on your stream and I kept 12" — built to be shown to a
streamer. **ADMIN ONLY** (`/admin` → Clip Record), by request: it is an
operator view spanning every user, so `/admin/stream-stats` must never lose
its `_require_admin` — without it any signed-in user could read everyone
else's monitored streamers and how the product performs on them.

Sortable on every column (numeric columns compare as numbers — sorting
"caught" as text puts 9 above 40), filterable by channel or user, and each
row expands to its per-stream breakdown. `_summarise` is shared by
`for_user` and `all_rows` so a per-user view, if one is ever added back,
cannot disagree with the admin table.

**It is a dedicated append-only ledger, and it has to be.** Neither existing
source can answer the question:
- `_clips` cannot: rejecting DELETES the clip, and so does cap-eviction. A
  channel where 40 were caught and 30 rejected would read as "10 caught" —
  the exact opposite of the point being made.
- `training_log.jsonl` cannot: it deliberately skips clips with no signal
  vector (VOD moments, legacy), so it is a training set, not a census.

Four hooks, one per outcome: caught (at creation), approved, rejected, expired
(cap eviction). `test_every_clip_outcome_has_a_hook` guards the wiring — a
missing reject hook would show a 100% keep rate, which is worse than no number
at all when the number is being used as evidence.

**Sessions are INFERRED from gaps** (`SESSION_GAP_S` = 4h), because the app
never persisted broadcast boundaries. Labelled by date, never given a broadcast
id we do not have. Events are grouped by the clip's `created_at`, not by when
it was reviewed — approving on Friday must not create a Friday session for a
Tuesday clip.

Two rates, deliberately: `kept_pct` is over everything caught, and
`kept_of_reviewed_pct` is over what the user actually looked at. Counting
un-reviewed clips as rejections understates a channel whose queue is unworked.

Counting starts from deploy — there is no backfill, and reconstructing one from
the sources above would be exactly the undercount this exists to avoid.

## Reviews / social proof (started 2026-08-03)

**Why not Google Business Profile:** it requires serving customers in person or
at a verified address. A pure web app does not qualify, and a listing on a home
address gets suspended, taking the reviews with it. **Why not self-marked-up
testimonials:** Google has ignored self-serving review markup since 2019.
The one legitimate route for us is `SoftwareApplication` + `aggregateRating`,
which the landing JSON-LD already half-declares — the entity is a software
product, not a business.

**Built:** `src/feedback/reviews.py` + a prompt after **25 approved clips**
(`MILESTONES = (25, 150, 500)`).

**Four rules, all mutation-tested (`tests/test_reviews.py`):**
1. **The trigger is a CLIP COUNT and never sentiment.** Showing this only to
   users with a high approval rate is *review gating* — prohibited by Google
   and Trustpilot, and Trustpilot removes profiles for it. There is a test that
   greps `should_prompt` for rate/ratio/sentiment/score words.
2. **Dismissal is real.** "Not now" snoozes a month AND records the milestone;
   "don't ask again" is permanent; a submitted review is never followed by
   another ask. The prompt reaches users two ways — the live broadcast and the
   flag on `/me` — and only one marked it shown, so the *dismissal* records the
   milestone rather than trusting the caller.
3. **Publishing needs consent AND admin approval**, and the display name is
   discarded outright without consent. `public()` never emits the user id or
   username. Deleting an account deletes its reviews.
4. **`aggregate()` covers published reviews only.** That number would feed
   schema.org; averaging private ones describes something no visitor can read,
   which is what structured-data penalties are for.

**Where to see them:** the `/admin` panel, "Reviews" section — rating, comment,
who wrote it, the name it would show as, and Publish/Unpublish/Delete. The
publish button only exists for reviews the user consented to; consent is not
overridable from the panel.

**That block contains no backslashes and no inline `onclick`, on purpose.**
ADMIN_HTML is a Python triple-quoted string, so a JS escape is eaten by Python
first: `onclick="rvApprove(\'ID\')"` reached the browser as `rvApprove('')`, a
SyntaxError that killed the entire script and left the section on "Loading..."
with nothing in the suite noticing. Buttons carry `data-` attributes and one
delegated listener reads them. `test_the_admin_reviews_script_actually_parses`
runs `node --check` on the extracted block — do the same for any new admin JS.

**Not built yet:** the landing-page testimonial section and the
`aggregateRating` JSON-LD that consumes `reviews.aggregate()`. Do not add the
markup before real approved reviews exist.

**Still the highest-leverage item and not a code task:** free profiles on
Trustpilot, G2, Capterra and Product Hunt. Those are what actually rank in
Google for SaaS; our own page never will.

## Queued nice-to-haves

Discord webhook notifications on clip_ready (top retention idea), edit_url
"Extend to 60s" button, per-promo-code signup tracking in admin, first-run
onboarding flow, "trial ending soon" notice for admin-granted trials,
streamer partnership (clips-first DM, free Pro + custom code + $5/paid
signup; target a 300–1,000 viewer streamer).
