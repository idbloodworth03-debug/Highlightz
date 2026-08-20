/**
 * Render the REAL dashboard and lift its DOM for the landing page.
 *
 * WHY THIS EXISTS. The landing page used to redraw the product in CSS: 90 divs
 * approximating a dashboard that already exists twenty files away. That reads
 * as illustration no matter how carefully it is styled, because it is one.
 *
 * So nothing here is drawn. This boots the actual React app out of
 * aurora_html.py in a real browser, feeds it seeded data shaped like the real
 * API responses, lets the real components render, and then lifts the resulting
 * DOM and the stylesheet that painted it. What the landing page shows IS the
 * product's own markup.
 *
 * Regenerate after any dashboard change that should show up on the marketing
 * page:
 *
 *     node scripts/capture_product_ui.mjs
 *
 * Writes src/dashboard/landing_product.py. That file is generated; edit this
 * script, not the output.
 */
import { chromium } from 'playwright';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const ROOT = process.cwd();
const OUT = join(ROOT, 'src/dashboard/landing_product.py');

// ── Seeded data ──────────────────────────────────────────────────────────────
// Field names and value ranges come from the codebase, not from imagination:
//   streams   the record built in add_stream()            src/dashboard/api.py
//   clips     ClipMetadata.to_dict()                      src/processor/metadata.py
//   profiles  StreamerProfile.to_dict()                   src/profiles/profile.py
//   scores    the score_update breakdown                  src/trigger/engine.py:180
//   titles    TriggerEngine._generate_clip_title()        src/trigger/engine.py:607
//   statuses  the real enums: pending|approved|rejected, live|offline|reconnecting
//
// The numbers are a real account mid-session, taken from production journals:
// uneven scores, a channel offline, one reconnecting, a queue that is not full,
// and a rejected clip sitting in it.
const NOW = 1755649200;

const ME = {
  id: 'u1', username: 'bloodworthhh', twitch_login: 'bloodworthhh',
  avatar_url: '', is_admin: false, is_labeler: false,
  plan: 'pro', plan_label: 'Pro', subscription_status: 'active',
  plan_limits: { max_streams: 10, max_pending: 200, vod: true, uploads: true },
  features: { uploads: false, clip_import: false, captions: false, vod_audio: true },
  clips_lost_24h: 0, review_prompt: false, next_plan: null,
};

const STREAMS = [
  { channel: 'jynxzi',         platform: 'twitch', preset: 'fps',     status: 'live',         user_id: 'u1', added_at: NOW - 86400 * 11 },
  { channel: 'lacy',           platform: 'twitch', preset: 'default', status: 'live',         user_id: 'u1', added_at: NOW - 86400 * 6 },
  { channel: 'jasontheween',   platform: 'twitch', preset: 'irl',     status: 'live',         user_id: 'u1', added_at: NOW - 86400 * 3 },
  { channel: 'stableronaldo',  platform: 'twitch', preset: 'default', status: 'offline',      user_id: 'u1', added_at: NOW - 86400 * 19 },
  { channel: 'theburntpeanut', platform: 'twitch', preset: 'default', status: 'reconnecting', user_id: 'u1', added_at: NOW - 3600 * 5 },
];

const W = { CHAT_VELOCITY: 1.0, KEYWORD: 1.0, SENTIMENT: 1.0, AUDIO_SPIKE: 1.0,
            VIEWER_SPIKE: 1.0, SILENCE_BURST: 1.0, EMOTE_HOMOGENEITY: 1.0 };

