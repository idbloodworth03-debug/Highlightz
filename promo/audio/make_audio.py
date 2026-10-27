#!/usr/bin/env python3
"""The promo's score and sound effects, synthesized from nothing.

WHY SYNTHESIZED. The brief asks for a licensed or royalty-free 128 BPM track.
Nothing in this file is a sample or someone else's recording: every drum, bass
note, chord, riser, whoosh and coin is generated here in numpy, so the music is
original to Highlightz and there is no licence to track. To use a licensed
track instead, pass a WAV at 128 BPM whose first downbeat is at 0.000 s as
--music (and --teaser-music for a separate 12 s edit); the SFX, ducking,
fade and loudness all still apply.

TIMING. Every cue comes from src/timeline.json, the file the Remotion scenes
read, in BEATS at 128 BPM: a cut and its sound cannot drift apart.

OUTPUT (public/audio/):
  promo-30s.wav     final mix, exactly 30.000 s
  teaser-12s.wav    final mix, exactly 12.000 s
  stems/            music and SFX before the master, for remixing

LOUDNESS. -14 LUFS integrated (what YouTube, TikTok and Instagram normalise
to), true peak at or under -1 dBTP, checked with ffmpeg's ebur128 after the
write. Music ducks under every sound effect. The mix fades out over the
final beat.

    python3 audio/make_audio.py            # both mixes
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess

import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 48000
HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
TIMELINE = json.load(open(os.path.join(PROMO, "src", "timeline.json")))
BPM = TIMELINE["bpm"]
BEAT = 60.0 / BPM
BAR = 4 * BEAT
OUT = os.path.join(PROMO, "public", "audio")
rng = np.random.default_rng(20261027)


# ── primitives ───────────────────────────────────────────────────────────────

def ns(seconds: float) -> int:
    return int(round(seconds * SR))


def tt(length_s: float) -> np.ndarray:
    return np.arange(ns(length_s)) / SR


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _sos(kind, f):
    return signal.butter(2, f, kind, fs=SR, output="sos")


def lp(x, f):
    return signal.sosfilt(_sos("lowpass", f), x, axis=-1)


def hp(x, f):
    return signal.sosfilt(_sos("highpass", f), x, axis=-1)


def bp(x, lo, hi):
    return signal.sosfilt(_sos("bandpass", [lo, hi]), x, axis=-1)


def pan2(x: np.ndarray, pan: float = 0.0) -> np.ndarray:
    """Mono to stereo, equal power. pan -1 (left) .. 1 (right)."""
    a = (pan + 1) * math.pi / 4
    return np.stack([x * math.cos(a), x * math.sin(a)])


def add(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0):
    """Mix x (mono or stereo) into the stereo buffer starting at `at` seconds.
    A start before 0 is clipped, so a whoosh can lead into the first frame."""
    if x.ndim == 1:
        x = pan2(x, pan)
    i = ns(at)
    j0 = max(0, -i)
    i = max(0, i)
    end = min(buf.shape[1], i + x.shape[1] - j0)
    if end > i:
        buf[:, i:end] += x[:, j0:j0 + end - i] * gain


def saw(f: float, t: np.ndarray, phase: float = 0.0) -> np.ndarray:
    """Band-limited sawtooth (PolyBLEP): bright without aliasing hiss."""
    p = (f * t + phase) % 1.0
    dt = f / SR
    y = 2 * p - 1
    m = p < dt
    x = p[m] / dt
    y[m] -= x + x - x * x - 1
    m = p > 1 - dt
    x = (p[m] - 1) / dt
    y[m] -= x * x + x + x + 1
    return y


def adsr(length_s, a=0.005, d=0.08, s=0.7, r=0.05):
    n = ns(length_s)
    env = np.full(n, s)
    na, nd, nr = ns(a), ns(d), ns(r)
    na = max(1, min(na, n))
    env[:na] = np.linspace(0, 1, na)
    if nd > 0 and na < n:
        k = min(nd, n - na)
        env[na:na + k] = 1 - (1 - s) * (np.arange(k) / max(1, nd))
    if nr > 0:
        k = min(nr, n)
        env[n - k:] *= np.linspace(1, 0, k)
    return env


def reverb_ir(length_s=1.8, decay_s=0.45, bright=7000.0):
    """A stereo room: decaying noise, darker as it fades."""
    t = tt(length_s)
    ir = rng.standard_normal((2, len(t))) * np.exp(-t / decay_s)
    ir = lp(ir, bright)
    ir[:, :ns(0.012)] *= np.linspace(0, 1, ns(0.012))   # pre-delay softness
    return ir / np.sqrt((ir ** 2).sum(axis=1, keepdims=True))


IR = reverb_ir()


def verb(x: np.ndarray) -> np.ndarray:
    """Stereo in, stereo out (wet only)."""
    return np.stack([signal.fftconvolve(x[c], IR[c])[: x.shape[1]] for c in range(2)])


# ── drums ────────────────────────────────────────────────────────────────────

def kick():
    t = tt(0.5)
    f = 46 + (175 - 46) * np.exp(-t / 0.032)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.2)
    click = hp(rng.standard_normal(len(t)), 2500) * np.exp(-t / 0.0025) * 0.5
    return np.tanh(2.2 * (body + click)) * 0.95


def clap():
    t = tt(0.55)
    env = np.zeros(len(t))
    for k, d in enumerate((0.0, 0.010, 0.021)):
        i = ns(d)
        env[i:] += np.exp(-t[: len(t) - i] / 0.007) * (0.7 if k < 2 else 1.0)
    env += np.exp(-t / 0.13) * 0.32
    body = np.sin(2 * np.pi * 210 * t) * np.exp(-t / 0.03) * 0.25
    return bp(rng.standard_normal(len(t)), 900, 4200) * env + body


def metal(length_s, decay_s):
    """808-style metallic noise: six square waves at inharmonic ratios."""
    t = tt(length_s)
    x = np.zeros(len(t))
    for f in np.array([205.3, 304.4, 369.6, 522.7, 540.0, 800.0]) * 1.65:
        x += np.sign(np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)))
    x = bp(x, 6500, 15000) + 0.35 * hp(rng.standard_normal(len(t)), 9000)
    return x * np.exp(-t / decay_s)


HAT_C = metal(0.09, 0.016) * 0.32
HAT_O = metal(0.42, 0.11) * 0.24
CRASH = metal(2.2, 0.75) * 0.32
KICK = kick()
CLAP = clap()


def snare_roll(buf, start_beat, end_beat, gain=0.5):
    """Accelerating roll into a hit: eighths, then sixteenths, then 32nds."""
    b = start_beat
    span = end_beat - start_beat
    while b < end_beat - 1e-6:
        prog = (b - start_beat) / span
        step = 0.5 if prog < 0.5 else 0.25 if prog < 0.8 else 0.125
        add(buf, CLAP * (0.35 + 0.65 * prog), b * BEAT, gain * (0.5 + 0.5 * prog))
        b += step


# ── tonal parts ──────────────────────────────────────────────────────────────

# F minor, one chord per bar: i - VI - III - VII, voiced close around middle C.
PROG = [
    ("Fm", 41, (53, 56, 60, 65)),
    ("Db", 37, (53, 56, 61, 65)),
    ("Ab", 44, (51, 56, 60, 63)),
    ("Eb", 39, (51, 55, 58, 63)),
]


def chord_of(bar_index: int):
    return PROG[bar_index % 4]


def supersaw(notes, length_s, bright=5200.0, spread=0.22):
    """Seven detuned saws per note, spread across the stereo field."""
    t = tt(length_s)
    out = np.zeros((2, len(t)))
    detune = np.array([-1, -0.62, -0.3, 0, 0.3, 0.62, 1]) * spread  # semitones
    gains = np.array([0.55, 0.7, 0.85, 1.0, 0.85, 0.7, 0.55])
    pans = np.linspace(-0.85, 0.85, 7)
    for m in notes:
        for d, g, p in zip(detune, gains, pans):
            out += pan2(saw(hz(m + d), t, rng.uniform()) * g, p)
    out = lp(out, bright)
    return out / (len(notes) * gains.sum()) * 2.2


def pluck(midi, length_s=0.22, bright=4200.0):
    t = tt(length_s)
    x = 0.6 * saw(hz(midi), t, rng.uniform()) + 0.4 * saw(hz(midi + 0.08), t, rng.uniform())
    x = lp(x, bright) * np.exp(-t / 0.07)
    return x


def bass(midi, length_s):
    t = tt(length_s)
    f = hz(midi)
    x = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t)
    x = np.tanh(2.4 * x) / np.tanh(2.4)        # harmonics a phone speaker can play
    return x * adsr(length_s, a=0.004, d=0.06, s=0.85, r=0.03)


def riser(length_s, f0=250.0, f1=6000.0):
    """Noise whose band climbs, plus a rising tone, swelling into a hit."""
    t = tt(length_s)
    noise = rng.standard_normal(len(t))
    parts = np.zeros(len(t))
    k = 6
    for i in range(k):
        c = f0 * (f1 / f0) ** (i / (k - 1))
        w = np.clip(1 - np.abs(t / length_s * (k - 1) - i), 0, 1)   # crossfade by time
        parts += bp(noise, c * 0.7, min(c * 1.4, SR / 2 - 100)) * w
    tone_f = 200 * (2400 / 200) ** (t / length_s)
    tone = np.sin(2 * np.pi * np.cumsum(tone_f) / SR) * 0.25
    env = (t / length_s) ** 2.2
    return (parts + tone) * env


def impact():
    """Low boom and a crash: the music's own downbeat hit."""
    t = tt(2.4)
    f = 36 + (90 - 36) * np.exp(-t / 0.09)
    boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.55)
    x = np.tanh(1.6 * boom) * 0.9
    x[: len(CRASH)] += CRASH[: len(t)] * 0.9
    return x


