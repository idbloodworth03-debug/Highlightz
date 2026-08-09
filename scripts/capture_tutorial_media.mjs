/**
 * Capture every screenshot and video used by /tutorial.
 *
 *     node scripts/capture_tutorial_media.mjs            # desktop (what the page shows)
 *     node scripts/capture_tutorial_media.mjs --mobile   # also write *-mobile.png
 *     node scripts/capture_tutorial_media.mjs --keep     # leave the dev server running
 *
 * WHY THIS EXISTS. A walkthrough goes stale the moment the UI moves, and hand-
 * captured screenshots go stale silently — nobody re-opens a PNG to check it.
 * This script re-shoots the whole set from the running app, so refreshing the
 * tutorial after a UI change is one command rather than an afternoon.
 *
 * HOW IT SIGNS IN. The dashboard is behind Twitch OAuth, which cannot be
 * scripted (and should not be — it is a third party's login form). Instead the
 * script mints a session cookie with the app's OWN signer, exactly as
 * tests/ do, and serves seeded API responses. That means:
 *
 *   * NO REAL USER DATA EVER APPEARS. Every channel, clip title, viewer count
 *     and game below is invented. Nothing touches the production database,
 *     no real email or key is ever on screen, and the run works offline.
 *   * IT IS DETERMINISTIC. The clock is pinned (see PINNED_NOW) so relative
 *     timestamps like "2h ago" do not drift between runs, which would
 *     otherwise make every capture a spurious diff.
 *
 * THE ACCOUNT IS DELIBERATELY NOT AN ADMIN. Several gates in the dashboard read
 * `x || me.is_admin` — captions is the clearest (aurora_html.py: captionsOn).
 * Capturing as an admin would film panels that a normal user cannot see, and
 * the tutorial would document a UI nobody else has. The seeded user is a
 * plain Pro subscriber.
 *
 * VIDEO. Playwright records .webm. The .mp4 twin needs an H.264 encoder, which
 * the ffmpeg bundled with Playwright does not ship — so the script looks for a
 * real ffmpeg (see resolveFfmpeg) and, if it cannot find one with libx264,
 * writes the .webm anyway and tells you the .mp4 is missing rather than dying.
 * The page falls back to <source> order, so webm-only still plays everywhere
 * except older Safari.
 */

import { chromium } from 'playwright';
import { randomBytes } from 'node:crypto';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { mkdirSync, existsSync, rmSync, renameSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, '..');
const OUT = join(REPO, 'src', 'dashboard', 'static', 'tutorial');
const TMP = join(OUT, '.work');
const PORT = Number(process.env.TUTORIAL_PORT || 8934);
const BASE = `http://127.0.0.1:${PORT}`;
const WANT_MOBILE = process.argv.includes('--mobile');
const KEEP = process.argv.includes('--keep');

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
// Video records at 16:9, matching the width/height tutorial_content.py declares
// for kind="video". Recording at the 16:10 screenshot size instead would make
// the page reserve a box the file does not fit, and every video would sit
// letterboxed inside it.
const VIDEO_VP = { width: 1440, height: 810 };

/**
 * The dashboard pulls React, ReactDOM and Babel from unpkg at runtime — there
 * is no bundler in this repo. A capture run has no business reaching the public
 * internet (it would make shots depend on a CDN being up, and on whatever
 * version unpkg serves that day), so those three requests are served from
 * node_modules instead. Everything else off-origin stays stubbed.
 *
 * Without this the page loads, the cookie is accepted, the server returns the
 * real dashboard HTML — and then nothing renders, because React never arrived.
 * That failure looks identical to an auth problem from the outside, which is
 * exactly how it wasted an afternoon.
 */
const CDN_VENDOR = [
  ['unpkg.com/react@', 'react/umd/react.production.min.js'],
  ['unpkg.com/react-dom@', 'react-dom/umd/react-dom.production.min.js'],
  ['unpkg.com/@babel/standalone@', '@babel/standalone/babel.min.js'],
];

// Pinned so relative timestamps render identically on every run.
const PINNED_NOW = Date.UTC(2026, 2, 14, 21, 30, 0);   // 2026-03-14 21:30 UTC

