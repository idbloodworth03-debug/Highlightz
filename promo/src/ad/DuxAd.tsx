// The Duxxion clip ad (owner, 2026-11-02): "Use this clip and make me another
// short ad for the campaign. Professional and high quality but also goofy and
// entertaining. 20-30 seconds, no voice over, trendy music, talk about the
// campaign and joining it."
//
// 26.25 s, 14 bars at 128 BPM, 1080x1920 at 60 fps. Every time comes from
// ./ad.json, which ad/make_ad_audio.py reads too, so a cut and its sound
// cannot drift apart.
//
//   0:00.000  hook     his goal celebration, then the same again zoomed in:
//                      "me when my Duxxion clip hits 1M views"
//   0:01.875  rewind   record scratch, freeze, VHS rewind to the start
//   0:03.750  replay   the play, annotated; a slow-mo "wait for it"; GOAL on
//                      the bar; then the report menu (his yell is bleeped)
//   0:11.250  pitch    Duxxion's Campaign, live now
//   0:13.125  pool     $500 prize pool
//   0:15.000  podium   1st $250, 2nd $150, 3rd $100
//   0:16.875  views    most views on one clip wins
//   0:18.750  steps    join free, clip his stream, post with #duxxion + link;
//                      posting closes Fri, Nov 6
//   0:24.375  cta      highlightz.app
//
// The clip's frames are the 2x Real-ESRGAN upscale (ad/prepare_footage.py),
// one JPEG per source frame in public/ad/frames, picked per output frame from
// ad.json's `footage` map, so slow-mo, freezes and the rewind are exact.
import React from 'react';
import {AbsoluteFill, Img, interpolate, random, staticFile, useCurrentFrame} from 'remotion';
import AD from './ad.json';
import {C, EASE, FONT, SPRING} from '../brand';
import {loadFonts} from '../kit/fonts';
import {Grain, LogoGlow, LogoMark} from '../kit/Look';
import {Shockwave} from '../kit/Fx';

const FPS = AD.fps;
const BEAT_S = 60 / AD.bpm;
export const AD_FPS = FPS;
export const AD_FRAMES = Math.round(AD.beats * BEAT_S * FPS); // 1575
const T = (s: number) => Math.round(s * FPS);
const S = AD.scenes;
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const EMOJI = "'Noto Color Emoji'";
const SANS = `${FONT.sans.split(',')[0]}, ${EMOJI}, system-ui, sans-serif`;
const MONO = `${FONT.mono.split(',')[0]}, ${EMOJI}, ui-monospace, monospace`;

// ── the clip ────────────────────────────────────────────────────────────────

type Seg = (typeof AD.footage)[number];
const segAt = (t: number): Seg | null => AD.footage.find((s) => t >= s.from && t < s.to) ?? null;

export const srcIndex = (srcT: number) =>
  Math.max(0, Math.min(AD.source_frames - 1, Math.floor(srcT * AD.source_fps + 1e-6)));
const frameUrl = (i: number) => staticFile(`ad/frames/${String(i).padStart(4, '0')}.jpg`);

// Where his face is, in 1080x1920 frame coordinates, on the two frames the
// stickers use (measured off the upscaled frames).
const FACE_LAUGH = {i: 156, cx: 705, cy: 262, r: 200};
const FACE_YELL = {i: 188, cx: 645, cy: 392, r: 205};
// The facecam is the top 616 px; zooming from its top edge pushes the clip's
// own burned-in caption (y ~962) off the bottom of the frame.
const FACE_ORIGIN = '660px 0px';

const Frame: React.FC<{i: number; style?: React.CSSProperties}> = ({i, style}) => (
  <Img src={frameUrl(i)} style={{position: 'absolute', left: 0, top: 0, width: 1080, height: 1920, ...style}} />
);

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

const RGB = () => (
  <svg width="0" height="0" style={{position: 'absolute'}}>
    <filter id="adR"><feColorMatrix type="matrix" values="1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0" /></filter>
    <filter id="adG"><feColorMatrix type="matrix" values="0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0" /></filter>
    <filter id="adB"><feColorMatrix type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0" /></filter>
  </svg>
);