# ── arrangement ──────────────────────────────────────────────────────────────

LEAD_STEPS = (0, 3, 6, 8, 10, 11, 14)        # 16th positions in a bar
LEAD_SHAPE = (3, 2, 1, 2, 3, 0, 2)           # index into the chord's notes


def render_music(bars: list[dict], total_s: float):
    """bars: one dict per bar of which parts play. Returns (music, kick_times)."""
    n = ns(total_s)
    drums = np.zeros((2, n))
    tonal = np.zeros((2, n))          # bass + chords: pumped by the kick
    lead = np.zeros((2, n))
    fx = np.zeros((2, n))
    kicks = []
    for bi, b in enumerate(bars):
        t0 = bi * BAR
        name, root, notes = chord_of(b.get("chord", bi))
        # Drums
        for beat in range(4):
            bt = t0 + beat * BEAT
            if b.get("kick") and not (b.get("break_from") is not None and beat >= b["break_from"]):
                if not (b.get("kick") == "half" and beat % 2):
                    add(drums, KICK, bt, 0.9)
                    kicks.append(bt)
            if b.get("clap") and beat in (1, 3) and not (b.get("break_from") is not None and beat >= b["break_from"]):
                add(drums, CLAP, bt, 0.55)
            if b.get("hats") and not (b.get("break_from") is not None and beat >= b["break_from"]):
                for s in range(4):
                    vel = (0.55, 0.3, 0.8, 0.35)[s]
                    if s == 2 and b.get("open"):
                        add(drums, HAT_O, bt + s * BEAT / 4, 0.7, 0.25)
                    else:
                        add(drums, HAT_C, bt + s * BEAT / 4, vel, -0.2 + 0.1 * s)
        if b.get("roll"):
            snare_roll(drums, bi * 4 + b["roll"][0], bi * 4 + b["roll"][1])
        # Bass: offbeat eighths, the root, an octave bounce on the last one
        if b.get("bass"):
            for e in range(8):
                if e % 2 == 1 and not (b.get("break_from") is not None and e / 2 >= b["break_from"]):
                    m = root + (12 if e == 7 else 0)
                    add(tonal, bass(m, BEAT / 2 * 0.9), t0 + e * BEAT / 2, 0.42)
        # Chords: a held pad, or pumping stabs
        mode = b.get("chords")
        if mode == "pad":
            add(tonal, supersaw(notes, BAR + 0.3, bright=b.get("bright", 1800)), t0, 0.32)
        elif mode == "stabs":
            for e in range(8):
                if e % 2 == 1 and not (b.get("break_from") is not None and e / 2 >= b["break_from"]):
                    st = supersaw(notes, BEAT * 0.42, bright=b.get("bright", 5200))
                    st *= np.exp(-np.arange(st.shape[1]) / SR / 0.16)
                    add(tonal, st, t0 + e * BEAT / 2, 0.55)
        elif mode == "hold":
            add(tonal, supersaw(notes, b.get("hold_s", 3.0), bright=b.get("bright", 4200))
                * np.exp(-np.arange(ns(b.get("hold_s", 3.0))) / SR / b.get("hold_decay", 1.1)), t0, 0.5)
        # Lead pluck
        if b.get("lead"):
            for s, k in zip(LEAD_STEPS, LEAD_SHAPE):
                m = notes[k] + 12
                add(lead, pluck(m), t0 + s * BEAT / 4, 0.42, (-0.3, 0.3)[s % 2])
        # FX
        if b.get("riser"):
            a, z = b["riser"]
            add(fx, riser((z - a) * BEAT), t0 + a * BEAT, 0.38)
        if b.get("impact") is not None:
            add(fx, impact(), t0 + b["impact"] * BEAT, 0.5)
        if b.get("crash"):
            add(drums, CRASH, t0, 0.55)
    # Sidechain pump on bass and chords: duck hard on every kick, recover.
    pump = np.ones(n)
    for k in kicks:
        i = ns(k)
        seg = np.arange(min(ns(0.24), n - i)) / SR
        pump[i:i + len(seg)] = np.minimum(pump[i:i + len(seg)], 0.3 + 0.7 * (1 - np.exp(-seg / 0.06)))
    tonal *= pump
    # Space: reverb on clap, lead and chords; a dotted-eighth echo on the lead.
    echo = np.zeros_like(lead)
    d = ns(BEAT * 0.75)
    echo[:, d:] += lead[::-1, :-d] * 0.32          # ping-pong: channels swapped
    lead = lead + echo
    wet = verb(drums * 0.12 + lead * 0.5 + tonal * 0.18)
    music = drums + tonal + lead * 0.9 + fx + wet * 0.35
    return music, kicks


