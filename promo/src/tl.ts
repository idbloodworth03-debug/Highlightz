// Beat-grid helpers over src/timeline.json (the file audio/make_audio.py
// reads too). 128 BPM at 60 fps: one beat is 28.125 frames, so a cut snaps to
// the nearest frame (at most 8 ms early or late) while its sound lands on the
// exact sample.
import TL from './timeline.json';

export const FPS = TL.fps;
export const BEAT_S = 60 / TL.bpm;
export const BEAT_F = FPS * BEAT_S;
export const fr = (beats: number) => Math.round(beats * BEAT_F);

export type Scene = {id: string; from: number; to: number};
const PROMO_SCENES = TL.promo.scenes as Scene[];

export const PROMO_FRAMES = fr(TL.promo.beats);          // 1800
export const TEASER_FRAMES = Math.round(12 * FPS);       // 720

export const sceneRange = (id: string) => {
  const s = PROMO_SCENES.find((x) => x.id === id);
  if (!s) throw new Error('no scene ' + id);
  return {from: fr(s.from), dur: fr(s.to) - fr(s.from), fromBeat: s.from, toBeat: s.to};
};

type ShotKey = keyof typeof TL.promo.shots;
export const shotBeat = (k: ShotKey) => TL.promo.shots[k];
// Frames from the start of a scene to one of its shots.
export const at = (k: ShotKey, sceneId: string) => fr(TL.promo.shots[k]) - sceneRange(sceneId).from;

export const TEASER_SCENES = TL.teaser.scenes as (Scene & {src_from: number})[];