const PROFILES = [
  // velocity_samples and thresholds are real values pulled from prod profiles.
  { channel: 'jynxzi', platform: 'twitch', preset: 'fps', avg_velocity: 1.595,
    velocity_samples: 32016, trigger_threshold: 51.0, total_clips: 552,
    approved_clips: 83, rejected_clips: 469, signal_weights: W,
    is_calibrated: true, calibration_pct: 100.0, approval_rate: 0.15,
    avg_audio_db: -63.2, avg_sentiment: 0.31, avg_keyword_rate: 0.04 },
  { channel: 'lacy', platform: 'twitch', preset: 'default', avg_velocity: 2.628,
    velocity_samples: 5844, trigger_threshold: 60.0, total_clips: 70,
    approved_clips: 21, rejected_clips: 49, signal_weights: W,
    is_calibrated: true, calibration_pct: 100.0, approval_rate: 0.3,
    avg_audio_db: -54.9, avg_sentiment: 0.28, avg_keyword_rate: 0.03 },
  { channel: 'jasontheween', platform: 'twitch', preset: 'irl', avg_velocity: 3.132,
    velocity_samples: 1078, trigger_threshold: 60.0, total_clips: 135,
    approved_clips: 31, rejected_clips: 104, signal_weights: W,
    is_calibrated: true, calibration_pct: 100.0, approval_rate: 0.23,
    avg_audio_db: -48.1, avg_sentiment: 0.34, avg_keyword_rate: 0.05 },
  { channel: 'stableronaldo', platform: 'twitch', preset: 'default', avg_velocity: 5.641,
    velocity_samples: 19802, trigger_threshold: 60.0, total_clips: 221,
    approved_clips: 18, rejected_clips: 203, signal_weights: W,
    is_calibrated: true, calibration_pct: 100.0, approval_rate: 0.08,
    avg_audio_db: -51.4, avg_sentiment: 0.29, avg_keyword_rate: 0.04 },
  { channel: 'theburntpeanut', platform: 'twitch', preset: 'default', avg_velocity: 1.988,
    velocity_samples: 4546, trigger_threshold: 63.0, total_clips: 47,
    approved_clips: 9, rejected_clips: 38, signal_weights: W,
    is_calibrated: false, calibration_pct: 61.0, approval_rate: 0.19,
    avg_audio_db: -57.7, avg_sentiment: 0.26, avg_keyword_rate: 0.02 },
];