def promo_bars():
    """16 bars. Scene changes are the bar lines in src/timeline.json."""
    return [
        # 1 hook: a held chord off the impact, no drums
        {"chords": "hold", "hold_s": 2.2, "hold_decay": 0.9, "bright": 2600, "impact": 0, "riser": (2, 4)},
        # 2-4 landing: the groove arrives with the badge slam
        {"kick": 1, "hats": 1, "bass": 1, "chords": "stabs", "bright": 3200, "crash": 1},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "bright": 3800},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "bright": 4200, "roll": (3, 4)},
        # 5-6 bot: building to the threshold hit on beat 22 (bar 6, beat 3)
        {"kick": 1, "clap": 1, "hats": 1, "bass": 1, "chords": "pad", "bright": 1500, "riser": (0, 4)},
        {"kick": 1, "clap": 1, "hats": 1, "bass": 1, "chords": "pad", "bright": 2600, "break_from": 2,
         "riser": (0, 2), "roll": (0, 2), "impact": 2},
        # 7-9 the drop: card pops in, approve, editor
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1, "crash": 1},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1},
        # 10-11 slides
        {"kick": 1, "clap": 1, "hats": 1, "bass": 1, "chords": "stabs", "bright": 4200},
        {"kick": 1, "clap": 1, "hats": 1, "bass": 1, "chords": "stabs", "bright": 4600, "roll": (3, 4)},
        # 12-14 the campaign: second drop
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1, "crash": 1},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1, "riser": (2, 4)},
        # 15-16 CTA: a big chord, then the last bar thins out to the fade
        {"kick": "half", "clap": 0, "hats": 1, "bass": 1, "chords": "hold", "hold_s": 3.6, "hold_decay": 1.6,
         "bright": 4800, "impact": 0, "lead": 1},
        {"kick": "half", "chords": "hold", "hold_s": 2.0, "hold_decay": 0.8, "bright": 3400, "chord": 0},
    ]


