// Virtual time for frame-exact screen capture. Injected before any page script
// (Playwright addInitScript), in the top-level document of the app's own origin
// only: a cross-origin embed keeps its own clock.
//
// Until __vt.start() the page runs normally: real timers, real clocks, so it
// can load, fetch and settle. After start(), time only moves when the capture
// rig calls __vt.step(ms): every setTimeout/setInterval due in that window
// fires in order, requestAnimationFrame runs once, Date and performance.now
// read the virtual clock, every CSS animation/transition and Web Animation is
// seeked to the virtual time, and every playing <video> is seeked to match.
// A screenshot taken after step() is therefore the same picture no matter
// how slowly it was taken.
(() => {
  if (window.top !== window) return;
  if (window.__vt) return;
  const R = {
    setTimeout: window.setTimeout.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
    rAF: window.requestAnimationFrame.bind(window),
    cAF: window.cancelAnimationFrame.bind(window),
    perfNow: performance.now.bind(performance),
    dateNow: Date.now.bind(Date),
    Date: window.Date,
  };
  let mode = 'real';
  let vt = 0;
  let t0Perf = 0;
  let t0Date = 0;
  let nextId = 1;
  const timers = new Map();      // id -> {fn, args, delay, interval, due, real}
  let rafQ = new Map();
  let rafReal = new Map();

  const call = (fn, args) => {
    try {
      if (typeof fn === 'function') fn.apply(window, args);
      else (0, eval)(String(fn));
    } catch (e) {
      setTimeout(() => { throw e; });
    }
  };
  const arm = (t) => {
    if (mode === 'real') {
      t.realDue = R.perfNow() + t.delay;
      t.real = R.setTimeout(() => fire(t), t.delay);
    } else {
      t.due = vt + t.delay;
    }
  };
  const fire = (t) => {
    if (!timers.has(t.id)) return;
    if (!t.interval) timers.delete(t.id);
    call(t.fn, t.args);
    if (t.interval && timers.has(t.id)) arm(t);
  };
  const add = (fn, delay, args, interval) => {
    const t = {id: nextId++, fn, args, delay: Math.max(0, Number(delay) || 0), interval};
    if (interval && t.delay < 1) t.delay = 1;
    timers.set(t.id, t);
    arm(t);
    return t.id;
  };
  const clear = (id) => {
    const t = timers.get(id);
    if (!t) return;
    if (t.real !== undefined) R.clearTimeout(t.real);
    timers.delete(id);
  };
  window.setTimeout = (fn, delay, ...args) => add(fn, delay, args, false);
  window.setInterval = (fn, delay, ...args) => add(fn, delay, args, true);
  window.clearTimeout = clear;
  window.clearInterval = clear;

  window.requestAnimationFrame = (cb) => {
    const id = nextId++;
    if (mode === 'real') rafReal.set(id, R.rAF((ts) => { rafReal.delete(id); cb(ts); }));
    else rafQ.set(id, cb);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    if (rafReal.has(id)) { R.cAF(rafReal.get(id)); rafReal.delete(id); }
    rafQ.delete(id);
  };

  performance.now = () => (mode === 'real' ? R.perfNow() : t0Perf + vt);
  function VDate(...a) {
    if (!(this instanceof VDate)) return new R.Date(Date.now()).toString();
    return a.length ? new R.Date(...a) : new R.Date(Date.now());
  }
  VDate.prototype = R.Date.prototype;
  VDate.now = () => (mode === 'real' ? R.dateNow() : t0Date + vt);
  VDate.parse = R.Date.parse;
  VDate.UTC = R.Date.UTC;
  window.Date = VDate;

  const media = new Map();       // video -> {start, base}
  const syncAnimations = () => {
    for (const a of document.getAnimations()) {
      if (!a.__vt) {
        a.__vt = {start: vt, base: a.currentTime || 0};
        try { a.pause(); } catch (e) {}
      }
      const rate = a.playbackRate || 1;
      try { a.currentTime = a.__vt.base + (vt - a.__vt.start) * rate; } catch (e) {}
    }
  };
  const syncMedia = async () => {
    const waits = [];
    for (const v of document.querySelectorAll('video')) {
      if (!media.has(v)) {
        if (v.paused && !v.autoplay) continue;
        media.set(v, {start: vt, base: v.currentTime || 0});
        try { v.pause(); } catch (e) {}
      }
      const m = media.get(v);
      let t = m.base + ((vt - m.start) / 1000) * (v.playbackRate || 1);
      if (v.duration && isFinite(v.duration)) t = v.loop ? t % v.duration : Math.min(t, v.duration - 0.001);
      if (Math.abs(v.currentTime - t) > 0.0005) {
        waits.push(new Promise((res) => {
          const done = () => { v.removeEventListener('seeked', done); res(); };
          v.addEventListener('seeked', done);
          R.setTimeout(done, 1500);
          v.currentTime = t;
        }));
      }
    }
    await Promise.all(waits);
  };
  // Let React's MessageChannel scheduler and microtasks flush.
  const settle = () => new Promise((r) => R.setTimeout(r, 0));

  window.__vt = {
    get mode() { return mode; },
    get now() { return vt; },
    start() {
      if (mode === 'virtual') return;
      mode = 'virtual';
      t0Perf = R.perfNow();
      t0Date = R.dateNow();
      vt = 0;
      for (const t of timers.values()) {
        if (t.real !== undefined) {
          R.clearTimeout(t.real);
          t.real = undefined;
          t.due = Math.max(0, t.realDue - t0Perf);
        }
      }
      for (const [id, h] of rafReal) { R.cAF(h); rafReal.delete(id); }
      syncAnimations();
    },
    async step(ms) {
      const target = vt + ms;
      for (let guard = 0; guard < 10000; guard++) {
        let next = null;
        for (const t of timers.values()) if (t.due !== undefined && t.due <= target && (!next || t.due < next.due)) next = t;
        if (!next) break;
        vt = Math.max(vt, next.due);
        fire(next);
        await settle();
      }
      vt = target;
      const q = rafQ;
      rafQ = new Map();
      for (const cb of q.values()) call(cb, [t0Perf + vt]);
      await settle();
      await syncMedia();
      syncAnimations();
      await settle();
    },
  };
})();