// VHS: the three channels pulled apart, scanlines, a tracking band and jitter.
const Vhs: React.FC<{i: number; amount: number; f: number}> = ({i, amount, f}) => {
  const split = 7 * amount;
  const jy = (random('vj' + f) - 0.5) * 6 * amount;
  const bandY = ((f * 37) % 2300) - 200;
  return (
    <AbsoluteFill style={{background: '#000', transform: `translateY(${jy}px)`}}>
      <RGB />
      {(['adR', 'adG', 'adB'] as const).map((id, k) => (
        <Frame key={id} i={i} style={{filter: `url(#${id}) contrast(1.15) saturate(1.25)`, mixBlendMode: 'screen', transform: `translateX(${(k - 1) * split}px)`}} />
      ))}
      <div style={{position: 'absolute', left: 0, right: 0, top: bandY, height: 150, overflow: 'hidden', opacity: amount}}>
        <Frame i={i} style={{top: -bandY, transform: `translateX(${26 * amount}px)`, filter: 'brightness(1.5) blur(1px)'}} />
        <AbsoluteFill style={{background: 'repeating-linear-gradient(90deg, rgba(255,255,255,.18) 0 3px, transparent 3px 9px)', mixBlendMode: 'overlay'}} />
      </div>
      <AbsoluteFill style={{background: 'repeating-linear-gradient(0deg, rgba(0,0,0,.28) 0 2px, transparent 2px 5px)', opacity: amount}} />
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,.55) 100%)', opacity: amount}} />
    </AbsoluteFill>
  );
};

const Osd: React.FC<{text: string; f: number; blink?: boolean}> = ({text, f, blink}) => (
  <div
    style={{
      position: 'absolute', left: 90, top: 230, fontFamily: MONO, fontWeight: 600, fontSize: 64,
      color: '#fff', letterSpacing: '.06em', textShadow: '3px 3px 0 rgba(0,0,0,.7), 0 0 18px rgba(255,255,255,.45)',
      opacity: blink && Math.floor(f / 15) % 2 ? 0.25 : 1,
    }}
  >
    {text}
  </div>
);

const Footage: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS;
  const seg = segAt(t);
  if (!seg) return null;
  const srcT = seg.src + seg.rate * (t - seg.from);
  const i = srcIndex(srcT);
  const local = f - T(seg.from);

  if (seg.look === 'rewind') {
    return (
      <AbsoluteFill>
        <Vhs i={i} amount={1} f={f} />
        <Osd text="◀◀ REW" f={f} blink />
      </AbsoluteFill>
    );
  }

  let scale = 1, origin = '540px 960px', filter = 'saturate(1.12) contrast(1.04)';
  if (seg.look === 'hook') {
    scale = interpolate(local, [0, T(0.9375)], [1.0, 1.07], clamp);
  } else if (seg.look === 'hookzoom') {
    scale = interpolate(local, [0, 6], [1.65, 2.15], {...clamp, easing: EASE});
    origin = FACE_ORIGIN;
    filter = 'saturate(1.35) contrast(1.1)';
  } else if (seg.look === 'freeze') {
    scale = 2.15 + local * 0.002;
    origin = FACE_ORIGIN;
    filter = 'grayscale(.85) contrast(1.15)';
  } else if (seg.look === 'slowmo') {
    // A slow push into the play, the colour draining a little.
    scale = interpolate(local, [0, T(2.34)], [1.0, 1.12], clamp);
    origin = '540px 1250px';
    filter = 'saturate(.85) contrast(1.08)';
  } else if (seg.look === 'play' && seg.from >= S.goal) {
    // The goal: a punch in that settles.
    scale = 1 + 0.12 * Math.exp(-local / 10);
    origin = '540px 1200px';
    filter = 'saturate(1.25) contrast(1.06)';
  }
  const sh = shake(f, [0, 0.9375, S.goal, S.goal + BEAT_S * 2], seg.look === 'hookzoom' ? 22 : 16, 'fs');
  const vhsOut = seg.look === 'play' && seg.from === S.replay ? interpolate(local, [0, 14], [1, 0], clamp) : 0;
  return (
    <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
      <AbsoluteFill style={{transform: `translate(${sh.x}px, ${sh.y}px) rotate(${sh.r}deg) scale(${scale})`, transformOrigin: origin}}>
        <Frame i={i} style={{filter}} />
      </AbsoluteFill>
      {vhsOut > 0.01 && (
        <AbsoluteFill style={{opacity: vhsOut}}>
          <Vhs i={i} amount={vhsOut} f={f} />
        </AbsoluteFill>
      )}
      {seg.look === 'play' && seg.from === S.replay && local < T(0.85) && <Osd text="▶ PLAY" f={f} />}
      {seg.look === 'slowmo' && <Osd text="🐢 0.5x" f={f} />}
      {seg.look === 'slowmo' && (
        <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 45%, rgba(0,0,0,.6) 100%)'}} />
      )}
      {seg.look === 'freeze' && (
        <AbsoluteFill style={{background: 'rgba(0,0,0,.25)'}} />
      )}
    </AbsoluteFill>
  );
};

