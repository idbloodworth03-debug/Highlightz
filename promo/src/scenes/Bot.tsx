// 0:07.500-0:16.875 (5 bars), the heart of it. The real dashboard: a live
// channel's score climbing on Live Streams with its seven signals pulsing in,
// the crossing (freeze, flash, shockwave, CLIP CAPTURED), the clip landing in
// Clip Review and approved, the Clip Editor, two featured clips, Post to TikTok.
import React from 'react';
import {AbsoluteFill, Freeze, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, EASE, FONT} from '../brand';
import {cap, DATA, type FeaturedClip} from '../data';
import {at, sceneRange} from '../tl';
import {CaptureVideo, Placeholder} from '../kit/Frame';
import {focusPoint, UiShot} from '../kit/UiShot';
import {Flash, Hud, Shockwave, Stamp} from '../kit/Fx';
import type {Layout} from '../Promo';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// The engine's signal names (src/trigger/engine.py) as the landing page labels them.
export const SIGNALS: [string, string][] = [
  ['CHAT_VELOCITY', 'Chat velocity'],
  ['KEYWORD', 'Keyword hits'],
  ['EMOTE_HOMOGENEITY', 'Emote wall'],
  ['SENTIMENT', 'Sentiment'],
  ['AUDIO_SPIKE', 'Audio spike'],
  ['VIEWER_SPIKE', 'Viewer spike'],
  ['SILENCE_BURST', 'Silence burst'],
];

// The score and the seven signals feeding it, from the real trace.
const SignalPanel: React.FC<{layout: Layout; cross: number}> = ({layout, cross}) => {
  const f = useCurrentFrame();
  const tall = layout === 'tall';
  const tr = DATA.signal_trace;
  const i = Math.max(0, Math.min(f, cross));
  const score = tr ? Math.round(tr.score[Math.min(i, tr.score.length - 1)]) : null;
  const over = f >= cross;
  const size = tall ? 26 : 28;
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: tall ? 8 : 14}}>
      <div style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 26 : 22, letterSpacing: '.16em', color: C.ink3}}>TRIGGER SCORE · LIVE</div>
      <div style={{display: 'flex', alignItems: 'baseline', gap: 18}}>
        <span
          style={{
            fontFamily: FONT.mono,
            fontWeight: 600,
            fontSize: tall ? 104 : 130,
            lineHeight: 1,
            color: over ? C.ember : C.ink,
            textShadow: over ? '0 0 40px rgba(247,167,69,.6)' : '0 0 30px rgba(201,56,255,.35)',
          }}
        >
          {score === null ? '—' : score}
        </span>
        <span style={{fontFamily: FONT.mono, fontSize: tall ? 30 : 26, color: C.ink3}}>{tr ? `thr ${Math.round(tr.threshold)}` : 'thr —'}</span>
      </div>
      <div style={{display: 'grid', gridTemplateColumns: tall ? '1fr 1fr' : '1fr', gap: tall ? 8 : 10, marginTop: tall ? 4 : 8}}>
        {SIGNALS.map(([key, label]) => {
          const v = tr && tr.signals[key] ? tr.signals[key][Math.min(i, tr.signals[key].length - 1)] : 0;
          return (
            <div
              key={key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: `${size * 0.32}px ${size * 0.5}px`,
                borderRadius: 14,
                background: `rgba(201,56,255,${0.06 + v * 0.3})`,
                border: `1px solid rgba(243,67,254,${0.12 + v * 0.6})`,
                boxShadow: v > 0.05 ? `0 0 ${12 + v * 34}px rgba(243,67,254,${v * 0.55})` : 'none',
                fontFamily: FONT.sans,
                fontWeight: 600,
                fontSize: size,
                color: v > 0.05 ? C.ink : C.ink3,
              }}
            >
              <span style={{width: size * 0.36, height: size * 0.36, borderRadius: 99, background: v > 0.05 ? 'linear-gradient(180deg,#F343FE,#9847EE)' : C.hair2, transform: `scale(${1 + v * 0.6})`}} />
              {label}
            </div>
          );
        })}
      </div>
      {!tr && <div style={{fontFamily: FONT.sans, fontSize: tall ? 22 : 18, color: C.ember}}>Score and signal values come from the real capture.</div>}
    </div>
  );
};

