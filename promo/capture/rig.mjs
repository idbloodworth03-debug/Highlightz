// The capture rig: a real browser on the real app, stepped one 60 fps frame at
// a time (vt.js), each frame screenshotted at device-scale 2 (3 on a phone)
// and piped straight into ffmpeg. Also: a cursor with click ripples drawn into
// the page, eased scrolling, and helpers for the shot scripts in capture.mjs.
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const PROMO = path.dirname(HERE);
const VT = fs.readFileSync(path.join(HERE, 'vt.js'), 'utf8');
export const TL = JSON.parse(fs.readFileSync(path.join(PROMO, 'src', 'timeline.json'), 'utf8'));
export const FPS = TL.fps;
const BEAT_F = (FPS * 60) / TL.bpm;
export const fr = (beats) => Math.round(beats * BEAT_F);

export const LAYOUTS = {
  wide: {viewport: {width: 1440, height: 900}, deviceScaleFactor: 2, isMobile: false, hasTouch: false},
  tall: {viewport: {width: 390, height: 844}, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'},
};

export const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : 1 - Math.pow(1 - t, 3));          // out-cubic
export const easeInOut = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
// Piecewise keyframes [[frame, value], ...] with easing between them.
export const keys = (k, f, e = easeInOut) => {
  if (f <= k[0][0]) return k[0][1];
  for (let i = 1; i < k.length; i++) {
    if (f <= k[i][0]) {
      const [f0, v0] = k[i - 1];
      const [f1, v1] = k[i];
      return v0 + (v1 - v0) * e((f - f0) / Math.max(1, f1 - f0));
    }
  }
  return k[k.length - 1][1];
};

export async function launch({layout, base, session, route, extraInit}) {
  // Behind an outbound proxy (a cloud sandbox), the browser goes through it too.
  const proxyUrl = process.env.HTTPS_PROXY || process.env.https_proxy;
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--force-color-profile=srgb', '--hide-scrollbars', '--disable-features=PaintHolding'],
    proxy: proxyUrl ? {server: proxyUrl, bypass: process.env.NO_PROXY || process.env.no_proxy} : undefined,
  });
  const L = LAYOUTS[layout];
  const context = await browser.newContext({
    ...L,
    colorScheme: 'dark',
    reducedMotion: 'no-preference',
    locale: 'en-US',
    timezoneId: 'America/New_York',
  });
  if (session) {
    const u = new URL(base);
    await context.addCookies([{name: 'session', value: session, domain: u.hostname, path: '/', secure: u.protocol === 'https:', httpOnly: true}]);
  }
  const origin = new URL(base).origin;
  await context.addInitScript(({vt, origin, route}) => {
    if (location.origin !== origin) return;
    (0, eval)(vt);
    try { if (route) sessionStorage.setItem('hz_route', route); } catch (e) {}
  }, {vt: VT, origin, route});
  if (extraInit) await context.addInitScript(extraInit);
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('[page error]', e.message));
  return {browser, context, page};
}

// Everything that should be on screen is loaded, decoded and laid out.
export async function warm(page, {selector, settleMs = 1200} = {}) {
  await page.waitForLoadState('networkidle', {timeout: 60000}).catch(() => {});
  if (selector) await page.waitForSelector(selector, {state: 'visible', timeout: 60000});
  await page.evaluate(async () => {
    await document.fonts.ready;
    // Lazy images load as they scroll into view, which on a 60 fps whip is
    // after they are on screen: load every one of them up front.
    for (const i of document.images) if (i.loading === 'lazy') i.loading = 'eager';
    const imgs = [...document.images].filter((i) => !i.complete);
    await Promise.all(imgs.map((i) => new Promise((r) => { i.onload = i.onerror = r; setTimeout(r, 8000); })));
    document.documentElement.style.scrollBehavior = 'auto';
  });
  await page.waitForTimeout(settleMs);
}