// ── captions and stickers ───────────────────────────────────────────────────

// The caption bar: white, heavy, a little crooked. On the clip it sits over
// the clip's own burned-in caption ("musty catching strays", y ~962), and is
// at least as wide, so that never shows through.
const Cap: React.FC<{
  from: number; to: number; children: React.ReactNode; y?: number; size?: number;
  tilt?: number; minWidth?: number; sub?: React.ReactNode; big?: boolean;
}> = ({from, to, children, y = 962, size = 64, tilt = -2, minWidth = 880, sub, big}) => {
  const f = useCurrentFrame();
  const a = T(from), b = T(to);
  if (f < a || f >= b) return null;
  const p = interpolate(f - a, [0, 9], [0, 1], {...clamp, easing: SPRING});
  const out = interpolate(f, [b - 4, b], [1, 0], clamp);
  const s = interpolate(p, [0, 1], [big ? 1.8 : 0.6, 1]) * interpolate(out, [0, 1], [0.85, 1]);
  const wob = Math.sin((f - a) / 9) * 0.6;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: y, display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
      <div
        style={{
          transform: `translateY(-50%) scale(${s}) rotate(${tilt + wob}deg)`,
          opacity: Math.min(p * 3, 1) * out,
          minWidth, maxWidth: 940, boxSizing: 'border-box', textAlign: 'center',
          padding: `${size * 0.26}px ${size * 0.5}px`, borderRadius: size * 0.36,
          background: '#fff', color: '#0A0A0C', border: '5px solid #0A0A0C',
          boxShadow: '0 12px 0 rgba(10,10,12,.55), 0 24px 60px rgba(0,0,0,.45)',
          fontFamily: SANS, fontWeight: 800, fontSize: size, lineHeight: 1.08, letterSpacing: '-0.035em',
        }}
      >
        {children}
        {sub && <div style={{fontFamily: MONO, fontWeight: 600, fontSize: size * 0.5, letterSpacing: '.04em', color: '#5a4f63', marginTop: 8}}>{sub}</div>}
      </div>
    </div>
  );
};

const Hi: React.FC<{children: React.ReactNode}> = ({children}) => (
  <span style={{background: 'linear-gradient(90deg,#F343FE,#C938FF 55%,#9847EE)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent'}}>{children}</span>
);

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

// A round sticker cut from one frame of the clip, around his face.
const FaceSticker: React.FC<{face: typeof FACE_LAUGH; at: number; until?: number; x: number; y: number; size: number; rot?: number; spin?: boolean}> = ({face, at, until, x, y, size, rot = -8, spin}) => {
  const f = useCurrentFrame();
  const a = T(at);
  if (f < a || (until !== undefined && f >= T(until))) return null;
  const p = interpolate(f - a, [0, 12], [0, 1], {...clamp, easing: SPRING});
  const k = size / (2 * face.r);
  const r = spin ? interpolate(p, [0, 1], [-200, rot]) : rot + Math.sin((f - a) / 8) * 4;
  return (
    <div style={{position: 'absolute', left: x - size / 2, top: y - size / 2, width: size, height: size, transform: `scale(${p}) rotate(${r}deg)`}}>
      <div style={{width: size, height: size, borderRadius: '50%', overflow: 'hidden', position: 'relative', border: '10px solid #fff',
        boxSizing: 'border-box', boxShadow: '0 18px 40px rgba(0,0,0,.5), 0 0 0 5px #0A0A0C'}}>
        <Img src={frameUrl(face.i)} style={{position: 'absolute', width: 1080 * k, height: 1920 * k, left: size / 2 - face.cx * k, top: size / 2 - face.cy * k}} />
      </div>
    </div>
  );
};

