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

- **We record live broadcasts now. THIS CHANGED 2026-09-08, on the owner's
  decision.** For most of the product's life the constraint here was "no video
  recording, ever", and it was the compliance moat — rejected repeatedly for
  both 60s clips and Kick. The owner decided to hold video so a clip can be a
  FILE the user downloads, edits and schedules without leaving the site.
  What that means in practice:
  - `src/ingestion/clip_recorder.py` runs its own streamlink per monitored
    channel and keeps a rolling ~3 min buffer of MPEG-TS segments, stream-copy
    only (never transcodes — clip detection owns the core). Bounded by
    `clip_capture_max_total_mb`; wiped when monitoring stops.
  - On a trigger, `stream_worker._cut_local_file` cuts an MP4 named by the
    `clip_id` and `src/clips/files.py` holds it. Retention
    `clip_file_max_age_days` (30), swept in `main.sweep_dead_clips_task`.
  - **Off by default.** `clip_capture_enabled=False`. Turning it on is a
    deliberate act, not a deploy.
  - Opt-out is checked BEFORE the recorder starts, and that ordering is pinned
    by a test — an opted-out broadcaster is never recorded.
  - The five user-facing places the old note said must be rewritten first HAVE
    been rewritten (2026-09-08): landing FAQ, llms.txt, llms-full.txt, ToS
    §1 and §5, Privacy §1 and §6a, the compare page and the tutorial FAQ.
    `tests/test_video_disclosure.py` now fails if a retired denial comes back
    or if the promised retention drifts from `clip_file_max_age_days` (both
    legal pages render it from the setting via a `<!--CLIPDAYS-->` placeholder,
    so do NOT type the number).
  - **Unchanged, and still the narrower claim worth defending:** the clip
    itself is still a real Twitch clip made through Helix on the user's own
    token, and Twitch still hosts it.
- **We now DO fetch clip video from Twitch — as a fallback. THIS CHANGED
  2026-09-15, on the owner's decision, with the risk stated to them.** From
  2026-07-31 the line here was "we never download from Twitch ourselves",
  and it held through the capture change. The owner reversed it because
  capture alone leaves every clip the recorder missed, and every clip from
  before capture existed, permanently un-downloadable — and a clip that
  cannot be downloaded, edited or scheduled is a clip the product cannot
  finish. Their words: "Even if this means we are no longer Twitch compliant
  I will take the risk for a better product." Do not relitigate it, and do
  not quietly widen it either. What it means in practice:
  - `src/clips/fetch.py` retrieves a clip's MP4 via **streamlink** (already
    on the box as the capture engine), which uses Twitch's playback-token
    GQL call — the path every clip tool alive uses. Deliberately not a
    hand-rolled call: there is no undocumented URL in our code for Twitch
    to change out from under us, and streamlink is maintained against that.
    The thumbnail→MP4 rewrite is still dead (probe, July) and still banned.
  - **Capture stays primary.** It is free, better quality (source 720p60),
    and never touches Twitch. The fetch runs only when capture produced
    nothing: `api._fetch_when_capture_misses` waits out the capture window
    after `notify_clip_ready`, then fetches. Historical clips fetch **on
    demand** (`POST /clips/{id}/fetch`, or Edit, which fetches inline) —
    never eagerly, so the grey path is used for clips a human asked for.
  - **Off by default.** `clip_fetch_enabled=False`; `CLIP_FETCH_ENABLED=true`
    in `.env` is the deliberate act. One download at a time, per-clip
    dedupe, `.part`-then-rename, timeout, size ceiling, same disk cap/trim
    as every other file.
  - **Honours the opt-out.** `fetchable()` refuses an opted-out broadcaster;
    the Privacy Policy now says an opted-out channel's clips are never
    retrieved, and a test pins it.
  - The six public statements that said "never downloads video from Twitch"
    (tutorial FAQ, landing FAQ, llms.txt, llms-full.txt, ToS, Privacy) were
    rewritten the same day; `tests/test_video_disclosure.py` now fails if
    any of them comes back. `tests/test_no_video_ingest.py` allowlists
    exactly TWO video-pulling modules.
  - **The risk, restated so nobody rediscovers it.** This is squarely what
    Twitch's Developer Agreement prohibits; the realistic downside is the
    app's API access being suspended, which would stop clip CREATION for
    every user, not just downloads. It scales with visibility. Fetching only
    what capture missed and only on request is what keeps the footprint
    small; eager bulk fetching would not be a small delta, it would be a
    different risk profile, and needs its own decision.
  - **Unverified from the dev container:** streamlink's clip download was
    exercised against a real Twitch clip on prod at deploy time, not here
    (egress proxy). If Twitch changes the clip flow, `pip install -U
    streamlink` on the box is the fix, not a code change.
