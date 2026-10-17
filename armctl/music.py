"""armctl.music: beat / tempo / bar / section analysis of a song, for beat-synced dances.

Nothing here moves the arm. ``analyze(path)`` returns a JSON-able dict that a human (or
Claude, the "intelligence layer") can read before writing a choreography plan for
``armctl.choreo``::

    .venv/bin/python -m armctl.music analyze music/inbox/song.mp3 [--json out.json]
    .venv/bin/python -m armctl.music excerpt music/inbox/song.mp3 [--max 55]

Analysis keys (times in seconds from the start of the file):

* ``tempo_bpm``, ``beat_period``: from a straight-line fit through the tracked beats
  (more precise than the tempogram peak); ``tempo_stability`` = std/mean of the
  beat intervals (0.00 = metronome, > 0.05 = drifting or a tempo change).
* ``beats``: tracked beat times (librosa), shifted as a whole onto the actual attacks
  (the onset-strength peak trails the attack by ~20 ms); beats in silence are dropped.
* ``downbeats``: every 4th beat (4/4 assumed). The bar phase is the one whose beats
  carry the most low-band (kick) onset energy plus harmonic (chroma) change;
  ``downbeat_confidence`` is the margin over the runner-up phase (z units, > 0.5 is solid).
* ``beat_energy``: RMS between consecutive beats, divided by the song's 95th percentile
  and clipped to 0..1 (one value per beat; the last beat repeats the previous value).
* ``sections``: bar-aligned [{start, end, start_beat, end_beat, label, level, energy}],
  boundaries at peaks of a bar-level novelty curve (MFCC + chroma + loudness). Labels
  are heuristics from energy only: intro / verse / chorus / drop / break / outro.
* ``key_moments``: the biggest bar-to-bar energy jumps [{t, beat, delta, section}].
* ``onsets``: note/hit onsets. ``waveform``: peak envelope for drawing.
* ``excerpt``: the suggested <= 55 s window (starts on a downbeat, whole bars,
  highest mean energy). The choreographer uses it unless a plan overrides it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
MUSIC_DIR = REPO / "music"
INBOX = MUSIC_DIR / "inbox"
ANALYSIS_DIR = MUSIC_DIR / "analysis"
CLIPS_DIR = MUSIC_DIR / "clips"
PLANS_DIR = MUSIC_DIR / "plans"
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus", ".aif", ".aiff", ".webm"}

SR = 22050
HOP = 256            # 11.6 ms frames
MAX_EXCERPT_S = 55.0
FIRST_BEAT_PRIOR = 0.25  # z units, see _downbeat_phase
ANALYSIS_VERSION = 1


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", Path(text).stem.lower()).strip("-")
    return (s or "song")[:48]


def load_audio(path: str | Path, sr: int = SR) -> np.ndarray:
    """Mono float32 at ``sr``. libsndfile reads wav/flac/ogg/mp3; anything else goes through ffmpeg."""
    import librosa
    try:
        y, _ = librosa.load(str(path), sr=sr, mono=True)
        return y
    except Exception:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                            "-ar", str(sr), "-"], capture_output=True, check=False)
        if r.returncode != 0 or not r.stdout:
            raise ValueError(f"cannot decode {path}: {r.stderr.decode(errors='replace')[-300:]}")
        return np.frombuffer(r.stdout, dtype="<f4").copy()


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _z(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    s = x.std()
    return (x - x.mean()) / s if s > 1e-12 else np.zeros_like(x)


# ------------------------------------------------------------------ beats and tempo
def _track_beats(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """-> (beat times, onset envelope, frame times)."""
    import librosa
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, aggregate=np.median)
    ft = librosa.frames_to_time(np.arange(len(oenv)), sr=sr, hop_length=HOP)
    _, bf = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=HOP, tightness=100,
                                    trim=False, units="frames")
    bt = ft[np.asarray(bf, dtype=int)]
    # drop beats in silence (lead-in / tail): local RMS 45 dB under the loudest part
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    loud = 20 * np.log10(rms + 1e-9) > 20 * np.log10(rms.max() + 1e-9) - 45
    idx = np.clip(np.asarray(bf, dtype=int), 0, len(loud) - 1)
    near = np.array([loud[max(0, i - 4): i + 5].any() for i in idx], bool) if len(idx) else np.zeros(0, bool)
    # ... and beats with almost no onset at the very start/end (reverb tails, fades)
    strong = near & (oenv[idx] > 0.15 * np.median(oenv[idx])) if len(idx) else near
    if strong.any():
        a, b = np.flatnonzero(strong)[[0, -1]]
        bt = bt[a:b + 1]
    return np.unique(_refine_to_attacks(y, sr, bt)), oenv, ft


def _refine_to_attacks(y: np.ndarray, sr: int, beats: np.ndarray) -> np.ndarray:
    """Shift the beat grid onto the actual attacks.

    The onset-strength peak sits ~20 ms after the attack (spectral-flux frames are 93 ms
    wide). For each beat, find the steepest rise of a 3 ms log-energy envelope in
    [-70, +40] ms; the median offset over beats with a clear attack (> 6 dB rise) is applied
    to all beats, which keeps the tracker's grid but removes its latency.
    """
    if len(beats) < 4:
        return beats
    h = 64
    n = len(y) // h
    env = np.sqrt(np.mean(y[: n * h].reshape(n, h).astype(float) ** 2, axis=1))
    led = 20 * np.log10(env + 1e-7)
    k = 3  # rise over ~9 ms
    rise = np.zeros_like(led)
    rise[k:] = led[k:] - led[:-k]
    offs = []
    for t in beats:
        c = int(t * sr / h)
        a, b = max(k, c - int(0.07 * sr / h)), min(n, c + int(0.04 * sr / h))
        if b <= a:
            continue
        i = a + int(np.argmax(rise[a:b]))
        if rise[i] > 6.0:
            # attack time = start of the rise window
            offs.append((i - k + 1) * h / sr - t)
    if len(offs) < max(4, len(beats) // 5):
        return beats
    return beats + float(np.median(offs))


def _fit_tempo(beats: np.ndarray) -> tuple[float, float, float]:
    """Straight-line fit of beat times vs index with outlier rejection -> (bpm, period, stability)."""
    if len(beats) < 4:
        return 0.0, 0.0, 1.0
    ioi = np.diff(beats)
    med = float(np.median(ioi))
    # index each beat by cumulative rounded intervals so a missed beat doesn't bend the fit
    idx = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(ioi / med)))])
    keep = np.ones(len(beats), bool)
    for _ in range(3):
        p = np.polyfit(idx[keep], beats[keep], 1)
        res = beats - np.polyval(p, idx)
        keep = np.abs(res) < max(0.03, 3 * np.std(res[keep]))
    period = float(p[0])
    stab = float(np.std(ioi) / np.mean(ioi))
    return 60.0 / period, period, stab


# ------------------------------------------------------------------ bars
def _downbeat_phase(y: np.ndarray, sr: int, beats: np.ndarray, oenv: np.ndarray) -> tuple[int, float, dict]:
    """Bar phase (0..3) for 4/4: which beat index mod 4 is "one".

    Evidence per beat: low-band (< 160 Hz, kick/bass) onset strength, harmonic change
    (chroma of the 4 beats before vs the 4 beats after: chords and bass notes change on
    bar lines), the same 4-beat change for timbre/loudness (instruments enter on bar lines)
    and, negatively, high-band (> 2 kHz) onsets without low-band ones (snare/clap
    backbeats on 2 and 4). The phase whose beats score highest wins; the first tracked beat's
    phase gets a small prior (FIRST_BEAT_PRIOR), since most songs start on a downbeat.
    """
    import librosa
    if len(beats) < 8:
        return 0, 0.0, {}
    S = np.abs(librosa.stft(y, hop_length=HOP))
    mel = librosa.feature.melspectrogram(S=S**2, sr=sr, n_mels=64, fmax=8000)
    mel_db = librosa.power_to_db(mel)
    mf = librosa.mel_frequencies(n_mels=64, fmax=8000)
    low = librosa.onset.onset_strength(S=mel_db[mf < 160], sr=sr, hop_length=HOP)
    high = librosa.onset.onset_strength(S=mel_db[mf > 2000], sr=sr, hop_length=HOP)
    chroma = librosa.feature.chroma_stft(S=S, sr=sr, hop_length=HOP)
    bf = np.clip(librosa.time_to_frames(beats, sr=sr, hop_length=HOP), 0, len(oenv) - 1)

    def at(env):  # onset peaks trail the attack by ~20 ms (beats are on the attack)
        return np.array([env[max(0, f - 2): f + 6].max() for f in bf])

    low_b, high_b = _z(at(low)), _z(at(high))
    edges = np.concatenate([bf, [min(len(oenv) - 1, bf[-1] + int(np.median(np.diff(bf))))]])
    cs = [chroma[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])]
    cs = np.array(cs)

    def cosd(a, b):
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        return 0.0 if na < 1e-9 or nb < 1e-9 else 1 - float(a @ b / (na * nb))

    # bar-to-bar change: the 4 beats before vs the 4 beats after. Every 4-beat window holds
    # the whole drum pattern, so only real harmonic movement (on bar lines) shows up here.
    nov = [cosd(cs[max(0, i - 4):i].mean(axis=0), cs[i:i + 4].mean(axis=0)) if 0 < i < len(cs) - 1 else 0.0
           for i in range(len(cs))]
    nov_b = _z(np.array(nov))
    # the same 4-beat-window change for timbre/loudness: instruments enter and leave on bar lines
    ms = np.array([mel_db[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])])
    tim = [float(np.linalg.norm(ms[max(0, i - 4):i].mean(axis=0) - ms[i:i + 4].mean(axis=0)))
           if 0 < i < len(ms) - 1 else 0.0 for i in range(len(ms))]
    tim_b = _z(np.array(tim))
    backbeat = np.clip(high_b - low_b, 0, None)
    feat = low_b + nov_b + 0.5 * tim_b - backbeat
    scores = np.array([feat[p::4].mean() for p in range(4)])
    scores[0] += FIRST_BEAT_PRIOR  # most songs start on a downbeat
    order = np.argsort(scores)[::-1]
    conf = float(scores[order[0]] - scores[order[1]])
    parts = {k: [round(float(v[p::4].mean()), 2) for p in range(4)]
             for k, v in (("low", low_b), ("harmonic_change", nov_b), ("timbre_change", tim_b), ("backbeat", backbeat))}
    return int(order[0]), conf, {"phase_scores": [round(float(s), 3) for s in scores], **parts}


# ------------------------------------------------------------------ sections
def _sections(y: np.ndarray, sr: int, beats: np.ndarray, downbeat_idx: list[int],
              beat_energy: np.ndarray, duration: float) -> list[dict]:
    import librosa
    n = len(beats)
    if n < 8 or len(downbeat_idx) < 3:
        return [{"start_beat": 0, "end_beat": max(0, n - 1)}]
    S = np.abs(librosa.stft(y, hop_length=HOP))
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(librosa.feature.melspectrogram(S=S**2, sr=sr)),
                                sr=sr, n_mfcc=13)
    chroma = librosa.feature.chroma_stft(S=S, sr=sr, hop_length=HOP)
    rms = librosa.feature.rms(S=S)
    bars = list(downbeat_idx) + ([n - 1] if downbeat_idx[-1] < n - 1 else [])
    bf = librosa.time_to_frames(beats[bars], sr=sr, hop_length=HOP)
    F = []
    for a, b in zip(bf[:-1], bf[1:]):
        b = max(b, a + 1)
        F.append(np.concatenate([mfcc[1:, a:b].mean(axis=1) / 20.0, chroma[:, a:b].mean(axis=1),
                                 [3.0 * np.log10(rms[0, a:b].mean() + 1e-6)]]))
    F = np.array(F)
    F = (F - F.mean(axis=0)) / (F.std(axis=0) + 1e-6)
    F[:, -1] *= 3.0  # loudness weighs as much as a timbre group
    nb = len(F)
    W = 4 if nb >= 16 else 2
    nov = np.zeros(nb)
    for i in range(1, nb):
        a, b = F[max(0, i - W):i], F[i:i + W]
        if len(a) and len(b):
            nov[i] = np.linalg.norm(a.mean(axis=0) - b.mean(axis=0))
    min_bars = 4 if nb >= 12 else 2
    k_max = max(1, int(round(duration / 15.0)))
    thr = nov[1:].mean() + 0.5 * nov[1:].std() if nb > 2 else np.inf
    bounds: list[int] = []
    for i in np.argsort(nov)[::-1]:
        if nov[i] < thr or len(bounds) >= k_max:
            break
        if i < min_bars or nb - i < min_bars // 2 or any(abs(i - j) < min_bars for j in bounds):
            continue
        bounds.append(int(i))
    bar_edges = [0] + sorted(bounds) + [nb]
    return [{"start_beat": bars[a], "end_beat": bars[b] if b < len(bars) else n - 1,
             "novelty": round(float(nov[a]), 3)} for a, b in zip(bar_edges[:-1], bar_edges[1:])]


def _label_sections(secs: list[dict], beat_energy: np.ndarray) -> None:
    es = [float(beat_energy[s["start_beat"]:max(s["end_beat"], s["start_beat"] + 1)].mean()) for s in secs]
    top = max(es) if es else 1.0
    for i, (s, e) in enumerate(zip(secs, es)):
        rel = e / top if top > 0 else 0.0
        level = "high" if rel >= 0.8 and e >= 0.35 else "mid" if rel >= 0.5 and e >= 0.2 else "low"
        prev = es[i - 1] if i else None
        if len(secs) > 1 and i == 0 and level != "high":
            lab = "intro"
        elif len(secs) > 2 and i == len(secs) - 1 and prev is not None and e < 0.85 * prev:
            lab = "outro"
        elif level == "high":
            lab = "drop" if prev is not None and e - prev >= 0.25 else "chorus"
        elif level == "mid":
            lab = "verse"
        else:
            lab = "break"
        s.update(label=lab, level=level, energy=round(e, 3))


# ------------------------------------------------------------------ excerpt
def choose_excerpt(an: dict, max_len: float = MAX_EXCERPT_S) -> dict:
    """Best window of at most ``max_len`` s: starts on a downbeat, whole bars, highest mean energy.

    If everything from the first downbeat to the last beat fits, that is the window. Otherwise
    score = mean beat energy + 0.3 * (length / max_len) (+0.03 when it starts a section), so
    full-length windows win unless a shorter one is clearly more energetic.
    """
    beats = np.array(an["beats"])
    e = np.array(an["beat_energy"])
    n = len(beats)
    db = [i for i in an["downbeat_beats"] if i < n - 1]
    starts = set(s["start_beat"] for s in an["sections"])
    best = None
    if db and beats[n - 1] - beats[db[0]] <= max_len:  # the whole song fits: take all of it
        db = db[:1]
    for i in db:
        ends = [j for j in an["downbeat_beats"] if j > i and beats[j] - beats[i] <= max_len]
        if beats[n - 1] - beats[i] <= max_len and (not ends or ends[-1] < n - 1):
            ends.append(n - 1)  # run to the last beat when it fits
        if not ends:
            continue
        j = max(ends)
        if j - i < 8:
            continue
        length = beats[j] - beats[i]
        score = float(e[i:j].mean()) + (0.03 if i in starts else 0.0) + 0.3 * min(1.0, length / max_len)
        if best is None or score > best[0] + 1e-9:
            best = (score, i, j)
    if best is None:  # very short song: everything
        i, j, score = 0, n - 1, float(e.mean()) if n else 0.0
    else:
        score, i, j = best
    return {"start": round(float(beats[i]), 3), "end": round(float(beats[j]), 3), "start_beat": int(i),
            "end_beat": int(j), "n_beats": int(j - i), "duration": round(float(beats[j] - beats[i]), 3),
            "mean_energy": round(float(e[i:j].mean()) if j > i else 0.0, 3), "score": round(score, 3),
            "max_len": max_len}


# ------------------------------------------------------------------ main entry
def analyze(path: str | Path, max_excerpt: float = MAX_EXCERPT_S) -> dict[str, Any]:
    import librosa
    path = Path(path)
    y = load_audio(path, SR)
    duration = len(y) / SR
    beats, oenv, ft = _track_beats(y, SR)
    bpm, period, stab = _fit_tempo(beats)
    n = len(beats)

    # per-beat energy
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP)[0]
    rt = librosa.frames_to_time(np.arange(len(rms)), sr=SR, hop_length=HOP)
    be = []
    for k in range(n):
        a = beats[k]
        b = beats[k + 1] if k + 1 < n else a + (period or 0.5)
        m = (rt >= a) & (rt < b)
        be.append(float(np.sqrt(np.mean(rms[m] ** 2))) if m.any() else 0.0)
    be = np.array(be)
    ref = float(np.percentile(be, 95)) if n else 1.0
    beat_energy = np.clip(be / ref, 0, 1) if ref > 0 else be

    phase, conf, extra = _downbeat_phase(y, SR, beats, oenv)
    down_idx = list(range(phase, n, 4))
    secs = _sections(y, SR, beats, down_idx, beat_energy, duration)
    _label_sections(secs, beat_energy)
    for s in secs:
        s["start"] = round(float(beats[s["start_beat"]]), 3)
        s["end"] = round(float(beats[s["end_beat"]]) if s["end_beat"] < n else duration, 3)
    # sections: first one starts at 0 s, last ends at the end of the file (labels cover the whole song)
    if secs:
        secs[0]["start"] = 0.0
        secs[-1]["end"] = round(duration, 3)

    # key moments: biggest jumps in bar energy (2 bars after vs 2 bars before)
    bar_e = np.array([beat_energy[i:i + 4].mean() for i in down_idx]) if down_idx else np.zeros(0)
    km = []
    for b in range(1, len(bar_e)):
        d = bar_e[b:b + 2].mean() - bar_e[max(0, b - 2):b].mean()
        km.append((float(d), b))
    km.sort(reverse=True)
    moments: list[dict] = []
    for d, b in km:
        if d < 0.12 or len(moments) >= 6:
            break
        if any(abs(b - m["bar"]) < 4 for m in moments):
            continue
        bi = down_idx[b]
        sec = next((s["label"] for s in secs if s["start_beat"] <= bi < max(s["end_beat"], bi + 1)), None)
        moments.append({"t": round(float(beats[bi]), 3), "beat": int(bi), "bar": int(b),
                        "delta": round(d, 3), "section": sec})
    moments.sort(key=lambda m: m["t"])

    onsets = librosa.onset.onset_detect(onset_envelope=oenv, sr=SR, hop_length=HOP, units="time")
    npk = 1200
    hop = max(1, len(y) // npk)
    peaks = np.abs(y[: hop * (len(y) // hop)]).reshape(-1, hop).max(axis=1)
    peaks = peaks / (peaks.max() or 1.0)

    an: dict[str, Any] = {
        "version": ANALYSIS_VERSION,
        "source": {"file": path.name, "path": _rel(path), "sha1": _sha1(path), "slug": slug(path.name)},
        "duration": round(duration, 3),
        "tempo_bpm": round(bpm, 2), "beat_period": round(period, 4), "tempo_stability": round(stab, 4),
        "meter": "4/4 (assumed)",
        "beats": [round(float(t), 3) for t in beats],
        "downbeat_phase": phase, "downbeat_confidence": round(conf, 3),
        "downbeat_beats": down_idx,
        "downbeats": [round(float(beats[i]), 3) for i in down_idx],
        "beat_energy": [round(float(x), 3) for x in beat_energy],
        "sections": secs,
        "key_moments": moments,
        "onsets": [round(float(t), 3) for t in onsets],
        "waveform": {"dt": round(hop / SR, 5), "peaks": [round(float(p), 3) for p in peaks]},
        **({"bar_phase_detail": extra} if extra else {}),
    }
    an["excerpt"] = choose_excerpt(an, max_excerpt)
    return an


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def analysis_path(audio: str | Path) -> Path:
    return ANALYSIS_DIR / f"{slug(Path(audio).name)}.json"


def analyze_cached(audio: str | Path, refresh: bool = False) -> dict[str, Any]:
    """Analysis from music/analysis/<slug>.json when it matches the file's sha1, else recompute and save."""
    audio = Path(audio)
    out = analysis_path(audio)
    if out.exists() and not refresh:
        try:
            an = json.loads(out.read_text())
            if an.get("version") == ANALYSIS_VERSION and an["source"]["sha1"] == _sha1(audio):
                return an
        except Exception:
            pass
    an = analyze(audio)
    save_analysis(an, out)
    return an