// Titles are exactly what _generate_clip_title produces: "{channel} — {label}",
// where label is one of the _SIGNAL_TITLES or a multi-signal fallback.
//
// trigger_signals carries the shape stream_worker.py:484 writes:
//   {"type": "CHAT_VELOCITY", "value": 0.72, "metadata": {}}
// The modal's "Why it fired" reads value*100, so an empty list renders every
// bar at 0% — which is what the first capture produced and is exactly the
// hollow, unreal look this whole exercise exists to avoid.
const sig = (o) => Object.entries(o).map(([type, value]) => ({ type, value, metadata: {} }));
//
// thumbnail_url and embed_url are left EMPTY on purpose. The components have a
// real fallback for a clip with no stored thumbnail: a gradient derived from
// the clip id. Using it keeps the landing page free of third-party requests
// while still showing a genuine product state rather than a drawn one.
const clip = (o) => ({
  platform: 'twitch', duration_seconds: 30.0, storage_url: '', vertical_url: '',
  user_id: 'u1', chat_snapshot: [], twitch_clip_id: o.id, thumbnail_url: '',
  embed_url: '', trigger_signals: [], ...o,
});
const CLIPS = [
  clip({ id: 'c01', channel: 'jynxzi', clip_title: 'jynxzi — Sub/Raid Hype',
         stream_title: '[586/730] SOLO TO CHAMPION PC', game: 'Rainbow Six Siege',
         trigger_score: 61.7, virality_score: 44.0, status: 'pending', created_at: NOW - 240,
         trigger_signals: sig({CHAT_VELOCITY:0.72, KEYWORD:0.65, SENTIMENT:0.82, AUDIO_SPIKE:0.81, VIEWER_SPIKE:0.13}) }),
  clip({ id: 'c02', channel: 'lacy', clip_title: 'lacy — Chat Erupts',
         stream_title: 'chill grind then variety', game: 'Just Chatting',
         trigger_score: 58.3, virality_score: 29.0, status: 'pending', created_at: NOW - 700,
         trigger_signals: sig({CHAT_VELOCITY:0.64, KEYWORD:0.11, SENTIMENT:0.58, AUDIO_SPIKE:0.29}) }),
  clip({ id: 'c03', channel: 'jynxzi', clip_title: 'jynxzi — Everything Pops Off At Once',
         stream_title: '[586/730] SOLO TO CHAMPION PC', game: 'Rainbow Six Siege',
         trigger_score: 82.4, virality_score: 50.0, status: 'approved', created_at: NOW - 1180,
         approved_at: NOW - 1100,
         trigger_signals: sig({CHAT_VELOCITY:0.88, KEYWORD:0.71, SENTIMENT:0.79, AUDIO_SPIKE:0.9, VIEWER_SPIKE:0.44}) }),
  // The rejected one. A queue with nothing rejected in it is a demo, not a queue.
  clip({ id: 'c04', channel: 'stableronaldo', clip_title: 'stableronaldo — Loud Reaction',
         stream_title: 'watching stuff', game: 'Just Chatting',
         trigger_score: 63.1, virality_score: 17.0, status: 'rejected', created_at: NOW - 1620,
         trigger_signals: sig({CHAT_VELOCITY:0.19, KEYWORD:0.0, SENTIMENT:0.34, AUDIO_SPIKE:0.93}) }),
  clip({ id: 'c05', channel: 'jasontheween', clip_title: 'jasontheween — Chat Speaks As One',
         stream_title: 'JASON IS LIVE', game: 'Just Chatting',
         trigger_score: 54.9, virality_score: 23.0, status: 'pending', created_at: NOW - 2050,
         trigger_signals: sig({CHAT_VELOCITY:0.57, KEYWORD:0.08, SENTIMENT:0.66, AUDIO_SPIKE:0.42, EMOTE_HOMOGENEITY:0.61}) }),
  clip({ id: 'c06', channel: 'jynxzi', clip_title: 'jynxzi — Chat Calls For The Clip',
         stream_title: '[586/730] SOLO TO CHAMPION PC', game: 'Rainbow Six Siege',
         trigger_score: 71.2, virality_score: 47.0, status: 'approved', created_at: NOW - 2900,
         approved_at: NOW - 2800,
         trigger_signals: sig({CHAT_VELOCITY:0.69, KEYWORD:0.84, SENTIMENT:0.5, AUDIO_SPIKE:0.36}) }),
  clip({ id: 'c07', channel: 'theburntpeanut', clip_title: 'theburntpeanut — Hype Moment',
         stream_title: 'late night', game: 'Minecraft',
         trigger_score: 65.2, virality_score: 31.0, status: 'pending', created_at: NOW - 3400,
         trigger_signals: sig({CHAT_VELOCITY:0.48, KEYWORD:0.22, SENTIMENT:0.55, AUDIO_SPIKE:0.6}) }),
  clip({ id: 'c08', channel: 'lacy', clip_title: 'lacy — Emotions Run High',
         stream_title: 'chill grind then variety', game: 'Just Chatting',
         trigger_score: 60.4, virality_score: 26.0, status: 'pending', created_at: NOW - 4120,
         trigger_signals: sig({CHAT_VELOCITY:0.61, KEYWORD:0.05, SENTIMENT:0.74, AUDIO_SPIKE:0.31}) }),
];

