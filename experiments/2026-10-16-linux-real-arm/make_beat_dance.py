"""Generate the beat-synced dance, the range-of-motion tours, and the beat track.

    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/make_beat_dance.py

Writes playables/beat-dance.json, playables/rom-arm.json, playables/rom-wrist.json and
media/audio/beat-dance-100bpm.wav, then validates each playable at the "dance" caps and
checks every sample with forward kinematics (no link near the table).

Timing: 100 BPM, one beat = 0.6 s. Moves land on beats with cubic (smoothstep) easing,
whose peaks are 1.5*d/T for velocity and 6*d/T^2 for acceleration. At the dance caps
(0.6 rad/s, 2.0 rad/s^2) that allows about 0.45 rad per 2-beat move and 0.12 rad per
1-beat move. play_real.py starts the audio when the trajectory starts; the WAV begins
with BLEND_S of silence, so beat 0 lands on the first keyframe.
"""

from __future__ import annotations

import json
import math
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
import armctl.playable as P  # noqa: E402

BPM = 100
BEAT = 60.0 / BPM
BLEND_S = 1.0  # must match play_real.BLEND_S
DANCE_VEL, DANCE_ACC = 0.6, 2.0
READY = [0.0, 0.5, 0.8, 0.0, 0.0, 0.0]
UP = [0.0, 0.85, 1.2, -0.2, 0.0, 0.0]
URDF = Path.home() / ".local/share/dimos/repo/data/yam_description/i2rt/yam.urdf"
MIN_TIP_Z = 0.08   # m above the base plane (the table is at or below 0)
MIN_LINK_Z = 0.03  # m, every link origin except the base


def pose(base=READY, **j):
    q = list(base)
    for k, v in j.items():
        q[int(k[1]) - 1] = v
    return q


def mk(name, keys, note):
    return {"name": name, "author": "claude (make_beat_dance.py)", "created": P.now_iso(),
            "space": "joint", "start": "ready", "loop": 1, "speed": 1.0, "caps": "dance", "note": note,
            "keyframes": [{"t": round(t, 3), "q": [round(x, 4) for x in q], "grip": 0.0, "ease": e}
                          for t, q, e in keys]}


# ------------------------------------------------------------------ beat dance (52 beats)
def beat_dance():
    k = [(0.0, READY, "cubic")]
    b = lambda n: n * BEAT  # noqa: E731
    # intro: count-in with wrist nods on beats 1-3
    for n, d in [(1, 0.1), (2, 0.0), (3, 0.1), (4, 0.0)]:
        k.append((b(n), pose(j4=d), "cubic"))
    # A: sway the base with a counter-roll, 2 beats each (beats 4-12)
    for i, n in enumerate(range(6, 13, 2)):
        s = 1 if i % 2 == 0 else -1
        k.append((b(n), pose(j1=0.22 * s, j6=-0.22 * s), "cubic"))
    # B: pump up and down (beats 12-20)
    for i, n in enumerate(range(14, 21, 2)):
        k.append((b(n), UP if i % 2 == 0 else READY, "cubic"))
    # C: big sweep right then left with a wrist swing (beats 20-32)
    k.append((b(24), pose(j1=0.9, j5=0.4), "cubic"))
    k.append((b(32), pose(j1=-0.9, j5=-0.4), "cubic"))
    # D: roll the wrist on the beat while the base comes home (beats 32-40)
    for i, n in enumerate(range(34, 41, 2)):
        j1 = -0.9 + 0.9 * (i + 1) / 4
        k.append((b(n), pose(j1=j1, j6=(0.2 if i % 2 == 0 else -0.2) if n < 40 else 0.0), "cubic"))
    # E: up-left / up-right combo (beats 40-48)
    for i, n in enumerate(range(42, 49, 2)):
        s = 1 if i % 2 == 0 else -1
        k.append((b(n), pose(UP, j1=0.2 * s, j6=0.2 * s), "cubic"))
    # finale: back to ready, a last nod, hold
    k.append((b(50), READY, "cubic"))
    k.append((b(51), pose(j4=0.12), "cubic"))
    k.append((b(52), READY, "cubic"))
    return mk("beat-dance", k, f"{BPM} BPM, 52 beats; play with --caps dance --audio media/audio/beat-dance-100bpm.wav")


# ------------------------------------------------------------------ range-of-motion tours
def seg_time(q0, q1):
    d = max(abs(a - b) for a, b in zip(q0, q1))
    return max(1.5 * d / DANCE_VEL, math.sqrt(6 * d / DANCE_ACC), 0.4) * 1.1


def tour(name, poses, note, hold=0.6):
    k, t, q = [(0.0, READY, "cubic")], 0.0, READY
    for p in poses:
        t += seg_time(q, p)
        k.append((t, p, "cubic"))
        t += hold
        k.append((t, p, "hold"))
        q = p
    t += seg_time(q, READY)
    k.append((t, READY, "cubic"))
    return mk(name, k, note)


