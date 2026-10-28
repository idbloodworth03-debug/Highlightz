#!/usr/bin/env python3
"""Reel v9's soundtrack: a voice-over, a quiet phonk bed and transition hits,
laid under the owner's 20 s "Stop clipping for free" reel. The picture is
copied untouched; only the audio track is replaced.

VOICE. Kokoro-82M, an open text-to-speech model under Apache-2.0: free, runs
here on the CPU, and its output is ours to use commercially (the owner won't
pay for ElevenLabs, whose free plan forbids commercial use). Voice am_fenrir,
the owner's pick ("the guy"): of Kokoro's best-rated US male voices the
highest, liveliest pitch. Quality first: the full-precision (fp32) model, not
a quantized one, and every line at Kokoro's natural speed, never sped up or
stretched. The lines flow one after another: each starts just after its
scene's cut or a breath after the line before, whichever is later, so a long
line nudges the next a little later rather than being rushed. Kokoro says "Duxxion" as /ˈdʌksiən/ (DUCKS-ee-on, as the owner says
it) and "Highlightz" as "highlights" with no respelling.

The model and voices come from the npm registry, pinned by sha256, because
that is what this sandbox can reach (Hugging Face and GitHub releases are
blocked): onnx-community's fp32 model.onnx in 19 shards across three
packages (kokoro-fp32{a,b,c}-shards, the publisher of kokoro-q8-shards,
whose join matches its published hash), the voices from kokoro-js, Kokoro's
reference JS build. The fp32 join predicts the same timing as the q8 model on
every line of the script, to 0.03 s: the same network, unquantized.

MUSIC. Phonk at 128 BPM, synthesized from the promo's own drum and synth parts
(audio/make_audio.py): a pitched 808 cowbell riff, a distorted 808 that slides,
a halftime clap and trap hats. Original, so there is nothing to license. Kept
low and ducked under the voice: the owner wants the voice-over to lead.

TIMING. The reel cuts every 8 beats at 128 BPM (3.75 s), measured off its
frames; every cue below is in seconds on the reel's clock.

PICTURE. Two files, both from the reel's own frames:
  ...-v9.mp4            the picture stream copied bit for bit: no loss at all
  ...-v9-1080x1920.mp4  for uploading (Instagram and TikTok both ask for
                        1080x1920), x264 CRF 10 veryslow. With --upscaled, made
                        from reel/upscale_reel.py's Real-ESRGAN frames (2x,
                        1440x2560) brought down to 1080x1920: crisp text and
                        HUD edges, compression blocking cleaned. Without it, a
                        plain Lanczos upscale of the source: faithful but soft.
                        Either way the ceiling is the 720p source; a
                        higher-resolution export of the reel would beat both.

OUTPUT (out/):
  highlightz-reel-v9-stop-clipping-for-free.mp4
  highlightz-reel-v9-stop-clipping-for-free-1080x1920.mp4
  reel-v9-stems/{voice,music,sfx,mix}.wav   to rebalance in an editor

    pip install kokoro-onnx
    python3 reel/upscale_reel.py reel/reel-v1.mov          # ~40 min, once
    python3 reel/make_reel_audio.py reel/reel-v1.mov --upscaled reel/esrgan/x2
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROMO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROMO, "audio"))
import make_audio as ma  # noqa: E402  (the promo's synth parts and mastering)

SR = ma.SR
BEAT = 60.0 / 128
STEP = BEAT / 4
BAR = 4 * BEAT
OUT = os.path.join(PROMO, "out")
VERSION = "v9"
rng = np.random.default_rng(20261028)

VOICE = "am_fenrir"
SPEED = 1.0                 # Kokoro's natural read: the most natural it sounds
LEAD = 0.08                 # a line starts just after its cut: the whoosh lands first
GAP = 0.15                  # at least this much breath between lines
MAX_LAG = 0.9               # a line may trail its scene's cut by this much, no more

# Each line and the cut of the scene it belongs to. Written to be said, not
# read: whole sentences, joined the way people talk, no word said twice in a
# row ("prize", then "up for grabs"). The $250 is a prize, so every mention of
# it is something you win, never something you pay: "clip for two-fifty"
# sounded like a price.
LINES = [
    ("Stop clipping for free, come clip for a two-fifty dollar prize instead.", 0.0),
    ("There's five hundred up for grabs, and it all comes down to views.", 3.75),
    ("Join for free, clip Duxxion's stream, and post from Highlightz.", 7.50),
    ("Want an edge? Pro unlocks the Clip Editor and one-press posting.", 11.25),
    ("Start free, upgrade anytime.", 15.00),
    ("Clip to win two-fifty at highlightz dot app.", 16.875),
]

# What happens on screen (seconds), measured frame by frame off reel v1.
REVEAL_250 = 1.43           # "free" struck out, "$250" lands
GLITCHES = (3.52, 11.02)    # the RGB-split glitch into scenes 2 and 4
CUTS = (3.75, 7.50, 11.25, 15.00)
END_CARD = 16.875           # logo, "Clip for $250."
URL_PILL = 18.50            # highlightz.app lands


# ── voice ────────────────────────────────────────────────────────────────────

KOKORO_DIR = os.path.join(HERE, "kokoro")
NPM = "https://registry.npmjs.org"
MODEL_TARBALLS = [   # in shard order: a holds 0-6, b 7-13, c 14-18
    (f"{NPM}/kokoro-fp32a-shards/-/kokoro-fp32a-shards-1.0.0.tgz",
     "3865cf44462777e1dfc30aebf268bd57f09949b1d69204c7dd8b6d511cb3c18b"),
    (f"{NPM}/kokoro-fp32b-shards/-/kokoro-fp32b-shards-1.0.0.tgz",
     "8fe7242dc2d2135eec6497e996b5efbb1b33163ddd9682107dc6e473fb2505fc"),
    (f"{NPM}/kokoro-fp32c-shards/-/kokoro-fp32c-shards-1.0.0.tgz",
     "21091855eab91d5d9fbdbbc6fb8c69e08b1f42eb707891aae5d01cd2ce34c1db"),
]
MODEL_SHARDS = 19
MODEL_SHA256 = "8fbea51ea711f2af382e88c833d9e288c6dc82ce5e98421ea61c058ce21a34cb"   # joined
VOICES_TARBALL = (f"{NPM}/kokoro-js/-/kokoro-js-1.2.1.tgz",
                  "1f0d0383ef0349af0bd9440d81dc12abbea3a5392eb645283eda876f537f5860")


def fetch_kokoro() -> tuple[str, str]:
    """The model and the voices, downloaded once into reel/kokoro/."""
    model = os.path.join(KOKORO_DIR, "kokoro-fp32.onnx")
    voices = os.path.join(KOKORO_DIR, "voices.npz")
    if os.path.exists(model) and os.path.exists(voices):
        return model, voices
    os.makedirs(KOKORO_DIR, exist_ok=True)

    def tarball(url, sha):
        with urllib.request.urlopen(url, timeout=600) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != sha:
            sys.exit(f"{url} does not match its pinned sha256")
        return tarfile.open(fileobj=io.BytesIO(data))

    shards = {}
    for url, sha in MODEL_TARBALLS:
        t = tarball(url, sha)
        for m in t.getmembers():
            if m.name.endswith(".bin"):
                shards[int(m.name.rsplit("part", 1)[1][:-4])] = t.extractfile(m).read()
    joined = b"".join(shards[i] for i in range(MODEL_SHARDS))
    if hashlib.sha256(joined).hexdigest() != MODEL_SHA256:
        sys.exit("the joined Kokoro model does not match its sha256")
    with open(model, "wb") as f:
        f.write(joined)
    t = tarball(*VOICES_TARBALL)
    arrays = {}
    for m in t.getmembers():
        if m.name.startswith("package/voices/") and m.name.endswith(".bin"):
            raw = t.extractfile(m).read()
            arrays[os.path.basename(m.name)[:-4]] = np.frombuffer(raw, np.float32).reshape(-1, 1, 256)
    np.savez(voices, **arrays)
    return model, voices


def energy_db(x: np.ndarray, hop_s=0.01) -> np.ndarray:
    h = ma.ns(hop_s)
    e = np.sqrt(np.convolve(x ** 2, np.ones(h) / h, "same"))[::h]
    return 20 * np.log10(e + 1e-9)


def trim(x: np.ndarray) -> np.ndarray:
    """The line's speech with a little air either side, faded so it can't click."""
    db = energy_db(x)
    loud = np.flatnonzero(db >= db.max() - 38)
    s = max(0.0, loud[0] / 100 - 0.03)
    e = min(len(x) / SR, loud[-1] / 100 + 0.08)
    x = x[ma.ns(s):ma.ns(e)].copy()
    f_in, f_out = ma.ns(0.008), ma.ns(0.03)
    x[:f_in] *= np.linspace(0, 1, f_in)
    x[-f_out:] *= np.linspace(1, 0, f_out)
    return x


def speak(model: str, voices: str, total_s: float) -> tuple[np.ndarray, list]:
    """Say each line at natural speed and let them flow: a line starts just
    after its scene's cut, or a breath after the line before if that runs
    long. Stops the build if a line would trail its scene too far or run into
    the closing fade: shorten a line rather than rush it."""
    from kokoro_onnx import Kokoro
    k = Kokoro(model, voices)
    buf = np.zeros((2, ma.ns(total_s)))
    report = []
    prev_end = -GAP                     # nothing before the first line to breathe after
    for i, (text, cut) in enumerate(LINES):
        a, sr = k.create(text, voice=VOICE, speed=SPEED, lang="en-us")
        g = math.gcd(SR, sr)
        seg = trim(ma.signal.resample_poly(np.asarray(a, np.float64), SR // g, sr // g))
        at = max(cut + LEAD, prev_end + GAP)
        end = at + len(seg) / SR
        if at - cut > MAX_LAG:
            sys.exit(f"line {i + 1} would start {at - cut:.2f}s after its cut: shorten the lines before it")
        if end > total_s - BEAT:
            sys.exit(f"the voice would run into the closing fade ({end:.2f}s): shorten a line")
        ma.add(buf, ma.hp(seg, 85), at)
        report.append((at, end, at - cut, text))
        prev_end = end
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


def probe(path: str) -> tuple[float, int, str]:
    """Duration, frame count and frame rate of the reel's picture."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=duration,nb_frames,r_frame_rate", "-of", "default=nw=1", path],
                       capture_output=True, text=True, check=True)
    f = dict(line.split("=", 1) for line in r.stdout.split())
    return float(f["duration"]), int(f["nb_frames"]), f["r_frame_rate"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reel", help="reel v1 (its picture is kept, its audio replaced)")
    ap.add_argument("--upscaled", help="reel/upscale_reel.py's frames, for the upload copy")
    args = ap.parse_args()
    total_s, n_frames, rate = probe(args.reel)
    if args.upscaled:
        have = len([f for f in os.listdir(args.upscaled) if f.endswith(".png") and not f.startswith(".")])
        if have != n_frames:
            sys.exit(f"{args.upscaled} has {have} of the reel's {n_frames} frames: finish upscale_reel.py")

    voice, report = speak(*fetch_kokoro(), total_s)
    for s, e, lag, text in report:
        print(f"  voice {s:5.2f}-{e:5.2f}s  +{lag:.2f}s after its cut  {text}")

    voice = to_lufs(voice, VOICE_LUFS)
    music = to_lufs(render_phonk(total_s), VOICE_LUFS - MUSIC_UNDER)
    music = duck_under(music, voice)
    sfx = duck_under(to_lufs(render_sfx(total_s), SFX_LUFS), voice, depth_db=6.0)   # words over whooshes

    stems = os.path.join(OUT, f"reel-{VERSION}-stems")
    os.makedirs(stems, exist_ok=True)
    for name, x in (("voice", voice), ("music", music), ("sfx", sfx)):
        ma.write(os.path.join(stems, f"{name}.wav"), x)

    mix = master(voice + music + sfx, total_s)
    wav = os.path.join(stems, "mix.wav")
    ma.write(wav, mix)
    lufs, tp = ma.loudness(wav)

    audio = ["-c:a", "aac", "-b:a", "320k", "-ar", str(SR), "-t", f"{total_s:.4f}", "-movflags", "+faststart"]
    base = os.path.join(OUT, f"highlightz-reel-{VERSION}-stop-clipping-for-free")
    # The picture exactly as it came: no re-encode, so no loss.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.reel, "-i", wav,
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-tag:v", "hvc1",
                    *audio, base + ".mp4"], check=True)
    # The upload copy: 1080x1920, near-lossless H.264, BT.709 SDR like the source.
    if args.upscaled:   # RGB frames: convert back to BT.709 TV range explicitly
        picture = ["-framerate", rate, "-start_number", "0", "-i", os.path.join(args.upscaled, "%04d.png")]
        vf = ("scale=1080:1920:flags=lanczos+accurate_rnd+full_chroma_int"
              ":out_color_matrix=bt709:out_range=tv,format=yuv420p")
    else:
        picture = ["-i", args.reel]
        vf = "scale=1080:1920:flags=lanczos+accurate_rnd+full_chroma_int"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *picture, "-i", wav,
                    "-map", "0:v:0", "-map", "1:a:0", "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryslow", "-crf", "10", "-profile:v", "high",
                    "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-color_range", "tv",
                    *audio, base + "-1080x1920.mp4"], check=True)
    print(f"{os.path.relpath(base, PROMO)}[-1080x1920].mp4: {total_s:.3f} s, "
          f"{lufs:.1f} LUFS, true peak {tp:.1f} dBTP")


if __name__ == "__main__":
    main()
