// The GTA 6 breaking-news ad (owner, 2026-11-14): "make another ad using this
// video as the hook". The video is an AI news anchor: "Breaking. GTA 6 is
// coming. Who's clipping the biggest moments first?" The ad answers him.
//
// 24.375 s, 13 bars at 128 BPM, 1080x1920 at 60 fps. Every time comes from
// ./gta.json, which ad/make_gta_audio.py reads too, so a cut and its sound
// cannot drift apart. The newsroom never leaves: each product beat is a
// headline under an "HZ NEWS" bar.
//
//   0:00.000  hook     the owner's clip, untouched, with a lower third that
//                      shows each word as he says it
//   0:05.625  answer   freeze on his face: "YOU ARE. …if Highlightz is watching"
//   0:07.500  flood    GTA 6 drops, everyone goes live: 30 live tiles
//   0:09.375  watch    you can't watch them all, Highlightz can: 30 / 30
//   0:11.250  spike    the trigger score climbs as chat explodes
//   0:13.125  clip     ...it's already a clip; approve it
//   0:15.000  post     make it vertical, post in one press (Pro)
//   0:16.875  vod      meanwhile, everyone else: still scrubbing the VOD
//   0:18.750  free     free to start: 1 channel, no card, no time limit
//   0:20.625  cta      Catch it live. Post it first. highlightz.app
//
// WHAT THE AD MAY SAY. Every product line is the site's own (landing page and
// PLAN_LIMITS, read 2026-11-14): thirty channels at once on Studio, ten on
// Pro, one on Free; "Every big moment, already a clip"; "Trigger score ·
// live" against the channel's own threshold; the signal "Chat Calls For The
// Clip"; the Clip Editor's "Cam + Game" layout and 1080x1920 60 fps export,
// and one-press posting to TikTok, both Pro; "Free plan · one channel, Twitch
// or Kick · no card · no time limit". No speed is promised and no date is
// given for the game. "GTA 6" is written as plain words: no logo, no art, no
// footage of the game, and none of the tiles is a real channel.
//
// The clip's frames are the 2x Real-ESRGAN upscale (ad/prepare_gta_footage.py),
// one JPEG per source frame in public/ad-gta/frames.
import React from 'react';
import {AbsoluteFill, Img, interpolate, random, staticFile, useCurrentFrame} from 'remotion';
import AD from './gta.json';
import {C, EASE, FONT, IN_OUT, SPRING} from '../brand';
import {loadFonts} from '../kit/fonts';
import {Grain, LogoGlow, LogoMark} from '../kit/Look';
import {Shockwave} from '../kit/Fx';
import {PhoneFrame} from '../kit/Phone';

const FPS = AD.fps;
const BEAT = 60 / AD.bpm;
export const GTA_FPS = FPS;
export const GTA_FRAMES = Math.round(AD.beats * BEAT * FPS); // 1463
const T = (s: number) => Math.round(s * FPS);
const S = AD.scenes;
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const EMOJI = "'Noto Color Emoji'";
const SANS = `${FONT.sans.split(',')[0]}, ${EMOJI}, system-ui, sans-serif`;
const MONO = `${FONT.mono.split(',')[0]}, ${EMOJI}, ui-monospace, monospace`;
const NEWS_RED = '#E0162B';
const YELLOW = '#FFE14D';
const GREEN = '#4ADE80';
const PURPLE_X = 'linear-gradient(90deg,#F343FE,#C938FF 55%,#9847EE)';

// ── the clip ────────────────────────────────────────────────────────────────

const srcIndex = (srcT: number) =>
  Math.max(0, Math.min(AD.source_frames - 1, Math.floor(srcT * AD.source_fps + 1e-6)));
const frameUrl = (i: number) => staticFile(`ad-gta/frames/${String(i).padStart(4, '0')}.jpg`);

const Frame: React.FC<{i: number; style?: React.CSSProperties}> = ({i, style}) => (
  <Img src={frameUrl(i)} style={{position: 'absolute', left: 0, top: 0, width: 1080, height: 1920, ...style}} />
);

// His face on the freeze frame, in 1080x1920 coordinates (measured off the
// upscaled frame): used by the zoom and by the round sticker on the end card.
const FACE = {i: AD.freeze_frame, cx: 540, cy: 760, r: 400};

// A shake that decays after each of the given times (seconds).
const shake = (f: number, hits: number[], amp: number, seed: string) => {
  let x = 0, y = 0, r = 0;
  for (const h of hits) {
    const d = f - T(h);
    if (d < 0 || d > 14) continue;
    const k = Math.exp(-d / 4) * amp;
    x += (random(seed + 'x' + f) - 0.5) * 2 * k;
    y += (random(seed + 'y' + f) - 0.5) * 2 * k;
    r += (random(seed + 'r' + f) - 0.5) * 0.6 * k / 10;
  }
  return {x, y, r};
};

const Hi: React.FC<{children: React.ReactNode}> = ({children}) => (
  <span style={{background: PURPLE_X, WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent'}}>{children}</span>
);

// ── the hook: his clip and a lower third ────────────────────────────────────

const Hook: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      <Frame i={srcIndex(Math.min(t, AD.hook_s - 0.001))} style={{filter: 'saturate(1.06) contrast(1.03)'}} />
    </AbsoluteFill>
  );
};

const TICKER = 'CHAT GOES FERAL  ·  LOCAL CLIPPER SLEEPS THROUGH THE MOMENT OF THE YEAR  ·  SOURCES: “IT WAS A REALLY GOOD CLIP”  ·  DEVELOPING: EVERYONE IS ABOUT TO GO LIVE  ·  ';

