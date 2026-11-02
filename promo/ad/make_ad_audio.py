#!/usr/bin/env python3
"""The Duxxion clip ad's soundtrack: original Brazilian-phonk-style music,
goofy sound design, and his laugh from the clip. No voice-over.

    python3 ad/make_ad_audio.py      # -> public/ad/duxxion-ad.wav (+ stems in public/ad/stems)

THE MUSIC is synthesized here (nothing sampled, nothing to license), in the
style that scores gaming edits right now: 128 BPM, a distorted 808 sliding
between notes, a melodic cowbell riff, a "tamborzao"-style kick pattern with
a tom answering it. F# minor, i-i-VI-V. The arrangement follows the edit:
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


# ── mix ──────────────────────────────────────────────────────────────────────

def main():
    stems = render_music()
    music = stems["drums"] * 0.9 + stems["perc"] * 0.8 + stems["bass"] * 0.7 + stems["bell"] * 1.0
    music = music + ma.verb(stems["bell"] + stems["perc"]) * 0.16
    music = ma.hp(music, 30)
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

    os.makedirs(os.path.join(OUT, "stems"), exist_ok=True)
    path = os.path.join(OUT, "duxxion-ad.wav")
    ma.write(path, mix)
    for name, x in (("music", music), ("sfx", sfx), ("laugh", laugh), ("bleep", bleep)):
        ma.write(os.path.join(OUT, "stems", name + ".wav"), x * 0.5)
    lufs, tp = ma.loudness(path)
    print(f"{path}: {mix.shape[1] / SR:.3f} s, {lufs:.1f} LUFS, true peak {tp:.1f} dBTP")


if __name__ == "__main__":
    main()