/**
 * A throwaway signing key shared by the server we spawn and the cookie we mint.
 *
 * REQUIRED, not a convenience. config/settings.py replaces the default
 * dashboard_secret_key with `secrets.token_hex(32)` at import — a deliberate
 * guard so nothing ever runs on a publicly known key. The side effect is that
 * every process invents its own, so a cookie minted here would be signed with a
 * different key than the server validates against, and the session would be
 * silently ignored: the app serves the marketing landing page and every
 * dashboard shot quietly fails. Pinning one key for both sides is the fix.
 *
 * Generated per run and never written to disk, so it is not a secret anyone can
 * reuse — and it never touches the real .env.
 */
const DEV_SECRET = randomBytes(32).toString('hex');
const CHILD_ENV = { ...process.env, DASHBOARD_SECRET_KEY: DEV_SECRET };

// ── seeded data — every value here is invented ───────────────────────────────

const NOW_S = PINNED_NOW / 1000;
const ME = {
  id: 'u_demo', username: 'novaplays', twitch_login: 'novaplays',
  avatar_url: '', is_admin: false, is_labeler: false,
  subscription_status: 'active', plan: 'pro',
  plan_limits: { max_streams: 10, max_pending: 200, vod: true, uploads: true },
  // Mirrors production: uploads and import on, captions off.
  features: { uploads: true, clip_import: true, captions: false },
};

const STREAMS = [
  { channel: 'novaplays', platform: 'twitch', preset: 'fps', status: 'running',
    user_id: 'u_demo', added_at: NOW_S - 7200, title: 'ranked grind till we hit masters',
    game: 'VALORANT', viewers: 1284, live: true },
  { channel: 'kestrel', platform: 'twitch', preset: 'variety', status: 'running',
    user_id: 'u_demo', added_at: NOW_S - 5400, title: 'co-op night',
    game: 'Just Chatting', viewers: 342, live: true },
];

const mkClip = (id, ch, title, status, mins, score, game) => ({
  id, channel: ch, platform: 'twitch', status,
  clip_title: title, title,
  created_at: NOW_S - mins * 60,
  approved_at: status === 'approved' ? NOW_S - (mins - 2) * 60 : 0,
  trigger_score: score, virality_score: score, duration_seconds: 30, game,
  twitch_url: 'https://clips.twitch.tv/example' + id,
  thumbnail_url: '', signals: ['chat velocity', 'audio spike'],
});

const CLIPS = [
  mkClip('c1', 'novaplays', '1v4 clutch to win the round', 'pending', 6, 94, 'VALORANT'),
  mkClip('c2', 'novaplays', 'ace on defence, chat goes off', 'pending', 19, 88, 'VALORANT'),
  mkClip('c3', 'kestrel', 'the timing on this was perfect', 'pending', 34, 81, 'Just Chatting'),
  mkClip('c4', 'novaplays', 'no-scope across the site', 'approved', 96, 91, 'VALORANT'),
  mkClip('c5', 'kestrel', 'story about the airport', 'approved', 150, 84, 'Just Chatting'),
  mkClip('c6', 'novaplays', 'clutch defuse with 2 seconds', 'approved', 220, 89, 'VALORANT'),
];

const SUGGEST = {
  recent: [], popular: [
    { login: 'novaplays', display_name: 'novaplays', viewers: 1284, game: 'VALORANT' },
    { login: 'kestrel', display_name: 'kestrel', viewers: 342, game: 'Just Chatting' },
    { login: 'atlasruns', display_name: 'atlasruns', viewers: 2210, game: 'Elden Ring' },
  ],
};


// ── plumbing ────────────────────────────────────────────────────────────────

function mintSessionCookie() {
  // Signed with the app's own key via the app's own signer — reimplementing
  // itsdangerous in JS would be one silent format change away from breaking.
  const py = [
    'import base64, json, sys',
    'sys.path.insert(0, ' + JSON.stringify(REPO) + ')',
    'from itsdangerous import TimestampSigner',
    'from config.settings import settings',
    'd = {"auth": True, "user_id": "u_demo", "username": "novaplays",',
    '     "is_admin": False, "is_labeler": False, "subscription_status": "active"}',
    's = TimestampSigner(settings.dashboard_secret_key)',
    'print(s.sign(base64.b64encode(json.dumps(d).encode())).decode())',
  ].join('\n');
  return execFileSync('python3', ['-c', py], { cwd: REPO, env: CHILD_ENV }).toString().trim();
}

