#!/usr/bin/env node
// Fills src/data/promo-data.json with the real numbers the video shows, read
// from the live app, and downloads the featured clips:
//
//   node capture/fetch-data.mjs --session <demo cookie> [--clips 3]
//
//   budget_range    the min and max the live /promote calculator accepts
//   clips_total     the landing page's clip counter (/landing/stats)
//   featured_clips  the clips the owner featured on the landing page
//                   (/landing/showcase, which already leaves out every
//                   channel that opted out), downloaded from Twitch with
//                   streamlink to public/captures/clip-N.mp4
//   campaign        the campaign that is live right now (/campaigns, as the
//                   demo account sees it: it must have joined, or the page
//                   is locked and has no leaderboard to show)
//   signal_trace    the score and signals per frame of the live shot, from
//                   capture/recordings/live.json (record-live.mjs)
//
// Nothing here is typed by hand, and an empty source stays null so the
// preview keeps its placeholder rather than showing a guess.
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fr, PROMO, TL} from './rig.mjs';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
    return acc;
  }, []),
);
const BASE = 'https://highlightz.app';
const SESSION = args.session || process.env.HZ_SESSION || '';
const N_CLIPS = Math.max(2, Math.min(3, Number(args.clips || 3)));
const DATA_FILE = path.join(PROMO, 'src', 'data', 'promo-data.json');
const CAPTURES = path.join(PROMO, 'public', 'captures');
const SIGNALS = ['CHAT_VELOCITY', 'KEYWORD', 'EMOTE_HOMOGENEITY', 'SENTIMENT', 'AUDIO_SPIKE', 'VIEWER_SPIKE', 'SILENCE_BURST'];

const get = async (p, {json = true, auth = false} = {}) => {
  const res = await fetch(BASE + p, {headers: auth && SESSION ? {cookie: 'session=' + SESSION} : {}, redirect: 'manual'});
  if (res.status !== 200) throw new Error(`${p}: HTTP ${res.status}`);
  return json ? res.json() : res.text();
};

async function budgetRange() {
  const html = await get('/promote', {json: false});
  const m = html.match(/id="pc"[^>]*data-min="(\d+)"[^>]*data-max="(\d+)"/);
  if (!m) throw new Error('/promote: the fee calculator (#pc data-min/data-max) was not found');
  return {min: Number(m[1]) / 100, max: Number(m[2]) / 100, source: 'live /promote fee calculator', verified_live: true};
}

// Where the moment is: the loudest 1.2 s of the clip's sound (a clip is
// shown for about a second, muted, so it should open on the reaction).
function peakStart(file) {
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', file, '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], {maxBuffer: 1 << 28});
  const s = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length >> 1);
  const win = 2000;                                  // 0.25 s at 8 kHz
  const rms = [];
  for (let i = 0; i + win <= s.length; i += win) {
    let e = 0;
    for (let j = i; j < i + win; j++) e += s[j] * s[j];
    rms.push(Math.sqrt(e / win));
  }
  let best = 0;
  let bestE = -1;
  for (let i = 0; i + 5 <= rms.length; i++) {
    const e = rms[i] + rms[i + 1] + rms[i + 2] + rms[i + 3] + rms[i + 4];
    if (e > bestE) { bestE = e; best = i; }
  }
  return Math.max(0, +(best * 0.25 - 0.4).toFixed(2));
}

async function featuredClips() {
  const {clips} = await get('/landing/showcase');
  // One clip per channel, so the edit shows more than one streamer.
  const seen = new Set();
  const picked = (clips || []).filter((c) => c.twitch_url && c.channel && !seen.has(c.channel) && seen.add(c.channel)).slice(0, N_CLIPS);
  fs.mkdirSync(CAPTURES, {recursive: true});
  const out = [];
  for (const [i, c] of picked.entries()) {
    const file = `clip-${i + 1}.mp4`;
    const dest = path.join(CAPTURES, file);
    console.log(`  clip ${i + 1}: ${c.channel} · ${c.clip_title}`);
    execFileSync('streamlink', ['--force', '-o', dest, c.twitch_url, 'best'], {stdio: 'inherit'});
    out.push({channel: c.channel, platform: 'twitch', file: 'captures/' + file, start_s: peakStart(dest), title: c.clip_title});
  }
  return out;
}

