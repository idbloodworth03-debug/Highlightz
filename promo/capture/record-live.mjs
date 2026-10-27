#!/usr/bin/env node
// Records the real WebSocket traffic of a live channel the demo account is
// monitoring, until the bot captures a clip on it:
//
//   node capture/record-live.mjs --session <demo cookie> --channel <twitch login> [--max-min 45]
//
// Start monitoring the channel in the dashboard first (Live Streams → Monitor
// stream). This script only listens: it opens the signed-in dashboard on
// https://highlightz.app, keeps every score_update for the channel and the
// clip_ready that ends it, and writes capture/recordings/live.json:
//
//   {channel, threshold, messages: [{t, msg}], cross_t, recorded_at}
//
// t is seconds since the recording started; cross_t is the score_update where
// the score last rose through the threshold before the clip. capture.mjs
// replays these messages into the real Live Streams and Clip Review screens,
// and fetch-data.mjs derives the score trace the edit's signal panel shows.
import fs from 'node:fs';
import path from 'node:path';
import {launch, PROMO} from './rig.mjs';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
    return acc;
  }, []),
);
const BASE = 'https://highlightz.app';
const SESSION = args.session || process.env.HZ_SESSION || '';
const CHANNEL = String(args.channel || '').toLowerCase();
const MAX_MS = Number(args['max-min'] || 45) * 60 * 1000;
const OUT = path.join(PROMO, 'capture', 'recordings', 'live.json');
if (!SESSION || !CHANNEL) {
  console.error('usage: node capture/record-live.mjs --session <demo cookie> --channel <login> [--max-min 45]');
  process.exit(2);
}

const threshold = (msg) => {
  const b = msg.breakdown || {};
  for (const k of ['_threshold', 'threshold', '_thr']) if (typeof b[k] === 'number') return b[k];
  return null;
};

async function main() {
  const {browser, page} = await launch({layout: 'wide', base: BASE, session: SESSION, route: 'streams'});
  const t0 = Date.now();
  const messages = [];
  let done = null;
  const finished = new Promise((r) => { done = r; });
  page.on('websocket', (ws) => {
    if (!/\/ws$/.test(new URL(ws.url()).pathname)) return;
    ws.on('framereceived', ({payload}) => {
      let msg;
      try { msg = JSON.parse(String(payload)); } catch (e) { return; }
      const ch = String(msg.channel || (msg.clip && msg.clip.channel) || '').toLowerCase();
      if (ch !== CHANNEL) return;
      if (msg.event === 'score_update' || msg.event === 'clip_ready') {
        messages.push({t: (Date.now() - t0) / 1000, msg});
        if (msg.event === 'score_update') process.stdout.write(`  ${CHANNEL} score ${Number(msg.score).toFixed(1)} (${messages.length} msgs)\r`);
        if (msg.event === 'clip_ready') done('clip');
      }
    });
  });
  await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
  console.log(`listening for ${CHANNEL} (stops at its first captured clip, or after ${MAX_MS / 60000} min)`);
  const why = await Promise.race([finished, new Promise((r) => setTimeout(() => r('timeout'), MAX_MS))]);
  await browser.close();
  if (why !== 'clip') {
    console.error(`\nno clip was captured on ${CHANNEL} in ${MAX_MS / 60000} min; nothing written. Try a busier channel or a longer --max-min.`);
    process.exit(1);
  }
  const scores = messages.filter((m) => m.msg.event === 'score_update');
  const clip = messages.find((m) => m.msg.event === 'clip_ready');
  const thr = scores.map((m) => threshold(m.msg)).filter((v) => v !== null).pop();
  if (thr == null) throw new Error('score_update carried no threshold in its breakdown');
  // The crossing: the last rise through the threshold before the clip landed.
  let cross = null;
  for (let i = 1; i < scores.length && scores[i].t <= clip.t; i++) {
    if (scores[i - 1].msg.score < thr && scores[i].msg.score >= thr) cross = scores[i];
  }
  if (!cross) cross = scores.filter((m) => m.t <= clip.t).reduce((a, b) => (b.msg.score > a.msg.score ? b : a), scores[0]);
  fs.mkdirSync(path.dirname(OUT), {recursive: true});
  fs.writeFileSync(OUT, JSON.stringify({channel: CHANNEL, threshold: thr, cross_t: cross.t, recorded_at: new Date().toISOString(), messages}, null, 1) + '\n');
  console.log(`\nwrote ${path.relative(PROMO, OUT)}: ${scores.length} score updates, crossing at ${cross.t.toFixed(1)} s, clip ${clip.msg.clip.id}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
