// 0:01.875-0:07.500 (3 bars). The real landing page scrolling from the hero
// to "Get your stream clipped.", the NEW badge slammed on the bar line, and
// three callouts landing on beats.
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {cap} from '../data';
import {at, fr, sceneRange} from '../tl';
import {Callout, NewBadge} from '../kit/Fx';
import {UiShot} from '../kit/UiShot';
import type {Layout} from '../Promo';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const LINES = ['7 live signals, one score', '10 streams at once', 'Free to start'];
const WHY = 'Recorded from the live site once this session can reach highlightz.app.';
const TITLE = 'highlightz.app: hero → What it catches → Ten streams live → Get your stream clipped.';

export const Landing: React.FC<{layout: Layout}> = ({layout}) => {
  const f = useCurrentFrame();
  const tall = layout === 'tall';
  const dur = sceneRange('landing').dur;
  const starts = [at('landing.callout1', 'landing'), at('landing.callout2', 'landing'), at('landing.callout3', 'landing')];
  const whip = at('landing.whip', 'landing');
  // The whip past pricing: vertical blur peaking mid-beat (only on real footage).
  const blurY = cap('landing', layout) ? interpolate(f, [whip - 2, whip + fr(0.5), whip + fr(1)], [0, 26, 0], clamp) : 0;
  if (tall) {
    return (
      <AbsoluteFill>
        <UiShot name="landing" layout={layout} dur={dur} push={1} focusY={1180} focusWidth={900} tilt={-4} blurY={blurY} title={TITLE} why={WHY} />
        <div style={{position: 'absolute', top: 150, left: 0, right: 0, display: 'flex', justifyContent: 'center'}}>
          <NewBadge start={0} size={30} />
        </div>
        <div style={{position: 'absolute', left: 64, top: 260, display: 'flex', flexDirection: 'column', gap: 18}}>
          {LINES.map((t, i) => <Callout key={t} start={starts[i]} text={t} size={40} />)}
        </div>
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill>
      <UiShot name="landing" layout={layout} dur={dur} push={1} tilt={9} blurY={blurY} title={TITLE} why={WHY} />
      <div style={{position: 'absolute', left: 1300, top: 190}}>
        <NewBadge start={0} size={24} />
      </div>
      <div style={{position: 'absolute', left: 1290, top: 380, display: 'flex', flexDirection: 'column', gap: 26}}>
        {LINES.map((t, i) => <Callout key={t} start={starts[i]} text={t} size={32} />)}
      </div>
    </AbsoluteFill>
  );
};
