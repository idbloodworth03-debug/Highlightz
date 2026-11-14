// Stills of an ad at given times, for checking the layout without a full
// render:   node ad/stills.mjs 0.3 1.2 9.5 ...        -> out/ad-stills/t<sec>.png
//           AD=GtaAd node ad/stills.mjs 0.5 6 8 ...   -> out/GtaAd-stills/t<sec>.png
import path from 'node:path';
import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const id = process.env.AD || 'DuxAd';
const times = process.argv.slice(2).map(Number);
const out = path.join(root, 'out', id === 'DuxAd' ? 'ad-stills' : `${id}-stills`);
fs.mkdirSync(out, {recursive: true});

// This machine's Playwright headless shell, whichever build (see remotion.config.ts).
const findHeadless = () => {
  const base = '/opt/pw-browsers';
  if (!fs.existsSync(base)) return undefined;
  for (const dir of fs.readdirSync(base).filter((d) => d.startsWith('chromium_headless_shell-')).sort().reverse()) {
    for (const rel of ['chrome-headless-shell-linux64/chrome-headless-shell', 'chrome-linux/headless_shell']) {
      const exe = path.join(base, dir, rel);
      if (fs.existsSync(exe)) return exe;
    }
  }
  return undefined;
};
const browserExecutable = findHeadless();

const serveUrl = await bundle({entryPoint: path.join(root, 'src', 'index.ts')});
const composition = await selectComposition({serveUrl, id, browserExecutable});
for (const t of times) {
  const frame = Math.min(composition.durationInFrames - 1, Math.round(t * composition.fps));
  const output = path.join(out, `t${t.toFixed(2)}.png`);
  await renderStill({serveUrl, composition, frame, output, browserExecutable, chromiumOptions: {gl: 'swiftshader'}});
  console.log(output);
}
