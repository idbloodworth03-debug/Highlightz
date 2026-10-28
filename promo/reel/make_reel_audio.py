#!/usr/bin/env python3
"""Reel v2's soundtrack: a voice-over, a quiet phonk bed and transition hits,
laid under the owner's 20 s "Stop clipping for free" reel. The picture is
copied untouched; only the audio track is replaced.

VOICE. ElevenLabs "Liam" (young, energetic): one take of the whole script so
the read stays continuous, then cut into its six lines at the pauses and each
line placed on its scene. The take is cached in reel/vo/ so a re-mix spends no
characters; --new-take asks for a new one (needs ELEVENLABS_API_KEY). Duxxion
is said DUCKS-ee-on and Highlightz "highlights", hence the spellings in LINES.

MUSIC. Phonk at 128 BPM, synthesized from the promo's own drum and synth parts
(audio/make_audio.py): a pitched 808 cowbell riff, a distorted 808 that slides,
a halftime clap and trap hats. Original, so there is nothing to license. Kept
low and ducked under the voice: the owner wants the voice-over to lead.

TIMING. The reel cuts every 8 beats at 128 BPM (3.75 s), measured off its
frames; every cue below is in seconds on the reel's clock.

OUTPUT (out/):
  highlightz-reel-v2-stop-clipping-for-free.mp4   the reel with the new audio
  reel-v2-stems/{voice,music,sfx}.wav             before the master, to remix

    python3 reel/make_reel_audio.py reel/reel-v1.mov
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.request

import numpy as np
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROMO, "audio"))
import make_audio as ma  # noqa: E402  (the promo's synth parts and mastering)

SR = ma.SR
BEAT = 60.0 / 128
STEP = BEAT / 4
BAR = 4 * BEAT
OUT = os.path.join(PROMO, "out")
VO_DIR = os.path.join(HERE, "vo")
rng = np.random.default_rng(20261028)

VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ"          # Liam: young, energetic, social media
VOICE_MODEL = "eleven_v4"
VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.45,
                  "use_speaker_boost": True, "speed": 1.12}

# Each line and where it starts. Lines start just after their cut, so the
# whoosh lands on the cut and the voice follows it.
LINES = [
    ("Stop clipping for free. Top Ducks-ee-on clip wins two-fifty!", 0.08),
    ("Five hundred in prizes. Most views on one clip wins.", 3.85),
    ("Join free. Clip Ducks-ee-on's stream. Post from Highlights.", 7.60),
    ("Want an edge? Pro adds the Clip Editor and one-press posts.", 11.35),
    ("Start free. Upgrade anytime.", 15.08),
    ("Clip for two-fifty. Highlights dot app.", 17.05),
]
MAX_STRETCH = 1.10          # quicker than this starts to sound sped up

# What happens on screen (seconds), measured frame by frame off reel v1.
REVEAL_250 = 1.43           # "free" struck out, "$250" lands
GLITCHES = (3.52, 11.02)    # the RGB-split glitch into scenes 2 and 4
CUTS = (3.75, 7.50, 11.25, 15.00)
END_CARD = 16.875           # logo, "Clip for $250."
URL_PILL = 18.50            # highlightz.app lands


# ── voice ────────────────────────────────────────────────────────────────────

def new_take(take_mp3: str, take_json: str):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("ELEVENLABS_API_KEY is not set: add it in the environment settings")
    text = "\n".join(line for line, _ in LINES)
    body = json.dumps({"text": text, "model_id": VOICE_MODEL, "voice_settings": VOICE_SETTINGS}).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/with-timestamps"
        "?output_format=mp3_44100_128",
        data=body, headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    os.makedirs(VO_DIR, exist_ok=True)
    with open(take_mp3, "wb") as f:
        f.write(base64.b64decode(d.pop("audio_base64")))
    d.update(text=text, model_id=VOICE_MODEL, voice_id=VOICE_ID, voice_settings=VOICE_SETTINGS)
    with open(take_json, "w") as f:
        json.dump(d, f)


def decode(path: str) -> np.ndarray:
    """Any audio file to 48 kHz mono float."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def energy_db(x: np.ndarray, hop_s=0.01) -> np.ndarray:
    h = ma.ns(hop_s)
    e = np.sqrt(np.convolve(x ** 2, np.ones(h) / h, "same"))[::h]
    return 20 * np.log10(e + 1e-9)