def rom_arm():
    return tour("rom-arm", [
        pose(j1=1.0), pose(j1=-1.0), READY,           # base sweep ±57° (cables!)
        [0, 1.2, 2.2, 0, 0, 0],                        # reach tall
        [0, 2.2, 2.0, 0, 0, 0],                        # stretch forward
    ], "range of motion: base ±1.0, shoulder/elbow tall + forward stretch; dance caps")


def rom_wrist():
    return tour("rom-wrist", [
        pose(j4=1.2), pose(j4=-1.2), READY,            # wrist pitch
        pose(j5=1.0), pose(j5=-1.0), READY,            # wrist yaw
        pose(j6=1.2), pose(j6=-1.2), READY,            # wrist roll (wrist camera cable)
    ], "range of motion: wrist pitch ±1.2, yaw ±1.0, roll ±1.2; dance caps", hold=0.4)


# ------------------------------------------------------------------ beat track
def beat_track(n_beats: int, path: Path, sr: int = 44100) -> None:
    lead = int(BLEND_S * sr)
    total = lead + int((n_beats * BEAT + 2.0) * sr)
    y = np.zeros(total)
    t = np.arange(int(0.5 * sr)) / sr
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-t * 30)) * t) * np.exp(-t * 9)
    rng = np.random.default_rng(7)
    snare = (rng.standard_normal(t.size) * 0.6 + np.sin(2 * np.pi * 190 * t) * 0.5) * np.exp(-t * 18)
    hat = rng.standard_normal(int(0.05 * sr)) * np.exp(-np.arange(int(0.05 * sr)) / sr * 90) * 0.25
    bass_notes = [55.0, 55.0, 65.41, 49.0]  # A1 A1 C2 G1, one per bar

    def add(sig, at, gain=1.0):
        i = lead + int(at * sr)
        j = min(total, i + sig.size)
        y[i:j] += gain * sig[: j - i]

    for n in range(n_beats):
        at = n * BEAT
        add(kick, at, 0.9)
        if n % 4 in (1, 3) and n >= 4:
            add(snare, at, 0.5)
        if n >= 4:
            add(hat, at + BEAT / 2, 1.0)
            add(hat, at, 0.6)
        if n >= 12 and n % 2 == 0:
            f = bass_notes[(n // 4) % 4]
            bt = np.arange(int(BEAT * 1.9 * sr)) / sr
            add(np.sin(2 * np.pi * f * bt) * np.exp(-bt * 1.5) * 0.35, at)
    for n in (0, 4, 12, 20, 32, 40, 48):  # section markers: a soft chime
        ct = np.arange(int(0.6 * sr)) / sr
        add(np.sin(2 * np.pi * 880 * ct) * np.exp(-ct * 6) * 0.12, n * BEAT)
    y /= max(1e-9, np.abs(y).max()) / 0.8
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def dance_envelope():
    """Envelope with the dance caps for both the playable validator and its envelope cross-check."""
    from armctl.envelope import Envelope
    return P._Envelope(Envelope(vel_caps=DANCE_VEL, acc_caps=DANCE_ACC))


# ------------------------------------------------------------------ checks
def fk_checker():
    import pinocchio as pin
    m = pin.buildModelFromUrdf(str(URDF))
    d = m.createData()
    bodies = [(k, f.name) for k, f in enumerate(m.frames) if f.type == pin.FrameType.BODY and f.name != "base"]
    idx = [m.joints[m.getJointId(f"joint{i + 1}")].idx_q for i in range(6)]

    def check(q6):
        q = pin.neutral(m)
        for i, v in zip(idx, q6):
            q[i] = v
        pin.forwardKinematics(m, d, q)
        pin.updateFramePlacements(m, d)
        z = {n: float(d.oMf[k].translation[2]) for k, n in bodies}
        low = min((v, n) for n, v in z.items() if n != "link1")
        return z["gripper_tip"], low
    return check


def main() -> int:
    P.set_envelope(dance_envelope())
    check = fk_checker()
    out = REPO / "playables"
    ok = True
    for pl in (beat_dance(), rom_arm(), rom_wrist()):
        r = P.validate(pl)
        s = P.sample(pl, 50)
        tips, lows = zip(*(check(q) for q in s["q"]))
        min_tip, (min_link, which) = min(tips), min(lows)
        fk_ok = min_tip >= MIN_TIP_Z and min_link >= MIN_LINK_Z
        print(f"{pl['name']:12} valid={r['ok']} dur={r['duration']:.1f}s maxvel={max(r['max_vel']):.2f} "
              f"maxacc={max(r['max_acc']):.2f} | FK min tip z={min_tip:.3f} lowest link {which} z={min_link:.3f} "
              f"{'OK' if fk_ok else 'TOO LOW'}")
        if not r["ok"]:
            print(P.report(r))
        ok &= r["ok"] and fk_ok
        (out / f"{pl['name']}.json").write_text(json.dumps(pl, indent=1) + "\n")
    beat_track(52, HERE / "media/audio/beat-dance-100bpm.wav")
    print("wrote", HERE / "media/audio/beat-dance-100bpm.wav")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