// The cursor and its click ripple, drawn in the page so they sit exactly on
// the real UI. Positions are viewport CSS px; the rig moves it every frame.
export async function installCursor(page, scale = 1) {
  await page.evaluate((scale) => {
    const c = document.createElement('div');
    c.id = '__cursor';
    c.innerHTML = `<svg width="${28 * scale}" height="${40 * scale}" viewBox="0 0 28 40" style="filter:drop-shadow(0 3px 6px rgba(0,0,0,.45))"><path d="M2 2 L2 31 L9.5 24.5 L14.5 36.5 L19.5 34.5 L14.6 22.6 L24.5 22.6 Z" fill="#fff" stroke="#0A0A0C" stroke-width="2" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, {position: 'fixed', left: '0px', top: '0px', zIndex: 2147483647, pointerEvents: 'none', transform: 'translate(-2px,-2px)', opacity: '0'});
    const r = document.createElement('div');
    r.id = '__ripple';
    Object.assign(r.style, {position: 'fixed', left: '0px', top: '0px', width: `${56 * scale}px`, height: `${56 * scale}px`, marginLeft: `${-28 * scale}px`, marginTop: `${-28 * scale}px`, borderRadius: '50%', border: `${3 * scale}px solid #F343FE`, boxShadow: '0 0 24px rgba(243,67,254,.8)', zIndex: 2147483646, pointerEvents: 'none', opacity: '0'});
    document.body.append(r, c);
  }, scale);
}

export async function setCursor(page, {x, y, visible = true, ripple = -1}) {
  await page.evaluate(({x, y, visible, ripple}) => {
    const c = document.getElementById('__cursor');
    const r = document.getElementById('__ripple');
    if (!c) return;
    c.style.left = x + 'px';
    c.style.top = y + 'px';
    c.style.opacity = visible ? '1' : '0';
    c.style.transform = `translate(-2px,-2px) scale(${ripple >= 0 && ripple < 0.25 ? 0.86 : 1})`;
    if (ripple >= 0 && ripple <= 1) {
      r.style.left = x + 'px';
      r.style.top = y + 'px';
      r.style.opacity = String(1 - ripple);
      r.style.transform = `scale(${0.3 + ripple * 1.6})`;
    } else {
      r.style.opacity = '0';
    }
  }, {x, y, visible, ripple});
}

export const centerOf = async (page, selector) => page.evaluate((s) => {
  const el = typeof s === 'string' ? document.querySelector(s) : null;
  if (!el) return null;
  const b = el.getBoundingClientRect();
  return {x: b.left + b.width / 2, y: b.top + b.height / 2, rect: {x: b.left, y: b.top, w: b.width, h: b.height}};
}, selector);

export const docTop = async (page, selector, offset = 0) => page.evaluate(({s, offset}) => {
  const el = document.querySelector(s);
  if (!el) return null;
  return Math.max(0, el.getBoundingClientRect().top + window.scrollY - offset);
}, {s: selector, offset});

// Record `frames` frames into `out` (H.264, crf 10). perFrame(i) runs before
// each frame's virtual-time step, so it can scroll, move the cursor, click,
// or push a WebSocket message that this frame should show.
export async function record({page, frames, out, perFrame, afterStep}) {
  fs.mkdirSync(path.dirname(out), {recursive: true});
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '10', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], {stdio: ['pipe', 'inherit', 'inherit']});
  await page.evaluate(() => window.__vt.start());
  const t0 = Date.now();
  for (let i = 0; i < frames; i++) {
    if (perFrame) await perFrame(i);
    await page.evaluate((ms) => window.__vt.step(ms), 1000 / FPS);
    if (afterStep) await afterStep(i);
    const png = await page.screenshot({type: 'png', animations: 'allow', caret: 'hide', scale: 'device'});
    if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once('drain', r));
    if (i % 30 === 0) process.stdout.write(`  ${path.basename(out)} ${i}/${frames} (${((Date.now() - t0) / 1000).toFixed(0)} s)\r`);
  }
  ff.stdin.end();
  await new Promise((r, j) => ff.on('close', (c) => (c === 0 ? r() : j(new Error('ffmpeg exited ' + c)))));
  process.stdout.write(`  ${path.basename(out)} ${frames}/${frames} done in ${((Date.now() - t0) / 1000).toFixed(0)} s\n`);
}