const Burst: React.FC<{at: number; until: number; x: number; y: number; text: string; rot?: number}> = ({at, until, x, y, text, rot = 8}) => {
  const f = useCurrentFrame();
  if (f < T(at) || f >= T(until)) return null;
  const p = interpolate(f - T(at), [0, 7], [0, 1], {...clamp, easing: SPRING});
  const jit = (random('b' + f) - 0.5) * 6;
  return (
    <div style={{position: 'absolute', left: x, top: y, transform: `translate(-50%,-50%) scale(${p}) rotate(${rot + jit}deg)`,
      padding: '18px 34px', background: '#FFE14D', color: '#0A0A0C', border: '6px solid #0A0A0C', borderRadius: 14,
      fontFamily: SANS, fontWeight: 800, fontSize: 70, letterSpacing: '-0.02em', boxShadow: '8px 8px 0 #0A0A0C'}}>
      {text}
    </div>
  );
};

// ── the pitch ───────────────────────────────────────────────────────────────

// Behind the pitch: the clip again, looping, blurred down to colour.
const Blurred: React.FC = () => {
  const f = useCurrentFrame();
  const t = f / FPS - S.pitch;
  const i = srcIndex((t * 0.8) % 4.2);
  return (
    <AbsoluteFill style={{background: C.base}}>
      <Frame i={i} style={{filter: 'blur(26px) brightness(.34) saturate(1.4)', transform: 'scale(1.15)'}} />
      <AbsoluteFill style={{background: 'radial-gradient(circle at 25% 25%, rgba(243,67,254,.22), transparent 50%), radial-gradient(circle at 80% 78%, rgba(152,71,238,.26), transparent 55%)'}} />
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 45%, rgba(0,0,0,.65) 100%)'}} />
    </AbsoluteFill>
  );
};

// Enter with a spring, leave quickly before the next block takes the stage.
const useInOut = (from: number, to: number, enter = 10) => {
  const f = useCurrentFrame();
  const a = T(from), b = T(to);
  const vis = f >= a && f < b;
  const p = interpolate(f - a, [0, enter], [0, 1], {...clamp, easing: SPRING});
  const out = interpolate(f, [b - 5, b], [1, 0], clamp);
  return {vis, p, out, f, a, b};
};

const Kicker: React.FC<{children: React.ReactNode; color?: string}> = ({children, color = C.ink}) => (
  <div style={{fontFamily: MONO, fontWeight: 600, fontSize: 46, letterSpacing: '.32em', marginRight: '-.32em', color, textTransform: 'uppercase'}}>{children}</div>
);

const Pitch: React.FC = () => {
  const {vis, p, out, f, a} = useInOut(S.pitch, S.pool, 8);
  if (!vis) return null;
  const sub = interpolate(f - T(S.pitch + BEAT_S), [0, 10], [0, 1], {...clamp, easing: SPRING});
  const live = 0.6 + 0.4 * Math.abs(Math.sin((f - a) / 9));
  return (
    <AbsoluteFill style={{alignItems: 'center', opacity: out}}>
      <div style={{position: 'absolute', top: 400, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 26,
        transform: `scale(${interpolate(p, [0, 1], [1.6, 1])})`, opacity: Math.min(1, p * 2)}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 16, padding: '14px 28px', borderRadius: 99, background: 'rgba(0,0,0,.55)',
          border: '2px solid rgba(74,222,128,.55)', fontFamily: MONO, fontWeight: 600, fontSize: 36, letterSpacing: '.2em', color: '#4ADE80'}}>
          <span style={{width: 22, height: 22, borderRadius: 99, background: '#4ADE80', opacity: live, boxShadow: '0 0 18px #4ADE80'}} />LIVE NOW
        </div>
        <Kicker>Duxxion&rsquo;s</Kicker>
        <div style={{fontFamily: SANS, fontWeight: 800, fontSize: 178, letterSpacing: '-0.05em', lineHeight: 0.9}}><Hi>Campaign</Hi></div>
      </div>
      <div style={{position: 'absolute', top: 905, fontFamily: SANS, fontWeight: 800, fontSize: 76, letterSpacing: '-0.03em', color: C.ink,
        transform: `translateY(${(1 - sub) * 40}px) scale(${0.9 + sub * 0.1})`, opacity: sub}}>
        clip him. win cash. 💸
      </div>
      <FaceSticker face={FACE_LAUGH} at={S.pitch + BEAT_S * 2} x={540} y={1300} size={380} rot={-7} spin />
      <Emoji at={S.pitch + BEAT_S * 3} x={790} y={1130} size={110} char="🤑" rot={12} />
    </AbsoluteFill>
  );
};

