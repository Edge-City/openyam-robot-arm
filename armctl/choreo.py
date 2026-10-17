"""armctl.choreo: song analysis -> a beat-synced, validated dance playable (and its audio clip).

Nothing here moves the arm. Pipeline::

    audio --armctl.music.analyze--> analysis JSON (beats, bars, sections, energy, excerpt)
          --auto_plan (or your plan JSON)--> plan: [{start_beat, end_beat, motif, amplitude, note}]
          --realize--> playable (keyframes on beats, cubic ease) --validate (dance caps) + FK clearance
          --> playables/<name>.json + music/clips/<name>.wav + music/plans/<name>.json

CLI (repo root)::

    .venv/bin/python -m armctl.choreo music/inbox/song.mp3 [--style groove|hype|chill] [--seed 0]
        [--plan plan.json] [--start 42.0] [--max-len 55] [--emit-plan plan.json] [--save name]
    .venv/bin/python -m armctl.choreo motifs        # the motif library, for plan writers

The plan is the "intelligence layer": read the analysis (sections, energy, key moments),
then write or edit the plan; beats are counted from the window start (beat 0 = the first
downbeat of the excerpt, the arm is at "ready" there and at the last beat). Gaps are
filled with "hold". ``amplitude`` (0..1.5, default by energy) scales the motif's nominal
size; it is then capped so every move satisfies the dance caps at the song's real beat
times, so a fast song gets smaller moves, never faster ones.

Speed rule (cubic/smoothstep ease, see make_beat_dance.py): a move of d rad in T s peaks
at 1.5*d/T rad/s and 6*d/T^2 rad/s^2, so d <= min(0.9*0.6*T/1.5, 0.9*2.0*T^2/6)
(90% of the "dance" caps 0.6 rad/s, 2.0 rad/s^2), per joint, per segment.

Audio: the clip is the excerpt with BLEND_S = 1.0 s of silence in front, because
play_real.py starts ``aplay`` when the trajectory is accepted and spends BLEND_S blending
from the measured pose to the first keyframe; beat 0 then meets keyframe 0.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from armctl import fk
from armctl import music as M
from armctl import playable as P

REPO = Path(__file__).resolve().parents[1]
READY = np.array([0.0, 0.5, 0.8, 0.0, 0.0, 0.0])
UP_D = np.array([0.0, 0.35, 0.4, -0.2, 0.0, 0.0])  # make_beat_dance UP minus READY
CAPS = "dance"
VEL_CAP, ACC_CAP = P.CAP_PRESETS[CAPS]
CAP_MARGIN = 0.9
SOFT_MARGIN = 0.05
BLEND_S = 1.0  # must match play_real.BLEND_S
MAX_AMPLITUDE = 1.5
STYLES = ("groove", "hype", "chill")
PLAY_CMD = "experiments/2026-10-16-linux-real-arm/session_play.sh {name} --caps dance --audio music/clips/{name}.wav"


def d_max(T: float) -> float:
    """Largest cubic-ease move (rad, per joint) that fits the dance caps in T seconds."""
    return min(CAP_MARGIN * VEL_CAP * T / 1.5, CAP_MARGIN * ACC_CAP * T * T / 6.0)


def J(**kw: float) -> np.ndarray:
    v = np.zeros(6)
    for k, x in kw.items():
        v[int(k[1]) - 1] = x
    return v


def mirror(v: np.ndarray) -> np.ndarray:
    """Left/right mirror: flip base yaw (j1), wrist yaw (j5) and wrist roll (j6)."""
    return v * np.array([-1, 1, 1, 1, -1, -1])


# ------------------------------------------------------------------ motif library
@dataclass
class Motif:
    name: str
    unit: int                  # beats per step at slow tempos (doubled until a step lasts >= min_step)
    cycle: list[np.ndarray]    # poses (offsets from ready, nominal amplitude) visited one per step
    desc: str
    kind: str = "cycle"        # "cycle" | "hit" | "freeze" | "hold"
    extra: dict = field(default_factory=dict)

    def steps(self, n: int, flip: bool) -> list[tuple[np.ndarray, str]]:
        """Offsets (and ease) for steps 1..n; the last step is always back at ready."""
        f = mirror if flip else (lambda v: v)
        z = np.zeros(6)
        if n <= 0:
            return []
        if self.kind == "hold":
            return [(z, "hold")] * n
        if self.kind == "freeze":
            F = f(self.cycle[0])
            if n == 1:
                return [(z, "cubic")]
            return [(F, "cubic")] + [(F, "hold")] * (n - 2) + [(z, "cubic")]
        if self.kind == "hit":
            A, B = f(self.cycle[0]), f(self.cycle[1])
            out: list[tuple[np.ndarray, str]] = []
            group = 0
            while len(out) < n:
                a, b = (A, B) if group % 2 == 0 else (mirror(A), mirror(B))
                out += [(a, "cubic"), (b, "cubic"), (b, "hold"), (z, "cubic")]
                group += 1
            out = out[:n]
            out[-1] = (z, "cubic")
            return out
        out = [(f(self.cycle[i % len(self.cycle)]), "cubic") for i in range(n)]
        out[-1] = (z, "cubic")
        return out


# Arm raised high with the gripper tipped up, like a phone or lighter held aloft (offset from ready).
LIFT_D = J(j2=0.45, j3=0.9, j4=-0.35)

MOTIFS: dict[str, Motif] = {m.name: m for m in [
    Motif("hold", 4, [], "stay still at ready (rests, silences, a dramatic pause)", kind="hold"),
    Motif("nod", 1, [J(j4=0.2), J()], "wrist pitch nod on every step (count-ins, quiet parts)"),
    Motif("bounce", 1, [J(j2=0.08, j3=0.12), J()], "small up/down bounce of shoulder+elbow every step (fast, light)"),
    Motif("sway", 2, [J(j1=0.25, j6=-0.25), J(j1=-0.25, j6=0.25)],
          "base sways left/right with a counter-roll of the wrist (groove)"),
    Motif("pump", 2, [UP_D, J()], "pump up to 'up' and back down (energy, chorus)"),
    Motif("sweep", 4, [J(j1=0.9, j5=0.4), J(j1=-0.9, j5=-0.4)],
          "big slow base sweep right/left with a wrist swing (builds, long notes)"),
    Motif("wrist-roll", 2, [J(j6=0.3, j4=0.06), J(j6=-0.3, j4=0.06)], "roll the wrist on the beat"),
    Motif("wrist-wave", 2, [J(j5=0.35, j4=-0.1), J(j5=-0.35, j4=-0.1)], "wrist yaw wave left/right"),
    Motif("up-side", 2, [UP_D + J(j1=0.2, j6=0.2), UP_D + J(j1=-0.2, j6=-0.2)],
          "up-left / up-right combo (high energy)"),
    Motif("hit", 2, [J(j1=0.2, j4=-0.2, j6=0.35), J(j1=-0.2, j4=-0.2, j6=-0.35)],
          "accent hits: A, B (lands on the bar's downbeat), freeze, back; 2 bars per group", kind="hit"),
    Motif("freeze", 2, [J(j2=0.12, j3=0.2, j4=-0.25, j6=0.4)],
          "strike a pose on the first step, freeze, return on the last (breaks, stops)", kind="freeze"),
    Motif("breathe", 4, [J(j2=0.12, j3=0.2, j4=-0.1), J()], "slow rise and settle (chill, intros, outros)"),
    Motif("lighter-wave", 4, [LIFT_D + J(j1=0.35, j6=-0.2), LIFT_D + J(j1=-0.35, j6=0.2)],
          "concert 'phone in the air' wave: arm raised high, slow side-to-side sway, one side per step (ballads, singalongs)"),
]}

POOLS = {
    "chill": {"low": ["breathe", "nod", "sway"], "mid": ["sway", "wrist-wave", "breathe"],
              "high": ["sweep", "sway", "up-side"]},
    "groove": {"low": ["nod", "sway", "wrist-roll"], "mid": ["sway", "pump", "wrist-roll", "wrist-wave"],
               "high": ["up-side", "pump", "sweep", "hit"]},
    "hype": {"low": ["bounce", "nod", "freeze"], "mid": ["pump", "hit", "bounce"],
             "high": ["hit", "pump", "up-side"]},
}
ACCENT = {"chill": "sweep", "groove": "hit", "hype": "hit"}
MIN_STEP_S = {"chill": 0.9, "groove": 0.5, "hype": 0.4}
BLOCK_BEATS = {"chill": 16, "groove": 8, "hype": 8}


def default_amplitude(style: str, energy: float) -> float:
    a = {"chill": 0.45 + 0.35 * energy, "groove": 0.6 + 0.4 * energy, "hype": 0.8 + 0.3 * energy}[style]
    return round(min(MAX_AMPLITUDE, a), 2)


def motifs_doc() -> str:
    L = ["motif        unit  description"]
    for m in MOTIFS.values():
        L.append(f"{m.name:12} {m.unit:>3}b  {m.desc}")
    L.append("unit = beats per step at slow tempos; doubled while a step would be shorter than the "
             "style's minimum (groove 0.5 s, hype 0.4 s, chill 0.9 s). Every motif starts and ends at ready.")
    return "\n".join(L)


# ------------------------------------------------------------------ window
def window_from(an: dict, start: float | None = None, max_len: float = M.MAX_EXCERPT_S,
                start_beat: int | None = None, end_beat: int | None = None) -> dict:
    """A window on the analysis' beat grid. Default: the analysis' suggested excerpt.

    ``start`` (s) snaps to the nearest downbeat; the window then runs whole bars up to
    ``max_len`` s (or to the last beat).
    """
    beats = an["beats"]
    n = len(beats)
    if start is None and start_beat is None:
        x = an["excerpt"]
        if x["duration"] <= max_len + 1e-6:
            return dict(x)
        start_beat = x["start_beat"]
    if start_beat is None:
        start_beat = min(an["downbeat_beats"], key=lambda i: abs(beats[i] - start))
    i = int(start_beat)
    if not 0 <= i < n - 1:
        raise ValueError(f"window start beat {i} outside 0..{n - 2}")
    if end_beat is None:
        ends = [j for j in range(i + 4, n, 4) if beats[j] - beats[i] <= max_len]
        if beats[n - 1] - beats[i] <= max_len:
            ends.append(n - 1)
        if not ends:
            raise ValueError("window too short: less than one bar fits")
        end_beat = max(ends)
    j = int(end_beat)
    if not i < j < n:
        raise ValueError(f"window end beat {j} must be in {i + 1}..{n - 1}")
    if beats[j] - beats[i] > max_len + 1e-6:
        raise ValueError(f"window {beats[j] - beats[i]:.1f} s is longer than {max_len} s")
    e = an["beat_energy"][i:j]
    return {"start": beats[i], "end": beats[j], "start_beat": i, "end_beat": j, "n_beats": j - i,
            "duration": round(beats[j] - beats[i], 3), "mean_energy": round(float(np.mean(e)), 3),
            "max_len": max_len}


# ------------------------------------------------------------------ automatic plan
def _level(e: float) -> str:
    return "high" if e >= 0.7 else "mid" if e >= 0.4 else "low"


def auto_plan(an: dict, window: dict, style: str = "groove", seed: int = 0) -> dict:
    """Choose motifs per section: a motif pair per section label (repeated sections dance alike),
    alternated per block; a key moment (energy jump) gets the style's accent motif."""
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}")
    rng = np.random.default_rng(seed)
    w0, N = window["start_beat"], window["n_beats"]
    energy = np.array(an["beat_energy"][w0:w0 + N] or [0.5])
    # section boundaries inside the window, in window beats, snapped to bars
    bounds = {0, N}
    for s in an["sections"]:
        b = s["start_beat"] - w0
        if 0 < b < N:
            b = int(round(b / 4) * 4)
            if 4 <= b <= N - 4:
                bounds.add(b)
    bounds = sorted(bounds)
    keys = {int(round((m["beat"] - w0) / 4) * 4) for m in an["key_moments"] if 0 <= m["beat"] - w0 < N}

    def section_at(b: int) -> dict:
        ab = b + w0
        for s in an["sections"]:
            if s["start_beat"] <= ab < max(s["end_beat"], s["start_beat"] + 1):
                return s
        return an["sections"][-1] if an["sections"] else {"label": "song", "level": "mid"}

    pairs: dict[str, list[str]] = {}
    segs: list[dict] = []
    prev = None
    for a, b in zip(bounds[:-1], bounds[1:]):
        sec = section_at(a)
        lev = sec.get("level") or _level(float(energy[a:b].mean()))
        lab = sec.get("label", "song")
        if lab not in pairs:
            pool = list(POOLS[style][lev])
            rng.shuffle(pool)
            if pool[0] == prev and len(pool) > 1:
                pool[0], pool[1] = pool[1], pool[0]
            pairs[lab] = pool[:2] if len(pool) > 1 else pool * 2
        size = BLOCK_BEATS[style] if b - a >= BLOCK_BEATS[style] else 8
        edges = list(range(a, b, size)) + [b]
        if len(edges) > 2 and edges[-1] - edges[-2] < 4:  # don't leave a sliver: merge it
            edges.pop(-2)
        for k, (s0, s1) in enumerate(zip(edges[:-1], edges[1:])):
            e = float(energy[s0:s1].mean()) if s1 > s0 else 0.5
            motif = pairs[lab][k % 2]
            note = f"{lab} ({lev}), energy {e:.2f}"
            if s0 in keys and lev != "low":
                motif, note = ACCENT[style], note + "; key moment (energy jump): accent"
            if s0 == 0 and lev != "high" and style != "hype" and s1 - s0 >= 4:
                motif, note = ("breathe" if style == "chill" else "nod"), note + "; opening"
            if motif == prev and len(set(pairs[lab])) > 1:
                motif = pairs[lab][(k + 1) % 2]
            segs.append({"start_beat": s0, "end_beat": s1, "motif": motif,
                         "amplitude": default_amplitude(style, e), "note": note})
            prev = motif
    return {"song": an["source"]["file"], "analysis_sha1": an["source"]["sha1"], "style": style, "seed": seed,
            "tempo_bpm": an["tempo_bpm"], "beat_period": an["beat_period"],
            "window": {k: window[k] for k in ("start", "end", "start_beat", "end_beat", "n_beats")},
            "segments": segs}