def teaser_bars():
    """Six bars and a 0.75 s tail: hook, build into the hit on beat 10,
    the drop under the prize pool, and the CTA chord."""
    return [
        {"chords": "hold", "hold_s": 2.2, "hold_decay": 0.9, "bright": 2600, "impact": 0, "riser": (2, 4)},
        {"kick": 1, "hats": 1, "bass": 1, "chords": "pad", "bright": 1600, "crash": 1, "riser": (0, 4), "chord": 1},
        {"kick": 1, "clap": 1, "hats": 1, "bass": 1, "chords": "pad", "bright": 2600, "break_from": 2,
         "riser": (0, 2), "roll": (0, 2), "impact": 2, "chord": 2},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1, "crash": 1, "chord": 0},
        {"kick": 1, "clap": 1, "hats": 1, "open": 1, "bass": 1, "chords": "stabs", "lead": 1, "chord": 1},
        {"kick": "half", "chords": "hold", "hold_s": 2.6, "hold_decay": 1.0, "bright": 4600, "impact": 0, "chord": 0},
    ]


# ── sound effects ────────────────────────────────────────────────────────────

def sfx_whoosh(length_s=0.55, peak_at=0.36):
    """Band-passed air sweeping up and across, loudest on the cut."""
    t = tt(length_s)
    noise = rng.standard_normal(len(t))
    out = np.zeros(len(t))
    for i in range(5):
        c = 350 * (9.0 ** (i / 4))
        w = np.clip(1 - np.abs(t / length_s * 4 - i), 0, 1)
        out += bp(noise, c * 0.6, min(c * 1.7, 20000)) * w
    env = np.where(t < peak_at, (t / peak_at) ** 2.5, np.exp(-(t - peak_at) / 0.09))
    x = out * env
    pan_path = np.linspace(-0.7, 0.7, len(t))
    a = (pan_path + 1) * math.pi / 4
    return np.stack([x * np.cos(a), x * np.sin(a)]) * 0.9, peak_at


