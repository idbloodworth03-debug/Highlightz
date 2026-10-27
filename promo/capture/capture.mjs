#!/usr/bin/env node
// Records the real UI shots of the promo from the live app.
//
//   node capture/capture.mjs --session <demo-account session cookie> \
//        [--shots landing,live,review,editor,post,campaign] [--layouts wide,tall]
//
// Every shot is the real page on https://highlightz.app, signed in as the demo
// account where it needs one, stepped frame by frame (vt.js) so the motion is
// exact, and written to public/captures/<shot>-<layout>.mp4 with its metadata
// in src/data/promo-data.json.
//
// --test --base <url> runs the same scripts against any other server (a local
// copy) and writes to --out ONLY: never to public/captures and never to the
// data file, so nothing recorded against non-production data can reach the
// video.
import fs from 'node:fs';
import path from 'node:path';
import {centerOf, docTop, fr, installCursor, keys, launch, PROMO, record, setCursor, TL, warm, ease} from './rig.mjs';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
    return acc;
  }, []),
);
const PROD = 'https://highlightz.app';
const TEST = !!args.test;
const BASE = (args.base || PROD).replace(/\/$/, '');
if (!TEST && BASE !== PROD) throw new Error('Real captures come from ' + PROD + ' only. Use --test for anything else.');
const OUT = TEST ? path.resolve(args.out || '/tmp/hz-test-captures') : path.join(PROMO, 'public', 'captures');
const SESSION = args.session || process.env.HZ_SESSION || '';
const SHOTS = String(args.shots || 'landing,live,review,editor,post,campaign').split(',');
const LAYOUTS = String(args.layouts || 'wide,tall').split(',');
const DATA_FILE = path.join(PROMO, 'src', 'data', 'promo-data.json');

// Scene lengths straight from the timeline, so a capture is exactly as long
// as the shot it fills.
const sceneLen = (id) => {
  const s = TL.promo.scenes.find((x) => x.id === id);
  return fr(s.to) - fr(s.from);
};
const shotAt = (k, scene) => fr(TL.promo.shots[k]) - fr(TL.promo.scenes.find((x) => x.id === scene).from);
const rel = (beats) => fr(beats);    // beats measured from a shot's own start

