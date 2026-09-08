// Render ad.html to 900 PNG frames (30 s at 30 fps, 1080x1920).
//   node render.js [fps] [start] [end]
const { chromium } = require('/home/user/Highlightz/node_modules/playwright');
const fs = require('fs'); const path = require('path');
const STATIC = '/home/user/Highlightz/src/dashboard/static';
const HTML = fs.readFileSync(__dirname + '/ad.html', 'utf8');
const FPS = +(process.argv[2] || 30), T0 = +(process.argv[3] || 0), T1 = +(process.argv[4] || 30);
const OUT = __dirname + '/frames'; fs.mkdirSync(OUT, { recursive: true });
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-proxy-server', '--disable-background-networking', '--font-render-hinting=none'] });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  page.on('pageerror', e => console.log('PAGE ERROR', String(e).slice(0, 200)));
  await page.route('**/*', r => { const u = new URL(r.request().url()); const p = u.pathname;
    if (p === '/') return r.fulfill({ contentType: 'text/html', body: HTML });
    if (p.startsWith('/static/')) { const f = path.join(STATIC, p.replace('/static/', '')); if (fs.existsSync(f)) return r.fulfill({ contentType: f.endsWith('.woff2') ? 'font/woff2' : f.endsWith('.png') ? 'image/png' : f.endsWith('.webp') ? 'image/webp' : 'application/octet-stream', body: fs.readFileSync(f) }); }
    return r.fulfill({ status: 404, body: '' }); });
  await page.goto('http://ad.test/', { waitUntil: 'load' });
  await page.evaluate(() => window.ready);
  const n0 = Math.round(T0 * FPS), n1 = Math.round(T1 * FPS);
  const t0 = Date.now();
  for (let f = n0; f < n1; f++) {
    await page.evaluate(t => window.seek(t), f / FPS);
    await page.screenshot({ path: `${OUT}/f${String(f).padStart(4, '0')}.jpg`, type: 'jpeg', quality: 95, clip: { x: 0, y: 0, width: 1080, height: 1920 }, animations: 'disabled', caret: 'hide' });
    if (f % 150 === 0) console.log('frame', f, ((Date.now() - t0) / 1000).toFixed(0) + 's');
  }
  console.log('done', n1 - n0, 'frames in', ((Date.now() - t0) / 1000).toFixed(0) + 's');
  await browser.close();
})();
