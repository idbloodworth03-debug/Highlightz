// 0:20.625-0:26.250 (3 bars). The real campaign page (the live campaign, or
// else the next to open), with its real numbers counted up beside it: the
// prize pool (coin sting on beat 45), the split, the hashtags, and then entries
// and views once it is live, or clippers joined and the opening date before.
// A live campaign's leaderboard slides into rank inside the real page.
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C, EASE, FONT} from '../brand';
import {DATA, money} from '../data';
import {at, sceneRange} from '../tl';
import {countUp, Hud} from '../kit/Fx';
import {UiShot} from '../kit/UiShot';
import type {Layout} from '../Promo';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const ordinal = (i: number) => ['1st', '2nd', '3rd'][i] || `${i + 1}th`;
// The date as the site shows it (its times are US Eastern): "OCT 30".
const day = (t: number) => new Date(t * 1000).toLocaleDateString('en-US', {month: 'short', day: 'numeric', timeZone: 'America/New_York'}).toUpperCase();
const TITLE = "The campaign's page: prize pool, split, hashtags, who has joined";
const WHY = 'Recorded from the real Campaigns tab (demo account) once this session can reach highlightz.app.';

const Reveal: React.FC<{start: number; children: React.ReactNode; style?: React.CSSProperties}> = ({start, children, style}) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [start, start + 14], [0, 1], {...clamp, easing: EASE});
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * 26}px)`, ...style}}>{children}</div>;
};

export const Campaign: React.FC<{layout: Layout}> = ({layout}) => {
  const f = useCurrentFrame();
  const tall = layout === 'tall';
  const S = 'campaign';
  const dur = sceneRange(S).dur;
  const camp = DATA.campaign;
  const tPool = at('campaign.pool', S);
  const tSplit = at('campaign.split', S);
  const tTags = at('campaign.tags', S);
  const tCounts = at('campaign.counts', S);
  const tGlow = at('campaign.glow', S);
  const poolGlow = interpolate(f, [tPool, tPool + 6, tPool + 40], [0, 1, 0.35], clamp);
  const boardGlow = interpolate(f, [tGlow, tGlow + 10], [0, 1], {...clamp, easing: EASE});
  const label: React.CSSProperties = {fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 26 : 22, letterSpacing: '.16em', color: C.ink3};

  const title = (
    <Reveal start={0}>
      <div style={{fontFamily: FONT.sans, fontWeight: 800, letterSpacing: '-0.03em', fontSize: tall ? 46 : 40, color: C.ink, lineHeight: 1.05}}>
        {camp ? camp.title : 'The live campaign'}
      </div>
      {camp && camp.streamers.length > 0 && (
        <div style={{display: 'flex', gap: 10, marginTop: 12}}>
          {camp.streamers.map((s) => (
            <span key={s.name} style={{fontFamily: FONT.sans, fontWeight: 700, fontSize: tall ? 28 : 22, padding: '6px 14px', borderRadius: 10, background: 'rgba(201,56,255,.14)', border: `1px solid ${C.hair2}`, color: C.ink}}>
              {s.name} <span style={{color: C.ink3, fontWeight: 500}}>· {s.platform === 'kick' ? 'Kick' : 'Twitch'}</span>
            </span>
          ))}
        </div>
      )}
    </Reveal>
  );
  const pool = (
    <Reveal start={tPool}>
      <div style={label}>PRIZE POOL</div>
      <div
        style={{
          fontFamily: FONT.mono,
          fontWeight: 600,
          fontSize: tall ? 110 : 132,
          lineHeight: 1,
          background: 'linear-gradient(180deg,#FFD38A,#F7A745 60%,#E07B1F)',
          WebkitBackgroundClip: 'text',
          backgroundClip: 'text',
          color: 'transparent',
          filter: `drop-shadow(0 0 ${20 + poolGlow * 40}px rgba(247,167,69,${0.3 + poolGlow * 0.4}))`,
        }}
      >
        {camp ? money(countUp(f, tPool, 22, camp.prize_pool)) : '—'}
      </div>
      {camp && camp.funded_by && <div style={{fontFamily: FONT.sans, fontSize: tall ? 26 : 21, color: C.ink2, marginTop: 6}}>Prize pool from {camp.funded_by}</div>}
    </Reveal>
  );
  const split = camp && camp.payouts.length > 0 && (
    <Reveal start={tSplit} style={{display: 'flex', gap: 10, flexWrap: 'wrap'}}>
      {camp.payouts.map((amt, i) => (
        <span key={i} style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 30 : 24, padding: '8px 14px', borderRadius: 10, background: 'rgba(255,255,255,.05)', border: `1px solid ${C.hair2}`, color: C.ink}}>
          <span style={{color: C.ink3}}>{ordinal(i)}</span> {money(amt)}
        </span>
      ))}
    </Reveal>
  );
  const tags = camp && camp.hashtags.length + camp.mentions.length > 0 && (
    <Reveal start={tTags} style={{display: 'flex', gap: 10, flexWrap: 'wrap'}}>
      {[...camp.hashtags.map((t) => '#' + t), ...camp.mentions.map((m) => '@' + m)].map((t) => (
        <span key={t} style={{fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 28 : 22, padding: '6px 12px', borderRadius: 10, color: C.flare, background: 'rgba(210,106,251,.1)', border: '1px solid rgba(210,106,251,.35)'}}>{t}</span>
      ))}
    </Reveal>
  );
  const top10 = camp && camp.counts_scope === 'top10' ? ' · TOP 10' : '';
  const upcoming = camp && camp.status === 'upcoming';
  const big: React.CSSProperties = {fontFamily: FONT.mono, fontWeight: 600, fontSize: tall ? 64 : 60, color: C.ink};
  const counts = (
    <Reveal start={tCounts} style={{display: 'flex', gap: tall ? 56 : 40}}>
      {upcoming ? (
        <>
          <div>
            <div style={label}>CLIPPERS JOINED</div>
            <div style={big}>{countUp(f, tCounts, 16, camp!.joined_count).toLocaleString('en-US')}</div>
          </div>
          <div>
            <div style={label}>OPENS</div>
            <div style={big}>{day(camp!.start_at)}</div>
          </div>
        </>
      ) : (
        <>
          <div>
            <div style={label}>ENTRIES{top10}</div>
            <div style={big}>{camp ? countUp(f, tCounts, 20, camp.entries).toLocaleString('en-US') : '—'}</div>
          </div>
          <div>
            <div style={label}>{camp && camp.metric === 'engagement' ? 'ENGAGEMENT' : 'VIEWS'}{top10}</div>
            <div style={big}>{camp ? countUp(f, tCounts, 26, camp.total_views).toLocaleString('en-US') : '—'}</div>
          </div>
        </>
      )}
    </Reveal>
  );
  const glow = {filter: `drop-shadow(0 0 ${boardGlow * 50}px rgba(243,67,254,.42))`, position: 'absolute' as const, inset: 0};

  if (tall) {
    // A card in the clear band at the top says what it is and what it pays;
    // below it, the whole phone shows the real page scrolling to each part.
    return (
      <AbsoluteFill>
        <div style={glow}>
          <UiShot name="campaign" layout={layout} dur={dur} tilt={-4} fit={{top: 740, height: 1280}} title={TITLE} why={WHY} />
        </div>
        <Hud style={{position: 'absolute', left: 56, right: 56, top: 140, display: 'flex', flexDirection: 'column', gap: 14}}>
          {title}
          {pool}
          {split}
          {counts}
        </Hud>
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill>
      <div style={glow}>
        <UiShot name="campaign" layout={layout} dur={dur} tilt={5} push={1.05} wideBox={{left: 70, top: 150, width: 1150}} title={TITLE} why={WHY} />
      </div>
      <div style={{position: 'absolute', left: 1280, top: 150, width: 580, display: 'flex', flexDirection: 'column', gap: 20}}>
        {title}
        {pool}
        {split}
        {tags}
        {counts}
      </div>
    </AbsoluteFill>
  );
};