// The lower third. Every word of a line is laid out from the start (hidden
// until it is spoken), so nothing reflows as he talks.
const Chyron: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS;
  if (t >= AD.hook_s) return null;
  const inP = interpolate(f, [2, 16], [0, 1], {...clamp, easing: SPRING});
  const flip = interpolate(f - T(AD.tag_flip), [0, 9], [0, 1], {...clamp, easing: EASE});
  const flipped = t >= AD.tag_flip;
  const accent = flipped ? C.mid : NEWS_RED;
  const k = Math.max(0, AD.lines.reduce((acc, l, idx) => (t >= l.from ? idx : acc), 0));
  const line = AD.lines[k];
  const size = k === 0 ? 112 : k === 1 ? 94 : 63;
  const lineIn = interpolate(f - T(line.from), [0, 7], [0, 1], {...clamp, easing: SPRING});
  const dot = 0.45 + 0.55 * Math.abs(Math.sin(f / 9));
  let last = -1;
  line.words.forEach((w, idx) => { if (t >= (w[1] as number)) last = idx; });
  return (
    <div style={{position: 'absolute', left: 54, right: 54, top: 1176, transform: `translateY(${(1 - inP) * 300}px)`, opacity: Math.min(1, inP * 2)}}>
      <div style={{display: 'inline-flex', alignItems: 'center', gap: 14, padding: '12px 24px 12px 20px', background: flipped ? PURPLE_X : NEWS_RED,
        color: '#fff', fontFamily: MONO, fontWeight: 600, fontSize: 34, letterSpacing: '.16em', borderRadius: '12px 12px 0 0',
        transform: `perspective(600px) rotateX(${flipped ? (1 - flip) * -90 : 0}deg)`, transformOrigin: '50% 100%'}}>
        <span style={{width: 18, height: 18, borderRadius: 99, background: '#fff', opacity: dot}} />
        {flipped ? 'HZ NEWS · LIVE' : 'BREAKING NEWS'}
      </div>
      <div style={{minHeight: 196, boxSizing: 'border-box', padding: '22px 36px', display: 'flex', alignItems: 'center', background: 'rgba(10,10,14,.94)',
        borderLeft: `14px solid ${accent}`, boxShadow: '0 24px 70px rgba(0,0,0,.55)'}}>
        <div style={{fontFamily: SANS, fontWeight: 800, fontSize: size, lineHeight: 1.08, letterSpacing: '-0.04em', color: '#fff',
          transform: `scale(${interpolate(lineIn, [0, 1], [0.96, 1])})`, transformOrigin: '0 50%'}}>
          {line.words.map((w, idx) => {
            const at = T(w[1] as number);
            const p = interpolate(f - at, [0, 6], [0, 1], {...clamp, easing: SPRING});
            return (
              <React.Fragment key={idx}>
                <span style={{display: 'inline-block', opacity: f >= at ? 1 : 0, color: idx === last ? YELLOW : '#fff',
                  transform: `translateY(${(1 - p) * 18}px) scale(${interpolate(p, [0, 1], [1.18, 1])})`, transformOrigin: '50% 80%'}}>
                  {w[0]}
                </span>
                {idx < line.words.length - 1 ? ' ' : null}
              </React.Fragment>
            );
          })}
        </div>
      </div>
      <div style={{height: 58, overflow: 'hidden', background: accent, position: 'relative', borderRadius: '0 0 12px 12px'}}>
        <div style={{position: 'absolute', left: -f * 4.2, top: 0, height: 58, display: 'flex', alignItems: 'center', whiteSpace: 'nowrap',
          fontFamily: MONO, fontWeight: 600, fontSize: 27, letterSpacing: '.06em', color: '#fff'}}>
          {TICKER + TICKER + TICKER}
        </div>
      </div>
    </div>
  );
};

// ── stickers ────────────────────────────────────────────────────────────────

// The caption sticker: white, heavy, a little crooked.
const Cap: React.FC<{
  from: number; to: number; children: React.ReactNode; y: number; size?: number; tilt?: number; big?: boolean;
}> = ({from, to, children, y, size = 54, tilt = -2, big}) => {
  const f = useCurrentFrame();
  const a = T(from), b = T(to);
  if (f < a || f >= b) return null;
  const p = interpolate(f - a, [0, 9], [0, 1], {...clamp, easing: SPRING});
  const out = interpolate(f, [b - 4, b], [1, 0], clamp);
  const s = interpolate(p, [0, 1], [big ? 1.9 : 0.6, 1]) * interpolate(out, [0, 1], [0.85, 1]);
  const wob = Math.sin((f - a) / 9) * 0.6;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: y, display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
      <div style={{transform: `translateY(-50%) scale(${s}) rotate(${tilt + wob}deg)`, opacity: Math.min(p * 3, 1) * out, maxWidth: 960,
        boxSizing: 'border-box', textAlign: 'center', padding: `${size * 0.26}px ${size * 0.5}px`, borderRadius: size * 0.36, background: '#fff',
        color: '#0A0A0C', border: '5px solid #0A0A0C', boxShadow: '0 12px 0 rgba(10,10,12,.55), 0 24px 60px rgba(0,0,0,.45)',
        fontFamily: SANS, fontWeight: 800, fontSize: size, lineHeight: 1.08, letterSpacing: '-0.035em', whiteSpace: 'nowrap'}}>
        {children}
      </div>
    </div>
  );
};

const Burst: React.FC<{at: number; until: number; x: number; y: number; text: string; rot?: number}> = ({at, until, x, y, text, rot = 8}) => {
  const f = useCurrentFrame();
  if (f < T(at) || f >= T(until)) return null;
  const p = interpolate(f - T(at), [0, 7], [0, 1], {...clamp, easing: SPRING});
  const jit = (random('b' + Math.floor(f / 2)) - 0.5) * 3;
  return (
    <div style={{position: 'absolute', left: x, top: y, transform: `translate(-50%,-50%) scale(${p}) rotate(${rot + jit}deg)`, padding: '16px 30px',
      background: YELLOW, color: '#0A0A0C', border: '6px solid #0A0A0C', borderRadius: 14, fontFamily: SANS, fontWeight: 800, fontSize: 60,
      letterSpacing: '-0.02em', boxShadow: '8px 8px 0 #0A0A0C', whiteSpace: 'nowrap'}}>
      {text}
    </div>
  );
};

const Emoji: React.FC<{at: number; until?: number; x: number; y: number; size: number; char: string; rot?: number}> = ({at, until, x, y, size, char, rot = 0}) => {
  const f = useCurrentFrame();
  const a = T(at);
  if (f < a || (until !== undefined && f >= T(until))) return null;
  const p = interpolate(f - a, [0, 10], [0, 1], {...clamp, easing: SPRING});
  const bob = Math.sin((f - a) / 7) * 6;
  return (
    <div style={{position: 'absolute', left: x - size / 2, top: y - size / 2 + bob, fontSize: size, lineHeight: 1, fontFamily: EMOJI,
      transform: `scale(${p}) rotate(${rot + Math.sin((f - a) / 11) * 8}deg)`, filter: 'drop-shadow(0 10px 16px rgba(0,0,0,.45))'}}>
      {char}
    </div>
  );
};

// A round sticker of the anchor, cut from the freeze frame.
const FaceSticker: React.FC<{at: number; x: number; y: number; size: number; rot?: number}> = ({at, x, y, size, rot = -8}) => {
  const f = useCurrentFrame();
  const a = T(at);
  if (f < a) return null;
  const p = interpolate(f - a, [0, 12], [0, 1], {...clamp, easing: SPRING});
  const k = size / (2 * FACE.r);
  const r = interpolate(p, [0, 1], [-200, rot]) + Math.sin((f - a) / 8) * 3 * p;
  return (
    <div style={{position: 'absolute', left: x - size / 2, top: y - size / 2, width: size, height: size, transform: `scale(${p}) rotate(${r}deg)`}}>
      <div style={{width: size, height: size, borderRadius: '50%', overflow: 'hidden', position: 'relative', border: '10px solid #fff',
        boxSizing: 'border-box', boxShadow: '0 18px 40px rgba(0,0,0,.5), 0 0 0 5px #0A0A0C'}}>
        <Img src={frameUrl(FACE.i)} style={{position: 'absolute', width: 1080 * k, height: 1920 * k, left: size / 2 - FACE.cx * k - 10, top: size / 2 - FACE.cy * k - 10}} />
      </div>
    </div>
  );
};