- **The risk this trades into, stated plainly so nobody rediscovers it.**
  Twitch's Developer Agreement / DSA terms prohibit storing Twitch Content and
  cap caching at 24h; our buffer is short but the cut files are kept for days.
  The realistic downside is app suspension rather than litigation, and it
  scales WITH success. The owner was walked through this before deciding and
  chose to proceed. Do not relitigate it — but do not let the copy drift back
  into denying it either, which is what the disclosure tests are for.
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
- **Kick: LIVE since 2026-09-15** (owner: "get kick integrated as best as we
  can right now using the same thing we have with twitch … we cannot do
  highlight clips with them yet"). Still no public clip-creation API
  (re-verified September 2026), so the shape is:
  - **A Kick clip is a FILE.** `_process_kick` returns a pending record with
    `platform_url=https://kick.com/<slug>` and no Twitch fields; the video is
    the stream worker's live-capture cut, addressed by the same clip id and
    announced by the same `clip_file_ready`. **`CLIP_CAPTURE_ENABLED` is a
    precondition**: `POST /streams` answers 503 for Kick without it, the
    processor refuses, `_force_clip` says why. The modal plays
    `/clips/{id}/file` when there is no embed; cards and the modal link
    "Open on Kick" (the channel page).
  - **Liveness** (`src/ingestion/platform/kick.py`): Kick's public API
    (`api.kick.com/public/v1/channels?slug=`) with an APP token minted by
    client credentials from `KICK_CLIENT_ID`/`KICK_CLIENT_SECRET` (a Kick
    developer app; no user login). Without those it falls back to the site
    endpoint `kick.com/api/v2/channels/<slug>` (browser headers; behind
    Cloudflare, may 403 from a datacenter IP). `ChannelOffline` is not
    retried; a near-miss slug is refused.
  - **Chat**: Pusher (`src/chat/platform/kick_chat.py`, key `32cbd69e…`,
    `chatrooms.<id>.v2`). The chatroom id only exists on the site endpoint,
    so it is **cached on disk** (`clips/kick_chatrooms.json`) the first time
    it is learned; if it cannot be learned the channel STILL runs on audio +
    viewer count and logs `kick_chat_unavailable`. No sub/raid events.
  - **Video/audio**: the site endpoint's `playback_url` (the channel's IVS
    HLS playlist, token included) is handed to streamlink as
    `hls://<playback_url>`, which forces the generic HLS reader — NOT the
    Kick plugin. Verified on prod 2026-09-15: `kick.com/api/v2/channels/xqc`
    answered the droplet HTTP 200 with a playback_url while
    `streamlink https://kick.com/xqc` said "No playable streams" (the plugin
    wants a headless browser for Cloudflare's challenge). The page URL is the
    fallback only when the site endpoint could not be read. The URL is
    fetched fresh on every session start, so a token that expires is
    replaced by the worker's normal reconnect. The audio meter and recorder
    are platform-agnostic.
  - **Verified on prod 2026-09-15**: a 43 s Kick clip captured and playing
    in the dashboard (owner's screenshot). First Kick bug from it: the
    "Age-restricted on Twitch … plays on Twitch" banner showed over it,
    because Kick's `is_mature` lands in the same `age_restricted` flag. The
    banner now only renders when the flag actually stops playback (`gated
    && !fileSrc`) and names the clip's platform; the card badge's tooltip
    says the Kick file plays here.
  - **Suggestions** (owner: "make it so it suggests kick streamers like on
    twitch"): `/streams/suggest?platform=kick` — search via the site's
    `kick.com/api/search` (falls back to an exact-slug lookup on the public
    API, which has no name search), popular via the public API's
    `/livestreams?sort=viewer_count` (app token) or the site's
    `kick.com/stream/livestreams/en` without one; its own popular cache;
    "recently monitored" is filtered by the profile file's `platform`. Same
    row shapes as the Twitch dropdown; the panel refetches per platform and
    clears the list on switch. The two site endpoints are unverified on
    prod (the channels one is verified) — an empty "Popular" on Kick means
    check `journalctl` for `kick_site_livestreams_unusable` / `kick_search_failed`.
  - **Not on Kick**: Highlight clips / viewer-clip learning
    (`_record_viewer_clips` returns for non-Twitch — it would look up a
    Twitch user of the same name), auto-preset (Twitch category lookup),
    the opt-out page (Twitch identities), the landing showcase (Twitch clips
    only), Twitch-refusal notices. Kick stays **unmarketed** (nobody asked).
  - **Same-name collision**: stream keys, profiles and recorders are keyed
    by channel name without platform, so `xqc` on Twitch and `xqc` on Kick
    are one channel to the store. Known, not fixed.
  - Legal: ToS §1 and the Privacy Policy now describe Kick monitoring and
    file-only clips, still with "no Kick credentials are requested or
    stored" (the phrase `test_legal_pages_match_the_code` pins while there is
    no `/auth/kick` route). **OPEN TO EVERYONE since 2026-09-16** (owner:
    "open kick to all users"; it was an admin-only beta for one day, after
    "Kick dashboard is closed I need it open for admins"): `const kickOpen
    = true` in the app, and `POST /streams` no longer checks admin for Kick
    (the only Kick refusal left is `CLIP_CAPTURE_ENABLED` off). To close
    Kick again: `kickOpen = !!(me && me.is_admin)` and put the 503 back in
    `add_stream`; `KICK_BLOCKED` still lists the tabs that would close. A
    deploy does not reload an open tab's JS — hard-refresh to see a new
    gate. The mechanism stays
    (`test_kick_blocked_nav_buttons_are_actually_disabled_not_just_dimmed`)
    for the next screen that has to close on Kick, and **Kick must never be
    a trap** (`test_kick_never_traps_the_user`). 14 tests in
    `tests/test_kick_live.py`.

## Billing (Stripe) — current design (TWO TIERS since 2026-07-11)

- **Two plans**: Starter $10/mo (3 monitored streams, 50 pending clips, no
  VOD scanner) and Pro $25/mo (10 streams, 200 pending, VOD scanner).
  Plumbing: `src/billing/plans.py` (PLAN_LIMITS + get_plan), price ids in
  settings (STRIPE_PRICE_ID_STARTER / STRIPE_PRICE_ID_PRO; legacy
  STRIPE_PRICE_ID = the $15 era, mapped to 'pro' — **existing subscribers
  are grandfathered as Pro**). The admin MRR counts a legacy subscriber at
  `plans.LEGACY_PRICE` ($15; owner, 2026-09-16 — it used to leave them out
  and call the figure a floor). `mrr_unknown` in `/admin/overview` now just
  means "how many are on the legacy rate". The webhook reads the subscription's price id
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
    is still the global ceiling.
  - **TWO CEILINGS, and they count different things.**
    `max_concurrent_streams` bounds LIVE streams and is the hardware guard,
    enforced at go-live by `api.acquire_live_slot()`. `max_registered_streams`
    bounds how many channels may be ADDED and is what
    `_check_server_capacity()` refuses on; it derives as live x 4 and is
    deliberately loose. Conflating them was a real bug: the CPU ceiling used to
    cap registrations, so users were refused for queueing channels that cost
    nothing. An offline channel polls `is_live` every 30s and spawns no
    subprocesses, and offline is the normal state — prod measured 8 channels
    registered against a load average of 0.00. A channel that goes live with no
    slot free reports status `queued` and retries, rendering as "waiting for a
    slot"; it must never report `offline`, which would be a visible lie.
  - **The LIVE ceiling is DERIVED FROM THE MACHINE, not hardcoded.**
    `config/settings.py` computes it as usable cores x 6 (see
    `default_max_concurrent_streams`), clamped to 6..400, overridable with
    `MAX_CONCURRENT_STREAMS` in `.env`. Six per core is the measurement with
    headroom put back: on the original 1-vCPU droplet, EIGHT live streams sat
    at 93.4% of a single core, so 8 is saturation rather than capacity. The old
    hardcoded 20 was a promise that box could not keep. A droplet resize now
    changes the ceiling on restart with no code change; confirm the resolved
    number with `scripts/capacity.py`, which prints it, and re-measure the
    per-core cost with `scripts/stream_cost.py` after any audio-meter change.
    Confirmed on the 2-vCPU/4GB droplet (Aug 2026): derives 12 live, 48
    registered, with no `.env` pin.
- **REVERSED AGAIN (2026-08-26): THE SELF-SERVE TRIAL IS RETIRED AND FREE IS
  THE FRONT DOOR.** Read this block before the two below it, which describe the
  arrangements it replaces. `TRIAL_DAYS` is GONE from `plans.py` and
  `_checkout_trial_days` returns 0 unconditionally, so a checkout is now
  unambiguously "start paying".
  - **Free**: 1 monitored stream, 20 pending clips, 3 crowd suggestions, no VOD
    scanner, no Clip Editor. No card, no expiry.
  - **`get_plan` now sends EVERY non-active account to free** — never
    subscribed, cancelled, lapsed and finished-trial alike. The `grandfathered`
    flag no longer changes any access decision (funnel_stage still reads it so
    legacy users are not chased as "lapsed"); `locked` is reachable only from
    `not user`, i.e. a deleted account holding a live session.
  - **Trials already running were deliberately not touched.** `trialing` still
    resolves to pro, so anyone Stripe was mid-trial for finished on the terms
    they signed up under. Retiring an offer must not reach backwards into the
    accounts that took it.
  - **WHY.** Card-up-front fixed the old free week's conversion problem and
    created a worse one: the card was the wall, so the top of the funnel was
    what got optimised away. This is the third arrangement — free tier, then
    trial, now free tier again — so treat the NUMBERS as the changeable part
    and the derivation as the durable one: every public page reads its figures
    from `PLAN_LIMITS`, pinned by `tests/test_free_tier_offer_copy.py` (renamed
    from `test_no_free_tier_claim.py`, whose whole premise inverted).
  - **Crowd suggestions have their own budget** (`max_suggested`: free 3,
    starter 15, pro 50) and NO LONGER draw on `max_pending`. This replaced the
    50% pending-queue reserve in `stream_worker.py`: a separate pool makes
    "a suggestion can never take a slot a triggered clip wanted" structural
    rather than arithmetic, and it is what lets free mean 20 of our clips PLUS
    3 of the crowd's. `api.suggestion_room()` is the counter; `notify_clip_ready`
    picks the cap by `clip["suggested"]` and both halves are mutation-tested.
  - **Copy that must never come back**: "7 days free", "cancel before day 7",
    and any affirmative "card required". The banned-claims list is a set of
    REGEXES, not substrings, because "card required" is a substring of the new
    copy's own "no card required".

- **SUPERSEDED 2026-08-26 — see the block above. This block was stale and was
  itself a reversal. There WAS a self-serve free trial.** It used to read "Billed immediately, there is NO self-serve free
  trial", describing a state that a later cutover undid. `TRIAL_DAYS = 7` in
  `src/billing/plans.py` is the single source of truth: new signups get 7 days
  of the full product with no card, `get_plan` resolves `trialing` to `pro`,
  and when it lapses the account goes to `locked` (zero streams, zero queue) —
  not to free, which is legacy-only now. Every public page states it in one
  fixed wording, "7 days free, no credit card required", pinned by
  tests/test_no_free_tier_claim.py. Do not strip that copy on the strength of
  an old note; check plans.py.
- **CARD REQUIRED AT SIGNUP (cutover 2026-08-24).** Signing up grants
  NOTHING: `subscription_status="none"` -> get_plan `locked`. The 7 free days
  are now STRIPE'S trial, claimed at checkout —
  `create_checkout_url(..., trial_days=7)` sets `trial_period_days` and pins
  `payment_method_collection="always"` (Stripe's default for a trialing
  Checkout is `if_required`, which would create the trial with NO card and
  silently undo the whole change). Access then arrives as a Stripe
  `trialing` subscription.
  - `sync_subscription_event` no longer folds `trialing` into `active` —
    "we hold their card, first charge on day 7" is a different fact from
    "we have charged them", and the dashboard says so to the user.
  - **THE TRAP**: the auth-middleware gate reads
    `status == "trialing" and trial_ends_at > 0 and time.time() >= trial_ends_at`.
    The `> 0` is load-bearing. Without it a Stripe trial (no app-managed end
    date) hits `time.time() >= 0`, which is always true — the customer is
    expired, streams stopped, "your trial has ended" toast, at the instant
    they pay. Do not "simplify" it.
  - `/billing/success` SELF-HEALS off the checkout session id rather than
    trusting the webhook. Access used to come from the app, so a missed
    webhook cost billing state but not access; now it would mean paid-and-
    locked. Webhook is still primary.
  - **WHO GETS FREE DAYS** (`_checkout_trial_days`): 0 for `pre_card_cutover`
    accounts, 0 for anyone in the trial ledger, else TRIAL_DAYS. The free week
    is burned in the webhook when Stripe actually starts a trial — not at
    signup (a look-around must not cost it) and not at checkout creation (an
    abandoned session costs nothing).
  - **GRANDFATHERING**: `mark_pre_card_cutover_accounts()` runs once at boot
    beside `grandfather_existing_accounts()`, marks every account missing the
    key, new accounts carry it explicitly False. Frozen in BOTH directions —
    they keep their access AND a subscription they start bills immediately, as
    it does today. It does not read subscription_status or trial_ends_at, so
    an in-flight no-card trial runs to its own date untouched. Explicit mark,
    never a date comparison (created_at cannot separate the groups once the
    boundary has passed).
  - Copy: every public page says "card required" AND "cancel before day 7";
    `test_no_free_tier_claim.py` bans the old "no credit card" family outright.
- **Admin comps are unchanged**: still app-managed, still no card, still no
  Stripe. `/me` exposes `trial_converts` so the dashboard can tell the two
  kinds of trial apart — a card-up-front trial gets "Manage billing", a comp
  gets "Subscribe". Getting that backwards sends a payer into a 2nd checkout.
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
- **Legal audit, second pass (2026-09-02, evening).** Owner: "go through the
  tos … and double check them", then "fix what is necessary". Seven findings
  fixed, pinned in `test_legal_pages_match_the_code.py` under "the 2026-09-02
  audit": (1) the viewer-clip learning log (`viewer_clips.jsonl`) wrote each
  clipper's Twitch NAME and ID — non-users — while the Privacy Policy said the
  opt-out list was the only non-user record; it now writes `clipper`, a
  16-hex sha256 of the id (stable for distinct-clipper counts, not
  reversible), and the policy discloses "Public clip records"; (2) the Terms
  never covered Highlight clips — §1 and §5 now say they are other Twitch
  users' clips, hosted by Twitch, approving keeps a link, no mechanism
  described (test bans "audience interest"/"spike"/"viewers clipped" in the
  Terms and the mechanism sentence in the Privacy Policy); (3) the Privacy
  Policy's Highlight bullet no longer says how they are found — "the two
  numbers used to rank it, an audience-interest count and a view count";
  (4) the Cookie Policy claimed an "encrypted session identifier" — Starlette's
  cookie is SIGNED and carries user_id/username/avatar/plan status, and now
  says so; (5) opting out only blocked ADDING a channel while running
  monitors kept clipping — `optout_confirm_submit` now calls
  `_stop_monitors_for_channel(login)`, which runs `stop_stream_internal` for
  every user (tabs drop the row live), and §5 says "any monitoring of it
  already running is stopped"; (6) the public showcase ignored opt-outs —
  `_load_showcase()` filters by `is_opted_out` at read time and
  `admin_toggle_showcase` refuses to feature one; (7) the Privacy Policy now
  lists `last_login_at`, `checkout_started_at` and the referral `ref`. Also:
  refunds "except at our discretion", price-notice "where we hold an email,
  otherwise in the dashboard", all three effective dates → September 2, 2026.
  **Left for the owner** (prod facts the dev box cannot see): the "logs kept
  up to 90 days" line has no rotation config in the repo; if uploads or the
  backup job point at S3/GCS in prod, the provider needs naming under
  sharing and backups need a retention line.
- **Clip Editor RELEASED TO PRO (2026-09-15).** Owner: "open up the editor
  to pro users now as well." The `uploads` NAV entry lost `adminOnly`,
  `adminOnlyTabs` is now `['schedule']` only, and the card's Edit button
  (`editorOn`) follows `plan_limits.uploads` instead of the admin flag. It is
  gated the way the VOD scanner is: the tab shows for everyone, the screen is
  the paywall below Pro, `_require_upload_access` refuses independently.
  `uploads_enabled` defaults True now and is the kill switch
  (`UPLOADS_ENABLED=false` takes it away from everyone but admins in one
  restart). The Scheduler stayed admin-only for a few hours and was then
  released and made to POST the same day — see "Publishing — the Scheduler
  POSTS" below; `schedulerOn` on `ClipEditor` now follows the plan, the
  same gate as the tab. **Marketed the same day** (owner: "market the editor on
  the landing page now"): a block on the landing page inside the pricing
  section, under the plans and hairlined off them the way the FAQ is
  (`_editor_section()`, rendered into `<!--EDITOR-->` at import so the
  captions card appears only when `CAPTIONS_ENABLED` is on). It is a `div`,
  not a ninth `<section>`: `test_landing_not_a_template.py` pins the page to
  exactly eight sections in a fixed dark/light rhythm, a budget of three em
  dashes and one "no X, no Y" construction, and the copy was written to
  those rules rather than the rules loosened. Also a Clip Editor
  row in the pricing tables and the paywall, the FAQ entry, the Terms' plan
  sentence and both LLM briefs. **The Scheduler followed the same evening**
  (owner: "market the scheduler and only open it to pro"): a second block
  inside the same `_editor_section()` div (`id="post"`, "Then post it.",
  three cards: Connect once / Pick a time / One caption, checked), a
  Scheduler row in the pricing facts, the paywall subline and Pro card, the
  FAQ entry (now "What are the VOD Scanner, the Clip Editor and the
  Scheduler?", which also carries the honest TikTok-private-until-audit
  line), the Terms' plan sentence, both LLM briefs, and a "Connected posting
  accounts" item in the Privacy Policy's data list with the Google API
  Services User Data Policy sentence Google's verification asks for. Pro
  only is the existing entitlement (`PLAN_LIMITS[*]["uploads"]`, true only
  on Pro), and `test_the_scheduler_is_marketed_on_the_landing_page_and_
  sold_as_pro_only` pins both the copy and that the pricing table says Yes
  on exactly the Pro column. `test_the_clip_editor_is_marketed_on_the_
  landing_page` still pins the editor copy.
- **Clip Editor and Scheduler: unmarketed and gated (2026-09-02, late).**
  Owner: "not ready to push that out yet — remove the clip editor and auto
  post stuff on the landing page and make sure it is gatekept". Removed from
  every public surface: the pricing "Clip Editor and uploads" row, the
  landing FAQ (now "What is the VOD Scanner?"), the Terms' plan sentence,
  both LLM briefs and the full file's plans table, and the in-app "Want
  more?" upgrade prompt. The legal DISCLOSURES about uploaded video stay
  (an admin can still upload). Gating already existed — NAV `adminOnly`,
  `_require_upload_access` (503 while `UPLOADS_ENABLED` is off, 403 below
  Pro) — but six routes lacked it: GET /uploads/{id}/file, GET
  /publish/platforms, GET/PUT/DELETE /publish/schedule…, GET
  /uploads/{id}/captions; all gated now, and
  `test_every_editor_and_scheduler_endpoint_is_behind_the_release_gate`
  walks every /uploads and /publish route. `test_the_unreleased_features_are_not_marketed_anywhere_public`
  sweeps the public surfaces (the compare page may still name the
  COMPETITORS' schedulers and auto-posting). PLAN_LIMITS `uploads` is
  untouched, so flipping `UPLOADS_ENABLED` later releases it to Pro without
  a plan change — re-add the pricing row and FAQ answer then.
- **Shelf carousel: loops with any number of clips (2026-09-02, late).**
  Owner: "the carousel of clips does not work on the landing page". Cause:
  `sizeLoop()` only engaged the loop when one set was WIDER than the shelf,
  so a three-clip showcase on a 1440 screen stood still — the exact
  complaint the carousel was built to fix. Now the real set is cloned as
  many times as the shelf needs (`copies = max(2, 2*ceil(shelf/set))`, even
  so `translate(-50%)` lands on a seam), rebuilt from the real set on every
  rail filter, and the loop runs whenever more than one card is visible.
  `--shelf-t` is the half-track width at ~55px/s. Under reduced motion it
  is still a plain scroll row (`prefers-reduced-motion` block) — an iPhone
  with Reduce Motion on shows a static row by design. Verified in Chromium
  with a 3-clip and a 6-clip showcase (`scratchpad/v4/loopcheck.js`).
- **Legal pages restyled + the LLM/SEO pass (2026-09-02, late).** /tos,
  /privacy, /cookies share `_LEGAL_STYLE` / `_LEGAL_NAV` / `_LEGAL_FOOT`
  (defined just above `TOS_HTML`): the landing's fixed bar, paper ground,
  display h1, mono meta, hairline-ruled h2s, the one-row footer; the "Back to
  Highlightz → /login" link is gone (the logo goes home). The opt-out pages'
  `_OPTOUT_BASE_STYLE` is the same system on black (Twitch button keeps
  Twitch purple; confirm is the orange). For machines: `/llms.txt` was
  reworded — it used to say Highlight clips come from "unusual spikes in
  audience clipping activity", the plainest mechanism leak on the site; it
  now says quality + green label only (test bans the tells), names the seven
  signals from `_SIGNAL_TITLES`, the 30s recheck / 8h idle stop, per-plan
  Highlight budgets, the Clip Editor, the immediate opt-out, the credits
  point. New **`/llms-full.txt`** (llmstxt.org's full file, in `_OPEN_PATHS`,
  linked from llms.txt and robots.txt): the whole public copy as markdown,
  generated from `LANDING_HTML`'s FAQ markup, `tutorial_content` and
  `compare_content` (plans table, FAQ, walkthrough, comparison, credits,
  legal links) — so a model fetching one file gets everything the pages say
  and nothing they do not. Tests: `test_the_legal_pages_wear_the_site_s_bar_and_footer`,
  `test_the_llm_brief_does_not_describe_the_highlight_mechanism`,
  `test_the_full_brief_is_public_and_carries_the_faq_and_the_comparison`.
  Already in place and verified this pass: robots allows every AI crawler,
  sitemap lists all seven public pages with real lastmods, every public page
  has canonical + description + OG card + rel=alternate → llms.txt,
  structured data on all three marketing pages (SoftwareApplication +
  Organization/WebSite + FAQPage, HowTo, ItemList), and the landing's
  crawlable-text floor.
- **Admin page restyled, nothing hidden on a phone (2026-09-08).** `ADMIN_HTML`
  wears the site's system now: black ground, the display voice for the one
  heading and the rail figures, mono labels, the ember as the only action
  colour (current tab, selected filter, the mint button, Pro), hairline
  tables, Sora/Plex only. The old sheet referenced `--dur-fast`, `--dur-slow`,
  `--ease` and `--fg-2` without declaring them, so every transition was
  instant; they are declared in `:root` now and a test forbids undeclared
  vars. Phone (≤700): the users table used to HIDE every column past
  Membership. Now every table (users, refusals, invites, referrals, promo,
  clip record incl. the per-stream drill, reviews) stacks into cards; each
  cell shows its column name via `td::before{content:attr(data-l)}`, copied
  from the header by a `MutationObserver` (`labelCells`) after every render
  so the renderers know nothing about it. The clip record's header becomes a
  row of sort chips so sorting still works stacked. Tabs are a 2×2 grid,
  filters wrap, the drawer is full-width. Markup/JS otherwise untouched (all
  pinned substrings, zero backslashes, delegated handlers). Verified in
  Chromium at 1440/390 with stubbed `/admin/*` JSON
  (`scratchpad/v4/adminshot.js`): overflow 0, no clipped cells, no console
  errors, drawer + drill + sort exercised. The feedback and opt-out sub-pages
  followed the same day: both are built from `_ADMIN_SUB_STYLE` (tokens,
  bar, buttons, fields, chips, toast) plus `_admin_nav(current)` for the
  shared top bar with the current screen marked, defined just above
  `_ADMIN_FEEDBACK_HTML`. Feedback: composer as a hairline panel, ember
  chips, threads with an ember left rule when unread, our replies on an
  ember rule and theirs on a hairline; opt-out: the admin table, stacking
  into labelled cards on a phone. Their JS is untouched apart from class
  names (`scratchpad/v4/subshot.js` renders both with stubs).
- **Landing v4 — the cinematic page (2026-09-02, later the same day).** The
  owner's second full brief: delete every section below the cover and rebuild
  imagery-first (a Squarespace/Apple register: full-bleed frame, huge plain
  headline, product screens fanned in the foreground, hard dark→light cuts).
  **The cover (`#cover`: mark, wordmark, stat strip, glow, cue) is
  byte-identical** to v3 — markup, `.cover*` CSS, `.stats` CSS, the live
  counter script and the count-up/cover/slide script were diffed. Everything
  after it is new. Order (and the mandatory tonal rhythm): `#proof` dark →
  `#numbers` dark → `#catches` light → `#score` light → `#watch` dark →
  `#pricing` light → `#start` dark → footer. `.light` re-declares the ink
  tokens on the paper ground (`--paper:#F4F4F2`).
  - **Frames are real clip previews from the curated showcase, baked in per
    request** (`_frames("hero")` for the proof/close/scrub frames,
    `_frames("gallery")` for the shelf; `render_landing` fills FRAME_HERO /
    FRAME_END / SCRUB_FRAMES / SHELF / BIGNUMS). `_frame_tag` asks Twitch for
    the 1280x720 preview and steps down once to the stored URL via
    `onerror`. **With nothing curated, the product's own screens
    (`static/landing/tour-*.webp`) stand in**, pushed back (`img.ui`). The dev
    box cannot reach Twitch's CDN (proxy 403), so no clip frame could be baked
    into the repo — curate the showcase on prod and the page fills itself.
  - **The shelf is a looping carousel** (owner's follow-up: "moving in a
    circle"). The script clones `.shelf-set` once (aria-hidden, tabindex -1),
    marks the shelf `.is-loop`, and `@keyframes shelf-roll` slides the track
    by -50%; each set carries the gap as its own right padding so the join
    is seamless. Duration is derived from the set's width (~55px/s,
    `--shelf-t`). Pauses on hover / focus-within / off screen; reduced
    motion and no-JS get a still, scrollable single set. The rail filter
    hides cards in both copies and restarts the animation.
  - **Frames ask for the 1080p preview first** (owner: "the thumbnails
    need to be the 1080p version as well"). `_preview_ladder` builds
    `-preview-1920x1080` → `-preview-1280x720` → the stored URL, and
    `_frame_tag` puts the rest in `data-next`; the inline `onerror` steps
    down one rung per miss and stops (no loop). Twitch keeps different sizes
    for different clips and the big ones are not guaranteed, which is why
    it is a ladder and not a swap. **Prod's clips are in Twitch's newer
    layout** (`static-cdn.jtvnw.net/twitch-video-assets/…/landscape/thumb/
    thumb-0000000000-480x272.jpg`); probed 2026-09-02, it serves 1920x1080,
    1280x720, 1080x608, 960x540 and 640x360 for the same clip, so
    `_RE_PREVIEW` swaps the trailing `-WxH` in either layout. A URL with no
    size suffix is used as stored. Verify with a real URL from
    `_load_showcase()` on prod rather than assuming.
  - **The clip player is the whole screen and goes full screen on click**
    (owner: "all of them played in 1080p"). Twitch's clip embed picks its
    rendition from the player's size when it boots and has no quality
    parameter; the direct MP4 route is closed (see "Why the file itself is
    out of reach"). So `.exl-card` is 100vw×100vh, `openLb` calls
    `lb.requestFullscreen()` from the click gesture BEFORE assigning
    `ifr.src`, and `.exl:fullscreen .exl-meta` is hidden so the iframe is
    exactly the display (measured in Chromium: the embed boots at 1920×1080
    on a 1080p viewport). Leaving full screen closes the player. A clip can
    only be as sharp as the stream it came from.
  - **The rail is the engine's own `_SIGNAL_TITLES`** ("Chat Erupts", "Loud
    Reaction", …), read from `src/trigger/engine.py` at import; a showcase
    entry now records `signal` (the SignalType that led the clip) so a card
    files under the right tab; older entries are spread across tabs.
  - **Every wall tile is a real channel** (owner's call): the backfill
    `names` list in the wall script is sixteen well-known Twitch channels
    with the category each is known for (`{n,g}`), used after the showcase's
    own channels; dedup keys drop trailing underscores so `caseoh`/`caseoh_`
    are one channel. Nothing on the wall claims a channel is live or a
    customer — the tiles are the demo of the scoring, labelled with real
    names.
  - **The wall is `#watch` now, ten tiles (`var N=10`)**: `visibleCount()`
    returns 10 / 6 (≤1000px) / 4 (≤700px) and the CSS hides the same tiles
    (`.watch .wall .tile:nth-child(n+7)` / `(n+5)`). It fires in the ORANGE
    (every `--flare`/`--glow` in the tile rules became `--ember`/`--ink`) and
    a `.tile-pull` chip ("clip saved") rises out of the firing tile. The
    near-miss arithmetic and the 4-name backfill logic are unchanged.
  - **`#score` is scroll-scrubbed**: a 260vh track with a sticky scene; the
    score curve holds over the line from p≈.6 (`ramp`), the seven signals
    light at their `data-at`, the frame swaps in thirds. Reduced motion: the
    track collapses (`height:auto`) and `paint(1)` draws the finished frame.
  - **No FAQ on the page any more**, so `_faq_schema` returns "" when it
    finds no questions and the landing publishes no FAQPage.
    `_free_plan_answer` went with it. Pricing is `_pricing()`: a free lead
    line + two `.plan` columns (Starter, Pro) of `<span>fact</span><b>value</b>`
    rows, all from PLAN_LIMITS.
  - 77 tests that described the deleted sections were dropped or repointed
    (`scratchpad/v4/retests.py` is the record); the crawlable-text floor in
    test_seo_head went 4000 → 2000 because the brief cut prose on purpose.
  - **Phone pass** (owner: "the mobile ui … is bugged out on some pages").
    Rendered at 390×844 and 360×740 in Chromium (`isMobile`, DPR 2), four
    things were broken and are fixed + pinned in `test_landing.py`
    (`test_the_phone_layout_of_the_pages_that_broke`): (1) the proof
    section's outer two product screens were never hidden — `.fan img`
    outranked `.fan-l{display:none}`, the rule is `.fan .fan-l` now; (2) the
    stuck `#score` scene was taller than a phone viewport, headline under the
    fixed bar and the signals clipped — the script moves `.score-head` into
    `#score-lead` above the track under 900px (and back when wider) and the
    scene pads its top by `--nav-h`; (3) the cover's five-column stat band
    ran a five-digit count off the right edge — two columns under 700px, rows
    divided by the hairline (desktop band untouched); (4) half-width watch
    tiles cut the game and the signal labels to two letters — the game drops
    to its own line and labels size to their text. Harness:
    `scratchpad/v4/mobile.js` (note its `/landing/` stub must not swallow
    `/static/landing/*.webp` — that bug hid the fan for a while).
  - **The cover's mark is the room's light now** (owner: "a nice big blurred
    logo in the background on the hero"). The small sharp mark above the
    wordmark is gone; `.cover-bg` (absolute, z-index 0, under `.cover-in`)
    holds the same `logo-mark.png` at 104vh, `filter:blur(14–22px)`,
    opacity .5 (phone: 96vh, 12px, .45). The blur radius is deliberately
    modest — at 64px the H dissolved into a plain purple glow and stopped
    being the logo; the test pins ≤24px. The floor glow (`.cover::after`)
    still paints over it. `test_the_cover_mark_is_the_room_s_light_not_an_object_in_it`
    replaced `test_the_cover_mark_is_painted_flat`.
  - **The FAQ is back, as `#faq`** (owner: "I need a FAQ tab also … talk about
    Highlight clips and what they are because people wont know about
    those"). A nav tab after Pricing; a paper section between the plans and
    the close (tonal rhythm is now D D L L D L L D — the FAQ shares the
    paper with pricing, cut by a hairline). `_faq()` builds three groups
    (Using it / Highlight clips / Plans and the fine print, 17 questions) as
    native `<details>`, every number from PLAN_LIMITS; `_faq_schema` derives
    the FAQPage from that markup again, so the landing publishes FAQPage
    once more (`test_seo_layer`, `test_no_faq_page_is_published_without_questions`
    repointed). Copy rules that bit: `test_the_no_x_construction_appears_at_most_once`
    (no "no X, no Y" sentences — only the score section's "no black box"
    survives) and the clipper-credit ban ("viewers made" is a banned phrase).
    Kick stays out of the FAQ on purpose (scrubbed from marketing).
    **The Highlight answers do not say how Highlight clips are found** —
    owner: "this is our secret sauce that makes our clips better than the
    rest". Public copy says only that they are usually the higher-quality
    clips, that a green label (the `rd-clippedbadge`) marks the ones that
    stood out even more, that they carry no trigger score, and that they
    never take more than half the queue. The test bans the tells (viewer,
    audience, crowd, clipped, views, settle, cluster…) in that group. Keep
    it that way on the tutorial and in card titles too.
  - **The walkthrough (/tutorial) wears the landing's system now** (owner:
    "match the vibe and theme going on with the landing page"). `tutorial_html._CSS`
    was rewritten: the fixed bar with the landing's links (What it catches /
    How it scores / Channels / Pricing / FAQ / Tutorial / Compare), a black
    hero with the display sans and the overview screen hanging off its
    bottom edge, the paper ground for the reading (sticky mono rail, `01`
    step numerals in the orange, hairline rows, orange-rule tips, the
    landing's FAQ rows and buttons), a black close and the landing's one-row
    footer. No Lobster, no purple below the bar. `BASE_CSS` is that same
    sheet, and **/compare was restyled on it in the next commit** (owner: "do
    the compare page too"): black hero, the three products as the pricing
    page's columns (ours told apart by an orange top rule only), the
    price-argument as a black band, the matrix on hairline rows with the
    mono for answers (stacks to labelled cards under 900px, unchanged), the
    landing's FAQ rows, a black close, the one-row footer. Lobster is gone
    from the site entirely; `test_compare` pins the display voice and the
    shared bar/footer instead. `compare_content` now says "Highlight clips"
    (was "crowd suggestions" — a mechanism tell) and derives the closer's
    free-plan note from PLAN_LIMITS. **The credits section (`#credits`,
    `CREDITS` in compare_content)** was the owner's next ask ("they also
    charge for credits"): five rows (what is metered, what the plan
    includes, when it runs out, unused allowance, paid extras) × three
    columns, dated `CREDITS_CHECKED_ON` and linked to the companies' own
    help pages. Researched 2026-09-02 via search — the official pricing
    pages and help centres are egress-blocked from the dev container, so
    the figures come from the help-page snippets and three 2026 write-ups
    that agree; the owner should eyeball the linked pages once from a
    normal browser. Facts recorded: Opus — 1 credit = 1 minute of upload,
    60/150/300 a month, no standalone credit purchase (re-buy the plan or
    add a 300-credit + 2-seat pack), no rollover, monthly credits expire
    after 60 days / annual after 12 months, free cannot buy; Eklipse — YT
    Credits in minutes (30 free, 600 on Premium), next pack $39.98/mo for
    1,200 or $299.98/yr for 14,400, beyond that via support, free cannot
    buy, Pro Edits $18.99 / 3 for $49.99 / 7 for $99.99, VIP Pass per game
    unless annual, $27.99 in the mobile apps. Content updates in `tutorial_content.py`:
    Highlight clips say quality + green label only (no mechanism), the two
    "your trial includes it" leftovers are gone, the Account body and the
    stream-limit answer derive their numbers from PLAN_LIMITS, the Kick
    question is gone (scrubbed from marketing), and three questions were
    added (green label, clips kept per week, cancelling). The design-token
    tests run on the tutorial too: spacing on the scale, integer font sizes.
- **Landing v3 (2026-09-02).** A full "spec sheet" rebuild (hairline grids,
  mono labels, no Lobster, orange-only accent) shipped as `df56cbf` and was
  **rejected by the owner within the hour** ("you made it worse … poor and
  rushed"; reference shown: Squarespace's landing — big product imagery,
  personality). It was reverted wholesale (`716f411`) rather than patched.
  What the owner asked for instead, and what is live now:
  - **The nav is FIXED over the top of the page**, over the cover, with no
    lines (`.nav{position:fixed;top:0}`; still the hero band's #09070C tone,
    no border/backdrop). It is the first element in `<body>`. The hero is
    `min-height:100svh` with `padding-top:calc(var(--nav-h) + var(--s-3))`
    to clear it; `slideTo(coverEl.offsetHeight)` still lands slide 2 exactly.
  - **The tour** (`#tour`, right after the hero): Lobster title, a row of
    mono tabs, and a Squarespace-style row of six big cards that run off the
    right edge — each a REAL dashboard screen. Images are
    `static/landing/tour-*.webp` (23–40KB each), cropped from the tutorial
    captures with the sidebar removed (`crop (104,0,1440,835)` → 1200×750,
    q82; regenerate with PIL if the captures change). Cards carry the page's
    215deg rim light and the product's 14px radius; the active card gets the
    flare rim. Tabs ↔ row are linked by IntersectionObserver (root = the row)
    and `scrollTo` on click; a native scroll container, so it works with JS
    off. `test_the_tour_shows_the_real_screens_as_images_not_lifted_dom`
    pins: shipped WebP, width/height, lazy, real alt, tabs == cards.
  - Everything else below the hero is the page as the owner had shaped it
    over the previous sessions (stagger How-it-works + formula, grouped
    features with gold titles, pricing ladder, 16-question FAQ list, Lobster
    titles). **Do not "redesign" those again without a screenshot-level
    brief from the owner.**
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
| `UPLOADS_ENABLED` | drag-and-drop upload + library + the editor | **Released to Pro 2026-09-15; defaults True.** Now the kill switch, not the release gate. |

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

### Clip Editor rebuilt for smoothness (2026-09-08)

Same features (trim, shape, crop/blur fill, zoom + position, title text,
auto-captions, export to the Scheduler), rebuilt so they are fast to use.
What changed and why, in `ClipEditor` / `EdTimeline` / `buildThumbs` /
`outputSize` in `aurora_html.py`:

- **Timeline is a filmstrip with draggable cut points.** 16 thumbnails from a
  second muted video element (`buildThumbs`, batched in as they are made),
  the cut lit and the rest dimmed, in/out handles and a playhead all driven
  by one pointer model with pointer capture (a drag survives the finger
  leaving the strip). Dragging a handle seeks the preview to that frame.
  Keyboard: Space/K play, ←/→ one frame (Shift = 1s), I/O set start/end at
  the playhead, Home/End, Esc closes. Preview loops inside the cut.
- **The stage is the framing control.** Drag pans, wheel and pinch zoom
  (wheel is a hand-wired non-passive listener; React's is passive and the
  page would scroll). Sliders remain for precision.
- **Paint on demand.** One rAF loop registered once, reading everything
  through `latest` (a ref refreshed by render). A paused editor paints only
  when `dirtyRef` is set (any draw state change, `seeked`, `loadeddata`);
  playing or exporting paints every tick. Measured in Chromium: 0 paints/s
  paused, 60 playing. `seeked` marking dirty is what makes a scrub show the
  frame under the finger rather than the previous one.
- **Export cannot hang any more.** The old editor hung at 0% if you pressed
  Export while the preview was playing (reproduced in the harness: 90s and
  never finished). `runExport` pauses the preview first; every wait in
  `exportRecorder` is time-boxed (`AudioContext.resume` 1s, the in-point
  seek 3s, a stall guard of real time + 6s on the recording loop, `onstop`
  4s). Verified: export-while-playing of a 5.4s cut finishes in ~6s and the
  file decodes at 720x1280.
- **Output size follows the shape** (`outputSize`): 9:16 → 720x1280 or
  1080x1920, 1:1 → 720/1080 square, 16:9 → 1280x720 or 1920x1080, HD only
  when the source's short side is ≥1080. 16:9 used to render 2276x1280.
- **Side panel is tabbed** (Trim / Frame / Text / Captions, the last only
  when `captionsOn`) with the export button pinned in a footer; on a phone
  the editor is the whole screen and the footer is sticky.
- **Two rendering traps, both pinned by tests:** (1) a canvas as a grid
  item with `max-width/max-height` did NOT scale in Chromium — it sat at
  native size cropped to the stage top, so captions and the bottom of every
  frame were off screen; the canvas is now `position:absolute;inset:0;
  object-fit:contain` inside a `display:block` stage with a definite
  height. (2) the late `@supports .glass` gradient made `.ed` near
  transparent and the library page bled through on a phone; `.ed.glass` is
  solid `--rd-bg-2`.

**Blur fill and captions, quality pass (2026-09-08, same day).**
- *Blur backdrop* (`blurBackdrop`): two offscreen downscales (1/4 → 1/16 of
  the output) and one smoothed upscale; the single `ctx.filter` pass runs on
  the 1/16 canvas (a few thousand pixels) and only where filters exist. It no
  longer needs `ctx.filter` at all — the old version fell back to a plain
  crop without it (Safari < 18, some Firefox), and on browsers that had it
  ran a full-frame 28px blur on every tick during playback AND export.
  Measured in software-rendered Chromium: 22 fps → 50 fps in blur mode. A
  soft shadow rect under the contained picture separates it from a backdrop
  made of the same colours. The two offscreen canvases are module-level and
  reused (`_BG`).
- *Captions* (`drawCaption`): Inter (the dashboard's self-hosted face —
  the old code asked for Sora, which the dashboard never loads, and drew
  the fallback), preloaded with `document.fonts.load` before the first paint
  so an export never starts in a fallback font. Styles: **Outline** (thick
  dark stroke + soft shadow) or **Boxed** (rounded plate per line), optional
  **ALL CAPS**, and **Word pop**: the word being spoken lit in `#F7A745`.
  Word pop needs per-word timings, which cues now carry —
  `transcribe.Segment.words = [[start, end, word], …]`, filled by
  `_cues_from_words` (empty when a segment had no word timings, so nothing
  is invented; the editor then draws the line unlit). Captions produced
  before this change have no `words` and simply render without the pop;
  regenerating adds it. Word-by-word layout is measured, so the line is
  centred as a whole and wraps at 86% of the frame, max three lines.
- Verified by exporting in blur mode with captions and decoding a frame out
  of the file (`shots/*-file-frame-*.png`): backdrop, plate/outline and the
  lit word are all in the bytes, not just the preview.

**Export sharpness (2026-09-08, same day).** Owner: "after editing, some
clips get blurry". Measured with `scratchpad/ed/sharp.js` (variance of the
Laplacian on a decoded frame of the export vs the source frame drawn the
same way) and `bitrate.js` (does the recorder honour `videoBitsPerSecond`?
Yes, proportionally: 6 Mbps asked → 3.4 achieved, 16 → 7.8 on detailed
content; the flat test clip is content-limited so it cannot show the gain).
Three fixes, all pinned in `test_dashboard_contract.py`:
1. `paintFrame` sets `imageSmoothingQuality='high'` — every crop is an
   upscale (16:9 → 9:16 is 1.78x) and the default resampler is bilinear.
2. Bitrate budget 16 Mbps HD / 10 Mbps SD (was 9 / 6): a 1080p Twitch clip
   arrives at 6-8 Mbps and the old export landed at 1.8 Mbps.
3. `REC_TYPES` lists High (`avc1.640028`) and Main (`avc1.4D401F`) before
   Baseline — same bitrate, more detail (CABAC, B-frames).
Plus `outputSize` now ships the 1080 class (1080x1920 / 1080x1080 /
1920x1080) for any source of 720p and up, reversing the earlier
"never upscale" rule on purpose: the platforms re-encode every upload to
1080x1920 with their own scaler, and a 720x1280 file comes out of that
softer than the same picture delivered at 1080x1920. Export of a 5.4s cut
at 1080x1920 still runs in real time (6.6s) in software-rendered Chromium,
blur layout included. What cannot be fixed here: a 9:16 crop of a 1080p
source is a 607px-wide region; every tool upscales it, and Punch In zooms
it further. If the owner still sees softness on prod, check the SOURCE
(a 720p download from Twitch is the usual culprit) before the pipeline.

**Templates (2026-09-08, same day).** Five one-click starting points in a
row above the side-panel tabs (`TEMPLATES` in `aurora_html.py`), copied from
the formats streamer clips ship in on TikTok / Shorts / Reels (researched
2026-09-08: facecam-top/gameplay-bottom at ~40/60 is the standard; full
frame with word-level captions; blurred fill keeping the 16:9; punch-in
reaction zooms; a hook line at the top). Each is a complete set of the
editor's own knobs (shape, layout, fill, zoom, position, caption
position/style/caps/word-pop/size, and for the hook the title position and
size), applied through `applyTemplate` → `SETTERS`, landing on the tab that
most wants a human look. Nothing is locked afterwards.

| id | name | what it sets |
|---|---|---|
| `camgame` | Cam + Game | **new `layout:'split'`**: top 40% is a window cut from the source around the camera (`zoom` = tightness, `offX/offY` = where), bottom keeps the whole frame at full width, blurred fill behind both, boxed ALL-CAPS captions `low` (under the gameplay) |
| `full` | Full Frame | 9:16 crop, zoom 1, outlined captions bottom, word pop |
| `blur` | Blur Bars | blur fill, boxed ALL-CAPS captions `low` |
| `punch` | Punch In | crop, zoom 1.35, big (0.07) boxed ALL-CAPS captions |
| `hook` | Hook Title | crop, title at top at 0.09, focuses the text box |

Layout note from the same pass: the export footer (`.ed-foot`) is no longer
inside the side panel. `.ed` is a grid (header across the top; main | side;
footer as its own row under the side column, `.ed-body{display:contents}`
on desktop) so on a phone the footer is a row under the scroller rather
than a sticky element inside it — sticky covered whatever control had
been scrolled to the bottom edge, which the harness hit on every run.

The split layout is also a manual control (Frame tab → Layout). In split
mode a stage drag moves the camera window (sign inverted and scaled by
1/zoom so the picture follows the finger) and zoom goes to 4. `SPLIT_TOP`
is the one constant. Tests: `test_there_are_five_templates…`,
`test_the_split_layout_draws_a_camera_window…`,
`test_the_template_row_is_in_the_panel…`.

Harness: `scratchpad/ed/` — `mkclip.js` records a 12s test clip in
Chromium itself (no ffmpeg on the box), `harness.js` serves the real
`DASHBOARD_HTML` with vendored React and stubbed `/me`, `/uploads`,
`/publish/*`, and `edrun.js` drives open → scrub → drag handle → I/O →
wheel/pan → shapes → blur → play → export-while-playing → catches the
download and decodes it. This Chromium has no H.264, so its MP4 is VP9 in
fMP4; real Chrome picks `avc1/mp4a` from `REC_TYPES` as before.

### Editor quality pass — export, preview, captions (2026-09-15)

Owner: "still not the highest quality … make everything clean and smoothly
working. Captions are still crappy and the fonts look pixelated." Everything
below was measured before it was changed; the harnesses are in the session
scratchpad (`ed/preview_sharp.js`, `ed/export_fps.js`, `ed/export_fa.js`).

**The export was the smoothness problem, and it was structural.** MediaRecorder
records the canvas in REAL TIME: it keeps whatever frames the encoder finishes
before the next one lands and drops the rest, and spends bitrate on its own
schedule. On the real paint path (blur fill + caption) in software-rendered
Chromium: `captureStream(30)` → **10.8 fps in the file**, `captureStream(60)`
→ 11.6, at **1.5 Mbps against 16 asked for**. A laptop GPU does better, but
smoothness depends on the machine and 60 fps is never guaranteed.

**Frame-accurate export (WebCodecs), now the default where the browser can:**
`exportFrameAccurate` in `aurora_html.py`. `requestVideoFrameCallback` hands
over every presented frame; the canvas is painted for exactly that media
time; the frame goes to a `VideoEncoder`. Three things make it lossless:
encoder backpressure PAUSES playback until the queue drains (never drops);
an element skip (`presentedFrames` jumps) halves `playbackRate` AND seeks
back to the last encoded frame so the skipped ones are presented again; and
re-presented frames are deduplicated by media-time timestamp. Audio is
decoded OFFLINE from the source (`decodeAudioData` → `OfflineAudioContext` at
48 kHz → `AudioEncoder`), so it cannot drift or stall. Container by vendored
muxers in `src/dashboard/static/vendor/` (mp4-muxer 5.2.2 / webm-muxer
5.1.4, MIT, unmodified IIFE builds — the raw encoder output is an elementary
stream nobody can open, which is why this was never wired before): MP4
(H.264 + AAC) where those encoders exist, else WebM (VP9 + Opus).
`frameAccurateSupport(w, h, fps)` probes with the REAL output — H.264 level
4.0 is only rated to 1080p30, so 1080x1920@60 asks for 4.2 first.
MediaRecorder stays as the fallback (now `captureStream(60)` for HD) and
`runExport` sizes the canvas to the output synchronously before either path.
**Measured (`export_fa.js`, VP9 branch, this container):** 60 fps source →
**62 fps file, 154 frames, every presented frame kept** (161 callbacks, 7
re-presented dupes correctly skipped); 30 fps → 30.4 fps, 76 frames, done in
half real time. A decoded frame of the output carries the blur, the title
and the lit caption word. **Unverified here, on purpose stated:** the MP4
branch (H.264/AAC) — this Chromium has only the free codecs; the code path is
identical with a different muxer and runs on any user's Chrome/Edge/Safari.
If an export ever comes out wrong on prod, the first check is
`frameAccurateSupport` in the console: `null` means the recorder ran.

**"Fonts look pixelated" — not reproducible, and the bitmap is sharp.**
`preview_sharp.js` renders the same frame two ways at 2x: the shipped
1080x1920 canvas CSS-scaled into a 236px stage, and a canvas painted at the
stage's own device-pixel size. **Laplacian variance 1374 vs 1376 — identical
on screen**, and the export bitmap at 1:1 is clean Inter 800 with a crisp
stroke (`shots/export-bitmap-caption-1to1.png`). The font is a real variable
face (400–800), so 800 is not synthesized. Whatever the owner saw is either
their display path or the old low-fps/low-bitrate export smearing text —
the export fix is the likeliest cure. Ask for a screenshot before touching
`drawCaption` again.

**Preview is painted at display size anyway** (`stageSizeRef` fed by a
`ResizeObserver`; the paint loop sizes the backing store to stage × DPR,
capped at the output). Same picture, about a fifth of the pixels per tick —
that fifth was the stutter on a laptop GPU or a phone. The export resizes to
the real output before it starts and `busy` holds it there.

**Captions: the defaults were the problem.** `tiny.en` / greedy / no prompt —
the cheapest settings Whisper has — and the prod A/B (`caption_model_test`)
showed tiny.en mis-hearing and greedy decoding TRUNCATING the tail of what was
said. Defaults are now **`base.en`, beam 5**, and a generic register prompt
("Live Twitch stream. A streamer reacts and commentates over gameplay, with
chat.") — generic on purpose: a prompt that names a word tempts a small model
to insert it. Cost is a slower caption job, never a slower clip (serialised,
timeboxed, detection wins the core). `CAPTIONS_MODEL=tiny.en` puts it back.
The cue shaping (`_MAX_CUE_*`) and the word-pop rendering were already the
short-form standard and are unchanged.

**Transitions and sound effects in the templates (2026-09-15, same day).**
Owner: "our editing preset models add sound effects and small transitions."
- *Transitions* are pure functions of media time inside `paintFrame` /
  `drawCaption`, keyed off `o.inPt`/`o.outPt` (Infinity when there is no
  cut, which disables them with no special case): a zoom punch (1.12x → 1x
  over `TRANS_DUR` 0.45s, a transform around the whole video block so every
  layout gets it), fade in / fade out painted LAST over captions and title,
  the title rising into place over 0.35s (`textAnim`), and a caption
  word-pop (1.14x → 1x over 90ms, scaled about the word's centre; needs the
  word's start time, which timed cues now carry as `st`). One draw path
  still — the preview and both exporters get the identical frame.
- *Sound effects are SYNTHESIZED* (`SFX` in `aurora_html.py`: whoosh, hit,
  pop, riser, ding), not sample files: no asset to licence, nothing the
  dashboard's script rules would have to allow, and the same function
  schedules the same nodes on a live `AudioContext` (preview and the
  recorder export, through a new `sfx` bus on `audioGraph` that feeds both
  `monitor` and `dest`) and on the `OfflineAudioContext` the frame-accurate
  export renders through — so the file carries exactly what the preview
  played. Noise is seeded, so two renders of one clip are byte-identical.
  `sfxPlanFor(span, in, out, gain)` is the plan: one sound at the cut's
  start, one landing 0.35s before its end. A silent source with effects
  still gets an audio track.
- *Verified* (`export_fa.js`): a silent source exported with a whoosh at 0
  and a ding at 2.1s decodes to RMS 0.015 / 0.000 / 0.111 in the whoosh,
  quiet and ding windows; luminance 0 at t=0.02 (fade in), 12 at the tail,
  44 mid-clip; still 153 frames at 61 fps. The whoosh and riser gains are
  ~4x the others' because a bandpass on noise sheds most of its energy —
  measured at 0.9 the whoosh peaked at 0.10 against the hit's 0.60.
- The five templates each carry `transIn / transOut / textAnim / sfxIn /
  sfxOut`; the **Effects** tab exposes them plus a volume. Pinned in
  `test_dashboard_contract.py`.

### Editor audit: black stage and white dropdowns (2026-09-16)

Owner: "A ton of the features in the editor are bugged out. The formats are
just making the screen black with some clips. The drop downs are white and
you cant read the words. Check for all the problems." Audited in the
browser harness (`scratchpad/ed/editor_audit.js`: opens a landscape and a
portrait WebM, clicks every style, ratio and fill, plays, types a title,
exports, and measures the preview canvas's mean luma so "black" is a
number). Findings, both reproduced then fixed:

1. **Black stage = the fade-in at the in-point.** Full Frame and Blur Bars
   carry `transIn: 'fade'`; a paused editor sits at t = inPt, where the
   fade is 100% black (luma 0 on both clips) and the title's rise has it
   below the frame. Nothing was wrong with the picture. Fix: the preview
   loop passes `settled: !live` and `paintFrame` treats `settled` as "no
   cut here" (`tin`/`tout` = Infinity), so a paused frame shows the
   picture as it will look once the transition is over. Playback and
   export (`live`) still paint every transition. After: luma 83 / 71 on
   Full Frame / Blur Bars. Pinned by
   `test_the_paused_preview_is_drawn_settled_so_a_fade_in_is_not_a_black_stage`.
2. **White dropdowns = the OS popup on a light-scheme page.** `select.ed-in`
   (sound in/out, Autopilot spacing) had light text and options with a
   transparent background, so Windows/Chrome drew a white list with
   near-white words. Fix: `select.ed-in, select.rd-select {color-scheme:
   dark}` and `option {background:#15151c; color:var(--fg)}`.

Checked clean in the same run: all five styles, 9:16 / 1:1 / 16:9, blur
and crop fills, playback, the title, and an export that renders (WebCodecs
in headless Chromium). No page errors. Not checkable here: captions (server
Whisper) and sound effects (AudioContext output).

### Editor library screen facelift (2026-09-16)

Owner: "still way too cluttered. I dont want the tut on the top I want it
to be a button… a complete facelift… less going on in general." The screen
that opens on the Clip Editor tab (`UploadScreen`) is now three things:

- **Header row:** title + count, then two buttons on the right. `How it
  works` toggles the three-step strip (same `rd-how` markup, inside
  `.rd-howbox` with a close X, state `showHow`, off by default). `Add a
  clip` (gradient) opens the file picker.
- **Drop strip:** `.rd-drop` big when the library is empty, `.rd-drop.slim`
  (one line) once there are clips. The "Add clips" / "Your clips" cards,
  their descriptions and the "Or edit one you've already uploaded" chip row
  are gone; the grid (`.rd-lib-grid`) is the only list and each card's
  gradient **Edit** button is the way into the editor. Autopilot renders
  show "· Autopilot" under the name.
- **Footer:** one quiet quota line (`.rd-lib-foot`), only when there are
  clips. The admin preview banner stays (admins only, `.rd-lib-admin`); the
  Twitch import card (`TwitchImport`, behind `CLIP_IMPORT_ENABLED`) now
  sits BELOW the library instead of above the drop zone.

Tests repointed: `test_already_uploaded_clips_are_one_click_from_the_editor`
now checks the grid's Edit button and forbids `rd-picks`;
`test_the_walkthrough_is_a_button_not_a_banner` pins the toggle. Verified
in the Playwright harness (`scratchpad/ed/lib_shot.js`: desktop, phone,
empty, walkthrough open).

### Approved clips populate the editor on their own (2026-09-16)

Owner: "make it so auto accepted clips populate in the editor." Approving a
clip now copies its file into the Clip Editor library without the Edit
click. `api.library_copy_if_approved(clip_id)` runs detached from
`approve_clip` (via `runner.kick`, so the click returns at once) and is
awaited first thing in `on_clip_file_ready`, which covers a clip approved
before its capture or Twitch fetch landed. The copy code is shared with the
Edit button (`_copy_clip_into_library`): `save_stream` with
`source="clip"`, the `editor_upload_id` link-back under `_data_lock`, and
the `upload_added` + `clip_updated` broadcasts, so the library card appears
live and the card's Edit reuses the copy.

Quiet, never raises, on: plan without the editor or `UPLOADS_ENABLED` off
(`_can_use_editor`), clip not approved, no file yet, already linked to a
living upload, library cap (`UploadError` → `clip_auto_library_skipped`
log). Nothing fetches from Twitch just for this: the existing
`_fetch_when_capture_misses` policy brings the file and re-enters the hook.
Autopilot's render still lands as a second, `source="render"` card. Tests:
`tests/test_clip_auto_library.py`.

### Platform-switch sweep (2026-09-16)

Owner: "the transition between kick and twitch … right now the only thing
that changes is the color." `switchPlatform` in the app now runs a sweep:
`platFx = {to, n}` renders `.plat-wipe` (fixed, z-index 900, pointer-events
none), whose `.plat-wipe-band` is a skewed full-bleed band in the target
platform's gradient (Twitch `#9146ff→#7c6bff`, Kick `#53fc18→#39b515`)
that crosses left-to-right in `PLAT_SWEEP_MS = 800` with "Switching to
Kick/Twitch" on it. Keyframes hold the band fully across from 36% to 64%
(288–512 ms); `setActivePlatform` fires at 50% (400 ms), so the theme and
screen swap while covered, and `platFx` clears at 800 ms. `.rd-screen`
gets `plat-in` once `platFx.to === activePlatform` (a `--dur-slow` fade
and 8 px rise). Reduced motion: no sweep, immediate swap. Clicking the
same platform, or the target of a sweep already running, is a no-op;
timers are cleared on unmount. The animations use `ease-in-out` by
keyword (`var(--ease)` made the entrance a flash: the band was fully
across by 120 ms), and 800 ms is a literal because the transition-token
test only governs `transition:` and `--dur-event` is reserved for the
score wall. Pinned by `test_switching_platform_is_a_sweep_not_a_repaint`.
Frames were checked by pausing `document.getAnimations()` and scrubbing
`currentTime` (`scratchpad/ed/switch_frames.js`), because screenshot
latency made real-time captures land ~200 ms late.

## Announcements — one message in front of every user (2026-09-15)

Owner: "a way to send out notifications for all users ... pop up in front
of the screen." `src/dashboard/announcements.py` (title, body, sent, expires;
`clips/announcements.json`), admin tab **Announce** on `/admin` (compose,
show-for 7/14/30/90 days, list with a read receipt = who pressed Got it,
Retire), dashboard modal `AnnouncementModal` at **z-index 300** — above the
editor (200) and everything else, because the one job of this surface is to
be impossible to miss. Plain text, line breaks kept, React-escaped.

**Two delivery paths, on purpose.** `POST /admin/announcements` broadcasts
`announcement` with `user_id=None` — the one genuinely global broadcast —
so every open tab gets the modal at once. `GET /announcements` (active
minus what this account dismissed) is pulled on mount and in `refetchAll`,
so everyone who was offline sees it on their next open. A message only the
people online at that second saw would miss most of the accounts it was
written for. Dismissal (`POST /announcements/{id}/seen`) is persisted on the
user record (`announcements_seen`, bounded to 50) and broadcast to that
user's other tabs; `DELETE /admin/announcements/{id}` retires it and
broadcasts `announcement_retired` so it leaves an open modal too. Several
waiting show one at a time, oldest first. Every announcement expires
(default 14 days, max 90) and the store prunes expired rows on every write.
20 tests in `tests/test_announcements.py`, including both halves of the
realtime contract.

## Publishing — the Scheduler POSTS (2026-09-15, reversing 2026-08-02)

**Owner: "I need the scheduler to be working and integrated now" — real
auto-posting, all three platforms, open to Pro now.** Until this day the
Scheduler was a reminder queue by design (the paragraph below, kept because
its costs are still the costs). The owner chose to pay them.

**The August reasoning, still true:** posting through TikTok's Content
Posting API, Instagram's Content Publishing API or YouTube's Data API each
require the app to pass platform review — weeks of calendar time — and
YouTube's default 10,000 units/day against 1,600 per `videos.insert` caps the
ENTIRE app at six uploads a day until Google raises it. Instagram will not
accept bytes; it fetches from a public URL, i.e. we serve user video publicly
for a few minutes. Each of those is now handled rather than avoided:

- **YouTube** (`src/publish/providers/youtube.py`): Google OAuth
  (`youtube.upload` + `youtube.readonly`, `access_type=offline&prompt=consent`
  so a refresh token always comes back), resumable upload streamed from disk
  in 1 MB blocks. Title = first line of the caption (100 chars), description
  = the caption, category Gaming, public. A `quotaExceeded` is a RETRYABLE
  failure worded as ours ("YouTube's daily upload quota for Highlightz is
  used up"), never the user's. **Ask Google for a quota increase as soon as
  there are users** — six posts a day across the whole app is the default.
- **TikTok** (`tiktok.py`): Login Kit with PKCE, Content Posting API direct
  post. `creator_info` says which privacy levels the account may post at;
  until TikTok audits the app that is `SELF_ONLY` only, so the post lands
  PRIVATE and the card says so (`note` on the result) — the user flips it
  public in the app. Chunked by TikTok's rules (`chunk_plan`): one chunk up
  to 64 MB, else 32 MB chunks with the remainder folded into the last.
- **Instagram** (`instagram.py`): "Instagram API with Instagram Login" (no
  Facebook Page). Account must be Professional (Business/Creator) — a
  personal one is refused at connect with the reason. Reels via a container
  built from `video_url`, polled to FINISHED, then `media_publish`. The
  `video_url` is **`/media/<signed token>`** (`src/publish/media_link.py`):
  HMAC over upload id + expiry, one hour, the ONLY video route with no
  session (`_OPEN_PREFIXES` in api.py). WebM is refused before any network
  call. The 1-hour short token is swapped for the 60-day one at connect and
  refreshed whenever under a week remains (`refreshes_without_refresh_token`).

**Operator setup (once, per platform; a blank id = the Connect button says
"Coming soon" to users and "add the app keys" to admins):**
1. `PUBLIC_BASE_URL=https://highlightz.app` (default). Redirect URIs are
   derived from it: `https://highlightz.app/publish/connect/{youtube,tiktok,
   instagram}/callback` — register those three exact strings.
2. Google Cloud console → APIs & Services → enable *YouTube Data API v3* →
   Credentials → OAuth client (Web application) with the youtube callback →
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. OAuth consent screen: add
   the two scopes; until it is verified, add testers there (100 max) and
   expect the "unverified app" interstitial.
3. TikTok for Developers → app → add *Login Kit* and *Content Posting API*
   (Direct Post), scopes `user.info.basic`, `video.publish`, the tiktok
   callback → `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`. Submit for
   audit when ready; posts are private until then.
4. Meta for Developers → app → *Instagram* product → "API setup with
   Instagram login" → the instagram callback under *Business login
   settings* → `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`. Add Instagram
   testers (Roles) until App Review grants
   `instagram_business_content_publish`.
5. Restart. `/publish/connections` reports `configured` per platform.

**What exists now:**
- `src/publish/connections.py` — one connection per user per platform,
  tokens Fernet-encrypted with the same key as Twitch's (`users._encrypt`),
  written through `atomic_write_json` (0600). `public()` never carries a
  token; the API payload and every broadcast are built from it.
  `last_error` marks a dead grant; `connected_platforms()` excludes those,
  and the card shows the error with "Connect again". Account deletion
  removes them.
- `src/publish/poster.py` — the worker. `post_due()` runs inside
  `schedule_due_task` every 30 s: due + pending (or FAILED with only
  retryable failures, after a 15-min backoff, inside the 24 h grace) + a
  connected platform → `post_item()`. Serial on purpose (1 vCPU; uploads
  are network-bound). Each platform's outcome is written to
  `Item.results[platform]` and broadcast as `schedule_updated` BEFORE the
  next platform is tried, so the card shows "Uploading… / Posted · View /
  error" live and a restart loses at most the platform in flight. **A
  platform whose result is `posted` is never tried again** — by the worker,
  by Retry, by a second Post now (`_inflight` guards the double click).
  `reauth` errors also set `last_error` and broadcast
  `publish_connections_changed`.
- `schedule.py` — statuses `pending → posting → posted | failed` (+ the old
  `skipped`), derived in `mark_result` from the rows so summary and rows
  cannot disagree; `reset_for_retry` drops failures, keeps successes;
  `due_for_posting` includes FAILED so retryable ones get another go.
  `newly_due` still nudges — but only for platforms the user has NOT
  connected (a "time to post" toast beside "Posting to YouTube…" reads as a
  contradiction).
- Routes (all behind `_require_upload_access`): `GET /publish/connections`,
  `GET /publish/connect/{p}` (state + PKCE verifier in the session),
  `GET /publish/connect/{p}/callback` (→ `/?connected=p` or
  `/?connect_error=…`, which the dashboard turns into a toast and the
  Scheduler tab), `DELETE /publish/connections/{p}` (best-effort revoke),
  `POST /publish/schedule/{id}/post` (Post now / Retry, 202, background).
  Plus the sessionless `GET /media/{token}`.
- Dashboard: Scheduler released to Pro (`adminOnlyTabs = []`, paywall card
  below Pro). **Rebuilt as a calendar the same evening** (owner: "a little
  too confusing … simpler … sleek … a real calendar"): `AccountChips` (one
  row: Connected as … ×, reconnect, Connect, soon), `InboxTray` (exports
  with no time yet, draggable), `MonthCalendar` (7×5/6 grid, a chip per
  clip colored by state, drag between days — a drop keeps the clip's time
  of day or gives it `SC_DEFAULT_HOUR` 18:00, resolved from LOCAL fields to
  an instant), `DayList` (the selected day; on a phone the cells only show
  dots so this is where the names are), and `ScheduleDrawer` (video,
  caption, platform chips with the **Auto** tag, "Posts at" vs "Remind at"
  `datetime-local`, results with the link and TikTok's private note, Post
  now / Retry; Share / Mark posted only when some chosen platform is
  manual). The drawer is DERIVED from the queue (`items.find(...openId)`),
  so a result over the socket updates it in place. The 1-2-3 strip only
  renders on an empty queue. Two things that bit: calendar state classes
  are `is-*` because a bare `.today` is the TodayHeader's global rule, and
  the grid tracks are `minmax(0,1fr)` or a long filename widens the cell.
  Realtime: `connections` in `refetchAll`, `publish_connections_changed`
  refetches, `schedule_updated` carries the results.
  `test_the_queue_says_exactly_when_it_posts_and_when_it_only_reminds` pins
  the wording both ways; `test_the_scheduler_is_a_calendar` pins the shape.
  Rendered and eyeballed in headless Chromium at 1280 and 400 wide
  (scratchpad harness, not checked in).
- 38 tests in `tests/test_publish_posting.py`: encryption at rest, scoping,
  signed link forge/expiry, status derivation, never-twice, dead-token
  handling, backoff, each provider against canned HTTP (resumable upload,
  TikTok chunking + forced-private note, Instagram public URL + polling),
  the routes and the UI strings.

**Not done, deliberately:** scheduling per platform at different times
(one time per item); YouTube
Shorts-specific metadata beyond vertical + ≤60 s (YouTube decides Shorts
from the file); the `ORG_PROFILES` still empty.

**Tabs:** Clip Editor is for CUTTING; **Scheduler** is for POSTING. Every
export uploads the render back to the server (`POST /uploads?source=render`)
and drops it in the Scheduler. That round-trip is not bookkeeping — it is
what the poster uploads from, and a blob in one tab's memory is unreachable
from the phone that has the TikTok app on it for the manual path.
`source="render"` keeps exports out of the editor's source-clip picker; they
are output, not input.

**What existed before (still there, for platforms not connected):**
- `src/publish/platforms.py` — the single source of the per-platform limits
  (ideal vs hard duration, caption cap, preferred ratio, upload URL). Carries a
  checked-on date; these move, and a stale limit is worse than none because it
  is confidently wrong.
- Editor "Post it" panel: keeps the exported blob, offers `navigator.share`
  with the file (the OS share sheet — TikTok/IG/YouTube, one tap, on a phone),
  a caption box, and a per-platform FIT CHECK.
- `src/publish/schedule.py` + `/publish/schedule` — the queue itself.

**Editor side panel (simplified 2026-09-15).** Owner: "still looks very
cluttered … big, easy and professional … not too big." Two numbered steps:
**1 Pick a style** — the five TEMPLATES as large cards (`.ed-tpl-row`, two
per row, diagram beside the name); **2 Adjust** — the old five tabs are
collapsible sections (`.ed-sec`), one open at a time (`tab` is the open
key; a template still opens its own section), each header carrying a
one-line summary of its current setting so the whole state reads closed.
Help text is one line per control. "Title rises in" lives in Text, not
Effects. Nothing in paintFrame/export changed. Rendered and eyeballed at
1380 and 400 wide with a browser-generated clip (the scratchpad harness
makes a WebM with canvas + MediaRecorder; there is no ffmpeg in the dev
container).

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
4. **The card says which platforms are posted FOR the user and which they
   post themselves** (`test_the_queue_says_exactly_when_it_posts_and_when_
   it_only_reminds`). Before 2026-09-15 the rule was "the queue reminds; it
   cannot post" for the same reason in the other direction: a queue that
   misdescribes what will happen costs someone a posting slot or posts
   something they did not expect.

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

## Autopilot — approve → render → post, with nobody in the loop (2026-09-15)

Owner: "an autopilot thing for people with pro that can auto grab accepted
clips, edit them and post." Pro only, per user, OFF by default, switched on
from the Scheduler tab (`AutopilotCard`, above the calendar).

**The chain, and what writes what:**
1. `POST /clips/{id}/approve` → `runner.kick(maybe_run(clip))` (detached;
   the approve click returns at once). A clip whose file lands AFTER the
   approval is caught by `api.on_clip_file_ready(clip_id)`, which every
   file-producing path calls (capture cut in stream_worker, both Twitch
   fetch paths).
2. `runner.process_clip`: `clip["autopilot"] = {status}` at every step —
   `waiting_file` (no file yet; still eligible), `rendering`, `scheduled`
   (`item_id`, `due_at`, `platforms`), `failed` (`error`). Each write is
   `_save_clips()` + a `clip_updated` broadcast, so the card badge
   (`.rd-apbadge`) follows live. `_inflight` stops a double run.
3. `src/autopilot/render.py`: ONE ffmpeg pass, `libx264 veryfast crf 20`,
   1080×1920, templates `full` (centre crop), `punch` (1.25× zoom), `blur`
   (16:9 over a blurred fill), `hook` (full + boxed title). Title and
   captions are `drawtext` (escaping in `_esc`; `enable=between(t,s,e)` per
   cue, ≤120 cues); fades in/out 0.4 s video+audio. **No Cam + Game**
   (needs a human to point at the camera) and **no SFX** (browser-only).
   `_slot` = one render at a time — 1 vCPU that is also scoring streams.
   Font: `AUTOPILOT_FONT` (DejaVu Sans Bold by default); missing font =
   no text, logged `autopilot_font_missing`, never a failed render. The
   card shows a warning when `/autopilot` reports `font_ok: false`.
   Captions come from the same Whisper pass the editor uses
   (`transcribe.load` cache beside the file, else `transcribe`), only when
   `CAPTIONS_ENABLED` and the user ticked it; any failure = no captions.
4. The render is copied into the uploads library (`source="render"`, so
   it is in the editor's library too, never in its source picker), then a
   schedule item with `source="autopilot"` — a NEW `Item` field, "" for
   everything else — on `cfg.platforms ∩ connected`. Nothing connected =
   a reminder on the calendar, which the card says out loud.
5. The poster posts it like any other item.

**Timing (`autopilot.next_due`, pure, tested on fixed clocks):** `now` =
+60 s; `spaced` = `spacing_h` after the LAST autopilot item's due
(`schedule.last_due_from(uid, "autopilot")`), so an evening of approvals
becomes a run of posts; `daily` = next `daily_at` in the USER's zone
(`tz_offset_min`, sent by the browser on every save), one per day.
Caption from `caption_text` with `{title} {channel} {game} {platform}`
(unknown keys blank, never an error).

**Config** lives on the user record (`users.autopilot_for` /
`set_autopilot`, normalised by `autopilot.normalize`: unknown keys dropped,
ranges clamped). Routes, all `_require_upload_access`: `GET/PUT
/autopilot`, `POST /autopilot/run` (the bulk button: approved this week,
has a file, not rendering/scheduled — failed ones get another go, ≤10).
`autopilot_changed` broadcast; `/autopilot` in `refetchAll`.

**Verified on prod (2026-09-16):** the droplet's ffmpeg has `drawtext` and
`/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf` exists. The render
itself is the same ffmpeg the capture cut uses. 18 tests in
`tests/test_autopilot.py` build the command rather than run it (no ffmpeg
in the dev container). An end-to-end render on prod is still untested.

## Public copy matches the product again (2026-09-16)

Owner: "Online it still picks up that we create native twitch clips we
need to change that to be accurate to how everything is working now."
Every public surface used to describe the 2026-07 model — "a native Twitch
clip, nothing re-uploaded, nothing re-encoded, no video file" — which
stopped being true once uploads, the editor, the Scheduler and Autopilot
shipped. What the copy says now, and where:

- **Twitch:** a real Twitch clip under your account through the official
  API, AND the video file, which the editor, Scheduler and Autopilot use.
  Files live on the storage timer (`RETENTION_DAYS`), same as before.
- **Kick:** the file IS the clip (no clip API); open to everyone since
  2026-09-16 (see the Kick section above). The landing page's own sections
  still lead with Twitch; the title, meta and llms copy say Twitch and
  Kick. `platform_url` is the clip's link.
- **Vertical reframe + captions, auto-posting:** the compare page rows
  (`compare_content.py`) flipped to True for Highlightz; a watermark note
  says exports carry none.
- **Legal:** ToS §1/§5 and the data sentence, Privacy uses + 6a, the
  opt-out page, `llms.txt` / `llms-full`, landing hero line and alt texts,
  FAQ "What does Highlightz actually do?" / "Do you record or store my
  stream?", compare FAQ "Do you re-upload or re-host my video?", tutorial
  "Are clips actually posted to my Twitch?" / "Do you record my stream?".
- **Still secret:** HOW a highlight is found is not on any of these pages
  (`test_public_exposure.py`); the tutorial page must not name the
  Scheduler / editor (`test_tutorial.py`), so it says "the posting queue".
- **"It still says we are only Twitch" (owner, later the same day):** an
  AI summary of the site cited the page `<title>` ("Automatic Twitch
  Clipper"). The title, meta description, og/twitter descriptions, both
  JSON-LD descriptions (SoftwareApplication in `LANDING_HTML`, WebSite in
  `_org_schema`), the llms-full lead and the llms.txt "Monitors…" bullet
  now say "Twitch and Kick" and mention the vertical editor and
  auto-posting. Kick opened to everyone the same day; llms.txt's Notes
  say "open on every plan". Sign-in copy stays Twitch (it is).
- **Kick on the landing page (owner, same day: "market kick on the landing
  page"):** the proof note reads "one channel, Twitch or Kick"; the watch
  section's sub says "Twitch and Kick, in the same dashboard" and gains a
  two-tile `.plats` row (`#platforms`: Twitch = real clip via the official
  Twitch API plus the file, Highlight clips too; Kick = no clip API, the
  file is cut from the live broadcast, same signals/queue/editor). Tiles,
  not a section, so the page keeps its eight. Twitch's rule is white:
  `test_the_purple_stays_in_the_logo_and_the_cover` bans `#9146ff` below
  the cover; Kick's is `#53fc18`. FAQ: "Can I clip channels I don't own?"
  now says Twitch or Kick, and a new "Does it work on Kick?" answer. The
  no-JS claims guard needs the literal "official Twitch API" in body text,
  which the Twitch tile carries. Verified in the harness
  (`scratchpad/ed/landing_shot.js`).

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

## The landing clip counter is SERVER-RENDERED (2026-08-03)

It used to be fetched by JS after load, and the tile shipped as
`style="display:none"` containing a literal `0`. Crawlers, link unfurlers and
AI readers overwhelmingly parse the raw response and never execute JS, so they
did not merely miss the number — **they saw zero**, which invites "Highlightz
has captured 0 clips".

`render_landing()` bakes the real count into the response: the visible span,
the tile's visibility, AND the JSON-LD `interactionStatistic`
(`InteractionCounter`), which is where a machine actually looks for a count.
`GET /` must call it — serving `LANDING_HTML` directly puts the bug straight
back, and there is a test for exactly that.

Three things not to break:
1. **JSON-LD carries the RAW integer**, never the comma-formatted string. A
   `"1,234,567"` is not a valid schema.org count and consumers drop it.
2. **Zero clips leaves the tile hidden.** Advertising a real zero is worse than
   saying nothing.
3. **The client animation starts FROM the rendered value**, not from 0.
   Counting up from zero would wipe the server-rendered number for a second and
   put a literal `0` back in the DOM — the exact state a crawler might sample.

Verified with JavaScript disabled in a real browser, which is the only way to
see what a scraper gets.

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

## Crowd suggestions — clips the score never saw (2026-08-26)

**Why.** Clips went viral off channels we were watching and the bot missed
them. Every other path into the review queue runs through a score, and the
n=1001 calibration says the score is close to blind on virality (within-labeler
r = -0.060, within-account AUC 0.547). A path that never consults it is the
only way a missed moment can reach the user at all.

**The one substitution, and it is not negotiable by argument.** The obvious
build is "see viewers clipping, create our own clip of that moment". It cannot
work, and that was settled with data in Phase 0: Twitch takes a **median 167s
(p90 369s)** to make a viewer's clip visible in Helix, and Create Clip reaches
back only ~60-90s. The <45s bar for acting on this was written down BEFORE that
measurement precisely so it could not be rationalised afterwards, and it failed
by a factor of four. **So we create nothing — we surface the viewer's own
clip**, which is already a real Twitch clip with a slug, an embed URL and a
thumbnail. That is strictly better: it is the right 30 seconds because a human
framed it, latency stops mattering, it spends no Create Clip budget, and it
stays inside the compliance model (Twitch hosts it; we store metadata and an
embed URL, exactly as for our own clips). It is also what the VOD scanner
already does — `get_clips_for_vod` merges viewer clips as first-class moments
and badges them "N clipped it". This is that, running live.

**Cost: zero extra Helix calls.** `viewer_clips.poll_and_record` has polled
`/clips` every 90s per channel since Phase 0 for the learning log. The suggester
is a sink on that existing call (`on_rows`), and it deliberately receives EVERY
row rather than only new ones — the poll window (420s) is much wider than the
interval, so a clip returns on several polls and each return carries a fresher
view count for free.

**Shape** (`src/trigger/suggested_clips.py`): candidates ripen for
`SETTLE_SECS` (180) from first sighting, which is the "delay" the feature was
asked for — it buys corroboration (other viewers landing on the same moment)
and view counts. Clips within `CLUSTER_SECS` (45) are one moment by
single-linkage; the most-viewed member represents it, ties to the earliest.
Capped at `MAX_PER_HOUR` (6) per channel. Emitted moments are remembered **by
time, not just by slug** — otherwise a fourth viewer clipping the same play
three minutes later forms a fresh cluster and suggests it again.

**Two things that must not regress** (`tests/test_suggested_clips.py`, 42 tests,
16/16 mutants killed):

1. **The queue reserve.** Suggestions share the plan-capped pending queue with
   real clips and a full queue drops the NEWEST arrival, so unreserved they
   could be the reason a paid-for clip did not land.
   `_SUGGESTION_QUEUE_RESERVE = 0.5` in `stream_worker.py` keeps half the queue
   untouchable. A dropped suggestion also does NOT fire the "you missed a clip"
   upgrade prompt — nothing the user pays for was lost.

2. **They are inert to learning and telemetry.** `_is_grabbed` was renamed
   `_excluded_from_learning` and widened to cover them. The sharp edge is that
   the damage runs BACKWARDS: rejecting a suggestion would call
   `record_clip(approved=False)` and add +0.75 to the channel's trigger
   threshold — punishing the detector for a moment it never claimed, making it
   fire LESS on exactly the channel where the crowd is finding what it missed.
   They are also excluded from CAUGHT and from the public clip counter, because
   the keep rate published beside it already excludes them and counting at one
   end of a ratio only is worse than either.

**UI.** Gold glow + "Suggested" badge, and the trigger badge is SUPPRESSED — a
suggestion carries `trigger_score` 0, so the unconditional badge rendered
"0% trigger", which reads as "the detector rated this worthless" on the one card
type that exists to carry moments it missed. The modal's "Why it fired" panel
likewise becomes "Why it is here" (sigKeys is a fixed list of four and would
otherwise draw four bars at 0%). The card credits the viewer by name: the clip
belongs to their Twitch account, not the streamer's.

**Not built, deliberately:** no per-user toggle (not asked for; the reserve and
the per-hour cap are the safety), and no plan gate — worth a decision if
suggestions turn out to be a Pro-shaped feature.

## Queued nice-to-haves

Discord webhook notifications on clip_ready (top retention idea), edit_url
"Extend to 60s" button, per-promo-code signup tracking in admin, first-run
onboarding flow, "trial ending soon" notice for admin-granted trials,
streamer partnership (clips-first DM, free Pro + custom code + $5/paid
signup; target a 300–1,000 viewer streamer).