// A featured clip, whole: the 16:9 frame as the streamer's viewers saw it
// (a centre crop to 9:16 cut the face cam and the action out), over a
// blurred copy of itself. The title sits above the clip, not on it; the
// credit is on the clip's corner in 16:9 and under it in 9:16.
const ClipCard: React.FC<{clip: FeaturedClip | undefined; layout: Layout; punch?: boolean; showTitle?: boolean}> = ({clip, layout, punch, showTitle}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tall = layout === 'tall';
  const s = punch ? interpolate(f, [0, 9], [1.18, 1], {...clamp, easing: EASE}) : interpolate(f, [0, 12], [1.06, 1], {...clamp, easing: EASE});
  const w = tall ? 1000 : 1380;
  const h = w * (9 / 16);
  const top = tall ? 640 : 190;
  const left = (tall ? 1080 : 1920) / 2 - w / 2;
  // The credit is up from the clip's first frames and the title soon after:
  // a clip is on screen for one 2-beat shot (0.94 s), and no text may show
  // for under 0.8 s.
  const credit = interpolate(f, [0, 6], [0, 1], {...clamp, easing: EASE});
  const titleP = interpolate(f, [2, 12], [0, 1], {...clamp, easing: EASE});
  const video = (fit: 'cover' | 'contain') =>
    clip ? <OffthreadVideo src={staticFile(clip.file)} startFrom={Math.round(clip.start_s * fps)} muted style={{width: '100%', height: '100%', objectFit: fit}} /> : null;
  return (
    <AbsoluteFill>
      {clip && <AbsoluteFill style={{filter: 'blur(48px) brightness(.4) saturate(1.2)', transform: 'scale(1.25)'}}>{video('cover')}</AbsoluteFill>}
      {clip && showTitle && clip.title && (
        <div
          style={{
            position: 'absolute',
            left: 64,
            right: 64,
            bottom: (tall ? 1920 : 1080) - top + (tall ? 36 : 28),
            textAlign: 'center',
            fontFamily: FONT.sans,
            fontWeight: 800,
            letterSpacing: '-0.03em',
            fontSize: tall ? 64 : 52,
            lineHeight: 1.05,
            color: '#fff',
            filter: 'drop-shadow(0 4px 18px rgba(0,0,0,.7))',
            transform: `translateY(${(1 - titleP) * 30}px)`,
            opacity: titleP,
          }}
        >
          {clip.title}
        </div>
      )}
      <div style={{position: 'absolute', left, top, width: w, height: h, borderRadius: tall ? 30 : 26, overflow: 'hidden', transform: `scale(${s})`, boxShadow: '0 50px 140px rgba(0,0,0,.7), 0 0 80px rgba(201,56,255,.3)', border: `1px solid ${C.hair2}`}}>
        {clip ? video('contain') : <Placeholder compact title="A featured clip: real footage, about one second" why="The clips featured on highlightz.app, downloaded from the live app." />}
      </div>
      {clip && (
        <div
          style={{
            position: 'absolute',
            left: left + (tall ? 0 : 28),
            top: tall ? top + h + 28 : top + h - 28 - 58,
            opacity: credit,
            transform: `translateY(${(1 - credit) * 16}px)`,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '12px 20px',
            borderRadius: 14,
            background: 'rgba(10,10,12,.78)',
            border: `1px solid ${C.hair2}`,
            fontFamily: FONT.sans,
            fontWeight: 700,
            fontSize: tall ? 38 : 28,
            color: C.ink,
          }}
        >
          <span style={{fontFamily: FONT.mono, fontSize: tall ? 24 : 18, letterSpacing: '.1em', color: C.ink3}}>CLIP OF</span>
          {clip.channel}
          <span style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 22 : 16, letterSpacing: '.08em', padding: '4px 10px', borderRadius: 8, background: clip.platform === 'kick' ? 'rgba(83,252,24,.15)' : 'rgba(145,70,255,.25)', color: clip.platform === 'kick' ? '#53FC18' : '#C9B3FF'}}>
            {clip.platform === 'kick' ? 'KICK' : 'TWITCH'}
          </span>
        </div>
      )}
    </AbsoluteFill>
  );
};

