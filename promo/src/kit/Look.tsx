// The look: plum-black ground with the logo's magenta and violet glowing
// through it, film grain over everything, and the logo itself.
import React from 'react';
import {AbsoluteFill, Img, interpolate, random, staticFile, useCurrentFrame} from 'remotion';
import {C, EASE} from '../brand';

export const Backdrop: React.FC<{intensity?: number; drift?: number}> = ({intensity = 1, drift = 1}) => {
  const f = useCurrentFrame();
  const x1 = 22 + Math.sin(f / 140) * 6 * drift;
  const y1 = 30 + Math.cos(f / 170) * 5 * drift;
  const x2 = 78 + Math.cos(f / 155) * 6 * drift;
  const y2 = 72 + Math.sin(f / 190) * 5 * drift;
  return (
    <AbsoluteFill style={{background: C.base}}>
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at ${x1}% ${y1}%, rgba(243,67,254,${0.2 * intensity}) 0%, transparent 45%),
            radial-gradient(circle at ${x2}% ${y2}%, rgba(152,71,238,${0.22 * intensity}) 0%, transparent 50%)`,
        }}
      />
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,.6) 100%)'}} />
    </AbsoluteFill>
  );
};

// Film grain: eight tiles, changed every other frame (30 Hz reads as film).
export const Grain: React.FC<{opacity?: number}> = ({opacity = 0.06}) => {
  const f = Math.floor(useCurrentFrame() / 2);
  const ox = Math.floor(random('gx' + f) * 512);
  const oy = Math.floor(random('gy' + f) * 512);
  return (
    <AbsoluteFill
      style={{
        backgroundImage: `url(${staticFile(`kit/grain-${f % 8}.png`)})`,
        backgroundPosition: `${ox}px ${oy}px`,
        mixBlendMode: 'overlay',
        opacity,
        pointerEvents: 'none',
      }}
    />
  );
};

const LOGO = staticFile('brand/logo-mark.png');
const LOGO_RATIO = 374 / 501;

// The H play-mark. `glitch` 0..1: channel split and sliced displacement, the
// way it arrives in the hook; 0 is the clean mark.
export const LogoMark: React.FC<{height: number; glitch?: number; seed?: string}> = ({height, glitch = 0, seed = 'g'}) => {
  const f = useCurrentFrame();
  const w = height * LOGO_RATIO;
  const base: React.CSSProperties = {position: 'absolute', left: 0, top: 0, width: w, height};
  if (glitch <= 0.001) {
    return (
      <div style={{position: 'relative', width: w, height}}>
        <Img src={LOGO} style={base} />
      </div>
    );
  }
  const off = (k: string) => (random(seed + k + f) - 0.5) * 2 * glitch * height * 0.09;
  const slices = [0, 1, 2, 3, 4].map((i) => {
    const top = random(seed + 's' + i + Math.floor(f / 2)) * 90;
    const h = 4 + random(seed + 'h' + i + f) * 14;
    return {top, h, dx: off('x' + i) * 1.6};
  });
  return (
    <div style={{position: 'relative', width: w, height}}>
      <svg width="0" height="0" style={{position: 'absolute'}}>
        <filter id="hzR"><feColorMatrix type="matrix" values="1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0" /></filter>
        <filter id="hzB"><feColorMatrix type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0" /></filter>
        <filter id="hzG"><feColorMatrix type="matrix" values="0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0" /></filter>
      </svg>
      {(['hzR', 'hzG', 'hzB'] as const).map((id, i) => (
        <Img
          key={id}
          src={LOGO}
          style={{
            ...base,
            filter: `url(#${id})`,
            mixBlendMode: 'screen',
            transform: `translate(${off(id) * (i - 1)}px, ${off(id + 'y') * 0.2}px)`,
            opacity: 1,
          }}
        />
      ))}
      {slices.map((s, i) => (
        <Img
          key={i}
          src={LOGO}
          style={{
            ...base,
            clipPath: `inset(${s.top}% 0 ${Math.max(0, 100 - s.top - s.h)}% 0)`,
            transform: `translateX(${s.dx}px)`,
            opacity: 0.9,
          }}
        />
      ))}
    </div>
  );
};

// The giant soft copy of the mark behind the lockup, as on the OG card.
export const LogoGlow: React.FC<{height: number; opacity: number}> = ({height, opacity}) => (
  <Img
    src={LOGO}
    style={{
      position: 'absolute',
      height,
      width: height * LOGO_RATIO,
      left: '50%',
      top: '50%',
      transform: 'translate(-50%, -50%)',
      filter: `blur(${height * 0.09}px)`,
      opacity,
    }}
  />
);

// Words rising out of a mask, one after another.
export const Rise: React.FC<{
  text: string;
  start: number;
  stagger?: number;
  dur?: number;
  style?: React.CSSProperties;
  gradientWords?: number[];
}> = ({text, start, stagger = 3, dur = 16, style, gradientWords = []}) => {
  const f = useCurrentFrame();
  const words = text.split(' ');
  return (
    <span style={{display: 'inline', ...style}}>
      {words.map((w, i) => {
        const p = interpolate(f, [start + i * stagger, start + i * stagger + dur], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: EASE,
        });
        const grad = gradientWords.includes(i);
        // The space sits OUTSIDE the clipping box: trailing white space inside
        // an inline-block collapses, which ran the words together.
        return (
          <React.Fragment key={i}>
            <span style={{display: 'inline-block', overflow: 'hidden', verticalAlign: 'bottom', paddingBottom: '0.1em', marginBottom: '-0.1em'}}>
              <span
                style={{
                  display: 'inline-block',
                  transform: `translateY(${(1 - p) * 105}%)`,
                  opacity: p > 0 ? 1 : 0,
                  ...(grad
                    ? {background: 'linear-gradient(90deg,#F343FE,#C938FF 55%,#9847EE)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent'}
                    : {}),
                }}
              >
                {w}
              </span>
            </span>
            {i < words.length - 1 ? ' ' : null}
          </React.Fragment>
        );
      })}
    </span>
  );
};
