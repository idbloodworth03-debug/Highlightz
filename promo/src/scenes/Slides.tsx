// 0:16.875-0:20.625 (2 bars). How campaigns work: four cards, two beats each,
// pushed in and out with motion blur. Text sized for a phone.
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, EASE, FONT, IN_OUT} from '../brand';
import {DATA, money} from '../data';
import {at, fr} from '../tl';
import {MotionBlur} from '../kit/Fx';
import type {Layout} from '../Promo';

const Icon: React.FC<{k: number; size: number}> = ({k, size}) => {
  const p = {fill: 'none', stroke: 'url(#hzg)', strokeWidth: 2.4, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const};
  return (
    <svg width={size} height={size} viewBox="0 0 48 48">
      <defs>
        <linearGradient id="hzg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#F343FE" />
          <stop offset=".5" stopColor="#C938FF" />
          <stop offset="1" stopColor="#9847EE" />
        </linearGradient>
      </defs>
      {k === 0 && (
        <g {...p}>
          <ellipse cx="24" cy="12" rx="14" ry="5" />
          <path d="M10 12v8c0 2.8 6.3 5 14 5s14-2.2 14-5v-8M10 20v8c0 2.8 6.3 5 14 5s14-2.2 14-5v-8M10 28v8c0 2.8 6.3 5 14 5s14-2.2 14-5v-8" />
        </g>
      )}
      {k === 1 && (
        <g {...p}>
          <path d="M16 8h16v10a8 8 0 01-16 0z" />
          <path d="M16 11h-5a5 5 0 005 7M32 11h5a5 5 0 01-5 7M24 26v7M17 40h14M19 40l1-7h8l1 7" />
        </g>
      )}
      {k === 2 && (
        <g {...p}>
          <path d="M8 40h32M12 40V28M21 40V20M30 40V24M39 40V12" />
          <path d="M33 10l6-3 1 7" />
        </g>
      )}
      {k === 3 && (
        <g {...p}>
          <rect x="6" y="13" width="36" height="22" rx="4" />
          <circle cx="24" cy="24" r="5" />
          <path d="M12 19v0M36 29v0M30 39l4 4 8-9" />
        </g>
      )}
    </svg>
  );
};

export const Slides: React.FC<{layout: Layout}> = ({layout}) => {
  const f = useCurrentFrame();
  const {width} = useVideoConfig();
  const tall = layout === 'tall';
  const br = DATA.budget_range;
  const cards = [
    {line: 'Set your budget.', sub: `${money(br.min)} to ${money(br.max)}`},
    {line: 'Choose prizes + rules.', sub: ''},
    {line: 'Clippers compete.', sub: ''},
    {line: 'Winners get paid.', sub: ''},
  ];
  const starts = [at('slides.1', 'slides'), at('slides.2', 'slides'), at('slides.3', 'slides'), at('slides.4', 'slides')];
  const len = fr(2);
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      {cards.map((c, i) => {
        const s = starts[i];
        const local = f - s;
        if (local < -1 || local > len + 10) return null;
        const IN = 11;
        const OUT = 10;
        const pIn = interpolate(local, [0, IN], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: EASE});
        const pOut = i === cards.length - 1 ? 0 : interpolate(local, [len - 2, len - 2 + OUT], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: IN_OUT});
        const x = pIn * width * 0.75 - pOut * width * 0.75;
        // Blur follows speed: the derivative of the push, in pixels per frame.
        const vIn = local >= 0 && local < IN ? (interpolate(local - 1, [0, IN], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: EASE}) - pIn) * width * 0.75 : 0;
        const vOut = pOut > 0 && pOut < 1 ? width * 0.75 * 0.12 : 0;
        const blur = Math.min(40, (vIn + vOut) * 0.45);
        const cw = tall ? 940 : 1180;
        return (
          <MotionBlur key={i} id={`mb-slide-${i}`} x={blur} style={{position: 'absolute', transform: `translateX(${x}px)`}}>
            <div
              style={{
                width: cw,
                padding: tall ? '70px 64px' : '64px 76px',
                borderRadius: 40,
                background: 'linear-gradient(180deg, rgba(36,28,44,.92), rgba(18,15,22,.94))',
                border: `1px solid ${C.hair2}`,
                boxShadow: '0 40px 120px rgba(0,0,0,.6), 0 0 80px rgba(201,56,255,.18)',
                display: 'flex',
                flexDirection: tall ? 'column' : 'row',
                alignItems: tall ? 'flex-start' : 'center',
                gap: tall ? 44 : 56,
              }}
            >
              <div style={{flexShrink: 0, width: tall ? 150 : 170, height: tall ? 150 : 170, borderRadius: 36, background: 'rgba(201,56,255,.10)', border: `1px solid ${C.hair2}`, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
                <Icon k={i} size={tall ? 104 : 116} />
              </div>
              <div style={{display: 'flex', flexDirection: 'column', gap: 18}}>
                <div style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 30 : 26, letterSpacing: '.16em', color: C.ink3}}>
                  {String(i + 1).padStart(2, '0')} / 04
                </div>
                <div style={{fontFamily: FONT.sans, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1.0, fontSize: tall ? 100 : 92, color: C.ink}}>{c.line}</div>
                {c.sub && <div style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 52 : 46, color: C.ember}}>{c.sub}</div>}
              </div>
            </div>
          </MotionBlur>
        );
      })}
    </AbsoluteFill>
  );
};