// ── the answer: freeze on his face ──────────────────────────────────────────

const Answer: React.FC = () => {
  const f = useCurrentFrame();
  const l = f - T(S.answer);
  const z = 1 + 0.2 * interpolate(l, [0, 9], [0, 1], {...clamp, easing: EASE}) + l * 0.0005;
  const sh = shake(f, [S.answer], 22, 'an');
  return (
    <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
      <AbsoluteFill style={{transform: `translate(${sh.x}px, ${sh.y}px) rotate(${sh.r}deg) scale(${z})`, transformOrigin: '540px 640px'}}>
        <Frame i={AD.freeze_frame} style={{filter: 'grayscale(.7) contrast(1.18) brightness(.86)'}} />
      </AbsoluteFill>
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(152,71,238,.30), rgba(243,67,254,.16))', mixBlendMode: 'screen'}} />
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 40%, transparent 38%, rgba(8,0,16,.78) 100%)'}} />
    </AbsoluteFill>
  );
};

// ── the newsroom the pitch plays in ─────────────────────────────────────────

const Stage: React.FC = () => {
  const f = useCurrentFrame();
  const drift = 1.18 + Math.sin(f / 160) * 0.02;
  return (
    <AbsoluteFill style={{background: C.base, overflow: 'hidden'}}>
      <Frame i={AD.freeze_frame} style={{filter: 'blur(30px) brightness(.30) saturate(1.5)', transform: `scale(${drift})`}} />
      <AbsoluteFill style={{background: 'radial-gradient(circle at 22% 22%, rgba(243,67,254,.20), transparent 50%), radial-gradient(circle at 82% 80%, rgba(152,71,238,.26), transparent 55%)'}} />
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 42%, rgba(0,0,0,.68) 100%)'}} />
    </AbsoluteFill>
  );
};

const TAGS: [number, string][] = [
  [S.flood, 'DEVELOPING'], [S.watch, 'LIVE'], [S.spike, 'THIS JUST IN'], [S.clip, 'CONFIRMED'],
  [S.post, 'EXCLUSIVE'], [S.vod, 'MEANWHILE'], [S.free, 'FORECAST'],
];

// The bar that stays up through the pitch: the station, and what kind of news this is.
const NewsBar: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS;
  if (t < S.flood || t >= S.cta) return null;
  const inP = interpolate(f - T(S.flood), [0, 10], [0, 1], {...clamp, easing: SPRING});
  const out = interpolate(f, [T(S.cta) - 5, T(S.cta)], [1, 0], clamp);
  let cur = TAGS[0];
  for (const tg of TAGS) if (t >= tg[0]) cur = tg;
  const flip = interpolate(f - T(cur[0]), [0, 8], [0, 1], {...clamp, easing: EASE});
  const dot = 0.45 + 0.55 * Math.abs(Math.sin(f / 9));
  return (
    <div style={{position: 'absolute', left: 60, top: 208, display: 'flex', alignItems: 'stretch', opacity: out,
      transform: `translateX(${(1 - inP) * -500}px)`, boxShadow: '0 16px 40px rgba(0,0,0,.45)', borderRadius: 12}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 13, padding: '12px 22px 12px 18px', background: PURPLE_X, borderRadius: '12px 0 0 12px',
        fontFamily: MONO, fontWeight: 600, fontSize: 32, letterSpacing: '.14em', color: '#fff'}}>
        <span style={{width: 16, height: 16, borderRadius: 99, background: '#fff', opacity: dot}} />HZ NEWS
      </div>
      <div style={{padding: '12px 24px', background: 'rgba(10,10,14,.94)', borderRadius: '0 12px 12px 0', overflow: 'hidden', display: 'flex', alignItems: 'center'}}>
        <div style={{fontFamily: MONO, fontWeight: 600, fontSize: 32, letterSpacing: '.16em', color: YELLOW, whiteSpace: 'nowrap',
          transform: `translateY(${(1 - flip) * 46}px)`, opacity: flip}}>
          {cur[1]}
        </div>
      </div>
    </div>
  );
};

// Enter with a spring, leave quickly before the next block takes the stage.
const useInOut = (from: number, to: number, enter = 9) => {
  const f = useCurrentFrame();
  const a = T(from), b = T(to);
  const vis = f >= a && f < b;
  const p = interpolate(f - a, [0, enter], [0, 1], {...clamp, easing: SPRING});
  const out = interpolate(f, [b - 5, b], [1, 0], clamp);
  return {vis, p, out, f, a, b};
};

const Headline: React.FC<{from: number; to: number; children: React.ReactNode; size?: number}> = ({from, to, children, size = 88}) => {
  const {vis, p, out} = useInOut(from, to, 9);
  if (!vis) return null;
  return (
    <div style={{position: 'absolute', left: 60, right: 60, top: 300, fontFamily: SANS, fontWeight: 800, fontSize: size, lineHeight: 1.02,
      letterSpacing: '-0.045em', color: C.ink, opacity: Math.min(1, p * 2) * out, transform: `translateY(${(1 - p) * 46}px) scale(${interpolate(p, [0, 1], [1.08, 1])})`,
      transformOrigin: '0 50%', textShadow: '0 8px 30px rgba(0,0,0,.55)'}}>
      {children}
    </div>
  );
};

// ── flood + watch: thirty live tiles ────────────────────────────────────────

const COLS = 5, ROWS = 6, TILE_W = 180, TILE_H = 96, GAP = 14;
const WALL_X = (1080 - (COLS * TILE_W + (COLS - 1) * GAP)) / 2;
const WALL_Y = 548;
const WALL_H = ROWS * TILE_H + (ROWS - 1) * GAP;
const HUES = [276, 205, 18, 150, 330, 44, 232, 0, 178, 292];
const TILES = Array.from({length: COLS * ROWS}, (_, k) => {
  const viewers = [0.9, 1.4, 2.2, 3.8, 5.1, 7.6, 12.4, 18.9, 26.3, 41.7][Math.floor(random('tv' + k) * 10)];
  return {
    k,
    x: WALL_X + (k % COLS) * (TILE_W + GAP),
    y: WALL_Y + Math.floor(k / COLS) * (TILE_H + GAP),
    order: random('to' + k),
    hue: HUES[Math.floor(random('th' + k) * HUES.length)],
    viewers: `${viewers.toFixed(1)}K`,
    bx: 30 + random('tb' + k) * 40,
  };
});