def split_lines(x: np.ndarray, take: dict) -> list[np.ndarray]:
    """Cut the take into its lines. The alignment says roughly where each line
    ends; the cut goes in the middle of the longest pause near there, then each
    line is trimmed to its speech with a little air either side."""
    a = take["alignment"]
    chars, st, en = a["characters"], a["character_start_times_seconds"], a["character_end_times_seconds"]
    spans, pos = [], 0
    for line in "".join(chars).split("\n"):
        spans.append((st[pos], en[pos + len(line) - 1]))
        pos += len(line) + 1
    if len(spans) != len(LINES):
        sys.exit(f"the take has {len(spans)} lines, LINES has {len(LINES)}: make a new take")
    db = energy_db(x)
    quiet = db < db.max() - 38
    cuts = [0.0]
    for (_, end_i), (start_j, _) in zip(spans, spans[1:]):
        lo, hi = int((end_i - 0.6) * 100), int((start_j + 0.4) * 100)
        best, run_start, best_mid = 0, None, None
        for k in range(max(lo, 0), min(hi, len(quiet))):
            if quiet[k]:
                run_start = k if run_start is None else run_start
                if k - run_start + 1 > best:
                    best, best_mid = k - run_start + 1, (run_start + k) / 2 / 100
            else:
                run_start = None
        if best < 12:                                   # under 120 ms: no real pause
            sys.exit(f"no pause found between lines near {end_i:.2f}s: make a new take")
        cuts.append(best_mid)
    cuts.append(len(x) / SR)
    out = []
    for c0, c1 in zip(cuts, cuts[1:]):
        seg = x[ma.ns(c0):ma.ns(c1)]
        loud = np.flatnonzero(energy_db(seg) >= db.max() - 38)
        s = max(0.0, loud[0] / 100 - 0.03)
        e = min(len(seg) / SR, loud[-1] / 100 + 0.08)
        seg = seg[ma.ns(s):ma.ns(e)].copy()
        f_in, f_out = ma.ns(0.008), ma.ns(0.03)
        seg[:f_in] *= np.linspace(0, 1, f_in)
        seg[-f_out:] *= np.linspace(1, 0, f_out)
        out.append(seg)
    return out


def stretch(x: np.ndarray, tempo: float) -> np.ndarray:
    """Quicker by `tempo`, same pitch (Rubber Band, via ffmpeg)."""
    if tempo <= 1.0001:
        return x
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.wav")
        wavfile.write(src, SR, x.astype(np.float32))
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-af", f"rubberband=tempo={tempo:.4f}",
                              "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def place_voice(lines: list[np.ndarray], total_s: float) -> tuple[np.ndarray, list]:
    buf = np.zeros((2, ma.ns(total_s)))
    report = []
    for i, ((text, at), seg) in enumerate(zip(LINES, lines)):
        nxt = LINES[i + 1][1] if i + 1 < len(LINES) else total_s - 0.3
        budget = nxt - 0.08 - at
        dur = len(seg) / SR
        tempo = max(1.0, dur / budget)
        if tempo > MAX_STRETCH:
            sys.exit(f"line {i + 1} runs {dur:.2f}s for {budget:.2f}s: shorten it or move its start")
        seg = stretch(seg, tempo)
        seg = ma.hp(seg, 85)
        ma.add(buf, seg, at)
        report.append((at, at + len(seg) / SR, tempo, text))
    return buf, report


# ── phonk ────────────────────────────────────────────────────────────────────

def square(f: float, t: np.ndarray) -> np.ndarray:
    """Band-limited square: odd harmonics up to 14 kHz."""
    y = np.zeros(len(t))
    k = 1
    while k * f < 14000:
        y += np.sin(2 * np.pi * k * f * t) / k
        k += 2
    return y * 4 / np.pi


def cowbell(midi: float, length_s=0.30) -> np.ndarray:
    """The 808 cowbell, pitched: two squares a ratio of 1.48 apart (540/800 Hz
    on the original), band-passed, a fast knock then a short ring."""
    t = ma.tt(length_s)
    f = ma.hz(midi)
    x = square(f, t) + 0.8 * square(f * 1.4815, t)
    x = ma.bp(x, f * 0.85, min(f * 1.4815 * 4, 15000))
    return x * (0.7 * np.exp(-t / 0.035) + 0.3 * np.exp(-t / 0.16))