def sfx_hit():
    t = tt(1.6)
    f = 40 + (120 - 40) * np.exp(-t / 0.05)
    boom = np.tanh(2.0 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.42))
    snap = hp(rng.standard_normal(len(t)), 1200) * np.exp(-t / 0.03) * 0.7
    x = boom + snap
    x[: len(CRASH)] += CRASH[: len(t)] * 0.7
    st = pan2(x)
    return st + verb(st) * 0.25


def sfx_slam():
    t = tt(0.6)
    f = 42 + (110 - 42) * np.exp(-t / 0.03)
    thump = np.tanh(2.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.16))
    snap = bp(rng.standard_normal(len(t)), 1500, 7000) * np.exp(-t / 0.018)
    ring = np.sin(2 * np.pi * 1320 * t) * np.exp(-t / 0.12) * 0.12
    return thump * 0.9 + snap * 0.6 + ring


def sfx_pop():
    t = tt(0.16)
    f = 380 + (1250 - 380) * np.exp(-t / 0.018)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.045)
    tick = hp(rng.standard_normal(len(t)), 3000) * np.exp(-t / 0.002) * 0.3
    return body + tick


def sfx_tick():
    t = tt(0.09)
    return (np.sin(2 * np.pi * 1850 * t) * np.exp(-t / 0.018) * 0.6
            + hp(rng.standard_normal(len(t)), 4000) * np.exp(-t / 0.0015) * 0.25)


def sfx_click():
    t = tt(0.07)
    x = np.zeros(len(t))
    for d, g in ((0.0, 1.0), (0.032, 0.6)):          # press, release
        i = ns(d)
        x[i:] += hp(rng.standard_normal(len(t) - i), 2500) * np.exp(-t[: len(t) - i] / 0.0012) * g
    return x * 0.8


