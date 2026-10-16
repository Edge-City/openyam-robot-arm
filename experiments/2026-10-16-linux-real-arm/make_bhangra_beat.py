"""Synthesize a short bhangra track for the arm to dance to.

    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/make_bhangra_beat.py

Writes music/inbox/bhangra-112.wav (ours, so it can be committed as
experiments/2026-10-16-linux-real-arm/media/audio/bhangra-112.wav too).

112 BPM, 4/4, swung eighths (triplet feel, the dhol "chaal"), 16 bars:
  bars 1-4   dhol alone (dagga = bass head, tilli = treble stick)
  bars 5-8   + tumbi riff (plucked, Karplus-Strong)
  bars 9-12  + chimta jingles on 2 and 4, fuller dhol
  bars 13-15 dhol-roll build
  bar 16     "hoi" accent hit on beat 1, then the last dhol hit on beat 3
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
BPM = 112
BEAT = 60.0 / BPM
SWING = 2.0 / 3.0  # the off-beat eighth lands two-thirds through the beat (triplet swing)
BARS = 16
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
rng = np.random.default_rng(16)


def env(n: int, decay: float) -> np.ndarray:
    return np.exp(-np.arange(n) / SR * decay)


def dagga(gain: float = 1.0) -> np.ndarray:
    """Bass head: a pitch-dropping thump with a little skin noise."""
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 70 + 80 * np.exp(-t * 25)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 7)
    skin = rng.standard_normal(n) * env(n, 60) * 0.15
    return gain * (body + skin)


def tilli(gain: float = 1.0) -> np.ndarray:
    """Treble head hit with the thin stick: bright, short, ringing."""
    n = int(0.18 * SR)
    t = np.arange(n) / SR
    ring = (np.sin(2 * np.pi * 410 * t) + 0.5 * np.sin(2 * np.pi * 820 * t)) * env(n, 28)
    click = rng.standard_normal(n) * env(n, 180) * 0.6
    return gain * 0.55 * (ring + click)


def chimta(gain: float = 1.0) -> np.ndarray:
    """Tongs with jingles: a cluster of bright metallic partials."""
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (3100, 4300, 5200, 6800))
    return gain * 0.12 * s * env(n, 22) * (1 + 0.5 * np.sin(2 * np.pi * 38 * t))


def tumbi(freq: float, dur: float, gain: float = 1.0) -> np.ndarray:
    """Karplus-Strong pluck, very bright: the one-string tumbi."""
    n = int(dur * SR)
    p = max(2, int(SR / freq))
    buf = rng.uniform(-1, 1, p)
    out = np.empty(n)
    for i in range(n):
        out[i] = buf[i % p]
        buf[i % p] = 0.996 * 0.5 * (buf[i % p] + buf[(i + 1) % p])
    return gain * 0.35 * out


def hoi(gain: float = 1.0) -> np.ndarray:
    """A shouted 'hoi!'-like accent: a vowel formant sweep on a buzzy source."""
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    f0 = 220 + 90 * np.minimum(t / 0.12, 1.0)
    src = np.sign(np.sin(2 * np.pi * np.cumsum(f0) / SR)) * 0.5
    form = (np.sin(2 * np.pi * (500 + 1300 * np.minimum(t / 0.25, 1.0)) * t))
    a = np.minimum(t / 0.02, 1.0) * env(n, 6)
    return gain * 0.35 * src * (0.6 + 0.4 * form) * a


def main() -> None:
    total = int((BARS * 4 * BEAT + 2.0) * SR)
    y = np.zeros(total)

    def add(sig: np.ndarray, at: float) -> None:
        i = int(at * SR)
        j = min(total, i + sig.size)
        if i < total:
            y[i:j] += sig[: j - i]

    off = lambda beat: beat * BEAT + SWING * BEAT  # noqa: E731  (swung "and")
    on = lambda beat: beat * BEAT  # noqa: E731
    riff = [659.3, 587.3, 659.3, 784.0, 659.3, 587.3, 523.3, 587.3]  # E5 D5 E5 G5 E5 D5 C5 D5
    for bar in range(BARS):
        b0 = bar * 4
        if bar == BARS - 1:  # finale
            add(dagga(1.3), on(b0)); add(hoi(1.0), on(b0)); add(tilli(1.0), on(b0))
            add(dagga(1.2), on(b0 + 2)); add(tilli(0.9), on(b0 + 2))
            continue
        # chaal: DAGGA on 1 and 3, tilli on the swung "and"s, light dagga on swung "and" of 2
        for k in range(4):
            add(dagga(1.0 if k in (0, 2) else 0.7), on(b0 + k))  # a bass pulse on every beat anchors the grid
            add(tilli(0.55 if k % 2 else 0.4), off(b0 + k))
        add(dagga(0.55), off(b0 + 1))
        add(tilli(0.4), on(b0 + 1)); add(tilli(0.4), on(b0 + 3))
        if 4 <= bar:  # tumbi riff, one note per beat on bars 5-12, swung pickups
            if bar < 12:
                for k in range(4):
                    add(tumbi(riff[(bar * 4 + k) % 8], 0.5, 0.9), on(b0 + k))
                    if k % 2 == 1:
                        add(tumbi(riff[(bar * 4 + k + 1) % 8], 0.25, 0.5), off(b0 + k))
        if 8 <= bar < 12:  # chimta on 2 and 4 (+ a swung flick)
            for k in (1, 3):
                add(chimta(1.0), on(b0 + k))
            add(chimta(0.5), off(b0 + 3))
        if 12 <= bar < 15:  # roll build: tilli on every swung eighth, louder each bar
            g = 0.5 + 0.2 * (bar - 12)
            for k in range(4):
                add(tilli(g), on(b0 + k)); add(tilli(g * 0.8), off(b0 + k))
        if bar in (4, 8, 12):
            add(hoi(0.7), on(b0))
    y /= max(1e-9, np.abs(y).max()) / 0.85
    for out in (REPO / "music/inbox/bhangra-112.wav", HERE / "media/audio/bhangra-112.wav"):
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((y * 32767).astype("<i2").tobytes())
        print("wrote", out)


if __name__ == "__main__":
    main()