function resolveFfmpeg() {
  const tries = [];
  if (process.env.FFMPEG) tries.push(process.env.FFMPEG);
  tries.push('ffmpeg');
  try {
    tries.push(execFileSync('python3',
      ['-c', 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())'],
      { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim());
  } catch { /* imageio-ffmpeg not installed; fine */ }
  for (const bin of tries) {
    if (!bin) continue;
    try {
      const enc = execFileSync(bin, ['-hide_banner', '-encoders'],
        { stdio: ['ignore', 'pipe', 'ignore'] }).toString();
      if (enc.includes('libx264')) {
        return { bin, h264: true, vp9: enc.includes('libvpx-vp9') };
      }
    } catch { /* not this one */ }
  }
  return { bin: null, h264: false, vp9: false };
}

/**
 * Seconds of black at the head of a recording.
 *
 * Playwright starts the recorder the instant the context opens, which is before
 * the first navigation has painted anything — so every take begins with two or
 * three seconds of pure black. Left in, the hero video opens on a blank
 * rectangle and the poster frame extracted from it is blank too, which is
 * exactly what it looks like when a video is broken.
 *
 * blackdetect reports the black run rather than us guessing a constant, so this
 * keeps working if startup gets slower or faster.
 */
function leadingBlackSeconds(bin, file) {
  if (!bin) return 0;
  try {
    const r = spawnSync(bin,
      ['-i', file, '-vf', 'blackdetect=d=0.1:pix_th=0.12', '-f', 'null', '-'],
      { encoding: 'utf8' });
    const text = (r.stderr || '') + (r.stdout || '');
    // Only a run that starts at 0 is the startup blank; a black frame later in
    // the take is real content (a transition) and must not be cut.
    const m = text.match(/black_start:0(?:\.0+)?\s+black_end:([0-9.]+)/);
    return m ? Math.max(0, parseFloat(m[1]) - 0.1) : 0;
  } catch {
    return 0;
  }
}

async function waitForServer(ms = 45000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(BASE + '/health');
      if (r.ok) return true;
    } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

/** Seeded API + a frozen clock. Registered newest-last because Playwright
 *  matches the most recently added route first — so the catch-all goes FIRST
 *  and the specific routes below it win. */
async function seed(ctx, opts = {}) {
  // Screenshots want the score to climb once and then hold still, so the value
  // in the file is the same on every run. Video wants it to keep moving for the
  // length of the take, or the stream sits frozen for forty seconds.
  const LOOP = opts.loopScores ? 'true' : 'false';
  await ctx.addInitScript(`(function(){
    var HZ_LOOP = ${LOOP};
    try { localStorage.setItem('hz_welcome_seen','1'); } catch (e) {}
    var fixed = ${PINNED_NOW};
    var RealDate = Date;
    function FakeDate(){
      if (arguments.length === 0) return new RealDate(fixed);
      return new RealDate(...arguments);
    }
    FakeDate.prototype = RealDate.prototype;
    FakeDate.now = function(){ return fixed; };
    FakeDate.parse = RealDate.parse; FakeDate.UTC = RealDate.UTC;
    window.Date = FakeDate;
    // A stub socket, but NOT a silent one. The live trigger score arrives only
    // over the WebSocket, so a socket that never speaks leaves every stream
    // reading "0.0 — engine waiting for first update", and the Live Streams
    // screenshot then contradicts the sentence it is illustrating. This replays
    // a short scripted climb instead, then stops so the page holds still for
    // the shot.
    window.WebSocket = function(){
      var s = this;
      s.readyState = 1; s.send = function(){}; s.close = function(){};
      setTimeout(function(){ if (s.onopen) s.onopen(); }, 20);
      // Climb, fire, settle back down — one full cycle of what a viewer is
      // being told happens, so a looping video shows the whole story.
      var curve = HZ_LOOP
        ? [31,34,38,42,47,53,60,68,75,83,89,94,92,86,74,63,55,48,42,37,33,30,
           32,36,41,46,52,59,67,74,82,88,93,90,84,72,61,53,46,40,35,31]
        : [31,34,38,42,47,53,60,68,75,83,89,94];
      var i = 0;
      var t = setInterval(function(){
        if (!s.onmessage) return;
        if (i >= curve.length) {
          if (!HZ_LOOP) { clearInterval(t); return; }
          i = 0;                                  // keep the stream alive
        }
        var v = curve[i++];
        [['novaplays', v], ['kestrel', Math.max(12, Math.round(v * 0.62))]].forEach(function(pair){
          s.onmessage({ data: JSON.stringify({
            event: 'score_update', channel: pair[0], score: pair[1],
            breakdown: { chat_velocity: pair[1] * 0.34, audio: pair[1] * 0.28,
                         keywords: pair[1] * 0.22, sentiment: pair[1] * 0.16 },
          })});
        });
      // 60ms, not 120: at 25fps a 120ms tick moves the score on every third
      // frame, which reads as a stutter rather than a climb.
      }, 60);
    };
  })();`);

  const json = (d) => (route) => route.fulfill({ json: d });
  await ctx.route('**/*', (route) => {
    const u = route.request().url();
    if (u.startsWith(BASE)) return route.continue();
    return route.fulfill({ json: [] });          // never touch the network
  });
  await ctx.route('**/me', json(ME));
  await ctx.route('**/clips', json(CLIPS));
  await ctx.route('**/streams', json(STREAMS));
  // Two different shapes from one endpoint: {results:[...]} for a typed query,
  // {recent, popular} for the zero state. Serving the zero-state shape to a
  // query is why the dropdown read "No channels found" mid-capture.
  await ctx.route('**/streams/suggest**', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q');
    if (q) {
      const hit = SUGGEST.popular.filter(p => p.login.includes(q.toLowerCase()));
      return route.fulfill({ json: { results: hit.length ? hit : [SUGGEST.popular[2]] } });
    }
    return route.fulfill({ json: SUGGEST });
  });
  // GET /profiles returns a LIST of StreamerProfile.to_dict(), not a map keyed
  // by channel. With the wrong shape every per-stream stat renders as an em
  // dash, which makes a working detector look broken in the screenshot.
  const profile = (channel, threshold, clips, approved, sessions) => ({
    channel, platform: 'twitch',
    trigger_threshold: threshold,
    total_clips: clips, rejected_clips: clips - approved,
    approval_rate: Math.round((approved / clips) * 1000) / 1000,
    total_sessions: sessions, total_watch_seconds: sessions * 4200,
    velocity_spike_multiplier: 2.1, velocity_std: 0.42, var_velocity: 0.18,
    velocity_samples: 1840, keyword_samples: 612, sentiment_samples: 980,
    calibration_target: 60, calibration_pct: 100.0, is_calibrated: true,
    researched: true, research_clips_per_day: 3.4,
    created_at: NOW_S - 86400 * 26, last_seen: NOW_S - 40, last_decay_ts: NOW_S - 3600,
    signal_weights: { CHAT_VELOCITY: 1.18, AUDIO_SPIKE: 1.04, KEYWORD: 0.96,
                      SENTIMENT: 0.89, VIEWER_SPIKE: 1.11, SILENCE_BURST: 0.74 },
  });
  await ctx.route('**/profiles', json([
    profile('novaplays', 68.0, 41, 28, 14),
    profile('kestrel', 61.0, 17, 11, 6),
  ]));
  // Shape mirrors GET /stats in api.py: one object per channel.
  await ctx.route('**/stats', json({
    novaplays: {
      channel: 'novaplays', total_clips: 41, clips_this_week: 12,
      approved: 28, pending: 2, avg_score: 78.4, avg_virality: 74.1,
      top_signal: { signal: 'chat velocity', count: 19 },
    },
    kestrel: {
      channel: 'kestrel', total_clips: 17, clips_this_week: 5,
      approved: 11, pending: 1, avg_score: 71.9, avg_virality: 68.3,
      top_signal: { signal: 'audio spike', count: 7 },
    },
  }));
  await ctx.route('**/vod/jobs', json([]));
  await ctx.route('**/publish/platforms', json({ platforms: [] }));
  await ctx.route('**/publish/schedule', json({ queue: [] }));
  await ctx.route('**/feedback/unread-count', json({ count: 0 }));
  await ctx.route('**/landing/showcase', json({ clips: [] }));
  await ctx.route('**/landing/stats', json({ clips_total: 13464 }));

  // Registered last so they beat the catch-all above.
  for (const [needle, rel] of CDN_VENDOR) {
    const file = join(REPO, 'node_modules', rel);
    await ctx.route(
      (u) => u.href.includes(needle),
      (route) => route.fulfill({ path: file, contentType: 'application/javascript' }));
  }
}

function assertVendorPresent() {
  const missing = CDN_VENDOR
    .map(([, rel]) => join(REPO, 'node_modules', rel))
    .filter(f => !existsSync(f));
  if (missing.length) {
    throw new Error(
      'Missing vendored front-end libraries:\n  ' + missing.join('\n  ')
      + '\n\nInstall them next to playwright:\n'
      + '  npm install --no-save react@18.3.1 react-dom@18.3.1 @babel/standalone@7.29.0\n');
  }
}

async function newCtx(browser, viewport, extra = {}, seedOpts = {}) {
  const ctx = await browser.newContext({
    viewport, deviceScaleFactor: 2, colorScheme: 'dark', ...extra,
  });
  // `url`, not `domain`+`path`. Domain matching against a bare IP literal is
  // unreliable in Chromium — the cookie is accepted by addCookies and then
  // silently never sent, which looks exactly like a bad signature.
  await ctx.addCookies([{ name: 'session', value: SESSION, url: BASE }]);
  await seed(ctx, seedOpts);
  return ctx;
}

async function goto(page, path) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' });
  await page.waitForTimeout(900);            // let the SPA settle
}