async function liveCampaign() {
  const {rows} = await get('/campaigns', {auth: true});
  const c = (rows || []).find((r) => r.status === 'live' && r.published !== false);
  if (!c) return null;
  if (c.locked || !c.board) throw new Error(`campaign "${c.title}" is locked for the demo account: join it in the dashboard first`);
  const b = c.board;
  // The board carries the top ten. Their entries and views are the whole
  // campaign's only when every entrant is on it; otherwise the edit says so.
  const complete = b.rows.length >= b.entrants;
  return {
    title: c.title, summary: c.summary || '', status: c.status,
    prize_pool: Number(c.prize_pool), payouts: (c.payouts || []).map(Number),
    hashtags: c.hashtags || [], mentions: c.mentions || [], platforms: c.platforms || [],
    streamers: (c.streamers || []).map((s) => ({name: s.name, platform: s.platform})),
    start_at: c.start_at, end_at: c.end_at, judging_text: c.judging_text || '',
    joined_count: c.joined_count || 0, entrants: b.entrants, metric: b.metric,
    entries: b.rows.reduce((n, r) => n + (r.clips || 0), 0),
    total_views: b.rows.reduce((n, r) => n + (r.score || 0), 0),
    counts_scope: complete ? 'all' : 'top10',
    board: b.rows.map((r) => ({rank: r.rank, name: r.name, clips: r.clips, score: r.score})),
  };
}

// The same time compression capture.mjs applies to the live shot, so the
// panel's number and the real screen behind it move together.
function signalTrace() {
  const recPath = path.join(PROMO, 'capture', 'recordings', 'live.json');
  if (!fs.existsSync(recPath)) return null;
  const rec = JSON.parse(fs.readFileSync(recPath, 'utf8'));
  const msgs = rec.messages.filter((m) => m.msg.event === 'score_update');
  const bot = TL.promo.scenes.find((s) => s.id === 'bot');
  const crossF = fr(TL.promo.shots['bot.cross']) - fr(bot.from);
  const frames = crossF + 64;
  const window_s = Math.min(40, rec.cross_t - msgs[0].t);
  const timeAt = (f) => (f <= crossF ? rec.cross_t - ((crossF - f) / crossF) * window_s : rec.cross_t + (f - crossF) / TL.fps);
  const at = (t) => {
    let last = msgs[0];
    for (const m of msgs) { if (m.t > t) break; last = m; }
    return last.msg;
  };
  const score = [];
  const raw = Object.fromEntries(SIGNALS.map((k) => [k, []]));
  for (let f = 0; f < frames; f++) {
    const m = at(timeAt(f));
    score.push(Number(m.score));
    for (const k of SIGNALS) raw[k].push(Math.max(0, Number((m.breakdown || {})[k]) || 0));
  }
  const peak = Math.max(1e-9, ...SIGNALS.flatMap((k) => raw[k]));
  const signals = Object.fromEntries(SIGNALS.map((k) => [k, raw[k].map((v) => +(v / peak).toFixed(3))]));
  return {threshold: rec.threshold, score: score.map((v) => +v.toFixed(2)), signals, channel: rec.channel};
}

async function main() {
  const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
  let got = 0;
  const step = async (name, fn, apply) => {
    try {
      const v = await fn();
      apply(v);
      got++;
      console.log(`ok   ${name}`);
    } catch (e) {
      console.log(`skip ${name}: ${e.cause?.message || e.message}`);
    }
  };
  await step('budget range', budgetRange, (v) => { data.budget_range = v; });
  await step('clip counter', async () => (await get('/landing/stats')).clips_total, (v) => { data.clips_total = typeof v === 'number' ? v : null; });
  await step('featured clips', featuredClips, (v) => { data.featured_clips = v; });
  if (SESSION) {
    await step('live campaign', liveCampaign, (v) => {
      // Who funds it is not in the API; carried over from what the owner said.
      data.campaign = v ? {...v, funded_by: data.campaign?.funded_by ?? (args['funded-by'] || undefined)} : null;
    });
  } else {
    console.log('skip live campaign: needs --session (the demo account, joined to the campaign)');
  }
  await step('signal trace', async () => {
    const t = signalTrace();
    if (!t) throw new Error('no capture/recordings/live.json yet (record-live.mjs)');
    return t;
  }, (v) => { data.signal_trace = v; });
  if (!got) {
    console.log('nothing fetched; the data file is unchanged');
    process.exit(1);
  }
  data.fetched_at = new Date().toISOString();
  data.source = BASE;
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2) + '\n');
  console.log(`wrote ${path.relative(PROMO, DATA_FILE)}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