const LiveWall: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS;
  if (t < S.flood || t >= S.spike) return null;
  const out = interpolate(f, [T(S.spike) - 6, T(S.spike)], [1, 0], clamp);
  const scanEnd = T(S.watch + 2.4 * BEAT);
  const scan = interpolate(f, [T(S.watch) + 5, scanEnd], [WALL_Y - 30, WALL_Y + WALL_H + 30], {...clamp, easing: IN_OUT});
  const watching = t >= S.watch;
  const seen = watching ? TILES.filter((tl) => tl.y + TILE_H / 2 < scan).length : 0;
  const chip = interpolate(f - T(S.watch), [0, 10], [0, 1], {...clamp, easing: SPRING});
  const done = seen === TILES.length;
  const pulse = done && f >= scanEnd ? 1 + 0.05 * Math.exp(-((f - scanEnd) % 28) / 6) : 1;
  return (
    <AbsoluteFill style={{opacity: out}}>
      {TILES.map((tl) => {
        const at = T(S.flood) + 4 + Math.round(tl.order * 58);
        if (f < at) return null;
        const p = interpolate(f - at, [0, 9], [0, 1], {...clamp, easing: SPRING});
        const on = watching && tl.y + TILE_H / 2 < scan;
        const blink = Math.floor((f + tl.k * 7) / 9);
        return (
          <div key={tl.k} style={{position: 'absolute', left: tl.x, top: tl.y, width: TILE_W, height: TILE_H, borderRadius: 14, overflow: 'hidden',
            transform: `scale(${p})`, background: `linear-gradient(135deg, hsl(${tl.hue},64%,38%), hsl(${tl.hue + 38},66%,16%))`,
            boxShadow: on ? '0 0 0 3px #F343FE, 0 0 26px rgba(243,67,254,.65)' : '0 0 0 1.5px rgba(242,234,247,.16), 0 10px 24px rgba(0,0,0,.4)'}}>
            <div style={{position: 'absolute', left: `${tl.bx}%`, top: '30%', width: 90, height: 90, borderRadius: 99, transform: 'translate(-50%,-30%)',
              background: `radial-gradient(circle, hsla(${tl.hue + 20},95%,74%,.7), transparent 65%)`}} />
            <div style={{position: 'absolute', left: 8, top: 8, padding: '3px 8px', borderRadius: 6, background: NEWS_RED, fontFamily: MONO, fontWeight: 600,
              fontSize: 17, letterSpacing: '.08em', color: '#fff'}}>LIVE</div>
            <div style={{position: 'absolute', left: 9, bottom: 7, fontFamily: MONO, fontWeight: 600, fontSize: 16, color: '#fff', textShadow: '0 1px 4px rgba(0,0,0,.8)'}}>
              {tl.viewers}
            </div>
            {[0, 1, 2].map((r) => (
              <div key={r} style={{position: 'absolute', right: 8, bottom: 9 + r * 9, height: 4, borderRadius: 2, background: 'rgba(255,255,255,.55)',
                width: 14 + random('tc' + tl.k + r + blink) * 34}} />
            ))}
            {on && (
              <div style={{position: 'absolute', right: 7, top: 7, width: 26, height: 26, borderRadius: 99, background: 'rgba(10,10,14,.85)',
                display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
                <LogoMark height={17} />
              </div>
            )}
          </div>
        );
      })}
      {watching && scan < WALL_Y + WALL_H + 20 && (
        <div style={{position: 'absolute', left: WALL_X - 30, right: WALL_X - 30, top: scan - 3, height: 6, borderRadius: 3, background: PURPLE_X,
          boxShadow: '0 0 34px 8px rgba(243,67,254,.75), 0 -40px 60px 10px rgba(201,56,255,.22)'}} />
      )}
      {watching && (
        <>
          <div style={{position: 'absolute', left: 0, right: 0, top: WALL_Y + WALL_H + 30, display: 'flex', justifyContent: 'center'}}>
            <div style={{display: 'flex', alignItems: 'center', gap: 18, padding: '16px 32px', borderRadius: 99, background: 'rgba(10,10,14,.9)',
              border: `3px solid ${done ? C.magenta : C.hair2}`, boxShadow: done ? '0 0 44px rgba(243,67,254,.5)' : 'none', fontFamily: SANS, fontWeight: 800,
              fontSize: 50, letterSpacing: '-0.03em', color: C.ink, transform: `scale(${interpolate(chip, [0, 1], [0.6, 1]) * pulse})`, opacity: chip}}>
              <span style={{fontFamily: EMOJI, fontSize: 44}}>👀</span>
              watching <span style={{fontFamily: MONO, fontWeight: 600, color: C.flare}}>{seen}/30</span> at once
            </div>
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: WALL_Y + WALL_H + 134, textAlign: 'center', fontFamily: MONO, fontWeight: 600, fontSize: 25,
            letterSpacing: '.08em', color: C.ink2, opacity: chip}}>
            30 CHANNELS ON STUDIO · 10 ON PRO · 1 FREE
          </div>
        </>
      )}
    </AbsoluteFill>
  );
};

// ── spike: the trigger score, and chat ──────────────────────────────────────

const GRAPH = {x: 110, y: 720, w: 860, h: 400};
const THRESHOLD = 0.6;
// The score over the bar: a restless baseline, then the climb.
const scoreAt = (u: number) => {
  const wob = 0.035 * Math.sin(u * 41) + 0.025 * Math.sin(u * 97 + 1.3) + 0.02 * Math.sin(u * 173 + 0.4);
  const climb = u > 0.42 ? Math.pow((u - 0.42) / 0.58, 2.3) * 0.8 : 0;
  return Math.max(0.03, Math.min(1, 0.17 + wob + climb));
};
const CHAT = AD.chat as [number, string, number][];