/** Scroll an element into view over ~600ms instead of teleporting to it.
 *  scrollIntoViewIfNeeded jumps in a single frame, which in a recording reads
 *  as a hard cut and is the main thing that made the tour feel jerky. */
async function glideTo(page, locator) {
  const box = await locator.boundingBox();
  if (!box) return;
  await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'smooth' }),
    Math.max(0, box.y + (await page.evaluate(() => window.scrollY)) - 220));
  await page.waitForTimeout(900);
}

/** Jump straight to a dashboard tab by clicking its nav button.
 *
 *  Fails FAST and loudly if the dashboard is not on screen. Without this check
 *  an unauthenticated page just shows the marketing site, every tab click burns
 *  a 30s timeout, and the run ends with eight identical "locator timeout"
 *  errors that say nothing about the actual cause (the session was rejected).
 *
 *  hasText takes a plain string, not an anchored regex: nav buttons also
 *  contain an icon and, on Clip Review, a pending-count badge, so textContent
 *  is not exactly the label. All six labels are unique as substrings.
 */
async function tab(page, label) {
  if (!await page.locator('.rd-nav').count()) {
    // Two very different causes look the same here, so name both rather than
    // guessing: the server can return the marketing page (session rejected),
    // or return the real dashboard that then never boots (React missing).
    const isLanding = await page.locator('.nav-logo').count() > 0
      && await page.locator('#rd-app').count() === 0;
    throw new Error(isLanding
      ? 'served the marketing landing page — the session cookie was rejected. '
        + 'Check DASHBOARD_SECRET_KEY is shared with the spawned server.'
      : 'the dashboard HTML loaded but React never mounted — the vendored '
        + 'react/react-dom/babel files did not load. See CDN_VENDOR.');
  }
  await page.locator('.rd-navitem', { hasText: label }).first().click({ timeout: 15000 });
  await page.waitForTimeout(700);
}

