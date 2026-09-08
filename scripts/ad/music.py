"""Music bed + sound design for the 30 s Highlightz ad, synthesised with numpy.

Timeline (seconds) follows ad.html's scenes:
  0.0- 3.6  night: sub drone, sparse soft pluck, a slow clock tick
  3.6- 8.0  the moment: riser hit, drums in, bass + arp at full tilt, chat crackle
  8.0-11.4  the loss: everything cut to a dark pad, filtered; clock ticks; silence
 11.4-14.6  morning: warm pad swell, notification ding at 12.0
 14.6-22.0  product: beat returns brighter, lifted progression, whooshes on each beat
 22.0-26.4  payoff: full energy, crowd-like noise swell, hearts (light chime cluster)
 26.4-30.0  lockup: one held chord, tail
"""
import numpy as np, wave, sys

SR = 44100
DUR = 30.0
N = int(SR * DUR)
t = np.arange(N) / SR
mix = np.zeros(N)

def env(n, a, d, s, r, hold):
    """ADSR over n samples; hold = sustain length in samples."""
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    e = np.zeros(n)
    i = 0
    e[i:i + a] = np.linspace(0, 1, a)[: max(0, min(a, n - i))]; i += a
    e[i:i + d] = np.linspace(1, s, d)[: max(0, min(d, n - i))]; i += d
    e[i:i + hold] = s; i += hold
    e[i:i + r] = np.linspace(s, 0, r)[: max(0, min(r, n - i))]
    return e[:n]

def add(sig, at, gain=1.0):
    i = int(at * SR); j = min(N, i + len(sig))
    if j > i: mix[i:j] += sig[: j - i] * gain

def note(freq, dur, kind='saw', a=.005, d=.08, s=.6, r=.15, cutoff=None):
    n = int(dur * SR); tt = np.arange(n) / SR
    if kind == 'saw': w = 2 * (tt * freq % 1) - 1
    elif kind == 'sq': w = np.sign(np.sin(2 * np.pi * freq * tt))
    elif kind == 'tri': w = 2 * np.abs(2 * (tt * freq % 1) - 1) - 1
    else: w = np.sin(2 * np.pi * freq * tt)
    if kind in ('saw', 'sq'):   # detuned pair for width
        w = 0.6 * w + 0.4 * (2 * (tt * freq * 1.004 % 1) - 1)
    e = env(n, a, d, s, r, max(0, n - int((a + d + r) * SR)))
    out = w * e
    if cutoff:   # one-pole lowpass with envelope-following cutoff
        y = np.zeros(n); alpha = np.clip(cutoff * e * 2 * np.pi / SR, 0.002, 0.9); prev = 0.0
        for k in range(n):
            prev = prev + alpha[k] * (out[k] - prev); y[k] = prev
        out = y
    return out

def kick(dur=.35):
    n = int(dur * SR); tt = np.arange(n) / SR
    f = 150 * np.exp(-tt * 28) + 45
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 9)

def snare(dur=.22):
    n = int(dur * SR); tt = np.arange(n) / SR
    rng = np.random.default_rng(7)
    return (rng.standard_normal(n) * np.exp(-tt * 22) * .7 + np.sin(2 * np.pi * 190 * tt) * np.exp(-tt * 30) * .5)

def hat(dur=.06, open_=False):
    n = int((0.25 if open_ else dur) * SR); tt = np.arange(n) / SR
    rng = np.random.default_rng(11)
    x = rng.standard_normal(n)
    x = x - np.concatenate([[0], x[:-1]])   # crude highpass
    return x * np.exp(-tt * (12 if open_ else 60)) * .35

def whoosh(dur=.6, up=True):
    n = int(dur * SR); tt = np.arange(n) / SR
    rng = np.random.default_rng(3)
    x = rng.standard_normal(n)
    y = np.zeros(n); prev = 0.0
    sweep = np.linspace(200, 6000, n) if up else np.linspace(6000, 200, n)
    alpha = np.clip(sweep * 2 * np.pi / SR, .01, .8)
    for k in range(n):
        prev = prev + alpha[k] * (x[k] - prev); y[k] = prev
    return y * np.sin(np.pi * tt / dur) * .5

def ding(freq=1568, dur=1.4):
    n = int(dur * SR); tt = np.arange(n) / SR
    return (np.sin(2 * np.pi * freq * tt) + .35 * np.sin(2 * np.pi * freq * 2.01 * tt)) * np.exp(-tt * 3.2) * .5

def tick():
    n = int(.03 * SR); tt = np.arange(n) / SR
    return np.sin(2 * np.pi * 2400 * tt) * np.exp(-tt * 180) * .35

def riser(dur=2.0):
    n = int(dur * SR); tt = np.arange(n) / SR
    rng = np.random.default_rng(5)
    x = rng.standard_normal(n)
    y = np.zeros(n); prev = 0.0
    alpha = np.clip(np.linspace(150, 5000, n) * 2 * np.pi / SR, .005, .8)
    for k in range(n):
        prev = prev + alpha[k] * (x[k] - prev); y[k] = prev
    return y * (tt / dur) ** 2 * .8

A = 55.0
def hz(semi, base=A): return base * 2 ** (semi / 12)
# A minor: Am F C G  (roots relative to A)
PROG = [0, -4, 3, -2]

# ── 0-3.6 night ──
add(note(hz(0), 3.8, 'saw', a=.6, d=.5, s=.8, r=.6, cutoff=180), 0.0, .35)
for k, at in enumerate([0.9, 1.7, 2.5]):
    add(note(hz(12 + [0, 3, 7][k]), .8, 'tri', a=.01, d=.3, s=.2, r=.4), at, .18)