export const Bot: React.FC<{layout: Layout}> = ({layout}) => {
  const frame = useCurrentFrame();
  const tall = layout === 'tall';
  const S = 'bot';
  const cross = at('bot.cross', S);
  const freezeEnd = at('bot.freezeEnd', S);
  const review = at('bot.review', S);
  const editor = at('bot.editor', S);
  const clip2 = at('bot.clip2', S);
  const clip3 = at('bot.clip3', S);
  const post = at('bot.post', S);
  const end = sceneRange(S).dur;
  const fc = DATA.featured_clips;
  const live = cap('live', layout);
  // The live capture is cut so its crossing is its key_frame. Play up to it,
  // hold it for one beat, then let the real UI carry on under the stamp.
  const k = live && live.key_frame !== undefined ? live.key_frame : cross;
  const liveMap = (f: number) => (f < cross ? f - cross + k : f < freezeEnd ? k : k + (f - freezeEnd));
  const focusY = tall ? 1130 : undefined;
  const hit = focusPoint('live', layout, focusY);
  return (
    <AbsoluteFill>
      <Sequence durationInFrames={review} name="live + cross">
        <UiShot name="live" layout={layout} dur={review} frameMap={liveMap} focusY={focusY} tilt={5}
          title="Live Streams: a live channel's score climbing to its threshold, then crossing" />
        {tall ? (
          <Hud style={{position: 'absolute', left: 56, right: 56, top: 150}}>
            <SignalPanel layout={layout} cross={cross} />
          </Hud>
        ) : (
          <div style={{position: 'absolute', left: 1300, top: 150, width: 560}}>
            <SignalPanel layout={layout} cross={cross} />
          </div>
        )}
        <Flash start={cross} color="#FFFFFF" dur={9} peak={0.9} />
        <Flash start={cross + 2} color={C.magenta} dur={18} peak={0.35} />
        <Shockwave start={cross} x={hit.x} y={hit.y} size={tall ? 1000 : 1100} />
        <div style={{position: 'absolute', left: hit.x, top: hit.y, transform: 'translate(-50%,-50%)'}}>
          <Stamp start={cross} end={review} text="CLIP CAPTURED" size={tall ? 76 : 70} />
        </div>
        {/* The one-beat freeze reads as a held breath: the frame edge lights. */}
        <AbsoluteFill style={{border: `6px solid ${C.ember}`, opacity: frame >= cross && frame < freezeEnd ? 0.6 : 0}} />
      </Sequence>
      <Sequence from={review} durationInFrames={editor - review} name="review">
        <UiShot name="review" layout={layout} dur={editor - review} tilt={-5}
          title="Clip Review: the new clip flies in with its score; the cursor clicks Approve" />
      </Sequence>
      <Sequence from={editor} durationInFrames={clip2 - editor} name="editor">
        <UiShot name="editor" layout={layout} dur={clip2 - editor} tilt={4}
          title="Clip Editor: the clip reframed to vertical, captions lighting up word by word" />
      </Sequence>
      <Sequence from={clip2} durationInFrames={clip3 - clip2} name="clip 2">
        <ClipCard clip={fc[0]} layout={layout} punch />
        <Flash start={0} dur={6} peak={0.7} />
      </Sequence>
      <Sequence from={clip3} durationInFrames={post - clip3} name="clip 3">
        <ClipCard clip={fc[1]} layout={layout} punch showTitle />
        <Flash start={0} dur={6} peak={0.6} />
      </Sequence>
      <Sequence from={post} durationInFrames={end - post} name="post">
        <UiShot name="post" layout={layout} dur={end - post} tilt={-4} push={1.2} title="Post to TikTok, one press" />
      </Sequence>
    </AbsoluteFill>
  );
};