def bass808(midi: float, length_s: float, glide_to: float | None = None, glide_s=0.10) -> np.ndarray:
    """Distorted 808: a pitch punch at the start, a slide into the next note."""
    t = ma.tt(length_s)
    f0 = ma.hz(midi)
    f = f0 * (1 + 1.2 * np.exp(-t / 0.012))
    if glide_to is not None:
        g0 = max(0.0, length_s - glide_s)
        m = t > g0
        f[m] = f0 * (ma.hz(glide_to) / f0) ** ((t[m] - g0) / glide_s)
    x = np.tanh(3.0 * np.sin(2 * np.pi * np.cumsum(f) / SR)) / np.tanh(3.0)
    return ma.lp(x * ma.adsr(length_s, a=0.002, d=0.25, s=0.75, r=0.04), 2500)


# F minor, a bar each: i - i - VI - V (harmonic minor), the phonk move.
CHORDS = [(65, 68, 72, 77), (65, 68, 72, 75), (65, 68, 73, 77), (64, 67, 72, 76)]
ROOTS = [41, 41, 37, 36]                         # F2, F2, Db2, C2: speaks on a phone
RIFF_STEPS = (0, 3, 6, 8, 10, 11, 14)            # 16ths: x..x..x.x.xx..x.
RIFF_SHAPES = [(3, 2, 1, 2, 3, 0, 2), (2, 3, 2, 1, 0, 1, 3)]


def render_phonk(total_s: float) -> np.ndarray:
    n_bars = math.ceil(total_s / BAR) + 1
    drums = np.zeros((2, ma.ns(total_s + 2)))
    bass = np.zeros_like(drums)
    bell = np.zeros_like(drums)
    breakdown = int(round(15.0 / BAR))            # "Start free.": drums out, riser in
    for b in range(n_bars):
        t0 = b * BAR
        chord, root = CHORDS[b % 4], ROOTS[b % 4]
        shape = RIFF_SHAPES[b % 2]
        for step, idx in zip(RIFF_STEPS, shape):
            note = chord[idx]
            add_at = t0 + step * STEP
            ma.add(bell, cowbell(note), add_at, 0.55 if step % 4 == 0 else 0.42, pan=0.18)
            ma.add(bell, cowbell(note + 12, 0.18), add_at + 3 * STEP, 0.10, pan=-0.35)  # echo
        if b == breakdown:
            continue
        kicks = (0, 10) if b % 2 == 0 else (0, 10, 14)
        for k in kicks:
            ma.add(drums, ma.KICK, t0 + k * STEP, 0.75)
        ma.add(drums, ma.CLAP, t0 + 8 * STEP, 0.65)                    # halftime clap
        for s in range(0, 16, 2):
            ma.add(drums, ma.HAT_C, t0 + s * STEP, 0.55 if s % 4 == 0 else 0.4, pan=-0.2)
        if b % 2 == 1:                                                  # roll into the next bar
            for s in (12.5, 13, 13.5, 14.5, 15, 15.25, 15.5, 15.75):
                ma.add(drums, ma.HAT_C, t0 + s * STEP, 0.3, pan=-0.2)
        ma.add(drums, ma.HAT_O, t0 + 6 * STEP, 0.35, pan=0.25)
        nxt_root = ROOTS[(b + 1) % 4]
        starts = list(kicks) + [16]
        for k0, k1 in zip(starts, starts[1:]):
            length = (k1 - k0) * STEP
            glide = nxt_root if (k1 == 16 and nxt_root != root) else None
            ma.add(bass, bass808(root, length, glide), t0 + k0 * STEP, 0.8)
    # The breakdown: bell muffled, a riser into the end card's drop.
    b0, b1 = ma.ns(breakdown * BAR), ma.ns((breakdown + 1) * BAR)
    bell[:, b0:b1] = ma.lp(bell[:, b0:b1], 1400)
    r = ma.riser(BAR)
    ma.add(bell, r, breakdown * BAR, 0.35)
    mix = drums * 0.9 + bass * 0.6 + bell * 1.0       # 808 held back: it's a bed
    mix = mix + ma.verb(bell) * 0.18
    mix = ma.hp(mix, 30)
    return mix[:, :ma.ns(total_s)]


# ── sound effects ────────────────────────────────────────────────────────────

def render_sfx(total_s: float) -> np.ndarray:
    buf = np.zeros((2, ma.ns(total_s)))
    ma.add(buf, ma.pan2(ma.sfx_slam()), REVEAL_250, 0.55)
    for g in GLITCHES:
        ma.add(buf, ma.sfx_glitch(), g, 0.35)
    for c in CUTS:
        w, lead = ma.sfx_whoosh()
        ma.add(buf, w, c - lead, 0.40)
    ma.add(buf, ma.sfx_hit(), END_CARD, 0.45)          # its crash rings under the last line
    ma.add(buf, ma.pan2(ma.sfx_pop(), 0.1), URL_PILL, 0.35)
    return buf


