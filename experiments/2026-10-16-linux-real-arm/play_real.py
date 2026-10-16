"""Play a validated playable on the REAL arm through a running DimOS coordinator.

    ZENOH_SCOUT_ADDR=224.0.0.123:7449 .venv/bin/dimos --can-port can0 --viewer none run coordinator-openyam
    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/play_real.py nod [--speed 0.5] [--dry-run]

Steps: validate with armctl.playable (soft box, velocity and acceleration caps) -> check
every sample again against ArmCtl's soft limits -> go to the playable's start pose ->
blend from the measured pose (the arm sags 0.03-0.06 rad under gravity) to the first
sample with a 1 s min-jerk -> send one JointTrajectory -> wait, cancel on Ctrl-C or
timeout. Arm joints only; the gripper channel is ignored for now. Logs to
logs/armctl-real.log.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))  # -I drops the script dir
sys.path.insert(0, str(REPO))
from armctl_real import HOME_SPEED, SPEED, TOL, fmt, log  # noqa: E402
from armctl_sim import JOINTS, ArmCtl, LimitError, MotionFailed, _min_jerk  # noqa: E402

import armctl.playable as P  # noqa: E402
from dimos.msgs.trajectory_msgs.JointTrajectory import JointTrajectory  # noqa: E402
from dimos.msgs.trajectory_msgs.TrajectoryPoint import TrajectoryPoint  # noqa: E402
from dimos.msgs.trajectory_msgs.TrajectoryStatus import TrajectoryState  # noqa: E402

RATE = 50.0
BLEND_S = 1.0


def build(arm: ArmCtl, pl: dict, rate: float) -> JointTrajectory:
    s = P.sample(pl, rate)
    q0 = s["q"][0]
    now = arm.state()
    pts: list[TrajectoryPoint] = []
    nb = int(BLEND_S * rate)
    for k in range(nb):
        a = _min_jerk(k / nb)
        pts.append(TrajectoryPoint(time_from_start=k / rate,
                                   positions=[now[j] + (q0[i] - now[j]) * a for i, j in enumerate(JOINTS)]))
    for t, q in zip(s["t"], s["q"]):
        pts.append(TrajectoryPoint(time_from_start=BLEND_S + t, positions=list(q)))
    for k, p in enumerate(pts):
        for j, v in zip(JOINTS, p.positions):
            lo, hi = arm.soft_limits[j]
            if not lo - 1e-6 <= v <= hi + 1e-6:
                raise LimitError(f"point {k} t={p.time_from_start:.2f}s: {j}={v:+.3f} outside [{lo:+.3f}, {hi:+.3f}]")
    return JointTrajectory(joint_names=list(JOINTS), points=pts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--speed", type=float, default=None, help="override playable speed (<=1)")
    ap.add_argument("--dry-run", action="store_true", help="validate and plan only, send nothing")
    a = ap.parse_args()

    pl = P.load(a.name)
    if a.speed is not None:
        if not 0 < a.speed <= 1.0:
            print("--speed must be in (0, 1]")
            return 2
        pl["speed"] = a.speed
    r = P.validate(pl)
    if not r["ok"]:
        print(P.report(r))
        return 1
    log(f"play {a.name}: valid, {r['duration']:.1f}s, speed {pl.get('speed', 1.0)}, start {pl['start']}")

    arm = ArmCtl.connect(speed=SPEED, home_speed=HOME_SPEED)
    try:
        if a.dry_run:
            traj = build(arm, pl, RATE)
            log(f"dry run: {len(traj.points)} points over {traj.duration:.1f}s from {fmt(arm.state())}; nothing sent")
            return 0
        log(f"play {a.name}: going to start pose '{pl['start']}' from {fmt(arm.state())}")
        arm.go(pl["start"], tol=TOL)
        traj = build(arm, pl, RATE)
        res = arm._coord.execute_trajectory(traj)
        if res.status.name != "ACCEPTED":
            raise MotionFailed(f"execute_trajectory: {res.status.name} {res.message}")
        log(f"play {a.name}: playing {traj.duration:.1f}s ({len(traj.points)} points)")
        deadline = time.monotonic() + traj.duration + 5.0
        try:
            while True:
                time.sleep(0.05)
                st = arm.status()
                if st.state == TrajectoryState.COMPLETED:
                    break
                if st.state in (TrajectoryState.ABORTED, TrajectoryState.FAULT):
                    raise MotionFailed(f"trajectory {st.state.name}")
                if time.monotonic() > deadline:
                    arm.stop()
                    raise MotionFailed("did not complete in time; cancelled (holding)")
        except KeyboardInterrupt:
            arm.stop()
            log(f"play {a.name}: interrupted, cancelled (holding at {fmt(arm.state())})")
            return 130
        log(f"play {a.name}: done | now {fmt(arm.state())}")
        return 0
    except (LimitError, MotionFailed) as e:
        log(f"play {a.name} REFUSED/FAILED: {e}")
        return 1
    finally:
        arm.close()


if __name__ == "__main__":
    raise SystemExit(main())