for at in np.arange(0.0, 3.6, 0.5): add(tick(), at, .5)
add(riser(2.0), 1.6, .9)

# ── 3.6-8.0 the moment ──
BPM = 128; beat = 60 / BPM
add(whoosh(.3, up=False), 3.55, .9)
add(kick(.5), 3.6, 1.2)           # the hit
add(note(hz(0), .6, 'saw', a=.002, d=.2, s=.5, r=.2, cutoff=900), 3.6, .5)
for b in range(int((8.0 - 3.6) / beat) + 1):
    at = 3.6 + b * beat
    if at >= 8.0: break
    add(kick(), at, .9)
    if b % 2 == 1: add(snare(), at, .55)
    for h in range(2): add(hat(), at + h * beat / 2, .5 if h == 0 else .3)
    root = PROG[(b // 2) % 4]
    add(note(hz(root), beat * .95, 'saw', a=.003, d=.05, s=.7, r=.05, cutoff=420), at, .45)
    for i in range(4):   # arp, sixteenth notes
        semi = root + [12, 19, 24, 19][i]
        add(note(hz(semi, A * 4), beat / 4, 'sq', a=.002, d=.05, s=.3, r=.04, cutoff=3000), at + i * beat / 4, .12)
# chat crackle: rising density of tiny clicks
rng = np.random.default_rng(9)
for at in sorted(rng.uniform(3.8, 8.0, 90)):
    add(tick(), at, .12 + .25 * (at - 3.8) / 4.2)

# ── 8.0-11.4 the loss ──
add(whoosh(.5, up=False), 7.9, .7)
add(note(hz(0), 3.6, 'saw', a=.4, d=.8, s=.6, r=.8, cutoff=140), 8.0, .3)
add(note(hz(-4, A / 2), 3.6, 'sine', a=.6, d=.8, s=.7, r=.8), 8.0, .35)
for at in np.arange(8.4, 11.2, 0.5): add(tick(), at, .5)

# ── 11.4-14.6 morning ──
for semi, g in [(0, .18), (7, .14), (12, .16), (16, .12)]:
    add(note(hz(semi, A * 2), 3.4, 'saw', a=.8, d=.6, s=.85, r=.8, cutoff=700), 11.4, g)
add(ding(), 12.05, .9)
add(ding(1976, 1.2), 12.18, .5)
add(whoosh(.5, up=True), 14.1, .6)

# ── 14.6-26.4 product + payoff ──
add(kick(.5), 14.6, 1.0)
for b in range(int((26.4 - 14.6) / beat) + 1):
    at = 14.6 + b * beat
    if at >= 26.4: break
    bright = at >= 22.0
    add(kick(), at, .95)
    if b % 2 == 1: add(snare(), at, .55)
    for h in range(2): add(hat(open_=(bright and h == 1)), at + h * beat / 2, .45 if h == 0 else .28)
    root = [0, 3, -2, 5][(b // 2) % 4]            # Am C G D: lifted
    add(note(hz(root), beat * .95, 'saw', a=.003, d=.05, s=.7, r=.05, cutoff=520 if not bright else 800), at, .45)
    for i in range(4):
        semi = root + [12, 16, 19, 24][i]
        add(note(hz(semi, A * 4), beat / 4, 'sq', a=.002, d=.05, s=.3, r=.04, cutoff=3400 if bright else 2600), at + i * beat / 4, .11)
    if b % 8 == 0:   # pad on each chord change
        for semi, g in [(root, .1), (root + 7, .08), (root + 12, .08)]:
            add(note(hz(semi, A * 2), beat * 8, 'saw', a=.3, d=.4, s=.8, r=.5, cutoff=900), at, g)
for at in [17.0, 19.4, 21.8]: add(whoosh(.45, up=True), at - .4, .5)
add(whoosh(.8, up=True), 21.3, .8)
add(kick(.6), 22.0, 1.2)
# hearts: a little chime cluster
for i, at in enumerate(np.arange(23.0, 25.6, .13)):
    add(ding([1568, 1976, 2349, 2637][i % 4], .5), at, .12)
# crowd-ish swell
rng2 = np.random.default_rng(21)
x = rng2.standard_normal(int(4.4 * SR)); y = np.zeros_like(x); prev = 0.0; alpha = 900 * 2 * np.pi / SR
for k in range(len(x)):
    prev = prev + alpha * (x[k] - prev); y[k] = prev
tt = np.arange(len(x)) / SR
add(y * np.sin(np.pi * tt / 4.4) ** 2 * .12, 22.0, 1.0)

# ── 26.4-30 lockup ──
add(whoosh(.6, up=False), 26.2, .7)
add(kick(.6), 26.4, .9)
for semi, g in [(0, .22), (7, .16), (12, .18), (16, .12), (19, .1)]:
    add(note(hz(semi, A * 2), 3.6, 'saw', a=.05, d=.6, s=.8, r=1.2, cutoff=1100), 26.4, g)
add(note(hz(0), 3.6, 'sine', a=.05, d=.6, s=.8, r=1.2), 26.4, .4)
add(ding(2093, 2.0), 28.2, .45)

# ── master: gentle compression, fade out, normalise ──
mix = np.tanh(mix * 1.15)
fade = np.ones(N); fo = int(1.2 * SR); fade[-fo:] = np.linspace(1, 0, fo)
mix *= fade
mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.92
out = (mix * 32767).astype(np.int16)
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'music.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(out.tobytes())
print('wrote', N, 'samples')
