// 0:00.000-0:01.875 (1 bar). The mark glitches in with a gradient flare on the
// downbeat; the site's headline rises on beat 2.
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C, EASE, FONT} from '../brand';
import {at} from '../tl';
import {LogoGlow, LogoMark, Rise} from '../kit/Look';
import type {Layout} from '../Promo';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

export const Hook: React.FC<{layout: Layout}> = ({layout}) => {
  const f = useCurrentFrame();
  const tall = layout === 'tall';
  const head = at('hook.headline', 'hook');
  const logoH = tall ? 300 : 210;
  const glitch = interpolate(f, [0, 3, 12, 17], [1, 0.85, 0.3, 0], clamp);
  const s = interpolate(f, [0, 20], [1.16, 1], {...clamp, easing: EASE});
  const lift = interpolate(f, [head - 4, head + 14], [0, tall ? -40 : -70], {...clamp, easing: EASE});
  const flare = interpolate(f, [4, 16, 60], [0, 1, 0.45], {...clamp, easing: EASE});
  const glow = interpolate(f, [0, 22], [0, 0.55], {...clamp, easing: EASE});
  const size = tall ? 112 : 104;
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <div style={{position: 'absolute', inset: 0, opacity: glow}}>
        <LogoGlow height={tall ? 1500 : 1100} opacity={0.5} />
      </div>
      <div style={{transform: `translateY(${lift}px)`, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: tall ? 70 : 46}}>
        <div style={{position: 'relative', transform: `scale(${s})`}}>
          {/* The flare: a horizontal streak through the mark. */}
          <div
            style={{
              position: 'absolute',
              left: '50%',
              top: '48%',
              width: (tall ? 1000 : 1500) * flare,
              height: 5,
              transform: 'translate(-50%,-50%)',
              background: 'linear-gradient(90deg, transparent, #F343FE 30%, #fff 50%, #9847EE 70%, transparent)',
              filter: 'blur(2px)',
              opacity: flare,
              boxShadow: '0 0 40px 8px rgba(243,67,254,.55)',
            }}
          />
          <LogoMark height={logoH} glitch={glitch} />
        </div>
        <div
          style={{
            fontFamily: FONT.sans,
            fontWeight: 800,
            letterSpacing: '-0.04em',
            lineHeight: 1.0,
            fontSize: size,
            color: C.ink,
            textAlign: 'center',
            // A filter, not text-shadow: the words' clipping boxes would cut
            // a text-shadow into visible dark rectangles.
            filter: 'drop-shadow(0 4px 30px rgba(0,0,0,.55))',
            maxWidth: tall ? 960 : 1500,
          }}
        >
          <div><Rise text="Every big moment," start={head} /></div>
          <div><Rise text="already a clip." start={head + 8} gradientWords={[2]} /></div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
