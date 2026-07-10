# Session handoff — project state & hard-won knowledge

Read this before making changes. It captures decisions, constraints, and open
threads from the July 2026 working sessions. `CLAUDE.md` has the binding
engineering rules; this file is the context behind them.

## Production environment (facts, verified)

- Prod runs at `/opt/highlightz` on a DigitalOcean droplet (1vCPU/2GB,
  IP 137.184.24.121). Operator is **root** — never use sudo in commands.
- Deploy: `cd /opt/highlightz && git fetch origin && git reset --hard origin/claude/magical-feynman-7Sp19 && systemctl restart highlightz`
- Working branch: `claude/magical-feynman-7Sp19` (push here, never elsewhere).
- Prod Python: `/opt/highlightz/venv/bin/python`.
- DNS is on **DigitalOcean** (Namecheap is registrar only; there is no cPanel
  hosting product despite what Namecheap's UI implies).
- `ADMIN_TWITCH_ID` is **intentionally unset** — the owner declined setting it.
  Admin is granted via `python -m src.auth.grant_admin <username>`.
- Google Search Console: domain property verified via DO TXT record;
  `sitemap.xml` submitted and accepted (July 2026). Do not remove the TXT
  record or the robots/sitemap endpoints.

## Non-negotiable product constraints

- **No video recording/re-hosting, ever.** Clips are real Twitch clips made via
  the official Helix API with the user's own token; Twitch hosts everything.
  This is the compliance moat — rejected repeatedly for both 60s clips and Kick.
- **Twitch clips are ~30s, hard API limit.** No duration param exists on
  Create Clip. The captured buffer is ~90s; the creator can extend to 60s only
  in Twitch's browser editor (`edit_url`, currently discarded — storing it +
  an "Extend to 60s" button was designed but not built).