// ── the shots ───────────────────────────────────────────────────────────────

const SESSION = mintSessionCookie();
const FF = resolveFfmpeg();

/** name -> async (page) => void. Screenshots are full-viewport. */
const SHOTS = [
  ['01-signin', async (page) => { await goto(page, '/login'); }, { anon: true }],

  ['02-welcome', async (page) => {
    // The welcome overlay only renders when the "seen" flag is absent, so this
    // one shot deliberately clears what seed() sets.
    await page.addInitScript(`try{localStorage.removeItem('hz_welcome_seen')}catch(e){}`);
    await goto(page, '/');
  }],

  ['03-add-stream', async (page) => {
    await goto(page, '/');
    await tab(page, 'Live Streams');
    const input = page.locator('.rd-input').first();
    await input.click();
    await input.type('atlas', { delay: 45 });
    await page.waitForTimeout(600);
  }],

  ['05-live-streams', async (page) => {
    await goto(page, '/');
    await tab(page, 'Live Streams');
  }],

  ['06-clip-review', async (page) => {
    await goto(page, '/');
    await tab(page, 'Clip Review');
  }],

  ['07-clip-library', async (page) => {
    await goto(page, '/');
    await tab(page, 'Clip Library');
  }],

  ['08-vod-scanner', async (page) => {
    await goto(page, '/');
    await tab(page, 'VOD Scanner');
    const f = page.locator('input[placeholder*="twitch.tv/videos"]').first();
    if (await f.count()) await f.fill('https://www.twitch.tv/videos/123456789');
    await page.waitForTimeout(300);
  }],

  ['09-settings', async (page) => { await goto(page, '/'); await tab(page, 'Settings'); }],
  ['10-account', async (page) => { await goto(page, '/'); await tab(page, 'Account'); }],
];

