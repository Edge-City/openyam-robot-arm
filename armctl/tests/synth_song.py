"""Synthetic drum/bass loops with known beats, bars and an energy change, for testing armctl.music.

    .venv/bin/python armctl/tests/synth_song.py out.wav --bpm 120 --bars 24 [--lead 0.7] [--mp3]

Bars 0..change_bar-1 are a quiet groove (kick on 1 and 3, closed hats); from change_bar on it
is loud (kick on every beat, snare on 2 and 4, open hats, bass line, a crash on every
downbeat). Bar 1 of every bar has an accented kick and a new bass note, so the bar phase is
detectable. Returns the true beat times.
"""
from __future__ import annotations

import argparse
import subprocess
import wave
from pathlib import Path

import numpy as np


def synth_song(bpm: float, bars: int, change_bar: int | None = None, lead: float = 0.5,
               sr: int = 22050, seed: int = 3) -> tuple[np.ndarray, np.ndarray, int]:
    """-> (signal, true beat times, sample rate)."""
    change_bar = bars // 2 if change_bar is None else change_bar
    beat = 60.0 / bpm
    n_beats = bars * 4
    total = int((lead + n_beats * beat + 1.5) * sr)
    y = np.zeros(total)
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.4 * sr)) / sr
    kick = np.sin(2 * np.pi * (45 + 100 * np.exp(-t * 35)) * t) * np.exp(-t * 10)
    snare = (rng.standard_normal(t.size) * 0.7 + np.sin(2 * np.pi * 185 * t) * 0.4) * np.exp(-t * 20)
    hat = rng.standard_normal(int(0.04 * sr)) * np.exp(-np.arange(int(0.04 * sr)) / sr * 110)
    ct = np.arange(int(1.0 * sr)) / sr
    crash = rng.standard_normal(ct.size) * np.exp(-ct * 4) * 0.25
    notes = [55.0, 73.42, 65.41, 49.0]

    def release(sig):  # 30 ms fade so a note's end isn't a click (a click is an onset)
        r = min(sig.size, int(0.03 * sr))
        sig[-r:] *= np.linspace(1, 0, r)
        return sig

    def add(sig, at, g):
        i = int((lead + at) * sr)
        j = min(total, i + sig.size)
        if j > i:
            y[i:j] += g * sig[: j - i]

    for n in range(n_beats):
        at = n * beat
        bar, pos = divmod(n, 4)
        loud = bar >= change_bar
        if loud:
            add(kick, at, 1.0 if pos == 0 else 0.7)
            if pos in (1, 3):
                add(snare, at, 0.6)
            add(hat, at + beat / 2, 0.35)
            add(hat, at, 0.2)
            if pos == 0:
                add(crash, at, 1.0)
            bt = np.arange(int(beat * 0.95 * sr)) / sr
            f = notes[bar % 4]
            add(release(np.sin(2 * np.pi * f * bt) * np.exp(-bt * 2) * 0.45), at, 1.0)
        else:
            if pos in (0, 2):
                add(kick, at, 0.35 if pos == 0 else 0.22)
            add(hat, at, 0.06)
            if pos == 0:
                bt = np.arange(int(beat * 3.8 * sr)) / sr
                f = notes[bar % 4] * 4
                add(release(np.sin(2 * np.pi * f * bt) * np.exp(-bt * 0.8) * 0.06), at, 1.0)
    y /= np.abs(y).max() / 0.85
    beats = lead + np.arange(n_beats) * beat
    return y.astype(np.float32), beats, sr


def write_wav(path: Path, y: np.ndarray, sr: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--bpm", type=float, default=120)
    ap.add_argument("--bars", type=int, default=24)
    ap.add_argument("--change-bar", type=int, default=None)
    ap.add_argument("--lead", type=float, default=0.5)
    ap.add_argument("--mp3", action="store_true", help="also encode <out>.mp3 with ffmpeg")
    a = ap.parse_args()
    y, beats, sr = synth_song(a.bpm, a.bars, a.change_bar, a.lead)
    out = Path(a.out)
    write_wav(out, y, sr)
    print(f"wrote {out}: {len(y) / sr:.1f} s, first beat {beats[0]:.3f} s")
    if a.mp3:
        mp3 = out.with_suffix(".mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-b:a", "192k", str(mp3)], check=True)
        print(f"wrote {mp3}")


if __name__ == "__main__":
    main()