# ------------------------------------------------------------------ plan -> playable
def check_plan(plan: dict, n_beats: int) -> list[dict]:
    """Validate a plan's segments; return them sorted with gaps filled by 'hold'. Raises ValueError."""
    segs = plan.get("segments")
    if not isinstance(segs, list) or not segs:
        raise ValueError("plan.segments must be a non-empty list of {start_beat, end_beat, motif, amplitude, note}")
    out = []
    for i, s in enumerate(segs):
        try:
            a, b = int(s["start_beat"]), int(s["end_beat"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"segment {i}: start_beat and end_beat must be integers")
        if s.get("motif") not in MOTIFS:
            raise ValueError(f"segment {i}: unknown motif {s.get('motif')!r}; use one of {', '.join(MOTIFS)}")
        if not 0 <= a < b <= n_beats:
            raise ValueError(f"segment {i}: beats {a}..{b} must satisfy 0 <= start < end <= {n_beats}")
        amp = s.get("amplitude", 0.8)
        if not isinstance(amp, (int, float)) or not 0 <= amp <= MAX_AMPLITUDE:
            raise ValueError(f"segment {i}: amplitude must be 0..{MAX_AMPLITUDE}")
        seg = {"start_beat": a, "end_beat": b, "motif": s["motif"], "amplitude": float(amp),
               "note": str(s.get("note", ""))[:200]}
        if s.get("step_beats") is not None:
            sbv = s["step_beats"]
            if not isinstance(sbv, int) or not 1 <= sbv <= b - a:
                raise ValueError(f"segment {i}: step_beats must be an integer 1..{b - a}")
            seg["step_beats"] = sbv
        out.append(seg)
    out.sort(key=lambda s: s["start_beat"])
    filled, t = [], 0
    for s in out:
        if s["start_beat"] < t:
            raise ValueError(f"segments overlap at beat {s['start_beat']} (previous ends at {t})")
        if s["start_beat"] > t:
            filled.append({"start_beat": t, "end_beat": s["start_beat"], "motif": "hold", "amplitude": 0.0,
                           "note": "gap (auto hold)"})
        filled.append(s)
        t = s["end_beat"]
    if t < n_beats:
        filled.append({"start_beat": t, "end_beat": n_beats, "motif": "hold", "amplitude": 0.0,
                       "note": "gap (auto hold)"})
    return filled


def _block(seg: dict, bt: np.ndarray, style: str, flip: bool, scale: float = 1.0) -> dict:
    """Keyframes (beat index, offset, ease) for one segment, amplitude capped by caps and soft limits."""
    m = MOTIFS[seg["motif"]]
    a, b = seg["start_beat"], seg["end_beat"]
    L = b - a
    period = float(np.median(np.diff(bt))) if len(bt) > 1 else 0.5
    if seg.get("step_beats"):  # the plan fixes how many detected beats one step (one movement) takes
        sb = seg["step_beats"]
    else:
        sb = m.unit
        while sb * period < MIN_STEP_S[style] and sb * 2 <= L // 2:
            sb *= 2
        while sb > 1 and sb > L // 2:  # short segment: halve the step (stays a power-of-2 beat count)
            sb //= 2
    n = max(1, L // sb)
    beats_at = [a + (k + 1) * sb for k in range(n)]
    beats_at[-1] = b  # the last step lands on the segment end (extra beats make it slower, not faster)
    steps = m.steps(n, flip)
    # amplitude: requested, then what each cubic move allows at the real beat times
    req = seg["amplitude"] * scale
    lim_caps, lim_soft = math.inf, math.inf
    prev_o, prev_b = np.zeros(6), a
    for (o, ease), bb in zip(steps, beats_at):
        T = float(bt[bb] - bt[prev_b])
        if ease != "hold":
            d = np.abs(o - prev_o)
            for x in d[d > 1e-9]:
                lim_caps = min(lim_caps, d_max(T) / x)
        prev_o, prev_b = o, bb
    lo = np.array([x for x, _ in P.envelope().soft]) + SOFT_MARGIN
    hi = np.array([x for _, x in P.envelope().soft]) - SOFT_MARGIN
    for o, _ in steps:
        for j in range(6):
            if o[j] > 1e-9:
                lim_soft = min(lim_soft, (hi[j] - READY[j]) / o[j])
            elif o[j] < -1e-9:
                lim_soft = min(lim_soft, (lo[j] - READY[j]) / o[j])
    amp = min(req, lim_caps, lim_soft)
    limited = "requested" if amp == req else ("caps" if lim_caps <= lim_soft else "soft limits")
    keys = [(bb, READY + amp * o, ease) for (o, ease), bb in zip(steps, beats_at)]
    return {"keys": keys, "amplitude": round(amp, 3), "requested": round(seg["amplitude"], 3),
            "limited_by": limited if seg["motif"] != "hold" else "-", "step_beats": sb, "steps": n}


def realize(an: dict, plan: dict, name: str = "music-dance", style: str | None = None,
            run_fk: bool = True) -> tuple[dict, dict]:
    """Plan -> (playable, report). Shrinks a segment's amplitude if validation or FK still complain."""
    style = style or plan.get("style", "groove")
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}")
    win = plan.get("window") or an["excerpt"]
    w0, w1 = int(win["start_beat"]), int(win["end_beat"])
    if not 0 <= w0 < w1 < len(an["beats"]):
        raise ValueError(f"plan window beats {w0}..{w1} outside the analysis (0..{len(an['beats']) - 1})")
    N = w1 - w0
    bt = np.array(an["beats"][w0:w1 + 1]) - an["beats"][w0]
    if bt[-1] > P.MAX_DURATION_S - 1.0:
        raise ValueError(f"window lasts {bt[-1]:.1f} s; keep it <= {P.MAX_DURATION_S - 1:.0f} s (playables max 60 s)")
    segs = check_plan(plan, N)
    scale = [1.0] * len(segs)
    flips: list[bool] = []
    seen: dict[str, int] = {}
    for s in segs:
        flips.append(seen.get(s["motif"], 0) % 2 == 1)
        seen[s["motif"]] = seen.get(s["motif"], 0) + 1

    for attempt in range(8):
        blocks = [_block(s, bt, style, f, sc) for s, f, sc in zip(segs, flips, scale)]
        kf = [{"t": 0.0, "q": [round(float(x), 4) for x in READY], "grip": 0.0, "ease": "cubic"}]
        owner = [None]
        for bi, blk in enumerate(blocks):
            for bb, q, ease in blk["keys"]:
                kf.append({"t": round(float(bt[bb]), 3), "q": [round(float(x), 4) for x in q], "grip": 0.0,
                           "ease": ease})
                owner.append(bi)
        pl = {"name": name, "author": f"armctl.choreo ({style})", "created": P.now_iso(), "space": "joint",
              "start": "ready", "loop": 1, "speed": 1.0, "caps": CAPS,
              "note": (f"{an['source']['file']} {an['tempo_bpm']:.1f} BPM, beats {w0}-{w1} "
                       f"({an['beats'][w0]:.2f}-{an['beats'][w1]:.2f} s), style {style}; "
                       f"play with --caps dance --audio music/clips/{name}.wav"),
              "keyframes": kf,
              "music": {"song": an["source"]["file"], "sha1": an["source"]["sha1"],
                        "analysis": M._rel(M.analysis_path(an["source"]["file"])),
                        "clip": f"music/clips/{name}.wav", "blend_s": BLEND_S, "tempo_bpm": an["tempo_bpm"],
                        "window": {"start": an["beats"][w0], "end": an["beats"][w1], "start_beat": w0,
                                   "end_beat": w1, "n_beats": N}}}
        r = P.validate(pl)
        bad_blocks: set[int] = set()
        for v in r["violations"]:
            if v.get("t") is None:
                continue
            k = next((i for i in range(1, len(kf)) if kf[i]["t"] >= v["t"] - 1e-6), len(kf) - 1)
            if owner[k] is not None:
                bad_blocks.add(owner[k])
        fkr = None
        if run_fk:
            s = P.sample(pl, 50) if r["ok"] or not bad_blocks else None
            if s is not None:
                fkr = fk.clearance(s["q"], s["t"])
                for f in (fkr.get("failures") or []) if fkr.get("available") else []:
                    k = next((i for i in range(1, len(kf)) if kf[i]["t"] >= f["t"] - 1e-6), len(kf) - 1)
                    if owner[k] is not None:
                        bad_blocks.add(owner[k])
        if not bad_blocks:
            break
        for bi in bad_blocks:
            scale[bi] *= 0.8
    report = {"validation": r, "fk": fkr, "attempts": attempt + 1, "style": style,
              "window": pl["music"]["window"], "duration": r["duration"],
              "blocks": [{**{k: s[k] for k in ("start_beat", "end_beat", "motif", "note")},
                          **{k: b[k] for k in ("amplitude", "requested", "limited_by", "step_beats", "steps")}}
                         for s, b in zip(segs, blocks)],
              "ok": bool(r["ok"] and (fkr is None or fkr.get("ok") in (True, None))),
              "command": PLAY_CMD.format(name=name)}
    return pl, report


def choreograph(an: dict, window: dict | None = None, style: str = "groove", seed: int = 0,
                plan: dict | None = None, name: str = "music-dance", run_fk: bool = True) -> tuple[dict, dict, dict]:
    """-> (playable, report, plan used). ``plan`` overrides the automatic motif choice."""
    if plan is None:
        plan = auto_plan(an, window or window_from(an), style, seed)
    else:
        plan = copy.deepcopy(plan)
        if window is not None and "window" not in plan:
            plan["window"] = window
        if "window" not in plan:
            plan["window"] = {k: an["excerpt"][k] for k in ("start", "end", "start_beat", "end_beat", "n_beats")}
        style = plan.get("style", style)
    pl, rep = realize(an, plan, name=name, style=style, run_fk=run_fk)
    return pl, rep, plan


# ------------------------------------------------------------------ audio clip
def write_clip(audio: str | Path, start: float, end: float, out: Path, sr: int = 44100,
               lead: float = BLEND_S, tail: float = 1.5) -> Path:
    """Excerpt [start, end + tail] (mono, 16-bit) with ``lead`` s of silence in front, faded out."""
    import soundfile as sf
    y = M.load_audio(audio, sr)
    a, b = int(start * sr), min(len(y), int((end + tail) * sr))
    x = y[a:b].astype(np.float64).copy()
    fi = min(len(x), int(0.005 * sr))
    if fi:
        x[:fi] *= np.linspace(0, 1, fi)
    fo = min(len(x), int(min(tail, 1.0) * sr))
    if fo:
        x[-fo:] *= np.linspace(1, 0, fo)
    peak = np.abs(x).max() if len(x) else 0
    if peak > 0.99:
        x *= 0.99 / peak
    x = np.concatenate([np.zeros(int(lead * sr)), x])
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), x, sr, subtype="PCM_16")
    return out


