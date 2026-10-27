#!/usr/bin/env node
// Fills src/data/promo-data.json with the real numbers the video shows, read
// from the live app, and downloads the featured clips:
//
//   HZ_DEMO_PASSWORD=... node capture/fetch-data.mjs [--clips 3] [--funded-by Highlightz]
//
//   budget_range    the min and max the live /promote calculator accepts
//   clips_total     the landing page's clip counter (/landing/stats)
//   featured_clips  the clips the owner featured on the landing page
//                   (/landing/showcase, which already leaves out every
//                   channel that opted out), downloaded from Twitch with
//                   streamlink to public/captures/clip-N.mp4
//   campaign        the campaign that is live, or else the next to open (/campaigns, as the
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
import {demoSession} from './auth.mjs';
import {liveWindow} from './live-window.mjs';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
    return acc;
  }, []),
);
const BASE = 'https://highlightz.app';
const SESSION = args.session || await demoSession(BASE);
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

// Brightness (mean luma, 0-255) and motion (mean luma change from the
// previous sample) of each 0.25 s of the clip.
function pictureStats(file) {
  const out = execFileSync('ffmpeg', ['-v', 'error', '-i', file, '-vf', 'fps=4,scale=64:36,signalstats,metadata=print:file=-', '-f', 'null', '-'], {maxBuffer: 1 << 26}).toString();
  const pick = (k) => [...out.matchAll(new RegExp(`signalstats\\.${k}=([\\d.]+)`, 'g'))].map((m) => Number(m[1]));
  return {luma: pick('YAVG'), motion: pick('YDIF')};
}

const median = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)] || 0;

// Where the moment is: the loudest 1.2 s of the clip's sound (a clip is
// shown for about a second, muted, so it should open on the reaction),
// among the stretches where something is on screen and moving. A streamer
// alt-tabbing to a still, dim desktop can be the loudest part of a clip and
// show nothing; motion is judged against the clip's own (an IRL stream
// moves far less than a game).
function peakStart(file) {
  const {luma, motion} = pictureStats(file);
  const calm = median(motion) * 0.5;
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', file, '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], {maxBuffer: 1 << 28});
  const s = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.length >> 1);
  const win = 2000;                                  // 0.25 s at 8 kHz
  const rms = [];
  for (let i = 0; i + win <= s.length; i += win) {
    let e = 0;
    for (let j = i; j < i + win; j++) e += s[j] * s[j];
    rms.push(Math.sqrt(e / win));
  }
  const loudest = (ok) => {
    let best = -1;
    let bestE = -1;
    for (let i = 0; i + 5 <= rms.length; i++) {
      if (!ok(i)) continue;
      const e = rms.slice(i, i + 5).reduce((x, y) => x + y, 0);
      if (e > bestE) { bestE = e; best = i; }
    }
    return best;
  };
  const span = (a, i) => a.slice(i, i + 5);
  let best = loudest((i) => Math.min(...span(luma, i)) >= 30 && median(span(motion, i)) >= calm);
  if (best < 0) best = loudest(() => true);
  return Math.max(0, +(best * 0.25 - 0.4).toFixed(2));
}

const PICKS = JSON.parse(fs.readFileSync(path.join(PROMO, 'capture', 'clip-picks.json'), 'utf8'));
const slugOf = (url) => String(url).split('/').filter(Boolean).pop();

async function featuredClips() {
  const {clips} = await get('/landing/showcase');
  // One clip per channel, so the edit shows more than one streamer, in the
  // order capture/clip-picks.json gives.
  const seen = new Set();
  const rank = (ch) => { const i = PICKS.order.indexOf(ch); return i < 0 ? PICKS.order.length : i; };
  const picked = (clips || [])
    .filter((c) => c.twitch_url && c.channel && !seen.has(c.channel) && seen.add(c.channel))
    .map((c, i) => ({c, i}))
    .sort((a, b) => rank(a.c.channel) - rank(b.c.channel) || a.i - b.i)
    .map(({c}) => c)
    .slice(0, N_CLIPS);
  fs.mkdirSync(CAPTURES, {recursive: true});
  const out = [];
  for (const [i, c] of picked.entries()) {
    const file = `clip-${i + 1}.mp4`;
    const dest = path.join(CAPTURES, file);
    console.log(`  clip ${i + 1}: ${c.channel} · ${c.clip_title}`);
    execFileSync('streamlink', ['--force', '-o', dest, c.twitch_url, 'best'], {stdio: 'inherit'});
    const pinned = PICKS.inpoints[slugOf(c.twitch_url)];
    out.push({channel: c.channel, platform: 'twitch', file: 'captures/' + file, start_s: pinned ? pinned.start_s : peakStart(dest), title: c.clip_title});
  }
  return out;
}

// The campaign the video shows: the live one, or else the next to open
// (an upcoming campaign has no entries or leaderboard yet, and the edit
// shows its opening date and joined count instead).
async function currentCampaign() {
  const {rows} = await get('/campaigns', {auth: true});
  const open = (rows || []).filter((r) => r.published !== false);
  const c = open.find((r) => r.status === 'live') || open.find((r) => r.status === 'upcoming');
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

// The score and signals per frame of the live shot, from the same stretch
// of the recording that capture.mjs plays into the real screen
// (live-window.mjs), so the panel and the screen behind it agree.
function signalTrace() {
  const recPath = path.join(PROMO, 'capture', 'recordings', 'live.json');
  if (!fs.existsSync(recPath)) return null;
  const rec = JSON.parse(fs.readFileSync(recPath, 'utf8'));
  const frames = fr(TL.promo.shots['bot.clip2']) - fr(TL.promo.shots['bot.live']);
  const w = liveWindow(rec, frames, TL.fps);
  const at = (t) => {
    let last = w.msgs[0];
    for (const m of w.msgs) { if (m.t > t) break; last = m; }
    return last.msg;
  };
  const score = [];
  const raw = Object.fromEntries(SIGNALS.map((k) => [k, []]));
  for (let f = 0; f < frames; f++) {
    const m = at(w.timeOf(f));
    score.push(Number(m.score));
    for (const k of SIGNALS) raw[k].push(Math.max(0, Number((m.breakdown || {})[k]) || 0));
  }
  const peak = Math.max(1e-9, ...SIGNALS.flatMap((k) => raw[k]));
  const signals = Object.fromEntries(SIGNALS.map((k) => [k, raw[k].map((v) => +(v / peak).toFixed(3))]));
  return {threshold: rec.threshold, score: score.map((v) => +v.toFixed(2)), signals, channel: rec.channel, platform: rec.platform || 'twitch'};
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
    await step('campaign', currentCampaign, (v) => {
      // Who funds it is not in the API; carried over from what the owner said.
      data.campaign = v ? {...v, funded_by: data.campaign?.funded_by ?? (args['funded-by'] || undefined)} : null;
    });
  } else {
    console.log('skip campaign: needs the demo account (HZ_DEMO_PASSWORD or HZ_SESSION), joined to the campaign');
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
