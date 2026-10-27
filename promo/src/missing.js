// What the final video still has no real source for, in words a person can
// act on. Empty = ready to render. CommonJS so the preview (via data.ts) and
// capture/check-data.mjs (the gate in front of `npm run render:all`) share
// one list.
const SHOTS = [
  ['landing', 'landing capture (highlightz.app)'],
  ['live', 'Live Streams capture (a recorded live channel)'],
  ['campaign', 'campaign page capture'],
];

function missingFrom(data) {
  const m = [];
  const both = (n) => !!(data.captures[n] && data.captures[n].wide && data.captures[n].tall);
  for (const [name, words] of SHOTS) if (!both(name)) m.push(words);
  if (!data.signal_trace) m.push('real score trace (capture/record-live.mjs)');
  if (!data.campaign) m.push('live campaign data');
  if (data.featured_clips.length < 2) m.push('2-3 featured clips (real footage)');
  if (data.clips_total === null) m.push('live clip counter');
  if (!data.budget_range.verified_live) m.push('budget range read from the live /promote page');
  return m;
}

module.exports = {missingFrom};
