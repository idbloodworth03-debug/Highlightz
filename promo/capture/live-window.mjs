// Which stretch of a live recording the Live Streams shot replays, shared by
// capture.mjs (which plays it into the real screen) and fetch-data.mjs (which
// turns the same stretch into the score panel's trace), so the two always
// show the same seconds. Real speed: one second of the shot is one second
// of the stream. The stretch is the one where the score climbs the most, the
// part of a live channel worth watching.
export function liveWindow(rec, frames, fps) {
  const msgs = rec.messages.filter((m) => m.msg.event === 'score_update' && String(m.msg.channel).toLowerCase() === rec.channel);
  const span = frames / fps;
  const first = msgs[0].t;
  const last = msgs[msgs.length - 1].t;
  const at = (t) => {
    let s = msgs[0].msg.score;
    for (const m of msgs) { if (m.t > t) break; s = m.msg.score; }
    return s;
  };
  let best = Math.max(first, last - span);
  let bestRise = -Infinity;
  for (let t0 = first + 5; t0 + span <= last; t0 += 0.5) {
    const rise = at(t0 + span) - at(t0);
    if (rise > bestRise) { bestRise = rise; best = t0; }
  }
  return {msgs, t0: best, span, frameOf: (t) => Math.round((t - best) * fps), timeOf: (f) => best + f / fps, scoreAt: at};
}