// A live score frame, shaped exactly like the score_update breakdown the engine
// broadcasts. The underscore keys are the raw measurements the dashboard shows.
const SCORES = {
  jynxzi: { score: 61.7, breakdown: {
    CHAT_VELOCITY: 0.38, EMOTE_HOMOGENEITY: 0.0, AUDIO_SPIKE: 0.09, KEYWORD: 0.0,
    SENTIMENT: 1.0, VIEWER_SPIKE: 0.13, SILENCE_BURST: 0.0,
    _audio_db: -63.2, _audio_peak_db: -63.2, _audio_base_db: -64.5,
    _viewers: 9599, _viewer_base: 9012, _chat_vps: 1.1, _chat_base_vps: 1.95,
    _last_chat_s: 0, _threshold: 51.0 } },
  lacy: { score: 34.1, breakdown: {
    CHAT_VELOCITY: 0.21, EMOTE_HOMOGENEITY: 0.0, AUDIO_SPIKE: 0.04, KEYWORD: 0.0,
    SENTIMENT: 0.62, VIEWER_SPIKE: 0.0, SILENCE_BURST: 0.0,
    _audio_db: -54.9, _audio_peak_db: -54.9, _audio_base_db: -63.4,
    _viewers: 2411, _viewer_base: 2380, _chat_vps: 2.6, _chat_base_vps: 2.63,
    _last_chat_s: 1, _threshold: 60.0 } },
  jasontheween: { score: 66.9, breakdown: {
    CHAT_VELOCITY: 0.55, EMOTE_HOMOGENEITY: 0.12, AUDIO_SPIKE: 0.31, KEYWORD: 0.08,
    SENTIMENT: 0.74, VIEWER_SPIKE: 0.22, SILENCE_BURST: 0.0,
    _audio_db: -48.1, _audio_peak_db: -44.2, _audio_base_db: -52.0,
    _viewers: 14208, _viewer_base: 13740, _chat_vps: 3.9, _chat_base_vps: 3.13,
    _last_chat_s: 0, _threshold: 60.0 } },
  theburntpeanut: { score: 39.7, breakdown: {
    CHAT_VELOCITY: 0.18, EMOTE_HOMOGENEITY: 0.0, AUDIO_SPIKE: 0.02, KEYWORD: 0.0,
    SENTIMENT: 0.41, VIEWER_SPIKE: 0.0, SILENCE_BURST: 0.0,
    _audio_db: -57.7, _audio_peak_db: -57.7, _audio_base_db: -59.1,
    _viewers: 812, _viewer_base: 803, _chat_vps: 0.9, _chat_base_vps: 1.99,
    _last_chat_s: 4, _threshold: 63.0 } },
};

// ── Boot the real app ────────────────────────────────────────────────────────

const tmp = mkdtempSync(join(tmpdir(), 'pcap-'));
const page_html = join(tmp, 'dash.html');
execFileSync('python', ['-c',
  `import pathlib,sys;sys.path.insert(0,${JSON.stringify(ROOT)});` +
  `from src.dashboard.aurora_html import DASHBOARD_HTML as D;` +
  `pathlib.Path(${JSON.stringify(page_html)}).write_text(D)`],
  { cwd: ROOT, stdio: ['ignore', 'ignore', 'inherit'] });

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });

// CAPTURED AT TWO WIDTHS. The dashboard is responsive, so scaling a 1452px
// capture down to a phone makes its type about four pixels tall, and cropping
// a readable corner instead shows a fragment. Neither is what a phone user of
// the product sees. Capturing at a phone viewport gives the real mobile layout,
// which then sits on the mobile landing page at roughly 1:1.
async function captureAt(width, height) {
const page = await browser.newPage({ viewport: { width, height },
                                     deviceScaleFactor: 1 });
const errors = [];
page.on('pageerror', e => errors.push(String(e)));

// The socket is stubbed rather than removed: the app pushes score frames through
// it, and a stream card with no score is a different component state.
await page.addInitScript(([scores]) => {
  try { localStorage.setItem('hz_welcome_seen', '1'); } catch (e) {}
  window.__SCORES = scores;
  window.WebSocket = class {
    constructor() {
      this.readyState = 1;
      setTimeout(() => {
        this.onopen && this.onopen();
        for (const [ch, s] of Object.entries(window.__SCORES)) {
          this.onmessage && this.onmessage({ data: JSON.stringify(
            { event: 'score_update', channel: ch, score: s.score, breakdown: s.breakdown }) });
        }
      }, 60);
    }
    send() {} close() {}
  };
}, [SCORES]);

const json = (o) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
await page.route('**/*', async (route) => {
  const u = route.request().url();
  // The page is loaded over file://, so the app's own fetch('/clips') resolves
  // to file:///clips. Passing every file:// URL straight through therefore sent
  // the API calls to the filesystem, where they failed silently and the app
  // rendered an empty account. Only the page itself is passed through.
  if (u === 'file://' + page_html) return route.continue();
  const path = u.replace(/^file:\/\//, '').split('?')[0];
  if (path === '/me')                 return route.fulfill(json(ME));
  if (path === '/streams/suggest')    return route.fulfill(json({ recent: [], popular: [] }));
  if (path === '/streams')            return route.fulfill(json(STREAMS));
  if (path === '/clips/undo')         return route.fulfill(json([]));
  if (path === '/clips')              return route.fulfill(json(CLIPS));
  if (path === '/profiles')           return route.fulfill(json(PROFILES));
  if (path === '/stats')              return route.fulfill(json({}));
  if (u.includes('/me'))              return route.fulfill(json(ME));
  if (u.includes('/streams/suggest')) return route.fulfill(json({ recent: [], popular: [] }));
  if (u.includes('/streams'))         return route.fulfill(json(STREAMS));
  if (u.includes('/clips/undo'))      return route.fulfill(json([]));
  if (u.includes('/clips'))           return route.fulfill(json(CLIPS));
  if (u.includes('/profiles'))        return route.fulfill(json(PROFILES));
  if (u.includes('/stats'))           return route.fulfill(json({}));
  if (u.includes('unpkg.com')) {
    const rel = u.includes('react-dom') ? 'react-dom/umd/react-dom.production.min.js'
              : u.includes('/react@')   ? 'react/umd/react.production.min.js'
              : '@babel/standalone/babel.min.js';
    return route.fulfill({ path: join(ROOT, 'node_modules', rel),
                           contentType: 'application/javascript' });
  }
  return route.fulfill(json([]));
});

await page.goto('file://' + page_html);
await page.waitForTimeout(2600);

// ── Lift the rendered DOM ────────────────────────────────────────────────────

const goto = async (label) => {
  await page.evaluate((l) => {
    const it = [...document.querySelectorAll('.rd-navitem')]
      // includes(), not startsWith(): the Clip Review item renders its pending
      // count inside the same node, so its textContent is "5Clip Review".
      .find(n => n.textContent.trim().toLowerCase().includes(l));
    if (it) it.click();
  }, label);
  await page.waitForTimeout(900);
};

// Returns the markup AND the box it rendered in, because the landing page has
// to crop it at the real aspect ratio rather than guess one.
const grab = async (sel) => page.evaluate((s) => {
  const el = document.querySelector(s);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { html: el.outerHTML, w: Math.round(r.width), h: Math.round(r.height) };
}, sel);

const captures = {};

// 1. Live Streams: the channel monitor. The whole two-pane layout, because the
//    sidebar of channels and the detail panel are the same surface in use.
await goto('live streams');
captures.streams = await grab('.rd-streams-layout');

// 2. Clip Review: the queue, with its stat row and filters.
await goto('clip review');
captures.review = await grab('.rd-main') || await grab('.rd-screen');

// 3. Clip detail: the modal, opened on the clip whose numbers we seeded.
await page.evaluate(() => {
  const card = document.querySelector('.rd-clip');
  const hit = card && card.querySelector('.rd-media');
  (hit || card).click();
});
await page.waitForTimeout(800);
captures.detail = await grab('.rd-modal') || await grab('.rd-modal-bg');

const rawCss = await page.evaluate(() =>
  [...document.querySelectorAll('style')].map(s => s.textContent).join('\n'));

console.log(`captured @${width}px:`);
for (const [k, v] of Object.entries(captures))
  console.log(`   ${k.padEnd(8)} ${v ? (v.html.length + ' chars  ' + v.w + 'x' + v.h) : 'NULL'}`);
if (Object.values(captures).some(v => !v)) { await browser.close(); throw new Error(`a capture came back empty @${width}px`); }
if (errors.length) console.log('   page errors:', errors.slice(0, 3));
await page.close();
return { captures, rawCss };
}

const desk = await captureAt(1600, 1100);
const mob  = await captureAt(430, 1200);
await browser.close();
const rawCss = desk.rawCss;

// ── Sanitise ─────────────────────────────────────────────────────────────────
// These are pictures of the product, not the product. Everything that could
// reach the network, take focus, or collide with the landing page is removed.

function sanitise(html, tag) {
  let out = html;
  // Nothing should ever fetch. None of these appear with the seeded data (the
  // components fall back to a generated gradient when a clip has no stored
  // thumbnail), but a future seed with a real thumbnail_url would smuggle a
  // third-party request onto the marketing page, so strip them regardless.
  out = out.replace(/<iframe\b[\s\S]*?<\/iframe>/gi, '');
  out = out.replace(/<(img|video|source)\b[^>]*>/gi, '');
  out = out.replace(/<script\b[\s\S]*?<\/script>/gi, '');
  // A landing-page visitor must not tab through 30 dead controls, so every
  // control becomes a span carrying the same classes. The wrapper is inert
  // too; this is the belt for browsers without it.
  out = out.replace(/<button\b([^>]*)>/gi, '<span$1>').replace(/<\/button>/gi, '</span>');
  out = out.replace(/<(a)\b([^>]*?)\shref="[^"]*"([^>]*)>/gi, '<span$2$3>')
           .replace(/<\/a>/gi, '</span>');
  out = out.replace(/\s(tabindex|contenteditable|draggable)="[^"]*"/gi, '');
  // ids are global. Namespace them so nothing on the landing page collides.
  out = out.replace(/\sid="([^"]+)"/g, (m, v) => ' id="pcap-' + tag + '-' + v + '"');
  out = out.replace(/\s(for|aria-controls|aria-labelledby|aria-describedby)="([^"]+)"/g,
                    (m, a, v) => ' ' + a + '="pcap-' + tag + '-' + v + '"');
  return out.trim();
}