const Spike: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS;
  if (t < S.spike || t >= S.clip + 0.2) return null;
  const inP = interpolate(f - T(S.spike), [0, 10], [0, 1], {...clamp, easing: SPRING});
  const out = interpolate(f, [T(S.clip), T(S.clip + 0.2)], [1, 0], clamp);
  const u = Math.min(1, (t - S.spike) / (S.clip - S.spike));
  const N = 90;
  const pts: string[] = [];
  for (let k = 0; k <= N; k++) {
    const uu = (k / N) * u;
    pts.push(`${(GRAPH.x + uu * GRAPH.w).toFixed(1)},${(GRAPH.y + GRAPH.h * (1 - scoreAt(uu))).toFixed(1)}`);
  }
  const v = scoreAt(u);
  const hot = v >= THRESHOLD;
  const hx = GRAPH.x + u * GRAPH.w, hy = GRAPH.y + GRAPH.h * (1 - v);
  const ty = GRAPH.y + GRAPH.h * (1 - THRESHOLD);
  const stroke = hot ? C.ember : C.flare;
  const jit = hot ? (random('sj' + f) - 0.5) * 8 : 0;
  const shown = CHAT.filter((c) => t - S.spike >= c[0]);
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', left: 60, right: 60, top: 590, height: 590, borderRadius: 34, background: 'rgba(14,11,17,.9)',
        border: `2px solid ${hot ? C.ember : C.hair2}`, boxShadow: `0 30px 80px rgba(0,0,0,.55), 0 0 ${hot ? 70 : 40}px ${hot ? 'rgba(247,167,69,.35)' : 'rgba(201,56,255,.16)'}`,
        transform: `translate(${jit}px, ${(1 - inP) * 120}px) scale(${interpolate(inP, [0, 1], [0.9, 1])})`, opacity: Math.min(1, inP * 2)}} />
      <div style={{position: 'absolute', left: 100, top: 622, fontFamily: MONO, fontWeight: 600, fontSize: 30, letterSpacing: '.14em', color: C.ink2, opacity: inP}}>
        TRIGGER SCORE · LIVE
      </div>
      <div style={{position: 'absolute', right: 100, top: 606, fontFamily: MONO, fontWeight: 600, fontSize: 62, color: hot ? C.ember : C.ink, opacity: inP,
        transform: `scale(${hot ? 1.12 : 1})`, transformOrigin: '100% 50%'}}>
        {Math.round(v * 100)}
      </div>
      <svg width={1080} height={1920} style={{position: 'absolute', left: 0, top: 0, opacity: inP}}>
        <line x1={GRAPH.x} y1={ty} x2={GRAPH.x + GRAPH.w} y2={ty} stroke="rgba(242,234,247,.5)" strokeWidth={3} strokeDasharray="14 12" />
        <polyline points={pts.join(' ')} fill="none" stroke={stroke} strokeWidth={9} strokeLinejoin="round" strokeLinecap="round"
          style={{filter: `drop-shadow(0 0 ${hot ? 22 : 12}px ${stroke})`}} />
        <circle cx={hx} cy={hy} r={hot ? 20 : 14} fill="#fff" style={{filter: `drop-shadow(0 0 18px ${stroke})`}} />
      </svg>
      <div style={{position: 'absolute', left: GRAPH.x + 6, top: ty - 44, fontFamily: MONO, fontWeight: 600, fontSize: 24, letterSpacing: '.1em', color: C.ink2, opacity: inP}}>
        THIS CHANNEL&rsquo;S THRESHOLD
      </div>
      {shown.slice(-4).map((c, idx, arr) => {
        const age = f - T(S.spike + c[0]);
        const p = interpolate(age, [0, 6], [0, 1], {...clamp, easing: SPRING});
        const row = arr.length - 1 - idx;
        return (
          <div key={c[0]} style={{position: 'absolute', left: 90, top: 1404 - row * 74, display: 'flex', alignItems: 'center', gap: 16, padding: '10px 24px',
            borderRadius: 18, background: 'rgba(10,10,14,.88)', border: `1.5px solid ${C.hair2}`, fontFamily: SANS, fontWeight: 700, fontSize: 40,
            letterSpacing: '-0.02em', color: C.ink, whiteSpace: 'nowrap', opacity: (row === 3 ? 0.45 : 1) * Math.min(1, p * 2),
            transform: `translateX(${(1 - p) * -60}px) scale(${interpolate(p, [0, 1], [0.8, 1])})`, transformOrigin: '0 50%'}}>
            <span style={{width: 20, height: 20, borderRadius: 99, background: `hsl(${c[2]},80%,62%)`}} />{c[1]}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

// ── clip: already a clip; approve it ────────────────────────────────────────

const ClipScene: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.clip, S.post, 11);
  if (!vis) return null;
  const tap = T(AD.cues.click[0]);
  const pressed = f >= tap;
  const press = interpolate(f - tap, [0, 4, 12], [1, 0.92, 1], clamp);
  const finger = interpolate(f, [tap - 16, tap], [0, 1], {...clamp, easing: EASE});
  const chip = interpolate(f - T(S.clip), [4, 14], [0, 1], {...clamp, easing: SPRING});
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', left: 100, top: 570, width: 880, borderRadius: 36, overflow: 'hidden', background: 'rgba(14,11,17,.94)',
        border: `2px solid ${C.hair2}`, boxShadow: '0 40px 100px rgba(0,0,0,.6), 0 0 70px rgba(201,56,255,.22)',
        transform: `translateY(${(1 - p) * 220}px) rotate(${(1 - p) * -5}deg) scale(${interpolate(p, [0, 1], [0.8, 1])})`, opacity: Math.min(1, p * 2)}}>
        <div style={{position: 'relative', height: 470, background: 'linear-gradient(135deg, hsl(205,62%,30%), hsl(276,60%,14%) 70%)', overflow: 'hidden'}}>
          <div style={{position: 'absolute', left: '64%', top: '34%', width: 520, height: 520, borderRadius: 999, transform: 'translate(-50%,-50%)',
            background: 'radial-gradient(circle, rgba(255,196,120,.62), transparent 62%)'}} />
          <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 150, background: 'linear-gradient(180deg, transparent, rgba(8,4,14,.85))'}} />
          <div style={{position: 'absolute', left: '50%', top: '50%', width: 150, height: 150, borderRadius: 99, transform: 'translate(-50%,-50%)',
            background: 'rgba(10,10,14,.6)', border: '4px solid rgba(255,255,255,.9)', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
            <div style={{width: 0, height: 0, marginLeft: 12, borderLeft: '48px solid #fff', borderTop: '30px solid transparent', borderBottom: '30px solid transparent'}} />
          </div>
          <div style={{position: 'absolute', left: 24, top: 24, display: 'flex', alignItems: 'center', gap: 12, padding: '10px 20px', borderRadius: 99,
            background: PURPLE_X, fontFamily: MONO, fontWeight: 600, fontSize: 28, letterSpacing: '.12em', color: '#fff',
            transform: `scale(${interpolate(chip, [0, 1], [2.2, 1])}) rotate(${(1 - chip) * -10}deg)`, opacity: Math.min(1, chip * 2), transformOrigin: '0 50%'}}>
            <span style={{fontFamily: EMOJI}}>✂️</span>CAUGHT LIVE
          </div>
          <div style={{position: 'absolute', right: 24, bottom: 22, padding: '6px 14px', borderRadius: 10, background: 'rgba(0,0,0,.72)', fontFamily: MONO,
            fontWeight: 600, fontSize: 28, color: '#fff'}}>0:30</div>
        </div>
        <div style={{padding: '26px 30px 30px'}}>
          <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
            <div style={{padding: '10px 20px', borderRadius: 99, border: `2px solid ${C.iris}`, background: 'rgba(184,106,220,.14)', fontFamily: SANS, fontWeight: 700,
              fontSize: 34, letterSpacing: '-0.02em', color: C.ink}}>
              Chat Calls For The Clip
            </div>
            <div style={{fontFamily: MONO, fontWeight: 600, fontSize: 26, letterSpacing: '.08em', color: C.ink3}}>JUST NOW</div>
          </div>
          <div style={{display: 'flex', gap: 20, marginTop: 26}}>
            <div style={{flex: 2, height: 104, borderRadius: 24, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14, fontFamily: SANS,
              fontWeight: 800, fontSize: 46, letterSpacing: '-0.02em', transform: `scale(${press})`, color: pressed ? '#06210F' : GREEN,
              background: pressed ? GREEN : 'rgba(74,222,128,.12)', border: `3px solid ${GREEN}`, boxShadow: pressed ? '0 0 46px rgba(74,222,128,.55)' : 'none'}}>
              {pressed ? '✓ Approved' : '✓ Approve'}
            </div>
            <div style={{flex: 1, height: 104, borderRadius: 24, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: SANS, fontWeight: 800,
              fontSize: 42, letterSpacing: '-0.02em', color: C.ink3, border: `3px solid ${C.hair2}`}}>
              ✕ Reject
            </div>
          </div>
        </div>
      </div>
      {finger > 0 && f < tap + 16 && (
        <div style={{position: 'absolute', left: 470 + (1 - finger) * 260, top: 1262 + (1 - finger) * 240, fontSize: 120, lineHeight: 1, fontFamily: EMOJI,
          transform: `rotate(-18deg) scale(${f >= tap ? press : 1})`, filter: 'drop-shadow(0 12px 18px rgba(0,0,0,.5))',
          opacity: interpolate(f, [tap + 8, tap + 16], [1, 0], clamp)}}>
          👆
        </div>
      )}
    </AbsoluteFill>
  );
};