const Pool: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.pool, S.podium, 8);
  if (!vis) return null;
  const n = Math.round(interpolate(f, [T(S.pool), T(S.pool + 0.62)], [0, 500], {...clamp, easing: EASE}));
  const land = interpolate(f - T(S.pool + 0.62), [0, 8], [1.12, 1], {...clamp, easing: EASE});
  const sub = interpolate(f - T(S.pool + BEAT_S * 2), [0, 10], [0, 1], {...clamp, easing: SPRING});
  const coins = Array.from({length: 14}, (_, k) => k);
  return (
    <AbsoluteFill style={{alignItems: 'center', opacity: out}}>
      {coins.map((k) => {
        const start = T(S.pool) + k * 4;
        const d = f - start;
        if (d < 0) return null;
        // The side columns only: nothing falls across the words.
        const x = k % 2 ? 20 + random('cx' + k) * 170 : 870 + random('cx' + k) * 150;
        const y = -150 + d * (14 + random('cv' + k) * 10);
        return <div key={k} style={{position: 'absolute', zIndex: 0, left: x, top: y, fontSize: 84 + random('cs' + k) * 40, fontFamily: EMOJI,
          transform: `rotate(${d * (random('cr' + k) * 8 - 4)}deg)`, opacity: 0.9}}>{k % 3 ? '🪙' : '💵'}</div>;
      })}
      <div style={{position: 'absolute', zIndex: 2, top: 470, transform: `scale(${interpolate(p, [0, 1], [0.6, 1])})`, opacity: p}}>
        <Kicker color={C.ember}>Prize pool</Kicker>
      </div>
      <div style={{position: 'absolute', zIndex: 2, top: 560, fontFamily: SANS, fontWeight: 800, fontSize: 330, letterSpacing: '-0.06em', lineHeight: 1,
        transform: `scale(${land})`, background: 'linear-gradient(180deg,#FFE58A 0%,#F7A745 60%,#E07B1F 100%)', WebkitBackgroundClip: 'text',
        backgroundClip: 'text', color: 'transparent', filter: 'drop-shadow(0 16px 40px rgba(247,167,69,.45))'}}>
        ${n}
      </div>
      <div style={{position: 'absolute', zIndex: 2, top: 960, fontFamily: SANS, fontWeight: 800, fontSize: 68, color: C.ink, letterSpacing: '-0.03em', filter: 'drop-shadow(0 6px 18px rgba(0,0,0,.75))',
        opacity: sub, transform: `translateY(${(1 - sub) * 30}px)`, textAlign: 'center', lineHeight: 1.15}}>
        split between the<br /><Hi>top 3 clippers</Hi> 🏆
      </div>
    </AbsoluteFill>
  );
};

const PODIUM = [
  {place: '2ND', amount: 150, x: 225, h: 330, at: 0, grad: 'linear-gradient(180deg,#E8E4EE,#9C90A6)'},
  {place: '1ST', amount: 250, x: 540, h: 470, at: 1, grad: 'linear-gradient(180deg,#FFE58A,#F7A745 60%,#E07B1F)'},
  {place: '3RD', amount: 100, x: 855, h: 240, at: 2, grad: 'linear-gradient(180deg,#F2B48A,#B8693A)'},
];