/** Multi-step flows worth a moving picture.
 *
 *  These are silent screen recordings, not edited films — there is no
 *  voiceover and no music. That is why every video on the page carries a
 *  visible text description: the tutorial has to work with sound off anyway,
 *  so a silent capture loses nothing a reader needed.
 */
const VIDEOS = [
  ['00-overview', async (page) => {
    // The whole product in one take: a channel is chosen, the score climbs
    // past the threshold, the clip that fired is reviewed and approved, and
    // it turns up in the library. Paced slowly on purpose — this plays as a
    // muted loop in the hero, so a viewer has to be able to follow it without
    // a scrubber.
    await goto(page, '/');
    await tab(page, 'Live Streams');
    await page.waitForTimeout(1500);

    const input = page.locator('.rd-input').first();
    await input.click();
    await input.type('atlas', { delay: 110 });
    await page.waitForTimeout(1600);          // suggestions open
    await page.keyboard.press('Escape');
    await page.waitForTimeout(2600);          // the score climbs

    await tab(page, 'Clip Review');
    await page.waitForTimeout(2200);
    const approve = page.locator('button', { hasText: /^Approve$/ }).first();
    if (await approve.count()) {
      await glideTo(page, approve);
      await approve.hover();
      await page.waitForTimeout(700);
      await approve.click();
      await page.waitForTimeout(2000);
    }

    await tab(page, 'Clip Library');
    await page.waitForTimeout(2600);
  }],

  ['04-approve', async (page) => {
    await goto(page, '/');
    await tab(page, 'Clip Review');
    await page.waitForTimeout(1200);
    const approve = page.locator('button', { hasText: /^Approve$/ }).first();
    if (await approve.count()) {
      await glideTo(page, approve);
      await approve.hover();
      await page.waitForTimeout(600);
      await approve.click();
      await page.waitForTimeout(1800);
    }
  }],
];

// ── run ─────────────────────────────────────────────────────────────────────

mkdirSync(OUT, { recursive: true });
rmSync(TMP, { recursive: true, force: true });
mkdirSync(TMP, { recursive: true });

assertVendorPresent();
console.log('starting dev server on ' + PORT + ' ...');
const server = spawn('python3',
  ['-m', 'uvicorn', 'src.dashboard.api:app', '--host', '127.0.0.1',
   '--port', String(PORT), '--log-level', 'warning'],
  { cwd: REPO, stdio: 'ignore', detached: true, env: CHILD_ENV });