// ── Scope the stylesheet ─────────────────────────────────────────────────────
// The dashboard and the landing page share twelve short class names (accent,
// hot, ic, on, k, v ...). Rather than rename anything in either, every selector
// is prefixed so the dashboard's rules cannot escape the capture. :root, html
// and body become the wrapper itself, which is where the custom properties the
// components read have to land.

function scopeCss(css, scope) {
  const out = [];
  // Strip comments first so a brace inside one cannot confuse the split.
  css = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let i = 0;
  while (i < css.length) {
    const at = css.indexOf('@', i);
    const brace = css.indexOf('{', i);
    if (brace === -1) break;
    if (at !== -1 && at < brace) {
      // STATEMENT at-rules end in ';', not '{'. The dashboard's stylesheet
      // opens with @import url(fonts.googleapis.com ... Inter). Treating that
      // as a block prelude swallowed the :root that followed it and emitted it
      // UNSCOPED, which would have redefined the landing page's own custom
      // properties from a stylesheet meant for a different surface.
      // The terminator has to be found with parens and quotes tracked. The
      // font URL itself contains semicolons (family=Inter:wght@400;500;600...),
      // so a plain indexOf(';') split mid-URL and emitted the remainder as a
      // selector: the output began ".pcap 500;600;700;800&display=swap');".
      let semi = -1, dep = 0, q = '';
      for (let k = at; k < css.length; k++) {
        const ch = css[k];
        if (q) { if (ch === q && css[k - 1] !== '\\') q = ''; continue; }
        if (ch === '"' || ch === "'") { q = ch; continue; }
        if (ch === '(') dep++;
        else if (ch === ')') dep--;
        else if (ch === '{' && dep === 0) break;
        else if (ch === ';' && dep === 0) { semi = k; break; }
      }
      if (semi !== -1 && semi < brace) {
        const stmt = css.slice(at, semi + 1).trim();
        // @import is dropped rather than scoped. The landing page makes ZERO
        // external requests and self-hosts its faces; inheriting a
        // render-blocking Google Fonts call for a decorative capture is not a
        // trade worth making. The font stack below it still names Inter, so a
        // machine that has it uses it and everything else falls back.
        if (!/^@import/i.test(stmt)) out.push(stmt);
        i = semi + 1;
        continue;
      }
      // @media / @supports: keep the prelude, scope what is inside.
      const prelude = css.slice(at, brace).trim();
      let d = 1, j = brace + 1;
      while (j < css.length && d > 0) { if (css[j] === '{') d++; if (css[j] === '}') d--; j++; }
      const inner = css.slice(brace + 1, j - 1);
      if (/^@(media|supports)/.test(prelude)) out.push(prelude + '{' + scopeCss(inner, scope) + '}');
      else out.push(prelude + '{' + inner + '}');       // @keyframes, @font-face: verbatim
      i = j;
      continue;
    }
    const sel = css.slice(i, brace).trim();
    let d = 1, j = brace + 1;
    while (j < css.length && d > 0) { if (css[j] === '{') d++; if (css[j] === '}') d--; j++; }
    const body = css.slice(brace + 1, j - 1);
    i = j;
    if (!sel) continue;
    const scoped = sel.split(',').map(one => {
      const t = one.trim();
      if (!t) return t;
      if (/^(:root|html|body)$/.test(t)) return scope;
      if (/^(:root|html|body)\b/.test(t)) return t.replace(/^(:root|html|body)/, scope);
      if (t.startsWith('@')) return t;
      return scope + ' ' + t;
    }).join(',');
    out.push(scoped + '{' + body + '}');
  }
  return out.join('\n');
}