def save_analysis(an: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    # compact arrays, readable top level
    out.write_text(json.dumps(an, indent=1, separators=(",", ": ")) + "\n")
    return out


def summary(an: dict) -> str:
    L = [f"{an['source']['file']}: {an['duration']:.1f} s, {an['tempo_bpm']:.1f} BPM "
         f"(beat {an['beat_period'] * 1000:.0f} ms, stability {an['tempo_stability']:.3f}), "
         f"{len(an['beats'])} beats, {len(an['downbeats'])} bars",
         f"  first beat {an['beats'][0]:.3f} s, first downbeat {an['downbeats'][0]:.3f} s "
         f"(bar phase {an['downbeat_phase']}, confidence {an['downbeat_confidence']:.2f})"
         if an["beats"] and an["downbeats"] else "  no beats found",
         "  sections:"]
    for s in an["sections"]:
        bar = "#" * int(round(s["energy"] * 20))
        L.append(f"    {s['start']:6.1f}-{s['end']:6.1f} s  beats {s['start_beat']:4d}-{s['end_beat']:4d}  "
                 f"{s['label']:7} {s['level']:4} energy {s['energy']:.2f} {bar}")
    if an["key_moments"]:
        L.append("  key moments: " + ", ".join(f"{m['t']:.1f} s (+{m['delta']:.2f})" for m in an["key_moments"]))
    x = an["excerpt"]
    L.append(f"  excerpt: {x['start']:.2f}-{x['end']:.2f} s ({x['duration']:.1f} s, beats {x['start_beat']}-"
             f"{x['end_beat']}, mean energy {x['mean_energy']:.2f})")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m armctl.music", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("analyze", help="beats, bars, sections, energy; prints a summary")
    a1.add_argument("audio")
    a1.add_argument("--json", help="write the analysis here (default music/analysis/<slug>.json)")
    a1.add_argument("--refresh", action="store_true", help="ignore the cached analysis")
    a2 = sub.add_parser("excerpt", help="the best <= --max s window, starting on a downbeat")
    a2.add_argument("audio")
    a2.add_argument("--max", type=float, default=MAX_EXCERPT_S)
    a = ap.parse_args(argv)
    if a.cmd == "analyze":
        if a.json:
            an = analyze(a.audio)
            save_analysis(an, Path(a.json))
            print(f"wrote {a.json}")
        else:
            an = analyze_cached(a.audio, refresh=a.refresh)
            print(f"analysis: {_rel(analysis_path(a.audio))}")
        print(summary(an))
    else:
        an = analyze_cached(a.audio)
        print(json.dumps(choose_excerpt(an, a.max), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