def sfx_coin():
    """Ka-ching: the drawer, then two bells and a sparkle."""
    t = tt(1.4)
    out = np.zeros(len(t))
    ka = bp(rng.standard_normal(len(t)), 1800, 6000) * np.exp(-t / 0.012) * 0.6
    out += ka
    for delay, f0, g in ((0.05, 1975.5, 0.55), (0.11, 2637.0, 0.5)):
        i = ns(delay)
        tb = t[: len(t) - i]
        bell = np.zeros(len(tb))
        for ratio, amp, dec in ((1.0, 1.0, 0.6), (2.76, 0.45, 0.25), (5.40, 0.25, 0.12), (8.93, 0.12, 0.06)):
            bell += amp * np.sin(2 * np.pi * f0 * ratio * tb) * np.exp(-tb / dec)
        out[i:] += bell * g
    for k in range(9):                                   # sparkle
        i = ns(0.12 + 0.045 * k)
        f = rng.uniform(4200, 7800)
        tb = t[: len(t) - i]
        out[i:] += np.sin(2 * np.pi * f * tb) * np.exp(-tb / 0.05) * 0.06
    st = pan2(out)
    return st + verb(st) * 0.3


def sfx_glitch():
    """The logo's glitch-in: crushed digital stutters, then a clean tone."""
    t_total = 0.42
    x = np.zeros(ns(t_total))
    pos = 0.0
    while pos < 0.30:
        L = rng.uniform(0.012, 0.035)
        seg_t = tt(L)
        kind = rng.integers(3)
        if kind == 0:
            seg = np.sign(np.sin(2 * np.pi * rng.uniform(180, 1400) * seg_t))
        elif kind == 1:
            seg = np.round(rng.standard_normal(len(seg_t)) * 3) / 3
        else:
            seg = np.sin(2 * np.pi * rng.uniform(600, 3000) * seg_t) * np.sign(np.sin(2 * np.pi * 60 * seg_t))
        i = ns(pos)
        x[i:i + len(seg)] += seg[: len(x) - i] * rng.uniform(0.25, 0.6)
        pos += L + rng.uniform(0.0, 0.02)
    x = lp(x, 9000)
    return pan2(x * 0.7, 0.0)


SFX = {
    "whoosh": lambda: sfx_whoosh(),
    "hit": lambda: (sfx_hit(), 0.0),
    "slam": lambda: (pan2(sfx_slam()), 0.0),
    "pop": lambda: (pan2(sfx_pop(), 0.15), 0.0),
    "tick": lambda: (pan2(sfx_tick(), 0.3), 0.0),
    "click": lambda: (pan2(sfx_click(), 0.1), 0.0),
    "coin": lambda: (sfx_coin(), 0.0),
    "glitch": lambda: (sfx_glitch(), 0.0),
}
SFX_GAIN = {"whoosh": 0.42, "hit": 0.85, "slam": 0.8, "pop": 0.5, "tick": 0.35,
            "click": 0.55, "coin": 0.75, "glitch": 0.45}


def render_sfx(cues, total_s):
    buf = np.zeros((2, ns(total_s)))
    for c in cues:
        x, lead_in = SFX[c["kind"]]()
        add(buf, x, c["beat"] * BEAT - lead_in, SFX_GAIN[c["kind"]] * c.get("gain", 1.0))
    return buf


# ── mix and master ───────────────────────────────────────────────────────────

def duck(music, sfx, depth_db=7.0):
    """Music gives way under every effect: follow the SFX level, attack 4 ms,
    release 220 ms, up to depth_db of reduction."""
    # The low-pass rings slightly below zero; the root of that is NaN.
    level = np.sqrt(np.maximum(signal.sosfilt(_sos("lowpass", 30), (sfx ** 2).sum(axis=0)), 0.0))
    level = level / (level.max() + 1e-9)
    env = np.zeros_like(level)
    a, r = math.exp(-1 / (0.004 * SR)), math.exp(-1 / (0.22 * SR))
    prev = 0.0
    for i, v in enumerate(level):                    # one pass, ~1.4M samples
        prev = (a * prev + (1 - a) * v) if v > prev else (r * prev + (1 - r) * v)
        env[i] = prev
    gain = 10 ** (-depth_db * np.clip(env * 1.6, 0, 1) / 20)
    return music * gain


