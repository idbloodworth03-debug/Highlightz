// The site's three faces, from public/brand (copied from
// src/dashboard/static/fonts). Rendering waits until all of them are loaded,
// so no frame is ever drawn in a fallback face.
import {continueRender, delayRender, staticFile} from 'remotion';

const FACES: [string, string, FontFaceDescriptors][] = [
  ['Sora', 'brand/sora-var.woff2', {weight: '100 900'}],
  ['Plex', 'brand/plexmono-400.woff2', {weight: '400'}],
  ['Plex', 'brand/plexmono-600.woff2', {weight: '600'}],
  ['Lobster', 'brand/lobster-400.woff2', {weight: '400'}],
];

let started = false;
export const loadFonts = () => {
  if (started || typeof document === 'undefined') return;
  started = true;
  const handle = delayRender('brand fonts');
  Promise.all(
    FACES.map(([family, file, desc]) =>
      new FontFace(family, `url(${staticFile(file)}) format('woff2')`, desc).load().then((f) => {
        document.fonts.add(f);
      }),
    ),
  )
    .then(() => continueRender(handle))
    .catch((e) => {
      // A missing face must fail the render loudly, never ship a fallback.
      throw e;
    });
};
