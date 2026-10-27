// The hits: badge slam, callouts, flash, shockwave, the CLIP CAPTURED stamp,
// count-ups and directional motion blur.
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C, EASE, FONT, SPRING} from '../brand';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// "NEW · Campaigns", in the site's own badge style (.proof-new), slammed in.
export const NewBadge: React.FC<{start: number; size?: number}> = ({start, size = 22}) => {
  const f = useCurrentFrame() - start;
  if (f < 0) return null;
  const p = interpolate(f, [0, 9], [0, 1], {...clamp, easing: SPRING});
  const s = interpolate(p, [0, 1], [2.4, 1]);
  const rot = interpolate(p, [0, 1], [-9, 0]);
  const ring = interpolate(f, [0, 16], [0, 1], {...clamp, easing: EASE});
  return (
    <div style={{position: 'relative', display: 'inline-block'}}>
      <div
        style={{
          position: 'absolute',
          inset: -6,
          borderRadius: 99,
          border: `2px solid ${C.ember}`,
          transform: `scale(${1 + ring * 0.5})`,
          opacity: (1 - ring) * 0.8,
        }}
      />
      <div
        style={{
          transform: `scale(${s}) rotate(${rot}deg)`,
          opacity: Math.min(1, f / 3),
          display: 'flex',
          alignItems: 'center',
          gap: size * 0.5,
          padding: `${size * 0.5}px ${size * 0.95}px`,
          borderRadius: 99,
          background: 'rgba(0,0,0,.62)',
          border: `1px solid ${C.hair2}`,
          boxShadow: `0 0 ${40 * (1 - ring) + 18}px rgba(247,167,69,${0.35 * (1 - ring) + 0.12})`,
          fontFamily: FONT.mono,
          fontSize: size,
          letterSpacing: '.14em',
          textTransform: 'uppercase',
          color: C.ink,
          whiteSpace: 'nowrap',
        }}
      >
        <b style={{color: C.ember, fontWeight: 600}}>New</b>
        <span>Campaigns</span>
      </div>
    </div>
  );
};

export const Callout: React.FC<{start: number; text: string; size?: number; from?: 'right' | 'below'}> = ({start, text, size = 34, from = 'right'}) => {
  const f = useCurrentFrame() - start;
  if (f < 0) return null;
  const p = interpolate(f, [0, 16], [0, 1], {...clamp, easing: EASE});
  const pop = interpolate(f, [0, 8, 16], [0.92, 1.03, 1], {...clamp, easing: EASE});
  const dx = from === 'right' ? (1 - p) * 90 : 0;
  const dy = from === 'below' ? (1 - p) * 50 : 0;
  return (
    <div
      style={{
        transform: `translate(${dx}px, ${dy}px) scale(${pop})`,
        opacity: p,
        display: 'flex',
        alignItems: 'center',
        gap: size * 0.45,
        padding: `${size * 0.5}px ${size * 0.75}px`,
        borderRadius: size * 0.55,
        background: 'rgba(23,19,28,.82)',
        border: `1px solid ${C.hair2}`,
        boxShadow: '0 20px 60px rgba(0,0,0,.45)',
        fontFamily: FONT.sans,
        fontWeight: 700,
        fontSize: size,
        letterSpacing: '-0.02em',
        color: C.ink,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{width: size * 0.42, height: size * 0.42, borderRadius: 99, background: 'linear-gradient(180deg,#F343FE,#9847EE)', boxShadow: '0 0 16px rgba(243,67,254,.7)'}} />
      {text}
    </div>
  );
};

export const Flash: React.FC<{start: number; color?: string; dur?: number; peak?: number}> = ({start, color = '#fff', dur = 10, peak = 0.85}) => {
  const f = useCurrentFrame() - start;
  if (f < 0 || f > dur) return null;
  return <AbsoluteFill style={{background: color, opacity: interpolate(f, [0, dur], [peak, 0], {...clamp, easing: EASE}), mixBlendMode: 'screen'}} />;
};

export const Shockwave: React.FC<{start: number; x: number; y: number; size: number; dur?: number}> = ({start, x, y, size, dur = 26}) => {
  const f = useCurrentFrame() - start;
  if (f < 0 || f > dur) return null;
  const p = interpolate(f, [0, dur], [0, 1], {...clamp, easing: EASE});
  return (
    <>
      {[0, 0.18].map((lag, i) => {
        const q = Math.max(0, p - lag) / (1 - lag);
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x - size / 2,
              top: y - size / 2,
              width: size,
              height: size,
              borderRadius: '50%',
              border: `${10 * (1 - q) + 1.5}px solid ${i ? C.violet : C.magenta}`,
              boxShadow: `0 0 60px ${C.magenta}`,
              transform: `scale(${0.05 + q * 1.6})`,
              opacity: (1 - q) * 0.9,
            }}
          />
        );
      })}
    </>
  );
};

export const Stamp: React.FC<{start: number; text: string; size: number; end?: number}> = ({start, text, size, end}) => {
  const f = useCurrentFrame() - start;
  if (f < 0 || (end !== undefined && f > end - start)) return null;
  const p = interpolate(f, [0, 7], [0, 1], {...clamp, easing: SPRING});
  return (
    <div
      style={{
        transform: `scale(${interpolate(p, [0, 1], [1.9, 1])}) rotate(-6deg)`,
        opacity: Math.min(1, f / 2),
        padding: `${size * 0.32}px ${size * 0.6}px`,
        border: `${Math.max(4, size * 0.09)}px solid ${C.ember}`,
        borderRadius: size * 0.22,
        color: C.ember,
        fontFamily: FONT.mono,
        fontWeight: 600,
        fontSize: size,
        letterSpacing: '.1em',
        background: 'rgba(10,10,12,.55)',
        boxShadow: `0 0 50px rgba(247,167,69,.45), inset 0 0 30px rgba(247,167,69,.2)`,
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </div>
  );
};

export const countUp = (frame: number, start: number, dur: number, to: number) =>
  Math.round(interpolate(frame, [start, start + dur], [0, to], {...clamp, easing: EASE}));

// Directional motion blur. `amount` in px; the SVG filter id must be unique.
// `fill`: the blurred layer fills its parent. Needed when the children are
// absolutely positioned (a UI shot): a filter makes its element the
// containing block for them, and an unsized one collapses them to nothing.
export const MotionBlur: React.FC<{id: string; x?: number; y?: number; fill?: boolean; children: React.ReactNode; style?: React.CSSProperties}> = ({id, x = 0, y = 0, fill, children, style}) => (
  <div style={style}>
    <svg width="0" height="0" style={{position: 'absolute'}}>
      <filter id={id} x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation={`${Math.abs(x)} ${Math.abs(y)}`} />
      </filter>
    </svg>
    <div style={{...(fill ? {position: 'absolute', inset: 0} : {}), filter: Math.abs(x) + Math.abs(y) > 0.3 ? `url(#${id})` : undefined}}>{children}</div>
  </div>
);

// A glass card for anything that floats over the zoomed UI in the 9:16 cut,
// so the overlay never has to be read against the page beneath it.
export const Hud: React.FC<{children: React.ReactNode; style?: React.CSSProperties}> = ({children, style}) => (
  <div
    style={{
      padding: '26px 30px',
      borderRadius: 28,
      background: 'rgba(14,11,17,.88)',
      border: `1px solid ${C.hair2}`,
      boxShadow: '0 30px 80px rgba(0,0,0,.55), 0 0 60px rgba(201,56,255,.16)',
      backdropFilter: 'blur(14px)',
      ...style,
    }}
  >
    {children}
  </div>
);