let browser;
const done = [];
const failed = [];
try {
  if (!await waitForServer()) throw new Error('dev server never became healthy');
  console.log('server up. ffmpeg:', FF.bin || 'none', FF.h264 ? '(h264 ok)' : '(no h264 — mp4 will be skipped)');

  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || '/opt/pw-browsers/chromium' });

  const viewports = [['', DESKTOP]];
  if (WANT_MOBILE) viewports.push(['-mobile', MOBILE]);

  for (const [suffix, vp] of viewports) {
    for (const [name, fn, opts = {}] of SHOTS) {
      const ctx = opts.anon
        ? await (async () => {
            const c = await browser.newContext({ viewport: vp, deviceScaleFactor: 2, colorScheme: 'dark' });
            await seed(c); return c;
          })()
        : await newCtx(browser, vp);
      const page = await ctx.newPage();
      const errs = [];
      page.on('pageerror', e => errs.push(String(e)));
      try {
        await fn(page);
        const file = join(OUT, name + suffix + '.png');
        await page.screenshot({ path: file, scale: 'css' });
        done.push(name + suffix + '.png');
        console.log('  shot', name + suffix + '.png', errs.length ? '(page errors: ' + errs.length + ')' : '');
      } catch (e) {
        failed.push(name + suffix + ': ' + e.message);
        console.log('  FAILED', name + suffix, '-', e.message);
      }
      await ctx.close();
    }
  }

  for (const [name, fn] of VIDEOS) {
    const ctx = await newCtx(browser, VIDEO_VP, {
      recordVideo: { dir: TMP, size: VIDEO_VP },
      // 2x, then recorded down to VIDEO_VP: the browser renders text at double
      // density and the recorder supersamples it, which is most of the
      // difference between legible UI labels and grey smudges at this size.
      deviceScaleFactor: 2,
    }, { loopScores: true });
    const page = await ctx.newPage();
    try {
      await fn(page);
    } catch (e) {
      failed.push(name + ': ' + e.message);
      console.log('  FAILED', name, '-', e.message);
    }
    await ctx.close();                       // flush: the file only lands on close

    // Newest file, and TMP is emptied at the end of every iteration. Both
    // matter: the raw capture is no longer renamed out of TMP (both outputs are
    // encoded FROM it), so without a sweep the second video would re-encode the
    // first one's recording — which is exactly what happened, and produced two
    // byte-identical files for clips of different lengths.
    const raw = readdirSync(TMP)
      .filter(f => f.endsWith('.webm'))
      .map(f => join(TMP, f))
      .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
    if (!raw.length) { failed.push(name + ': no video produced'); continue; }
    const rawFile = raw[0];                       // stays in TMP, never shipped
    const webm = join(OUT, name + '.webm');
    const mp4 = join(OUT, name + '.mp4');
    const lead = leadingBlackSeconds(FF.bin, rawFile);

    if (!FF.bin) {
      renameSync(rawFile, webm);
      done.push(name + '.webm');
      console.log('  video', name + '.webm', '(no ffmpeg — untrimmed, VP8 as recorded)');
      continue;
    }

    // ONE ENCODE PER OUTPUT, BOTH FROM THE ORIGINAL RECORDING.
    //
    // The first version of this trimmed the webm in place (re-encoding it to
    // VP8 at a fixed 1400k) and then built the mp4 *from that file*. Two lossy
    // generations, the second at a bitrate far too low for 1440x810 of small
    // UI text — the result was 491 kb/s of mush. Both outputs now come
    // straight from the raw capture with the trim applied as an input seek, so
    // nothing is encoded twice.
    //
    // Constant frame rate matters as much as bitrate here: the recorder emits
    // variable timing, and a VFR file plays back with visible micro-stutter
    // even when every frame is present.
    const trim = lead > 0.2 ? ['-ss', String(lead)] : [];
    if (lead > 0.2) console.log('  trimming', lead.toFixed(1) + 's of leading black');

    try {
      execFileSync(FF.bin, ['-y', '-loglevel', 'error', ...trim, '-i', rawFile,
        '-c:v', 'libx264', '-preset', 'slow', '-crf', '20',
        '-r', '25', '-fps_mode', 'cfr',
        // yuv420p + faststart: without both, Safari refuses the file and the
        // browser must download it all before showing a frame.
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', mp4],
        { stdio: 'ignore' });
      done.push(name + '.mp4');
      console.log('  video', name + '.mp4');
    } catch (e) { failed.push(name + ' mp4: ' + e.message); }

    try {
      // VP9 at constant quality, not VP8 at a fixed bitrate. VP8 has to be told
      // a number that is wrong for at least part of the clip; -crf with -b:v 0
      // spends bits where the picture needs them, which on flat dark UI with
      // sharp text is most of the win.
      const vp9 = ['-c:v', 'libvpx-vp9', '-crf', '32', '-b:v', '0',
                   '-deadline', 'good', '-cpu-used', '2', '-row-mt', '1'];
      const vp8 = ['-c:v', 'libvpx', '-crf', '10', '-b:v', '3M'];
      execFileSync(FF.bin, ['-y', '-loglevel', 'error', ...trim, '-i', rawFile,
        ...(FF.vp9 ? vp9 : vp8), '-r', '25', '-fps_mode', 'cfr', '-an', webm],
        { stdio: 'ignore' });
      done.push(name + '.webm');
      console.log('  video', name + '.webm', FF.vp9 ? '(vp9)' : '(vp8)');
    } catch (e) { failed.push(name + ' webm: ' + e.message); }

    try {
      // A second in, not frame zero: the first frame after a trim can still be
      // mid-repaint, and the poster is all a reduced-motion visitor ever sees.
      execFileSync(FF.bin, ['-y', '-loglevel', 'error', '-ss', '1.0', '-i', mp4,
        '-frames:v', '1', '-q:v', '2', join(OUT, name + '-poster.jpg')],
        { stdio: 'ignore' });
      done.push(name + '-poster.jpg');
      console.log('  poster', name + '-poster.jpg');
    } catch (e) { failed.push(name + ' poster: ' + e.message); }

    // Sweep, so the next iteration cannot pick this recording up again.
    for (const f of readdirSync(TMP)) rmSync(join(TMP, f), { force: true });
  }
} finally {
  if (browser) await browser.close();
  rmSync(TMP, { recursive: true, force: true });
  if (!KEEP) { try { process.kill(-server.pid); } catch { /* already gone */ } }
}

