// The real data the video shows, and nothing else. See src/data/promo-data.json.
import raw from './data/promo-data.json';
import {missingFrom} from './missing';

export type BoardRow = {rank: number; name: string; clips: number; score: number};
export type Campaign = {
  title: string;
  summary?: string;
  status: string;
  prize_pool: number;
  payouts: number[];
  hashtags: string[];
  mentions: string[];
  platforms: string[];
  streamers: {name: string; platform: string}[];
  start_at: number;
  end_at: number;
  judging_text: string;
  joined_count: number;
  entrants: number;
  entries: number;
  total_views: number;
  metric: string;
  // 'top10': entries and views are summed over the top ten only (the board
  // the API sends), and the edit labels them that way.
  counts_scope?: 'all' | 'top10';
  board: BoardRow[];
};
export type FeaturedClip = {channel: string; platform: string; file: string; start_s: number; title?: string};
// A capture's `focus` is the part of the screen the shot is about (the score
// chart, the new clip card, the leaderboard), in CSS px of its viewport, as the
// capture rig measured it on the real page. The 9:16 cut zooms to it.
export type Rect = {x: number; y: number; w: number; h: number};
export type Capture = {
  file: string;
  width: number;
  height: number;
  fps: number;
  frames: number;
  viewport: {w: number; h: number};
  focus?: Rect;
  // Frame (of this capture) where the shot's key moment happens, e.g. the
  // score crossing; the edit freezes on it.
  key_frame?: number;
};
// Each UI shot is captured twice: the desktop layout for 16:9 and the real
// phone layout for 9:16.
export type CapturePair = {wide: Capture | null; tall: Capture | null};
export type ShotName = 'landing' | 'live' | 'campaign';
export type SignalTrace = {
  threshold: number;
  // One sample per output frame of the live shot: the score and each signal's share.
  score: number[];
  signals: Record<string, number[]>;
  channel: string;
};

type Data = {
  fetched_at: string | null;
  source: string | null;
  budget_range: {min: number; max: number; source: string; verified_live: boolean};
  clips_total: number | null;
  campaign: (Campaign & {funded_by?: string}) | null;
  featured_clips: FeaturedClip[];
  signal_trace: SignalTrace | null;
  captures: Record<ShotName, CapturePair | null>;
  mask_entrant_names: boolean;
};

export const DATA = raw as unknown as Data;

export const cap = (name: ShotName, layout: 'wide' | 'tall'): Capture | null =>
  (DATA.captures[name] && DATA.captures[name]![layout]) || null;

// What is still missing, in words a person can act on. Empty = ready to render.
export const missing = (): string[] => missingFrom(DATA);

// "jynxzi" -> "j•••••". Entrants agreed to appear on the campaign's
// leaderboard, not in an advert; the owner can switch this off in the data file.
export const shownName = (n: string) =>
  DATA.mask_entrant_names ? n.slice(0, 1) + '•'.repeat(Math.max(3, Math.min(6, n.length - 1))) : n;

export const money = (n: number) =>
  '$' + Math.round(n).toLocaleString('en-US');
