// The 9:16 cut shows the real phone layout of the site and the dashboard (both
// are responsive), in a phone, rather than a desktop window shrunk until
// nothing in it can be read.
import React from 'react';
import {C} from '../brand';

export const PHONE_VIEWPORT = {w: 390, h: 844};

export const PhoneFrame: React.FC<{width: number; tilt?: number; scale?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({
  width,
  tilt = 0,
  scale = 1,
  children,
  style,
}) => {
  const bezel = width * 0.035;
  const inner = width - bezel * 2;
  const innerH = inner * (PHONE_VIEWPORT.h / PHONE_VIEWPORT.w);
  return (
    <div style={{perspective: 2600, ...style}}>
      <div
        style={{
          width,
          height: innerH + bezel * 2,
          padding: bezel,
          borderRadius: width * 0.14,
          background: 'linear-gradient(160deg,#2A2230,#121015 40%,#1D1722)',
          boxShadow: `0 60px 140px rgba(0,0,0,.7), 0 0 0 1.5px ${C.hair2}, 0 0 100px rgba(201,56,255,.22)`,
          transform: `rotateY(${tilt}deg) scale(${scale})`,
        }}
      >
        <div style={{width: inner, height: innerH, borderRadius: width * 0.115, overflow: 'hidden', position: 'relative', background: C.base}}>
          {children}
          <div
            style={{
              position: 'absolute',
              top: inner * 0.03,
              left: '50%',
              transform: 'translateX(-50%)',
              width: inner * 0.3,
              height: inner * 0.085,
              borderRadius: 99,
              background: '#000',
            }}
          />
        </div>
      </div>
    </div>
  );
};
