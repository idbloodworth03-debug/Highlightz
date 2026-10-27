#!/usr/bin/env node
// The gate in front of `npm run render:all`: the release cuts are made only
// when every shot, number and clip in the video has a real source. Until then
// `npm run render:16x9` (and friends) still make the labelled preview.
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const PROMO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const require = createRequire(import.meta.url);
const {missingFrom} = require('../src/missing.js');
const data = JSON.parse(fs.readFileSync(path.join(PROMO, 'src', 'data', 'promo-data.json'), 'utf8'));
const m = missingFrom(data);
const absent = [];
for (const pair of Object.values(data.captures)) {
  for (const c of Object.values(pair || {})) if (c && !fs.existsSync(path.join(PROMO, 'public', c.file))) absent.push(c.file);
}
for (const c of data.featured_clips) if (!fs.existsSync(path.join(PROMO, 'public', c.file))) absent.push(c.file);
if (m.length || absent.length) {
  console.error('Not rendering the release cuts: the video still has no real source for');
  for (const line of m) console.error('  - ' + line);
  for (const f of absent) console.error('  - public/' + f + ' (in the data file, not on disk)');
  console.error('See README.md, "Getting the real shots". The preview renders still work: npm run render:16x9');
  process.exit(1);
}
console.log('every shot has a real source');
