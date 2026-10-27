// The site's own tokens (src/dashboard/api.py :root and the logo PNG), so the
// video and highlightz.app are one brand.
import {Easing} from 'remotion';

export const C = {
  base: '#0A0A0C',      // page base
  band: '#1E1826',      // the alternating band
  void: '#0E0B11',
  wall: '#1B1221',
  bruise: '#33203F',
  ink: '#F2EAF7',
  ink2: '#B9AEC4',
  ink3: '#9C90A6',
  hair: 'rgba(242,234,247,.085)',
  hair2: 'rgba(242,234,247,.15)',
  iris: '#B86ADC',
  flare: '#D26AFB',
  ember: '#F7A745',     // the site's one action colour
  // The H play-mark's gradient, sampled from static/logo-mark.png.
  magenta: '#F343FE',
  mid: '#C938FF',
  violet: '#9847EE',
};

export const GRAD = `linear-gradient(180deg, ${C.magenta} 0%, ${C.mid} 50%, ${C.violet} 100%)`;
export const GRAD_X = `linear-gradient(90deg, ${C.magenta} 0%, ${C.mid} 50%, ${C.violet} 100%)`;

export const FONT = {
  sans: "'Sora', system-ui, sans-serif",          // .disp: 800, -0.04em
  mono: "'Plex', ui-monospace, monospace",        // numbers and labels
};

// The site's one curve, and its one overshoot.
export const EASE = Easing.bezier(0.16, 1, 0.3, 1);
export const SPRING = Easing.bezier(0.34, 1.4, 0.64, 1);
export const IN_OUT = Easing.bezier(0.65, 0, 0.35, 1);
