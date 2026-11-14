#!/usr/bin/env python3
"""The GTA 6 breaking-news ad's soundtrack: the anchor's own voice for the
hook, then original music and sound design. No added voice-over.

    python3 ad/make_gta_audio.py      # -> public/ad-gta/gta-ad.wav (+ stems)

THE VOICE is the owner's clip, untouched, for the hook only (0-5.625 s):
"Breaking. GTA 6 is coming. Who's clipping the biggest moments first?"
(a speech-to-text pass with word timings; nothing in it needs muting). His
clip carries a faint steady tone near 1480 Hz under the voice, which is an
F#, so the music is in F# minor and the tone sits in the key instead of
against it.

THE MUSIC is synthesized here (nothing sampled, nothing to license), 128 BPM,
the playful trap the owner kept for the Duxxion ad (marimba hook, pizzicato
strings, half-time drums, sliding 808), moved up to F# minor and arranged to
this edit:
  0:00.000  a newsroom pulse under the anchor: ticking hats and pizzicato,
            no drums, well under his voice; a riser and a roll in bar 3
  0:05.391  half a beat of nothing, so "first?" lands alone
  0:05.625  the drop, on "YOU ARE."
  0:11.250  the spike bar is a build: a kick on every beat, the 808 out, a
            riser and a roll, then a half-beat gap
  0:13.125  the drop again, on "already a clip"
  0:16.875  "everyone else": the music goes dull and muffled for the bar
  0:18.750  it opens back up for "free"; runs to the end card and ends on it

Every time comes from src/ad/gta.json, the file the Remotion edit reads.
Loudness: -14 LUFS integrated, true peak at or under -1 dBTP.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROMO, "audio"))
sys.path.insert(0, os.path.join(PROMO, "reel"))
sys.path.insert(0, HERE)
import make_audio as ma                                  # noqa: E402
from make_reel_audio import duck_under, lufs_of, master   # noqa: E402
import make_ad_audio as dux                               # noqa: E402  (the instruments)

AD = json.load(open(os.path.join(PROMO, "src", "ad", "gta.json")))
SR = ma.SR
BEAT = 60.0 / AD["bpm"]
STEP = BEAT / 4
BAR = 4 * BEAT
TOTAL = AD["beats"] * BEAT                     # 24.375 s
S = AD["scenes"]
CUE = AD["cues"]
HOOK = AD["hook_s"]
CLIP = os.path.join(HERE, "gta-hook.mov")
OUT = os.path.join(PROMO, "public", "ad-gta")
rng = np.random.default_rng(20261114)

GAP1 = S["answer"] - BEAT / 2                  # the half beat before "YOU ARE."
GAP2 = S["clip"] - BEAT / 2                    # ...and before "already a clip"
UP = 4                                         # D minor -> F# minor

ROOTS = [r + UP for r in dux.T_ROOTS]          # F#2 F#2 D2 C#2
MEL = [tuple((st, n + UP) for st, n in bar) for bar in dux.T_MEL]
PIZZ = [tuple(n + UP for n in bar) for bar in dux.T_PIZZ]
KICKS = dux.T_KICKS


def section(t: float) -> str:
    if t < S["answer"]:
        return "news"
    if S["spike"] <= t < S["clip"]:
        return "build"
    return "drop"


def ok(at: float, *secs: str) -> bool:
    """A note may sound at `at` in these sections: never in either half-beat
    gap, and never in the last beat."""
    if GAP1 <= at < S["answer"] or GAP2 <= at < S["clip"] or at >= TOTAL - BEAT:
        return False
    return section(at + 1e-6) in secs


def render_music() -> np.ndarray:
    n = ma.ns(TOTAL + 3)
    drums, bass, keys, strings = (np.zeros((2, n)) for _ in range(4))
    for b in range(math.ceil(TOTAL / BAR)):
        t0, k = b * BAR, b % 4
        for step, note in MEL[k]:
            at = t0 + step * STEP
            if ok(at, "drop", "build"):
                ma.add(keys, dux.marimba(note), at, 0.55, pan=0.12)
                if t0 >= S["clip"]:                 # after the second drop the hook is doubled an octave up
                    ma.add(keys, dux.marimba(note + 12, 0.35), at, 0.16, pan=-0.3)
        for i, note in enumerate(PIZZ[k]):
            at = t0 + i * 2 * STEP
            if ok(at, "news", "drop", "build"):
                g = 0.30 if i % 2 else 0.38
                ma.add(strings, dux.pizz(note), at, g * (0.8 if section(at) == "news" else 1.0), pan=-0.25 + 0.5 * (i % 2))
        kicks = KICKS[b % 2]
        for step in range(16):
            at = t0 + step * STEP
            if ok(at, "drop"):
                if step in kicks:
                    ma.add(drums, ma.KICK, at, 0.8)
                if step == 8:
                    ma.add(drums, dux.SNARE, at, 0.75)
            if ok(at, "drop", "build") and step % 2 == 0:
                ma.add(drums, ma.HAT_C, at, 0.42 if step % 4 == 0 else 0.3, pan=-0.2)
            # The build: a kick on every beat, a pulse climbing with the score.
            if ok(at, "build") and step % 4 == 0:
                ma.add(drums, ma.KICK, at, 0.62)
            # The newsroom: a tick on every 16th, the wire-service clatter.
            if ok(at, "news"):
                ma.add(drums, ma.HAT_C, at, 0.20 if step % 4 == 0 else 0.11, pan=0.25 if step % 2 else -0.25)
        if b % 4 == 1:
            for s_ in (6, 6 + 2 / 3, 6 + 4 / 3):
                at = t0 + s_ * STEP
                if ok(at, "drop"):
                    ma.add(drums, ma.HAT_C, at, 0.26, pan=-0.2)
        if b % 4 == 3:
            for s_ in np.arange(12, 16, 0.5):
                at = t0 + s_ * STEP
                if ok(at, "drop"):
                    ma.add(drums, ma.HAT_C, at, 0.18 + 0.03 * (s_ - 12), pan=-0.2)
        if b % 2 == 0 and ok(t0 + 14 * STEP, "drop"):
            ma.add(drums, ma.HAT_O, t0 + 14 * STEP, 0.3, pan=0.3)
        root, nxt = ROOTS[k], ROOTS[(b + 1) % 4]
        starts = list(kicks) + [16]
        for k0, k1 in zip(starts, starts[1:]):
            at = t0 + k0 * STEP
            if ok(at, "drop"):
                glide = nxt if (k1 == 16 and nxt != root) else None
                ma.add(bass, dux.sub808(root + 12, (k1 - k0) * STEP, glide + 12 if glide else None), at, 0.8)
        # Under the anchor: one soft low note at the top of each bar.
        if section(t0 + 1e-6) == "news":
            ma.add(bass, dux.sub808(root + 12, BEAT * 1.5), t0, 0.42)
    # The last bar ends on the tonic: a held 808 and the hook's first note.
    ma.add(bass, dux.sub808(ROOTS[0] + 12, BEAT * 2), TOTAL - BEAT * 2, 0.8)
    ma.add(keys, dux.marimba(78, 1.2), TOTAL - BEAT * 2, 0.5)
    ma.add(keys, dux.marimba(66, 1.2), TOTAL - BEAT * 2, 0.4)
    music = drums * 0.9 + bass * 0.8 + keys * 1.0 + strings * 0.75
    return ma.hp(music + ma.verb(keys + strings) * 0.2, 30)


# ── sound effects ────────────────────────────────────────────────────────────

def sfx_scan() -> np.ndarray:
    """The scan over the wall of streams: a rising, fluttering tone."""
    L = 2.4 * BEAT
    t = ma.tt(L)
    f = 420 * (1900 / 420) ** (t / L)
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = (np.sin(ph) + 0.3 * np.sin(2 * ph)) * (0.6 + 0.4 * np.sin(2 * np.pi * 17 * t))
    air = ma.bp(rng.standard_normal(len(t)), 2000, 9000) * 0.25
    env = np.minimum(1, t / 0.05) * np.minimum(1, (L - t) / 0.12)
    y = (x * 0.35 + air) * env
    a = (np.linspace(-0.6, 0.6, len(t)) + 1) * math.pi / 4
    return np.stack([y * np.cos(a), y * np.sin(a)]) * 0.6


def sfx_ding() -> np.ndarray:
    """A bright two-note chime in the key (F#6 then C#7)."""
    out = np.zeros((2, ma.ns(1.3)))
    ma.add(out, dux.marimba(90, 1.0), 0.0, 0.6, pan=-0.1)
    ma.add(out, dux.marimba(97, 1.1), 0.09, 0.6, pan=0.15)
    return out + ma.verb(out) * 0.35


def sfx_sad() -> np.ndarray:
    """"Everyone else": two notes sagging downwards, a muted wah."""
    out = np.zeros((2, ma.ns(1.4)))
    for at, m0, m1 in ((0.0, 61, 60), (0.42, 58, 54)):
        L = 0.6
        t = ma.tt(L)
        f = ma.hz(m0) * (ma.hz(m1) / ma.hz(m0)) ** (t / L) * (1 + 0.012 * np.sin(2 * np.pi * 6 * t))
        ph = 2 * np.pi * np.cumsum(f) / SR
        x = ma.lp(np.sign(np.sin(ph)) * 0.5 + np.sin(ph), 1100)
        x *= np.minimum(1, t / 0.02) * np.minimum(1, (L - t) / 0.12)
        ma.add(out, x * 0.5, at, 1.0, pan=-0.1)
    return out


def render_sfx() -> np.ndarray:
    buf = np.zeros((2, ma.ns(TOTAL + 3)))
    for at in CUE["ident"]:
        ma.add(buf, ma.pan2(ma.impact()), at, 0.45)
    for at in CUE["drop"]:
        ma.add(buf, ma.pan2(ma.impact()), at, 0.8)
    for at in CUE["slam"]:
        ma.add(buf, ma.pan2(ma.sfx_slam()), at, 0.75)
    for at in CUE["whoosh"]:
        w, lead = ma.sfx_whoosh()
        ma.add(buf, w, at - lead, 0.45)
    for at in CUE["pop"]:
        ma.add(buf, ma.pan2(ma.sfx_pop(), 0.15), at, 0.5 if at > HOOK else 0.3)
    # The tiles: a tick for each as the wall fills.
    a, b = CUE["tiles"]
    at = a + 0.07
    while at < b:
        ma.add(buf, ma.pan2(ma.sfx_tick(), float(rng.uniform(-0.6, 0.6))), at, 0.3)
        at += STEP / 2
    for at in CUE["scan"]:
        ma.add(buf, sfx_scan(), at + 0.08, 0.5)
    # Chat, speeding up; then the build into the clip.
    for off, _, _ in AD["chat"]:
        ma.add(buf, ma.pan2(ma.sfx_pop(), float(rng.uniform(-0.5, 0.5))), S["spike"] + off, 0.32)
    r0, r1 = CUE["riser"]
    ma.add(buf, ma.pan2(ma.riser(GAP2 - r0, 300, 7000)), r0, 0.95)
    roll = np.zeros_like(buf)
    ma.snare_roll(roll, (r0 + BEAT * 0.5) / BEAT, GAP2 / BEAT, gain=0.75)
    buf += roll
    # The same into the first drop, quieter: the anchor is still talking.
    ma.add(buf, ma.pan2(ma.riser(GAP1 - (S["answer"] - BAR), 300, 6000)), S["answer"] - BAR, 0.3)
    roll = np.zeros_like(buf)
    ma.snare_roll(roll, (S["answer"] - 2 * BEAT) / BEAT, GAP1 / BEAT, gain=0.3)
    buf += roll
    for at in CUE["click"]:
        ma.add(buf, ma.pan2(ma.sfx_click()), at, 0.9)
    for at in CUE["ding"]:
        ma.add(buf, sfx_ding(), at, 0.55)
    for at in CUE["sad"]:
        ma.add(buf, sfx_sad(), at + BEAT * 0.5, 0.42)
        ma.add(buf, dux.sfx_slowdown(), at, 0.32)
    for at in CUE["boing"]:
        ma.add(buf, dux.sfx_boing(), at, 0.5)
    return buf


# ── the anchor ───────────────────────────────────────────────────────────────

def anchor() -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", CLIP, "-vn", "-ac", "2", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    src = np.frombuffer(raw, dtype=np.int16).astype(np.float64).reshape(-1, 2).T / 32768
    piece = src[:, :ma.ns(HOOK)].copy()
    f = ma.ns(0.02)
    piece[:, :ma.ns(0.005)] *= np.linspace(0, 1, ma.ns(0.005))
    piece[:, -f:] *= np.linspace(1, 0, f)
    out = np.zeros((2, ma.ns(TOTAL + 3)))
    ma.add(out, ma.hp(piece, 70), 0.0)
    return out


# ── mix ──────────────────────────────────────────────────────────────────────

def main():
    music = render_music()
    # "Everyone else": the music goes dull for the bar, then opens for "free".
    music = dux.sweep_down(music, S["vod"], S["vod"] + BEAT * 1.5, 9000, 520)
    lo = ma.lp(music, 520)
    a, b = ma.ns(S["vod"] + BEAT * 1.5), ma.ns(S["free"] - BEAT * 0.5)
    music[:, a:b] = lo[:, a:b]
    music = dux.sweep_down(music, S["free"] - BEAT * 0.5, S["free"], 520, 9000)
    # The build bar thins out as it climbs (the riser and the roll take over).
    music = dux.sweep_down(music, S["spike"] + BEAT, GAP2, 8000, 2200)
    music = music[:, :ma.ns(TOTAL)]

    sfx = render_sfx()[:, :ma.ns(TOTAL)]
    voice = anchor()[:, :ma.ns(TOTAL)]

    # Levels before the master. Under the anchor the music is a bed, 13 LU
    # below him and ducked further while he speaks; from the drop it leads.
    voice *= 10 ** ((-15.0 - lufs_of(voice[:, :ma.ns(HOOK)])) / 20)
    h = ma.ns(HOOK)
    head, tail = music[:, :h].copy(), music[:, h:].copy()
    head *= 10 ** ((-28.0 - lufs_of(head)) / 20)
    tail *= 10 ** ((-16.5 - lufs_of(tail)) / 20)
    music = np.concatenate([head, tail], axis=1)
    sfx *= 10 ** ((-21.0 - lufs_of(sfx)) / 20)
    music = duck_under(music, voice, depth_db=5.0)
    music = ma.duck(music, sfx, depth_db=4.0)
    mix = master(music + sfx + voice, TOTAL)

    os.makedirs(os.path.join(OUT, "stems"), exist_ok=True)
    path = os.path.join(OUT, "gta-ad.wav")
    ma.write(path, mix)
    for name, x in (("music", music), ("sfx", sfx), ("voice", voice)):
        ma.write(os.path.join(OUT, "stems", name + ".wav"), x * 0.5)
    lufs, tp = ma.loudness(path)
    print(f"{path}: {mix.shape[1] / SR:.3f} s, {lufs:.1f} LUFS, true peak {tp:.1f} dBTP")


if __name__ == "__main__":
    main()
