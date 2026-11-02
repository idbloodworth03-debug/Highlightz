#!/usr/bin/env python3
"""The Duxxion clip ad's soundtrack: original music in one of three styles,
goofy sound design, and his laugh from the clip. No voice-over.

    python3 ad/make_ad_audio.py --style trap     # -> public/ad/duxxion-ad-trap.wav (+ duxxion-ad.wav)
    python3 ad/make_ad_audio.py --style jersey   # -> public/ad/duxxion-ad-jersey.wav
    python3 ad/make_ad_audio.py --style phonk    # -> public/ad/duxxion-ad-phonk.wav

THE MUSIC is synthesized here (nothing sampled, nothing to license), 128 BPM:
  trap    playful trap: a sneaky marimba hook and pizzicato strings over
          half-time trap drums, rolling hats, a sliding 808. D minor.
  jersey  jersey club: kicks on 1-2-3 and a triplet run, claps on 2 and 4,
          the bed-squeak on the off-beats, chord stabs, a pluck hook. A minor.
  phonk   the first version (owner: "different music"): Brazilian phonk, a
          cowbell riff and a distorted 808. F# minor.
The arrangement follows the edit whatever the style:
the drop hits on frame one (the hook), stops dead on a tape-stop when the
record scratches, plays backwards at the rewind's speed, comes back filtered
for the replay, sweeps down and builds through the slow-mo, and drops again
on the goal; it runs under the whole pitch and ends on the end card.

HIS VOICE is muted. The clip's speech swears throughout (transcribed:
"this kid f-ing sucks ... I'm f-ing your dad ... f- you!"), which no ad
review passes. Only his laugh after the goal is used (src 4.28-5.66 s, the
transcript's [laughs]); the last yell is under a censor bleep, as the
"*BLEEP*" on screen says.

Every time comes from src/ad/ad.json, the file the Remotion edit reads.
Loudness: -14 LUFS integrated, true peak at or under -1 dBTP.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROMO, "audio"))
sys.path.insert(0, os.path.join(PROMO, "reel"))
import make_audio as ma                                        # noqa: E402
from make_reel_audio import bass808, cowbell, lufs_of, master  # noqa: E402

AD = json.load(open(os.path.join(PROMO, "src", "ad", "ad.json")))
SR = ma.SR
BEAT = 60.0 / AD["bpm"]
STEP = BEAT / 4
BAR = 4 * BEAT
TOTAL = AD["beats"] * BEAT                     # 26.25 s
S = AD["scenes"]
CUE = AD["cues"]
CLIP = os.path.join(HERE, "duxxion-goal.mp4")
OUT = os.path.join(PROMO, "public", "ad")
rng = np.random.default_rng(20261102)

SLOWMO = CUE["slowmo"][0]                      # the build starts with the slow-mo
GAP = S["goal"] - BEAT / 2                     # half a beat of silence before the drop


# ── instruments ──────────────────────────────────────────────────────────────

ROOTS = [42, 42, 38, 37]                       # F#2 F#2 D2 C#2: i i VI V
BELL = [
    (66, None, 73, None, 69, 66, None, 73, None, 71, 69, None, 66, None, 64, None),
    (66, None, 73, None, 69, 66, None, 74, None, 73, 69, None, 71, None, 69, None),
    (66, None, 74, None, 69, 66, None, 74, None, 73, 69, None, 66, None, 64, None),
    (65, None, 73, None, 68, 65, None, 73, None, 71, 68, None, 65, None, 73, None),
]
KICKS = [(0, 3, 6, 10), (0, 3, 6, 10, 13)]
CLAPS = (4, 12)
TOMS = ((2, 54), (7, 50), (9, 54), (14, 47))   # the tamborzao's answer


def tom(midi: float, length_s=0.22) -> np.ndarray:
    t = ma.tt(length_s)
    f0 = ma.hz(midi)
    f = f0 * (1 + 0.6 * np.exp(-t / 0.02))
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.09)
    skin = ma.bp(rng.standard_normal(len(t)), 600, 3000) * np.exp(-t / 0.012) * 0.35
    return np.tanh(1.8 * (body + skin)) * 0.8


TOM = {m: tom(m) for _, m in TOMS}


def section(t: float) -> str:
    if t < S["rewind"]:
        return "drop"
    if t < S["replay"]:
        return "off"
    if t < SLOWMO:
        return "groove"
    if t < S["goal"]:
        return "build"
    return "drop"


def render_music() -> dict[str, np.ndarray]:
    n = ma.ns(TOTAL + 3)
    drums, perc, bass, bell = (np.zeros((2, n)) for _ in range(4))
    bars = math.ceil(TOTAL / BAR)
    for b in range(bars):
        t0 = b * BAR
        k = b % 4
        root = ROOTS[k]
        sec0 = section(t0 + 1e-6)
        # The cowbell riff: in every section but the silence.
        for step, note in enumerate(BELL[k]):
            at = t0 + step * STEP
            sec = section(at + 1e-6)
            if note is None or sec == "off" or at >= TOTAL - BEAT * 2 or (GAP <= at < S["goal"]):
                continue
            g = 0.5 if step % 4 == 0 else 0.4
            if sec == "groove":
                g *= 0.8
            ma.add(bell, cowbell(note), at, g, pan=0.15)
            ma.add(bell, cowbell(note + 12, 0.16), at + 3 * STEP, 0.08, pan=-0.4)
        # Drums and 808.
        if sec0 == "off":
            continue
        kicks = KICKS[b % 2]
        for step in range(16):
            at = t0 + step * STEP
            sec = section(at + 1e-6)
            if sec == "off" or GAP <= at < S["goal"] or at >= TOTAL - BEAT:
                continue
            full = sec == "drop"
            if step in kicks and (full or step in (0, 10)):
                ma.add(drums, ma.KICK, at, 0.85 if full else 0.6)
            if step in CLAPS and (full or step == 12) and sec != "build":
                ma.add(drums, ma.CLAP, at, 0.7)
            if sec != "build":
                if full or step % 2 == 0:
                    ma.add(drums, ma.HAT_C, at, (0.5 if step % 4 == 0 else 0.32) * (1 if full else 0.8), pan=-0.25)
                if full and step == 14 and b % 2 == 1:
                    ma.add(drums, ma.HAT_O, at, 0.35, pan=0.3)
            for s_, m in TOMS:
                if step == s_ and full:
                    ma.add(perc, TOM[m], at, 0.42, pan=0.35 if m > 50 else -0.3)
        # The 808: a note per kick, sliding into the next bar's root.
        nxt = ROOTS[(b + 1) % 4]
        starts = list(kicks) + [16]
        for k0, k1 in zip(starts, starts[1:]):
            at = t0 + k0 * STEP
            sec = section(at + 1e-6)
            if sec in ("off", "build") or GAP <= at < S["goal"] or at >= TOTAL - BEAT:
                continue
            if sec == "groove" and k0 not in (0, 10):
                continue
            length = (k1 - k0) * STEP
            glide = nxt if (k1 == 16 and nxt != root) else None
            ma.add(bass, bass808(root, length, glide), at, 0.85 if sec == "drop" else 0.45)
    # The end card: one long 808 under the last beats, the riff's tail in the room.
    ma.add(bass, bass808(ROOTS[0], BEAT * 2), TOTAL - BEAT * 2, 0.8)
    return {"drums": drums, "perc": perc, "bass": bass, "bell": bell}


def sweep_down(x: np.ndarray, a: float, b: float, f0=7000.0, f1=380.0) -> np.ndarray:
    """Low-pass falling from f0 to f1 between a and b seconds (crossfaded
    fixed filters: smooth, and no filter-state clicks)."""
    cuts = np.geomspace(f0, f1, 6)
    ia, ib = ma.ns(a), min(x.shape[1], ma.ns(b))
    seg = x[:, ia:ib]
    versions = [ma.lp(seg, c) for c in cuts]
    pos = np.linspace(0, len(cuts) - 1, seg.shape[1])
    out = np.zeros_like(seg)
    for i, v in enumerate(versions):
        out += v * np.clip(1 - np.abs(pos - i), 0, 1)
    y = x.copy()
    y[:, ia:ib] = out
    return y


def tape_stop(x: np.ndarray, at: float, until: float, dur=0.42) -> np.ndarray:
    """From `at`, the tape slows to a stop over `dur`; silence until `until`."""
    i = ma.ns(at)
    m = ma.ns(dur)
    tau = np.arange(m) / SR
    pos = i + (tau - tau ** 2 / (2 * dur)) * SR          # samples read, slowing to zero
    y = x.copy()
    for c in range(2):
        y[c, i:i + m] = np.interp(pos, np.arange(x.shape[1]), x[c]) * np.linspace(1, 0.2, m)
    y[:, i + m:ma.ns(until)] = 0
    return y


def rewind_sound(music: np.ndarray, a: float, b: float, rate: float) -> np.ndarray:
    """The VHS rewind: the music before it, backwards, at the picture's speed,
    a little wow and some tape hiss."""
    length = b - a
    src_len = length * rate
    seg = music[:, :ma.ns(src_len) + 10][:, ::-1]
    m = ma.ns(length)
    t = np.arange(m) / SR
    wow = 1 + 0.03 * np.sin(2 * np.pi * 3.1 * t)
    pos = np.cumsum(np.full(m, rate) * wow)
    pos = np.clip(pos, 0, seg.shape[1] - 1)
    y = np.stack([np.interp(pos, np.arange(seg.shape[1]), seg[c]) for c in range(2)])
    y = ma.lp(ma.hp(y, 250), 5200) * 0.7
    hiss = ma.hp(rng.standard_normal((2, m)), 3500) * 0.03
    env = np.minimum(1, np.minimum(t / 0.05, (length - t) / 0.02))
    return (y + hiss) * env


# ── sound effects ────────────────────────────────────────────────────────────

def sfx_scratch() -> np.ndarray:
    """A record scratch: a back-and-forth pull, band-passed noise and tone."""
    L = 0.42
    t = ma.tt(L)
    motion = np.sin(np.pi * np.clip(t / 0.16, 0, 1)) * (t < 0.16) + 0.6 * np.sin(np.pi * np.clip((t - 0.19) / 0.2, 0, 1)) * (t >= 0.19)
    f = 260 + 2600 * motion
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR)
    noise = rng.standard_normal(len(t))
    hi = np.zeros(len(t))
    for c in (700, 1400, 2800):
        hi += ma.bp(noise, c * 0.7, c * 1.4) * np.clip(motion * 3 - (c / 1400 - 0.5), 0, 1)
    x = np.tanh(2.2 * (0.6 * tone + 0.5 * hi)) * (0.15 + motion)
    return ma.pan2(x * 0.8)


def sfx_boing() -> np.ndarray:
    """A cartoon spring: a pitch wobble that widens, then settles."""
    t = ma.tt(0.55)
    f = 180 * (1 + 0.9 * np.minimum(t / 0.08, 1)) * (1 + 0.28 * np.sin(2 * np.pi * 14 * t) * np.exp(-t / 0.35))
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.25 * np.sin(4 * np.pi * np.cumsum(f) / SR)
    x *= np.minimum(1, t / 0.005) * np.exp(-t / 0.22)
    return ma.pan2(x * 0.6, 0.1)


def sfx_slowdown() -> np.ndarray:
    """Into the slow-mo: a falling whoosh with a sinking tone."""
    t = ma.tt(0.9)
    f = 900 * (80 / 900) ** (t / 0.9)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.4
    air = ma.lp(rng.standard_normal(len(t)), 3000) * 0.6
    env = np.exp(-t / 0.35) * np.minimum(1, t / 0.02)
    return ma.verb(ma.pan2((tone + air) * env)) * 0.5 + ma.pan2((tone + air) * env)


def sfx_bleep(length_s: float) -> np.ndarray:
    t = ma.tt(length_s)
    x = np.sin(2 * np.pi * 1000 * t)
    x *= np.minimum(1, np.minimum(t / 0.004, (length_s - t) / 0.004))
    return ma.pan2(x)


def render_sfx() -> np.ndarray:
    buf = np.zeros((2, ma.ns(TOTAL + 3)))
    for at in CUE["drop"]:
        ma.add(buf, ma.pan2(ma.impact()), at, 0.75)
    for at in CUE["zoom_punch"]:
        ma.add(buf, ma.pan2(ma.sfx_slam()), at, 0.55)
        w, lead = ma.sfx_whoosh(0.3, 0.2)
        ma.add(buf, w, at - lead, 0.35)
    for at in CUE["scratch"]:
        ma.add(buf, sfx_scratch(), at, 0.8)
    for at in CUE["play_click"]:
        ma.add(buf, ma.pan2(ma.sfx_click()), at, 0.9)
    ma.add(buf, sfx_slowdown(), CUE["slowmo"][0], 0.6)
    # The build into the goal: a riser and an accelerating roll, then the gap.
    r = ma.riser(GAP - SLOWMO, 300, 7000)
    ma.add(buf, ma.pan2(r), SLOWMO, 0.5)
    roll = np.zeros_like(buf)
    ma.snare_roll(roll, (SLOWMO + BEAT) / BEAT, GAP / BEAT, gain=0.45)
    buf += roll
    for at in CUE["boing"]:
        ma.add(buf, sfx_boing(), at, 0.5)
    for at in CUE["pop"]:
        ma.add(buf, ma.pan2(ma.sfx_pop(), 0.15), at, 0.55)
    for at in CUE["whoosh"]:
        w, lead = ma.sfx_whoosh()
        ma.add(buf, w, at - lead, 0.45)
    for at in CUE["slam"]:
        ma.add(buf, ma.pan2(ma.sfx_slam()), at, 0.75)
    for at in CUE["coins"]:
        ma.add(buf, ma.sfx_coin(), at, 0.7)
    a, b = CUE["ticker"]
    tick = a + 0.2
    while tick < b:
        ma.add(buf, ma.pan2(ma.sfx_tick(), 0.3), tick, 0.22)
        tick += STEP / 2
    return buf


# ── his laugh, and the bleep ─────────────────────────────────────────────────

def clip_audio() -> tuple[np.ndarray, np.ndarray]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", CLIP, "-vn", "-ac", "2", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    src = np.frombuffer(raw, dtype=np.int16).astype(np.float64).reshape(-1, 2).T / 32768
    laugh = np.zeros((2, ma.ns(TOTAL + 3)))
    for seg in AD["clip_audio"]:
        L = seg["to"] - seg["from"]
        piece = src[:, ma.ns(seg["src"]):ma.ns(seg["src"] + L)].copy()
        f = ma.ns(0.015)
        piece[:, :f] *= np.linspace(0, 1, f)
        piece[:, -f:] *= np.linspace(1, 0, f)
        ma.add(laugh, piece, seg["from"])
    a, b = AD["bleep"]
    bleep = np.zeros_like(laugh)
    ma.add(bleep, sfx_bleep(b - a), a)
    return laugh, bleep


# ── style 2: playful trap (owner, 2026-11-02: "different music") ─────────────
#
# The "mischievous" beat of funny edits: a sneaky marimba hook and pizzicato
# strings over trap drums in half time (the snare on beat 3), rolling
# hi-hats and a long sliding 808. D minor, i-i-VI-V (Dm Dm Bb A).

def pizz(midi: float, length_s=0.4, decay=0.996) -> np.ndarray:
    """A plucked string (Karplus-Strong), damped short like a pizzicato."""
    from scipy import signal as sg
    f = ma.hz(midi)
    N = max(2, int(round(SR / f - 0.5)))          # the averaging adds half a sample
    n = ma.ns(length_s)
    x = np.zeros(n)
    # Excited by the string's shape at the moment it is plucked (a triangle,
    # plucked a third of the way along): its harmonics fall as 1/k^2, so the
    # note itself leads. Plain noise made a buzzy, random-sounding tone.
    pos = int(N * 0.3)
    shape = np.concatenate([np.linspace(0, 1, pos, endpoint=False), np.linspace(1, 0, N - pos)])
    x[:N] = shape - shape.mean() + ma.lp(rng.uniform(-1, 1, N), 2000) * 0.08
    a = np.zeros(N + 2)
    a[0], a[N], a[N + 1] = 1.0, -decay * 0.5, -decay * 0.5
    y = sg.lfilter([1.0], a, x)
    t = np.arange(n) / SR
    y = ma.lp(ma.hp(y * np.exp(-t / 0.17), 90), 2600)
    return y / (np.abs(y).max() + 1e-9) * 0.8


def marimba(midi: float, length_s=0.6) -> np.ndarray:
    t = ma.tt(length_s)
    f = ma.hz(midi)
    x = (np.sin(2 * np.pi * f * t) * np.exp(-t / 0.32)
         + 0.42 * np.sin(2 * np.pi * 3.99 * f * t) * np.exp(-t / 0.07)
         + 0.10 * np.sin(2 * np.pi * 10.6 * f * t) * np.exp(-t / 0.018))
    x += ma.bp(rng.standard_normal(len(t)), 1500, 5000) * np.exp(-t / 0.0025) * 0.15
    return x * np.minimum(1, t / 0.0015) * 0.8


def sub808(midi: float, length_s: float, glide_to: float | None = None, glide_s=0.12) -> np.ndarray:
    """A long, clean 808 with a little drive, sliding into the next note."""
    t = ma.tt(length_s)
    f0 = ma.hz(midi)
    f = f0 * (1 + 0.5 * np.exp(-t / 0.015))
    if glide_to is not None:
        g0 = max(0.0, length_s - glide_s)
        m = t > g0
        f[m] = f0 * (ma.hz(glide_to) / f0) ** ((t[m] - g0) / glide_s)
    x = np.tanh(1.8 * np.sin(2 * np.pi * np.cumsum(f) / SR)) / np.tanh(1.8)
    env = np.minimum(1, t / 0.003) * np.exp(-t / 1.4) * np.minimum(1, (length_s - t) / 0.03)
    return ma.lp(x * env, 1000)


def trap_snare() -> np.ndarray:
    t = ma.tt(0.4)
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05) * 0.6
    noise = ma.bp(rng.standard_normal(len(t)), 1200, 9000) * np.exp(-t / 0.09)
    x = np.tanh(1.5 * (body + noise)) * 0.8
    x[: len(ma.CLAP)] += ma.CLAP[: len(t)] * 0.5
    return x


SNARE = trap_snare()
T_ROOTS = [38, 38, 34, 33]                                  # D2 D2 Bb1 A1
T_MEL = [
    ((0, 74), (3, 74), (5, 77), (8, 69), (10, 68), (12, 69)),
    ((0, 74), (3, 74), (5, 77), (8, 81), (10, 79), (12, 77), (14, 76)),
    ((0, 70), (3, 70), (5, 74), (8, 77), (10, 76), (12, 74)),
    ((0, 69), (3, 69), (5, 73), (8, 76), (10, 73), (12, 69), (14, 68)),
]
T_PIZZ = [
    (50, 57, 62, 57, 53, 57, 62, 57),
    (50, 57, 62, 57, 53, 57, 62, 57),
    (46, 53, 58, 53, 50, 53, 58, 53),
    (45, 52, 57, 52, 49, 52, 57, 52),
]
T_KICKS = [(0, 7, 10), (0, 3, 10, 13)]


def ok(at: float, *secs: str) -> bool:
    """A note may sound at `at` in these sections: never in the silence after
    the scratch, the half beat before the goal, or the last beat."""
    return section(at + 1e-6) in secs and not (GAP <= at < S["goal"]) and at < TOTAL - BEAT


def render_trap() -> np.ndarray:
    n = ma.ns(TOTAL + 3)
    drums, bass, keys, strings = (np.zeros((2, n)) for _ in range(4))
    for b in range(math.ceil(TOTAL / BAR)):
        t0, k = b * BAR, b % 4
        lift = t0 >= S["goal"]                      # after the goal: the glockenspiel doubles the hook
        for step, note in T_MEL[k]:
            at = t0 + step * STEP
            if ok(at, "drop", "groove", "build"):
                ma.add(keys, marimba(note), at, 0.55, pan=0.12)
                if lift or b == 0:
                    ma.add(keys, marimba(note + 12, 0.35), at, 0.16, pan=-0.3)
        for i, note in enumerate(T_PIZZ[k]):
            at = t0 + i * 2 * STEP
            if ok(at, "drop", "groove", "build"):
                ma.add(strings, pizz(note), at, 0.30 if i % 2 else 0.38, pan=-0.25 + 0.5 * (i % 2))
        kicks = T_KICKS[b % 2]
        for step in range(16):
            at = t0 + step * STEP
            if ok(at, "drop"):
                if step in kicks:
                    ma.add(drums, ma.KICK, at, 0.8)
                if step == 8:
                    ma.add(drums, SNARE, at, 0.75)
            if ok(at, "drop", "groove") and step % 2 == 0:
                ma.add(drums, ma.HAT_C, at, 0.42 if step % 4 == 0 else 0.3, pan=-0.2)
        # The rolls that make it trap: a triplet run, then 32nds into the next bar.
        if b % 4 == 1:
            for s_ in (6, 6 + 2 / 3, 6 + 4 / 3):
                at = t0 + s_ * STEP
                if ok(at, "drop", "groove"):
                    ma.add(drums, ma.HAT_C, at, 0.26, pan=-0.2)
        if b % 4 == 3:
            for s_ in np.arange(12, 16, 0.5):
                at = t0 + s_ * STEP
                if ok(at, "drop", "groove"):
                    ma.add(drums, ma.HAT_C, at, 0.18 + 0.03 * (s_ - 12), pan=-0.2)
        if b % 2 == 0 and ok(t0 + 14 * STEP, "drop"):
            ma.add(drums, ma.HAT_O, t0 + 14 * STEP, 0.3, pan=0.3)
        root, nxt = T_ROOTS[k], T_ROOTS[(b + 1) % 4]
        starts = list(kicks) + [16]
        for k0, k1 in zip(starts, starts[1:]):
            at = t0 + k0 * STEP
            if ok(at, "drop"):
                glide = nxt if (k1 == 16 and nxt != root) else None
                ma.add(bass, sub808(root + 12, (k1 - k0) * STEP, glide + 12 if glide else None), at, 0.8)
    ma.add(bass, sub808(T_ROOTS[0] + 12, BEAT * 2), TOTAL - BEAT * 2, 0.8)
    ma.add(keys, marimba(74, 1.2), TOTAL - BEAT * 2, 0.5)
    ma.add(keys, marimba(62, 1.2), TOTAL - BEAT * 2, 0.4)
    music = drums * 0.9 + bass * 0.8 + keys * 1.0 + strings * 0.75
    return ma.hp(music + ma.verb(keys + strings) * 0.2, 30)


# ── style 3: jersey club ─────────────────────────────────────────────────────
#
# Bouncy and goofy: kicks on 1, 2, 3 and a triplet run across beat 4, claps on
# 2 and 4, the bed-squeak on the off-beats, chord stabs and a pluck hook.
# A minor, i-VI-III-VII (Am F C G).

def squeak() -> np.ndarray:
    """The bed squeak, synthesized: a short falling chirp with a wobble."""
    t = ma.tt(0.13)
    f = 2300 * (1450 / 2300) ** (t / 0.13) * (1 + 0.04 * np.sin(2 * np.pi * 38 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(ph) + 0.3 * np.sin(2 * ph)
    x = x * np.minimum(1, t / 0.004) * np.exp(-t / 0.05)
    x += ma.bp(rng.standard_normal(len(t)), 1800, 5000) * np.exp(-t / 0.02) * 0.15
    return x * 0.6


SQUEAK = squeak()
J_ROOTS = [45, 41, 48, 43]                                  # A2 F2 C3 G2
J_CHORDS = [(57, 60, 64, 69), (57, 60, 65, 69), (55, 60, 64, 67), (55, 59, 62, 67)]
J_LEAD = [
    ((0, 76), (3, 76), (6, 74), (8, 72), (10, 74), (12, 76)),
    ((0, 77), (3, 77), (6, 76), (8, 74), (10, 72), (12, 74)),
    ((0, 79), (3, 79), (6, 76), (8, 72), (10, 76), (12, 79)),
    ((0, 79), (3, 79), (6, 76), (8, 74), (10, 71), (12, 74), (14, 79)),
]
J_STABS = (0, 3, 6, 10, 12)
J_KICKS = [(0, 4, 8, 12, 12 + 4 / 3, 12 + 8 / 3), (0, 4, 8, 10, 12, 12 + 4 / 3, 12 + 8 / 3)]
J_SQUEAKS = [(6, 14), (2, 10)]


def render_jersey() -> np.ndarray:
    n = ma.ns(TOTAL + 3)
    drums, bass, stabs, lead = (np.zeros((2, n)) for _ in range(4))
    STAB = {k: ma.supersaw(c, 0.15, bright=4200) * ma.adsr(0.15, a=0.003, d=0.05, s=0.6, r=0.05)
            for k, c in enumerate(J_CHORDS)}
    for b in range(math.ceil(TOTAL / BAR)):
        t0, k = b * BAR, b % 4
        for step, note in J_LEAD[k]:
            at = t0 + step * STEP
            if ok(at, "drop", "groove", "build"):
                ma.add(lead, ma.pluck(note, 0.3, 5200), at, 0.55, pan=0.1)
                ma.add(lead, ma.pluck(note + 12, 0.2, 6000), at, 0.14, pan=-0.3)
        for step in J_STABS:
            at = t0 + step * STEP
            if ok(at, "drop", "build"):
                ma.add(stabs, STAB[k], at, 0.5)
            elif ok(at, "groove"):
                ma.add(stabs, STAB[k], at, 0.38)
        for s_ in J_KICKS[b % 2]:
            at = t0 + s_ * STEP
            if ok(at, "groove") and s_ in (0, 8):          # the replay keeps a pulse
                ma.add(drums, ma.KICK, at, 0.55)
            if ok(at, "drop"):
                ma.add(drums, ma.KICK, at, 0.8 if s_ < 12 else 0.7)
                if s_ in (0, 8):
                    ma.add(bass, ma.bass(J_ROOTS[k], 0.42), at, 0.55)
        for step in range(16):
            at = t0 + step * STEP
            if ok(at, "drop") and step in (4, 12):
                ma.add(drums, ma.CLAP, at, 0.75)
            if ok(at, "drop", "groove"):
                ma.add(drums, ma.HAT_C, at, 0.34 if step % 4 == 2 else 0.2, pan=-0.2)
                if step in J_SQUEAKS[b % 2]:
                    ma.add(drums, SQUEAK, at, 0.55, pan=0.3)
    ma.add(stabs, ma.supersaw(J_CHORDS[0], 1.0, bright=3800) * ma.adsr(1.0, a=0.005, d=0.3, s=0.5, r=0.4),
           TOTAL - BEAT * 2, 0.5)
    ma.add(bass, ma.bass(J_ROOTS[0], 0.9), TOTAL - BEAT * 2, 0.55)
    music = drums * 0.9 + bass * 0.8 + stabs * 0.8 + lead * 1.0
    return ma.hp(music + ma.verb(stabs + lead) * 0.18, 30)


def render_phonk() -> np.ndarray:
    stems = render_music()
    music = stems["drums"] * 0.9 + stems["perc"] * 0.8 + stems["bass"] * 0.7 + stems["bell"] * 1.0
    music = music + ma.verb(stems["bell"] + stems["perc"]) * 0.16
    return ma.hp(music, 30)


STYLES = {"trap": render_trap, "jersey": render_jersey, "phonk": render_phonk}


# ── mix ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=sorted(STYLES), default="trap")
    style = ap.parse_args().style
    music = STYLES[style]()
    # The rewind's sound is the hook's music, backwards (the hook bar looped,
    # so the reversed run never reaches the silence after the scratch).
    seg = AD["footage"][3]
    hook = music[:, :ma.ns(S["rewind"])]
    rew = rewind_sound(np.concatenate([hook] * 4, axis=1), seg["from"], seg["to"], abs(seg["rate"]))
    music = tape_stop(music, S["rewind"], S["replay"])
    ma.add(music, rew, seg["from"], 1.0)
    # The replay comes back muffled and opens up over its first bar.
    music = sweep_down(music, S["replay"], S["replay"] + BAR, 900, 9000)
    # The slow-mo: the music sinks under the build.
    music = sweep_down(music, SLOWMO, GAP, 6000, 350)
    music = music[:, :ma.ns(TOTAL)]

    sfx = render_sfx()[:, :ma.ns(TOTAL)]
    laugh, bleep = clip_audio()
    laugh, bleep = laugh[:, :ma.ns(TOTAL)], bleep[:, :ma.ns(TOTAL)]

    # Levels before the master: music the bed, effects on top, his laugh clear.
    music *= 10 ** ((-17.0 - lufs_of(music)) / 20)
    sfx *= 10 ** ((-21.0 - lufs_of(sfx)) / 20)
    laugh *= 10 ** ((-14.5 - lufs_of(laugh[:, :ma.ns(2.0)])) / 20)
    bleep *= 0.31
    music = ma.duck(music, sfx, depth_db=4.0)
    music = ma.duck(music, laugh + bleep, depth_db=5.0)
    mix = master(music + sfx + laugh + bleep, TOTAL)

    os.makedirs(os.path.join(OUT, "stems", style), exist_ok=True)
    path = os.path.join(OUT, f"duxxion-ad-{style}.wav")
    ma.write(path, mix)
    if style == "trap":                              # the default the render script muxes
        ma.write(os.path.join(OUT, "duxxion-ad.wav"), mix)
    for name, x in (("music", music), ("sfx", sfx), ("laugh", laugh), ("bleep", bleep)):
        ma.write(os.path.join(OUT, "stems", style, name + ".wav"), x * 0.5)
    lufs, tp = ma.loudness(path)
    print(f"{path}: {mix.shape[1] / SR:.3f} s, {lufs:.1f} LUFS, true peak {tp:.1f} dBTP")


if __name__ == "__main__":
    main()