- **Twitch's embedded player gates mature channels for logged-out viewers**
  (shows "clip is no longer available" even though the clip is fine). The
  dashboard keeps the inline embed + a "Player not loading? Watch on Twitch"
  fallback bar. The landing showcase uses thumbnails + lightbox embed with a
  Watch-on-Twitch escape hatch; mobile links out (Twitch embeds break in many
  mobile browsers — Error #4000).
- **Broadcasters/mods delete clips** (caseoh_'s mods famously mass-delete).
  A 6-hour dead-clip sweep (`sweep_dead_clips_task`) removes Twitch-deleted
  clips and prunes the landing showcase. Fail-safe: a failed lookup deletes
  NOTHING; `first=100` is set explicitly so pagination can't read as deletion.
- **Kick:** no public clip-creation API exists (verified against KickDevDocs,
  June 2026). Kick sign-in is disabled (Twitch is the only sign-in; Kick
  *linking* for authenticated users still works). Kick UI is gated behind an
  under-construction screen; all Kick mentions were scrubbed from marketing
  pages. Hype Train/EventSub signals are unavailable to us (require the
  broadcaster's own OAuth, which third-party clipping can't get).

## Billing (Stripe) — current design

- $15/month, 7-day free trial **with card required**
  (`trial_period_days` + `payment_method_collection: always`), auto-converts.
  One trial per identity via `trial_claims.json` ledger
  (`trial_claim_id`: twitch_id, else `kick:<id>`); the claim is burned in the
  webhook on activation so abandoned checkouts don't waste it.
- Checkout guards (July audit): refuses already-active users, does a LIVE
  Stripe subscription check for returning customers (closes the
  webhook-latency double-subscription window; self-heals the DB when Stripe
  says active), `past_due` routes to the portal, and the existing Stripe
  customer is always reused (`customer_id` param).
- Webhook: signature-verified, idempotent, and `apply_subscription_event`
  resolves the ACTUAL affected user on customer mismatch (a stale customer's
  cancellation must not kill the current subscription's streams).
- Paywall copy is conditional: trial-eligible users see "7 days free";
  returning users see "Restart your subscription — $15/month" (never promise
  free days to someone who'll be charged).
- Promo codes: `allow_promotion_codes` on checkout; 50%-off-first-month coupon
  exists in Stripe. Planned streamer partnership: per-streamer code + $5/paid
  signup (manual payout, Stripe redemption count is source of truth).

## Trigger formula — state and history

- **Clip timing: fire at the TOP of the trigger** (3s settle to catch the
  crest, then immediately). The decay-wait/double-peak dwell (8–45s) produced
  flat aftermath clips and was reverted. Do not reintroduce waiting-for-decay.
- **Spike-aware baseline** (`profile.update_velocity`): readings ≥2× the mean
  barely move the baseline (ratio gate, variance frozen during spikes) so
  sustained hype can't become "normal". A z-score gate was tried and rejected
  (feedback trap: the spike inflates the variance and disengages the gate).
- **Threshold bounds:** approve −2 / reject +2, clamped [30, 80]; 80 stays
  below the 85 emergency override. Dry-spell recalibration: −2 per 15 min of
  no triggers, floor **52** (the old −3/10min floor-40 dragged busy channels
  into junk-clip territory — the "clipping poorly" incident).
- **Feedback loop:** approve/reject uses `pm.load()` (a cache-only `get()`
  silently dropped most approvals for months — thresholds ratcheted up-only).
  A one-time `reset_feedback` was run; `--raise-floor` mode lifts sub-52
  thresholds without touching learning.
- **Training log** (`clips/training_log.jsonl`): every clip outcome (approved /
  rejected / expired_unreviewed) with its signal vector. Weight-learning ("#4")
  is deliberately NOT built — it needs ~150 labeled examples with 60+ of each
  class; check with the counter script before building. Do not fit weights on
  esports-paper priors; this content mix (IRL/reaction) likely differs.
- **VOD scanner:** profile-aware (learned threshold ×0.5, learned spike
  multiplier) + top-K ranking (~3 moments/hour, max 12), moments emitted after
  the scan completes. One-moment-per-contiguous-run dedup, 90s cooldown.

## Frontend / pages

- Dashboard = one Babel-standalone React string in `aurora_html.py`. A JSX
  error white-screens everything → ALWAYS extract the babel block and compile
  with `@babel/preset-react` before pushing (see CLAUDE.md).
- Landing page = `LANDING_HTML` string in `api.py`; the editable source of
  truth during sessions was `scratchpad/landing_new.html` spliced in — but the
  string in `api.py` is canonical. Plain string: no f-string/format braces.
- Landing features: animated capture demo (no chat panel — reframed for small
  streamers, "5 viewers or 50,000"), live clips counter
  (`/landing/stats`, monotonic `clip_counter.json`), admin-curated example
  clips (`/landing/showcase`, "Feature on landing page" button in the clip
  modal, lightbox playback), FAQ (10 items, `<details>` accordion), $15
  pricing with promo hint, og/twitter cards (`static/og-card.png`), JSON-LD
  (SoftwareApplication + FAQPage — note: Google no longer renders FAQ rich
  results for ordinary sites; markup kept for semantics). robots.txt +
  sitemap.xml endpoints; login/paywall are noindex.
- **CSS shorthand trap:** `.wrap` (class) beats `section` (type) on the
  `padding` shorthand — sections use longhand padding now. Grid `1fr` means
  `minmax(auto,1fr)`: min-content propagation made the app lay out 456px wide
  on phones. Mobile fixes rely on `minmax(0,1fr)` + `min-width:0` chains.
- Mobile was verified with an automated overflow scanner (every screen at
  360px with hostile data — long channel names, many chips). Re-run the same
  approach after layout changes.

## Verification workflow (what "done" means here)

1. `python -m pytest tests/` — suite ~90 tests, all green.
2. JSX: extract babel block → `babel.transformSync` with preset-react.
3. Render in headless Chromium (Playwright, `executablePath:
   '/opt/pw-browsers/chromium'`). unpkg/jsdelivr are BLOCKED by the sandbox
   proxy — vendor React/ReactDOM/Babel from npm and rewrite the script tags in
   a local copy. Stub API routes with fixture JSON; dismiss the first-run
   welcome modal before interacting.
4. TestClient smoke for endpoints (session-gated ones need real sessions —
   test pure helpers instead).
5. Real screenshots at 1440px and 360–390px, eyeballed, before any deploy
   command is given. The owner expects zero post-deploy bugs.

## Maintenance commands (server)

- Backup now: `venv/bin/python -m src.maintenance.backup`
  (nightly cron: `10 4 * * * cd /opt/highlightz && venv/bin/python -m src.maintenance.backup >> backups/backup.log 2>&1`)
  Off-site: set `BACKUP_S3_BUCKET`/`BACKUP_S3_ENDPOINT` + AWS keys.
- Threshold maintenance: `venv/bin/python -m src.profiles.reset_feedback`
  (full reset, dry-run default) / `--raise-floor` (surgical) / `--apply`.
- Data lives in `clips/` (users.json, clips.json, profiles/, trial_claims.json,
  training_log.jsonl, clip_counter.json, showcase.json) — untouched by
  `git reset --hard` deploys.

## Open threads (as of 2026-07-10)

1. **Prod config check never confirmed:** `TOKEN_ENCRYPTION_KEY` set +
   `cryptography` installed + stable `DASHBOARD_SECRET_KEY`. If unset, OAuth
   tokens are plaintext and/or break on every restart. HIGH priority.
2. End-to-end **test-mode Stripe checkout** never confirmed on prod.
3. Backup **cron** may not be installed yet; `--raise-floor` may not have been
   run on prod.
4. Queued nice-to-haves: Discord webhook notifications on clip_ready (top
   retention idea), `edit_url` "Extend to 60s" button, per-promo-code signup
   tracking in admin, first-run onboarding flow, trial-ending email.
5. Audit leftovers (Medium/Low): orphaned blank Kick account in users.json;
   `/auth/kick` 503s before redirect when unconfigured; VodScreen reconnect
   duplicate moment (cosmetic); backup tar not atomic; reset_feedback ignores
   unknown flags; counter seed double-counts reviewed clips; kick-theme keeps
   purple auras.
6. Streamer partnership: opener drafted (clips-first DM, free Pro + custom
   code + $5/paid signup); target is a 300–1,000 viewer streamer.
