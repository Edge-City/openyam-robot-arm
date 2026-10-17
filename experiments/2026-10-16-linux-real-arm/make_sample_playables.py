"""Hand-written sample playables (playables/*.json), timed for the armctl.envelope caps.

Run: .venv/bin/python experiments/2026-10-16-linux-real-arm/make_sample_playables.py
"""
import math, sys; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from armctl import playable as P
env = P.envelope(); V, A = min(env.vel_cap), min(env.acc_cap)
R = [0.0, 0.5, 0.8, 0.0, 0.0, 0.0]

def seg_T(q0, q1, extra=1.08):
    d = max(abs(a - b) for a, b in zip(q0, q1))
    T = max(1.5 * d / V, math.sqrt(6 * d / A), 0.4) * extra
    return math.ceil(T * 20) / 20  # round up to 0.05 s

class B:
    def __init__(s, name, notes):
        s.name, s.notes, s.k, s.t = name, notes, [{"t": 0, "q": R, "grip": 0.0, "ease": "cubic"}], 0.0
    def to(s, q, grip=None, ease="cubic", T=None):
        T = T or seg_T(s.k[-1]["q"], q)
        s.t = round(s.t + T, 3)
        d = {"t": s.t, "q": [round(x, 3) for x in q], "ease": ease}
        if grip is not None: d["grip"] = grip
        s.k.append(d); return s
    def hold(s, T):
        return s.to(s.k[-1]["q"], ease="hold", T=T)
    def done(s):
        return {"name": s.name, "author": "human:hand-written (claude agent)", "created": P.now_iso(),
                "notes": s.notes, "space": "joint", "start": "ready", "keyframes": s.k, "loop": 1, "speed": 1.0}

up = [0.0, 0.7, 1.1, -0.3, 0.0, 0.0]
w = B("wave", "Lift the forearm, then sway the base and roll the wrist side to side three times.").to(up)
for i in range(3):
    w.to([0.08, 0.7, 1.1, -0.3, 0, 0.12]).to([-0.08, 0.7, 1.1, -0.3, 0, -0.12])
wave = w.to(up).to(R).done()

n = B("nod", "Tilt the wrist (j4) down and up three times, like nodding yes.").to([0, 0.55, 0.9, 0.05, 0, 0])
for i in range(3):
    n.to([0, 0.55, 0.9, 0.3, 0, 0]).to([0, 0.55, 0.9, 0.05, 0, 0])
nod = n.to(R).done()

c = [0.0, 0.6, 1.0, 0.0, 0.0, 0.0]
d = B("happy-dance", "Rhythmic side-to-side sway of base and wrist with a little bounce and gripper snaps.").to(c, 0.0)
for i in range(5):
    s = 1 if i % 2 == 0 else -1
    d.to([0.08 * s, 0.65, 1.08, -0.06 * s, 0.08 * s, 0.1 * s], 0.6).to(c, 0.0)
dance = d.to(R, 0.0).done()

la = B("look-around", "Turn the base left and right, tilting the wrist to peek, like scanning the room.")
la.to([0, 0.6, 1.0, -0.15, 0, 0])
for q in ([0.5, 0.6, 1.0, -0.1, 0.15, 0], [0.5, 0.65, 1.05, -0.3, 0.15, 0],
          [-0.5, 0.6, 1.0, -0.1, -0.15, 0], [-0.5, 0.65, 1.05, -0.3, -0.15, 0],
          [0, 0.6, 1.0, -0.15, 0, 0]):
    la.to(q).hold(0.6)
look = la.to(R).done()

for p in (wave, nod, dance, look):
    r = P.validate(p)
    print(p["name"], P.report(r, 8))
    if r["ok"]: P.save(p)
