// 0:26.250-0:30.000 (2 bars). The lockup, the ask and the address, a pulse on
// the beats of the last bar, and the fade that ends with the music.
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C, EASE, FONT} from '../brand';
import {at, fr} from '../tl';
import {LogoGlow, LogoMark, Rise} from '../kit/Look';
import type {Layout} from '../Promo';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

export const Cta: React.FC<{layout: Layout; fadeFrom?: number; fadeTo?: number}> = ({layout, fadeFrom, fadeTo}) => {
  const f = useCurrentFrame();
  const tall = layout === 'tall';
  const l1 = at('cta.line1', 'cta');
  const l2 = at('cta.line2', 'cta');
  const l3 = at('cta.line3', 'cta');
  const pulseAt = at('cta.pulse', 'cta');
  const fadeA = fadeFrom ?? at('cta.fade', 'cta');
  const fadeB = fadeTo ?? fadeA + fr(1);
  const logoIn = interpolate(f, [0, 16], [0.85, 1], {...clamp, easing: EASE});
  const logoOp = interpolate(f, [0, 8], [0, 1], clamp);
  // One soft pulse per beat across the last bar.
  const beat = fr(1);
  const ph = f >= pulseAt ? ((f - pulseAt) % beat) / beat : 1;
  const pulse = f >= pulseAt ? Math.exp(-ph * 5) : 0;
  const fade = interpolate(f, [fadeA, fadeB], [1, 0], {...clamp, easing: EASE});
  const urlP = interpolate(f, [l2, l2 + 14], [0, 1], {...clamp, easing: EASE});
  const smallP = interpolate(f, [l3, l3 + 14], [0, 1], {...clamp, easing: EASE});
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', opacity: fade}}>
      <div style={{position: 'absolute', inset: 0, opacity: 0.55 + pulse * 0.25}}>
        <LogoGlow height={tall ? 1600 : 1200} opacity={0.5} />
      </div>
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: tall ? 54 : 34, textAlign: 'center'}}>
        <div style={{transform: `scale(${logoIn * (1 + pulse * 0.025)})`, opacity: logoOp, filter: `drop-shadow(0 0 ${30 + pulse * 40}px rgba(243,67,254,.55))`}}>
          <LogoMark height={tall ? 230 : 170} />
        </div>
        <div style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 40 : 30, letterSpacing: '.42em', color: C.ink, opacity: logoOp, marginRight: '-.42em'}}>HIGHLIGHTZ</div>
        <div style={{fontFamily: FONT.sans, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1.0, fontSize: tall ? 104 : 96, color: C.ink, marginTop: tall ? 30 : 16}}>
          <Rise text="Get your stream clipped." start={l1} gradientWords={[3]} />
        </div>
        <div
          style={{
            fontFamily: FONT.mono,
            fontWeight: 600,
            fontSize: tall ? 54 : 48,
            color: C.ink,
            opacity: urlP,
            transform: `translateY(${(1 - urlP) * 24}px)`,
            padding: tall ? '18px 34px' : '14px 30px',
            borderRadius: 18,
            background: 'rgba(247,167,69,.10)',
            border: `2px solid ${C.ember}`,
            boxShadow: `0 0 ${24 + pulse * 30}px rgba(247,167,69,${0.25 + pulse * 0.25})`,
          }}
        >
          highlightz.app<span style={{color: C.ember}}>/promote</span>
        </div>
        <div style={{fontFamily: FONT.sans, fontWeight: 500, fontSize: tall ? 40 : 34, color: C.ink2, opacity: smallP, transform: `translateY(${(1 - smallP) * 18}px)`}}>
          Clipper? Browse the campaigns.
        </div>
      </div>
    </AbsoluteFill>
  );
};