// Re-encode pass. Shots are taken at deviceScaleFactor 2 with `scale: 'css'`,
// so Playwright renders at 2x and downsamples — the file already lands at CSS
// size (1440x900, matching the width/height the page declares) with much better
// antialiasing than a 1x render. The cap below is therefore a safety net rather
// than the point; the real saving is dropping the alpha channel and re-encoding
// with optimize=True, worth roughly 8% on a dark, gradient-heavy UI.
try {
  const py = [
    'import sys, pathlib',
    'from PIL import Image',
    'MAX_W = 1640',
    'total_before = total_after = 0',
    'for f in sorted(pathlib.Path(sys.argv[1]).glob("*.png")):',
    '    before = f.stat().st_size',
    '    im = Image.open(f)',
    '    if im.width > MAX_W:',
    '        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)',
    '    im.convert("RGB").save(f, optimize=True)',
    '    total_before += before; total_after += f.stat().st_size',
    'print("%d KB -> %d KB" % (total_before // 1024, total_after // 1024))',
  ].join('\n');
  const saved = execFileSync('python3', ['-c', py, OUT]).toString().trim();
  console.log('\noptimised screenshots: ' + saved);
} catch (e) {
  console.log('\nskipped screenshot optimisation (needs Pillow): ' + e.message.split('\n')[0]);
}

console.log('\nwrote ' + done.length + ' file(s) to ' + OUT);
if (failed.length) {
  console.log('\n' + failed.length + ' problem(s):');
  failed.forEach(f => console.log('  - ' + f));
}
console.log([
  '',
  'CANNOT BE AUTOMATED — these need credentials or a live stream:',
  '  Twitch OAuth consent  Twitch\'s own domain; needs a real login typed in',
  '  Stripe checkout       needs live or test Stripe keys and a real session',
  '  Stripe billing portal needs a real Stripe customer',
  '  a genuinely live clip the videos above are seeded, not a real broadcast',
  '',
  'Everything else on the page is produced by this script. The videos are silent',
  'screen recordings — if you want narration over 00-overview.mp4, re-record it',
  'yourself; the page shows a text description under every video either way.',
].join('\n'));