# ------------------------------------------------------------------ text
def summary(an: dict, pl: dict, rep: dict) -> str:
    w = rep["window"]
    r = rep["validation"]
    L = [f"{pl['name']}: {an['source']['file']} @ {an['tempo_bpm']:.1f} BPM, window {w['start']:.2f}-{w['end']:.2f} s "
         f"(beats {w['start_beat']}-{w['end_beat']}, {w['n_beats']} beats), style {rep['style']}, "
         f"{len(pl['keyframes'])} keyframes",
         "  beats    motif        step  amp (asked)  limited by   note"]
    for b in rep["blocks"]:
        L.append(f"  {b['start_beat']:3d}-{b['end_beat']:<3d}  {b['motif']:12} {b['step_beats']:>2}b  "
                 f"{b['amplitude']:.2f} ({b['requested']:.2f})  {b['limited_by']:11}  {b['note']}")
    L.append(f"  validation (caps '{CAPS}' {VEL_CAP} rad/s, {ACC_CAP} rad/s^2): "
             f"{'OK' if r['ok'] else 'REJECTED'}, {r['duration']} s, "
             f"max vel {max(r['max_vel'] or [0]):.2f}, max acc {max(r['max_acc'] or [0]):.2f}")
    if not r["ok"]:
        L.append(P.report(r))
    f = rep.get("fk")
    if f is None:
        L.append("  FK: not run")
    elif not f.get("available"):
        L.append(f"  FK: unavailable ({f.get('error')})")
    else:
        L.append(f"  FK: {'OK' if f['ok'] else 'TOO LOW'}: min tip z {f['min_tip_z']:.3f} m "
                 f"(>= {fk.MIN_TIP_Z}), lowest link {f['lowest_link']} z {f['min_link_z']:.3f} m (>= {fk.MIN_LINK_Z})")
    if rep["attempts"] > 1:
        L.append(f"  (amplitudes shrunk in {rep['attempts'] - 1} extra pass(es) to pass the checks)")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["motifs"]:
        print(motifs_doc())
        return 0
    ap = argparse.ArgumentParser(prog="python -m armctl.choreo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", help="song file (mp3/wav/flac/ogg/m4a...), e.g. music/inbox/song.mp3")
    ap.add_argument("--style", choices=STYLES, default="groove")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--plan", help="plan JSON (the intelligence layer) overriding the automatic motif choice")
    ap.add_argument("--start", type=float, help="window start in seconds (snapped to a downbeat)")
    ap.add_argument("--max-len", type=float, default=M.MAX_EXCERPT_S)
    ap.add_argument("--emit-plan", help="write the plan used to this path (edit it, then pass --plan)")
    ap.add_argument("--save", metavar="NAME", help="write playables/NAME.json, music/clips/NAME.wav, music/plans/NAME.json")
    ap.add_argument("--refresh", action="store_true", help="recompute the analysis")
    a = ap.parse_args(argv)
    if a.max_len > M.MAX_EXCERPT_S:
        print(f"--max-len above {M.MAX_EXCERPT_S} s leaves no room under the 60 s playable limit")
        return 2
    an = M.analyze_cached(a.audio, refresh=a.refresh)
    print(f"analysis: {M._rel(M.analysis_path(a.audio))}")
    print(M.summary(an))
    name = a.save or P.slugify(f"{M.slug(a.audio)}-{a.style}")[:64]
    if not P.NAME_RE.match(name):
        print(f"bad name {name!r} (lowercase letters, digits, '-')")
        return 2
    plan = json.loads(Path(a.plan).read_text()) if a.plan else None
    window = window_from(an, a.start, a.max_len) if (a.start is not None or a.max_len != M.MAX_EXCERPT_S) else None
    try:
        pl, rep, used = choreograph(an, window, a.style, a.seed, plan, name=name)
    except ValueError as e:
        print(f"plan/window error: {e}")
        return 2
    print(summary(an, pl, rep))
    if a.emit_plan:
        Path(a.emit_plan).write_text(json.dumps(used, indent=1) + "\n")
        print(f"wrote plan {a.emit_plan}")
    if not rep["ok"]:
        print("NOT saved: validation or FK failed")
        return 1
    if a.save:
        P.save(pl)
        M.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        (M.PLANS_DIR / f"{name}.json").write_text(json.dumps(used, indent=1) + "\n")
        clip = write_clip(a.audio, pl["music"]["window"]["start"], pl["music"]["window"]["end"],
                          REPO / "music/clips" / f"{name}.wav")
        print(f"wrote playables/{name}.json, music/plans/{name}.json, {M._rel(clip)} "
              f"({BLEND_S:.1f} s lead-in)")
        print("rehearse in sim first; on the real arm (person at the arm says go):")
        print(f"  {rep['command']}")
    else:
        print("(dry run: pass --save NAME to write the playable and clip)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
