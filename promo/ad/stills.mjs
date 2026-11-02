// Stills of the Duxxion ad at given times, for checking the layout without a
// full render:  node ad/stills.mjs 0.3 1.2 9.5 ...   -> out/ad-stills/t<sec>.png
import path from 'node:path';
import fs from 'node:fs';
import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const times = process.argv.slice(2).map(Number);
const out = path.join(root, 'out', 'ad-stills');
fs.mkdirSync(out, {recursive: true});
const HEADLESS = '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell';
const browserExecutable = fs.existsSync(HEADLESS) ? HEADLESS : undefined;

const serveUrl = await bundle({entryPoint: path.join(root, 'src', 'index.ts')});
const composition = await selectComposition({serveUrl, id: 'DuxAd', browserExecutable});
for (const t of times) {
  const frame = Math.min(composition.durationInFrames - 1, Math.round(t * composition.fps));
  const output = path.join(out, `t${t.toFixed(2)}.png`);
  await renderStill({serveUrl, composition, frame, output, browserExecutable, chromiumOptions: {gl: 'swiftshader'}});
  console.log(output);
}