// ── post: vertical, one press ───────────────────────────────────────────────

const PostScene: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.post, S.vod, 11);
  if (!vis) return null;
  const tap = T(AD.cues.click[1]);
  const posted = f >= tap;
  const press = interpolate(f - tap, [0, 4, 12], [1, 0.9, 1], clamp);
  const W = 400;
  const side = interpolate(f - T(S.post + BEAT), [0, 10], [0, 1], {...clamp, easing: SPRING});
  const side2 = interpolate(f - T(S.post + BEAT * 1.5), [0, 10], [0, 1], {...clamp, easing: SPRING});
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', left: 540 - W / 2, top: 520, transform: `translateY(${(1 - p) * 320}px) rotate(${(1 - p) * 9 - 2}deg)`, opacity: Math.min(1, p * 2)}}>
        <PhoneFrame width={W}>
          <div style={{position: 'absolute', inset: 0, background: '#07060A'}}>
            <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: '42%', background: 'linear-gradient(160deg, hsl(276,55%,34%), hsl(300,60%,14%))', overflow: 'hidden'}}>
              <div style={{position: 'absolute', left: '50%', top: '34%', width: 96, height: 96, borderRadius: 99, background: 'rgba(255,255,255,.82)', transform: 'translate(-50%,-50%)'}} />
              <div style={{position: 'absolute', left: '50%', bottom: -70, width: 220, height: 170, borderRadius: '110px 110px 0 0', background: 'rgba(255,255,255,.82)', transform: 'translateX(-50%)'}} />
            </div>
            <div style={{position: 'absolute', left: 0, right: 0, top: '42%', bottom: 0, background: 'linear-gradient(135deg, hsl(205,62%,30%), hsl(18,70%,22%))', overflow: 'hidden'}}>
              <div style={{position: 'absolute', left: '66%', top: '30%', width: 300, height: 300, borderRadius: 999, transform: 'translate(-50%,-50%)',
                background: 'radial-gradient(circle, rgba(255,196,120,.6), transparent 62%)'}} />
            </div>
            <div style={{position: 'absolute', left: 0, right: 0, top: '37%', display: 'flex', justifyContent: 'center'}}>
              <div style={{padding: '7px 16px', borderRadius: 10, background: '#fff', color: '#0A0A0C', fontFamily: SANS, fontWeight: 800, fontSize: 28,
                letterSpacing: '-0.03em', border: '3px solid #0A0A0C', transform: 'rotate(-2deg)'}}>
                NO WAY <span style={{fontFamily: EMOJI}}>💀</span>
              </div>
            </div>
            <div style={{position: 'absolute', left: 18, right: 18, bottom: 22, height: 74, borderRadius: 20, display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: SANS, fontWeight: 800, fontSize: 34, letterSpacing: '-0.02em', color: posted ? '#06210F' : '#fff', transform: `scale(${press})`,
              background: posted ? GREEN : PURPLE_X, boxShadow: posted ? '0 0 40px rgba(74,222,128,.6)' : '0 0 30px rgba(243,67,254,.5)'}}>
              {posted ? 'Posted ✓' : 'Post to TikTok'}
            </div>
          </div>
        </PhoneFrame>
      </div>
      <div style={{position: 'absolute', left: 52, top: 760, padding: '14px 24px', borderRadius: 18, background: 'rgba(10,10,14,.92)', border: `2px solid ${C.iris}`,
        fontFamily: SANS, fontWeight: 700, fontSize: 40, letterSpacing: '-0.02em', color: C.ink, transform: `translateX(${(1 - side) * -200}px) rotate(-4deg)`, opacity: side}}>
        Cam + Game
      </div>
      <div style={{position: 'absolute', right: 44, top: 1010, padding: '14px 22px', borderRadius: 18, background: 'rgba(10,10,14,.92)', border: `2px solid ${C.iris}`,
        fontFamily: MONO, fontWeight: 600, fontSize: 32, color: C.ink, transform: `translateX(${(1 - side2) * 200}px) rotate(3deg)`, opacity: side2, textAlign: 'center', lineHeight: 1.25}}>
        1080×1920<br />60 fps
      </div>
      {f >= tap - 16 && f < tap + 16 && (
        <div style={{position: 'absolute', left: 560 + (1 - interpolate(f, [tap - 16, tap], [0, 1], {...clamp, easing: EASE})) * 240, top: 1268, fontSize: 104, lineHeight: 1,
          fontFamily: EMOJI, transform: `rotate(-18deg) scale(${f >= tap ? press : 1})`, filter: 'drop-shadow(0 12px 18px rgba(0,0,0,.5))',
          opacity: interpolate(f, [tap + 8, tap + 16], [1, 0], clamp)}}>
          👆
        </div>
      )}
      <div style={{position: 'absolute', left: 0, right: 0, top: 1380, textAlign: 'center', fontFamily: MONO, fontWeight: 600, fontSize: 26, letterSpacing: '.1em',
        color: C.ink2, opacity: p}}>
        CLIP EDITOR + SCHEDULER · PRO
      </div>
    </AbsoluteFill>
  );
};

