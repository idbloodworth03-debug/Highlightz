import React from 'react';
import {Composition} from 'remotion';
import {Promo, Teaser} from './Promo';
import {FPS, PROMO_FRAMES, TEASER_FRAMES} from './tl';

export const Root: React.FC = () => (
  <>
    <Composition id="Promo16x9" component={Promo} width={1920} height={1080} fps={FPS} durationInFrames={PROMO_FRAMES} defaultProps={{layout: 'wide' as const}} />
    <Composition id="Promo9x16" component={Promo} width={1080} height={1920} fps={FPS} durationInFrames={PROMO_FRAMES} defaultProps={{layout: 'tall' as const}} />
    <Composition id="Teaser12" component={Teaser} width={1920} height={1080} fps={FPS} durationInFrames={TEASER_FRAMES} defaultProps={{layout: 'wide' as const}} />
    <Composition id="Teaser12x9x16" component={Teaser} width={1080} height={1920} fps={FPS} durationInFrames={TEASER_FRAMES} defaultProps={{layout: 'tall' as const}} />
  </>
);