def limit(x, ceiling_db=-1.5, lookahead_s=0.004, release_s=0.08):
    """Lookahead peak limiter: never over the ceiling, no audible pumping."""
    ceil = 10 ** (ceiling_db / 20)
    peak = np.abs(x).max(axis=0)
    need = np.minimum(1.0, ceil / np.maximum(peak, 1e-9))
    la = ns(lookahead_s)
    # The gain needed at any point is the minimum over the next lookahead.
    padded = np.concatenate([need, np.ones(la)])
    from numpy.lib.stride_tricks import sliding_window_view
    g = sliding_window_view(padded, la + 1).min(axis=1)[: len(need)]
    out = np.empty_like(g)
    r = math.exp(-1 / (release_s * SR))
    cur = 1.0
    for i, v in enumerate(g):
        cur = v if v < cur else r * cur + (1 - r) * v
        out[i] = cur
    return x * out


def fade_last_beat(x, total_s):
    n0 = ns(total_s - BEAT)
    k = x.shape[1] - n0
    x[:, n0:] *= np.cos(np.linspace(0, math.pi / 2, k)) ** 2
    return x


def loudness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                        "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    txt = r.stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", txt)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    return float(i[-1]), float(p[-1])


def write(path, x):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tpdf = (rng.uniform(-1, 1, x.shape) + rng.uniform(-1, 1, x.shape)) / 32768
    pcm = np.clip((x + tpdf) * 32767, -32768, 32767).astype(np.int16)
    wavfile.write(path, SR, pcm.T)


def load_music(path, total_s):
    """A licensed track in place of the synthesized one: any WAV whose first
    downbeat is at 0.000 s, resampled to 48 kHz stereo, cut or padded to the
    cut's exact length."""
    sr, x = wavfile.read(path)
    x = x.astype(np.float64)               # any sample format: levelled below
    x = x.T[:2] if x.ndim == 2 else np.stack([x, x])
    if sr != SR:
        g = math.gcd(sr, SR)
        x = signal.resample_poly(x, SR // g, sr // g, axis=1)
    n = ns(total_s)
    x = x[:, :n] if x.shape[1] >= n else np.pad(x, ((0, 0), (0, n - x.shape[1])))
    return x / (np.abs(x).max() + 1e-9) * 0.9


def master(name, total_s, bars, cues, music_file=None):
    music = load_music(music_file, total_s) if music_file else render_music(bars, total_s)[0]
    sfx = render_sfx(cues, total_s)
    music = duck(music, sfx)
    write(os.path.join(OUT, "stems", f"{name}-music.wav"), music * 0.5)
    write(os.path.join(OUT, "stems", f"{name}-sfx.wav"), sfx * 0.5)
    mix = music * 0.62 + sfx * 0.9
    mix = fade_last_beat(mix, total_s)
    # Level to -14 LUFS: measure, scale, limit, measure again.
    tmp = os.path.join(OUT, f".{name}.tmp.wav")
    mix = mix / (np.abs(mix).max() + 1e-9) * 0.5
    write(tmp, mix)
    lufs, _ = loudness(tmp)
    ceiling = -1.5
    for _ in range(6):
        mix = limit(mix * 10 ** ((-14.0 - lufs) / 20), ceiling_db=ceiling)
        write(tmp, mix)
        lufs, tp = loudness(tmp)
        if abs(lufs + 14.0) < 0.3 and tp <= -1.0:
            break
        # Peaks between samples overshoot a sample-peak ceiling: aim lower.
        if tp > -1.0:
            ceiling -= (tp + 1.0) + 0.15
    os.remove(tmp)
    final = os.path.join(OUT, f"{name}.wav")
    write(final, mix)
    lufs, tp = loudness(final)
    dur = wavfile.read(final)[1].shape[0] / SR
    print(f"{name}: {dur:.3f} s, {lufs:.1f} LUFS, true peak {tp:.1f} dBTP")
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--music", help="a licensed 128 BPM WAV to use instead of the synthesized track")
    ap.add_argument("--teaser-music", help="the teaser's track (default: the first 12 s of --music)")
    a = ap.parse_args()
    master("promo-30s", TIMELINE["promo"]["beats"] * BEAT, promo_bars(), TIMELINE["promo"]["sfx"], a.music)
    master("teaser-12s", 12.0, teaser_bars(), TIMELINE["teaser"]["sfx"], a.teaser_music or a.music)


if __name__ == "__main__":
    main()