const Podium: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.podium, S.views, 8);
  if (!vis) return null;
  const base = 1400;
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', top: 330, left: 0, right: 0, textAlign: 'center', opacity: p, transform: `scale(${interpolate(p, [0, 1], [0.7, 1])})`}}>
        <Kicker>Top 3 win</Kicker>
      </div>
      {PODIUM.map((c) => {
        const at = T(S.podium + c.at * BEAT_S);
        const q = interpolate(f - at, [0, 12], [0, 1], {...clamp, easing: SPRING});
        if (f < at) return null;
        const h = c.h * q;
        return (
          <React.Fragment key={c.place}>
            <div style={{position: 'absolute', left: c.x - 140, width: 280, top: base - h, height: h, background: c.grad, borderRadius: '22px 22px 0 0',
              border: '5px solid #0A0A0C', borderBottom: 'none', boxShadow: '0 0 60px rgba(201,56,255,.25)', display: 'flex', justifyContent: 'center'}}>
              <div style={{marginTop: 22, fontFamily: MONO, fontWeight: 600, fontSize: 46, letterSpacing: '.08em', color: '#0A0A0C', opacity: q}}>{c.place}</div>
            </div>
            <div style={{position: 'absolute', left: c.x - 200, width: 400, top: base - h - 140, textAlign: 'center', fontFamily: SANS, fontWeight: 800,
              fontSize: c.amount === 250 ? 112 : 92, letterSpacing: '-0.05em', color: C.ink, opacity: q, transform: `scale(${0.6 + 0.4 * q})`,
              textShadow: '0 10px 30px rgba(0,0,0,.6)'}}>
              ${c.amount}
            </div>
          </React.Fragment>
        );
      })}
      <Emoji at={S.podium + BEAT_S * 1.3} x={540} y={base - 470 - 220} size={120} char="👑" rot={-10} />
      <div style={{position: 'absolute', left: 0, right: 0, top: base, height: 14, background: '#0A0A0C'}} />
      <Cap from={S.podium + BEAT_S * 3} to={S.views} y={1490} size={50} minWidth={0} tilt={2}>(that&rsquo;s a lot of burritos 🌯)</Cap>
    </AbsoluteFill>
  );
};

const Views: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.views, S.steps, 8);
  if (!vis) return null;
  const v = Math.round(interpolate(f, [T(S.views + 0.2), T(S.views + 1.4)], [0, 1284503], {...clamp, easing: Easing2}));
  const i = srcIndex(((f - T(S.views)) / FPS) % 4.3 + 0.1);
  const phoneP = interpolate(f - T(S.views), [0, 12], [0, 1], {...clamp, easing: SPRING});
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', top: 300, left: 0, right: 0, textAlign: 'center', fontFamily: SANS, fontWeight: 800, fontSize: 92,
        letterSpacing: '-0.045em', lineHeight: 1.0, color: C.ink, opacity: p, transform: `scale(${interpolate(p, [0, 1], [1.4, 1])})`}}>
        most views on<br /><Hi>ONE clip</Hi> wins 👀
      </div>
      <div style={{position: 'absolute', left: 540 - 210, top: 560, width: 420, height: 746, borderRadius: 46, overflow: 'hidden', border: '10px solid #0A0A0C',
        boxShadow: '0 0 0 4px rgba(242,234,247,.25), 0 40px 90px rgba(0,0,0,.6)', transform: `translateY(${(1 - phoneP) * 300}px) rotate(${(1 - phoneP) * 8 + 3}deg)`}}>
        <Img src={frameUrl(i)} style={{position: 'absolute', width: 420, height: 746, left: 0, top: 0}} />
        <div style={{position: 'absolute', left: 16, right: 16, bottom: 18, padding: '12px 16px', borderRadius: 16, background: 'rgba(0,0,0,.72)',
          fontFamily: MONO, fontWeight: 600, fontSize: 30, color: '#fff', textAlign: 'center'}}>
          👁 {v.toLocaleString('en-US')}
        </div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 1360, textAlign: 'center', fontFamily: MONO, fontWeight: 600, fontSize: 38,
        letterSpacing: '.18em', color: C.ink2, opacity: phoneP}}>
        👆 YOUR CLIP
      </div>
    </AbsoluteFill>
  );
};
const Easing2 = (x: number) => 1 - Math.pow(1 - x, 3);