// Cross-origin embeds (the Twitch player on the landing page) keep their own
// clock; stepping the page cannot pause them. They are replaced, for the
// capture only, by a still of themselves taken while warming up.
async function freezeEmbeds(page) {
  const frames = await page.$$('iframe');
  for (const f of frames) {
    const src = (await f.getAttribute('src')) || '';
    if (!src || src.startsWith(BASE)) continue;
    const box = await f.boundingBox();
    if (!box || box.width < 10) continue;
    await f.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(1500);
    const png = await f.screenshot().catch(() => null);
    if (!png) continue;
    await f.evaluate((el, data) => {
      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + data;
      img.style.cssText = getComputedStyle(el).cssText;
      img.style.objectFit = 'cover';
      img.width = el.clientWidth;
      img.height = el.clientHeight;
      el.replaceWith(img);
    }, png.toString('base64'));
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

// Admin-only chrome stays out of the video (the demo account is an admin so
// that joining the campaign does not change its public joined count).
const HIDE_ADMIN_CSS = `
  [data-admin], .admin-only, a[href="/admin"], .cmp-status.draft { display: none !important; }
`;

async function maskNames(page) {
  const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
  if (!data.mask_entrant_names) return;
  await page.evaluate(() => {
    const mask = (n) => n.slice(0, 1) + '•'.repeat(Math.max(3, Math.min(6, n.length - 1)));
    for (const b of document.querySelectorAll('.cmp-who > b')) {
      const t = b.firstChild;
      if (t && t.nodeType === 3 && t.textContent.trim()) t.textContent = mask(t.textContent.trim());
    }
    for (const b of document.querySelectorAll('.cmp-split b')) {
      if (b.parentElement && /place/.test(b.parentElement.textContent || '') && b.closest('li') && b.closest('li').querySelector('span b') === b) {
        b.textContent = mask(b.textContent.trim());
      }
    }
  });
}

// ── the shots ───────────────────────────────────────────────────────────────

const SHOT = {
  // Hero → What it catches → Ten streams live → a whip past pricing → Get
  // your stream clipped. Beats are counted from the shot's start (beat 4).
  async landing({page, layout, out}) {
    const frames = sceneLen('landing');
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await warm(page, {selector: '#proof-h'});
    await freezeEmbeds(page);
    const nav = await page.evaluate(() => document.querySelector('nav, header')?.getBoundingClientRect().height || 71);
    const y = async (sel, extra = 0) => ((await docTop(page, sel, nav)) ?? 0) + extra;
    const catches = await y('#catches');
    const watch = await y('#watch');
    const camp = (await docTop(page, '#campaigns', nav + 24)) ?? (await y('#pricing'));
    const K = [[0, 0], [rel(1), 0], [rel(3.5), catches], [rel(4.5), catches], [rel(5.75), watch], [rel(7), watch], [rel(8), camp], [frames, camp + 36]];
    const focusSel = '#campaigns';
    await record({page, frames, out, perFrame: (i) => page.evaluate((v) => window.scrollTo(0, v), keys(K, i))});
    await page.evaluate((v) => window.scrollTo(0, v), keys(K, frames - 1));
    return {focus: (await centerOf(page, focusSel))?.rect};
  },

  // The real Live Streams screen, fed the real score_update messages recorded
  // from a live channel by record-live.mjs, compressed so the climb to the
  // crossing fills the shot; the crossing lands on the shot's key frame.
  async live({page, layout, out, rec}) {
    const crossF = shotAt('bot.cross', 'bot');
    const frames = crossF + 64;
    const msgs = rec.messages.filter((m) => m.msg.event === 'score_update' && m.msg.channel === rec.channel);
    const tCross = rec.cross_t;
    const window_s = Math.min(40, tCross - msgs[0].t);
    const toFrame = (t) => (t <= tCross ? crossF - ((tCross - t) / window_s) * crossF : crossF + (t - tCross) * TL.fps);
    const plan = msgs.map((m) => ({f: Math.round(toFrame(m.t)), msg: m.msg})).filter((p) => p.f < frames);
    const early = plan.filter((p) => p.f < 0);
    const due = plan.filter((p) => p.f >= 0);
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await warm(page, {selector: `text=${rec.channel}`});
    // Fill the chart's history with what came before the window, at once.
    for (const p of early.slice(-40)) await page.__ws.send(p.msg);
    await page.waitForTimeout(600);
    let k = 0;
    await record({page, frames, out, perFrame: async (i) => {
      while (k < due.length && due[k].f <= i) await page.__ws.send(due[k++].msg);
      if (k) await page.waitForTimeout(15);
    }});
    const card = await centerOf(page, `[data-channel="${rec.channel}"]`) || await page.evaluate((ch) => {
      const el = [...document.querySelectorAll('*')].find((e) => e.children.length < 40 && e.textContent && e.textContent.includes(ch) && e.querySelector('svg'));
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return {rect: {x: b.left, y: b.top, w: b.width, h: b.height}};
    }, rec.channel);
    return {focus: card?.rect, key_frame: crossF};
  },

  // Clip Review: the real clip card arrives the way it did live (its real
  // clip_ready message) and the cursor approves it, for real.
  async review({page, layout, out, rec}) {
    const frames = shotAt('bot.editor', 'bot') - shotAt('bot.review', 'bot');
    const approveAt = shotAt('bot.approve', 'bot') - shotAt('bot.review', 'bot');
    const ready = rec.messages.find((m) => m.msg.event === 'clip_ready');
    if (!ready) throw new Error('the recording has no clip_ready: let record-live.mjs run until a clip is captured');
    const clipId = ready.msg.clip.id;
    // The initial list without the new clip, so it can arrive on camera.
    await page.route('**/clips?**', async (route) => {
      const res = await route.fetch();
      const body = await res.json().catch(() => null);
      if (!body) return route.fulfill({response: res});
      const strip = (v) => (Array.isArray(v) ? v.filter((c) => c.id !== clipId) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).filter(([k]) => k !== clipId)) : v);
      route.fulfill({response: res, json: body.clips ? {...body, clips: strip(body.clips)} : strip(body)});
    });
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await warm(page, {selector: 'main'});
    await installCursor(page, layout === 'tall' ? 0.8 : 1);
    let target = null;
    let start = null;
    await record({page, frames, out, perFrame: async (i) => {
      if (i === 4) await page.__ws.send(ready.msg);
      if (i === 14 && !target) {
        const btn = page.getByRole('button', {name: /^Approve/}).first();
        const b = await btn.boundingBox();
        if (b) target = {x: b.x + b.width / 2, y: b.y + b.height / 2};
        const vp = page.viewportSize();
        start = {x: vp.width * 0.82, y: vp.height * 0.85};
      }
      if (target) {
        const p = ease(Math.min(1, (i - 14) / Math.max(1, approveAt - 14)));
        const x = start.x + (target.x - start.x) * p;
        const y = start.y + (target.y - start.y) * p;
        const ripple = i >= approveAt ? (i - approveAt) / 16 : -1;
        await setCursor(page, {x, y, ripple});
        if (i === approveAt) {
          await page.mouse.click(target.x, target.y);
          await page.waitForLoadState('networkidle').catch(() => {});
        }
      }
    }});
    const card = await centerOf(page, `[data-clip-id="${clipId}"]`);
    return {focus: card?.rect || (target ? {x: target.x - 180, y: target.y - 260, w: 360, h: 320} : undefined)};
  },

  // Clip Editor: the captured clip, a vertical template, captions lighting up
  // word by word (the editor's own highlight), and the editor's title.
  async editor({page, layout, out, rec}) {
    const frames = shotAt('bot.clip2', 'bot') - shotAt('bot.editor', 'bot');
    const ready = rec.messages.find((m) => m.msg.event === 'clip_ready');
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await page.evaluate(() => sessionStorage.setItem('hz_route', 'library'));
    await page.reload({waitUntil: 'domcontentloaded'});
    await warm(page, {selector: 'main'});
    const card = ready ? page.locator(`[data-clip-id="${ready.msg.clip.id}"]`).first() : page.locator('main').first();
    await card.getByRole('button', {name: /edit/i}).first().click();
    await page.waitForSelector('.ed-tpl', {timeout: 60000});
    await page.locator('.ed-tpl', {has: page.locator('.ed-tpl-ic.punch')}).first().click().catch(() => {});
    await page.waitForTimeout(2500);                // captions and the first frame decode
    await page.locator('video').first().evaluate((v) => { v.currentTime = 0; v.play(); });
    await page.waitForTimeout(300);
    await record({page, frames, out});
    return {focus: (await centerOf(page, 'canvas'))?.rect};
  },

  // Post now, with TikTok picked: the button pulses under the cursor. Nothing
  // is posted.
  async post({page, layout, out, rec}) {
    const frames = sceneLen('bot') - shotAt('bot.post', 'bot');
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await page.evaluate(() => sessionStorage.setItem('hz_route', 'library'));
    await page.reload({waitUntil: 'domcontentloaded'});
    await warm(page, {selector: 'main'});
    await page.getByRole('button', {name: /^Post now$/}).first().click();
    await page.getByText(/TikTok/).first().click().catch(() => {});
    const btn = page.getByRole('button', {name: /Post now to TikTok/});
    await btn.waitFor({timeout: 30000});
    const b = await btn.boundingBox();
    await installCursor(page, layout === 'tall' ? 0.8 : 1);
    const vp = page.viewportSize();
    await record({page, frames, out, perFrame: async (i) => {
      const p = ease(Math.min(1, i / 24));
      await setCursor(page, {x: vp.width * 0.75 + (b.x + b.width * 0.6 - vp.width * 0.75) * p, y: vp.height * 0.9 + (b.y + b.height / 2 - vp.height * 0.9) * p});
      await btn.evaluate((el, t) => { el.style.boxShadow = `0 0 ${12 + t * 28}px rgba(243,67,254,${0.3 + t * 0.5})`; }, i >= 24 ? Math.exp(-((i - 24) % 28) / 9) : 0);
    }});
    return {focus: {x: b.x - 40, y: b.y - 220, w: b.width + 80, h: 300}};
  },

  // The live campaign's real page. Its numbers count up and its leaderboard
  // rows slide into rank order, on the real elements, in time with the edit.
  async campaign({page, layout, out}) {
    const frames = sceneLen('campaign');
    const tPool = shotAt('campaign.pool', 'campaign');
    const tBoard = shotAt('campaign.board', 'campaign');
    const tGlow = shotAt('campaign.glow', 'campaign');
    await page.addStyleTag({content: HIDE_ADMIN_CSS}).catch(() => {});
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await warm(page, {selector: '.cmp-card'});
    await page.addStyleTag({content: HIDE_ADMIN_CSS});
    const live = page.locator('.cmp-card', {has: page.locator('.cmp-status.live')}).first();
    await live.click();
    await warm(page, {selector: '.cmp-page'});
    await maskNames(page);
    const nav = 0;
    const yBoard = (await docTop(page, '.cmp-rows', 140)) ?? (await docTop(page, '.cmp-sec', 140)) ?? 0;
    const yTop = 0;
    const K = layout === 'tall'
      ? [[0, yTop], [rel(1.5), yTop], [rel(5), yBoard], [frames, yBoard + 20]]
      : [[0, yTop], [rel(4), yTop], [rel(6), Math.min(yBoard, yBoard * 0.6 + 120)], [frames, Math.min(yBoard, yBoard * 0.6 + 140)]];
    // The real values, read off the page before anything is animated.
    const real = await page.evaluate(() => ({
      pool: document.querySelector('.cmp-box .cmp-big')?.textContent || '',
      scores: [...document.querySelectorAll('.cmp-row .cmp-score')].map((s) => s.firstChild?.textContent || ''),
    }));
    const num = (s) => Number(String(s).replace(/[^0-9.]/g, '')) || 0;
    await record({page, frames, out, perFrame: async (i) => {
      await page.evaluate(({y, i, tPool, tBoard, tGlow, pool, scores}) => {
        window.scrollTo(0, y);
        const e = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : 1 - Math.pow(1 - t, 3));
        const fmt = (v, like) => (like.trim().startsWith('$') ? '$' : '') + Math.round(v).toLocaleString('en-US');
        const big = document.querySelector('.cmp-box .cmp-big');
        if (big) big.textContent = fmt(pool.n * e((i - tPool) / 22), pool.s);
        document.querySelectorAll('.cmp-row').forEach((row, r) => {
          const p = e((i - tBoard - r * 6) / 14);
          row.style.opacity = String(p);
          row.style.transform = `translateX(${(1 - p) * 60}px)`;
          const sc = row.querySelector('.cmp-score');
          if (sc && sc.firstChild && scores[r]) sc.firstChild.textContent = Math.round(scores[r].n * e((i - tBoard - r * 6) / 22)).toLocaleString('en-US');
          if (row.classList.contains('top')) {
            const g = e((i - tGlow - r * 3) / 12);
            row.style.boxShadow = `0 0 ${g * 26}px rgba(243,67,254,${g * 0.55}), inset 0 0 0 1px rgba(243,67,254,${g * 0.5})`;
          }
        });
      }, {y: keys(K, i), i, tPool, tBoard, tGlow, pool: {n: num(real.pool), s: real.pool}, scores: real.scores.map((s) => ({n: num(s)}))});
    }});
    return {focus: (await centerOf(page, '.cmp-rows'))?.rect || (await centerOf(page, '.cmp-page'))?.rect};
  },
};

