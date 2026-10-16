"""Simulation-only probes of DimOS joint-space behaviour (companion to armctl_sim.py).

Needs a sim started on the private bus (see armctl_sim.py docstring). Probes:
1. read /coordinator_joint_state over the transport (like yammac/tools/joint_probe.py)
2. Arm SDK (ManipulationModule planner): Arm.move_joints / Arm.home with speed_scale
3. what DimOS does with a raw out-of-limit joint trajectory (bypassing armctl): j4 = 2.0
4. the joint_command stream (single-point target -> 1 rad/s velocity-limited move)

Run: .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_sim_probes.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import armctl_sim  # noqa: E402  (sets the private bus before dimos imports)
from armctl_sim import JOINTS, ArmCtl, _fmt  # noqa: E402

from dimos.core.transport_factory import make_transport  # noqa: E402
from dimos.manipulation.sdk import Arm  # noqa: E402
from dimos.msgs.sensor_msgs.JointState import JointState  # noqa: E402
from dimos.msgs.trajectory_msgs.JointTrajectory import JointTrajectory  # noqa: E402
from dimos.msgs.trajectory_msgs.TrajectoryPoint import TrajectoryPoint  # noqa: E402


def p(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


arm = ArmCtl.connect()
p("bus", os.environ["ZENOH_SCOUT_ADDR"], "tasks", arm._coord.list_tasks())

# 1. topic
msgs: list[JointState] = []
t = make_transport("/coordinator_joint_state", JointState)
t.subscribe(lambda m: msgs.append(m))
time.sleep(1.0)
p(f"1. /coordinator_joint_state: {len(msgs)} msgs in 1 s; last names={list(msgs[-1].name) if msgs else None}")
if msgs:
    p("   positions", " ".join(f"{x:+.3f}" for x in msgs[-1].position))

# 2. Arm SDK through the planner
try:
    sdk = Arm.from_app(arm._app)
    p("2. Arm SDK group:", sdk.info.id, "joints", list(sdk.info.joint_names))
    p("   presets:", sorted(sdk.state().joint_presets))
    t0 = time.monotonic()
    r = sdk.move_joints([0.0, 0.5, 0.8, 0.0, 0.0, 0.0], speed_scale=0.3)
    p(f"   move_joints(ready, speed_scale=0.3): {r.status.name} in {time.monotonic() - t0:.1f}s ->", _fmt(arm.state()))
    t0 = time.monotonic()
    r = sdk.home(speed_scale=0.3)
    p(f"   home(speed_scale=0.3): {r.status.name} in {time.monotonic() - t0:.1f}s ->", _fmt(arm.state()))
    try:
        sdk.move_joints([0.0, 0.5, 0.8, 2.0, 0.0, 0.0], speed_scale=0.3)
        p("   planner accepted j4=2.0 (beyond URDF 1.571)!")
    except Exception as e:  # noqa: BLE001
        p("   planner j4=2.0:", type(e).__name__, str(e)[:300])
except Exception as e:  # noqa: BLE001
    p("2. Arm SDK failed:", type(e).__name__, str(e)[:400])

# 3. raw out-of-limit trajectory straight to the coordinator (sim only!)
arm.ready()
start = arm.state()
names = JOINTS
tgt = [start[j] for j in names]
tgt[3] = 2.0  # j4 beyond URDF upper 1.5708
traj = JointTrajectory(
    joint_names=names,
    points=[
        TrajectoryPoint(time_from_start=0.0, positions=[start[j] for j in names]),
        TrajectoryPoint(time_from_start=4.0, positions=tgt),
    ],
)
res = arm._coord.execute_trajectory(traj)
p("3. raw execute_trajectory j4->2.0:", res.status.name, res.message)
time.sleep(5.0)
p("   after 5 s:", _fmt(arm.state()), "status", arm.status().state.name)
# put it back with a raw trajectory (armctl would refuse to start from outside URDF)
now = arm.state()
back = JointTrajectory(
    joint_names=names,
    points=[
        TrajectoryPoint(time_from_start=0.0, positions=[now[j] for j in names]),
        TrajectoryPoint(time_from_start=4.0, positions=[start[j] for j in names]),
    ],
)
p("   back:", arm._coord.execute_trajectory(back).status.name)
time.sleep(5.0)
p("   after:", _fmt(arm.state()))

# 3b. a large single-step trajectory: how fast does the task move?
res = arm._coord.execute_trajectory(
    JointTrajectory(joint_names=["yam_joint1"], points=[TrajectoryPoint(positions=[1.0])])
)
p("3b. single-point j1 -> 1.0:", res.status.name)
t0 = time.monotonic()
samples = []
while time.monotonic() - t0 < 2.0:
    samples.append((time.monotonic() - t0, arm.state()["yam_joint1"]))
    time.sleep(0.1)
p("   j1 vs t:", " ".join(f"{dt:.1f}s:{x:+.2f}" for dt, x in samples[::3]))

# 4. joint_command stream
jc = make_transport("/joint_command", JointState)
jc.publish(JointState(name=["yam_joint1"], position=[0.0]))
time.sleep(2.0)
p("4. after publishing /joint_command j1=0.0:", _fmt(arm.state()))

arm.home()
p("final", _fmt(arm.state()))
t.stop() if hasattr(t, "stop") else None
arm.close()
