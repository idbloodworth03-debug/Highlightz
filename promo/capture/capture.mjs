#!/usr/bin/env node
// Records the real UI shots of the promo from the live app.
//
//   HZ_DEMO_PASSWORD=... npm run capture -- \
//        [--shots landing,live,campaign] [--layouts wide,tall]
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
import {centerOf, docTop, fr, keys, launch, PROMO, record, SCROLLER, TL, warm} from './rig.mjs';
import {demoSession} from './auth.mjs';
import {liveWindow} from './live-window.mjs';

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
// The demo password only ever goes to production, never to a --test server.
const SESSION = args.session || (TEST ? '' : await demoSession(PROD));
const SHOTS = String(args.shots || 'landing,live,campaign').split(',');
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

// Every signed-in shot: the dashboard in the colours of the platform it
// shows (Twitch purple; Kick green for a Kick channel's live scores, since
// Live Streams lists one platform at a time; a per-browser setting, not the
// account's), the sidebar as a clipper's account shows it (the admin-only
// Training and Landing Page tools are taken out; they carry no marker, so
// they go by label), and no toasts.
const dashboardInit = (platform) => `(() => {
  try { localStorage.setItem('hz_platform', ${JSON.stringify(platform)}); } catch (e) {}
  const ADMIN_TABS = ['Training', 'Landing Page'];
  const hide = () => {
    // The account's own notices (another tab's actions, a stream it stopped)
    // are not part of the shot: toasts stay out of frame.
    if (document.head && !document.getElementById('__cap-css')) {
      const st = document.createElement('style');
      st.id = '__cap-css';
      st.textContent = '.rd-toast{display:none!important}';
      document.head.append(st);
    }
    for (const b of document.querySelectorAll('.rd-navitem')) {
      if (ADMIN_TABS.includes((b.innerText || '').trim()) && b.style.display !== 'none') b.style.display = 'none';
    }
  };
  new MutationObserver(hide).observe(document, {childList: true, subtree: true});
})();`;

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

  // The real Live Streams screen, fed the real score_update messages that
  // record-live.mjs recorded from a live channel, at real speed: the stretch
  // where the score climbs most (live-window.mjs, shared with the panel's
  // trace). Earlier messages fill the chart's history before the first frame.
  async live({page, layout, out, rec}) {
    const frames = shotAt('bot.clip2', 'bot') - shotAt('bot.live', 'bot');
    const w = liveWindow(rec, frames, TL.fps);
    const plan = w.msgs.map((m) => ({f: w.frameOf(m.t), msg: m.msg}));
    const early = plan.filter((p) => p.f < 0);
    const due = plan.filter((p) => p.f >= 0 && p.f < frames);
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await warm(page, {selector: '.rd-streampick'});
    // The detail panel shows the selected channel: select the recorded one
    // (the list opens on the first channel, alphabetically).
    await page.locator('.rd-streampick', {hasText: rec.channel}).first().click();
    await page.waitForTimeout(800);
    if (layout === 'tall') {
      // On a phone the detail panel sits below the list: bring it up.
      const y = await docTop(page, '.rd-detail', 64);
      await page.evaluate(({y, SCROLLER}) => { (0, eval)(SCROLLER).scrollTop = y; }, {y, SCROLLER});
    }
    for (const p of early.slice(-40)) await page.__ws.send(p.msg);
    await page.waitForTimeout(800);
    let k = 0;
    await record({page, frames, out, perFrame: async (i) => {
      while (k < due.length && due[k].f <= i) await page.__ws.send(due[k++].msg);
      if (k) await page.waitForTimeout(15);
    }});
    return {focus: (await centerOf(page, '.rd-detail'))?.rect};
  },

  // The campaign's real page: the live one, or else the next to open. Its
  // numbers count up on the real elements in time with the edit (the prize
  // pool on the coin, the clippers joined) and, once a campaign is live, its
  // leaderboard rows slide into rank order. The scroll visits each part as
  // the edit calls it out: on a desktop it all fits once past the banner; on
  // a phone the pool and the caption tags sit far below the title.
  async campaign({page, layout, out}) {
    const frames = sceneLen('campaign');
    const tPool = shotAt('campaign.pool', 'campaign');
    const tTags = shotAt('campaign.tags', 'campaign');
    const tCounts = shotAt('campaign.counts', 'campaign');
    const tBoard = shotAt('campaign.board', 'campaign');
    const tGlow = shotAt('campaign.glow', 'campaign');
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
    await warm(page, {selector: '.cmp-card'});
    await page.addStyleTag({content: HIDE_ADMIN_CSS});
    const live = page.locator('.cmp-card', {has: page.locator('.cmp-status.live')});
    const card = (await live.count()) ? live.first() : page.locator('.cmp-card', {has: page.locator('.cmp-status.upcoming')}).first();
    await card.click();
    await warm(page, {selector: '.cmp-page'});
    await maskNames(page);
    const y = async (sel, above) => (await docTop(page, sel, above)) ?? 0;
    let K;
    let at;                                   // frame each number counts up on the page
    if (layout === 'tall') {
      const yTitle = await y('.cmp-cols', 70);
      const yPool = await y('.cmp-box', 90);
      const yTag = await y('.cmp-chip.tag', 420);
      K = [[0, 0], [rel(1), 0], [rel(2), yTitle], [rel(5), yTitle], [rel(6.5), yPool], [rel(9), yPool], [rel(10), yTag], [frames, yTag + 24]];
      at = {pool: rel(6.5), count: rel(3), tags: rel(10), board: tBoard, glow: tGlow};
    } else {
      const yCols = await y('.cmp-cols', 40);
      K = [[0, 100], [rel(1), 100], [rel(3), yCols], [frames, yCols + 40]];
      at = {pool: tPool, count: tCounts, tags: tTags, board: tBoard, glow: tGlow};
    }
    // The real values, read off the page before anything is animated.
    const real = await page.evaluate(() => ({
      pool: document.querySelector('.cmp-box .cmp-big')?.textContent || '',
      count: document.querySelector('.cmp-count-n')?.textContent || '',
      scores: [...document.querySelectorAll('.cmp-row .cmp-score')].map((s) => s.firstChild?.textContent || ''),
    }));
    const num = (s) => Number(String(s).replace(/[^0-9.]/g, '')) || 0;
    await record({page, frames, out, perFrame: async (i) => {
      await page.evaluate(({y, i, at, pool, count, scores, SCROLLER}) => {
        (0, eval)(SCROLLER).scrollTop = y;
        const e = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : 1 - Math.pow(1 - t, 3));
        const fmt = (v, like) => (like.trim().startsWith('$') ? '$' : '') + Math.round(v).toLocaleString('en-US');
        const big = document.querySelector('.cmp-box .cmp-big');
        if (big && pool.s) big.textContent = fmt(pool.n * e((i - at.pool) / 22), pool.s);
        const cn = document.querySelector('.cmp-count-n');
        if (cn && count.s) cn.textContent = fmt(count.n * e((i - at.count) / 16), count.s);
        for (const t of document.querySelectorAll('.cmp-chip.tag')) {
          const g = e((i - at.tags) / 10) * (1 - e((i - at.tags - 30) / 40) * 0.5);
          t.style.boxShadow = `0 0 ${g * 22}px rgba(243,67,254,${g * 0.6})`;
        }
        document.querySelectorAll('.cmp-row').forEach((row, r) => {
          const p = e((i - at.board - r * 6) / 14);
          row.style.opacity = String(p);
          row.style.transform = `translateX(${(1 - p) * 60}px)`;
          const sc = row.querySelector('.cmp-score');
          if (sc && sc.firstChild && scores[r]) sc.firstChild.textContent = Math.round(scores[r].n * e((i - at.board - r * 6) / 22)).toLocaleString('en-US');
          if (row.classList.contains('top')) {
            const g = e((i - at.glow - r * 3) / 12);
            row.style.boxShadow = `0 0 ${g * 26}px rgba(243,67,254,${g * 0.55}), inset 0 0 0 1px rgba(243,67,254,${g * 0.5})`;
          }
        });
      }, {y: keys(K, i), i, at, pool: {n: num(real.pool), s: real.pool}, count: {n: num(real.count), s: real.count}, scores: real.scores.map((s) => ({n: num(s)})), SCROLLER});
    }});
    return {focus: (await centerOf(page, '.cmp-rows'))?.rect || (await centerOf(page, '.cmp-cols'))?.rect};
  },
};

const ROUTE = {landing: null, live: 'streams', campaign: 'campaigns'};
const NEEDS_SESSION = {landing: false, live: true, campaign: true};
const NEEDS_REC = {live: true};

async function main() {
  const recPath = path.join(PROMO, 'capture', 'recordings', 'live.json');
  const rec = fs.existsSync(recPath) ? JSON.parse(fs.readFileSync(recPath, 'utf8')) : null;
  for (const shot of SHOTS) {
    if (NEEDS_SESSION[shot] && !SESSION) { console.log(`skip ${shot}: needs the demo account (HZ_DEMO_PASSWORD or HZ_SESSION)`); continue; }
    if (NEEDS_REC[shot] && !rec) { console.log(`skip ${shot}: run capture/record-live.mjs first`); continue; }
    for (const layout of LAYOUTS) {
      const {browser, page} = await launch({layout, base: BASE, session: NEEDS_SESSION[shot] ? SESSION : '', route: ROUTE[shot], extraInit: NEEDS_SESSION[shot] ? dashboardInit(shot === 'live' && rec && rec.platform === 'kick' ? 'kick' : 'twitch') : undefined});
      if (shot === 'live') {
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
          // Read it again: fetch-data.mjs may have written it during the take.
          const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
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