# ── mix ──────────────────────────────────────────────────────────────────────

def lufs_of(x: np.ndarray) -> float:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.wav")
        ma.write(p, x / (np.abs(x).max() + 1e-9) * 0.5)
        level, _ = ma.loudness(p)
    return level + 20 * math.log10((np.abs(x).max() + 1e-9) / 0.5)


def to_lufs(x: np.ndarray, target: float) -> np.ndarray:
    return x * 10 ** ((target - lufs_of(x)) / 20)


def duck_under(music: np.ndarray, voice: np.ndarray, depth_db=5.0) -> np.ndarray:
    """Music dips while the voice speaks: attack 30 ms, release 300 ms."""
    level = np.sqrt(np.maximum(ma.signal.sosfilt(ma._sos("lowpass", 20), (voice ** 2).sum(axis=0)), 0.0))
    on = (level > level.max() * 0.06).astype(float)
    env = np.zeros_like(on)
    a, r = math.exp(-1 / (0.03 * SR)), math.exp(-1 / (0.30 * SR))
    prev = 0.0
    for i, v in enumerate(on):
        prev = (a * prev + (1 - a) * v) if v > prev else (r * prev + (1 - r) * v)
        env[i] = prev
    return music * 10 ** (-depth_db * env / 20)


VOICE_LUFS = -16.0
MUSIC_UNDER = 14.0          # LU the bed sits under the voice, before ducking
SFX_LUFS = -27.0


def master(mix: np.ndarray, total_s: float) -> np.ndarray:
    """-14 LUFS integrated, true peak at or under -1 dBTP; fade the last beat."""
    mix = ma.fade_last_beat(mix, total_s)
    lufs = lufs_of(mix)
    ceiling = -1.5
    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, "mix.wav")
        for _ in range(6):
            mix = ma.limit(mix * 10 ** ((-14.0 - lufs) / 20), ceiling_db=ceiling)
            ma.write(tmp, mix)
            lufs, tp = ma.loudness(tmp)
            if abs(lufs + 14.0) < 0.3 and tp <= -1.0:
                break
            if tp > -1.0:
                ceiling -= (tp + 1.0) + 0.15
    return mix


def duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=duration", "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reel", help="reel v1 (its picture is kept, its audio replaced)")
    ap.add_argument("--new-take", action="store_true", help="ask ElevenLabs for a new voice take")
    args = ap.parse_args()
    total_s = duration(args.reel)

    take_mp3, take_json = os.path.join(VO_DIR, "take.mp3"), os.path.join(VO_DIR, "take.json")
    if args.new_take or not os.path.exists(take_mp3):
        new_take(take_mp3, take_json)
    take = json.load(open(take_json))
    if take.get("text") != "\n".join(line for line, _ in LINES):
        sys.exit("LINES changed since the cached take: run with --new-take")
    voice, report = place_voice(split_lines(decode(take_mp3), take), total_s)
    for s, e, tempo, text in report:
        print(f"  voice {s:5.2f}-{e:5.2f}s  x{tempo:.3f}  {text}")

    voice = to_lufs(voice, VOICE_LUFS)
    music = to_lufs(render_phonk(total_s), VOICE_LUFS - MUSIC_UNDER)
    music = duck_under(music, voice)
    sfx = to_lufs(render_sfx(total_s), SFX_LUFS)

    stems = os.path.join(OUT, "reel-v2-stems")
    os.makedirs(stems, exist_ok=True)
    for name, x in (("voice", voice), ("music", music), ("sfx", sfx)):
        ma.write(os.path.join(stems, f"{name}.wav"), x)

    mix = master(voice + music + sfx, total_s)
    wav = os.path.join(stems, "mix.wav")
    ma.write(wav, mix)
    lufs, tp = ma.loudness(wav)

    out = os.path.join(OUT, "highlightz-reel-v2-stop-clipping-for-free.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.reel, "-i", wav,
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-tag:v", "hvc1",
                    "-c:a", "aac", "-b:a", "256k", "-ar", str(SR), "-t", f"{total_s:.4f}",
                    "-movflags", "+faststart", out], check=True)
    print(f"{os.path.relpath(out, PROMO)}: {total_s:.3f} s, {lufs:.1f} LUFS, true peak {tp:.1f} dBTP")


if __name__ == "__main__":
    main()