const ROUTE = {landing: null, live: 'streams', review: 'review', editor: 'library', post: 'library', campaign: 'campaigns'};
const NEEDS_SESSION = {landing: false, live: true, review: true, editor: true, post: true, campaign: true};
const NEEDS_REC = {live: true, review: true, editor: true};

async function main() {
  const recPath = path.join(PROMO, 'capture', 'recordings', 'live.json');
  const rec = fs.existsSync(recPath) ? JSON.parse(fs.readFileSync(recPath, 'utf8')) : null;
  const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
  for (const shot of SHOTS) {
    if (NEEDS_SESSION[shot] && !SESSION) { console.log(`skip ${shot}: needs --session (the demo account)`); continue; }
    if (NEEDS_REC[shot] && !rec) { console.log(`skip ${shot}: run capture/record-live.mjs first`); continue; }
    for (const layout of LAYOUTS) {
      const {browser, page} = await launch({layout, base: BASE, session: NEEDS_SESSION[shot] ? SESSION : '', route: ROUTE[shot]});
      if (shot === 'live' || shot === 'review') {
        // The real socket is replaced by the recording: nothing live leaks in.
        await page.routeWebSocket(/\/ws$/, (ws) => { page.__wsRoute = ws; });
        page.__ws = {send: async (msg) => { for (let k = 0; k < 50 && !page.__wsRoute; k++) await page.waitForTimeout(100); page.__wsRoute.send(JSON.stringify(msg)); }};
      }
      const file = `${shot}-${layout}.mp4`;
      const out = path.join(OUT, file);
      console.log(`${shot} (${layout})`);
      try {
        const meta = await SHOT[shot]({page, layout, out, rec});
        const L = page.viewportSize();
        const probe = JSON.parse(await new Promise((res) => {
          import('node:child_process').then(({execFile}) => execFile('ffprobe', ['-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,nb_read_frames', '-of', 'json', out], (e, so) => res(so || '{}')));
        }));
        const st = (probe.streams || [{}])[0];
        const entry = {file: 'captures/' + file, width: st.width, height: st.height, fps: 60, frames: Number(st.nb_read_frames), viewport: {w: L.width, h: L.height}, ...meta};
        if (!TEST) {
          data.captures[shot] = {...(data.captures[shot] || {wide: null, tall: null}), [layout]: entry};
          fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2) + '\n');
        }
        console.log('  ', JSON.stringify(entry));
      } finally {
        await browser.close();
      }
    }
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
