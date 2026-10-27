#!/usr/bin/env node
// Records the real WebSocket traffic of a live channel the demo account is
// monitoring, until the bot captures a clip on it:
//
//   HZ_DEMO_PASSWORD=... node capture/record-live.mjs --channel <twitch login> [--add] [--max-min 45]
//
// Start monitoring the channel in the dashboard first (Live Streams → Monitor
// stream), or pass --add to have the script start it. Otherwise it only listens: it opens the signed-in dashboard on
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
import {demoSession} from './auth.mjs';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
    return acc;
  }, []),
);
const BASE = 'https://highlightz.app';
const SESSION = args.session || await demoSession(BASE);
const CHANNEL = String(args.channel || '').toLowerCase();
const MAX_MS = Number(args['max-min'] || 45) * 60 * 1000;
const OUT = path.join(PROMO, 'capture', 'recordings', 'live.json');
if (!SESSION || !CHANNEL) {
  console.error('usage: HZ_DEMO_PASSWORD=... node capture/record-live.mjs --channel <login> [--max-min 45]');
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
      // The server stops a channel it can never clip (no Twitch authorisation
      // on the account, clipping restricted by the broadcaster): no clip is
      // coming, so stop listening and say so.
      if (msg.event === 'stream_removed') {
        console.error(`\nthe server stopped monitoring ${CHANNEL} (stream_removed): the account cannot clip it. Is Twitch connected in Settings?`);
        done('removed');
        return;
      }
      if (msg.event === 'score_update' || msg.event === 'clip_ready') {
        messages.push({t: (Date.now() - t0) / 1000, msg});
        if (msg.event === 'score_update') process.stdout.write(`  ${CHANNEL} score ${Number(msg.score).toFixed(1)} (${messages.length} msgs)\r`);
        if (msg.event === 'clip_ready') done('clip');
      }
    });
  });
  await page.goto(BASE + '/', {waitUntil: 'domcontentloaded'});
  if (args.add) {
    // Start monitoring the channel from the signed-in page, as Live Streams'
    // Monitor stream button does (201 added, 409 already monitored).
    const status = await page.evaluate(async (channel) => (await fetch('/streams', {
      method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({channel, platform: 'twitch'}),
    })).status, CHANNEL);
    console.log(`monitor ${CHANNEL}: HTTP ${status}`);
    if (status !== 201 && status !== 409) throw new Error('could not start monitoring ' + CHANNEL);
  }
  console.log(`listening for ${CHANNEL} (stops at its first captured clip, or after ${MAX_MS / 60000} min)`);
  const why = await Promise.race([finished, new Promise((r) => setTimeout(() => r('timeout'), MAX_MS))]);
  await browser.close();
  if (why === 'removed') process.exit(1);
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