const STEPS = [
  {n: 1, at: S.steps, text: <>Join <b>Duxxion&rsquo;s campaign</b> on Highlightz</>},
  {n: 2, at: S.steps + 4 * BEAT_S, text: <>Clip his stream ✂️</>},
  {n: 3, at: S.steps + 8 * BEAT_S, text: <>Post it on TikTok or Reels with <b>#duxxion</b>, then add the link 🔗</>},
];

const Steps: React.FC = () => {
  const {vis, p, out, f} = useInOut(S.steps, S.cta, 8);
  if (!vis) return null;
  const close = T(S.steps + 10 * BEAT_S);
  const cq = interpolate(f - close, [0, 10], [0, 1], {...clamp, easing: SPRING});
  return (
    <AbsoluteFill style={{opacity: out}}>
      <div style={{position: 'absolute', top: 290, left: 0, right: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14,
        opacity: p, transform: `translateY(${(1 - p) * -30}px)`}}>
        <Kicker>How to join</Kicker>
        <div style={{fontFamily: SANS, fontWeight: 800, fontSize: 64, letterSpacing: '-0.03em', color: C.ink}}>it&rsquo;s <Hi>free</Hi> 🫶</div>
      </div>
      {STEPS.map((s, k) => {
        const q = interpolate(f - T(s.at), [0, 12], [0, 1], {...clamp, easing: SPRING});
        if (f < T(s.at)) return null;
        return (
          <div key={s.n} style={{position: 'absolute', left: 80, right: 80, top: 520 + k * 250, minHeight: 210, display: 'flex', alignItems: 'center', gap: 34,
            padding: '26px 36px', boxSizing: 'border-box', borderRadius: 34, background: 'rgba(14,11,17,.86)', border: `2px solid ${C.hair2}`,
            boxShadow: '0 30px 70px rgba(0,0,0,.5), 0 0 50px rgba(201,56,255,.15)', transform: `translateX(${(1 - q) * 700}px) rotate(${(1 - q) * 6}deg)`, opacity: Math.min(1, q * 2)}}>
            <div style={{flex: 'none', width: 110, height: 110, borderRadius: 99, display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'linear-gradient(180deg,#F343FE,#C938FF 50%,#9847EE)', fontFamily: SANS, fontWeight: 800, fontSize: 62, color: '#fff',
              boxShadow: '0 0 30px rgba(243,67,254,.55)'}}>{s.n}</div>
            <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 48, lineHeight: 1.15, letterSpacing: '-0.02em', color: C.ink}}>{s.text}</div>
          </div>
        );
      })}
      {f >= close && (
        <div style={{position: 'absolute', left: 0, right: 0, top: 1330, display: 'flex', justifyContent: 'center'}}>
          <div style={{padding: '20px 38px', borderRadius: 99, border: `3px solid ${C.ember}`, background: 'rgba(247,167,69,.12)', fontFamily: SANS,
            fontWeight: 800, fontSize: 52, color: C.ink, letterSpacing: '-0.02em', transform: `scale(${interpolate(cq, [0, 1], [1.5, 1])})`, opacity: cq,
            boxShadow: '0 0 40px rgba(247,167,69,.35)'}}>
            ⏰ Posting closes <span style={{color: C.ember}}>Fri, Nov 6</span>
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};

const Cta: React.FC = () => {
  const f = useCurrentFrame();
  const a = T(S.cta);
  if (f < a) return null;
  const l = f - a;
  const logo = interpolate(l, [0, 12], [0, 1], {...clamp, easing: SPRING});
  const l1 = interpolate(l, [6, 18], [0, 1], {...clamp, easing: SPRING});
  const l2 = interpolate(l, [T(BEAT_S), T(BEAT_S) + 12], [0, 1], {...clamp, easing: SPRING});
  const l3 = interpolate(l, [T(BEAT_S * 2), T(BEAT_S * 2) + 12], [0, 1], {...clamp, easing: EASE});
  const beat = FPS * BEAT_S;
  const pulse = Math.exp(-((l % beat) / beat) * 5);
  return (
    <AbsoluteFill style={{alignItems: 'center'}}>
      <div style={{position: 'absolute', inset: 0, opacity: 0.5 + pulse * 0.2}}><LogoGlow height={1500} opacity={0.5} /></div>
      <div style={{position: 'absolute', top: 330, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 34}}>
        <div style={{transform: `scale(${logo * (1 + pulse * 0.03)})`, filter: `drop-shadow(0 0 ${30 + pulse * 40}px rgba(243,67,254,.55))`}}>
          <LogoMark height={200} />
        </div>
        <div style={{fontFamily: MONO, fontWeight: 600, fontSize: 40, letterSpacing: '.42em', marginRight: '-.42em', color: C.ink, opacity: logo}}>HIGHLIGHTZ</div>
        <div style={{marginTop: 26, textAlign: 'center', fontFamily: SANS, fontWeight: 800, fontSize: 96, lineHeight: 1.0, letterSpacing: '-0.045em', color: C.ink,
          opacity: l1, transform: `translateY(${(1 - l1) * 40}px)`}}>
          Join <Hi>Duxxion&rsquo;s</Hi><br />campaign
        </div>
        <div style={{marginTop: 14, fontFamily: MONO, fontWeight: 600, fontSize: 60, color: C.ink, padding: '18px 38px', borderRadius: 20,
          background: 'rgba(247,167,69,.10)', border: `3px solid ${C.ember}`, boxShadow: `0 0 ${24 + pulse * 30}px rgba(247,167,69,${0.25 + pulse * 0.25})`,
          opacity: l2, transform: `scale(${interpolate(l2, [0, 1], [1.4, 1])})`}}>
          highlightz.app
        </div>
        <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 42, color: C.ink2, opacity: l3}}>free to join · $500 · top 3 win</div>
      </div>
      <FaceSticker face={FACE_LAUGH} at={S.cta + BEAT_S * 2} x={540} y={1265} size={230} rot={-10} spin />
      <Cap from={S.cta + BEAT_S * 2.4} to={S.end + 1} y={1455} size={46} minWidth={0} tilt={-3}>see you on the leaderboard 🫡</Cap>
    </AbsoluteFill>
  );
};

