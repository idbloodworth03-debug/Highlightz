// One real UI shot, framed for the layout:
//   wide: a browser window, pushing in slowly toward the shot's focus region;
//   tall: the real phone layout, zoomed like a camera so the focus region
//         fills the width and its text stays readable on a phone.
import React from 'react';
import {Freeze, interpolate, useCurrentFrame} from 'remotion';
import {EASE, IN_OUT} from '../brand';
import {cap, type Rect, type ShotName} from '../data';
import {BrowserFrame, CaptureVideo, CHROME_H, Placeholder} from './Frame';
import {PHONE_VIEWPORT, PhoneFrame} from './Phone';
import {MotionBlur} from './Fx';
import type {Layout} from '../Promo';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
export const WIDE_BOX = {left: 70, top: 150, width: 1180};

// Where a shot's focus region lands on the canvas (ignoring the slight tilt),
// for effects that must hit the real UI: the shockwave at the crossing.
export const focusPoint = (name: ShotName, layout: Layout, focusY = 1000): {x: number; y: number} => {
  const c = cap(name, layout);
  if (layout === 'tall') return {x: 540, y: focusY};
  const vp = c ? c.viewport : {w: 1440, h: 900};
  const r: Rect = (c && c.focus) || {x: 0, y: 0, w: vp.w, h: vp.h};
  const bodyH = WIDE_BOX.width / (vp.w / vp.h);
  return {
    x: WIDE_BOX.left + ((r.x + r.w / 2) / vp.w) * WIDE_BOX.width,
    y: WIDE_BOX.top + CHROME_H + ((r.y + r.h / 2) / vp.h) * bodyH,
  };
};

export const UiShot: React.FC<{
  name: ShotName;
  layout: Layout;
  title: string;
  why?: string;
  tilt?: number;
  dur: number;                   // the shot's length in frames (drives the push)
  push?: number;                 // wide: how far to push in over the shot (1 = none)
  // Which capture frame to show at each frame of the shot: holds and jumps
  // (the crossing's one-beat freeze) without cutting the camera move.
  frameMap?: (f: number) => number;
  // tall: where the focus region sits on the 1080x1920 canvas (its centre y)
  focusY?: number;
  focusWidth?: number;           // tall: canvas px the focus region spans
  wideBox?: {left: number; top: number; width: number};
  blurY?: number;                // vertical motion blur on the page (a whip scroll)
}> = ({name, layout, title, why, tilt = 4, dur, push = 1.12, frameMap, focusY = 1000, focusWidth = 900, wideBox = WIDE_BOX, blurY = 0}) => {
  const f = useCurrentFrame();
  const durationInFrames = dur;
  const c = cap(name, layout);
  const enter = interpolate(f, [0, 12], [0, 1], {...clamp, easing: EASE});
  const t = interpolate(f, [0, durationInFrames], [0, 1], {...clamp, easing: IN_OUT});
  const reason = why || 'Recorded from the real dashboard (demo account) once this session can reach highlightz.app.';
  const video = c ? (
    <MotionBlur id={'mb-' + name + '-' + layout} y={blurY} style={{position: 'absolute', inset: 0}}>
      <div style={{position: 'absolute', inset: 0}}>
        <Freeze frame={Math.max(0, Math.min(c.frames - 1, frameMap ? frameMap(f) : f))}>
          <CaptureVideo cap={c} />
        </Freeze>
      </div>
    </MotionBlur>
  ) : null;

  if (layout === 'tall') {
    const vp = c ? c.viewport : PHONE_VIEWPORT;
    const focus: Rect = (c && c.focus) || {x: 0, y: 160, w: vp.w, h: 420};
    // Scale so the focus spans focusWidth, then place its centre at focusY.
    const scale = Math.min(focusWidth / focus.w, 3);
    const inner = vp.w * scale;
    const phoneW = inner / (1 - 0.07);
    const bezel = phoneW * 0.035;
    const fx = bezel + (focus.x + focus.w / 2) * scale;
    const fy = bezel + (focus.y + focus.h / 2) * scale;
    const drift = interpolate(t, [0, 1], [0, -18]);
    return (
      <div
        style={{
          position: 'absolute',
          left: 540 - fx,
          top: focusY - fy + drift,
          opacity: enter,
          transform: `scale(${0.96 + enter * 0.04})`,
          transformOrigin: `${fx}px ${fy}px`,
        }}
      >
        <PhoneFrame width={phoneW} tilt={-tilt * 0.4}>
          {video || <Placeholder compact title={title} why={reason} />}
        </PhoneFrame>
      </div>
    );
  }

  const vp = c ? c.viewport : {w: 1440, h: 900};
  const focus: Rect = (c && c.focus) || {x: 0, y: 0, w: vp.w, h: vp.h};
  const ox = ((focus.x + focus.w / 2) / vp.w) * 100;
  const oy = ((focus.y + focus.h / 2) / vp.h) * 100;
  const z = interpolate(t, [0, 1], [1, push]);
  return (
    <div style={{position: 'absolute', left: wideBox.left, top: wideBox.top, opacity: enter, transform: `scale(${0.96 + enter * 0.04})`}}>
      <BrowserFrame width={wideBox.width} aspect={vp.w / vp.h} tiltY={interpolate(t, [0, 1], [tilt, tilt * 0.4])} tiltX={2}>
        <div style={{position: 'absolute', inset: 0, transform: `scale(${z})`, transformOrigin: `${ox}% ${oy}%`}}>
          {video || <Placeholder title={title} why={reason} />}
        </div>
      </BrowserFrame>
    </div>
  );
};