// ── vod: everyone else ──────────────────────────────────────────────────────

const VodScene: React.FC = () => {
  const {vis, p, out, f, a} = useInOut(S.vod, S.free, 11);
  if (!vis) return null;
  const l = f - a;
  const head = 0.045 + 0.022 * Math.sin(l / 5) + 0.012 * Math.sin(l / 2.3);
  const TRACK = 780;
  const spin = (l * 9) % 360;
  const secs = 493 + Math.round(head * 600);
  const stamp = `0:${String(Math.floor(secs / 60)).padStart(2, '0')}:${String(secs % 60).padStart(2, '0')}`;
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', left: 90, top: 600, width: 900, height: 600, borderRadius: 30, background: '#16161A', border: '2px solid rgba(255,255,255,.14)',
        boxShadow: '0 40px 90px rgba(0,0,0,.6)', filter: 'grayscale(1)', transform: `translateY(${(1 - p) * 160}px) rotate(${1.5 - (1 - p) * 6}deg)`, opacity: Math.min(1, p * 2)}}>
        <div style={{position: 'absolute', left: 30, right: 30, top: 30, height: 400, borderRadius: 18, background: 'linear-gradient(135deg,#2a2a31,#101014)'}}>
          <div style={{position: 'absolute', left: '50%', top: '50%', width: 110, height: 110, marginLeft: -55, marginTop: -55, borderRadius: 99,
            border: '10px solid rgba(255,255,255,.14)', borderTopColor: 'rgba(255,255,255,.75)', transform: `rotate(${spin}deg)`}} />
          <div style={{position: 'absolute', left: 22, top: 18, padding: '6px 14px', borderRadius: 8, background: 'rgba(255,255,255,.12)', fontFamily: MONO, fontWeight: 600,
            fontSize: 26, letterSpacing: '.12em', color: 'rgba(255,255,255,.75)'}}>VOD</div>
        </div>
        <div style={{position: 'absolute', left: 60, top: 482, width: TRACK, height: 12, borderRadius: 6, background: 'rgba(255,255,255,.16)'}}>
          <div style={{position: 'absolute', left: 0, top: 0, height: 12, borderRadius: 6, width: TRACK * head, background: 'rgba(255,255,255,.7)'}} />
          <div style={{position: 'absolute', left: TRACK * head - 17, top: -11, width: 34, height: 34, borderRadius: 99, background: '#fff', boxShadow: '0 4px 14px rgba(0,0,0,.5)'}} />
        </div>
        <div style={{position: 'absolute', right: 60, top: 520, fontFamily: MONO, fontWeight: 600, fontSize: 32, color: 'rgba(255,255,255,.8)'}}>
          {stamp} <span style={{color: 'rgba(255,255,255,.4)'}}>/ 6:42:10</span>
        </div>
      </div>
      <div style={{position: 'absolute', left: 150 + TRACK * head - 8, top: 1094, fontSize: 84, lineHeight: 1, fontFamily: EMOJI, transform: 'rotate(-14deg)',
        filter: 'drop-shadow(0 10px 14px rgba(0,0,0,.55))', opacity: p}}>
        👆
      </div>
      <div style={{position: 'absolute', left: 250 + l * 0.55, top: 992, fontSize: 92, lineHeight: 1, fontFamily: EMOJI, transform: `scaleX(-1) rotate(${Math.sin(l / 6) * 4}deg)`, opacity: p}}>
        🐌
      </div>
    </AbsoluteFill>
  );
};

// ── free ────────────────────────────────────────────────────────────────────

const FREE_ROWS = ['1 channel · Twitch or Kick', 'no card', 'no time limit'];

