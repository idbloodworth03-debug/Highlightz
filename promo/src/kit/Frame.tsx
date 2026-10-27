// The browser window the real UI is shown in, the stand-in for a capture that
// does not exist yet, and the preview watermark.
import React from 'react';
import {AbsoluteFill, OffthreadVideo, staticFile, useCurrentFrame} from 'remotion';
import {C, FONT} from '../brand';
import type {Capture} from '../data';

export const CHROME_H = 46;

export const BrowserFrame: React.FC<{
  width: number;
  aspect?: number;               // body width / height (the capture viewport)
  tiltX?: number;
  tiltY?: number;
  scale?: number;
  url?: string;
  glow?: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({width, aspect = 1440 / 900, tiltX = 0, tiltY = 0, scale = 1, url = 'highlightz.app', glow = 0.35, children, style}) => {
  const bodyH = width / aspect;
  return (
    <div style={{perspective: 2600, ...style}}>
      <div
        style={{
          width,
          transform: `rotateX(${tiltX}deg) rotateY(${tiltY}deg) scale(${scale})`,
          transformStyle: 'preserve-3d',
          borderRadius: 16,
          overflow: 'hidden',
          background: '#120F16',
          boxShadow: `0 50px 120px rgba(0,0,0,.65), 0 0 0 1px ${C.hair2}, 0 0 90px rgba(201,56,255,${glow * 0.45})`,
        }}
      >
        <div style={{height: CHROME_H, display: 'flex', alignItems: 'center', gap: 9, padding: '0 18px', background: '#17131C', borderBottom: `1px solid ${C.hair}`}}>
          {['#FF5F57', '#FEBC2E', '#28C840'].map((c) => (
            <span key={c} style={{width: 12, height: 12, borderRadius: 6, background: c, opacity: 0.9}} />
          ))}
          <div
            style={{
              marginLeft: 18,
              flex: 1,
              maxWidth: width * 0.5,
              height: 28,
              borderRadius: 8,
              background: 'rgba(255,255,255,.06)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '0 12px',
              color: C.ink2,
              fontFamily: FONT.sans,
              fontSize: 15,
            }}
          >
            <svg width="11" height="13" viewBox="0 0 11 13"><rect x="1" y="5.5" width="9" height="7" rx="1.5" fill={C.ink3} /><path d="M3 5.5V4a2.5 2.5 0 015 0v1.5" stroke={C.ink3} strokeWidth="1.4" fill="none" /></svg>
            {url}
          </div>
        </div>
        <div style={{width, height: bodyH, position: 'relative', overflow: 'hidden', background: C.base}}>{children}</div>
      </div>
    </div>
  );
};

// A real capture, frame-locked: frame N of the shot is frame N of the file.
export const CaptureVideo: React.FC<{cap: Capture; startFrom?: number; playbackRate?: number}> = ({cap, startFrom = 0, playbackRate = 1}) => (
  <OffthreadVideo
    src={staticFile(cap.file)}
    startFrom={startFrom}
    playbackRate={playbackRate}
    muted
    style={{width: '100%', height: '100%', objectFit: 'cover'}}
  />
);

// Where a real capture goes until it exists. Deliberately looks like a gap,
// not like a screen: the video must never pass a stand-in off as the product.
export const Placeholder: React.FC<{title: string; why: string; compact?: boolean}> = ({title, why, compact}) => (
  <AbsoluteFill
    style={{
      background: 'repeating-linear-gradient(45deg, #15111A 0 22px, #1A1520 22px 44px)',
      alignItems: 'center',
      justifyContent: 'center',
      textAlign: 'center',
      padding: 40,
      gap: 14,
    }}
  >
    <div style={{fontFamily: FONT.mono, fontWeight: 600, letterSpacing: '.18em', fontSize: compact ? 16 : 20, color: C.ember}}>
      REAL CAPTURE GOES HERE
    </div>
    <div style={{fontFamily: FONT.sans, fontWeight: 700, fontSize: compact ? 26 : 34, color: C.ink, maxWidth: '80%'}}>{title}</div>
    <div style={{fontFamily: FONT.sans, fontSize: compact ? 17 : 20, color: C.ink3, maxWidth: '80%'}}>{why}</div>
  </AbsoluteFill>
);

export const PreviewMark: React.FC<{lines: string[]}> = ({lines}) => {
  const f = useCurrentFrame();
  if (!lines.length) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: 24,
        bottom: 22,
        padding: '8px 14px',
        borderRadius: 8,
        background: 'rgba(0,0,0,.72)',
        border: `1px solid ${C.ember}`,
        fontFamily: FONT.mono,
        fontSize: 15,
        color: C.ember,
        letterSpacing: '.08em',
        opacity: f % 120 < 112 ? 1 : 0.6,
      }}
    >
      PREVIEW, NOT FOR RELEASE: {lines.length} real shot{lines.length === 1 ? '' : 's'} missing
    </div>
  );
};