// ── the ad ──────────────────────────────────────────────────────────────────

export const DuxAd: React.FC = () => {
  loadFonts();
  const f = useCurrentFrame();
  const t = f / FPS;
  const onClip = t < S.pitch;
  const hit = (s: number, dur = 8, peak = 0.9) => {
    const d = f - T(s);
    return d >= 0 && d < dur ? interpolate(d, [0, dur], [peak, 0], {...clamp, easing: EASE}) : 0;
  };
  const flash = Math.max(hit(0, 8, 0.6), hit(S.goal, 12, 0.95), hit(S.pitch, 8, 0.8), hit(S.cta, 8, 0.6));
  const sh = shake(f, [S.pitch, S.pool + 0.62, S.cta], 14, 'gs');
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {onClip ? <Footage /> : (
        <AbsoluteFill style={{transform: `translate(${sh.x}px, ${sh.y}px)`}}>
          <Blurred />
          <Pitch />
          <Pool />
          <Podium />
          <Views />
          <Steps />
          <Cta />
        </AbsoluteFill>
      )}

      {/* The clip's captions: one is always up while the clip shows, so its own never peeks through. */}
      <Cap from={0} to={S.rewind} size={60} sub={undefined}>me when my <Hi>Duxxion clip</Hi><br />hits 1M views 📈</Cap>
      <Cap from={S.rewind} to={S.replay} tilt={2}>ok wait. let me explain 📼</Cap>
      <Cap from={S.replay} to={S.replay + 4 * BEAT_S} sub="(say it: DUCKS-ee-on)">meet <Hi>Duxxion</Hi> 👋</Cap>
      <Cap from={S.replay + 4 * BEAT_S} to={S.replay + 7 * BEAT_S} tilt={1.5}>watch him cook 🍳</Cap>
      <Cap from={S.replay + 7 * BEAT_S} to={S.goal} tilt={-1}>wait for it… ⏳</Cap>
      <Cap from={S.goal} to={S.goal + 2 * BEAT_S} size={96} big tilt={-4}>GOOOOAL 🚀</Cap>
      <Cap from={S.goal + 2 * BEAT_S} to={S.pitch} size={56} tilt={2}><span style={{whiteSpace: 'nowrap'}}>…then hits the <Hi>report</Hi> button 💀</span></Cap>
      <Burst at={AD.bleep[0]} until={S.pitch} x={830} y={300} text="*BLEEP*" />
      <Emoji at={S.replay + 0.1} until={S.replay + 4 * BEAT_S} x={905} y={420} size={120} char="👈" rot={-8} />
      {f >= T(S.goal) && f < T(S.goal + 0.5) && <Shockwave start={T(S.goal)} x={540} y={1250} size={900} />}

      {flash > 0 && <AbsoluteFill style={{background: '#fff', opacity: flash, mixBlendMode: 'screen'}} />}
      <Grain opacity={onClip ? 0.035 : 0.06} />
    </AbsoluteFill>
  );
};