// Prune to rules the captures can actually use, so the landing page does not
// carry 60KB of styling for screens it never shows.
function prune(css, html) {
  const present = new Set();
  for (const m of html.matchAll(/class="([^"]+)"/g))
    m[1].split(/\s+/).forEach(c => c && present.add(c));
  return css.split('\n').filter(rule => {
    const sel = rule.slice(0, rule.indexOf('{'));
    const classes = [...sel.matchAll(/\.([A-Za-z_][\w-]*)/g)].map(m => m[1]);
    if (!classes.length) return true;                       // element/:root rules
    return classes.some(c => present.has(c) || c === 'pcap');
  }).join('\n');
}

// ONE COPY OF THE MARKUP. The DOM the components render is byte-identical at
// both viewports; only the layout differs, because the dashboard's media
// queries key off the VIEWPORT, not the element. So the landing page carries
// the markup once and just swaps the crop box at its own breakpoint, and the
// product's own media queries then produce the mobile layout for free.
for (const k of Object.keys(desk.captures)) {
  if (desk.captures[k].html !== mob.captures[k].html)
    throw new Error(`${k}: DOM differs between viewports; the page needs both copies`);
}
const clean = {};
for (const [k, v] of Object.entries(desk.captures))
  clean[k] = { ...v, html: sanitise(v.html, k), mw: mob.captures[k].w, mh: mob.captures[k].h };
const allHtml = Object.values(clean).map(c => c.html).join('\n');
const scoped = prune(scopeCss(rawCss, '.pcap'), allHtml + ' class="pcap"');

console.log('css: ' + rawCss.length + ' raw -> ' + scoped.length + ' scoped+pruned');

// ── Emit ─────────────────────────────────────────────────────────────────────
const py = (v) => '"""' + v.replace(/\\/g, '\\\\').replace(/"""/g, '\\"\\"\\"') + '"""';
writeFileSync(OUT, `"""Captured surfaces of the real product, for the landing page.

GENERATED FILE. Do not edit. Run scripts/capture_product_ui.mjs to rebuild.

Every string below is DOM that the real dashboard components rendered in a real
browser, plus the stylesheet that painted them. Nothing here was drawn by hand,
which is the whole point: the landing page shows the product because it IS the
product's markup, down to the class names.

The capture is fed seeded data shaped like the real API responses (see the
script for which source file each shape comes from) and is then sanitised:
no images, iframes, videos or scripts, every control turned into a span so it
cannot take focus, and every id namespaced. The CSS is scoped under .pcap so
the dashboard's rules cannot reach the rest of the page, and pruned to the
classes these captures actually use.
"""

# The dashboard's stylesheet, every selector prefixed with .pcap.
PRODUCT_CSS = ${py(scoped)}

# Live Streams: the channel list and the monitor panel.
STREAMS_HTML = ${py(clean.streams.html)}
STREAMS_BOX   = (${clean.streams.w}, ${clean.streams.h})
STREAMS_BOX_M = (${clean.streams.mw}, ${clean.streams.mh})

# Clip Review: the queue, its counters and its filters.
REVIEW_HTML = ${py(clean.review.html)}
REVIEW_BOX   = (${clean.review.w}, ${clean.review.h})
REVIEW_BOX_M = (${clean.review.mw}, ${clean.review.mh})

# One clip opened: why it fired, and what it is.
DETAIL_HTML = ${py(clean.detail.html)}
DETAIL_BOX   = (${clean.detail.w}, ${clean.detail.h})
DETAIL_BOX_M = (${clean.detail.mw}, ${clean.detail.mh})
`);
console.log('wrote ' + OUT);
