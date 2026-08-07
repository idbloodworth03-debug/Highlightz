/**
 * Render scripts/og_card.html to the social preview PNG.
 *
 *     node scripts/build_og_card.mjs
 *
 * WHY A RENDER AND NOT A HAND-DRAWN IMAGE. The card has to match the landing
 * page, and the only way to guarantee that is to build it from the same
 * woff2 files and the same palette tokens the page ships. Drawing it by hand
 * is how the last one ended up in a design the site had already abandoned.
 *
 * WHY THE OUTPUT FILENAME IS VERSIONED. Facebook, Instagram and LinkedIn cache
 * OG images keyed on the URL, and they hold that cache for a long time —
 * overwriting the bytes at the old path does NOT reliably refresh the preview
 * anyone sees. Changing the card therefore means changing the filename and the
 * og:image/twitter:image tags together. Bump OUT below when the card changes.
 *
 * Requires playwright + a chromium at PLAYWRIGHT_CHROMIUM (defaults to the
 * image's preinstalled one). Both are build-time only: the server never
 * imports either, and deploys do not run pip/npm install.
 */
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, extname } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = join(HERE, '..', 'src', 'dashboard', 'static');
const OUT = join(STATIC, 'og-card-v2.png');
// The retired path is overwritten with the SAME artwork. It stays on disk
// because links shared before the rename still point at it — deleting it turns
// every one of those posts into a broken image. Rewriting it means that if a
// crawler ever does revalidate an old share, it picks up the corrected card
// instead of re-serving the "7 days free / $15 a month" one.
const LEGACY = join(STATIC, 'og-card.png');

const TYPES = { '.woff2': 'font/woff2', '.png': 'image/png',
                '.html': 'text/html; charset=utf-8', '.jpg': 'image/jpeg' };

// The card loads fonts, and fonts over file:// hit opaque-origin rules in
// Chromium — so it is served over real HTTP, rooted at the static dir so the
// /fonts/... and /logo-mark.png paths resolve exactly as they do in prod.
const server = createServer(async (req, res) => {
  const path = req.url === '/' ? '/og_card.html' : req.url.split('?')[0];
  const file = path === '/og_card.html' ? join(HERE, 'og_card.html')
                                        : join(STATIC, path);
  try {
    const buf = await readFile(file);
    res.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream' });
    res.end(buf);
  } catch {
    res.writeHead(404).end('nope');
  }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM || '/opt/pw-browsers/chromium',
});
// deviceScaleFactor 2 then downscale: the mark and the mono figures are small
// enough at 1200x630 that 1x sampling visibly crunches them.
const page = await browser.newPage({
  viewport: { width: 1200, height: 630 }, deviceScaleFactor: 2,
});
await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);

// A card that renders in a fallback face is worse than no card, and it fails
// silently — so prove every self-hosted family actually loaded before shooting.
const missing = await page.evaluate(() =>
  ['Lobster', 'Sora', 'Plex'].filter(f => !document.fonts.check(`16px "${f}"`)));
if (missing.length) throw new Error(`fonts did not load: ${missing.join(', ')}`);

await page.screenshot({ path: OUT, scale: 'css' });
await browser.close();
server.close();

await copyFile(OUT, LEGACY);
console.log('wrote', OUT, '\nwrote', LEGACY, '(retired path, same artwork)');
