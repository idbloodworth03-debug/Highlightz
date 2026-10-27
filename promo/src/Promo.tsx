// The 30-second promo: six scenes, each starting on a bar line of the 128 BPM
// grid (src/timeline.json), over the mix in public/audio.
import React from 'react';
import {AbsoluteFill, Html5Audio, Sequence, staticFile} from 'remotion';
import {C} from './brand';
import {missing} from './data';
import {loadFonts} from './kit/fonts';
import {Backdrop, Grain} from './kit/Look';
import {PreviewMark} from './kit/Frame';
import {sceneRange, TEASER_SCENES, fr} from './tl';
import {Hook} from './scenes/Hook';
import {Landing} from './scenes/Landing';
import {Bot} from './scenes/Bot';
import {Slides} from './scenes/Slides';
import {Campaign} from './scenes/Campaign';
import {Cta} from './scenes/Cta';

export type Layout = 'wide' | 'tall';

loadFonts();

const SCENES: [string, React.FC<{layout: Layout}>][] = [
  ['hook', Hook],
  ['landing', Landing],
  ['bot', Bot],
  ['slides', Slides],
  ['campaign', Campaign],
  ['cta', Cta],
];

export const Promo: React.FC<{layout: Layout}> = ({layout}) => (
  <AbsoluteFill style={{background: C.base}}>
    <Backdrop />
    {SCENES.map(([id, Scene]) => {
      const r = sceneRange(id);
      return (
        <Sequence key={id} from={r.from} durationInFrames={r.dur} name={id}>
          <Scene layout={layout} />
        </Sequence>
      );
    })}
    <Grain />
    <PreviewMark lines={missing()} />
    <Html5Audio src={staticFile('audio/promo-30s.wav')} />
  </AbsoluteFill>
);

// The 12-second teaser: the hook, the live scoring, the prize pool, the
// CTA. Each part is the promo's own scene played from where that beat starts.
export const Teaser: React.FC<{layout: Layout}> = ({layout}) => {
  const byId: Record<string, React.FC<{layout: Layout}>> = {hook: Hook, capture: Bot, campaign: Campaign};
  return (
    <AbsoluteFill style={{background: C.base}}>
      <Backdrop />
      {TEASER_SCENES.map((s) => {
        const from = fr(s.from);
        const dur = s.id === 'cta' ? 720 - from : fr(s.to) - from;
        const Scene = byId[s.id];
        return (
          <Sequence key={s.id} from={from} durationInFrames={dur} name={s.id}>
            {s.id === 'cta' ? (
              // The tail: hold, then fade from the last hit (beat 24) to 12.0 s.
              <Cta layout={layout} fadeFrom={fr(24) - from} fadeTo={720 - from} />
            ) : (
              <Scene layout={layout} />
            )}
          </Sequence>
        );
      })}
      <Grain />
      <PreviewMark lines={missing()} />
      <Html5Audio src={staticFile('audio/teaser-12s.wav')} />
    </AbsoluteFill>
  );
};