const FreeScene: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.free, S.cta, 9);
  if (!vis) return null;
  const land = interpolate(f - T(S.free), [0, 10], [1.5, 1], {...clamp, easing: EASE});
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', left: 0, right: 0, top: 560, textAlign: 'center', fontFamily: SANS, fontWeight: 800, fontSize: 320, letterSpacing: '-0.06em', lineHeight: 1,
        transform: `scale(${land})`, opacity: Math.min(1, p * 2), background: 'linear-gradient(180deg,#FFE58A 0%,#F7A745 60%,#E07B1F 100%)', WebkitBackgroundClip: 'text',
        backgroundClip: 'text', color: 'transparent', filter: 'drop-shadow(0 16px 40px rgba(247,167,69,.45))'}}>
        FREE
      </div>
      {FREE_ROWS.map((row, k) => {
        const at = T(S.free + BEAT * (1 + k));
        if (f < at) return null;
        const q = interpolate(f - at, [0, 10], [0, 1], {...clamp, easing: SPRING});
        return (
          <div key={row} style={{position: 'absolute', left: 150, top: 940 + k * 118, display: 'flex', alignItems: 'center', gap: 26, fontFamily: SANS, fontWeight: 800,
            fontSize: 60, letterSpacing: '-0.035em', color: C.ink, transform: `translateX(${(1 - q) * 420}px)`, opacity: Math.min(1, q * 2), whiteSpace: 'nowrap',
            textShadow: '0 6px 20px rgba(0,0,0,.6)'}}>
            <span style={{flex: 'none', width: 74, height: 74, borderRadius: 99, background: GREEN, color: '#06210F', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 46, boxShadow: '0 0 30px rgba(74,222,128,.5)', transform: `scale(${interpolate(q, [0, 1], [0.3, 1])})`}}>✓</span>
            {row}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

// ── the end card ────────────────────────────────────────────────────────────

const Cta: React.FC = () => {
  const f = useCurrentFrame();
  const a = T(S.cta);
  if (f < a) return null;
  const l = f - a;
  const logo = interpolate(l, [0, 12], [0, 1], {...clamp, easing: SPRING});
  const l1 = interpolate(l, [8, 20], [0, 1], {...clamp, easing: SPRING});
  const l1b = interpolate(l, [T(BEAT), T(BEAT) + 12], [0, 1], {...clamp, easing: SPRING});
  const l2 = interpolate(l, [T(BEAT * 2), T(BEAT * 2) + 12], [0, 1], {...clamp, easing: SPRING});
  const l3 = interpolate(l, [T(BEAT * 3), T(BEAT * 3) + 12], [0, 1], {...clamp, easing: EASE});
  const beat = FPS * BEAT;
  const pulse = Math.exp(-((l % beat) / beat) * 5);
  return (
    <AbsoluteFill style={{alignItems: 'center', background: C.base}}>
      <AbsoluteFill style={{background: 'radial-gradient(circle at 24% 20%, rgba(243,67,254,.20), transparent 50%), radial-gradient(circle at 80% 82%, rgba(152,71,238,.26), transparent 55%)'}} />
      <div style={{position: 'absolute', inset: 0, opacity: 0.5 + pulse * 0.2}}><LogoGlow height={1500} opacity={0.5} /></div>
      <div style={{position: 'absolute', top: 290, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 30}}>
        <div style={{transform: `scale(${logo * (1 + pulse * 0.03)})`, filter: `drop-shadow(0 0 ${30 + pulse * 40}px rgba(243,67,254,.55))`}}>
          <LogoMark height={210} />
        </div>
        <div style={{fontFamily: MONO, fontWeight: 600, fontSize: 42, letterSpacing: '.42em', marginRight: '-.42em', color: C.ink, opacity: logo}}>HIGHLIGHTZ</div>
        <div style={{marginTop: 22, textAlign: 'center', fontFamily: SANS, fontWeight: 800, fontSize: 112, lineHeight: 1.0, letterSpacing: '-0.05em', color: C.ink}}>
          <div style={{opacity: l1, transform: `translateY(${(1 - l1) * 40}px)`}}>Catch it live.</div>
          <div style={{opacity: l1b, transform: `translateY(${(1 - l1b) * 40}px)`}}><Hi>Post it first.</Hi></div>
        </div>
        <div style={{marginTop: 20, fontFamily: MONO, fontWeight: 600, fontSize: 64, color: C.ink, padding: '20px 42px', borderRadius: 22, background: 'rgba(247,167,69,.10)',
          border: `3px solid ${C.ember}`, boxShadow: `0 0 ${24 + pulse * 30}px rgba(247,167,69,${0.25 + pulse * 0.25})`, opacity: l2,
          transform: `scale(${interpolate(l2, [0, 1], [1.4, 1])})`}}>
          highlightz.app
        </div>
        <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 42, color: C.ink2, opacity: l3}}>start clipping free · Twitch + Kick</div>
      </div>
      <FaceSticker at={AD.cues.ding[1]} x={540} y={1300} size={250} rot={-8} />
      <Cap from={AD.cues.boing[1]} to={S.end + 1} y={1492} size={46} tilt={-3}>back to you, chat 🎤</Cap>
    </AbsoluteFill>
  );
};

// ── the ad ──────────────────────────────────────────────────────────────────

export const GtaAd: React.FC = () => {
  loadFonts();
  const f = useCurrentFrame();
  const t = f / FPS;
  const hit = (s: number, dur = 8, peak = 0.9) => {
    const d = f - T(s);
    return d >= 0 && d < dur ? interpolate(d, [0, dur], [peak, 0], {...clamp, easing: EASE}) : 0;
  };
  const flash = Math.max(hit(0, 8, 0.5), hit(S.answer, 10, 0.95), hit(S.clip, 10, 0.9), hit(S.cta, 8, 0.6));
  const sh = shake(f, [S.flood, S.clip, S.cta], 12, 'gs');
  const stage = t >= S.flood && t < S.cta;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {t < S.answer && <Hook />}
      {t >= S.answer && t < S.flood && <Answer />}
      {stage && (
        <AbsoluteFill style={{transform: `translate(${sh.x}px, ${sh.y}px)`}}>
          <Stage />
          <LiveWall />
          <Spike />
          <ClipScene />
          <PostScene />
          <VodScene />
          <FreeScene />
        </AbsoluteFill>
      )}
      {t >= S.cta && <Cta />}

      <Chyron />
      <Cap from={S.answer} to={S.flood} y={338} size={150} big tilt={-3}>YOU ARE. 🫵</Cap>
      <Cap from={S.answer + 2 * BEAT} to={S.flood} y={1322} size={58} tilt={2}>…if <Hi>Highlightz</Hi> is watching 👀</Cap>
      <Burst at={S.answer + BEAT} until={S.flood} x={850} y={530} text="EXCLUSIVE" />

      <NewsBar />
      <Headline from={S.flood} to={S.watch}>GTA 6 drops.<br /><Hi>Everyone</Hi> goes live.</Headline>
      <Headline from={S.watch} to={S.spike} size={82}>You can&rsquo;t watch them all.<br /><Hi>Highlightz can.</Hi></Headline>
      <Headline from={S.spike} to={S.clip} size={104}>Chat <Hi>explodes</Hi>…</Headline>
      <Headline from={S.clip} to={S.post} size={96}>…it&rsquo;s <Hi>already a clip.</Hi></Headline>
      <Headline from={S.post} to={S.vod} size={86}>Make it vertical.<br /><Hi>Post in one press.</Hi></Headline>
      <Headline from={S.vod} to={S.free} size={74}>Everyone else:<br /><span style={{whiteSpace: 'nowrap'}}>still scrubbing the VOD.</span></Headline>
      <Headline from={S.free} to={S.cta} size={96}>It&rsquo;s <Hi>free</Hi> to start.</Headline>

      <Cap from={S.flood + 2 * BEAT} to={S.watch} y={1290} size={52} tilt={2}>(that&rsquo;s a LOT of streams 😵)</Cap>
      <Cap from={S.watch + 2 * BEAT} to={S.spike} y={1448} size={48} tilt={-2}>you: asleep 😴&nbsp;&nbsp;it: watching</Cap>
      <Cap from={S.clip + 2 * BEAT} to={S.post} y={1452} size={48} tilt={2}>before you even alt-tabbed 👀</Cap>
      <Cap from={S.post + 2 * BEAT} to={S.vod} y={1470} size={46} tilt={-2}>be the first one posted 🥇</Cap>
      <Cap from={S.vod + 2 * BEAT} to={S.free} y={1330} size={50} tilt={2}>6 hours 42 minutes of pain 😩</Cap>
      <Cap from={S.free + 3.4 * BEAT} to={S.cta} y={1392} size={44} tilt={-2}>forecast: cloudy with a chance of clips 🌦️</Cap>
      {f >= T(S.clip) && f < T(S.clip + 0.5) && <Shockwave start={T(S.clip)} x={540} y={860} size={900} />}

      {flash > 0 && <AbsoluteFill style={{background: '#fff', opacity: flash, mixBlendMode: 'screen'}} />}
      <Grain opacity={t < S.answer ? 0.03 : 0.06} />
    </AbsoluteFill>
  );
};
